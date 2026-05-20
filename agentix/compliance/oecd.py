"""
OECD Due Diligence Report Generator.

Packages agentix's existing compliance evidence into a structured 6-step report
aligned with the OECD Due Diligence Guidance for Responsible AI (Feb 2026).

Target: Group 2 enterprises — AI system developers and deployers.

Steps mapped to agentix evidence:
  Step 1 — Embed Responsible AI Policy       → RBAC policy file + security layers
  Step 2 — Identify & Assess Adverse Impacts → EvalRunner results (adverse_impact scorer)
  Step 3 — Cease, Prevent & Mitigate         → PII redaction, GDPR erasure, consent, RBAC
  Step 4 — Track & Communicate               → ai.invocation audit entries (value-chain)
  Step 5 — Audit Evidence                    → HMAC-chained audit log verification
  Step 6 — Remediation                       → RemediationLog open/resolved items

Usage:
    from agentix.compliance.oecd import OECDDueDiligenceReport
    report = OECDDueDiligenceReport(db_path="data/agentix.db", cfg=cfg)
    zip_path = report.export(output_dir="compliance/oecd-2026-Q2")
"""
from __future__ import annotations

import json
import logging
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_FRAMEWORK_VERSION = "OECD-DDG-RAI-Feb2026"


class OECDDueDiligenceReport:
    """Generate an OECD RBC Due Diligence evidence bundle for agentix deployments."""

    def __init__(
        self,
        db_path: str,
        cfg: dict,
        hmac_secret: str = "",
        policy_file: str = "config/policy.yaml",
        period_days: int = 90,
    ) -> None:
        self._db_path = db_path
        self._cfg = cfg
        self._hmac_secret = hmac_secret
        self._policy_file = policy_file
        self._period_days = period_days
        self._since = datetime.now(timezone.utc).timestamp() - (period_days * 86400)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def export(self, output_dir: str = "compliance") -> Path:
        """Build and write a ZIP bundle. Returns the path to the ZIP file."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = Path(output_dir) / f"oecd-due-diligence-{ts}.zip"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        report = self._build_report()
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("oecd_due_diligence_report.json",
                        json.dumps(report, indent=2, default=str))
            self._add_policy(zf)
            self._add_ai_invocations(zf)
            self._add_pii_events(zf)
            self._add_eval_results(zf)
            self._add_remediation_log(zf)
            self._add_audit_chain_sample(zf)
            self._add_manifest(zf, ts)

        log.info("OECDDueDiligenceReport written to %s", output_path)
        return output_path

    def _build_report(self) -> dict:
        return {
            "framework": _FRAMEWORK_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "platform": "Agentix",
            "platform_version": self._cfg.get("version", "unknown"),
            "reporting_period_days": self._period_days,
            "enterprise_group": "Group 2 — AI System Developer/Deployer",
            "steps": {
                "step1_policy":           self._step1_policy(),
                "step2_impact_assessment": self._step2_impact_assessment(),
                "step3_prevent_mitigate":  self._step3_prevent_mitigate(),
                "step4_track_communicate": self._step4_track_communicate(),
                "step5_audit_evidence":    self._step5_audit_evidence(),
                "step6_remediation":       self._step6_remediation(),
            },
        }

    # ------------------------------------------------------------------
    # Step 1 — Embed responsible AI policy
    # ------------------------------------------------------------------

    def _step1_policy(self) -> dict:
        return {
            "description": "Responsible AI policy and governance commitments",
            "policy_file": self._policy_file,
            "policy_on_file": Path(self._policy_file).exists(),
            "security_layers": [
                "Channel authentication (HMAC / mTLS)",
                "Identity verification (JWT / OAuth2 / API key vs IdP)",
                "RBAC authorisation (OPA / Casbin policy.yaml)",
                "Skill and tool permission gates",
                "Row-level tenant data scoping",
                "Immutable HMAC-chained audit log (WORM)",
            ],
            "ai_principles_reference": "OECD AI Principles (OECD/LEGAL/0449)",
        }

    # ------------------------------------------------------------------
    # Step 2 — Identify and assess adverse impacts
    # ------------------------------------------------------------------

    def _step2_impact_assessment(self) -> dict:
        results = self._query("SELECT * FROM eval_results WHERE created_at >= ? LIMIT 2000",
                              (self._since,))
        if results is None or len(results) == 0:
            return {
                "description": "Adverse impact assessment via EvalRunner",
                "eval_runs_in_period": 0,
                "note": (
                    "No eval results found for this period. "
                    "Run EvalRunner with AdverseImpactScorer to populate. "
                    "See: agentix.eval.scorers.AdverseImpactScorer"
                ),
            }
        total = len(results)
        failed = sum(1 for r in results if not r.get("passed"))
        adverse = [r for r in results
                   if r.get("scorer") == "adverse_impact" and not r.get("passed")]
        return {
            "description": "Adverse impact assessment via EvalRunner",
            "eval_results_in_period": total,
            "failed_cases": failed,
            "adverse_impact_failures": len(adverse),
            "pass_rate": round((total - failed) / total, 3) if total else None,
            "adverse_impact_sample": adverse[:10],
        }

    # ------------------------------------------------------------------
    # Step 3 — Cease, prevent and mitigate adverse impacts
    # ------------------------------------------------------------------

    def _step3_prevent_mitigate(self) -> dict:
        return {
            "description": "Controls preventing and mitigating adverse AI impacts",
            "mitigations_deployed": {
                "pii_detection":   "PIIDetector (regex + optional Microsoft Presidio)",
                "pii_redaction":   "PIIRedactor applied on agent inputs and outputs",
                "gdpr_erasure":    "GDPREngine — Article 17 right-to-erasure",
                "consent":         "Per-purpose consent records with revocation support",
                "rbac":            "Role-based access control (OPA/Casbin)",
                "data_retention":  "Configurable auto-purge via RetentionEngine",
                "hitl":            "Human-in-the-loop checkpoints for high-risk tool calls",
            },
            "period_stats": {
                "pii_events_logged":       self._count_events("pii.%"),
                "gdpr_erasures_completed": self._count_events("gdpr.erasure_completed"),
                "access_denied_events":    self._count_events("security.access_denied"),
                "hitl_interrupts":         self._count_events("hitl.%"),
            },
        }

    # ------------------------------------------------------------------
    # Step 4 — Track and communicate (value-chain transparency)
    # ------------------------------------------------------------------

    def _step4_track_communicate(self) -> dict:
        invocations = self._query(
            "SELECT * FROM audit_chain WHERE event_type='ai.invocation' AND ts >= ?"
            " ORDER BY ts DESC LIMIT 1000",
            (self._since,),
        ) or []

        providers: dict[str, int] = {}
        models: dict[str, int] = {}
        connectors: dict[str, int] = {}
        tools: dict[str, int] = {}
        pii_input = pii_output = 0

        for row in invocations:
            try:
                d = json.loads(row.get("detail", "{}"))
            except (json.JSONDecodeError, TypeError):
                d = {}
            prov = d.get("model_provider", "unknown")
            mod  = d.get("model_id", "unknown")
            providers[prov] = providers.get(prov, 0) + 1
            models[mod]     = models.get(mod, 0) + 1
            for c in d.get("connectors_used", []):
                connectors[c] = connectors.get(c, 0) + 1
            for t in d.get("tools_used", []):
                tools[t] = tools.get(t, 0) + 1
            if d.get("pii_in_input"):
                pii_input += 1
            if d.get("pii_in_output"):
                pii_output += 1

        return {
            "description": "AI value-chain transparency — models, connectors and tools used",
            "ai_invocations_in_period": len(invocations),
            "model_providers": providers,
            "models_used": models,
            "connectors_used": connectors,
            "tools_used": tools,
            "runs_with_pii_in_input":  pii_input,
            "runs_with_pii_in_output": pii_output,
            "instrumentation_note": (
                "Populated by AuditLog.record_ai_invocation() called in agent_runtime/main.py. "
                "Zero counts mean instrumentation is not yet enabled."
            ),
        }

    # ------------------------------------------------------------------
    # Step 5 — Audit evidence (tamper-evident chain)
    # ------------------------------------------------------------------

    def _step5_audit_evidence(self) -> dict:
        try:
            from agentix.security.audit import AuditLog
            audit = AuditLog(db_path=self._db_path, hmac_secret=self._hmac_secret)
            ok, message = audit.verify_chain()
            total_rows = self._query("SELECT COUNT(*) AS c FROM audit_chain", ())
            total = (total_rows or [{}])[0].get("c", 0)
        except Exception as exc:
            return {"error": str(exc)}

        return {
            "description": "Tamper-evident HMAC-chained audit log",
            "chain_integrity": "VALID" if ok else "COMPROMISED",
            "verification_message": message,
            "total_audit_entries": total,
            "entries_in_period": self._count_events("%"),
            "hash_algorithm": "HMAC-SHA256" if self._hmac_secret else "SHA-256 (no HMAC secret set)",
            "worm_guarantee": "Append-only SQLite; any deletion or mutation breaks the hash chain.",
        }

    # ------------------------------------------------------------------
    # Step 6 — Remediation record
    # ------------------------------------------------------------------

    def _step6_remediation(self) -> dict:
        rows = self._query(
            "SELECT * FROM remediation_log WHERE discovered_at >= ? ORDER BY discovered_at DESC",
            (self._since,),
        )
        if rows is None:
            return {
                "description": "Remediation tracking — table not yet initialised",
                "note": "Run RemediationLog(db_path).init_db() to enable.",
                "open_items": 0,
            }
        open_items     = [r for r in rows if r.get("status") not in ("resolved", "closed", "wont_fix")]
        resolved_items = [r for r in rows if r.get("status") in ("resolved", "closed")]
        return {
            "description": "Remediation tracking for identified adverse AI impacts",
            "total_items_in_period": len(rows),
            "open_items":            len(open_items),
            "resolved_items":        len(resolved_items),
            "open_details":          open_items[:10],
        }

    # ------------------------------------------------------------------
    # ZIP evidence attachments
    # ------------------------------------------------------------------

    def _add_policy(self, zf: zipfile.ZipFile) -> None:
        p = Path(self._policy_file)
        if p.exists():
            zf.write(str(p), "policy/rbac_policy.yaml")
        else:
            zf.writestr("policy/rbac_policy.yaml", "# Policy file not found\n")

    def _add_ai_invocations(self, zf: zipfile.ZipFile) -> None:
        rows = self._query(
            "SELECT * FROM audit_chain WHERE event_type='ai.invocation' AND ts >= ?"
            " ORDER BY ts DESC",
            (self._since,),
        ) or []
        zf.writestr("evidence/ai_invocations.ndjson",
                    "\n".join(json.dumps(r, default=str) for r in rows))

    def _add_pii_events(self, zf: zipfile.ZipFile) -> None:
        rows = self._query(
            "SELECT * FROM audit_chain WHERE event_type LIKE 'pii%' AND ts >= ?",
            (self._since,),
        ) or []
        zf.writestr("evidence/pii_events.ndjson",
                    "\n".join(json.dumps(r, default=str) for r in rows))

    def _add_eval_results(self, zf: zipfile.ZipFile) -> None:
        rows = self._query(
            "SELECT * FROM eval_results WHERE created_at >= ? LIMIT 5000",
            (self._since,),
        ) or []
        if rows:
            zf.writestr("evidence/eval_results.ndjson",
                        "\n".join(json.dumps(r, default=str) for r in rows))

    def _add_remediation_log(self, zf: zipfile.ZipFile) -> None:
        rows = self._query("SELECT * FROM remediation_log ORDER BY discovered_at DESC", ())
        if rows is not None:
            zf.writestr("evidence/remediation_log.ndjson",
                        "\n".join(json.dumps(r, default=str) for r in rows))

    def _add_audit_chain_sample(self, zf: zipfile.ZipFile) -> None:
        rows = self._query(
            "SELECT * FROM audit_chain WHERE ts >= ? ORDER BY seq DESC LIMIT 2000",
            (self._since,),
        ) or []
        zf.writestr("evidence/audit_chain_sample.ndjson",
                    "\n".join(json.dumps(r, default=str) for r in rows))

    def _add_manifest(self, zf: zipfile.ZipFile, ts: str) -> None:
        manifest = {
            "framework": _FRAMEWORK_VERSION,
            "generated_at": ts,
            "platform": "Agentix",
            "reporting_period_days": self._period_days,
            "files": [
                "oecd_due_diligence_report.json",
                "policy/rbac_policy.yaml",
                "evidence/ai_invocations.ndjson",
                "evidence/pii_events.ndjson",
                "evidence/eval_results.ndjson",
                "evidence/remediation_log.ndjson",
                "evidence/audit_chain_sample.ndjson",
            ],
        }
        zf.writestr("MANIFEST.json", json.dumps(manifest, indent=2))

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _query(self, sql: str, params: tuple) -> list[dict] | None:
        """Execute a SELECT. Returns None if the table doesn't exist."""
        if not Path(self._db_path).exists():
            return []
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(sql, params).fetchall()
                return [dict(r) for r in rows]
            except sqlite3.OperationalError:
                return None   # table missing — caller decides how to handle
            finally:
                conn.close()
        except Exception:
            return []

    def _count_events(self, pattern: str) -> int:
        rows = self._query(
            "SELECT COUNT(*) AS c FROM audit_chain WHERE event_type LIKE ? AND ts >= ?",
            (pattern, self._since),
        )
        return (rows or [{}])[0].get("c", 0)
