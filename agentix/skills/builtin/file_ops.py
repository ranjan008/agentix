"""
Built-in Skill: file-ops
Safe file read/write/list operations within a sandboxed working directory.
"""
from __future__ import annotations

import os
from pathlib import Path

# Agents are restricted to this directory (resolved at import time)
_WORKDIR = Path(os.environ.get("AGENTIX_WORKDIR", "data/workdir")).resolve()
_WORKDIR.mkdir(parents=True, exist_ok=True)

INSTRUCTIONS = """
## File Operations Skill
You can read, write, and list files using these tools:
- `file_read`: Read the contents of a file.
- `file_write`: Write content to a file (creates or overwrites).
- `file_list`: List files in a directory.

All file paths are relative to the agent's working directory. You cannot access paths outside it.
""".strip()


def _safe_path(path: str) -> Path:
    """Resolve path and ensure it stays within the workdir sandbox."""
    resolved = (_WORKDIR / path).resolve()
    if not str(resolved).startswith(str(_WORKDIR)):
        raise PermissionError(f"Access denied: path outside working directory: {path}")
    return resolved


def _file_read(path: str) -> dict:
    try:
        full = _safe_path(path)
        if not full.exists():
            return {"path": path, "content": None, "error": "File not found"}
        content = full.read_text(errors="replace")
        return {"path": path, "content": content, "size": len(content)}
    except PermissionError as e:
        return {"path": path, "content": None, "error": str(e)}
    except Exception as e:
        return {"path": path, "content": None, "error": str(e)}


def _file_write(path: str, content: str) -> dict:
    try:
        full = _safe_path(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        return {"path": path, "written": True, "size": len(content)}
    except PermissionError as e:
        return {"path": path, "written": False, "error": str(e)}
    except Exception as e:
        return {"path": path, "written": False, "error": str(e)}


def _file_list(directory: str = ".") -> dict:
    try:
        full = _safe_path(directory)
        if not full.is_dir():
            return {"directory": directory, "files": [], "error": "Not a directory"}
        files = [
            {"name": f.name, "type": "dir" if f.is_dir() else "file", "size": f.stat().st_size if f.is_file() else 0}
            for f in sorted(full.iterdir())
        ]
        return {"directory": directory, "files": files}
    except PermissionError as e:
        return {"directory": directory, "files": [], "error": str(e)}
    except Exception as e:
        return {"directory": directory, "files": [], "error": str(e)}


TOOLS = {
    "file_read": _file_read,
    "file_write": _file_write,
    "file_list": _file_list,
}

TOOL_SCHEMAS = [
    {
        "name": "file_read",
        "description": "Read the contents of a file in the agent's working directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "file_write",
        "description": "Write text content to a file. Creates the file (and parent directories) if it doesn't exist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path"},
                "content": {"type": "string", "description": "Text content to write"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "file_list",
        "description": "List files and directories in the agent's working directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "Relative directory path (default: root)", "default": "."},
            },
        },
    },
]
