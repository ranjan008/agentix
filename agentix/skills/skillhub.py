"""
SkillHub Registry — versioned, signed skill package management.

Phase 2 implementation:
  - Local registry (installed skills stored in SQLite + filesystem)
  - Package format: YAML spec + optional Python module directory
  - Integrity: SHA-256 content hash stored at install time
  - Signature verification: Ed25519 signatures from verified publishers (optional)
  - Future: remote SkillHub API at https://hub.agentix.dev

Package layout (installed skill directory):
  ~/.agentix/skills/<name>/<version>/
    skill.yaml          — the skill spec
    __init__.py         — Python implementation (optional)
    SKILL.md            — LLM instructions (optional, referenced in spec)
    MANIFEST            — file hashes for integrity check
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

import yaml

from agentix.storage.state_store import StateStore

logger = logging.getLogger(__name__)

_SKILLS_HOME = Path(os.environ.get("AGENTIX_SKILLS_DIR", Path.home() / ".agentix" / "skills"))


# ---------------------------------------------------------------------------
# Integrity helpers
# ---------------------------------------------------------------------------

def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _hash_dict(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _build_manifest(skill_dir: Path) -> dict[str, str]:
    """Build {relative_path: sha256} manifest for all files in skill_dir."""
    manifest = {}
    for f in sorted(skill_dir.rglob("*")):
        if f.is_file() and f.name != "MANIFEST":
            manifest[str(f.relative_to(skill_dir))] = _hash_file(f)
    return manifest


def _verify_manifest(skill_dir: Path, manifest: dict[str, str]) -> bool:
    """Return True if all files match their recorded hashes."""
    current = _build_manifest(skill_dir)
    if current != manifest:
        changed = set(current) ^ set(manifest)
        logger.warning("Skill integrity check failed — changed files: %s", changed)
        return False
    return True


# ---------------------------------------------------------------------------
# Publisher verification (Ed25519)
# ---------------------------------------------------------------------------

_TRUSTED_PUBLISHERS: dict[str, str] = {
    # publisher_id -> base64-encoded Ed25519 public key
    # Populated at platform setup time
}


def verify_skill_signature(spec: dict, signature_b64: str, publisher_id: str) -> bool:
    """
    Verify an Ed25519 signature over the skill spec.
    Returns True if signature is valid and publisher is trusted.
    """
    if publisher_id not in _TRUSTED_PUBLISHERS:
        logger.warning("Publisher '%s' not in trusted list", publisher_id)
        return False
    try:
        from cryptography.hazmat.primitives.serialization import load_der_public_key
        import base64
        pub_bytes = base64.b64decode(_TRUSTED_PUBLISHERS[publisher_id])
        pub_key = load_der_public_key(pub_bytes)
        sig = base64.b64decode(signature_b64)
        pub_key.verify(sig, _hash_dict(spec).encode())
        return True
    except ImportError:
        logger.warning("cryptography package not installed — signature verification skipped")
        return False
    except Exception as e:
        logger.warning("Signature verification failed: %s", e)
        return False


def register_trusted_publisher(publisher_id: str, public_key_b64: str) -> None:
    _TRUSTED_PUBLISHERS[publisher_id] = public_key_b64


# ---------------------------------------------------------------------------
# SkillHub
# ---------------------------------------------------------------------------

class SkillHub:
    """
    Manages the local skill registry: install, uninstall, list, verify.
    """

    def __init__(self, store: StateStore, skills_home: Path = _SKILLS_HOME) -> None:
        self.store = store
        self.skills_home = skills_home
        self.skills_home.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Install
    # ------------------------------------------------------------------

    def install_from_yaml(self, spec_path: str | Path, source: str = "local") -> dict:
        """
        Install a skill from a YAML spec file (with optional sibling Python files).
        Returns the installed skill record.
        """
        spec_path = Path(spec_path).resolve()
        with open(spec_path) as f:
            spec = yaml.safe_load(f)

        name = spec["metadata"]["name"]
        version = spec["metadata"].get("version", "0.0.1")

        skill_dir = self.skills_home / name / version
        skill_dir.mkdir(parents=True, exist_ok=True)

        # Copy spec
        shutil.copy2(spec_path, skill_dir / "skill.yaml")

        # Copy sibling Python files if skill_path is a directory
        if spec_path.parent.is_dir():
            for f in spec_path.parent.iterdir():
                if f.suffix in (".py", ".md") and f.name != "skill.yaml":
                    shutil.copy2(f, skill_dir / f.name)

        # Build and write manifest
        manifest = _build_manifest(skill_dir)
        (skill_dir / "MANIFEST").write_text(json.dumps(manifest, indent=2))

        # Persist to DB
        record = {
            "name": name,
            "version": version,
            "source": source,
            "install_path": str(skill_dir),
            "spec_hash": _hash_dict(spec),
            "installed_at": time.time(),
            "verified": False,
        }
        self.store.install_skill(name, version, source, {**spec, **record})
        logger.info("Installed skill: %s v%s (%s)", name, version, source)
        return record

    def install_from_git(self, git_url: str) -> dict:
        """Clone a git repo and install the skill from it."""
        import subprocess
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                ["git", "clone", "--depth=1", git_url, tmpdir],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"git clone failed: {result.stderr}")
            # Look for skill.yaml in root or skills/ subdirectory
            for pattern in ("skill.yaml", "*/skill.yaml"):
                matches = list(Path(tmpdir).glob(pattern))
                if matches:
                    return self.install_from_yaml(matches[0], source="git")
        raise FileNotFoundError(f"No skill.yaml found in {git_url}")

    # ------------------------------------------------------------------
    # Uninstall
    # ------------------------------------------------------------------

    def uninstall(self, name: str, version: str | None = None) -> None:
        skill_dir = self.skills_home / name
        if version:
            skill_dir = skill_dir / version
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
        # Remove from DB
        import sqlite3
        conn = sqlite3.connect(str(self.store.db_path))
        if version:
            conn.execute("DELETE FROM skills WHERE name=? AND version=?", (name, version))
        else:
            conn.execute("DELETE FROM skills WHERE name=?", (name,))
        conn.commit()
        conn.close()
        logger.info("Uninstalled skill: %s", name)

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify(self, name: str) -> bool:
        """Check file integrity of an installed skill."""
        skill_record = self.store.get_skill(name)
        if not skill_record:
            raise ValueError(f"Skill '{name}' is not installed")
        install_path = skill_record["spec"].get("install_path")
        if not install_path:
            return True  # built-in or DB-only — nothing to verify
        skill_dir = Path(install_path)
        manifest_path = skill_dir / "MANIFEST"
        if not manifest_path.exists():
            logger.warning("No MANIFEST for skill '%s'", name)
            return False
        manifest = json.loads(manifest_path.read_text())
        return _verify_manifest(skill_dir, manifest)

    # ------------------------------------------------------------------
    # List / info
    # ------------------------------------------------------------------

    def list_installed(self) -> list[dict]:
        return self.store.list_skills()

    def get_skill_path(self, name: str) -> Path | None:
        record = self.store.get_skill(name)
        if not record:
            return None
        path = record["spec"].get("install_path")
        return Path(path) if path else None

    # ------------------------------------------------------------------
    # Remote SkillHub (stub — Phase 3+)
    # ------------------------------------------------------------------

    def search_hub(self, query: str) -> list[dict]:
        """
        Search the remote SkillHub. Not yet implemented in Phase 2.
        Returns empty list with a notice.
        """
        logger.info("Remote SkillHub search not yet available. Query: %s", query)
        return []
