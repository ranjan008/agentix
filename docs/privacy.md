# Agentix Platform — Privacy & Data Handling

> **Scope:** This document explains how the Agentix platform collects, processes,
> stores, protects, and erases personal data. It is intended for operators deploying
> Agentix, data protection officers (DPOs), and developers building agents on the
> platform.

---

## Table of Contents

1. [What Data Agentix Handles](#1-what-data-agentix-handles)
2. [Data Flow Diagram](#2-data-flow-diagram)
3. [PII Detection & Redaction](#3-pii-detection--redaction)
4. [How Data is Stored](#4-how-data-is-stored)
5. [Multi-Tenant Isolation](#5-multi-tenant-isolation)
6. [Access Control](#6-access-control)
7. [Secrets & Credential Management](#7-secrets--credential-management)
8. [Audit Logging](#8-audit-logging)
9. [Data Sent to External AI Providers](#9-data-sent-to-external-ai-providers)
10. [GDPR Compliance](#10-gdpr-compliance)
11. [Data Retention](#11-data-retention)
12. [SOC 2 Evidence](#12-soc-2-evidence)
13. [What Is NOT Protected by Default](#13-what-is-not-protected-by-default)
14. [Privacy Configuration Reference](#14-privacy-configuration-reference)

---

## 1. What Data Agentix Handles

Every message that enters Agentix through any channel becomes a **TriggerEnvelope**.
The following personal data categories may be present depending on the channel and
agent use case.

| Data Category | Examples | Where it appears |
|---|---|---|
| **Direct identifiers** | Name, phone number, email address, WhatsApp ID | Channel payloads, identity fields |
| **Message content** | Free-text messages, voice transcripts | `payload.text`, conversation history |
| **Device / network** | IP address, User-Agent | HTTP channel metadata |
| **Account identifiers** | User ID from Slack, Telegram chat ID, Teams AAD object ID | `identity.user_id` |
| **Credentials** | API keys, JWT tokens, HMAC secrets | Secrets vault only — never in message fields |
| **Behavioural** | Which agents a user triggers, tool call history | Trigger records, agent state, audit log |
| **Financial** | Credit card numbers (if user pastes them) | Detectable by PII scanner |

---

## 2. Data Flow Diagram

```
User (WhatsApp / Slack / HTTP / Email / ...)
    │
    │  raw channel payload
    ▼
┌─────────────────────────────────────────────────┐
│  Channel Adapter  (telegram.py / whatsapp.py …) │
│  • strips channel-specific envelope              │
│  • extracts identity fields                      │
│  • produces TriggerEnvelope (normalised dict)    │
└───────────────┬─────────────────────────────────┘
                │  TriggerEnvelope
                ▼
┌─────────────────────────────────────────────────┐
│  RBAC Gateway                                    │
│  • checks caller identity & role                 │
│  • rejects unauthorised triggers immediately     │
└───────────────┬─────────────────────────────────┘
                │  authorised envelope
                ▼
┌─────────────────────────────────────────────────┐
│  State Store  (SQLite / PostgreSQL)              │
│  • persists trigger record                       │
│  • writes audit log entry                        │
└───────────────┬─────────────────────────────────┘
                │  envelope written to disk
                ▼
┌─────────────────────────────────────────────────┐
│  Agent Runtime  (subprocess)                     │
│  • loads conversation history from state store   │
│  • ── calls External LLM API ──────────────────▶ │  ← personal data
│  •   (Anthropic / OpenAI / Gemini / Bedrock)     │    leaves system here
│  • executes tool calls                           │
│  • writes final response                         │
└───────────────┬─────────────────────────────────┘
                │  response text
                ▼
┌─────────────────────────────────────────────────┐
│  Output Handler                                  │
│  • sends reply back through originating channel  │
│  • persists conversation turn (with TTL)         │
│  • records cost ledger entry                     │
└─────────────────────────────────────────────────┘
```

**Key privacy boundary:** Personal data in the message content and identity fields is
sent to the configured LLM provider's API. Everything else — audit records, trigger
metadata, agent state — stays within your own infrastructure.

---

## 3. PII Detection & Redaction

### What is detected

The platform includes a built-in PII scanner (`agentix/compliance/pii.py`) that
identifies the following data types using regular expressions, with optional upgrade
to Microsoft Presidio for higher-accuracy NER-based detection:

| PII Type | Example | Detection Method |
|---|---|---|
| Email address | `alice@example.com` | Regex |
| Phone number | `+1 (555) 234-5678` | Regex (E.164 + US formats) |
| SSN (US) | `123-45-6789` | Regex with validity checks |
| Credit card number | `4111 1111 1111 1111` | Regex |
| IPv4 / IPv6 address | `192.168.1.1` | Regex |
| AWS access key | `AKIAIOSFODNN7EXAMPLE` | Regex |
| AWS secret key | pattern match | Regex |
| JWT token | `eyJ...` | Regex |
| Private key material | `-----BEGIN RSA PRIVATE KEY-----` | Regex |
| Person name, address, passport | (NER-based) | Presidio (if installed) |

### How redaction works

When redaction is enabled, PII values are replaced in-place with type placeholders
before the text is passed to the LLM:

```
Input:  "My email is alice@example.com and SSN is 123-45-6789"
Output: "My email is [EMAIL] and SSN is [SSN]"
```

The original value is **never stored or forwarded** — only the placeholder is retained.

### Enabling PII redaction

Install the optional Presidio library for higher accuracy:

```bash
pip install presidio-analyzer presidio-anonymizer
python -m spacy download en_core_web_lg
```

Add to your agent spec:

```yaml
# agents/my-agent.yaml
spec:
  privacy:
    pii_scan: true           # scan all incoming message text
    pii_redact: true         # replace detected PII before sending to LLM
    pii_redact_fields:       # optionally limit to specific payload fields
      - text
      - subject
```

Or apply globally to all agents in `config/watchdog.yaml`:

```yaml
privacy:
  pii_scan: true
  pii_redact: true
```

### Using the PII API directly

```python
from agentix.compliance.pii import PIIDetector, PIIRedactor

detector = PIIDetector(use_presidio=True)   # falls back to regex if Presidio absent
redactor = PIIRedactor(detector)

# Scan
findings = detector.scan("Call me at 555-867-5309")
# [PIIFinding(pii_type='PHONE', value='555-867-5309', start=10, end=22)]

# Redact
clean = redactor.redact("My card: 4111-1111-1111-1111")
# "My card: [CREDIT_CARD]"

# Redact a dict (e.g. webhook payload)
safe_payload = redactor.redact_dict(payload, fields=["text", "subject"])
```

---

## 4. How Data is Stored

### Storage locations

| Data | Table | Tier | Contains PII? |
|---|---|---|---|
| Trigger envelopes | `triggers` | All | Yes — caller identity, message text |
| Agent conversation memory | `agent_state` | All | Yes — conversation history |
| Audit log | `audit_chain` | All | Partial — identity IDs, action types |
| Cost ledger | `cost_ledger` | All | No — token counts only |
| Skill / agent registry | `agents` | All | No |
| Pipeline run history | `pipeline_runs` | All | No |
| Consent records | `consent` | All | Yes — identity ID, purpose |
| Erasure log | `erasure_log` | All | Pseudonymised after erasure |
| Service account keys | `service_accounts` | All | Hashed only (SHA-256) |

### Encryption at rest

Agentix **does not encrypt the database itself** — this must be handled at the
infrastructure layer:

- **SQLite (Lite tier):** Use filesystem-level encryption (LUKS, FileVault, BitLocker)
  or SQLCipher.
- **PostgreSQL (Standard/Enterprise tier):** Enable `pg_tde` or use an encrypted EBS
  volume / cloud disk.
- **Redis:** Enable TLS (`requiretls yes`) and use an encrypted volume.

### Encryption in transit

- All channel adapters use HTTPS/TLS for outbound calls (WhatsApp, Telegram APIs).
- For inbound webhooks, place Agentix behind a TLS-terminating reverse proxy (nginx,
  Caddy, AWS ALB).
- The gRPC channel supports mutual TLS (`GRPC_USE_TLS=true`).

---

## 5. Multi-Tenant Isolation

Each tenant's data is logically isolated at the database layer
(`agentix/storage/tenant.py`).

- Every row in `triggers`, `agent_state`, and `audit_chain` carries a `tenant_id`
  column.
- The `TenantStateStore` enforces tenant scoping on every read and write — cross-tenant
  queries are structurally impossible through the API.
- Tenant rows are never shared, even when multiple tenants use the same physical
  database.
- On deletion (`soft_delete_tenant`), the tenant record is flagged inactive and its
  data is subject to the configured retention policy.

**Physical isolation** (enterprise deployments): For the strongest isolation, give
each tenant a dedicated database. Set `db_path` per tenant in the watchdog config or
use separate PostgreSQL schemas.

---

## 6. Access Control

### Who can access what

Agentix enforces a five-level role hierarchy. Roles are verified on every API call,
trigger, skill activation, and tool invocation.

| Role | What they can access |
|---|---|
| `end-user` | Send triggers to agents they are permitted to reach |
| `operator` | All above + view trigger history for their tenant |
| `agent-author` | All above + register and update agent specs |
| `tenant-admin` | All above + manage users, service accounts, retention for their tenant |
| `platform-admin` | Full access across all tenants |

### How identity is established

Every inbound request must carry one of:

| Method | Header | Verified by |
|---|---|---|
| JWT bearer token | `Authorization: Bearer <jwt>` | HMAC-SHA256 signature, expiry check |
| Service account API key | `Authorization: Bearer sk-agentix-...` | SHA-256 hash lookup |
| OIDC token | `Authorization: Bearer <id_token>` | JWKS key rotation, expiry |
| SAML 2.0 assertion | POST body | XML signature verification |

Unauthenticated requests are rejected at the RBAC Gateway before any agent logic runs.

### Principle of least privilege for agents

Each agent spec declares the exact set of tools and skills it is permitted to use.
The `ToolExecutor` enforces the `allowed_tools` allowlist — an agent cannot call a
tool not listed in its spec, regardless of what the LLM requests.

```yaml
# agents/my-agent.yaml
spec:
  tools:
    - web_search      # only this tool is callable
  skills:
    - web_search      # only this skill is loadable
```

---

## 7. Secrets & Credential Management

All credentials are managed through the **Secrets Vault**
(`agentix/security/secrets.py`). The vault abstracts four backends:

| Backend | Use case | How secrets are stored |
|---|---|---|
| `env` | Development / simple deployments | OS environment variables |
| `file` | Single-server deployments | Base64-encoded JSON file on disk |
| `hashicorp_vault` | Enterprise on-premises | HashiCorp Vault KV v2 |
| `aws_secrets_manager` | AWS deployments | AWS Secrets Manager |

### Rules

- **API keys are never stored in agent YAML files or watchdog.yaml** — always use
  `${ENV_VAR}` substitution or the vault backend.
- **Service account API keys** are stored as SHA-256 hashes only. The plaintext key
  is returned once on creation and never stored. There is no "show key again" feature.
- **WhatsApp / Telegram tokens** are read from environment variables at startup and
  held in process memory only — never written to the database.

```yaml
# config/watchdog.yaml — correct pattern
llm:
  providers:
    anthropic:
      api_key: ${ANTHROPIC_API_KEY}   # resolved from env at startup
```

---

## 8. Audit Logging

Every privacy-sensitive action is recorded in a tamper-detectable audit log
(`agentix/security/audit.py`).

### What is logged

| Event | Logged fields |
|---|---|
| `trigger.received` | trigger ID, agent ID, identity ID, tenant ID, timestamp |
| `trigger.rejected` | reason (unknown agent / RBAC denied) |
| `tool.called` | tool name, trigger ID, agent ID |
| `tool.error` | tool name, error message |
| `agent.started` / `agent.completed` | trigger ID, response length |
| `gdpr.erasure_completed` | pseudonymised identity, tables affected |
| `gdpr.data_export` | identity ID, tenant ID |
| Skill activation / denial | skill name, role, outcome |
| Authentication failure | identity source, reason |

### Tamper detection

Each log entry includes:

- `prev_hash` — SHA-256 of the previous entry
- `entry_hash` — HMAC-SHA256 of this entry's canonical fields

Any modification, deletion, or reordering of entries breaks the chain. Verify
integrity at any time:

```bash
python -m agentix.cli.main audit verify
```

```
Chain valid: True
Entries verified: 1,482
```

### Audit log does NOT contain

- Full message text or conversation content
- Raw credentials or tokens
- Personal data beyond the identity ID used as a key

---

## 9. Data Sent to External AI Providers

This is the most significant privacy boundary in the platform.

### What is sent

When an agent runs, the following is transmitted to the configured LLM provider's
API over HTTPS:

| Sent | Example |
|---|---|
| System prompt (from agent spec) | "You are a customer support assistant…" |
| Conversation history | All messages in the current session window |
| User message text | The raw (or redacted) content of the trigger |
| Tool definitions | Names and schemas of permitted tools |
| Tool call results | Output of tools the agent has used |

### What is NOT sent

| Not sent |
|---|
| Identity IDs or roles |
| Tenant IDs |
| Raw channel metadata (IP, device info) |
| Audit log contents |
| Database records |
| Credentials or secrets |

### Provider-specific notes

| Provider | Data residency | Zero data retention option |
|---|---|---|
| Anthropic | US | Available on Enterprise plans |
| OpenAI | US / EU | Available via Azure OpenAI |
| Azure OpenAI | Your chosen Azure region | Yes — data not used for training by default |
| Google Gemini | Google Cloud region | Configurable |
| AWS Bedrock | Your chosen AWS region | Yes — no data retention by default |

### Minimising data exposure to the LLM

1. **Enable PII redaction** (Section 3) before messages reach the LLM call.
2. **Limit conversation history window** in the agent spec:
   ```yaml
   spec:
     memory:
       max_history_turns: 5    # only last 5 turns sent to LLM
       ttl_sec: 1800
   ```
3. **Use AWS Bedrock or Azure OpenAI** for data residency requirements.
4. **Use a self-hosted model** (e.g. Ollama with a local LLM) — no data leaves your
   infrastructure. Add a custom provider to `LLMRouter`.

---

## 10. GDPR Compliance

The `GDPREngine` (`agentix/compliance/gdpr.py`) implements Articles 17, 20, and 7
of the GDPR.

### Right to Erasure (Article 17)

Permanently removes or pseudonymises all personal data linked to an identity:

```python
from agentix.compliance.gdpr import GDPREngine

engine = GDPREngine(db_path="data/agentix.db")
result = engine.right_to_erasure(
    identity_id="user_12345",
    tenant_id="acme-corp",
)
# {
#   "identity_id": "anon_7f3a9c1b...",   ← pseudonymised
#   "tables": {"audit_log": 48, "agent_state": 3, "consent": 1},
#   "status": "completed"
# }
```

**What happens per table:**

| Table | Action |
|---|---|
| `audit_log` | `identity_id` replaced with `anon_<sha256[:16]>` |
| `triggers` | `caller.identity_id` in JSON replaced with pseudonym |
| `agent_state` | Rows with `scope = "user:<identity_id>"` deleted |
| `consent` | All consent records deleted |

Every erasure request is recorded in the `erasure_log` table with a timestamp and
summary of affected rows.

### Right to Data Portability (Article 20)

Export all data held for a user as a structured JSON document:

```python
export = engine.data_export(
    identity_id="user_12345",
    tenant_id="acme-corp",
)
# Returns dict with keys: identity_id, exported_at, data: {audit_log, agent_state, consent}
```

Via the Admin API:

```bash
# Download via REST (requires tenant-admin role)
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8090/api/v1/gdpr/export?identity_id=user_12345" \
  -o user-data-export.json
```

### Consent Management (Article 7)

Track and enforce user consent per processing purpose:

```python
# Record consent
engine.record_consent(
    identity_id="user_12345",
    tenant_id="acme-corp",
    purpose="customer_support_ai",
    granted=True,
)

# Check before processing
if not engine.has_consent("user_12345", "customer_support_ai"):
    raise PermissionError("User has not consented to AI processing")

# Revoke
engine.revoke_consent("user_12345", "customer_support_ai")
```

### Pseudonymisation

Identity IDs are pseudonymised using a one-way SHA-256 hash with a fixed prefix.
The pseudonym is deterministic (the same input always produces the same output)
but irreversible — the original ID cannot be recovered from the pseudonym.

```
user_12345  →  anon_7f3a9c1b4e820d11
```

---

## 11. Data Retention

Automatic purging of data older than a configured threshold is handled by
`RetentionEngine` (`agentix/compliance/retention.py`).

### Default retention periods (recommended)

| Data | Recommended retention | Regulatory minimum |
|---|---|---|
| Trigger records | 90 days | N/A |
| Agent conversation memory | 30 days | N/A |
| Audit log | 365 days | 1 year (SOC 2), 7 years (some regulations) |
| Cost ledger | 90 days | N/A |
| Consent records | Duration of relationship + 1 year | |

### Configuration

```yaml
# config/watchdog.yaml
retention:
  default_ttl_days: 90
  policies:
    - table: audit_log
      ttl_days: 365
    - table: agent_state
      ttl_days: 30
    - table: triggers
      ttl_days: 90
      tenant_overrides:
        enterprise-tenant: 365    # extended retention for specific tenants
    - table: cost_ledger
      ttl_days: 90
```

### Running the purge job

Schedule as a daily cron task:

```bash
# crontab entry — run at 02:00 every night
0 2 * * * cd /opt/agentix && \
  python -c "
from agentix.compliance.retention import RetentionEngine
from agentix.watchdog.config import load_config
cfg = load_config('config/watchdog.yaml')
engine = RetentionEngine.from_config(cfg, 'data/agentix.db')
summary = engine.run_once()
print('Retention run:', summary)
" >> logs/retention.log 2>&1
```

---

## 12. SOC 2 Evidence

The `SOC2Exporter` (`agentix/compliance/soc2.py`) generates a ZIP bundle containing
all evidence required for a SOC 2 Type II audit:

| File in bundle | Contents |
|---|---|
| `audit_log.ndjson` | Complete audit log in newline-delimited JSON |
| `chain_verification.json` | HMAC chain integrity report (pass/fail + tampered IDs) |
| `rbac_policy.yaml` | Access control policy snapshot |
| `config_snapshot.json` | Platform configuration with secrets redacted |
| `MANIFEST.json` | Bundle metadata (timestamp, platform version) |

Generate on demand:

```bash
python -c "
from agentix.compliance.soc2 import SOC2Exporter
from agentix.watchdog.config import load_config
import os
cfg = load_config('config/watchdog.yaml')
exporter = SOC2Exporter(
    db_path='data/agentix.db',
    cfg=cfg,
    hmac_secret=os.environ.get('AUDIT_HMAC_SECRET', ''),
)
path = exporter.export('compliance/soc2-2026-Q1')
print('Bundle written to:', path)
"
```

The GitHub Actions CI pipeline is configured to generate this bundle automatically
on a weekly schedule and upload it as a GitHub Actions artifact with 365-day retention.

---

## 13. What Is NOT Protected by Default

The following gaps exist in the current implementation and should be addressed
before processing sensitive personal data in production:

| Gap | Risk | Mitigation |
|---|---|---|
| **No database encryption at rest** | Database file readable if server is compromised | Use LUKS / BitLocker / encrypted cloud disk |
| **No LLM output scanning** | LLM may echo PII from its training data or context | Add output-side PII scan before storing/sending response |
| **No network egress filtering** | Agent tools can make arbitrary outbound HTTP calls | Use firewall rules or a proxy allowlist |
| **Agent subprocess runs as same OS user** | Agent process has full filesystem access | Use `bwrap`/`firejail`, or K8s pod security context |
| **Conversation history not encrypted** | Stored in plaintext in `agent_state` table | Encrypt at rest (infrastructure layer) |
| **Redis data not encrypted** | Event bus and rate limiter data in Redis plaintext | Enable Redis TLS and AUTH password |
| **No consent gate on inbound channels** | WhatsApp/Telegram messages processed without explicit consent check | Add `GDPREngine.has_consent()` check in channel dispatcher |

---

## 14. Privacy Configuration Reference

Complete reference of all privacy-related settings in `config/watchdog.yaml`:

```yaml
# ── PII ───────────────────────────────────────────────────────────────────────
privacy:
  pii_scan: true                  # scan all inbound message text for PII
  pii_redact: true                # replace detected PII with [TYPE] placeholders
  pii_use_presidio: true          # use Presidio NER (falls back to regex)

# ── Data retention ────────────────────────────────────────────────────────────
retention:
  default_ttl_days: 90
  policies:
    - table: audit_log
      ttl_days: 365
    - table: agent_state
      ttl_days: 30
    - table: triggers
      ttl_days: 90

# ── Security / access control ─────────────────────────────────────────────────
security:
  enforce_rbac: true
  jwt_secret_env: JWT_SECRET
  policy_file: config/policy.yaml

# ── Audit log ─────────────────────────────────────────────────────────────────
# AUDIT_HMAC_SECRET env var — set to a strong random string

# ── Secrets backend ───────────────────────────────────────────────────────────
secrets:
  backend: env                    # env | file | hashicorp_vault | aws_secrets_manager
  # hashicorp_vault:
  #   url: https://vault.example.com
  #   token: ${VAULT_TOKEN}
  #   mount: secret
  # aws_secrets_manager:
  #   region: us-east-1

# ── LLM data minimisation ─────────────────────────────────────────────────────
llm:
  default_provider: anthropic
  providers:
    anthropic:
      model: claude-sonnet-4-6

# Per-agent memory window (limits data sent to LLM)
# Set in agents/<name>.yaml:
# spec:
#   memory:
#     max_history_turns: 5
#     ttl_sec: 1800
```

---

*Last updated: 2026-04-03 — reflects Agentix Platform v4.0.0 (branch: claude/phase-4-enterprise)*
