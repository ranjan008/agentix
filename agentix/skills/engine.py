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
_BUILTIN_SKILLS: dict[str, str] = {
    "web-search": "agentix.skills.builtin.web_search",
    "file-ops": "agentix.skills.builtin.file_ops",
    "email-composer": "agentix.skills.builtin.email_composer",
}


class SkillEngine:
    def __init__(self, store: "StateStore") -> None:
        self.store = store

    def load_skills(self, skill_names: list[str]) -> list[str]:
        """
        Load skills and return their instruction strings (for the system prompt).
        """
        instructions = []
        for name in skill_names:
            try:
                module = self._import_skill(name)
                if hasattr(module, "INSTRUCTIONS"):
                    instructions.append(module.INSTRUCTIONS)
            except Exception as e:
                logger.warning("Could not load skill '%s': %s", name, e)
        return instructions

    def get_tool_schemas(self, skill_names: list[str]) -> list[dict]:
        """Return all tool schemas contributed by the listed skills."""
        schemas = []
        for name in skill_names:
            try:
                module = self._import_skill(name)
                if hasattr(module, "TOOL_SCHEMAS"):
                    schemas.extend(module.TOOL_SCHEMAS)
            except Exception as e:
                logger.warning("Could not get schemas for skill '%s': %s", name, e)
        return schemas

    def register_skill_tools(self, skill_names: list[str], executor: "ToolExecutor") -> None:
        """Register each skill's tools into the tool executor."""
        from agentix.agent_runtime.tool_executor import register_tool
        for name in skill_names:
            try:
                module = self._import_skill(name)
                if hasattr(module, "TOOLS"):
                    for tool_name, fn in module.TOOLS.items():
                        register_tool(tool_name, fn)
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
                mod = _importlib_util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod

        # 3. Installed skill from DB
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
