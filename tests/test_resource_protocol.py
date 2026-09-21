"""Validate resource discovery and reads through the authenticated HTTP endpoint."""

import json
from contextlib import asynccontextmanager
from itertools import count

import httpx
import pytest
from mcp.types import (
    ListResourcesResult,
    ListResourceTemplatesResult,
    ReadResourceResult,
)

from src.server import starlette_app


@pytest.fixture(params=["2025-06-18", "2025-11-25", "2026-07-28"])
def protocol_version(request):
    return request.param


@pytest.fixture(params=["path", "bearer"])
def resource_rpc(request, protocol_version):
    return lambda: resource_connection(request.param, protocol_version)


@asynccontextmanager
async def resource_connection(auth, protocol_version):
    headers = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": protocol_version,
    }
    path = "/RESOURCE_TEST_KEY/mcp" if auth == "path" else "/mcp"
    if auth == "bearer":
        headers["Authorization"] = "Bearer RESOURCE_TEST_KEY"
    identifiers = count(1)
    async with starlette_app.router.lifespan_context(starlette_app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=starlette_app),
            base_url="http://test",
            headers=headers,
        ) as client:

            async def call(method, params=None):
                params = dict(params or {})
                if protocol_version == "2026-07-28":
                    params["_meta"] = {
                        "io.modelcontextprotocol/protocolVersion": protocol_version,
                        "io.modelcontextprotocol/clientInfo": {
                            "name": "resource-test",
                            "version": "1",
                        },
                        "io.modelcontextprotocol/clientCapabilities": {},
                    }
                routing = {"Mcp-Method": method}
                if method == "resources/read":
                    routing["Mcp-Name"] = params["uri"]
                ident = next(identifiers)
                response = await client.post(
                    path,
                    headers=routing,
                    json={
                        "jsonrpc": "2.0",
                        "id": ident,
                        "method": method,
                        "params": params,
                    },
                )
                body = response.json()
                expected_status = (
                    400 if "error" in body and protocol_version == "2026-07-28" else 200
                )
                assert response.status_code == expected_status, response.text
                assert body["jsonrpc"] == "2.0"
                assert body["id"] == ident
                if "result" in body:
                    result = body["result"]
                    if protocol_version == "2026-07-28":
                        assert result["resultType"] == "complete"
                    else:
                        assert "resultType" not in result
                return body

            if protocol_version == "2026-07-28":
                discovery = await call("server/discover")
            else:
                discovery = await call(
                    "initialize",
                    {
                        "protocolVersion": protocol_version,
                        "clientInfo": {"name": "resource-test", "version": "1"},
                        "capabilities": {},
                    },
                )
                initialized = await client.post(
                    path, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
                )
                assert initialized.status_code == 202
            assert "resources" in discovery["result"]["capabilities"]
            yield call


async def test_resource_discovery_and_template_reads(resource_rpc):
    async with resource_rpc() as resource_rpc:
        listed = (await resource_rpc("resources/list"))["result"]
        templates = (await resource_rpc("resources/templates/list"))["result"]
        ListResourcesResult.model_validate(listed)
        ListResourceTemplatesResult.model_validate(templates)
        resources = {item["uri"]: item for item in listed["resources"]}
        assert "serpapi://engines" in resources
        assert any(uri.startswith("ui://prefab/") for uri in resources)
        template = next(
            t for t in templates["resourceTemplates"] if t["name"] == "serpapi-engine"
        )
        assert template["uriTemplate"] == "serpapi://engines/{engine_name}"
        assert resources["serpapi://engines"]["mimeType"] == "application/json"
        assert template["mimeType"] == "application/json"
        assert not listed.get("nextCursor")
        assert not templates.get("nextCursor")
        assert (await resource_rpc("resources/list"))["result"] == listed
        assert (await resource_rpc("resources/templates/list"))["result"] == templates

        async def read_json(uri):
            result = (await resource_rpc("resources/read", {"uri": uri}))["result"]
            ReadResourceResult.model_validate(result)
            assert "structuredContent" not in result
            assert len(result["contents"]) == 1
            content = result["contents"][0]
            assert content["uri"] == uri
            assert content["mimeType"] == "application/json"
            return json.loads(content["text"])

        index = await read_json("serpapi://engines")
        assert index["count"] == len(index["engines"])
        assert index["engines"] == sorted(set(index["engines"]))
        for engine in ["google_light", "google_hotels", "google_flights"]:
            assert engine in index["engines"]
            uri = template["uriTemplate"].replace("{engine_name}", engine)
            schema = await read_json(uri)
            assert schema["engine"] == engine
            assert "params" in schema
            assert "api_key" not in schema["common_params"]


@pytest.mark.parametrize("engine", ["nonexistent_resource_test", "google-hotels", ".."])
async def test_unknown_or_invalid_engine_is_a_protocol_error(resource_rpc, engine):
    async with resource_rpc() as resource_rpc:
        response = await resource_rpc(
            "resources/read", {"uri": f"serpapi://engines/{engine}"}
        )
        assert "result" not in response
        assert response["error"]["code"] == -32602
        assert response["error"]["message"]
