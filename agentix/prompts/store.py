"""
PromptStore — versioned system prompt management.

Stores prompt templates in SQLite with full revision history.
Agents can reference a prompt by name and optional version;
if version is omitted, the latest active version is used.

Schema:
  prompts      — one row per version (name + version + content)
  prompt_tags  — arbitrary key:value tags per prompt version

Features:
  - Semantic versioning (major.minor.patch) or auto-increment
  - Atomic publish/rollback (toggle active flag)
  - Variable substitution: {{var}} → render(values)
  - Draft support: unpublished prompts don't break production
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


_SCHEMA = """
CREATE TABLE IF NOT EXISTS prompts (
    id          TEXT PRIMARY KEY,          -- prompt_{uuid}
    name        TEXT NOT NULL,             -- logical name (e.g. "research-system")
    version     TEXT NOT NULL,             -- semver string e.g. "1.2.0"
    content     TEXT NOT NULL,             -- prompt template text
    variables   TEXT NOT NULL DEFAULT '[]', -- JSON list of expected variable names
    status      TEXT NOT NULL DEFAULT 'draft',  -- draft | active | archived
    description TEXT,
    author      TEXT,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    created_at  REAL NOT NULL,
    tags        TEXT NOT NULL DEFAULT '{}'   -- JSON dict
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_prompts_name_version ON prompts(name, version, tenant_id);
CREATE INDEX IF NOT EXISTS idx_prompts_name_status ON prompts(name, status, tenant_id);
"""

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


class PromptVersion:
    """Represents a single versioned prompt."""

    def __init__(self, row: dict) -> None:
        self._row = row

    @property
    def id(self) -> str:
        return self._row["id"]

    @property
    def name(self) -> str:
        return self._row["name"]

    @property
    def version(self) -> str:
        return self._row["version"]

    @property
    def content(self) -> str:
        return self._row["content"]

    @property
    def status(self) -> str:
        return self._row["status"]

    @property
    def variables(self) -> list[str]:
        return json.loads(self._row.get("variables") or "[]")

    def render(self, values: dict[str, str]) -> str:
        """Substitute {{var}} placeholders with provided values."""
        def _replace(match: re.Match) -> str:
            key = match.group(1)
            if key not in values:
                raise KeyError(f"Missing template variable: '{key}'")
            return str(values[key])
        return _VAR_RE.sub(_replace, self.content)

    def to_dict(self) -> dict:
        d = dict(self._row)
        d["variables"] = self.variables
        d["tags"] = json.loads(d.get("tags") or "{}")
        return d


class PromptStore:
    """SQLite-backed prompt version store."""

    def __init__(self, db_path: str | Path = "data/agentix.db") -> None:
        self.db_path = Path(db_path)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _tx(self):
        conn = self._conn()
        try:
            yield conn.cursor()
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def create(
        self,
        name: str,
        version: str,
        content: str,
        variables: list[str] | None = None,
        description: str | None = None,
        author: str | None = None,
        tenant_id: str = "default",
        tags: dict | None = None,
        status: str = "draft",
    ) -> PromptVersion:
        """Create a new prompt version. Status defaults to 'draft'."""
        pid = f"prompt_{uuid.uuid4().hex[:12]}"
        # Auto-detect variables from content if not provided
        if variables is None:
            variables = sorted(set(_VAR_RE.findall(content)))
        with self._tx() as cur:
            cur.execute(
                """INSERT INTO prompts
                   (id, name, version, content, variables, status, description, author, tenant_id, created_at, tags)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (pid, name, version, content, json.dumps(variables), status,
                 description, author, tenant_id, time.time(), json.dumps(tags or {})),
            )
        return self.get_by_id(pid)  # type: ignore[return-value]

    def publish(self, prompt_id: str) -> None:
        """Publish a draft prompt (set status=active)."""
        with self._tx() as cur:
            cur.execute("UPDATE prompts SET status='active' WHERE id=?", (prompt_id,))

    def archive(self, prompt_id: str) -> None:
        """Archive a prompt version (no longer returned by get_latest)."""
        with self._tx() as cur:
            cur.execute("UPDATE prompts SET status='archived' WHERE id=?", (prompt_id,))

    def update_content(self, prompt_id: str, content: str, variables: list[str] | None = None) -> None:
        """Update content of a draft prompt. Cannot update active/archived."""
        if variables is None:
            variables = sorted(set(_VAR_RE.findall(content)))
        with self._tx() as cur:
            cur.execute(
                "UPDATE prompts SET content=?, variables=? WHERE id=? AND status='draft'",
                (content, json.dumps(variables), prompt_id),
            )

    def delete(self, prompt_id: str) -> None:
        """Permanently delete a draft prompt."""
        with self._tx() as cur:
            cur.execute("DELETE FROM prompts WHERE id=? AND status='draft'", (prompt_id,))

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_by_id(self, prompt_id: str) -> PromptVersion | None:
        with self._tx() as cur:
            row = cur.execute("SELECT * FROM prompts WHERE id=?", (prompt_id,)).fetchone()
        return PromptVersion(dict(row)) if row else None

    def get_latest(
        self, name: str, tenant_id: str = "default", status: str = "active"
    ) -> PromptVersion | None:
        """Return the most-recently-created active version of a prompt."""
        with self._tx() as cur:
            row = cur.execute(
                """SELECT * FROM prompts WHERE name=? AND tenant_id=? AND status=?
                   ORDER BY created_at DESC LIMIT 1""",
                (name, tenant_id, status),
            ).fetchone()
        return PromptVersion(dict(row)) if row else None

    def get_version(
        self, name: str, version: str, tenant_id: str = "default"
    ) -> PromptVersion | None:
        with self._tx() as cur:
            row = cur.execute(
                "SELECT * FROM prompts WHERE name=? AND version=? AND tenant_id=?",
                (name, version, tenant_id),
            ).fetchone()
        return PromptVersion(dict(row)) if row else None

    def list_versions(
        self,
        name: str | None = None,
        tenant_id: str = "default",
        status: str | None = None,
    ) -> list[PromptVersion]:
        sql = "SELECT * FROM prompts WHERE tenant_id=?"
        params: list = [tenant_id]
        if name:
            sql += " AND name=?"
            params.append(name)
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY name, created_at DESC"
        with self._tx() as cur:
            rows = cur.execute(sql, params).fetchall()
        return [PromptVersion(dict(r)) for r in rows]

    def list_names(self, tenant_id: str = "default") -> list[str]:
        """Return distinct prompt names."""
        with self._tx() as cur:
            rows = cur.execute(
                "SELECT DISTINCT name FROM prompts WHERE tenant_id=? ORDER BY name",
                (tenant_id,),
            ).fetchall()
        return [r["name"] for r in rows]
