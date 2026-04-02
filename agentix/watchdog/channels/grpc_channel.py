"""
gRPC channel adapter — streaming trigger RPC.

Proto definition (generated stubs expected at agentix/proto/trigger_pb2*.py):

  syntax = "proto3";
  package agentix;

  service TriggerService {
    // Client sends a stream of TriggerRequests; server streams TriggerResponses.
    rpc StreamTriggers (stream TriggerRequest) returns (stream TriggerResponse);
    // Unary convenience call
    rpc SendTrigger (TriggerRequest) returns (TriggerResponse);
  }

  message TriggerRequest {
    string event_type  = 1;
    string payload_json = 2;   // JSON-encoded payload
    string identity_json = 3;  // JSON-encoded identity dict
    string agent_id    = 4;    // optional routing hint
  }

  message TriggerResponse {
    string trigger_id = 1;
    string status     = 2;
    string message    = 3;
  }

If proto stubs are not present the channel logs a warning and skips.

Config keys / env:
  GRPC_LISTEN_HOST (default 0.0.0.0)
  GRPC_LISTEN_PORT (default 50051)
  GRPC_MAX_WORKERS (default 10)
  GRPC_USE_TLS     (default false)
  GRPC_TLS_CERT    — path to PEM cert  (required when USE_TLS=true)
  GRPC_TLS_KEY     — path to PEM key   (required when USE_TLS=true)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Callable, Awaitable

from aiohttp import web

from agentix.watchdog.trigger_normalizer import TriggerEnvelope

log = logging.getLogger(__name__)


class GRPCChannel:
    """gRPC streaming inbound channel."""

    def __init__(
        self,
        cfg: dict,
        on_trigger: Callable[[TriggerEnvelope], Awaitable[None]],
        app: web.Application,
    ) -> None:
        self._host = cfg.get("grpc_listen_host") or os.environ.get("GRPC_LISTEN_HOST", "0.0.0.0")
        self._port = int(cfg.get("grpc_listen_port") or os.environ.get("GRPC_LISTEN_PORT", "50051"))
        self._max_workers = int(cfg.get("grpc_max_workers") or os.environ.get("GRPC_MAX_WORKERS", "10"))
        self._use_tls = str(cfg.get("grpc_use_tls") or os.environ.get("GRPC_USE_TLS", "false")).lower() == "true"
        self._tls_cert = cfg.get("grpc_tls_cert") or os.environ.get("GRPC_TLS_CERT", "")
        self._tls_key = cfg.get("grpc_tls_key") or os.environ.get("GRPC_TLS_KEY", "")
        self._on_trigger = on_trigger
        self._server = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        try:
            import grpc
            import grpc.aio
        except ImportError:
            log.error("GRPCChannel: grpcio not installed. Run: pip install grpcio")
            return

        try:
            from agentix.proto import trigger_pb2, trigger_pb2_grpc
        except ImportError:
            log.warning("GRPCChannel: proto stubs not found — generate with `python -m grpc_tools.protoc`. Channel skipped.")
            return

        self._loop = asyncio.get_event_loop()
        servicer = _TriggerServicer(self._on_trigger, self._loop)

        server = grpc.aio.server()
        trigger_pb2_grpc.add_TriggerServiceServicer_to_server(servicer, server)

        if self._use_tls and self._tls_cert and self._tls_key:
            with open(self._tls_cert, "rb") as f:
                cert = f.read()
            with open(self._tls_key, "rb") as f:
                key = f.read()
            creds = grpc.ssl_server_credentials([(key, cert)])
            server.add_secure_port(f"{self._host}:{self._port}", creds)
            log.info("GRPCChannel TLS listening on %s:%d", self._host, self._port)
        else:
            server.add_insecure_port(f"{self._host}:{self._port}")
            log.info("GRPCChannel (insecure) listening on %s:%d", self._host, self._port)

        await server.start()
        self._server = server

    async def stop(self) -> None:
        if self._server:
            await self._server.stop(grace=5)
            self._server = None


# ---------------------------------------------------------------------------
# gRPC servicer
# ---------------------------------------------------------------------------

class _TriggerServicer:
    """Implements TriggerService gRPC methods."""

    def __init__(self, on_trigger: Callable, loop: asyncio.AbstractEventLoop) -> None:
        self._on_trigger = on_trigger
        self._loop = loop

    async def SendTrigger(self, request, context):
        from agentix.proto import trigger_pb2
        envelope = _normalise_request(request)
        await self._on_trigger(envelope)
        return trigger_pb2.TriggerResponse(
            trigger_id=envelope.trigger_id,
            status="accepted",
            message="Trigger enqueued",
        )

    async def StreamTriggers(self, request_iterator, context):
        from agentix.proto import trigger_pb2
        async for request in request_iterator:
            envelope = _normalise_request(request)
            await self._on_trigger(envelope)
            yield trigger_pb2.TriggerResponse(
                trigger_id=envelope.trigger_id,
                status="accepted",
                message="Trigger enqueued",
            )


# ---------------------------------------------------------------------------
# Normalisation helper
# ---------------------------------------------------------------------------

def _normalise_request(req) -> TriggerEnvelope:
    try:
        payload = json.loads(req.payload_json) if req.payload_json else {}
    except Exception:
        payload = {"raw": req.payload_json}

    try:
        identity = json.loads(req.identity_json) if req.identity_json else {}
    except Exception:
        identity = {}

    if req.agent_id:
        payload["_agent_id"] = req.agent_id

    return TriggerEnvelope(
        channel="grpc",
        event_type=req.event_type or "grpc.trigger",
        payload=payload,
        identity=identity or {"user_id": "grpc"},
        raw={"event_type": req.event_type, "agent_id": req.agent_id},
    )
