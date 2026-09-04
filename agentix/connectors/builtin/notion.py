"""Notion connector — create/update pages, query databases, and read a
page's actual content (this is the part that shipped broken).

Notion's data model splits a page in two: `GET /pages/{id}` returns only
metadata and property values (title, etc.) — the actual body text lives in
the page's block *children*, fetched from a completely different endpoint
(`GET /blocks/{id}/children`, recursively — a page id doubles as a block
id). Before this file's read path existed, `get_page` was the only way to
read a page, and it structurally could not return any body text — every
retrieval/RAG use case was non-functional, confirmed live: a real,
connected page returned `{id, url, properties: {title: ...}}` and nothing
else. get_block_children/search/get_page_content below are the fix;
get_page_content (get_page + recursive children + markdown flattening in
one call) is the one that actually makes an agent work on the first try —
raw block JSON is roughly 10-20x the size of the equivalent markdown, and
models reason far better over markdown headings than nested rich_text
arrays.
"""
from __future__ import annotations

import asyncio
import re

import httpx

from agentix.connectors.base import BaseConnector, ConnectorAction, ConnectorMeta
from agentix.connectors.registry import register_connector

_MAX_BLOCKS = 2000          # total-block cap so a pathological page can't run away
_MAX_DEPTH = 4              # recursion depth cap for nested blocks
_DEFAULT_MAX_CHARS = 20_000

_ACTIONS = [
    ConnectorAction("create_page", "Create a new Notion page",
        {"type": "object",
         "properties": {
             "parent_id": {"type": "string", "description": "Parent page or database ID"},
             "parent_type": {"type": "string", "enum": ["page_id", "database_id"], "default": "database_id"},
             "title": {"type": "string"},
             "properties": {"type": "object", "description": "Page properties (for database pages)"},
         }, "required": ["parent_id", "title"]}),
    ConnectorAction("update_page", "Update properties of a Notion page",
        {"type": "object",
         "properties": {
             "page_id": {"type": "string"}, "properties": {"type": "object"},
         }, "required": ["page_id", "properties"]}),
    ConnectorAction("get_page", "Retrieve a Notion page's metadata and properties (title, etc.) by ID — "
                                "does NOT return the page's body text, which lives in its block children; "
                                "use get_page_content for that.",
        {"type": "object",
         "properties": {"page_id": {"type": "string"}},
         "required": ["page_id"]}),
    ConnectorAction("query_database", "Query a Notion database with optional filters",
        {"type": "object",
         "properties": {
             "database_id": {"type": "string"},
             "filter": {"type": "object"}, "sorts": {"type": "array"},
             "page_size": {"type": "integer", "default": 10},
         }, "required": []}),
    ConnectorAction("append_block", "Append content blocks to a Notion page",
        {"type": "object",
         "properties": {
             "page_id": {"type": "string"}, "text": {"type": "string"},
         }, "required": ["page_id", "text"]}),
    ConnectorAction("get_block_children",
        "List a block's (a page id is itself a block id) direct child blocks. Always fetches every "
        "page of results (never silently truncates) and, by default, recurses into nested blocks "
        "(toggles, list items with sub-items, columns, tables) up to max_depth. Prefer "
        "get_page_content for reading a whole page's text — this is the low-level building block.",
        {"type": "object",
         "properties": {
             "block_id": {"type": "string", "description": "Block or page ID"},
             "start_cursor": {"type": "string", "description": "Resume from a specific cursor instead "
                              "of fetching from the start (fetches exactly one page, not the whole tree)"},
             "page_size": {"type": "integer", "default": 100},
             "recursive": {"type": "boolean", "default": True,
                           "description": "Recurse into blocks with children"},
             "max_depth": {"type": "integer", "default": _MAX_DEPTH},
         }, "required": ["block_id"]}),
    ConnectorAction("search",
        "Search pages (or databases) this integration has been granted access to in Notion — the only "
        "way to reach a page without already knowing its ID. Notion's search only returns objects "
        "explicitly shared with this integration (its real security boundary) — an empty result can "
        "mean 'no match' OR 'exists but not shared with this integration', not necessarily 'no such page'.",
        {"type": "object",
         "properties": {
             "query": {"type": "string", "description": "Search text; empty returns everything visible"},
             "object_type": {"type": "string", "enum": ["page", "database"], "default": "page"},
             "page_size": {"type": "integer", "default": 20},
         }, "required": []}),
    ConnectorAction("get_page_content",
        "Fetch a Notion page's full text content as markdown in one call (composes get_page + "
        "recursive block fetch + markdown flattening) — the recommended way to read a page for "
        "retrieval/RAG. Returns a 'truncated' flag and block counts so an empty/short result can be "
        "told apart from a read that hit a length or depth limit.",
        {"type": "object",
         "properties": {
             "page_id": {"type": "string", "description": "Dashed or undashed UUID"},
             "max_chars": {"type": "integer", "default": _DEFAULT_MAX_CHARS},
         }, "required": ["page_id"]}),
]

_UUID_HEX_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)


def _normalize_id(raw_id: str) -> str:
    """Notion accepts dashed UUIDs; users/agents paste undashed ones just
    as often (found live). Normalize either into the canonical dashed
    8-4-4-4-12 form Notion's API expects, or fail with a clear message
    naming exactly what was rejected — rather than a generic 400 from
    Notion itself with no context on which of possibly several ids in a
    call was the bad one."""
    stripped = raw_id.replace("-", "")
    if not _UUID_HEX_RE.match(stripped):
        raise ValueError(
            f"'{raw_id}' doesn't look like a valid Notion page/block id "
            "(expected a 32-character hex UUID, dashed or undashed)"
        )
    return f"{stripped[0:8]}-{stripped[8:12]}-{stripped[12:16]}-{stripped[16:20]}-{stripped[20:32]}"


def _plain_text(rich_text: list[dict] | None) -> str:
    return "".join(rt.get("plain_text", "") for rt in (rich_text or []))


def _extract_title(item: dict) -> str:
    """A page's title lives inside whichever of its `properties` has
    type "title" (the property is USER-RENAMEABLE, e.g. "Name" instead of
    "Title" — there is no fixed key). A database's title is a top-level
    `title` array instead. Search results and get_page's own response are
    both this same "object" shape, so one helper covers both."""
    if item.get("object") == "database":
        return _plain_text(item.get("title", []))
    for prop in item.get("properties", {}).values():
        if prop.get("type") == "title":
            return _plain_text(prop.get("title", []))
    return ""


def _blocks_to_markdown(blocks: list[dict], depth: int = 0) -> tuple[list[str], int]:
    """Flatten Notion blocks (with any nested "children" already attached
    by _fetch_children) into markdown lines. Returns (lines,
    skipped_unknown_block_count) — an agent seeing a low count from a page
    it expected more from is a real signal, not silence."""
    lines: list[str] = []
    skipped = 0
    indent = "  " * depth  # 2 spaces per nesting level
    numbered_run = 0  # resets whenever a non-numbered_list_item block breaks the run

    for b in blocks:
        btype = b.get("type", "")
        content = b.get(btype, {}) if btype else {}
        text = _plain_text(content.get("rich_text"))

        if btype != "numbered_list_item":
            numbered_run = 0

        if btype == "paragraph":
            if text:
                lines.append(f"{indent}{text}")
        elif btype in ("heading_1", "heading_2", "heading_3"):
            hashes = {"heading_1": "#", "heading_2": "##", "heading_3": "###"}[btype]
            lines.append(f"{indent}{hashes} {text}")
        elif btype == "bulleted_list_item":
            lines.append(f"{indent}- {text}")
        elif btype == "numbered_list_item":
            numbered_run += 1
            lines.append(f"{indent}{numbered_run}. {text}")
        elif btype == "to_do":
            box = "[x]" if content.get("checked") else "[ ]"
            lines.append(f"{indent}- {box} {text}")
        elif btype == "toggle":
            lines.append(f"{indent}- {text}")
        elif btype == "code":
            lang = content.get("language", "")
            lines.append(f"{indent}```{lang}")
            lines.extend(f"{indent}{ln}" for ln in text.split("\n"))
            lines.append(f"{indent}```")
        elif btype == "quote":
            lines.append(f"{indent}> {text}")
        elif btype == "callout":
            emoji = (content.get("icon") or {}).get("emoji", "")
            prefix = f"{emoji} " if emoji else ""
            lines.append(f"{indent}> {prefix}{text}")
        elif btype == "divider":
            lines.append(f"{indent}---")
        elif btype == "table":
            # table_row children are consumed directly here (not via the
            # generic child-recursion below) so the header separator row
            # can be inserted after row 0 — required for valid markdown.
            rows = b.get("children", [])
            for i, row in enumerate(rows):
                cells = row.get("table_row", {}).get("cells", [])
                lines.append(f"{indent}| " + " | ".join(_plain_text(c) for c in cells) + " |")
                if i == 0:
                    lines.append(f"{indent}| " + " | ".join(["---"] * len(cells)) + " |")
            continue  # children already handled — skip the generic recursion below
        elif btype == "child_page":
            lines.append(f"{indent}[{content.get('title', '')}](notion://{b.get('id', '')})")
            continue  # per spec: don't recurse into a different page's content by default
        elif btype in ("image", "file"):
            file_obj = content.get("file") or content.get("external") or {}
            caption = _plain_text(content.get("caption"))
            lines.append(f"{indent}![{caption}]({file_obj.get('url', '')})")
        else:
            skipped += 1
            continue

        children = b.get("children")
        if children:
            child_lines, child_skipped = _blocks_to_markdown(children, depth + 1)
            lines.extend(child_lines)
            skipped += child_skipped

    return lines, skipped


@register_connector("notion")
class NotionConnector(BaseConnector):
    meta = ConnectorMeta(
        type_name="notion", display_name="Notion",
        description="Create/update pages, query databases, and read page content in Notion.",
        category="productivity", icon="📝", auth_type="api_key",
        required_config=["token"], optional_config=["default_database_id"],
        actions=_ACTIONS,
    )

    _BASE = "https://api.notion.com/v1"
    _VERSION = "2022-06-28"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        # Test-injection hook only — always None (real network) in production.
        self._transport: httpx.BaseTransport | None = None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._BASE,
            headers={
                "Authorization": f"Bearer {self._require('token')}",
                "Notion-Version": self._VERSION,
                "Content-Type": "application/json",
            },
            timeout=30,
            transport=self._transport,
        )

    async def _req(self, client: httpx.AsyncClient, method: str, path: str, **kwargs) -> dict:
        """Every Notion call goes through here so a node/agent sees a
        clear, actionable reason for failure instead of an empty result
        or a raw HTTP error — found live: a 404 (by far the most common
        failure, since it also covers "not shared with this integration")
        was otherwise unguessable without exactly this wording."""
        backoff = 1.0
        max_attempts = 5
        for attempt in range(1, max_attempts + 1):
            r = await client.request(method, path, **kwargs)

            if r.status_code == 429:
                if attempt == max_attempts:
                    raise RuntimeError(
                        f"Notion API: rate-limited (429) on {method} {path} — "
                        f"gave up after {max_attempts} attempts"
                    )
                wait = float(r.headers.get("Retry-After", backoff))
                await asyncio.sleep(wait)
                backoff = min(backoff * 2, 8.0)
                continue

            if r.status_code == 404:
                raise ValueError(
                    f"Notion {method} {path}: page or block not found, or this integration has not "
                    "been granted access to it. In Notion: open the page -> ... menu (top right) -> "
                    "Connections -> add your integration."
                )
            if r.status_code == 401:
                raise ValueError(
                    "Notion API rejected the request: the integration token is invalid or has been "
                    "revoked. Check the token under Settings -> Connectors."
                )
            if r.status_code == 400:
                try:
                    detail = r.json()
                except Exception:
                    detail = {}
                if detail.get("code") == "validation_error":
                    raise ValueError(f"Notion rejected {method} {path}: {detail.get('message', r.text)}")

            r.raise_for_status()
            return r.json()

        raise RuntimeError(f"Notion API: rate-limited (429) on {method} {path} — gave up")

    async def _fetch_children(
        self, client: httpx.AsyncClient, block_id: str, max_depth: int, depth: int,
        total_cap: int, counter: dict, recursive: bool, page_size: int = 100,
    ) -> list[dict]:
        blocks: list[dict] = []
        cursor: str | None = None
        while True:
            params: dict = {"page_size": page_size}
            if cursor:
                params["start_cursor"] = cursor
            data = await self._req(client, "GET", f"/blocks/{block_id}/children", params=params)
            for b in data.get("results", []):
                if counter["n"] >= total_cap:
                    counter["capped"] = True
                    return blocks
                counter["n"] += 1
                if (
                    recursive
                    and b.get("has_children")
                    and depth < max_depth
                    and b.get("type") != "child_page"  # don't cross into a different page's content
                ):
                    b["children"] = await self._fetch_children(
                        client, b["id"], max_depth, depth + 1, total_cap, counter, recursive, page_size
                    )
                blocks.append(b)
            if not data.get("has_more"):
                return blocks
            cursor = data.get("next_cursor")

    async def connect(self) -> None:
        async with self._client() as c:
            r = await c.get("/users/me")
            r.raise_for_status()

    async def create_page(self, parent_id: str, title: str,
                          parent_type: str = "database_id",
                          properties: dict | None = None) -> dict:
        parent_id = _normalize_id(parent_id)
        body: dict = {
            "parent": {parent_type: parent_id},
            "properties": properties or {
                "title": {"title": [{"text": {"content": title}}]}
            },
        }
        async with self._client() as c:
            d = await self._req(c, "POST", "/pages", json=body)
            return {"id": d["id"], "url": d.get("url")}

    async def update_page(self, page_id: str, properties: dict) -> dict:
        page_id = _normalize_id(page_id)
        async with self._client() as c:
            d = await self._req(c, "PATCH", f"/pages/{page_id}", json={"properties": properties})
            return {"id": d["id"], "url": d.get("url")}

    async def get_page(self, page_id: str) -> dict:
        page_id = _normalize_id(page_id)
        async with self._client() as c:
            d = await self._req(c, "GET", f"/pages/{page_id}")
            return {"id": d["id"], "url": d.get("url"), "properties": d.get("properties", {})}

    async def query_database(self, database_id: str = "", filter: dict | None = None,
                              sorts: list | None = None, page_size: int = 10) -> dict:
        db_id = _normalize_id(database_id) if database_id else self._cfg.get("default_database_id", "")
        body: dict = {"page_size": page_size}
        if filter:
            body["filter"] = filter
        if sorts:
            body["sorts"] = sorts
        async with self._client() as c:
            d = await self._req(c, "POST", f"/databases/{db_id}/query", json=body)
            return {"results": [{"id": p["id"], "url": p.get("url"), "properties": p.get("properties")}
                                  for p in d.get("results", [])],
                    "has_more": d.get("has_more", False)}

    async def append_block(self, page_id: str, text: str) -> dict:
        page_id = _normalize_id(page_id)
        children = [{"object": "block", "type": "paragraph",
                     "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]}}]
        async with self._client() as c:
            await self._req(c, "PATCH", f"/blocks/{page_id}/children", json={"children": children})
            return {"appended": True, "page_id": page_id}

    async def get_block_children(
        self, block_id: str, start_cursor: str | None = None, page_size: int = 100,
        recursive: bool = True, max_depth: int = _MAX_DEPTH,
    ) -> dict:
        block_id = _normalize_id(block_id)
        async with self._client() as c:
            if start_cursor:
                # Manual single-page mode: caller already holds a cursor
                # from a prior fetch and wants exactly one more page, not
                # a fresh full auto-paginated tree.
                data = await self._req(c, "GET", f"/blocks/{block_id}/children",
                                        params={"page_size": page_size, "start_cursor": start_cursor})
                blocks = data.get("results", [])
                return {"blocks": blocks, "has_more": data.get("has_more", False),
                        "next_cursor": data.get("next_cursor"), "block_count": len(blocks),
                        "truncated": False}

            counter = {"n": 0, "capped": False}
            blocks = await self._fetch_children(
                c, block_id, max_depth, 0, _MAX_BLOCKS, counter, recursive, page_size
            )
        return {"blocks": blocks, "has_more": False, "next_cursor": None,
                "block_count": counter["n"], "truncated": counter["capped"]}

    async def search(self, query: str = "", object_type: str = "page", page_size: int = 20) -> dict:
        body: dict = {"page_size": page_size}
        if query:
            body["query"] = query
        if object_type:
            body["filter"] = {"value": object_type, "property": "object"}
        async with self._client() as c:
            data = await self._req(c, "POST", "/search", json=body)
        results = [
            {"id": item["id"], "url": item.get("url"), "title": _extract_title(item),
             "object": item.get("object")}
            for item in data.get("results", [])
        ]
        return {"results": results, "has_more": data.get("has_more", False)}

    async def get_page_content(self, page_id: str, max_chars: int = _DEFAULT_MAX_CHARS) -> dict:
        page_id = _normalize_id(page_id)
        counter = {"n": 0, "capped": False}
        async with self._client() as c:
            page = await self._req(c, "GET", f"/pages/{page_id}")
            blocks = await self._fetch_children(c, page_id, _MAX_DEPTH, 0, _MAX_BLOCKS, counter, True)

        title = _extract_title(page)
        lines, skipped = _blocks_to_markdown(blocks)
        body = "\n".join(lines)
        text = f"# {title}\n\n{body}" if title else body

        truncated = counter["capped"]
        if len(text) > max_chars:
            truncated = True
            cut = text.rfind("\n", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            total_len = len(text)
            text = text[:cut] + f"\n\n[... TRUNCATED: showing {cut} of {total_len} characters ...]"

        return {
            "id": page.get("id"), "url": page.get("url"), "title": title,
            "content": text, "block_count": counter["n"],
            "skipped_block_count": skipped, "truncated": truncated,
        }
