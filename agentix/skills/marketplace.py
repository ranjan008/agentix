"""
SkillHub Marketplace — community skill discovery and remote install.

Phase 3 provides:
  - Local index of known community skills (bundled catalog)
  - Search by name / tag / description
  - Remote install stub (ready to wire up to https://hub.agentix.dev in Phase 4)
  - Verified publisher badge system

The bundled catalog is a YAML file at skills/catalog.yaml, updated with each release.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_CATALOG_PATH = Path(__file__).parent / "catalog.yaml"

# ---------------------------------------------------------------------------
# Bundled catalog (shipped with the package)
# ---------------------------------------------------------------------------

_BUNDLED_CATALOG: list[dict] = [
    {
        "name": "zendesk-support",
        "version": "1.3.0",
        "author": "agentix-community",
        "verified": True,
        "tags": ["support", "ticketing", "zendesk"],
        "description": "Create, update, and triage Zendesk tickets with smart routing",
        "permissions_needed": ["zendesk:read", "zendesk:write"],
        "install_url": "https://hub.agentix.dev/skills/zendesk-support/1.3.0",
    },
    {
        "name": "github-ops",
        "version": "2.0.1",
        "author": "agentix-community",
        "verified": True,
        "tags": ["devops", "github", "code"],
        "description": "Manage GitHub issues, PRs, and workflows from agents",
        "permissions_needed": ["github:read", "github:write"],
        "install_url": "https://hub.agentix.dev/skills/github-ops/2.0.1",
    },
    {
        "name": "notion-sync",
        "version": "1.0.4",
        "author": "agentix-community",
        "verified": True,
        "tags": ["productivity", "notion", "docs"],
        "description": "Read and write Notion pages, databases, and blocks",
        "permissions_needed": ["notion:read", "notion:write"],
        "install_url": "https://hub.agentix.dev/skills/notion-sync/1.0.4",
    },
    {
        "name": "slack-notifier",
        "version": "1.1.0",
        "author": "agentix-community",
        "verified": True,
        "tags": ["messaging", "slack", "notifications"],
        "description": "Send rich Slack messages, create channels, manage threads",
        "permissions_needed": ["slack:write"],
        "install_url": "https://hub.agentix.dev/skills/slack-notifier/1.1.0",
    },
    {
        "name": "data-analyzer",
        "version": "2.2.0",
        "author": "agentix-core",
        "verified": True,
        "tags": ["data", "analytics", "csv", "sql"],
        "description": "Analyse CSV/JSON data, run SQL queries, generate charts",
        "permissions_needed": ["data:read"],
        "install_url": "https://hub.agentix.dev/skills/data-analyzer/2.2.0",
    },
    {
        "name": "code-executor",
        "version": "1.5.0",
        "author": "agentix-core",
        "verified": True,
        "tags": ["code", "python", "sandbox"],
        "description": "Execute Python code in a sandboxed environment with output capture",
        "permissions_needed": ["code:execute"],
        "install_url": "https://hub.agentix.dev/skills/code-executor/1.5.0",
    },
    {
        "name": "calendar-manager",
        "version": "1.0.2",
        "author": "agentix-community",
        "verified": False,
        "tags": ["calendar", "google", "scheduling"],
        "description": "Read and create Google Calendar events",
        "permissions_needed": ["calendar:read", "calendar:write"],
        "install_url": "https://hub.agentix.dev/skills/calendar-manager/1.0.2",
    },
    {
        "name": "report-generator",
        "version": "1.0.0",
        "author": "agentix-core",
        "verified": True,
        "tags": ["reports", "pdf", "markdown"],
        "description": "Generate formatted reports in Markdown, HTML, or PDF",
        "permissions_needed": ["file:write"],
        "install_url": "https://hub.agentix.dev/skills/report-generator/1.0.0",
    },
]


class SkillMarketplace:
    """
    Search and browse the SkillHub community catalog.
    """

    def __init__(self, extra_catalog_path: Path | None = None) -> None:
        self._catalog = list(_BUNDLED_CATALOG)
        if extra_catalog_path and extra_catalog_path.exists():
            with open(extra_catalog_path) as f:
                extra = yaml.safe_load(f) or []
            self._catalog.extend(extra)

    def search(
        self,
        query: str = "",
        tags: list[str] | None = None,
        verified_only: bool = False,
    ) -> list[dict]:
        """Full-text + tag search over the catalog."""
        results = []
        q = query.lower()
        for skill in self._catalog:
            if verified_only and not skill.get("verified"):
                continue
            if tags:
                skill_tags = set(skill.get("tags", []))
                if not any(t in skill_tags for t in tags):
                    continue
            if q:
                haystack = (
                    skill["name"] + " " +
                    skill.get("description", "") + " " +
                    " ".join(skill.get("tags", []))
                ).lower()
                if q not in haystack:
                    continue
            results.append(skill)
        return results

    def get(self, name: str) -> dict | None:
        for skill in self._catalog:
            if skill["name"] == name:
                return skill
        return None

    def list_all(self, verified_only: bool = False) -> list[dict]:
        if verified_only:
            return [s for s in self._catalog if s.get("verified")]
        return list(self._catalog)

    def install(self, name: str, skillhub, db_path: str = "data/agentix.db") -> dict:
        """
        Install a skill from the marketplace.
        Phase 3: downloads spec from install_url (stub).
        Phase 4: full remote package download with signature verification.
        """
        entry = self.get(name)
        if not entry:
            raise ValueError(f"Skill '{name}' not found in marketplace catalog")

        logger.info(
            "Marketplace install: %s v%s (verified=%s)",
            name, entry["version"], entry["verified"],
        )

        # Phase 3 stub: register catalog entry directly into DB
        from agentix.storage.state_store import StateStore
        store = StateStore(db_path)
        spec = {
            "apiVersion": "agentix/v1",
            "kind": "Skill",
            "metadata": {
                "name": entry["name"],
                "version": entry["version"],
                "author": entry["author"],
                "verified": entry["verified"],
                "tags": entry["tags"],
            },
            "spec": {
                "description": entry["description"],
                "rbac": {"permissions_needed": entry["permissions_needed"]},
            },
        }
        store.install_skill(name, entry["version"], "hub", spec)
        return entry
