"""
RBAC Engine — OPA-inspired policy evaluator.

Role hierarchy (least → most privileged):
  end-user < operator < agent-author < tenant-admin < platform-admin

Each role inherits all permissions of roles below it.

Policy evaluation:
  can(identity, action, resource) -> bool

Actions:
  trigger:invoke      — trigger an agent
  skill:activate      — activate a skill inside an agent
  tool:call           — call a tool inside an agent
  agent:register      — register / update an agent definition
  agent:list          — list agents in scope
  skill:install       — install a skill from SkillHub
  secret:read         — read a secret from the vault
  audit:read          — read the audit log
  tenant:manage       — manage tenant settings and users
  platform:admin      — global platform administration
"""
from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Role definitions
# ---------------------------------------------------------------------------

Role = Literal["end-user", "operator", "agent-author", "tenant-admin", "platform-admin"]

# Each role's own permissions (not including inherited ones)
_ROLE_PERMISSIONS: dict[str, set[str]] = {
    "end-user": {
        "trigger:invoke",      # own scope only
        "agent:list",          # own scope
    },
    "operator": {
        "trigger:invoke",
        "agent:list",
        "skill:activate",
        "tool:call",
        "audit:read",          # team scope
    },
    "agent-author": {
        "trigger:invoke",
        "agent:list",
        "agent:register",
        "skill:install",
        "skill:activate",
        "tool:call",
        "audit:read",
        "secret:read",
    },
    "tenant-admin": {
        "trigger:invoke",
        "agent:list",
        "agent:register",
        "skill:install",
        "skill:activate",
        "tool:call",
        "audit:read",
        "secret:read",
        "tenant:manage",
    },
    "platform-admin": {
        "trigger:invoke",
        "agent:list",
        "agent:register",
        "skill:install",
        "skill:activate",
        "tool:call",
        "audit:read",
        "secret:read",
        "tenant:manage",
        "platform:admin",
    },
}

# Role hierarchy — roles inherit from all roles listed here
_ROLE_HIERARCHY: dict[str, list[str]] = {
    "end-user": [],
    "operator": ["end-user"],
    "agent-author": ["operator"],
    "tenant-admin": ["agent-author"],
    "platform-admin": ["tenant-admin"],
}


def _effective_permissions(role: str) -> set[str]:
    """Return the full permission set for a role (including inherited)."""
    perms = set(_ROLE_PERMISSIONS.get(role, set()))
    for parent in _ROLE_HIERARCHY.get(role, []):
        perms |= _effective_permissions(parent)
    return perms


# Pre-compute effective permissions per role
EFFECTIVE_PERMISSIONS: dict[str, set[str]] = {
    r: _effective_permissions(r) for r in _ROLE_PERMISSIONS
}


# ---------------------------------------------------------------------------
# Policy context
# ---------------------------------------------------------------------------

@dataclass
class PolicyContext:
    """Carries the identity and resource context for a single RBAC check."""
    identity_id: str
    roles: list[str]
    tenant_id: str
    # Resource attributes
    resource_tenant_id: str = "default"
    resource_agent_id: str = ""
    channel: str = ""
    skill_name: str = ""
    tool_name: str = ""
    # Optional: extra attributes for fine-grained policies
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Custom policy rules (loaded from YAML / code)
# ---------------------------------------------------------------------------

@dataclass
class PolicyRule:
    """
    A single allow/deny rule.
    Fields support glob patterns (e.g. tenant_id="acme-*").
    """
    effect: Literal["allow", "deny"] = "allow"
    roles: list[str] = field(default_factory=lambda: ["*"])
    actions: list[str] = field(default_factory=lambda: ["*"])
    agent_ids: list[str] = field(default_factory=lambda: ["*"])
    tenant_ids: list[str] = field(default_factory=lambda: ["*"])
    channels: list[str] = field(default_factory=lambda: ["*"])

    def matches(self, ctx: PolicyContext, action: str) -> bool:
        role_match = any(
            any(fnmatch.fnmatch(r, pat) for r in ctx.roles)
            for pat in self.roles
        )
        action_match = any(fnmatch.fnmatch(action, pat) for pat in self.actions)
        agent_match = any(fnmatch.fnmatch(ctx.resource_agent_id, pat) for pat in self.agent_ids)
        tenant_match = any(fnmatch.fnmatch(ctx.tenant_id, pat) for pat in self.tenant_ids)
        channel_match = (
            not ctx.channel
            or any(fnmatch.fnmatch(ctx.channel, pat) for pat in self.channels)
        )
        return role_match and action_match and agent_match and tenant_match and channel_match


# ---------------------------------------------------------------------------
# RBAC Engine
# ---------------------------------------------------------------------------

class RBACEngine:
    """
    Evaluates access control decisions.

    Decision flow:
      1. Explicit deny rules (custom rules with effect=deny) → DENY
      2. Built-in role permissions                          → check
      3. Data scope check (tenant isolation)                → check
      4. Custom allow rules                                 → check
      5. Default → DENY
    """

    def __init__(self, custom_rules: list[PolicyRule] | None = None) -> None:
        self._deny_rules: list[PolicyRule] = []
        self._allow_rules: list[PolicyRule] = []
        for rule in custom_rules or []:
            if rule.effect == "deny":
                self._deny_rules.append(rule)
            else:
                self._allow_rules.append(rule)

    def add_rule(self, rule: PolicyRule) -> None:
        if rule.effect == "deny":
            self._deny_rules.append(rule)
        else:
            self._allow_rules.append(rule)

    def can(self, ctx: PolicyContext, action: str) -> bool:
        """Return True if the identity is allowed to perform action."""

        # 1. Explicit deny always wins
        for rule in self._deny_rules:
            if rule.matches(ctx, action):
                logger.debug("RBAC DENY (explicit rule): %s %s", ctx.identity_id, action)
                return False

        # 2. Check role-based permissions
        role_allows = any(
            action in EFFECTIVE_PERMISSIONS.get(role, set())
            for role in ctx.roles
        )

        # 3. Data scope: non-platform-admin can only act on their own tenant
        scope_ok = (
            "platform-admin" in ctx.roles
            or ctx.tenant_id == ctx.resource_tenant_id
            or ctx.resource_tenant_id == "default"
        )

        if role_allows and scope_ok:
            logger.debug("RBAC ALLOW (role): %s %s", ctx.identity_id, action)
            return True

        # 4. Custom allow rules (for fine-grained overrides)
        for rule in self._allow_rules:
            if rule.matches(ctx, action):
                logger.debug("RBAC ALLOW (custom rule): %s %s", ctx.identity_id, action)
                return True

        logger.debug("RBAC DENY (default): %s %s", ctx.identity_id, action)
        return False

    def assert_can(self, ctx: PolicyContext, action: str) -> None:
        """Like can() but raises PermissionError on denial."""
        if not self.can(ctx, action):
            raise PermissionError(
                f"Access denied: identity='{ctx.identity_id}' "
                f"roles={ctx.roles} action='{action}' "
                f"resource_tenant='{ctx.resource_tenant_id}'"
            )

    # ------------------------------------------------------------------
    # Convenience constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, policy_path: str) -> "RBACEngine":
        """Load custom policy rules from a YAML file."""
        import yaml
        from pathlib import Path
        path = Path(policy_path)
        if not path.exists():
            return cls()
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        rules = []
        for r in data.get("rules", []):
            rules.append(PolicyRule(
                effect=r.get("effect", "allow"),
                roles=r.get("roles", ["*"]),
                actions=r.get("actions", ["*"]),
                agent_ids=r.get("agent_ids", ["*"]),
                tenant_ids=r.get("tenant_ids", ["*"]),
                channels=r.get("channels", ["*"]),
            ))
        return cls(rules)

    @classmethod
    def permissive(cls) -> "RBACEngine":
        """A fully permissive engine — for dev/lite tier when enforce_rbac=false."""
        engine = cls()
        engine.add_rule(PolicyRule(effect="allow", roles=["*"], actions=["*"]))
        return engine
