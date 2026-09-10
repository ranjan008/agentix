"""Tests for the generic HttpApiConnector — auth schemes, method
restriction, path joining, response shape. httpx.MockTransport (no network),
same pattern as test_notion.py.
"""
from __future__ import annotations

import httpx
import pytest

from agentix.connectors.builtin.http_api import HttpApiConnector


def _conn(handler, **cfg) -> HttpApiConnector:
    base = {"base_url": "https://api.example.com"}
    base.update(cfg)
    c = HttpApiConnector(base)
    c._transport = httpx.MockTransport(handler)
    return c


def _capture():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"ok": 1})

    return handler, seen


@pytest.mark.asyncio
async def test_connect_rejects_non_http_base_url():
    c = HttpApiConnector({"base_url": "ftp://nope"})
    with pytest.raises(ValueError, match="http"):
        await c.connect()


@pytest.mark.asyncio
async def test_path_is_joined_onto_base_url():
    handler, seen = _capture()
    await _conn(handler).request(method="GET", path="/v1/users/42")
    assert seen["url"] == "https://api.example.com/v1/users/42"


@pytest.mark.asyncio
async def test_bearer_auth():
    handler, seen = _capture()
    await _conn(handler, auth_type="bearer", auth_token="sekret").request(path="/x")
    assert seen["headers"]["authorization"] == "Bearer sekret"


@pytest.mark.asyncio
async def test_api_key_header_auth_custom_name():
    handler, seen = _capture()
    await _conn(handler, auth_type="api_key_header", api_key="k1", api_key_header_name="X-Corp-Key").request(path="/x")
    assert seen["headers"]["x-corp-key"] == "k1"


@pytest.mark.asyncio
async def test_api_key_query_auth():
    handler, seen = _capture()
    await _conn(handler, auth_type="api_key_query", api_key="qk", api_key_query_name="apikey").request(path="/x")
    assert seen["params"]["apikey"] == "qk"


@pytest.mark.asyncio
async def test_basic_auth():
    handler, seen = _capture()
    await _conn(handler, auth_type="basic", username="u", password="p").request(path="/x")
    # httpx encodes u:p -> base64("dTo=p") ... just assert the scheme is Basic
    assert seen["headers"]["authorization"].startswith("Basic ")


@pytest.mark.asyncio
async def test_allowed_methods_blocks_writes():
    handler, _ = _capture()
    conn = _conn(handler, allowed_methods="GET")
    await conn.request(method="GET", path="/x")  # fine
    with pytest.raises(ValueError, match="only permits"):
        await conn.request(method="POST", path="/x", json_body={"a": 1})


@pytest.mark.asyncio
async def test_response_shape_includes_headers_and_ok():
    def handler(request):
        return httpx.Response(201, json={"created": True}, headers={"X-Trace": "abc"})

    out = await _conn(handler).request(method="POST", path="/things", json_body={"n": 1})
    assert out["status_code"] == 201
    assert out["ok"] is True
    assert out["body"] == {"created": True}
    assert out["headers"]["x-trace"] == "abc"


@pytest.mark.asyncio
async def test_unknown_auth_type_raises():
    handler, _ = _capture()
    with pytest.raises(ValueError, match="unknown auth_type"):
        await _conn(handler, auth_type="oauth_magic").request(path="/x")
