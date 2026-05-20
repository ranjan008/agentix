# Agentix

**Serverless Agentic Platform — Zero-Idle, Event-Driven, Secure by Default**

Agentix is an open-source infrastructure layer for building and running production AI agents. Agents are defined as YAML files, triggered by any inbound channel (HTTP, Slack, WhatsApp, Telegram, Email, SQS, gRPC, Teams), and execute autonomously with LLM routing, tool use, graph orchestration, human-in-the-loop checkpoints, distributed tracing, and full enterprise compliance built in.

[![CI](https://github.com/ranjan008/agentix/actions/workflows/ci.yaml/badge.svg)](https://github.com/ranjan008/agentix/actions/workflows/ci.yaml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Agent Definition](#agent-definition)
- [Graph Engine](#graph-engine)
- [Multi-Agent Orchestration](#multi-agent-orchestration)
- [Human-in-the-Loop (HITL)](#human-in-the-loop-hitl)
- [Observability & Tracing](#observability--tracing)
- [Eval Framework](#eval-framework)
- [Channels](#channels)
- [LLM Providers](#llm-providers)
- [Skills & Tools](#skills--tools)
- [Security & RBAC](#security--rbac)
- [Compliance](#compliance)
- [Deployment](#deployment)
- [CLI Reference](#cli-reference)
- [Testing](#testing)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

Agentix solves the infrastructure problem for agentic AI: you define *what* your agent should do in YAML, and Agentix handles everything else — receiving triggers from any channel, routing to the right LLM, executing tools, managing memory, enforcing security policies, and observing costs.

```
Slack / WhatsApp / HTTP / Email / SQS / gRPC
           │
     ┌─────▼─────┐
     │  Watchdog  │  ← normalises triggers, enforces RBAC
     └─────┬─────┘
           │  TriggerEnvelope
     ┌─────▼──────────┐
     │  Agent Runtime  │  ← loads agent YAML, builds context
     │                 │
     │  ┌───────────┐  │        ┌──────────────────────┐
     │  │ Agentic   │  │───────▶│ Anthropic / OpenAI   │
     │  │   Loop    │  │        │ Gemini / Bedrock      │
     │  └─────┬─────┘  │        │ Ollama / vLLM         │
     │        │        │        └──────────────────────┘
     │  ┌─────▼─────┐  │
     │  │   Graph   │  │  ← graph mode: nodes, edges, state
     │  │  Engine   │  │
     │  └───────────┘  │
     └─────┬───────────┘
           │
     ┌─────▼──────┐
     │Tool Executor│ ← web_search, file_ops, email, custom tools
     └─────┬──────┘
           │
     ┌─────▼──────┐
     │   Storage   │ ← SQLite (dev) · PostgreSQL · Redis
     └────────────┘
```

---

## Features

### Core
- **Event-driven Watchdog** — listens on 8 inbound channels simultaneously; zero-idle (no polling in production)
- **Agent-as-YAML** — define agents declaratively with `apiVersion: agentix/v1`; register and deploy without code changes
- **Agentic Loop** — multi-turn LLM ↔ tool execution loop with configurable iteration and timeout limits
- **Unified TriggerEnvelope** — normalises all inbound events to a single schema before dispatch

### Graph Engine *(new)*
- **YAML-defined graphs** — declare nodes, edges, and state schema in the agent spec; no code required
- **Four node types** — `agent` (LLM call), `tool` (tool executor), `router` (conditional branching), `lambda` (state transform)
- **Four state reducers** — `replace`, `append`, `merge`, `add` — declared per field, applied automatically between steps
- **Conditional routing** — router nodes evaluate Python lambdas against state to select the next branch
- **Quality-gate loops** — loop back until a confidence threshold or iteration cap is met
- **Multi-model pipelines** — each agent node can use a different LLM model and system prompt
- **Per-node tracing** — every node emits a child span (`llm.call`, `tool.call`, `graph.router`, `graph.lambda`) under the parent run

### Multi-Agent Orchestration *(new)*
- **Fan-out** — spawn N parallel agent runs from a single trigger, collect results
- **Swarm** — dynamic multi-agent collaboration where agents hand off to each other via `transfer_to_agent` skill
- **DAG scheduler** — define agent pipelines as dependency graphs; steps run in parallel where possible
- **Pass-output chaining** — DAG steps can forward their response as input to downstream steps

### Human-in-the-Loop (HITL) *(new)*
- **`interrupt_before`** — pause before a tool call and require approval; shows call arguments in UI
- **`interrupt_after`** — pause after a tool call and require approval before continuing; shows tool result
- **Checkpoint store** — full conversation state persisted on interrupt; resumed on approve, discarded on reject
- **REST API** — `GET /hitl/pending`, `POST /hitl/{id}/approve`, `POST /hitl/{id}/reject`
- **Admin UI panel** — approve/reject with full context (tool name, input, output) from the chat interface

### LLM
- **Multi-provider routing** — Anthropic, OpenAI, Azure OpenAI, Google Gemini, AWS Bedrock, Ollama, vLLM, LM Studio
- **Tag-based routing** — route by agent tag (e.g. `fast` → Gemini, `cheap` → Haiku, `private` → Ollama)
- **Fallback chains** — automatically failover to the next provider on error
- **Cost ledger** — per-agent, per-tenant token cost tracking

### Observability & Tracing *(new)*
- **Distributed trace store** — every agent run recorded as a trace with nested spans (agent loop, LLM calls, tool calls, graph nodes)
- **Trace UI** — paginated trace browser with agent/status filters and full span-tree drilldown
- **Prometheus endpoint** — `/metrics/prometheus` for scraping by Grafana / alerting pipelines
- **Cost metrics** — `/metrics/cost` per-agent/tenant spend; `/metrics/agents` execution stats
- **OpenTelemetry tracing** — distributed traces exported to any OTLP-compatible collector

### Eval Framework *(new)*
- **`EvalRunner`** — run agent responses against a JSONL dataset and collect scores
- **Built-in scorers** — `exact_match`, `contains`, `regex_match`, `word_overlap`, `json_keys`, `LLMJudge`
- **`AdverseImpactScorer`** — OECD AI Principle 1.3 safety scorer; classifies outputs for BIAS, DISCRIMINATION, PRIVACY, MISINFORMATION, FINANCIAL_HARM, EMOTIONAL_HARM
- **Dataset management** — load, save, filter JSONL eval datasets

### Skills & Tools
- **Built-in skills** — `web-search`, `file-ops`, `email-composer`, `browser`
- **Skill marketplace** — community skills catalog with `agentix skill install`
- **`@tool` decorator** — register any Python function as an agent tool
- **`transfer_to_agent`** — built-in skill for swarm agent handoff

### Security
- **RBAC engine** — 5-level role hierarchy (end-user → operator → agent-author → tenant-admin → platform-admin)
- **JWT + API key auth** — Bearer tokens and `sk-agentix-` prefixed service account keys
- **Audit log** — tamper-evident HMAC-chained audit records for every action
- **Secrets backend** — env vars, HashiCorp Vault, AWS Secrets Manager (pluggable)
- **PII detection & redaction** — regex + optional Presidio integration
- **Skill RBAC** — per-skill permission gates enforced at activation time

### Compliance
- **GDPR engine** — right to erasure, data export, consent tracking, pseudonymisation
- **OECD Due Diligence** *(new)* — 6-step compliance report (Feb 2026 guidance) exportable as ZIP evidence bundle
- **`AdverseImpactScorer`** *(new)* — continuous OECD Principle 1.3 monitoring via eval pipeline
- **`RemediationLog`** *(new)* — OECD Step 6 harm tracker from discovery through resolution
- **SOC2 evidence bundle** — automated ZIP export of access logs, audit trails, and config snapshots
- **Retention engine** — configurable data retention policies with automatic purge

### Infrastructure
- **HA leader election** — Redis SETNX-based distributed lock; only one replica runs the scheduler
- **Durable event bus** — Redis Streams or Kafka backends (falls back to in-process for dev)
- **Scheduler** — cron and one-shot job scheduling via `schedules/*.yaml`
- **GitOps ready** — Helm chart, Terraform (EKS + RDS + Redis), ArgoCD ApplicationSet included

---

## Architecture

```
agentix/
├── watchdog/          # Inbound channel adapters + dispatcher
│   ├── channels/      # HTTP, Slack, WhatsApp, Telegram, Email, SQS, gRPC, Teams
│   ├── ha/            # Leader election, rate limiter, trigger queue
│   └── auth.py        # JWT validation
├── agent_runtime/     # Agent loader, context builder, agentic loop
│   └── graph_runner.py  # YAML → CompiledGraph compiler
├── graph/             # Graph engine (NEW)
│   ├── graph.py       # StateGraph, CompiledGraph, edge resolution
│   ├── nodes.py       # AgentNode, ToolNode, RouterNode, LambdaNode
│   └── state.py       # StateSchema, FieldSchema, reducers
├── llm/               # LLM router + provider adapters
├── skills/            # Skill engine, marketplace, SkillHub
├── storage/           # StateStore (SQLite/PostgreSQL), tenant management
├── memory/            # Vector store (sqlite-vec / pgvector / pure-Python)
├── security/          # RBAC, audit log, identity, secrets, skill RBAC
├── compliance/        # GDPR, PII, SOC2, retention, OECD, remediation (NEW)
├── orchestration/     # Fan-out, Swarm (NEW), DAG pipeline
├── hitl/              # Human-in-the-loop checkpoints and gate (NEW)
├── scheduler/         # Cron scheduler engine
├── observability/     # Trace store, cost ledger, OpenTelemetry (NEW)
├── eval/              # EvalRunner, scorers, dataset management (NEW)
├── api/               # FastAPI admin REST API
├── cli/               # `agentix` CLI
└── testing/           # AgentTestHarness, MockLLMProvider, AgentAssertions
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- An LLM API key (Anthropic, OpenAI, Gemini, or AWS credentials for Bedrock)

### Install

```bash
git clone https://github.com/ranjan008/agentix.git
cd agentix
pip install -e ".[dev]"
```

### Configure

```bash
cp .env.example .env
# Set at minimum:
# ANTHROPIC_API_KEY=sk-ant-...
# JWT_SECRET=change-me-in-production
```

### Start the watchdog

```bash
agentix dev start
```

This boots the watchdog on `http://localhost:8000` with the HTTP webhook channel enabled and registers any agents found in `agents/`.

### Send your first trigger

```bash
curl -X POST http://localhost:8000/trigger \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "research-assistant", "text": "What is the capital of France?"}'
```

### Start the Admin API + UI (optional)

```bash
uvicorn agentix.api.app:create_app --factory --reload --port 8000
# Swagger UI:  http://localhost:8000/docs
# Admin UI:    http://localhost:8000/ui
```

---

## Configuration

The main config file is `config/watchdog.yaml`. Environment variables override file values with `${VAR_NAME}` syntax.

```yaml
# config/watchdog.yaml
watchdog:
  port: 8000
  log_level: INFO

llm:
  default_provider: anthropic
  providers:
    anthropic:
      api_key: ${ANTHROPIC_API_KEY}
      model: claude-sonnet-4-6
    openai:
      api_key: ${OPENAI_API_KEY}
      model: gpt-4o
  routing:
    rules:
      - match_tag: fast
        provider: gemini
    fallback_chain: [anthropic, openai]

security:
  enforce_rbac: true
  jwt_secret: ${JWT_SECRET}

storage:
  db_path: data/agentix.db   # use postgresql://... for production
```

See [docs/setup-guide.md](docs/setup-guide.md) for the complete configuration reference.

---

## Agent Definition

Agents are plain YAML files following the `agentix/v1` spec:

```yaml
# agents/my-agent.yaml
apiVersion: "agentix/v1"
kind: "Agent"
metadata:
  name: "my-agent"
  version: "1.0.0"
spec:
  system_prompt: |
    You are a helpful assistant. Answer concisely.

  model:
    provider: anthropic
    model_id: claude-sonnet-4-6
    temperature: 0.3
    max_tokens: 4096

  skills:
    - web-search

  memory:
    short_term: sqlite
    scope: user
    max_history_turns: 10

  execution:
    timeout_sec: 120
    max_tool_calls: 20

  triggers:
    - channel: http_webhook
    - channel: slack
```

Register an agent:

```bash
agentix agent register agents/my-agent.yaml
```

---

## Graph Engine

For multi-stage pipelines, define your agent as a directed graph inside the same YAML spec. Each node is an explicit step; edges declare how state flows between them.

```yaml
spec:
  graph:
    max_steps: 40

    state_schema:
      messages:   { reducer: append,  default: [] }
      tool_calls: { reducer: replace, default: [] }
      final_answer: { reducer: replace, default: "" }
      token_count:  { reducer: add,     default: 0 }

    nodes:
      - id: researcher
        type: agent
        system_prompt: "Search and summarise the topic."
        tools: ["web_search"]
        output_key: notes

      - id: tool_executor
        type: tool

      - id: router
        type: router
        condition: "lambda state: 'tool_executor' if state.get('tool_calls') else 'synthesizer'"

      - id: synthesizer
        type: agent
        messages_key: synth_messages   # reads clean context, not tool history
        tools: []
        output_key: final_answer

    edges:
      - { from: researcher, to: router }
      - from: router
        mapping: { tool_executor: tool_executor, synthesizer: synthesizer }
      - { from: tool_executor, to: researcher }
      - { from: synthesizer, to: __end__ }

    entry: researcher
```

### Node types

| Type | What it does |
|------|-------------|
| `agent` | Calls the LLM; handles tool-use and end-turn stop reasons |
| `tool` | Executes `state["tool_calls"]` via ToolExecutor, clears the queue |
| `router` | Evaluates a condition lambda and returns a routing key; never modifies state |
| `lambda` | Runs any `lambda state: patch` — for transforms, counters, context prep |

### When to use graph vs regular agent vs DAG

| Need | Regular agent | Graph | DAG |
|------|:---:|:---:|:---:|
| Single LLM + optional tools | ✅ | — | — |
| Multiple LLM calls sharing context | — | ✅ | — |
| Conditional routing on LLM output | — | ✅ | — |
| Loop until quality threshold | — | ✅ | — |
| Separate system prompts per stage | — | ✅ | — |
| Parallel independent agents | — | — | ✅ |
| Scheduled / cron pipelines | — | — | ✅ |

See [docs/graph-engine.md](docs/graph-engine.md) for the full reference including all reducers, edge types, and common patterns.

---

## Multi-Agent Orchestration

### Fan-out

Spawn N agent runs in parallel from a single trigger and collect all results:

```python
from agentix.orchestration.fanout import FanOut, FanOutConfig

config = FanOutConfig(
    agents=["researcher-a", "researcher-b", "researcher-c"],
    merge_strategy="concat",
    total_timeout=120.0,
)
runner = FanOut(config=config, db_path="data/agentix.db", on_trigger=dispatcher)
result = await runner.run("Analyse Q3 performance", caller=caller)
```

### Swarm

Dynamic multi-agent collaboration — agents hand off to each other via the built-in `transfer_to_agent` skill:

```python
from agentix.orchestration.swarm import SwarmRunner, SwarmConfig

config = SwarmConfig(
    coordinator="triage-agent",
    specialists=["billing-agent", "technical-agent", "escalation-agent"],
    total_timeout=300.0,
    max_handoffs=5,
)
runner = SwarmRunner(config=config, db_path="data/agentix.db", on_trigger=dispatcher)
result = await runner.run("My invoice is wrong", caller=caller)
```

The coordinator decides whether to answer directly or call `transfer_to_agent(name="billing-agent")`. The swarm tracks handoff history and enforces the `max_handoffs` cap.

### DAG (dependency pipeline)

```yaml
# schedules/etl-pipeline.yaml
name: etl-pipeline
trigger:
  type: cron
  expression: "0 2 * * *"  # 02:00 daily
steps:
  - id: extract
    agent: extractor-agent
  - id: transform
    agent: transformer-agent
    depends_on: [extract]
    pass_output: true        # forwards extractor response as input
  - id: load
    agent: loader-agent
    depends_on: [transform]
```

---

## Human-in-the-Loop (HITL)

Add an `hitl:` block to any agent to pause execution and require human approval before sensitive tool calls proceed.

```yaml
spec:
  hitl:
    interrupt_before:
      - tool: send_email          # pause before sending — show draft to approver
      - tool: delete_record
    interrupt_after:
      - tool: web_search          # pause after search — approver reviews findings
        condition: "lambda result: 'confidential' in result.lower()"
```

Pending approvals appear in the Admin UI and via the REST API:

```bash
# List pending
curl http://localhost:8000/api/v1/hitl/pending

# Approve
curl -X POST http://localhost:8000/api/v1/hitl/{checkpoint_id}/approve

# Reject
curl -X POST http://localhost:8000/api/v1/hitl/{checkpoint_id}/reject \
  -d '{"reason": "Output contains PII"}'
```

On approval, the agent resumes from the exact checkpoint. On rejection, the run is stopped and the rejection reason is recorded in the audit log.

---

## Observability & Tracing

Every agent run is recorded as a **trace** with nested **spans**. In graph mode each node produces its own child span.

### Trace hierarchy

```
trace (agent run)
  └── graph.run
        ├── llm.call     (researcher agent node)
        ├── tool.call    (tool_executor node)
        ├── llm.call     (researcher — second pass)
        ├── graph.router (route_after_researcher)
        └── llm.call     (synthesizer node)
```

### REST API

```bash
# List traces with filters
GET /api/v1/traces?agent_id=my-agent&status=done&limit=30&offset=0

# Full trace with span tree
GET /api/v1/traces/{trace_id}

# Cost summary
GET /api/v1/metrics/cost?tenant_id=acme

# Prometheus scrape
GET /api/v1/metrics/prometheus
```

### Admin UI

The Traces page (`/ui`) shows a paginated trace list with Previous/Next controls and agent/status filters. Click any trace to see the full nested span tree with timing, token counts, and error details.

---

## Eval Framework

Run your agents against a dataset and score outputs before shipping to production.

```python
from agentix.eval.runner import EvalRunner
from agentix.eval.dataset import EvalDataset
from agentix.eval.scorers import AdverseImpactScorer

dataset = EvalDataset.from_jsonl("tests/data/eval_cases.jsonl")

runner = EvalRunner(
    agent_fn=my_agent_call,
    scorers=["exact_match", "contains"],
    threshold=0.8,
)
results = await runner.run(dataset)
print(f"Pass rate: {results.pass_rate:.1%}")
```

### Built-in scorers

| Scorer | Description |
|--------|-------------|
| `exact_match` | Exact string equality |
| `contains` | Output contains expected substring |
| `regex_match` | Output matches a regex pattern |
| `word_overlap` | F1-style token overlap |
| `json_keys` | All expected JSON keys present |
| `LLMJudge` | GPT/Claude rates quality on a rubric |
| `AdverseImpactScorer` | OECD Principle 1.3 safety scorer |

### Adverse impact monitoring

```python
from agentix.eval.scorers import AdverseImpactScorer

scorer = AdverseImpactScorer(llm=llm_router)
score = await scorer(actual=agent_output)
# score.value: 1.0=none, 0.75=low, 0.5=medium, 0.0=high risk
# score.explanation: "[BIAS] severity=low: ..."
```

---

## Channels

| Channel | Enable by setting |
|---------|-------------------|
| HTTP Webhook | Always enabled (listens on watchdog port) |
| Slack | `SLACK_BOT_TOKEN` + `SLACK_SIGNING_SECRET` |
| WhatsApp | `WHATSAPP_ACCESS_TOKEN` + `WHATSAPP_PHONE_ID` |
| Telegram | `TELEGRAM_BOT_TOKEN` |
| Microsoft Teams | `TEAMS_APP_ID` + `TEAMS_APP_PASSWORD` |
| Email (IMAP) | `EMAIL_IMAP_HOST` + credentials |
| AWS SQS | `SQS_QUEUE_URL` (+ AWS credentials) |
| gRPC | `GRPC_LISTEN_PORT` |

---

## LLM Providers

| Provider | Config key | Notes |
|----------|------------|-------|
| Anthropic | `anthropic` | Claude 3.x / Claude 4.x family |
| OpenAI | `openai` | GPT-4o, GPT-4o-mini, o1 |
| Azure OpenAI | `azure_openai` | Requires endpoint + deployment name |
| Google Gemini | `gemini` | Gemini 2.0 Flash recommended |
| AWS Bedrock | `bedrock` | Uses boto3 credential chain |
| Ollama | `ollama` | Local models (Llama, Mistral, Qwen, Phi) |
| LM Studio | `lmstudio` | Local GUI server |
| vLLM | `vllm` | Production GPU server |

**Mixed routing** — local for privacy-sensitive tags, cloud for everything else:

```yaml
llm:
  routing:
    rules:
      - match_tag: private
        provider: ollama    # keep data on-prem
      - match_tag: fast
        provider: anthropic
    fallback_chain: [anthropic, ollama]
```

---

## Skills & Tools

### Built-in skills

| Skill | Tools provided |
|-------|----------------|
| `web-search` | `web_search`, `web_fetch` |
| `file-ops` | `file_read`, `file_write`, `file_list` |
| `email-composer` | `send_email`, `draft_email` |
| `browser` | `browser_navigate`, `browser_get_text`, `browser_click`, `browser_screenshot` + LinkedIn tools |

### Custom tools

```python
from agentix.agent_runtime.tool_executor import tool

@tool(
    name="get_weather",
    description="Get current weather for a city",
    input_schema={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
)
def get_weather(city: str) -> dict:
    return {"city": city, "temp_c": 22, "condition": "sunny"}
```

---

## Security & RBAC

### Role hierarchy

```
platform-admin  (full access)
  └── tenant-admin  (manage one tenant)
        └── agent-author  (register/update agents)
              └── operator  (trigger agents, read audit)
                    └── end-user  (invoke triggers only)
```

### Audit log

Every agent invocation, HITL decision, skill activation, and admin action is written to a tamper-evident HMAC-chained audit log:

```bash
agentix audit list --limit 50
agentix audit list --tenant my-tenant --action trigger:invoke
```

---

## Compliance

### GDPR

```python
from agentix.compliance.gdpr import GDPREngine

engine = GDPREngine("data/agentix.db")
engine.record_consent(identity_id, "marketing_emails", granted=True)
engine.right_to_erasure(identity_id)   # deletes all PII for user
data = engine.data_export(identity_id)  # GDPR portability export
```

### PII detection

```python
from agentix.compliance.pii import PIIDetector, PIIRedactor

detector = PIIDetector()
findings = detector.scan("My email is alice@example.com")

redactor = PIIRedactor()
clean = redactor.redact("Call me at +1-555-123-4567")
# → "Call me at [PHONE]"
```

### OECD Due Diligence (AI Act / Feb 2026 Guidance)

Agentix generates a full 6-step OECD Due Diligence evidence bundle for enterprise AI compliance audits. The report maps each OECD step to existing agentix evidence collected automatically during agent runs.

| OECD Step | Evidence source in Agentix |
|-----------|---------------------------|
| Step 1 — Responsible AI Policy | RBAC policy file + security layer inventory |
| Step 2 — Identify Adverse Impacts | EvalRunner results via `AdverseImpactScorer` |
| Step 3 — Prevent & Mitigate | PII redaction, GDPR erasure, HITL checkpoints, RBAC |
| Step 4 — Track & Communicate | `ai.invocation` audit entries (model, tools, connectors, PII flags) |
| Step 5 — Audit Evidence | HMAC-chained audit log with integrity verification |
| Step 6 — Remediation | `RemediationLog` open/resolved items linked to audit entries |

```python
from agentix.compliance.oecd import OECDDueDiligenceReport

report = OECDDueDiligenceReport(
    db_path="data/agentix.db",
    cfg={"version": "1.0.0"},
    hmac_secret=os.environ["AUDIT_HMAC_SECRET"],
    period_days=90,
)
zip_path = report.export("compliance/oecd-2026-Q2")
# → compliance/oecd-2026-Q2/oecd-due-diligence-20260520_143022.zip
```

The ZIP bundle contains:
- `oecd_due_diligence_report.json` — structured 6-step report
- `policy/rbac_policy.yaml` — current RBAC policy
- `evidence/ai_invocations.ndjson` — all model invocations in the period
- `evidence/pii_events.ndjson` — PII detection/redaction events
- `evidence/eval_results.ndjson` — adverse impact eval results
- `evidence/remediation_log.ndjson` — all remediation items
- `evidence/audit_chain_sample.ndjson` — tamper-evident audit records
- `MANIFEST.json`

### Remediation tracking

```python
from agentix.compliance.remediation import RemediationLog

rlog = RemediationLog(db_path="data/agentix.db", audit_log=audit)

# Open a new item when an adverse impact is detected
item_id = rlog.open_item(
    harm_type="BIAS",
    severity="medium",
    description="Agent suggested lower salary range for female candidates",
    owner="safety@example.com",
    audit_seq_ref=1234,   # links to the offending ai.invocation audit entry
)

# Resolve after fix + re-eval
rlog.resolve(item_id, resolution_note="Updated system prompt; passed AdverseImpactScorer at 0.95")

# Summary for dashboard
print(rlog.summary())
# {"by_status": {"open": 1, "resolved": 3}, "by_severity": {"medium": 2, "low": 2}}
```

### SOC2 evidence

```bash
python -c "
from agentix.compliance.soc2 import SOC2Exporter
e = SOC2Exporter('data/agentix.db', {})
print('Bundle:', e.export('compliance/'))
"
```

See [docs/privacy.md](docs/privacy.md) for the full privacy and data handling guide.

---

## Deployment

### Docker

```bash
docker build -t agentix:latest .
docker run -p 8000:8000 --env-file .env agentix:latest
```

### Kubernetes (Helm)

```bash
helm install agentix deploy/helm/agentix \
  --set image.tag=latest \
  --set config.anthropicApiKey=$ANTHROPIC_API_KEY
```

### Terraform (AWS EKS)

```bash
cd deploy/terraform
terraform init
terraform apply \
  -var="anthropic_api_key=$ANTHROPIC_API_KEY" \
  -var="jwt_secret=$JWT_SECRET"
```

### GitOps (ArgoCD)

```bash
kubectl apply -f deploy/argocd/application.yaml
# ArgoCD auto-syncs on every push to main
```

---

## CLI Reference

```
agentix dev start                        Start watchdog + API in dev mode

agentix agent list                       List registered agents
agentix agent register <path.yaml>       Register an agent from YAML
agentix agent run <agent-id> [--text]    Manually trigger an agent

agentix skill list                       List bundled + installed skills
agentix skill install <name>             Install a skill from marketplace

agentix trigger list [--agent] [--limit] List recent trigger history
agentix token generate [--roles] [--ttl] Generate a dev JWT token

agentix audit list [--tenant] [--limit]  Read the audit log
agentix tenant list                      List tenants (platform-admin only)
agentix tenant create <id> <name>        Create a tenant
```

---

## Testing

Agentix ships a first-class test harness for agent logic — no subprocess, no real LLM calls, no disk I/O.

```python
import pytest
from agentix.testing import AgentTestHarness, AgentAssertions, LLMScript
from agentix.testing.mock_llm import LLMTurn, ToolCall

@pytest.mark.asyncio
async def test_web_search_agent():
    harness = AgentTestHarness.from_dict({
        "metadata": {"name": "test-agent"},
        "spec": {
            "system_prompt": "You are a research assistant.",
            "tools": ["web_search"],
        },
    })

    script = LLMScript([
        LLMTurn(
            tool_calls=[ToolCall(id="t1", name="web_search", input={"query": "climate"})],
            stop_reason="tool_use",
        ),
        LLMTurn(content="Climate change is accelerating.", stop_reason="end_turn"),
    ])

    result = await harness.run(trigger_text="Tell me about climate", llm_script=script)

    AgentAssertions(result).tool_called("web_search").final_text_contains("Climate")
```

Run the test suite:

```bash
pytest tests/ -v
```

Run lint and type checks:

```bash
ruff check agentix/ tests/              # lint
mypy agentix/ --ignore-missing-imports  # type check
```

---

## Contributing

1. **Fork** the repository and create a feature branch: `git checkout -b feat/my-feature`
2. **Install dev dependencies**: `pip install -e ".[dev]"`
3. **Make your changes** — keep commits focused and descriptive
4. **Run checks** before pushing:
   ```bash
   ruff check agentix/ tests/
   mypy agentix/ --ignore-missing-imports
   pytest tests/ -v
   ```
5. **Open a pull request** against `main` with a clear description of the change

### Code style

- Formatter: `black` (line length 100)
- Linter: `ruff`
- Type checker: `mypy` (strict on new files)
- All public APIs should have docstrings
- New features require a test in `tests/`

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## Acknowledgements

Built with:
- [FastAPI](https://fastapi.tiangolo.com/) — Admin REST API
- [aiohttp](https://docs.aiohttp.org/) — Async HTTP watchdog
- [Anthropic SDK](https://github.com/anthropics/anthropic-sdk-python) — LLM client
- [slack-bolt](https://slack.dev/bolt-python/) — Slack channel adapter
- [Click](https://click.palletsprojects.com/) — CLI framework
- [PyYAML](https://pyyaml.org/) — Agent spec parsing
