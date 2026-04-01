"""
HTTP Webhook channel adapter.
Listens on a configurable port/path and converts POST requests into TriggerEnvelopes.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Callable, Awaitable

from aiohttp import web

from agentix.watchdog import trigger_normalizer as tn
from agentix.watchdog.auth import AuthError, RateLimitError, RateLimiter, extract_bearer, validate_jwt

logger = logging.getLogger(__name__)


class HttpWebhookChannel:
    """
    Async HTTP channel that accepts:
      POST /trigger          — authenticated via JWT Bearer
      POST /trigger/{agent}  — agent_id from URL path
      GET  /healthz          — liveness probe
    """

    def __init__(
        self,
        port: int = 8080,
        path: str = "/trigger",
        jwt_secret: str = "",
        enforce_auth: bool = True,
        hmac_secret: str = "",
        on_trigger: Callable[[dict], Awaitable[None]] | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.port = port
        self.path = path.rstrip("/")
        self.jwt_secret = jwt_secret
        self.enforce_auth = enforce_auth
        self.hmac_secret = hmac_secret
        self.on_trigger = on_trigger
        self.rate_limiter = rate_limiter or RateLimiter()
        self._app = web.Application()
        self._runner: web.AppRunner | None = None
        self._setup_routes()

    def _setup_routes(self) -> None:
        self._app.router.add_get("/healthz", self._healthz)
        self._app.router.add_post(self.path, self._handle)
        self._app.router.add_post(self.path + "/{agent_id}", self._handle)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _healthz(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def _handle(self, request: web.Request) -> web.Response:
        agent_id = request.match_info.get("agent_id", "")

        # --- HMAC verification (optional, for signed webhooks) ---
        if self.hmac_secret:
            sig = request.headers.get("x-hub-signature-256", "")
            body_bytes = await request.read()
            expected = "sha256=" + hmac.new(
                self.hmac_secret.encode(), body_bytes, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(sig, expected):
                return web.json_response({"error": "invalid signature"}, status=401)
            body = json.loads(body_bytes)
        else:
            body = await request.json()

        # --- JWT auth ---
        identity: dict | None = None
        if self.enforce_auth and self.jwt_secret:
            try:
                token = extract_bearer(request.headers.get("Authorization", ""))
                claims = validate_jwt(token, self.jwt_secret)
                identity = {
                    "identity_id": claims.get("sub", "unknown"),
                    "roles": claims.get("roles", ["end-user"]),
                    "tenant_id": claims.get("tenant_id", "default"),
                }
            except AuthError as e:
                return web.json_response({"error": str(e)}, status=401)

        # --- Rate limit ---
        id_key = (identity or {}).get("identity_id", request.remote or "anon")
        try:
            self.rate_limiter.check(id_key)
        except RateLimitError as e:
            return web.json_response({"error": str(e)}, status=429)

        # --- Normalise ---
        headers = {
            ":method": request.method,
            ":path": str(request.rel_url),
            "x-forwarded-for": request.headers.get("X-Forwarded-For", request.remote or ""),
            "x-identity-id": (identity or {}).get("identity_id", "anonymous"),
            "x-roles": ",".join((identity or {}).get("roles", ["end-user"])),
            "x-tenant-id": (identity or {}).get("tenant_id", "default"),
        }
        if not agent_id:
            agent_id = body.get("agent_id", "")

        if not agent_id:
            return web.json_response({"error": "agent_id required"}, status=400)

        envelope = tn.from_http(body, headers, agent_id, identity)
        logger.info("HTTP trigger received: agent=%s trigger=%s", agent_id, envelope["id"])

        if self.on_trigger:
            await self.on_trigger(envelope)

        return web.json_response({"trigger_id": envelope["id"], "status": "queued"}, status=202)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()
        logger.info("HTTP webhook listening on 0.0.0.0:%d%s", self.port, self.path)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
