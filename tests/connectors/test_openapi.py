"""Tests for the OpenApiConnector — spec parsing, the single generated
tool, operation dispatch with path-param substitution + auth, and the
size/shape guards. httpx.MockTransport, no network.
"""
from __future__ import annotations

import json

import httpx
import pytest

from agentix.connectors.builtin.openapi import OpenApiConnector

_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Widget API"},
    "servers": [{"url": "https://widgets.example.com/v1"}],
    "paths": {
        "/widgets": {
            "get": {"operationId": "listWidgets", "summary": "List all widgets",
                    "parameters": [{"name": "limit", "in": "query"}]},
            "post": {"operationId": "createWidget", "summary": "Create a widget",
                     "requestBody": {"content": {"application/json": {}}}},
        },
        "/widgets/{id}": {
            "get": {"operationId": "getWidget", "summary": "Get one widget",
                    "parameters": [{"name": "id", "in": "path", "required": True}]},
        },
    },
}


def _conn(handler, spec=None, **cfg) -> OpenApiConnector:
    base = {"spec": json.dumps(spec or _SPEC)}
    base.update(cfg)
    c = OpenApiConnector(base)
    c._transport = httpx.MockTransport(handler)
    return c


def _capture():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json={"ok": 1})

    return handler, seen


@pytest.mark.asyncio
async def test_parses_and_builds_operation_map():
    c = _conn(lambda r: httpx.Response(200))
    await c.connect()
    assert set(c._ops) == {"listWidgets", "createWidget", "getWidget"}
    assert c._ops["getWidget"]["method"] == "GET"
    assert c._base_url == "https://widgets.example.com/v1"


@pytest.mark.asyncio
async def test_single_tool_lists_every_operation_in_description():
    c = _conn(lambda r: httpx.Response(200))
    await c.connect()
    schemas = c.tool_schemas()
    assert len(schemas) == 1
    assert schemas[0]["name"] == "openapi__call_operation"
    d = schemas[0]["description"]
    assert "listWidgets" in d and "createWidget" in d and "getWidget" in d
    assert "Widget API" in d


@pytest.mark.asyncio
async def test_call_operation_substitutes_path_params_and_joins_base():
    handler, seen = _capture()
    c = _conn(handler)
    await c.connect()
    await c.call_operation("getWidget", path_params={"id": 99})
    assert seen["url"] == "https://widgets.example.com/v1/widgets/99"
    assert seen["method"] == "GET"


@pytest.mark.asyncio
async def test_call_operation_applies_auth():
    handler, seen = _capture()
    c = _conn(handler, auth_type="bearer", auth_token="tok")
    await c.connect()
    await c.call_operation("listWidgets", query={"limit": 5})
    assert seen["headers"]["authorization"] == "Bearer tok"


@pytest.mark.asyncio
async def test_unknown_operation_id_lists_valid_ones():
    c = _conn(lambda r: httpx.Response(200))
    await c.connect()
    with pytest.raises(ValueError, match="createWidget"):
        await c.call_operation("deleteEverything")


@pytest.mark.asyncio
async def test_missing_path_param_is_a_clear_error():
    c = _conn(lambda r: httpx.Response(200))
    await c.connect()
    with pytest.raises(ValueError, match="path_params"):
        await c.call_operation("getWidget")  # no id


@pytest.mark.asyncio
async def test_base_url_override_wins():
    c = _conn(lambda r: httpx.Response(200), base_url_override="https://staging.internal/api")
    await c.connect()
    assert c._base_url == "https://staging.internal/api"


@pytest.mark.asyncio
async def test_oversized_spec_rejected():
    big = {"openapi": "3.0.0", "info": {"title": "x"}, "paths": {},
           "_pad": "z" * (520 * 1024)}
    c = OpenApiConnector({"spec": json.dumps(big)})
    with pytest.raises(ValueError, match="larger than"):
        await c.connect()


@pytest.mark.asyncio
async def test_spec_with_no_operation_ids_rejected():
    c = OpenApiConnector({"spec": json.dumps({"openapi": "3.0.0", "info": {"title": "x"},
                                              "paths": {"/a": {"get": {"summary": "no id"}}}})})
    with pytest.raises(ValueError, match="operationId"):
        await c.connect()


@pytest.mark.asyncio
async def test_yaml_spec_also_parses():
    yaml_spec = (
        "openapi: 3.0.0\n"
        "info:\n  title: YAML API\n"
        "servers:\n  - url: https://y.example.com\n"
        "paths:\n  /ping:\n    get:\n      operationId: ping\n      summary: ping\n"
    )
    c = OpenApiConnector({"spec": yaml_spec})
    c._transport = httpx.MockTransport(lambda r: httpx.Response(200))
    await c.connect()
    assert "ping" in c._ops
