"""
Skill Engine — loads skills, provides their instructions and tool schemas.

Skills are loaded in priority order:
  1. Built-in (bundled with agentix core)
  2. Local (relative path in agent spec, e.g. ./skills/my-skill)
  3. Installed via SkillHub (stored in DB)
"""
from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from agentix.agent_runtime.tool_executor import ToolExecutor
    from agentix.storage.state_store import StateStore

logger = logging.getLogger(__name__)

# Map skill name -> module path for built-in skills
# Both hyphenated and underscore variants are accepted.
_BUILTIN_SKILLS: dict[str, str] = {
    # Web
    "web-search": "agentix.skills.builtin.web_search",
    "web_search": "agentix.skills.builtin.web_search",
    # web_fetch lives in the same module — referencing it as a skill registers the tool
    "web-fetch": "agentix.skills.builtin.web_search",
    "web_fetch": "agentix.skills.builtin.web_search",
    # File / browser — the skill bundle and individual tool names both work
    "file-ops": "agentix.skills.builtin.file_ops",
    "file_ops": "agentix.skills.builtin.file_ops",
    # Individual tool aliases: listing file_read/write/list as a skill loads the full file_ops module
    "file_read": "agentix.skills.builtin.file_ops",
    "file_write": "agentix.skills.builtin.file_ops",
    "file_list": "agentix.skills.builtin.file_ops",
    "browser": "agentix.skills.builtin.browser",
    # Email composer (inbound parsing helper — distinct from send_email)
    "email-composer": "agentix.skills.builtin.email_composer",
    "email_composer": "agentix.skills.builtin.email_composer",
    # Outbound channel push skills (for use by cron agents)
    "send_telegram": "agentix.skills.builtin.send_telegram",
    "send-telegram": "agentix.skills.builtin.send_telegram",
    "send_slack": "agentix.skills.builtin.send_slack",
    "send-slack": "agentix.skills.builtin.send_slack",
    "send_email": "agentix.skills.builtin.send_email",
    "send-email": "agentix.skills.builtin.send_email",
    "send_teams": "agentix.skills.builtin.send_teams",
    "send-teams": "agentix.skills.builtin.send_teams",
    "send_whatsapp": "agentix.skills.builtin.send_whatsapp",
    "send-whatsapp": "agentix.skills.builtin.send_whatsapp",
}


class SkillEngine:
    def __init__(self, store: "StateStore", agent_dir: "Path | str | None" = None) -> None:
        self.store = store
        self.agent_dir = Path(agent_dir) if agent_dir else None

    def load_skills(self, skill_names: list[str]) -> list[str]:
        """
        Load skills and return their instruction strings (for the system prompt).

        Checks both ``INSTRUCTIONS`` (older convention) and ``SKILL_INSTRUCTIONS``
        (browser skill / newer convention) so both styles work.
        """
        instructions = []
        for name in skill_names:
            try:
                module = self._import_skill(name)
                text = getattr(module, "INSTRUCTIONS", None) or getattr(module, "SKILL_INSTRUCTIONS", None)
                if text:
                    instructions.append(text)
            except Exception as e:
                logger.warning("Could not load skill '%s': %s", name, e)
        return instructions

    def get_tool_schemas(self, skill_names: list[str]) -> list[dict]:
        """Return all tool schemas contributed by the listed skills.

        Checks both ``TOOL_SCHEMAS`` and ``SKILL_TOOLS`` (list of tool names
        whose schemas are retrieved from the tool executor registry).
        """
        from agentix.agent_runtime.tool_executor import _TOOL_REGISTRY
        schemas = []
        for name in skill_names:
            try:
                module = self._import_skill(name)
                if hasattr(module, "TOOL_SCHEMAS"):
                    schemas.extend(module.TOOL_SCHEMAS)
                elif hasattr(module, "SKILL_TOOLS"):
                    for tool_name in module.SKILL_TOOLS:
                        fn = _TOOL_REGISTRY.get(tool_name)
                        if fn and hasattr(fn, "_tool_schema"):
                            schemas.append(fn._tool_schema)  # type: ignore[attr-defined]
            except Exception as e:
                logger.warning("Could not get schemas for skill '%s': %s", name, e)
        return schemas

    def register_skill_tools(self, skill_names: list[str], executor: "ToolExecutor") -> None:
        """Register each skill's tools into the tool executor.

        Tools decorated with ``@tool`` self-register on import, so for skills
        using that pattern we just need to import the module.  Skills using the
        older ``TOOLS`` dict pattern are registered explicitly.
        """
        from agentix.agent_runtime.tool_executor import register_tool
        for name in skill_names:
            try:
                module = self._import_skill(name)
                if hasattr(module, "TOOLS"):
                    for tool_name, fn in module.TOOLS.items():
                        register_tool(tool_name, fn)
                # @tool-decorated functions self-register on import; nothing extra needed
            except Exception as e:
                logger.warning("Could not register tools for skill '%s': %s", name, e)

    def _import_skill(self, name: str):
        # 1. Built-in
        if name in _BUILTIN_SKILLS:
            return importlib.import_module(_BUILTIN_SKILLS[name])

        # 2. Local path (starts with ./ or /)
        if name.startswith("./") or name.startswith("/"):
            skill_dir = Path(name)
            skill_init = skill_dir / "__init__.py"
            if skill_init.exists():
                import importlib.util as _importlib_util
                spec = _importlib_util.spec_from_file_location(f"skill_{skill_dir.name}", skill_init)
                if spec is None or spec.loader is None:
                    raise ImportError(f"Cannot load skill module from {skill_init}")
                mod = _importlib_util.module_from_spec(spec)
                spec.loader.exec_module(mod)  # type: ignore[union-attr]
                return mod

        # 3. Agent-local YAML skill: {agent_dir}/skills/{name}/skill.yaml
        if self.agent_dir:
            yaml_path = self.agent_dir / "skills" / name / "skill.yaml"
            if not yaml_path.exists():
                yaml_path = self.agent_dir / "skills" / name / "skill.yml"
            if yaml_path.exists():
                return _YamlSkillStub(yaml_path)

        # 4. Installed skill from DB
        db_skill = self.store.get_skill(name)
        if db_skill:
            logger.info("Loaded installed skill '%s' from DB (no runtime module)", name)
            # Return a minimal stub so the agent still starts
            return _SkillStub(db_skill["spec"])

        raise ImportError(f"Skill '{name}' not found (not built-in, not local, not installed)")


class _SkillStub:
    """Minimal stub for skills that are registered in the DB but have no Python module."""
    def __init__(self, spec: dict) -> None:
        self.INSTRUCTIONS = spec.get("description", "")
        self.TOOL_SCHEMAS: list[dict] = []
        self.TOOLS: dict = {}


class _YamlSkillStub:
    """Stub for agent-local YAML-only skills (skill.yaml with instructions but no Python module)."""
    def __init__(self, yaml_path: Path) -> None:
        import yaml as _yaml
        with open(yaml_path, encoding="utf-8") as fh:
            data = _yaml.safe_load(fh)
        skill_spec = data.get("spec", {})
        self.INSTRUCTIONS: str = skill_spec.get("instructions", "")
        self.TOOL_SCHEMAS: list[dict] = []
        self.TOOLS: dict = {}
