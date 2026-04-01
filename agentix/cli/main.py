"""
Agentix CLI — entry point for all agentix commands.

Commands:
  agentix dev start           Start the watchdog in dev mode
  agentix agent list          List registered agents
  agentix agent register      Register an agent from a YAML file
  agentix agent run           Manually trigger an agent
  agentix skill list          List installed skills
  agentix skill install       Install a skill
  agentix trigger list        List recent triggers
  agentix token generate      Generate a dev JWT token
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click
import yaml

from agentix.storage.state_store import StateStore
from agentix.watchdog.auth import make_jwt


def _get_store(db_path: str = "data/agentix.db") -> StateStore:
    return StateStore(db_path)


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------

@click.group()
@click.version_option("0.1.0", prog_name="agentix")
def cli():
    """Agentix — Serverless Agentic Platform"""


# ---------------------------------------------------------------------------
# dev
# ---------------------------------------------------------------------------

@cli.group()
def dev():
    """Development commands."""


@dev.command("start")
@click.option("--config", "-c", default="config/watchdog.yaml", help="Path to watchdog config")
@click.option("--port", "-p", default=None, type=int, help="Override HTTP webhook port")
def dev_start(config: str, port: int | None):
    """Start the watchdog in development mode."""
    if not Path(config).exists():
        click.echo(f"Config not found: {config}. Creating default config…")
        _create_default_config(config, port or 8080)

    click.echo(f"Starting Agentix watchdog (config={config})")

    if port:
        # Patch port into environment for the watchdog to pick up
        os.environ["AGENTIX_HTTP_PORT"] = str(port)

    from agentix.watchdog.main import run
    run(config)


def _create_default_config(path: str, port: int = 8080) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    cfg = {
        "watchdog": {
            "infra_tier": "lite",
            "db_path": "data/agentix.db",
            "max_concurrent_agents": 10,
            "shutdown_timeout_sec": 30,
            "rate_limit": {"max_requests": 60, "window_sec": 60},
            "security": {
                "enforce_rbac": False,
                "jwt_secret_env": "JWT_SECRET",
            },
            "channels": [
                {"type": "http_webhook", "port": port, "path": "/trigger"},
            ],
        }
    }
    with open(path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False)
    click.echo(f"Created default config at {path}")


# ---------------------------------------------------------------------------
# agent
# ---------------------------------------------------------------------------

@cli.group()
def agent():
    """Agent management commands."""


@agent.command("list")
@click.option("--db", default="data/agentix.db", help="Database path")
def agent_list(db: str):
    """List all registered agents."""
    store = _get_store(db)
    agents = store.list_agents()
    if not agents:
        click.echo("No agents registered. Use 'agentix agent register <file.yaml>' to add one.")
        return
    click.echo(f"{'NAME':<30} {'VERSION':<12}")
    click.echo("-" * 44)
    for a in agents:
        click.echo(f"{a['name']:<30} {a['version']:<12}")


@agent.command("register")
@click.argument("spec_file")
@click.option("--db", default="data/agentix.db", help="Database path")
def agent_register(spec_file: str, db: str):
    """Register an agent from a YAML spec file."""
    from agentix.agent_runtime.loader import load_agent_spec
    try:
        spec = load_agent_spec(spec_file)
    except Exception as e:
        click.echo(f"Error loading spec: {e}", err=True)
        sys.exit(1)

    store = _get_store(db)
    store.upsert_agent(spec)
    name = spec["metadata"]["name"]
    version = spec["metadata"].get("version", "?")
    click.echo(f"Registered agent: {name} v{version}")


@agent.command("run")
@click.argument("agent_id")
@click.option("--text", "-t", default="Hello", help="Trigger text payload")
@click.option("--db", default="data/agentix.db", help="Database path")
@click.option("--identity", default="cli-user", help="Caller identity ID")
def agent_run(agent_id: str, text: str, db: str, identity: str):
    """Manually trigger an agent (dev shortcut)."""
    import asyncio
    from agentix.watchdog import trigger_normalizer as tn
    from agentix.watchdog.agent_spawner import AgentSpawner

    envelope = tn.from_http(
        body={"text": text, "agent_id": agent_id},
        headers={"x-identity-id": identity, "x-roles": "operator", "x-tenant-id": "default"},
        agent_id=agent_id,
    )

    store = _get_store(db)
    agent_spec = store.get_agent(agent_id)
    if not agent_spec:
        # Check agents/ dir
        from agentix.agent_runtime.loader import find_agent_spec, load_agent_spec
        path = find_agent_spec(agent_id)
        if path:
            spec = load_agent_spec(path)
            store.upsert_agent(spec)
            click.echo(f"Auto-registered agent from {path}")
        else:
            click.echo(f"Agent '{agent_id}' not found. Register it first with 'agentix agent register'.", err=True)
            sys.exit(1)

    store.create_trigger(envelope)
    click.echo(f"Trigger {envelope['id']} → {agent_id}")
    click.echo("Spawning agent…")

    spawner = AgentSpawner(db_path=db)

    async def _run():
        await spawner.spawn(envelope)
        while spawner.active_count > 0:
            await asyncio.sleep(0.5)

    asyncio.run(_run())
    click.echo("Done.")


# ---------------------------------------------------------------------------
# skill
# ---------------------------------------------------------------------------

@cli.group()
def skill():
    """Skill management commands."""


@skill.command("list")
@click.option("--db", default="data/agentix.db", help="Database path")
def skill_list(db: str):
    """List installed skills."""
    store = _get_store(db)
    skills = store.list_skills()

    # Also show built-ins
    from agentix.skills.engine import _BUILTIN_SKILLS
    builtin_names = set(_BUILTIN_SKILLS.keys())
    installed_names = {s["name"] for s in skills}

    click.echo(f"{'NAME':<30} {'VERSION':<10} {'SOURCE'}")
    click.echo("-" * 56)
    for name in sorted(builtin_names):
        marker = "*" if name in installed_names else " "
        click.echo(f"{name:<30} {'built-in':<10} built-in{marker}")
    for s in skills:
        if s["name"] not in builtin_names:
            click.echo(f"{s['name']:<30} {s['version']:<10} {s['source']}")


@skill.command("install")
@click.argument("name")
@click.option("--version", default="latest", help="Version to install")
@click.option("--db", default="data/agentix.db", help="Database path")
def skill_install(name: str, version: str, db: str):
    """Install a skill (local path or SkillHub name)."""
    store = _get_store(db)

    # Local YAML path
    if name.endswith(".yaml") or name.endswith(".yml"):
        path = Path(name)
        if not path.exists():
            click.echo(f"Skill file not found: {name}", err=True)
            sys.exit(1)
        with open(path) as f:
            spec = yaml.safe_load(f)
        skill_name = spec["metadata"]["name"]
        skill_version = spec["metadata"].get("version", version)
        store.install_skill(skill_name, skill_version, "local", spec)
        click.echo(f"Installed skill: {skill_name} v{skill_version} (local)")
        return

    # Built-in shortcut
    from agentix.skills.engine import _BUILTIN_SKILLS
    if name in _BUILTIN_SKILLS:
        store.install_skill(name, "built-in", "builtin", {"name": name, "description": f"Built-in skill: {name}"})
        click.echo(f"Registered built-in skill: {name}")
        return

    click.echo(f"SkillHub install not yet available in Phase 1. Available built-ins: {', '.join(_BUILTIN_SKILLS)}")


# ---------------------------------------------------------------------------
# trigger
# ---------------------------------------------------------------------------

@cli.group()
def trigger():
    """Trigger history commands."""


@trigger.command("list")
@click.option("--db", default="data/agentix.db", help="Database path")
@click.option("--limit", default=20, help="Number of triggers to show")
def trigger_list(db: str, limit: int):
    """List recent triggers."""
    import sqlite3
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, agent_id, channel, status, created_at FROM triggers ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()

    if not rows:
        click.echo("No triggers recorded yet.")
        return

    click.echo(f"{'TRIGGER ID':<22} {'AGENT':<25} {'CHANNEL':<14} {'STATUS':<10} CREATED")
    click.echo("-" * 90)
    for r in rows:
        import datetime
        ts = datetime.datetime.fromtimestamp(r["created_at"]).strftime("%Y-%m-%d %H:%M:%S")
        click.echo(f"{r['id']:<22} {r['agent_id']:<25} {r['channel']:<14} {r['status']:<10} {ts}")


# ---------------------------------------------------------------------------
# token
# ---------------------------------------------------------------------------

@cli.group()
def token():
    """Token management commands."""


@token.command("generate")
@click.option("--identity", default="dev-user", help="Identity ID (sub claim)")
@click.option("--role", multiple=True, default=["operator"], help="Roles (repeatable)")
@click.option("--tenant", default="default", help="Tenant ID")
@click.option("--ttl", default=3600, help="Token TTL in seconds")
@click.option("--secret", default=None, help="JWT secret (default: $JWT_SECRET env var)")
def token_generate(identity: str, role: tuple, tenant: str, ttl: int, secret: str | None):
    """Generate a dev JWT token for testing the HTTP webhook."""
    jwt_secret = secret or os.environ.get("JWT_SECRET", "dev-secret-change-me")
    claims = {
        "sub": identity,
        "roles": list(role),
        "tenant_id": tenant,
    }
    tok = make_jwt(claims, jwt_secret, ttl_sec=ttl)
    click.echo(tok)


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------

@cli.group()
def audit():
    """Audit log commands."""


@audit.command("list")
@click.option("--db", default="data/agentix.db", help="Database path")
@click.option("--tenant", default=None, help="Filter by tenant")
@click.option("--agent", default=None, help="Filter by agent_id")
@click.option("--event", default=None, help="Filter by event_type (glob supported)")
@click.option("--limit", default=20, help="Max entries to show")
def audit_list(db: str, tenant: str | None, agent: str | None, event: str | None, limit: int):
    """List audit log entries."""
    from agentix.security.audit import AuditLog
    import os
    al = AuditLog(db, hmac_secret=os.environ.get("AUDIT_HMAC_SECRET", ""))
    entries = al.query(tenant_id=tenant, agent_id=agent, event_type=event, limit=limit)
    if not entries:
        click.echo("No audit entries found.")
        return
    click.echo(f"{'SEQ':<6} {'EVENT':<25} {'AGENT':<22} {'ACTOR':<20} TIMESTAMP")
    click.echo("-" * 90)
    for e in entries:
        import datetime
        ts = datetime.datetime.fromtimestamp(e["ts"]).strftime("%Y-%m-%d %H:%M:%S")
        click.echo(
            f"{e['seq']:<6} {(e['event_type'] or ''):<25} "
            f"{(e['agent_id'] or ''):<22} {(e['actor'] or ''):<20} {ts}"
        )


@audit.command("verify")
@click.option("--db", default="data/agentix.db", help="Database path")
@click.option("--tenant", default=None, help="Verify only this tenant's chain slice")
def audit_verify(db: str, tenant: str | None):
    """Verify the integrity of the audit log chain."""
    from agentix.security.audit import AuditLog
    import os
    al = AuditLog(db, hmac_secret=os.environ.get("AUDIT_HMAC_SECRET", ""))
    ok, msg = al.verify_chain(tenant_id=tenant)
    symbol = "✓" if ok else "✗"
    click.echo(f"{symbol} {msg}")
    if not ok:
        sys.exit(1)


# ---------------------------------------------------------------------------
# secret
# ---------------------------------------------------------------------------

@cli.group()
def secret():
    """Secrets vault commands."""


@secret.command("set")
@click.argument("key")
@click.argument("value")
@click.option("--backend", default="file", help="Backend: env | file | vault | aws_sm")
@click.option("--path", default="data/secrets.json", help="File backend path")
def secret_set(key: str, value: str, backend: str, path: str):
    """Store a secret in the vault."""
    from agentix.security.secrets import SecretsVault
    vault = SecretsVault.from_config({"backend": backend, "path": path})
    vault.set(key, value)
    click.echo(f"Secret '{key}' stored ({backend} backend).")


@secret.command("get")
@click.argument("key")
@click.option("--backend", default="file", help="Backend: env | file | vault | aws_sm")
@click.option("--path", default="data/secrets.json", help="File backend path")
def secret_get(key: str, backend: str, path: str):
    """Retrieve a secret from the vault."""
    from agentix.security.secrets import SecretsVault, SecretNotFoundError
    vault = SecretsVault.from_config({"backend": backend, "path": path})
    try:
        click.echo(vault.get(key))
    except SecretNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)


@secret.command("list")
@click.option("--backend", default="file", help="Backend: env | file | vault | aws_sm")
@click.option("--path", default="data/secrets.json", help="File backend path")
def secret_list(backend: str, path: str):
    """List secret keys in the vault."""
    from agentix.security.secrets import SecretsVault
    vault = SecretsVault.from_config({"backend": backend, "path": path})
    for k in vault.list_keys():
        click.echo(k)


# ---------------------------------------------------------------------------
# skillhub
# ---------------------------------------------------------------------------

@cli.group()
def hub():
    """SkillHub management commands."""


@hub.command("install")
@click.argument("source")
@click.option("--db", default="data/agentix.db", help="Database path")
def hub_install(source: str, db: str):
    """Install a skill from a YAML file or Git URL."""
    from agentix.skills.skillhub import SkillHub
    from agentix.storage.state_store import StateStore
    store = StateStore(db)
    hub = SkillHub(store)
    if source.startswith("git@") or source.startswith("https://") and source.endswith(".git"):
        record = hub.install_from_git(source)
    else:
        record = hub.install_from_yaml(source)
    click.echo(f"Installed: {record['name']} v{record['version']} ({record['source']})")


@hub.command("verify")
@click.argument("skill_name")
@click.option("--db", default="data/agentix.db", help="Database path")
def hub_verify(skill_name: str, db: str):
    """Verify integrity of an installed skill."""
    from agentix.skills.skillhub import SkillHub
    from agentix.storage.state_store import StateStore
    store = StateStore(db)
    hub = SkillHub(store)
    ok = hub.verify(skill_name)
    click.echo(f"{'✓ Integrity OK' if ok else '✗ Integrity FAILED'}: {skill_name}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    cli()
