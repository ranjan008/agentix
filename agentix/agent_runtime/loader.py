"""
Agent Loader — reads an agent YAML definition and validates it.

System prompt resolution order (first match wins):
  1. spec.system_prompt_file  — path to a .md file, relative to the YAML
  2. spec.prompt_sections     — list of {file:} or {text:} blocks, concatenated
  3. spec.system_prompt       — inline string
  4. spec.instructions        — legacy inline string (still supported)
"""
from __future__ import annotations

from pathlib import Path

import yaml


REQUIRED_FIELDS = ("metadata.name", "spec.model.provider", "spec.model.model_id")


class AgentLoadError(Exception):
    pass


def load_agent_spec(path: str | Path) -> dict:
    """Load and minimally validate an agent YAML file.

    Also resolves any system_prompt_file / prompt_sections references so the
    rest of the runtime always sees a plain ``spec.system_prompt`` string.
    """
    path = Path(path).resolve()
    if not path.exists():
        raise AgentLoadError(f"Agent spec not found: {path}")

    with open(path, encoding="utf-8") as f:
        spec = yaml.safe_load(f)

    if spec.get("apiVersion") != "agentix/v1":
        raise AgentLoadError("Agent spec must have apiVersion: agentix/v1")
    if spec.get("kind") != "Agent":
        raise AgentLoadError("Agent spec must have kind: Agent")

    if not spec.get("metadata", {}).get("name"):
        raise AgentLoadError("Missing required field: metadata.name")

    # Resolve file-based system prompts so downstream always has a plain string
    _resolve_system_prompt(spec, base_dir=path.parent)

    return spec


def _resolve_system_prompt(spec: dict, base_dir: Path) -> None:
    """
    Mutates spec['spec'] to set a resolved 'system_prompt' string.

    Supports three patterns:

    Pattern 1 — single file reference:
        spec:
          system_prompt_file: prompts/system.md

    Pattern 2 — ordered sections (mix of files and inline text):
        spec:
          prompt_sections:
            - file: prompts/identity.md
            - file: prompts/tools.md
            - text: "Always respond in English."
            - file: prompts/constraints.md

    Pattern 3 — inline string (existing behaviour, unchanged):
        spec:
          system_prompt: |
            You are a helpful assistant.
    """
    agent_spec = spec.get("spec", {})

    # Pattern 1: single file
    prompt_file = agent_spec.pop("system_prompt_file", None)
    if prompt_file:
        resolved = (base_dir / prompt_file).resolve()
        if not resolved.exists():
            raise AgentLoadError(f"system_prompt_file not found: {resolved}")
        agent_spec["system_prompt"] = resolved.read_text(encoding="utf-8").strip()
        return

    # Pattern 2: ordered sections
    sections = agent_spec.pop("prompt_sections", None)
    if sections:
        parts: list[str] = []
        for section in sections:
            if "file" in section:
                resolved = (base_dir / section["file"]).resolve()
                if not resolved.exists():
                    raise AgentLoadError(f"prompt_sections file not found: {resolved}")
                parts.append(resolved.read_text(encoding="utf-8").strip())
            elif "text" in section:
                parts.append(section["text"].strip())
        agent_spec["system_prompt"] = "\n\n---\n\n".join(parts)
        return

    # Pattern 3: inline — nothing to do, already a plain string


def find_agent_spec(agent_id: str, agents_dir: str | Path = "agents") -> Path | None:
    """Locate an agent YAML file by agent_id."""
    agents_dir = Path(agents_dir)
    for pattern in (f"{agent_id}.yaml", f"{agent_id}.yml", f"**/{agent_id}.yaml"):
        matches = list(agents_dir.glob(pattern))
        if matches:
            return matches[0]
    return None
