"""
RBAC Gateway — sits between channel adapters and agent spawner.
Called once per trigger envelope before spawn.

Checks:
  1. trigger:invoke — can this identity trigger this agent?
  2. Tenant scope — agent must belong to caller's tenant (or be shared)
  3. Audit the decision (allow or deny)
"""
from __future__ import annotations

import logging

from agentix.security.rbac import PolicyContext, RBACEngine
from agentix.security.audit import AuditLog

logger = logging.getLogger(__name__)


class RBACGateway:
    def __init__(self, rbac: RBACEngine, audit: AuditLog) -> None:
        self._rbac = rbac
        self._audit = audit

    def check_trigger(self, envelope: dict, agent_spec: dict) -> bool:
        """
        Returns True if the trigger is allowed.
        Logs an audit entry for both allow and deny.
        """
        caller = envelope["caller"]
        ctx = PolicyContext(
            identity_id=caller["identity_id"],
            roles=caller.get("roles", ["end-user"]),
            tenant_id=caller.get("tenant_id", "default"),
            resource_tenant_id=agent_spec.get("metadata", {}).get("tenant_id", "default"),
            resource_agent_id=envelope["agent_id"],
            channel=envelope["channel"],
        )

        allowed = self._rbac.can(ctx, "trigger:invoke")

        self._audit.record(
            event_type="trigger.allow" if allowed else "trigger.deny",
            trigger_id=envelope["id"],
            agent_id=envelope["agent_id"],
            actor=caller["identity_id"],
            tenant_id=caller.get("tenant_id", "default"),
            detail={
                "roles": caller.get("roles", []),
                "channel": envelope["channel"],
                "resource_tenant": ctx.resource_tenant_id,
            },
        )

        if not allowed:
            logger.warning(
                "Trigger denied by RBAC: identity=%s roles=%s agent=%s channel=%s",
                caller["identity_id"], caller.get("roles"), envelope["agent_id"], envelope["channel"],
            )

        return allowed

    def check_skill_activation(
        self,
        envelope: dict,
        skill_name: str,
        permissions_needed: list[str],
    ) -> bool:
        """Check whether this caller can activate a given skill."""
        from agentix.security.skill_rbac import SkillRBACEnforcer, SkillPermissionSpec
        caller = envelope["caller"]
        ctx = PolicyContext(
            identity_id=caller["identity_id"],
            roles=caller.get("roles", ["end-user"]),
            tenant_id=caller.get("tenant_id", "default"),
            resource_tenant_id=caller.get("tenant_id", "default"),
            skill_name=skill_name,
        )
        enforcer = SkillRBACEnforcer(self._rbac)
        spec = SkillPermissionSpec(
            name=skill_name,
            permissions_needed=permissions_needed,
        )
        enforcer.register_skill(spec)
        allowed = enforcer.can_activate(ctx, skill_name)

        self._audit.record(
            event_type="skill.allow" if allowed else "skill.deny",
            trigger_id=envelope["id"],
            agent_id=envelope["agent_id"],
            actor=caller["identity_id"],
            tenant_id=caller.get("tenant_id", "default"),
            detail={"skill": skill_name, "permissions_needed": permissions_needed},
        )
        return allowed

    def check_tool_call(self, envelope: dict, tool_name: str) -> bool:
        """Check whether this caller can invoke a tool."""
        caller = envelope["caller"]
        ctx = PolicyContext(
            identity_id=caller["identity_id"],
            roles=caller.get("roles", ["end-user"]),
            tenant_id=caller.get("tenant_id", "default"),
            resource_tenant_id=caller.get("tenant_id", "default"),
            tool_name=tool_name,
        )
        allowed = self._rbac.can(ctx, "tool:call")
        if not allowed:
            self._audit.record(
                event_type="tool.deny",
                trigger_id=envelope["id"],
                agent_id=envelope["agent_id"],
                actor=caller["identity_id"],
                tenant_id=caller.get("tenant_id", "default"),
                detail={"tool": tool_name},
            )
        return allowed
