"""Regression tests for the Notion connector's read path.

Before this file's fix, get_page returned only page metadata and property
values — Notion splits a page's actual body text into its block children,
fetched from a completely different endpoint. Any retrieval/RAG use case
was non-functional: verified live, a real connected page returned
{id, url, properties: {title: ...}} and nothing else. These tests cover
get_block_children (pagination + recursion), search, get_page_content
(the composed markdown-flattening call), the block->markdown mapping, id
normalization, and Notion's real error shapes (404/401/400/429).
"""
from __future__ import annotations

import httpx
import pytest

from agentix.connectors.builtin.notion import (
    NotionConnector,
    _blocks_to_markdown,
    _extract_title,
    _normalize_id,
)


def _connector(handler) -> NotionConnector:
    c = NotionConnector({"token": "secret_test_token"})
    c._transport = httpx.MockTransport(handler)
    return c


_UUID = "380db9f5-ca96-80b1-813d-deade3c3a1ea"  # a real id shape — get_block_children normalizes/validates it


# ---------------------------------------------------------------------------
# _normalize_id
# ---------------------------------------------------------------------------

def test_normalize_id_accepts_dashed():
    assert _normalize_id("380db9f5-ca96-80b1-813d-deade3c3a1ea") == "380db9f5-ca96-80b1-813d-deade3c3a1ea"


def test_normalize_id_accepts_undashed_and_reinserts_dashes():
    """Found live: pasting an undashed id (32 hex chars, no dashes) — a
    real, common shape — must still work."""
    assert _normalize_id("380db9f5ca9680b1813ddeade3c3a1ea") == "380db9f5-ca96-80b1-813d-deade3c3a1ea"


def test_normalize_id_rejects_garbage_naming_the_bad_value():
    with pytest.raises(ValueError, match="not-a-real-id"):
        _normalize_id("not-a-real-id")


# ---------------------------------------------------------------------------
# _extract_title
# ---------------------------------------------------------------------------

def test_extract_title_from_page_properties():
    page = {
        "object": "page",
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Northwind Data Systems"}]},
            "Status": {"type": "select", "select": {"name": "Live"}},
        },
    }
    assert _extract_title(page) == "Northwind Data Systems"


def test_extract_title_from_database_top_level_title():
    db = {"object": "database", "title": [{"plain_text": "Trust Center"}]}
    assert _extract_title(db) == "Trust Center"


# ---------------------------------------------------------------------------
# _blocks_to_markdown — the mapping table
# ---------------------------------------------------------------------------

def _rt(text: str) -> list[dict]:
    return [{"plain_text": text}]


def test_markdown_headings_and_paragraph():
    blocks = [
        {"type": "heading_2", "heading_2": {"rich_text": _rt("9. Disaster Recovery")}},
        {"type": "paragraph", "paragraph": {"rich_text": _rt("RTO: 4 hours. RPO: 1 hour.")}},
    ]
    lines, skipped = _blocks_to_markdown(blocks)
    assert lines == ["## 9. Disaster Recovery", "RTO: 4 hours. RPO: 1 hour."]
    assert skipped == 0


def test_markdown_numbered_list_increments_and_resets():
    blocks = [
        {"type": "numbered_list_item", "numbered_list_item": {"rich_text": _rt("first")}},
        {"type": "numbered_list_item", "numbered_list_item": {"rich_text": _rt("second")}},
        {"type": "paragraph", "paragraph": {"rich_text": _rt("interruption")}},
        {"type": "numbered_list_item", "numbered_list_item": {"rich_text": _rt("restarts at one")}},
    ]
    lines, _ = _blocks_to_markdown(blocks)
    assert lines == ["1. first", "2. second", "interruption", "1. restarts at one"]


def test_markdown_todo_checked_and_unchecked():
    blocks = [
        {"type": "to_do", "to_do": {"rich_text": _rt("done"), "checked": True}},
        {"type": "to_do", "to_do": {"rich_text": _rt("not done"), "checked": False}},
    ]
    lines, _ = _blocks_to_markdown(blocks)
    assert lines == ["- [x] done", "- [ ] not done"]


def test_markdown_code_block_with_language():
    blocks = [{"type": "code", "code": {"rich_text": _rt("print(1)"), "language": "python"}}]
    lines, _ = _blocks_to_markdown(blocks)
    assert lines == ["```python", "print(1)", "```"]


def test_markdown_callout_with_emoji():
    blocks = [{"type": "callout", "callout": {"rich_text": _rt("heads up"), "icon": {"emoji": "⚠️"}}}]
    lines, _ = _blocks_to_markdown(blocks)
    assert lines == ["> ⚠️ heads up"]


def test_markdown_divider():
    assert _blocks_to_markdown([{"type": "divider", "divider": {}}])[0] == ["---"]


def test_markdown_table_inserts_header_separator_after_first_row():
    table = {
        "type": "table", "table": {},
        "children": [
            {"type": "table_row", "table_row": {"cells": [_rt("Col A"), _rt("Col B")]}},
            {"type": "table_row", "table_row": {"cells": [_rt("1"), _rt("2")]}},
        ],
    }
    lines, _ = _blocks_to_markdown([table])
    assert lines == ["| Col A | Col B |", "| --- | --- |", "| 1 | 2 |"]


def test_markdown_child_page_does_not_recurse():
    """Spec: child_page renders as a link and is never expanded, even if
    Notion reports it has_children / children were attached."""
    blocks = [{
        "type": "child_page", "child_page": {"title": "Sub Page"}, "id": "abc123",
        "children": [{"type": "paragraph", "paragraph": {"rich_text": _rt("should not appear")}}],
    }]
    lines, _ = _blocks_to_markdown(blocks)
    assert lines == ["[Sub Page](notion://abc123)"]


def test_markdown_unknown_block_type_is_skipped_and_counted():
    blocks = [
        {"type": "some_future_block_type", "some_future_block_type": {}},
        {"type": "paragraph", "paragraph": {"rich_text": _rt("real content")}},
    ]
    lines, skipped = _blocks_to_markdown(blocks)
    assert lines == ["real content"]
    assert skipped == 1


def test_markdown_nested_children_are_indented_two_spaces_per_level():
    blocks = [{
        "type": "toggle", "toggle": {"rich_text": _rt("Details")},
        "children": [{"type": "paragraph", "paragraph": {"rich_text": _rt("nested text")}}],
    }]
    lines, _ = _blocks_to_markdown(blocks)
    assert lines == ["- Details", "  nested text"]


# ---------------------------------------------------------------------------
# get_block_children — pagination + recursion (via httpx.MockTransport)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_block_children_loops_until_has_more_is_false():
    """Silent truncation is worse than an error — a page over ~100 blocks
    must not come back cut off just because Notion paginates at 100."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        cursor = request.url.params.get("start_cursor")
        if cursor is None:
            return httpx.Response(200, json={
                "results": [{"id": "b1", "type": "paragraph", "paragraph": {"rich_text": _rt("page one")}}],
                "has_more": True, "next_cursor": "cursor_2",
            })
        return httpx.Response(200, json={
            "results": [{"id": "b2", "type": "paragraph", "paragraph": {"rich_text": _rt("page two")}}],
            "has_more": False, "next_cursor": None,
        })

    conn = _connector(handler)
    result = await conn.get_block_children(block_id="380db9f5-ca96-80b1-813d-deade3c3a1ea")
    assert result["block_count"] == 2
    assert [b["id"] for b in result["blocks"]] == ["b1", "b2"]
    assert result["truncated"] is False
    assert len(calls) == 2  # confirms it actually paginated, not just got lucky


@pytest.mark.asyncio
async def test_get_block_children_recurses_into_nested_blocks():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(f"/blocks/{_UUID}/children"):
            return httpx.Response(200, json={
                "results": [{"id": "toggle_1", "type": "toggle", "has_children": True,
                             "toggle": {"rich_text": _rt("Section")}}],
                "has_more": False,
            })
        if path.endswith("/blocks/toggle_1/children"):
            return httpx.Response(200, json={
                "results": [{"id": "nested_1", "type": "paragraph",
                             "paragraph": {"rich_text": _rt("nested content")}}],
                "has_more": False,
            })
        raise AssertionError(f"unexpected request: {path}")

    conn = _connector(handler)
    result = await conn.get_block_children(block_id=_UUID)
    assert result["blocks"][0]["id"] == "toggle_1"
    assert result["blocks"][0]["children"][0]["id"] == "nested_1"
    assert result["block_count"] == 2


@pytest.mark.asyncio
async def test_get_block_children_does_not_recurse_past_max_depth():
    def handler(request: httpx.Request) -> httpx.Response:
        block_id = request.url.path.rsplit("/", 2)[-2]
        return httpx.Response(200, json={
            "results": [{"id": f"{block_id}_child", "type": "toggle", "has_children": True,
                         "toggle": {"rich_text": _rt("x")}}],
            "has_more": False,
        })

    conn = _connector(handler)
    result = await conn.get_block_children(block_id=_UUID, max_depth=1)
    # depth 0 (root's children) fetched, one level of recursion (depth 1) fetched,
    # but that child's own has_children is NOT followed (would be depth 2 > max_depth=1)
    level0 = result["blocks"][0]
    assert level0["id"] == f"{_UUID}_child"
    level1 = level0["children"][0]
    assert level1["id"] == f"{_UUID}_child_child"
    assert "children" not in level1


@pytest.mark.asyncio
async def test_get_block_children_does_not_recurse_into_child_page():
    """A child_page block reporting has_children must not pull in a
    DIFFERENT page's content — that's a distinct document, not this one."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "results": [{"id": "cp1", "type": "child_page", "has_children": True,
                         "child_page": {"title": "Other Page"}}],
            "has_more": False,
        })

    conn = _connector(handler)
    result = await conn.get_block_children(block_id=_UUID)
    assert "children" not in result["blocks"][0]


@pytest.mark.asyncio
async def test_get_block_children_total_cap_sets_truncated_flag():
    def handler(request: httpx.Request) -> httpx.Response:
        # An "infinite" page: always has_more, so only the total-block cap
        # can ever stop this from looping forever.
        return httpx.Response(200, json={
            "results": [{"id": "x", "type": "paragraph", "paragraph": {"rich_text": _rt("x")}}],
            "has_more": True, "next_cursor": "again",
        })

    conn = _connector(handler)
    result = await conn.get_block_children(block_id=_UUID, max_depth=0)
    from agentix.connectors.builtin import notion as notion_mod
    assert result["block_count"] == notion_mod._MAX_BLOCKS
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_get_block_children_start_cursor_is_manual_single_page_mode():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("start_cursor") == "resume_here"
        return httpx.Response(200, json={
            "results": [{"id": "b1", "type": "paragraph", "paragraph": {"rich_text": _rt("x")}}],
            "has_more": True, "next_cursor": "more_after_this",
        })

    conn = _connector(handler)
    result = await conn.get_block_children(block_id=_UUID, start_cursor="resume_here")
    assert result["block_count"] == 1
    assert result["has_more"] is True
    assert result["next_cursor"] == "more_after_this"


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_sends_object_filter_and_extracts_titles():
    sent_body = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        sent_body.update(json.loads(request.content))
        return httpx.Response(200, json={"results": [
            {"id": "p1", "object": "page", "url": "https://notion.so/p1",
             "properties": {"Name": {"type": "title", "title": [{"plain_text": "Northwind"}]}}},
        ], "has_more": False})

    conn = _connector(handler)
    result = await conn.search(query="Northwind")
    assert sent_body == {"page_size": 20, "query": "Northwind", "filter": {"value": "page", "property": "object"}}
    assert result["results"][0]["title"] == "Northwind"


# ---------------------------------------------------------------------------
# get_page_content — the composed call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_page_content_returns_markdown_with_title_heading():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/pages/380db9f5-ca96-80b1-813d-deade3c3a1ea"):
            return httpx.Response(200, json={
                "id": "380db9f5-ca96-80b1-813d-deade3c3a1ea", "url": "https://notion.so/x",
                "properties": {"Name": {"type": "title", "title": [{"plain_text": "Northwind Data Systems"}]}},
            })
        return httpx.Response(200, json={
            "results": [
                {"id": "h1", "type": "heading_2", "heading_2": {"rich_text": _rt("9. Disaster Recovery")}},
                {"id": "p1", "type": "paragraph", "paragraph": {"rich_text": _rt("RTO: 4 hours. RPO: 1 hour.")}},
            ],
            "has_more": False,
        })

    conn = _connector(handler)
    result = await conn.get_page_content(page_id="380db9f5ca9680b1813ddeade3c3a1ea")  # undashed on purpose
    assert result["title"] == "Northwind Data Systems"
    assert "## 9. Disaster Recovery" in result["content"]
    assert "RTO: 4 hours. RPO: 1 hour." in result["content"]
    assert result["content"].startswith("# Northwind Data Systems")
    assert result["truncated"] is False
    assert result["block_count"] == 2


@pytest.mark.asyncio
async def test_get_page_content_truncates_with_explicit_marker_not_silently():
    long_text = "x" * 100
    def handler(request: httpx.Request) -> httpx.Response:
        if "/pages/" in request.url.path:
            return httpx.Response(200, json={"id": "p", "url": "u", "properties": {}})
        return httpx.Response(200, json={
            "results": [{"id": "p1", "type": "paragraph", "paragraph": {"rich_text": _rt(long_text)}}],
            "has_more": False,
        })

    conn = _connector(handler)
    result = await conn.get_page_content(page_id="380db9f5-ca96-80b1-813d-deade3c3a1ea", max_chars=20)
    assert result["truncated"] is True
    assert "TRUNCATED" in result["content"]
    assert len(result["content"]) < len(long_text)


# ---------------------------------------------------------------------------
# Error handling — distinguish failure modes, don't return empty for all
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_404_names_the_access_grant_fix():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"code": "object_not_found"})

    conn = _connector(handler)
    with pytest.raises(ValueError, match="Connections"):
        await conn.get_page(page_id="380db9f5-ca96-80b1-813d-deade3c3a1ea")


@pytest.mark.asyncio
async def test_401_names_token_problem():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": "unauthorized"})

    conn = _connector(handler)
    with pytest.raises(ValueError, match="token"):
        await conn.get_page(page_id="380db9f5-ca96-80b1-813d-deade3c3a1ea")


@pytest.mark.asyncio
async def test_400_validation_error_names_the_rejected_request():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": "validation_error", "message": "path failed validation"})

    conn = _connector(handler)
    with pytest.raises(ValueError, match="path failed validation"):
        await conn.get_page(page_id="380db9f5-ca96-80b1-813d-deade3c3a1ea")


@pytest.mark.asyncio
async def test_429_respects_retry_after_then_succeeds():
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"code": "rate_limited"})
        return httpx.Response(200, json={"id": "p", "url": "u", "properties": {}})

    conn = _connector(handler)
    result = await conn.get_page(page_id="380db9f5-ca96-80b1-813d-deade3c3a1ea")
    assert result["id"] == "p"
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_429_exhausting_retries_raises_clearly():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0"}, json={"code": "rate_limited"})

    conn = _connector(handler)
    with pytest.raises(RuntimeError, match="rate-limited"):
        await conn.get_page(page_id="380db9f5-ca96-80b1-813d-deade3c3a1ea")
