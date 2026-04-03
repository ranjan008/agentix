# Agentix Platform — Setup & Configuration Guide

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Installation](#2-installation)
3. [Directory Structure](#3-directory-structure)
4. [Core Configuration](#4-core-configuration)
5. [AI Model Connection](#5-ai-model-connection)
6. [Channel Configuration — WhatsApp Example](#6-channel-configuration--whatsapp-example)
7. [Create Your First Agent](#7-create-your-first-agent)
8. [Run the Platform](#8-run-the-platform)
9. [Verify End-to-End](#9-verify-end-to-end)
10. [Production Deployment](#10-production-deployment)

---

## 1. Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11+ | 3.12 recommended |
| pip | 23+ | `pip install --upgrade pip` |
| Git | Any | |
| SQLite | 3.35+ | Bundled with Python — no install needed |
| A Meta Business account | — | Required for WhatsApp Cloud API |
| An Anthropic API key | — | Or any other supported LLM provider |

Optional (for Standard/Enterprise tier):

| Service | Purpose |
|---|---|
| PostgreSQL 15+ | Persistent state store (replaces SQLite) |
| Redis 7+ | Rate limiting, HA leader election, event bus |

---

## 2. Installation

### 2.1 Clone the repository

```bash
git clone https://github.com/ranjan008/agentix.git
cd agentix
git checkout claude/phase-4-enterprise
```

### 2.2 Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\activate         # Windows
```

### 2.3 Install dependencies

**Lite tier** (SQLite only, no external services):
```bash
pip install -e .
```

**Standard tier** (PostgreSQL + Redis):
```bash
pip install -e ".[standard]"
```

**All optional integrations** (all LLM providers, gRPC, Presidio PII):
```bash
pip install -e ".[full]"
```

### 2.4 Create required directories

```bash
mkdir -p data agents schedules skills logs
```

---

## 3. Directory Structure

```
agentix/
├── config/
│   ├── watchdog.yaml          ← Main platform config (edit this)
│   └── policy.yaml            ← RBAC policy rules
├── agents/
│   └── my-agent.yaml          ← Agent definitions (one file per agent)
├── schedules/                 ← Cron / DAG schedule YAML files
├── skills/                    ← Installed skill packages
├── data/
│   └── agentix.db             ← SQLite state store (auto-created)
├── .env                       ← Secret credentials (never commit this)
└── logs/                      ← Watchdog + agent logs
```

---

## 4. Core Configuration

### 4.1 Create `.env`

Create a `.env` file in the project root. This file holds **all secrets** — never commit it to git.

```bash
cp .env.example .env
```

Edit `.env`:

```dotenv
# ── LLM Provider ─────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-...           # Anthropic Claude (default)
# OPENAI_API_KEY=sk-...               # OpenAI (optional)
# GOOGLE_API_KEY=...                  # Google Gemini (optional)

# ── Security ─────────────────────────────────────────────────────────────────
JWT_SECRET=change-me-use-32-random-chars
AUDIT_HMAC_SECRET=change-me-use-32-random-chars

# ── WhatsApp (Meta Cloud API) ─────────────────────────────────────────────────
WHATSAPP_ACCESS_TOKEN=EAAxxxxxxxxxxxx   # Permanent system-user token
WHATSAPP_PHONE_NUMBER_ID=1234567890    # From Meta Business dashboard
WHATSAPP_VERIFY_TOKEN=my-random-verify-token   # You choose this string
WHATSAPP_APP_SECRET=abc123...          # Optional — for HMAC request verification

# ── Database (Standard tier only) ────────────────────────────────────────────
# DATABASE_URL=postgresql://agentix:password@localhost:5432/agentix
# REDIS_URL=redis://localhost:6379/0
```

Load `.env` automatically by installing python-dotenv and adding to config:

```bash
pip install python-dotenv
```

Or export manually before starting:

```bash
export $(grep -v '^#' .env | xargs)
```

### 4.2 Edit `config/watchdog.yaml`

This is the main platform config file.

```yaml
# config/watchdog.yaml

# ── Infrastructure tier ───────────────────────────────────────────────────────
infra_tier: lite          # lite | standard | enterprise
db_path: data/agentix.db

# ── Agent execution ───────────────────────────────────────────────────────────
max_concurrent_agents: 10
shutdown_timeout_sec: 30

# ── LLM Provider routing ─────────────────────────────────────────────────────
llm:
  default_provider: anthropic
  providers:
    anthropic:
      model: claude-sonnet-4-6
      # api_key resolved from ANTHROPIC_API_KEY env var

  routing:
    fallback_chain:
      - anthropic             # Try Anthropic first
      # - openai              # Fall back to OpenAI if Anthropic fails

# ── Security ─────────────────────────────────────────────────────────────────
security:
  enforce_rbac: false         # Set true to enforce role-based access
  jwt_secret_env: JWT_SECRET
  policy_file: config/policy.yaml

# ── Rate limiting ─────────────────────────────────────────────────────────────
rate_limit:
  max_requests: 60
  window_sec: 60

# ── Scheduler ────────────────────────────────────────────────────────────────
scheduler:
  schedules_dir: schedules
  tick_sec: 5

# ── Channels (see Section 6 for WhatsApp details) ────────────────────────────
channels:
  http:
    port: 8080
    path: /trigger

  whatsapp:
    enabled: true
    webhook_path: /channels/whatsapp
    # Credentials resolved from env vars:
    #   WHATSAPP_ACCESS_TOKEN
    #   WHATSAPP_PHONE_NUMBER_ID
    #   WHATSAPP_VERIFY_TOKEN
    #   WHATSAPP_APP_SECRET (optional)
    default_agent_id: whatsapp-agent   # Which agent handles WhatsApp messages
```

---

## 5. AI Model Connection

Agentix supports multiple LLM providers. The `LLMRouter` selects the provider at runtime based on configuration.

### 5.1 Anthropic Claude (default)

```dotenv
# .env
ANTHROPIC_API_KEY=sk-ant-api03-...
```

```yaml
# config/watchdog.yaml
llm:
  default_provider: anthropic
  providers:
    anthropic:
      model: claude-sonnet-4-6     # or claude-opus-4-6, claude-haiku-4-5
```

### 5.2 OpenAI

```dotenv
OPENAI_API_KEY=sk-proj-...
```

```yaml
llm:
  default_provider: openai
  providers:
    openai:
      model: gpt-4o                # or gpt-4o-mini, gpt-4-turbo
```

### 5.3 Google Gemini

```dotenv
GOOGLE_API_KEY=AIzaSy...
```

```yaml
llm:
  providers:
    gemini:
      model: gemini-2.0-flash
```

### 5.4 Azure OpenAI

```dotenv
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://my-resource.openai.azure.com
AZURE_OPENAI_API_VERSION=2024-02-01
```

```yaml
llm:
  providers:
    azure_openai:
      model: gpt-4o               # Your deployment name
```

### 5.5 Multi-provider with fallback

Use multiple providers so the platform falls back automatically if one fails:

```yaml
llm:
  default_provider: anthropic
  providers:
    anthropic:
      model: claude-sonnet-4-6
    openai:
      model: gpt-4o
    gemini:
      model: gemini-2.0-flash
  routing:
    fallback_chain:
      - anthropic
      - openai
      - gemini
```

### 5.6 Route specific agents to a specific model

Add `tags` to your agent spec and routing rules in watchdog.yaml:

```yaml
# config/watchdog.yaml
llm:
  routing:
    rules:
      - match_tag: fast       # agents tagged "fast" use Gemini Flash
        provider: gemini
        model: gemini-2.0-flash
      - match_tag: powerful   # agents tagged "powerful" use Claude Opus
        provider: anthropic
        model: claude-opus-4-6
```

```yaml
# agents/my-agent.yaml
spec:
  tags:
    - fast
```

---

## 6. Channel Configuration — WhatsApp Example

WhatsApp uses the **Meta Cloud API** (Webhooks). Messages flow like this:

```
User on WhatsApp
    │  (sends message)
    ▼
Meta servers
    │  (HTTP POST to your webhook URL)
    ▼
Agentix Watchdog  /channels/whatsapp
    │  (normalises to TriggerEnvelope)
    ▼
Agent (LLM loop)
    │  (calls WhatsApp API to reply)
    ▼
User on WhatsApp
```

### Step 1 — Expose Agentix to the internet

WhatsApp requires a public HTTPS URL. During development use **ngrok**:

```bash
# Install ngrok: https://ngrok.com/download
ngrok http 8080
# Note the HTTPS URL, e.g.: https://abc123.ngrok-free.app
```

In production, put Agentix behind a reverse proxy (nginx, Caddy, ALB) with a valid TLS certificate.

### Step 2 — Create a Meta App

1. Go to [developers.facebook.com](https://developers.facebook.com) → **My Apps** → **Create App**
2. Select **Business** type
3. Add the **WhatsApp** product to your app
4. Under **WhatsApp → API Setup**:
   - Note your **Phone Number ID** → set as `WHATSAPP_PHONE_NUMBER_ID` in `.env`
   - Generate a **Permanent System User Token** → set as `WHATSAPP_ACCESS_TOKEN` in `.env`

### Step 3 — Register the webhook

1. In Meta App Dashboard → **WhatsApp → Configuration → Webhook**
2. Click **Edit** and fill in:
   - **Callback URL:** `https://abc123.ngrok-free.app/channels/whatsapp`
   - **Verify Token:** the same string you put in `WHATSAPP_VERIFY_TOKEN` in `.env`
3. Click **Verify and Save**
4. Subscribe to the **messages** webhook field

### Step 4 — Configure `watchdog.yaml`

```yaml
# config/watchdog.yaml
channels:
  whatsapp:
    enabled: true
    webhook_path: /channels/whatsapp
    default_agent_id: whatsapp-agent
    # All credentials read from env vars automatically
```

### Step 5 — Create the WhatsApp agent

Create `agents/whatsapp-agent.yaml`:

```yaml
apiVersion: agentix/v1
kind: Agent

metadata:
  name: whatsapp-agent
  version: "1.0.0"
  description: "Customer support agent for WhatsApp"

spec:
  model: claude-sonnet-4-6

  system_prompt: |
    You are a friendly customer support assistant.
    You are responding to customers via WhatsApp.
    Keep your replies concise — WhatsApp messages should be short and readable on mobile.
    Use plain text only; avoid markdown formatting.

  skills:
    - web_search        # Enable web search if needed
    # - file_ops        # Only add if the agent needs file access

  tools: []

  memory:
    ttl_sec: 3600       # Remember conversation context for 1 hour

  output:
    channel: whatsapp   # Replies go back to WhatsApp

  # Route this agent to a fast/cheap model
  tags:
    - fast
```

Register the agent in the database on first run:

```bash
python -m agentix.cli.main agent register agents/whatsapp-agent.yaml
```

### Step 6 — (Optional) Send outbound messages programmatically

```python
import asyncio
from agentix.watchdog.channels.whatsapp import WhatsAppChannel

channel = WhatsAppChannel(cfg={}, on_trigger=None, app=None)

# Send a plain text message
asyncio.run(channel.send_text(
    to="15551234567",       # E.164 format, no +
    body="Hello from Agentix!"
))

# Send a message template (required for first outreach)
asyncio.run(channel.send_template(
    to="15551234567",
    template_name="hello_world",
    language_code="en_US",
))

# Send interactive buttons
asyncio.run(channel.send_interactive(
    to="15551234567",
    interactive={
        "type": "button",
        "body": {"text": "How can I help you today?"},
        "action": {
            "buttons": [
                {"type": "reply", "reply": {"id": "faq", "title": "FAQ"}},
                {"type": "reply", "reply": {"id": "human", "title": "Talk to human"}},
            ]
        }
    }
))
```

### WhatsApp webhook payload reference

Agentix normalises all incoming WhatsApp events into a `TriggerEnvelope`. The key fields your agent receives in `payload.context`:

| Field | Value | Description |
|---|---|---|
| `text` | `"Hello!"` | Message body (for text messages) |
| `type` | `text` / `image` / `interactive` | Message type |
| `message_id` | `"wamid.xxx"` | WhatsApp message ID |
| `button_id` | `"faq"` | Set when user taps an interactive button |
| `media` | `{id, mime_type}` | Set for image/audio/document messages |

The sender's phone number is in `identity.user_id`.

---

## 7. Create Your First Agent

A minimal agent that answers questions via WhatsApp:

```yaml
# agents/whatsapp-agent.yaml
apiVersion: agentix/v1
kind: Agent

metadata:
  name: whatsapp-agent
  version: "1.0.0"

spec:
  model: claude-sonnet-4-6
  system_prompt: |
    You are a concise assistant on WhatsApp.
    Reply in plain text, under 300 characters when possible.
  skills: []
  tools: []
  memory:
    ttl_sec: 3600
```

Register it:

```bash
python -m agentix.cli.main agent register agents/whatsapp-agent.yaml
```

Verify it's registered:

```bash
python -m agentix.cli.main agent list
```

---

## 8. Run the Platform

### 8.1 Load environment variables

```bash
export $(grep -v '^#' .env | xargs)
```

### 8.2 Start the Watchdog

```bash
python -m agentix.watchdog.main config/watchdog.yaml
```

Expected output:

```
2026-04-02 10:00:00 [INFO] agentix.watchdog: Agentix Watchdog starting (tier=lite)
2026-04-02 10:00:00 [INFO] agentix.watchdog: Channel HTTPWebhookChannel started
2026-04-02 10:00:00 [INFO] agentix.watchdog: Channel WhatsAppChannel started
2026-04-02 10:00:00 [INFO] agentix.watchdog: Watchdog ready — all configured channels active
```

### 8.3 Start the Admin UI (optional)

In a second terminal:

```bash
pip install uvicorn fastapi
uvicorn agentix.api.app:app --host 0.0.0.0 --port 8090 --reload
```

Open `http://localhost:8090/docs` for the API explorer or `http://localhost:8090/ui` for the dashboard (after building the React frontend).

### 8.4 Run as a background service (systemd)

Create `/etc/systemd/system/agentix.service`:

```ini
[Unit]
Description=Agentix Watchdog
After=network.target

[Service]
Type=simple
User=agentix
WorkingDirectory=/opt/agentix
EnvironmentFile=/opt/agentix/.env
ExecStart=/opt/agentix/.venv/bin/python -m agentix.watchdog.main config/watchdog.yaml
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable agentix
sudo systemctl start agentix
sudo journalctl -u agentix -f    # tail logs
```

---

## 9. Verify End-to-End

### 9.1 Test the HTTP channel

```bash
# Send a test trigger via the HTTP webhook
curl -X POST http://localhost:8080/trigger/whatsapp-agent \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello, what can you do?"}'
```

### 9.2 Test the WhatsApp webhook verification

```bash
curl "http://localhost:8080/channels/whatsapp?\
hub.mode=subscribe&\
hub.verify_token=my-random-verify-token&\
hub.challenge=test123"
# Expected response: test123
```

### 9.3 Send a real WhatsApp message

1. Add your phone number as a test number in the Meta App Dashboard
2. Send any message to your WhatsApp Business number
3. Watch the watchdog logs — you should see:
   ```
   [INFO] agentix.watchdog: Channel WhatsAppChannel dispatching message from +15551234567
   [INFO] agentix.agent_runtime: Agent loaded: whatsapp-agent v1.0.0
   [INFO] agentix.agent_runtime: Agent finished: whatsapp-agent
   ```
4. The reply arrives in WhatsApp from your Business number

### 9.4 Check trigger status

```bash
python -m agentix.cli.main trigger list
```

---

## 10. Production Deployment

### Option A — Docker

```bash
# Build
docker build -t agentix:4.0.0 .

# Run
docker run -d \
  --name agentix \
  --env-file .env \
  -p 8080:8080 \
  -p 8090:8090 \
  -v $(pwd)/data:/data \
  -v $(pwd)/agents:/app/agents \
  -v $(pwd)/config:/app/config \
  agentix:4.0.0
```

### Option B — Kubernetes (Helm)

```bash
# Create namespace
kubectl create namespace agentix

# Create secrets from .env
kubectl create secret generic agentix-secrets \
  --namespace agentix \
  --from-env-file=.env

# Install chart
helm upgrade --install agentix deploy/helm/agentix \
  --namespace agentix \
  --set image.tag=4.0.0 \
  --set existingSecrets.anthropicApiKey=agentix-secrets \
  --set existingSecrets.jwtSecret=agentix-secrets \
  --values deploy/helm/agentix/values.yaml
```

### Checklist before going live

- [ ] `JWT_SECRET` and `AUDIT_HMAC_SECRET` are strong random values (32+ chars)
- [ ] `.env` is not in git (`.gitignore` covers it)
- [ ] WhatsApp webhook URL uses HTTPS with a valid TLS certificate
- [ ] `WHATSAPP_APP_SECRET` is set for HMAC request verification
- [ ] `security.enforce_rbac: true` in `watchdog.yaml`
- [ ] Rate limits are configured appropriately
- [ ] `data/` directory is on a persistent volume
- [ ] Log rotation is configured
- [ ] A monitoring alert is set on `/healthz` returning non-200

---

## Quick Reference

| Task | Command |
|---|---|
| Start watchdog | `python -m agentix.watchdog.main config/watchdog.yaml` |
| Start admin API | `uvicorn agentix.api.app:app --port 8090` |
| Register agent | `python -m agentix.cli.main agent register agents/my-agent.yaml` |
| List agents | `python -m agentix.cli.main agent list` |
| List triggers | `python -m agentix.cli.main trigger list` |
| Send test trigger | `python -m agentix.cli.main trigger send <agent-id> "message"` |
| Generate dev token | `python -m agentix.cli.main token create --role operator` |
| Verify audit chain | `python -m agentix.cli.main audit verify` |
| Run tests | `pytest tests/ -v` |
