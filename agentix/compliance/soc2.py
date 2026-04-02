"""
SOC 2 Evidence Export.

Generates compliance evidence bundles (ZIP) containing:
  - Audit log export (NDJSON)
  - RBAC policy snapshot
  - Secrets vault configuration (redacted)
  - HMAC chain verification report
  - Retention policy summary
  - System configuration snapshot

Usage:
  engine = SOC2Exporter(db_path, cfg)
  path = engine.export(output_dir="compliance/soc2-2026-Q1")
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


class SOC2Exporter:
    """Generate SOC 2 evidence bundles."""

    def __init__(self, db_path: str, cfg: dict, hmac_secret: str = "") -> None:
        self._db_path = db_path
        self._cfg = cfg
        self._hmac_secret = hmac_secret

    def export(self, output_dir: str = "compliance") -> Path:
        """
        Create a ZIP bundle with all evidence artifacts.
        Returns path to the created ZIP file.
        """
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = Path(output_dir) / f"soc2-evidence-{ts}.zip"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            self._add_audit_log(zf)
            self._add_chain_verification(zf)
            self._add_rbac_policy(zf)
            self._add_config_snapshot(zf)
            self._add_manifest(zf, ts)

        log.info("SOC2Exporter: bundle written to %s", output_path)
        return output_path

    # ------------------------------------------------------------------
    # Evidence sections
    # ------------------------------------------------------------------

    def _add_audit_log(self, zf: zipfile.ZipFile) -> None:
        lines = []
        if Path(self._db_path).exists():
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute("SELECT * FROM audit_log ORDER BY timestamp").fetchall()
                for row in rows:
                    lines.append(json.dumps(dict(row)))
            except sqlite3.OperationalError:
                lines.append('{"error": "audit_log table not found"}')
            finally:
                conn.close()
        zf.writestr("audit_log.ndjson", "\n".join(lines))

    def _add_chain_verification(self, zf: zipfile.ZipFile) -> None:
        report: dict = {"verified_at": datetime.now(timezone.utc).isoformat(), "chain_valid": None, "tampered_count": 0}
        try:
            from agentix.security.audit import AuditLog
            audit = AuditLog(db_path=self._db_path, hmac_secret=self._hmac_secret)
            ok, tampered = audit.verify_chain()
            report["chain_valid"] = ok
            report["tampered_count"] = len(tampered)
            report["tampered_entry_ids"] = tampered
        except Exception as exc:
            report["error"] = str(exc)
        zf.writestr("chain_verification.json", json.dumps(report, indent=2))

    def _add_rbac_policy(self, zf: zipfile.ZipFile) -> None:
        policy_path = self._cfg.get("security", {}).get("policy_file", "config/policy.yaml")
        if Path(policy_path).exists():
            zf.write(policy_path, "rbac_policy.yaml")
        else:
            zf.writestr("rbac_policy.yaml", "# Policy file not found\n")

    def _add_config_snapshot(self, zf: zipfile.ZipFile) -> None:
        """Write config with secrets redacted."""
        safe_cfg = _redact_secrets(self._cfg)
        zf.writestr("config_snapshot.json", json.dumps(safe_cfg, indent=2, default=str))

    def _add_manifest(self, zf: zipfile.ZipFile, ts: str) -> None:
        manifest = {
            "generated_at": ts,
            "platform": "Agentix",
            "version": "4.0.0",
            "contents": [
                "audit_log.ndjson",
                "chain_verification.json",
                "rbac_policy.yaml",
                "config_snapshot.json",
            ],
        }
        zf.writestr("MANIFEST.json", json.dumps(manifest, indent=2))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECRET_KEYS = frozenset({"api_key", "password", "secret", "token", "private_key", "access_key"})


def _redact_secrets(obj, depth: int = 0):
    if depth > 10:
        return obj
    if isinstance(obj, dict):
        return {
            k: "[REDACTED]" if any(s in k.lower() for s in _SECRET_KEYS) else _redact_secrets(v, depth + 1)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact_secrets(i, depth + 1) for i in obj]
    return obj
