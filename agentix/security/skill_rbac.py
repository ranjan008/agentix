"""
Per-skill RBAC enforcement.

Each skill declares:
  - permissions_needed: list of permission strings the agent role must hold
  - secrets_required: secrets the skill needs from the vault
  - data_scope: "tenant" | "user" | "global"

Before activating a skill inside an agent, the engine checks:
  1. The agent's role holds all permissions_needed for the skill
  2. The caller's tenant matches resource tenant (unless platform-admin)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from agentix.security.rbac import EFFECTIVE_PERMISSIONS, PolicyContext, RBACEngine

logger = logging.getLogger(__name__)


@dataclass
class SkillPermissionSpec:
    """Parsed from a skill's spec.rbac section."""
    name: str
    permissions_needed: list[str] = field(default_factory=list)
    data_scope: str = "tenant"          # tenant | user | global
    secrets_required: list[str] = field(default_factory=list)

    @classmethod
    def from_spec(cls, skill_spec: dict) -> "SkillPermissionSpec":
        rbac = skill_spec.get("spec", {}).get("rbac", {})
        return cls(
            name=skill_spec.get("metadata", {}).get("name", "unknown"),
            permissions_needed=rbac.get("permissions_needed", []),
            data_scope=rbac.get("data_scope", "tenant"),
            secrets_required=[
                s.get("vault_path", s.get("name", ""))
                for s in skill_spec.get("spec", {}).get("secrets_required", [])
            ],
        )


class SkillRBACEnforcer:
    """
    Checks whether an agent (identified by its roles + tenant) is allowed
    to activate a given skill.
    """

    def __init__(self, rbac_engine: RBACEngine) -> None:
        self._rbac = rbac_engine
        # skill_name -> SkillPermissionSpec (populated at load time)
        self._skill_specs: dict[str, SkillPermissionSpec] = {}

    def register_skill(self, spec: SkillPermissionSpec) -> None:
        self._skill_specs[spec.name] = spec

    def can_activate(self, ctx: PolicyContext, skill_name: str) -> bool:
        """
        Returns True if the caller's roles satisfy the skill's permission requirements.
        """
        # First: general skill:activate permission
        if not self._rbac.can(ctx, "skill:activate"):
            logger.warning(
                "Skill activation denied (no skill:activate): identity=%s skill=%s",
                ctx.identity_id, skill_name,
            )
            return False

        # Second: skill-specific permissions
        spec = self._skill_specs.get(skill_name)
        if spec:
            for perm in spec.permissions_needed:
                has_perm = any(
                    perm in EFFECTIVE_PERMISSIONS.get(role, set())
                    for role in ctx.roles
                )
                if not has_perm:
                    logger.warning(
                        "Skill activation denied (missing %s): identity=%s skill=%s",
                        perm, ctx.identity_id, skill_name,
                    )
                    return False

            # Data scope check
            if spec.data_scope == "tenant":
                if ctx.tenant_id != ctx.resource_tenant_id and "platform-admin" not in ctx.roles:
                    logger.warning(
                        "Skill activation denied (tenant scope mismatch): identity=%s skill=%s",
                        ctx.identity_id, skill_name,
                    )
                    return False

        return True

    def assert_can_activate(self, ctx: PolicyContext, skill_name: str) -> None:
        if not self.can_activate(ctx, skill_name):
            raise PermissionError(
                f"Skill activation denied: identity='{ctx.identity_id}' "
                f"roles={ctx.roles} skill='{skill_name}'"
            )

    def get_required_secrets(self, skill_name: str) -> list[str]:
        spec = self._skill_specs.get(skill_name)
        return spec.secrets_required if spec else []
