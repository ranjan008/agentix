"""
Enterprise Identity Provider — OAuth2 / OIDC / SAML 2.0 / Service Accounts.

Supports:
  - OAuth2 Authorization Code + PKCE  (Okta, Auth0, Azure AD, Google Workspace)
  - SAML 2.0 SP (enterprise SSO via metadata URL)
  - JWT introspection (validate tokens issued by external IdPs)
  - Service account API keys (machine-to-machine, scoped + expiring)
  - Client credentials grant (M2M OAuth2 flow)

Configuration (watchdog.yaml):
  identity:
    provider: "oidc"                     # oidc | saml | local
    oidc_issuer: "https://company.okta.com/oauth2/default"
    oidc_client_id: "${OIDC_CLIENT_ID}"
    oidc_client_secret: "${OIDC_CLIENT_SECRET}"
    oidc_audience: "agentix"
    saml_metadata_url: "https://company.okta.com/app/.../metadata"
    saml_sp_entity_id: "https://agents.company.com"
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field

import httpx
import jwt as pyjwt

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Identity claims (normalised across all providers)
# ---------------------------------------------------------------------------

@dataclass
class IdentityClaims:
    identity_id: str
    email: str
    name: str
    roles: list[str]
    tenant_id: str
    provider: str
    raw_claims: dict = field(default_factory=dict)
    expires_at: float = 0.0

    @property
    def is_expired(self) -> bool:
        return self.expires_at > 0 and time.time() > self.expires_at


# ---------------------------------------------------------------------------
# OIDC / OAuth2 provider
# ---------------------------------------------------------------------------

class OIDCProvider:
    """
    Validates tokens against an OIDC-compliant identity provider.
    Supports JWT validation with JWKS key rotation.
    """

    def __init__(
        self,
        issuer: str,
        client_id: str,
        client_secret: str = "",
        audience: str = "",
        roles_claim: str = "roles",
        tenant_claim: str = "tenant_id",
        jwks_cache_ttl: int = 3600,
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.audience = audience or client_id
        self.roles_claim = roles_claim
        self.tenant_claim = tenant_claim
        self._jwks: dict | None = None
        self._jwks_fetched_at: float = 0.0
        self._jwks_cache_ttl = jwks_cache_ttl
        self._oidc_config: dict | None = None

    def _fetch_oidc_config(self) -> dict:
        if not self._oidc_config:
            url = f"{self.issuer}/.well-known/openid-configuration"
            resp = httpx.get(url, timeout=10)
            resp.raise_for_status()
            self._oidc_config = resp.json()
        return self._oidc_config

    def _fetch_jwks(self) -> dict:
        now = time.time()
        if self._jwks and (now - self._jwks_fetched_at) < self._jwks_cache_ttl:
            return self._jwks
        cfg = self._fetch_oidc_config()
        resp = httpx.get(cfg["jwks_uri"], timeout=10)
        resp.raise_for_status()
        self._jwks = resp.json()
        self._jwks_fetched_at = now
        return self._jwks

    def validate_token(self, token: str) -> IdentityClaims:
        """Validate a JWT access token. Returns normalised IdentityClaims."""
        try:
            jwks = self._fetch_jwks()
            header = pyjwt.get_unverified_header(token)
            # Find the matching key
            key = None
            for k in jwks.get("keys", []):
                if k.get("kid") == header.get("kid"):
                    from jwt.algorithms import RSAAlgorithm
                    key = RSAAlgorithm.from_jwk(json.dumps(k))
                    break
            if key is None:
                raise ValueError("No matching JWKS key found")

            claims = pyjwt.decode(
                token, key,  # type: ignore[arg-type]
                algorithms=["RS256", "RS384", "RS512"],
                audience=self.audience,
                issuer=self.issuer,
            )
        except pyjwt.ExpiredSignatureError:
            raise AuthenticationError("Token expired")
        except pyjwt.InvalidTokenError as e:
            raise AuthenticationError(f"Invalid token: {e}")

        return IdentityClaims(
            identity_id=claims.get("sub", ""),
            email=claims.get("email", ""),
            name=claims.get("name", claims.get("preferred_username", "")),
            roles=claims.get(self.roles_claim, ["end-user"]),
            tenant_id=claims.get(self.tenant_claim, "default"),
            provider="oidc",
            raw_claims=claims,
            expires_at=float(claims.get("exp", 0)),
        )

    def get_authorization_url(self, redirect_uri: str, state: str = "", scopes: list[str] | None = None) -> tuple[str, str]:
        """Generate OAuth2 authorization URL with PKCE. Returns (url, code_verifier)."""
        cfg = self._fetch_oidc_config()
        code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).rstrip(b"=").decode()

        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes or ["openid", "email", "profile"]),
            "state": state or secrets.token_urlsafe(16),
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        url = cfg["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)
        return url, code_verifier

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str) -> dict:
        """Exchange authorization code for tokens."""
        cfg = self._fetch_oidc_config()
        resp = httpx.post(
            cfg["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code_verifier": code_verifier,
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def client_credentials(self, scopes: list[str] | None = None) -> dict:
        """Machine-to-machine token via client_credentials grant."""
        cfg = self._fetch_oidc_config()
        resp = httpx.post(
            cfg["token_endpoint"],
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": " ".join(scopes or ["agentix:trigger"]),
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# SAML 2.0 SP
# ---------------------------------------------------------------------------

class SAMLProvider:
    """
    SAML 2.0 Service Provider.
    Requires: pip install python3-saml
    """

    def __init__(self, metadata_url: str, sp_entity_id: str, acs_url: str) -> None:
        self.metadata_url = metadata_url
        self.sp_entity_id = sp_entity_id
        self.acs_url = acs_url
        self._idp_metadata: str | None = None

    def _get_idp_metadata(self) -> str:
        if not self._idp_metadata:
            resp = httpx.get(self.metadata_url, timeout=15)
            resp.raise_for_status()
            self._idp_metadata = resp.text
        return self._idp_metadata

    def _saml_settings(self) -> dict:
        return {
            "strict": True,
            "debug": False,
            "sp": {
                "entityId": self.sp_entity_id,
                "assertionConsumerService": {
                    "url": self.acs_url,
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
                },
            },
            "idp": {"metadataUrl": self.metadata_url},
        }

    def get_login_url(self, relay_state: str = "") -> str:
        """Generate SAML AuthnRequest redirect URL."""
        try:
            from onelogin.saml2.auth import OneLogin_Saml2_Auth
            auth = OneLogin_Saml2_Auth({}, self._saml_settings())
            return auth.login(return_to=relay_state)
        except ImportError:
            raise ImportError("SAML requires 'python3-saml': pip install python3-saml")

    def process_response(self, post_data: dict) -> IdentityClaims:
        """Process SAML Response from IdP POST. Returns IdentityClaims."""
        try:
            from onelogin.saml2.auth import OneLogin_Saml2_Auth
            auth = OneLogin_Saml2_Auth(post_data, self._saml_settings())
            auth.process_response()
            if not auth.is_authenticated():
                raise AuthenticationError(f"SAML auth failed: {auth.get_last_error_reason()}")

            attrs = auth.get_attributes()
            return IdentityClaims(
                identity_id=auth.get_nameid(),
                email=_first(attrs.get("email", attrs.get("http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress", [""]))),
                name=_first(attrs.get("displayName", [""])),
                roles=attrs.get("roles", attrs.get("groups", ["end-user"])),
                tenant_id=_first(attrs.get("tenantId", ["default"])),
                provider="saml",
                raw_claims=attrs,
            )
        except ImportError:
            raise ImportError("SAML requires 'python3-saml': pip install python3-saml")


# ---------------------------------------------------------------------------
# Service Account API Keys
# ---------------------------------------------------------------------------

class ServiceAccountManager:
    """
    Manages machine-to-machine API keys with scoped permissions and TTL.
    Keys are stored hashed (SHA-256) in the database — never stored in plain text.
    """

    def __init__(self, db_path: str = "data/agentix.db") -> None:
        import sqlite3
        from pathlib import Path
        self.db_path = Path(db_path)
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS service_accounts (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL UNIQUE,
                key_hash    TEXT NOT NULL,
                roles       TEXT NOT NULL DEFAULT '["operator"]',
                tenant_id   TEXT NOT NULL DEFAULT 'default',
                scopes      TEXT NOT NULL DEFAULT '[]',
                expires_at  REAL,
                created_at  REAL NOT NULL,
                last_used_at REAL,
                revoked     INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

    def create(
        self,
        name: str,
        roles: list[str],
        tenant_id: str = "default",
        scopes: list[str] | None = None,
        ttl_days: int | None = None,
    ) -> tuple[str, str]:
        """
        Create a new service account key.
        Returns (account_id, plain_api_key) — plain key shown ONCE, never stored.
        """
        import sqlite3
        import uuid
        plain_key = f"sk-agentix-{secrets.token_urlsafe(32)}"
        key_hash = hashlib.sha256(plain_key.encode()).hexdigest()
        account_id = f"sa_{uuid.uuid4().hex[:12]}"
        expires_at = time.time() + ttl_days * 86400 if ttl_days else None

        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            """INSERT INTO service_accounts
               (id, name, key_hash, roles, tenant_id, scopes, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, name, key_hash, json.dumps(roles),
             tenant_id, json.dumps(scopes or []), expires_at, time.time()),
        )
        conn.commit()
        conn.close()
        logger.info("Service account created: %s (%s)", name, account_id)
        return account_id, plain_key

    def validate(self, api_key: str) -> IdentityClaims | None:
        """Validate an API key. Returns IdentityClaims or None if invalid."""
        import sqlite3
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM service_accounts WHERE key_hash=? AND revoked=0",
            (key_hash,),
        ).fetchone()

        if not row:
            conn.close()
            return None

        if row["expires_at"] and time.time() > row["expires_at"]:
            conn.close()
            return None

        conn.execute(
            "UPDATE service_accounts SET last_used_at=? WHERE id=?",
            (time.time(), row["id"]),
        )
        conn.commit()
        conn.close()

        return IdentityClaims(
            identity_id=row["id"],
            email=f"{row['name']}@service",
            name=row["name"],
            roles=json.loads(row["roles"]),
            tenant_id=row["tenant_id"],
            provider="service_account",
        )

    def revoke(self, name: str) -> None:
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("UPDATE service_accounts SET revoked=1 WHERE name=?", (name,))
        conn.commit()
        conn.close()
        logger.info("Service account revoked: %s", name)

    def list_accounts(self, tenant_id: str | None = None) -> list[dict]:
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        if tenant_id:
            rows = conn.execute(
                "SELECT id, name, roles, tenant_id, expires_at, revoked, created_at "
                "FROM service_accounts WHERE tenant_id=? ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, roles, tenant_id, expires_at, revoked, created_at "
                "FROM service_accounts ORDER BY created_at DESC"
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Unified identity resolver
# ---------------------------------------------------------------------------

class IdentityResolver:
    """
    Resolves an incoming credential (Bearer token or API key) to IdentityClaims.
    Tries OIDC → Service Account → local JWT in order.
    """

    def __init__(
        self,
        oidc: OIDCProvider | None = None,
        saml: SAMLProvider | None = None,
        service_accounts: ServiceAccountManager | None = None,
        local_jwt_secret: str = "",
    ) -> None:
        self._oidc = oidc
        self._saml = saml
        self._sa = service_accounts
        self._local_secret = local_jwt_secret

    def resolve(self, authorization: str) -> IdentityClaims:
        if not authorization:
            raise AuthenticationError("Missing Authorization header")

        parts = authorization.split()
        if len(parts) != 2:
            raise AuthenticationError("Malformed Authorization header")

        scheme, credential = parts[0].lower(), parts[1]

        # Service account API key
        if scheme == "bearer" and credential.startswith("sk-agentix-") and self._sa:
            claims = self._sa.validate(credential)
            if claims:
                return claims
            raise AuthenticationError("Invalid or expired API key")

        # OIDC JWT
        if scheme == "bearer" and self._oidc:
            try:
                return self._oidc.validate_token(credential)
            except AuthenticationError:
                pass  # Fall through to local JWT

        # Local JWT (Phase 1/2 fallback)
        if scheme == "bearer" and self._local_secret:
            try:
                claims = pyjwt.decode(credential, self._local_secret, algorithms=["HS256"])
                return IdentityClaims(
                    identity_id=claims.get("sub", "unknown"),
                    email=claims.get("email", ""),
                    name=claims.get("name", ""),
                    roles=claims.get("roles", ["end-user"]),
                    tenant_id=claims.get("tenant_id", "default"),
                    provider="local_jwt",
                    raw_claims=claims,
                    expires_at=float(claims.get("exp", 0)),
                )
            except pyjwt.InvalidTokenError as e:
                raise AuthenticationError(f"Invalid token: {e}")

        raise AuthenticationError("No valid authentication method resolved credential")

    @classmethod
    def from_config(cls, cfg: dict, db_path: str = "data/agentix.db") -> "IdentityResolver":
        identity_cfg = cfg.get("identity", {})
        provider_type = identity_cfg.get("provider", "local")

        oidc = None
        if provider_type == "oidc":
            import os
            oidc = OIDCProvider(
                issuer=identity_cfg.get("oidc_issuer", ""),
                client_id=os.environ.get("OIDC_CLIENT_ID", identity_cfg.get("oidc_client_id", "")),
                client_secret=os.environ.get("OIDC_CLIENT_SECRET", identity_cfg.get("oidc_client_secret", "")),
                audience=identity_cfg.get("oidc_audience", "agentix"),
                roles_claim=identity_cfg.get("roles_claim", "roles"),
                tenant_claim=identity_cfg.get("tenant_claim", "tenant_id"),
            )

        saml = None
        if provider_type == "saml":
            saml = SAMLProvider(
                metadata_url=identity_cfg.get("saml_metadata_url", ""),
                sp_entity_id=identity_cfg.get("saml_sp_entity_id", ""),
                acs_url=identity_cfg.get("saml_acs_url", ""),
            )

        sa_manager = ServiceAccountManager(db_path=db_path)
        local_secret = os.environ.get(
            cfg.get("security", {}).get("jwt_secret_env", "JWT_SECRET"), ""
        )

        return cls(oidc=oidc, saml=saml, service_accounts=sa_manager, local_jwt_secret=local_secret)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class AuthenticationError(Exception):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first(lst: list, default: str = "") -> str:
    return lst[0] if lst else default
