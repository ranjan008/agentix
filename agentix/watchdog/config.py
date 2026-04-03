"""
Watchdog configuration loader.
Reads watchdog.yaml and resolves ${ENV_VAR} references.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml


_ENV_RE = re.compile(r"\$\{([^}]+)\}")


def _resolve_env(value: Any) -> Any:
    """Recursively replace ${VAR} in strings with env values."""
    if isinstance(value, str):
        def _sub(m: re.Match) -> str:
            return os.environ.get(m.group(1), m.group(0))
        return _ENV_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    return value


def load_config(path: str | Path = "config/watchdog.yaml") -> dict:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Watchdog config not found: {path}")
    with open(path) as f:
        raw = yaml.safe_load(f)
    return _resolve_env(raw.get("watchdog", raw))
