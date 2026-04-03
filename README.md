# Agentix

**Serverless Agentic Platform — Zero-Idle, Event-Driven, Secure by Default**

Agentix is an open-source infrastructure layer for building and running production AI agents. Agents are defined as YAML files, triggered by any inbound channel (HTTP, Slack, WhatsApp, Telegram, Email, SQS, gRPC, Teams), and execute autonomously with LLM routing, tool use, vector memory, and full enterprise security built in.

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
     ┌─────▼──────┐
     │ Agent Runtime│ ← loads agent YAML, builds context
     └─────┬──────┘
           │
     ┌─────▼──────┐        ┌─────────────────┐
     │ LLM Router  │───────▶│ Anthropic / OpenAI│
     └─────┬──────┘        │ Gemini / Bedrock  │
           │               └─────────────────┘
     ┌─────▼──────┐
     │ Tool Executor│ ← web_search, file_ops, email, custom tools
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

### LLM
- **Multi-provider routing** — Anthropic, OpenAI, Azure OpenAI, Google Gemini, AWS Bedrock
- **Tag-based routing** — route by agent tag (e.g. `fast` → Gemini, `cheap` → Haiku)
- **Fallback chains** — automatically failover to the next provider on error
- **Cost ledger** — per-agent, per-tenant token cost tracking

### Skills & Tools
- **Built-in skills** — `web-search`, `file-ops`, `email-composer`
- **Skill marketplace** — community skills catalog with `agentix skill install`
- **SkillHub** — local skill registry with YAML spec + Python implementation pattern
- **`@tool` decorator** — register any Python function as an agent tool

### Security
- **RBAC engine** — 5-level role hierarchy (end-user → operator → agent-author → tenant-admin → platform-admin)
- **JWT + API key auth** — Bearer tokens and `sk-agentix-` prefixed service account keys
- **Audit log** — tamper-evident HMAC-chained audit records for every action
- **Secrets backend** — env vars, HashiCorp Vault, AWS Secrets Manager (pluggable)
- **PII detection & redaction** — regex + optional Presidio integration
- **Skill RBAC** — per-skill permission gates enforced at activation time

### Compliance
- **GDPR engine** — right to erasure, data export (portability), consent tracking, pseudonymisation
- **SOC2 evidence bundle** — automated ZIP export of access logs, audit trails, and config snapshots
- **Retention engine** — configurable data retention policies with automatic purge

### Observability
- **OpenTelemetry tracing** — distributed traces exported to any OTLP-compatible collector
- **Cost metrics** — per-provider, per-agent, per-tenant spend dashboards
- **Admin UI** — React/Vite/Tailwind dashboard for agents, triggers, skills, audit, and metrics

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
├── llm/               # LLM router + provider adapters
├── skills/            # Skill engine, marketplace, SkillHub
├── storage/           # StateStore (SQLite/PostgreSQL), tenant management
├── memory/            # Vector store (sqlite-vec / pgvector / pure-Python)
├── security/          # RBAC, audit log, identity, secrets, skill RBAC
├── compliance/        # GDPR, PII detection, SOC2, retention
├── orchestration/     # Multi-agent patterns (pipeline, fan-out, event bus)
├── scheduler/         # Cron scheduler engine
├── observability/     # OpenTelemetry tracing, cost ledger
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

This boots the watchdog on `http://localhost:8080` with the HTTP webhook channel enabled and registers any agents found in `agents/`.

### Send your first trigger

```bash
curl -X POST http://localhost:8080/trigger \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "research-assistant", "text": "What is the capital of France?"}'
```

### Start the Admin API (optional)

```bash
uvicorn agentix.api.app:create_app --factory --reload --port 8090
# Swagger UI: http://localhost:8090/docs
```

---

## Configuration

The main config file is `config/watchdog.yaml`. Environment variables override file values with `${VAR_NAME}` syntax.

```yaml
# config/watchdog.yaml
watchdog:
  port: 8080
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
  db_path: data/agentix.db   # use postgresql://... for Standard tier
```

See [docs/setup-guide.md](docs/setup-guide.md) for the complete configuration reference including WhatsApp, Telegram, Slack, SQS, gRPC, PostgreSQL, Redis, Vault, and Kubernetes setup.

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
  team: "platform"
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
    - file-ops

  tools:
    - web_search
    - web_fetch
    - file_read

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

## Channels

All channels normalise their payloads to a `TriggerEnvelope` before dispatch. Enable a channel by providing its credentials (in `.env` or `config/watchdog.yaml`).

| Channel | Enable by setting |
|---|---|
| HTTP Webhook | Always enabled (listens on watchdog port) |
| Slack | `SLACK_BOT_TOKEN` + `SLACK_SIGNING_SECRET` |
| WhatsApp | `WHATSAPP_ACCESS_TOKEN` + `WHATSAPP_PHONE_ID` |
| Telegram | `TELEGRAM_BOT_TOKEN` |
| Microsoft Teams | `TEAMS_APP_ID` + `TEAMS_APP_PASSWORD` |
| Email (IMAP) | `EMAIL_IMAP_HOST` + credentials |
| AWS SQS | `SQS_QUEUE_URL` (+ AWS credentials) |
| gRPC | `GRPC_LISTEN_PORT` |

See [docs/setup-guide.md](docs/setup-guide.md#whatsapp-channel-configuration) for a step-by-step WhatsApp setup example.

---

## LLM Providers

| Provider | Config key | Notes |
|---|---|---|
| Anthropic | `anthropic` | Claude 3.x / Claude 4.x family |
| OpenAI | `openai` | GPT-4o, GPT-4o-mini, o1 |
| Azure OpenAI | `azure_openai` | Requires endpoint + deployment name |
| Google Gemini | `gemini` | Gemini 2.0 Flash recommended |
| AWS Bedrock | `bedrock` | Uses boto3 credential chain |
| **Local / self-hosted** | `local`, `ollama`, `lmstudio`, `vllm` | Any OpenAI-compatible server |

Routing is tag-based. Tag an agent with `tags: [fast]` and set `match_tag: fast → provider: gemini` in the routing rules to route automatically.

### Local models (Ollama, LM Studio, vLLM, llama.cpp)

Any server that exposes an OpenAI-compatible `/v1/chat/completions` endpoint works out of the box.

**Ollama** (default port 11434):
```bash
ollama pull llama3.2          # or mistral, qwen2.5, phi3, gemma2, etc.
ollama serve
```

```yaml
# config/watchdog.yaml
llm:
  default_provider: ollama
  providers:
    ollama:
      base_url: http://localhost:11434/v1
      model: llama3.2
      api_key: ollama              # placeholder — not validated
```

**LM Studio** (default port 1234):
```yaml
llm:
  default_provider: lmstudio
  providers:
    lmstudio:
      base_url: http://localhost:1234/v1
      model: lmstudio-community/Meta-Llama-3.1-8B-Instruct-GGUF
```

**vLLM** (production GPU server):
```yaml
llm:
  default_provider: vllm
  providers:
    vllm:
      base_url: http://gpu-server:8000/v1
      model: mistralai/Mistral-7B-Instruct-v0.3
      api_key: ${VLLM_API_KEY}
```

**Mixed routing** — local for privacy-sensitive tags, cloud for everything else:
```yaml
llm:
  default_provider: anthropic
  providers:
    anthropic:
      api_key: ${ANTHROPIC_API_KEY}
      model: claude-sonnet-4-6
    ollama:
      base_url: http://localhost:11434/v1
      model: llama3.2
  routing:
    rules:
      - match_tag: private          # keep data on-prem
        provider: ollama
      - match_tag: fast
        provider: anthropic
    fallback_chain: [anthropic, ollama]
```

**Tool use with local models**: Most modern quantised models (Llama 3.1+, Mistral, Qwen 2.5, Phi-3) support tool calling. If your model does not, disable it and Agentix routes tool calls via prompt:
```yaml
providers:
  ollama:
    base_url: http://localhost:11434/v1
    model: phi3           # older model — no native tool support
    supports_tools: false
```

**Env var overrides** (useful in Docker/K8s):
```
LOCAL_LLM_BASE_URL=http://ollama-service:11434/v1
LOCAL_LLM_MODEL=llama3.2
LOCAL_LLM_API_KEY=ollama
```

---

## Skills & Tools

### Built-in skills

| Skill | Tools provided |
|---|---|
| `web-search` | `web_search`, `web_fetch` |
| `file-ops` | `file_read`, `file_write`, `file_list` |
| `email-composer` | `send_email`, `draft_email` |

### Custom tools

```python
from agentix.agent_runtime.tool_executor import tool, register_tool

@tool(
    name="get_weather",
    description="Get current weather for a city",
    input_schema={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
)
def get_weather(city: str) -> dict:
    # your implementation
    return {"city": city, "temp_c": 22, "condition": "sunny"}
```

### Marketplace

```bash
agentix skill list                  # list available skills
agentix skill install zendesk-support
agentix skill install github-ops
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

### Service accounts

```bash
# Create a service account API key for CI/CD
agentix service-account create \
  --name "ci-pipeline" \
  --roles operator \
  --tenant my-tenant
```

The key is returned **once** in plaintext and stored only as a bcrypt hash. Use it as a Bearer token: `Authorization: Bearer sk-agentix-<key>`.

### Audit log

Every agent invocation, skill activation, and admin action is written to a tamper-evident HMAC-chained audit log:

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

### SOC2 evidence

```bash
python -c "
from agentix.compliance.soc2 import SOC2Exporter
e = SOC2Exporter('data/agentix.db', {})
path = e.export('compliance/')
print('Bundle:', path)
"
```

See [docs/privacy.md](docs/privacy.md) for the full privacy and data handling guide.

---

## Deployment

### Docker

```bash
docker build -t agentix:latest .
docker run -p 8080:8080 --env-file .env agentix:latest
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

This provisions EKS, RDS (PostgreSQL), ElastiCache (Redis), and an ALB.

### GitOps (ArgoCD)

```bash
kubectl apply -f deploy/argocd/application.yaml
# ArgoCD auto-syncs on every push to main
```

---

## CLI Reference

```
agentix dev start                        Start watchdog in dev mode

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

---

## Contributing

Contributions are welcome! Please follow these steps:

1. **Fork** the repository and create a feature branch: `git checkout -b feat/my-feature`
2. **Install dev dependencies**: `pip install -e ".[dev]"`
3. **Make your changes** — keep commits focused and descriptive
4. **Run checks** before pushing:
   ```bash
   ruff check agentix/ tests/    # lint
   mypy agentix/ --ignore-missing-imports  # type check
   pytest tests/ -v              # tests
   ```
5. **Open a pull request** against `main` with a clear description of the change

### Code style

- Formatter: `black` (line length 100)
- Linter: `ruff`
- Type checker: `mypy` (strict on new files)
- All public APIs should have docstrings
- New features require a test in `tests/`

### Reporting bugs

Please open an issue with:
- Python version and OS
- Minimal reproduction steps
- Full error traceback

### Feature requests

Open an issue tagged `enhancement` with a description of the use case and proposed API.

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
