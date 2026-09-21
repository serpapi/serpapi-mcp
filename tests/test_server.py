"""Offline unit tests for src/server.py.

The SerpApi client and the HTTP request are built from the real library types
(serpapi.SerpResults, a real requests/serpapi HTTPError, a real starlette
Request), so the suite pins the actual library contract without a network call
or an API key.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import requests
import serpapi
from fastmcp import Client
from serpapi.models import SerpResults
from starlette.requests import Request

import src.mcp_components.apps as mcp_apps
import src.mcp_components.resources as mcp_resources
import src.mcp_components.tools as mcp_tools
import src.server as server
from src.version import __version__


def make_serpapi_http_error(
    status, body, reason="Error", url="https://serpapi.com/search?q=x"
):
    """Wrap a real requests HTTPError as the SerpApi client does."""
    resp = requests.Response()
    resp.status_code = status
    resp.reason = reason
    resp.url = url
    resp._content = json.dumps(body).encode()
    resp.headers["Content-Type"] = "application/json"
    try:
        resp.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        return serpapi.exceptions.HTTPError(exc)
    raise AssertionError("raise_for_status did not raise")


def real_request(path="/mcp", headers=None, state=None):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "headers": raw,
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
    }
    if state is not None:
        scope["state"] = dict(state)
    return Request(scope)


def serp_results(payload):
    return SerpResults(payload, client=None)


@pytest.fixture(autouse=True)
def clear_bearer_auth_cache():
    # Module-level cache is shared process-wide; keep tests isolated from it.
    server.bearer_auth_cache.clear()
    yield


def use_request(monkeypatch, request):
    monkeypatch.setattr(mcp_tools, "get_http_request", lambda: request)


def use_search(monkeypatch, fn):
    monkeypatch.setattr(mcp_tools.serpapi, "search", fn)


async def test_filesystem_provider_registers_tools_apps_and_resources():
    tools = {tool.name: tool for tool in await server.mcp.list_tools()}
    resources = {str(resource.uri) for resource in await server.mcp.list_resources()}
    templates = {
        template.uri_template for template in await server.mcp.list_resource_templates()
    }

    assert {"search", "search_table", "search_dashboard"} <= set(tools)
    assert tools["search"].meta is None
    assert (
        tools["search_table"].meta["ui"]["resourceUri"].startswith("ui://prefab/tool/")
    )
    assert (
        tools["search_dashboard"]
        .meta["ui"]["resourceUri"]
        .startswith("ui://prefab/tool/")
    )
    assert "serpapi://engines" in resources
    assert "serpapi://engines/{engine_name}" in templates


def test_engines_dir_resolves_to_repo_engines_directory():
    assert mcp_resources.ENGINES_DIR.exists()
    assert (mcp_resources.ENGINES_DIR / "google_light.json").exists()


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_protocol_server_identity_uses_application_metadata(mode):
    async with Client(server.mcp, mode=mode) as client:
        info = client.server_info
        instructions = client.instructions

    assert info.version == __version__
    assert str(info.website_url) == "https://github.com/serpapi/mcp-server"
    assert "serpapi://engines" in instructions


async def test_engines_index_resource_reads_engine_files():
    result = await server.mcp.read_resource("serpapi://engines")
    body = json.loads(result.contents[0].content)

    assert body["count"] == len(list(mcp_resources.ENGINES_DIR.glob("*.json")))
    assert "google_light" in body["engines"]
    assert "resources" not in body


def raiser(exc):
    def _search(params):
        raise exc

    return _search


class _Wrap(Exception):
    """Keep the inner exception in args[0] to test nested error extraction."""


class _Resp:
    """Minimal stand-in for a requests.Response: only .json() is exercised."""

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class _WithResponse(Exception):
    def __init__(self, response):
        super().__init__("boom")
        self.response = response


def nest(depth, leaf):
    """Wrap `leaf` `depth` times so it sits at args[0]-chain index `depth`."""
    cur = leaf
    for _ in range(depth):
        cur = _Wrap(cur)
    return cur


def test_extract_error_response_reads_json_body_from_serpapi_http_error():
    err = make_serpapi_http_error(400, {"error": "Invalid API key."})
    assert json.loads(mcp_tools.extract_error_response(err)) == {
        "error": "Invalid API key."
    }


def test_extract_error_response_falls_back_to_response_text_when_not_json():
    resp = requests.Response()
    resp.status_code = 502
    resp.url = "https://serpapi.com/search"
    resp._content = b"upstream boom"
    try:
        resp.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        err = serpapi.exceptions.HTTPError(exc)
    assert mcp_tools.extract_error_response(err) == "upstream boom"


def test_extract_error_response_falls_back_to_str():
    assert (
        mcp_tools.extract_error_response(ValueError("plain message")) == "plain message"
    )


def test_extract_error_response_terminates_and_returns_innermost_message():
    err = ValueError("deepest")
    for _ in range(20):
        err = ValueError(err)
    # 20 levels deep with no .response anywhere: the walk must terminate (not
    # hang) and fall back to the chain's message string.
    assert mcp_tools.extract_error_response(err) == "deepest"


def test_extract_error_response_finds_response_at_depth_cap_boundary():
    leaf = _WithResponse(_Resp({"error": "deep"}))
    # index 9 is the last position the depth cap (10) still inspects.
    err = nest(9, leaf)
    assert json.loads(mcp_tools.extract_error_response(err)) == {"error": "deep"}


def test_extract_error_response_stops_one_past_the_depth_cap():
    leaf = _WithResponse(_Resp({"error": "too deep"}))
    # index 10 is one past the cap: the body must never be reached.
    err = nest(10, leaf)
    out = mcp_tools.extract_error_response(err)
    assert "too deep" not in out  # cap enforced, not just "returns a string"
    assert out == "boom"  # falls back to str() of the chain


async def test_search_rejects_invalid_mode():
    out = await mcp_tools.search(params={"q": "x"}, mode="bogus")
    assert out.is_error
    assert out.content[0].text == "Error: Invalid mode. Must be 'complete' or 'compact'"


async def test_search_rejects_unsupported_output_before_search(monkeypatch):
    def should_not_search(params):
        raise AssertionError("SerpApi should not be called for invalid output")

    use_search(monkeypatch, should_not_search)

    out = await mcp_tools.search(params={"q": "x", "output": "html"})

    assert out.is_error
    assert out.content[0].text == (
        "Error: Invalid output. Use either 'md' or 'json' for the output parameter."
    )


async def test_search_without_api_key_returns_graceful_error(monkeypatch):
    # A real starlette Request with empty state: request.state.api_key would raise
    # AttributeError, so the guard must use getattr, not attribute access.
    use_request(monkeypatch, real_request(state={}))
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    out = await mcp_tools.search(params={"q": "x"})
    assert out.is_error
    assert out.content[0].text == (
        "Error: Unable to access API key from request context "
        "or SERPAPI_API_KEY environment variable"
    )


def no_http_request():
    # What fastmcp raises when the server runs over stdio (no HTTP request).
    raise RuntimeError("No active HTTP request found.")


async def test_search_falls_back_to_env_api_key_over_stdio(monkeypatch):
    captured = {}

    def fake_search(params):
        captured.update(params)
        return serp_results({"organic_results": []})

    monkeypatch.setattr(mcp_tools, "get_http_request", no_http_request)
    monkeypatch.setenv("SERPAPI_API_KEY", "ENVKEY")
    use_search(monkeypatch, fake_search)

    await mcp_tools.search(params={"q": "x"})
    assert captured["api_key"] == "ENVKEY"


async def test_request_api_key_takes_precedence_over_env(monkeypatch):
    captured = {}

    def fake_search(params):
        captured.update(params)
        return serp_results({"organic_results": []})

    use_request(monkeypatch, real_request(state={"api_key": "REQUEST"}))
    monkeypatch.setenv("SERPAPI_API_KEY", "ENVKEY")
    use_search(monkeypatch, fake_search)

    await mcp_tools.search(params={"q": "x"})
    assert captured["api_key"] == "REQUEST"


async def test_search_over_stdio_without_env_key_returns_graceful_error(monkeypatch):
    monkeypatch.setattr(mcp_tools, "get_http_request", no_http_request)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    out = await mcp_tools.search(params={"q": "x"})
    assert out.is_error
    assert out.content[0].text.startswith("Error: Unable to access API key")


async def test_search_complete_returns_full_payload(monkeypatch):
    payload = {"search_metadata": {"id": "1"}, "organic_results": [{"title": "hit"}]}
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, lambda params: serp_results(payload))
    result = await mcp_tools.search(params={"q": "x"})
    assert result.structured_content == {"result": result.content[0].text}
    assert json.loads(result.content[0].text) == payload


async def test_search_returns_markdown_response_unchanged(monkeypatch):
    markdown = (
        "## Organic Results\n\n| Position | Title |\n| --- | --- |\n| 1 | Hit |\n"
    )
    captured = {}

    def capture(params):
        captured.update(params)
        return markdown

    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, capture)

    result = await mcp_tools.search(params={"q": "x", "output": "md"})
    assert result.content[0].text == markdown
    assert result.structured_content == {"result": markdown}
    assert captured["output"] == "md"


async def test_search_explicit_json_output_returns_json(monkeypatch):
    payload = {"organic_results": [{"title": "hit"}]}
    captured = {}

    def capture(params):
        captured.update(params)
        return serp_results(payload)

    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, capture)

    result = await mcp_tools.search(params={"q": "x", "output": "json"})
    assert result.structured_content == {"result": result.content[0].text}
    assert json.loads(result.content[0].text) == payload
    assert captured["output"] == "json"


async def test_search_json_request_rejects_unexpected_text_response(monkeypatch):
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, lambda params: "<html>unexpected response</html>")

    out = await mcp_tools.search(params={"q": "x", "output": "json"})

    assert out.is_error
    assert (
        out.content[0].text
        == "Error: SerpApi returned text when JSON output was requested."
    )
    assert "<html>" not in out.content[0].text


async def test_search_compact_strips_serpapi_metadata(monkeypatch):
    payload = {
        "search_metadata": {},
        "search_parameters": {},
        "search_information": {},
        "pagination": {},
        "serpapi_pagination": {},
        "organic_results": [{"title": "hit"}],
    }
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, lambda params: serp_results(payload))
    out = await mcp_tools.search(params={"q": "x"}, mode="compact")
    assert out.structured_content == {"result": out.content[0].text}
    assert json.loads(out.structured_content["result"]) == {
        "organic_results": [{"title": "hit"}]
    }


async def test_search_compact_returns_markdown_unchanged(monkeypatch):
    markdown = "## Organic Results\n\n- Hit\n"
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, lambda params: markdown)

    result = await mcp_tools.search(params={"q": "x", "output": "md"}, mode="compact")
    assert result.content[0].text == markdown
    assert result.structured_content == {"result": markdown}


async def test_search_compact_does_not_mutate_the_live_result(monkeypatch):
    payload = {"search_metadata": {"id": "1"}, "organic_results": [{"title": "hit"}]}
    results = serp_results(payload)
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, lambda params: results)
    await mcp_tools.search(params={"q": "x"}, mode="compact")
    assert "search_metadata" in results.as_dict()


async def test_search_forwards_api_key_and_default_engine(monkeypatch):
    captured = {}

    def capture(params):
        captured.update(params)
        return serp_results({"ok": True})

    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, capture)
    await mcp_tools.search(params={"q": "x"})
    assert captured["api_key"] == "KEY"
    assert captured["engine"] == "google_light"
    assert captured["q"] == "x"
    assert "output" not in captured


async def test_search_caller_overrides_default_engine(monkeypatch):
    captured = {}

    def capture(params):
        captured.update(params)
        return serp_results({})

    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, capture)
    await mcp_tools.search(params={"q": "x", "engine": "google_news"})
    assert captured["engine"] == "google_news"


async def test_search_ignores_caller_supplied_api_key(monkeypatch):
    # Caller-supplied api_key must never override the authenticated key.
    captured = {}

    def capture(params):
        captured.update(params)
        return serp_results({})

    use_request(monkeypatch, real_request(state={"api_key": "TRUSTED"}))
    use_search(monkeypatch, capture)
    await mcp_tools.search(params={"q": "x", "api_key": "CALLER_CONTROLLED"})
    assert captured["api_key"] == "TRUSTED"
    assert captured["q"] == "x"


async def test_search_apps_ignore_caller_supplied_api_key(monkeypatch):
    # App variants share fetch_search_data, so the same guard must hold.
    captured = {}

    def capture(params):
        captured.update(params)
        return serp_results(_SAMPLE_PAYLOAD)

    use_request(monkeypatch, real_request(state={"api_key": "TRUSTED"}))
    use_search(monkeypatch, capture)
    await mcp_apps.search_table(params={"q": "x", "api_key": "CALLER_CONTROLLED"})
    assert captured["api_key"] == "TRUSTED"


async def test_search_apps_force_json_output(monkeypatch):
    captured = {}

    def capture(params):
        captured.update(params)
        return serp_results(_SAMPLE_PAYLOAD)

    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, capture)
    await mcp_apps.search_table(params={"q": "x", "output": "md"})

    assert captured["output"] == "json"


@pytest.mark.parametrize(
    "status, fragment",
    [
        (429, "Rate limit exceeded"),
        (401, "Invalid SerpApi API key"),
        (403, "forbidden"),
    ],
)
async def test_search_maps_real_http_errors(monkeypatch, status, fragment):
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, raiser(make_serpapi_http_error(status, {"error": "x"})))
    out = await mcp_tools.search(params={"q": "x"})
    assert out.is_error
    assert out.content[0].text.startswith("Error:")
    assert fragment in out.content[0].text


async def test_search_unmapped_http_error_returns_json_body(monkeypatch):
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(
        monkeypatch, raiser(make_serpapi_http_error(500, {"error": "server boom"}))
    )
    out = await mcp_tools.search(params={"q": "x"})
    assert out.is_error
    assert out.content[0].text.startswith("Error:")
    assert "server boom" in out.content[0].text


async def test_search_generic_exception_uses_extractor(monkeypatch):
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, raiser(ValueError("weird failure")))
    result = await mcp_tools.search(params={"q": "x"})
    assert result.is_error
    assert result.content[0].text == "Error: weird failure"


async def passthrough(request):
    return "OK"


async def test_middleware_skips_healthcheck():
    mw = server.ApiKeyMiddleware(app=lambda *a, **k: None)
    assert await mw.dispatch(real_request(path="/healthcheck"), passthrough) == "OK"


async def test_middleware_extracts_bearer_token():
    mw = server.ApiKeyMiddleware(app=lambda *a, **k: None)
    request = real_request(path="/mcp", headers={"Authorization": "Bearer ABC123"})
    assert await mw.dispatch(request, passthrough) == "OK"
    assert request.state.api_key == "ABC123"


async def test_middleware_extracts_path_key_and_rewrites_path():
    mw = server.ApiKeyMiddleware(app=lambda *a, **k: None)
    request = real_request(path="/MYKEY/mcp")
    assert await mw.dispatch(request, passthrough) == "OK"
    assert request.state.api_key == "MYKEY"
    assert request.scope["path"] == "/mcp"


async def test_middleware_returns_401_without_key():
    mw = server.ApiKeyMiddleware(app=lambda *a, **k: None)
    response = await mw.dispatch(real_request(path="/mcp"), passthrough)
    assert response.status_code == 401


async def test_middleware_ignores_non_mcp_two_segment_path():
    # /foo/bar has two segments but the second isn't "mcp", so the first segment
    # must NOT be treated as an API key — the guard requires path_parts[1] == "mcp".
    mw = server.ApiKeyMiddleware(app=lambda *a, **k: None)
    response = await mw.dispatch(real_request(path="/foo/bar"), passthrough)
    assert response.status_code == 401


async def test_healthcheck_returns_healthy_with_utc_timestamp():
    resp = await server.healthcheck_handler(real_request(path="/healthcheck"))
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["status"] == "healthy"
    assert body["service"] == "SerpApi MCP Server"
    # timezone-aware UTC, Z-suffixed (utcnow() was deprecated on 3.12+).
    assert body["timestamp"].endswith("Z")


# --- OAuth 2.0 Protected Resource Metadata (RFC 9728) ----------------------


async def test_middleware_skips_oauth_protected_resource():
    mw = server.ApiKeyMiddleware(app=lambda *a, **k: None)
    request = real_request(path=server.OAUTH_PROTECTED_RESOURCE_PATH)
    assert await mw.dispatch(request, passthrough) == "OK"


async def test_middleware_401_challenges_with_resource_metadata_url(monkeypatch):
    monkeypatch.setattr(server, "PUBLIC_ORIGIN", "")
    mw = server.ApiKeyMiddleware(app=lambda *a, **k: None)
    response = await mw.dispatch(real_request(path="/mcp"), passthrough)
    assert response.status_code == 401
    challenge = response.headers["WWW-Authenticate"]
    assert challenge.startswith("Bearer ")
    assert (
        f'resource_metadata="http://testserver{server.OAUTH_PROTECTED_RESOURCE_PATH}"'
        in challenge
    )


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_resource_metadata_url_is_absolute_and_scheme_aware(monkeypatch, scheme):
    monkeypatch.setattr(server, "PUBLIC_ORIGIN", "")
    request = real_request(path="/mcp")
    request.scope["scheme"] = scheme
    request.scope["server"] = ("testserver", 443 if scheme == "https" else 80)
    assert (
        server.resource_metadata_url(request)
        == f"{scheme}://testserver{server.OAUTH_PROTECTED_RESOURCE_PATH}"
    )


async def test_oauth_protected_resource_handler_returns_metadata(monkeypatch):
    monkeypatch.setattr(server, "PUBLIC_ORIGIN", "")
    request = real_request(path=server.OAUTH_PROTECTED_RESOURCE_PATH)
    resp = await server.oauth_protected_resource_handler(request)
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["resource"] == "http://testserver/mcp"
    assert body["authorization_servers"] == [server.OAUTH_AUTHORIZATION_SERVER]
    assert body["bearer_methods_supported"] == ["header"]
    assert body["scopes_supported"] == ["search"]


async def test_discovery_uses_public_origin_behind_tls_proxy(monkeypatch):
    monkeypatch.setattr(server, "PUBLIC_ORIGIN", "https://mcp.example.com")
    transport = httpx.ASGITransport(
        app=server.starlette_app, client=("10.0.1.23", 12345)
    )
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://internal:8000",
        headers={"X-Forwarded-Proto": "https"},
    ) as client:
        metadata = await client.get(server.OAUTH_PROTECTED_RESOURCE_PATH)
        challenge = await client.post("/mcp")

    assert metadata.status_code == 200
    assert metadata.json()["resource"] == "https://mcp.example.com/mcp"
    assert challenge.status_code == 401
    assert (
        'resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource"'
        in challenge.headers["WWW-Authenticate"]
    )


@pytest.mark.parametrize("override", [False, True])
def test_oauth_settings_load_from_dotenv_before_initialization(tmp_path, override):
    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text(
        "MCP_PUBLIC_ORIGIN=https://mcp.example.com/\n"
        "MCP_OAUTH_AUTHORIZATION_SERVER=https://auth.example.com\n"
        "MCP_OAUTH_CLIENT_ID=test-client\n"
        "MCP_OAUTH_CLIENT_SECRET=test-secret\n"
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("MCP_OAUTH_")
        and key not in {"MCP_PUBLIC_ORIGIN", "PYTHON_DOTENV_DISABLED"}
    }
    if override:
        env["MCP_OAUTH_INTROSPECTION_URL"] = "https://auth.example.com/custom"
        env["MCP_OAUTH_CLIENT_ID"] = "environment-client"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import json
import sys
from unittest.mock import patch

with patch("dotenv.main.find_dotenv", return_value=sys.argv[1]):
    from src import server
print(json.dumps([
    server.PUBLIC_ORIGIN,
    server.OAUTH_AUTHORIZATION_SERVER,
    server.OAUTH_INTROSPECTION_URL,
    server.OAUTH_CLIENT_ID,
    server.OAUTH_CLIENT_SECRET,
    server.OAUTH_INTROSPECTION_ENABLED,
]))
""",
            str(dotenv_file),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert json.loads(result.stdout) == [
        "https://mcp.example.com",
        "https://auth.example.com",
        "https://auth.example.com/custom"
        if override
        else "https://auth.example.com/oauth/introspect",
        "environment-client" if override else "test-client",
        "test-secret",
        True,
    ]


# --- OAuth token introspection ---------------------------------------------


class FakeIntrospectionResponse:
    def __init__(self, json_body, status_code=200):
        self._json_body = json_body
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json_body


class FakeAsyncClient:
    def __init__(self, response=None, exc=None, **kwargs):
        self._response = response
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, data=None, auth=None):
        if self._exc:
            raise self._exc
        return self._response


async def test_introspect_token_returns_api_key_for_active_token(monkeypatch):
    monkeypatch.setattr(server, "OAUTH_CLIENT_ID", "mcp-client")
    monkeypatch.setattr(server, "OAUTH_CLIENT_SECRET", "mcp-secret")
    response = FakeIntrospectionResponse({"active": True, "api_key": "USER_KEY"})
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: FakeAsyncClient(response=response)
    )
    assert await server.introspect_token("some-token") == "USER_KEY"


async def test_introspect_token_returns_none_for_inactive_token(monkeypatch):
    response = FakeIntrospectionResponse({"active": False})
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: FakeAsyncClient(response=response)
    )
    assert await server.introspect_token("revoked-token") is None


async def test_introspect_token_returns_none_on_http_error(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: FakeAsyncClient(exc=httpx.ConnectError("down")),
    )
    assert await server.introspect_token("some-token") is None


async def fut(value):
    return value


async def test_middleware_uses_introspection_when_enabled(monkeypatch):
    monkeypatch.setattr(server, "OAUTH_INTROSPECTION_ENABLED", True)
    monkeypatch.setattr(server, "introspect_token", lambda token: fut("USER_KEY"))

    def unexpected_account_call(**kwargs):
        pytest.fail("OAuth tokens must not trigger Account API validation")

    monkeypatch.setattr(serpapi, "account", unexpected_account_call)
    mw = server.ApiKeyMiddleware(app=lambda *a, **k: None)
    request = real_request(path="/mcp", headers={"Authorization": "Bearer oauth-token"})
    assert await mw.dispatch(request, passthrough) == "OK"
    assert request.state.api_key == "USER_KEY"


async def test_middleware_rejects_bearer_when_introspection_fails(monkeypatch):
    monkeypatch.setattr(server, "OAUTH_INTROSPECTION_ENABLED", True)
    monkeypatch.setattr(server, "introspect_token", lambda token: fut(None))
    monkeypatch.setattr(server, "is_valid_api_key", lambda key: fut(False))
    mw = server.ApiKeyMiddleware(app=lambda *a, **k: None)
    request = real_request(path="/mcp", headers={"Authorization": "Bearer bad-token"})
    response = await mw.dispatch(request, passthrough)
    assert response.status_code == 401


async def test_resolve_bearer_api_key_caches_successful_resolution(monkeypatch):
    calls = []

    async def introspect(token):
        calls.append(token)
        return "USER_KEY"

    monkeypatch.setattr(server, "introspect_token", introspect)
    assert await server.resolve_bearer_api_key("some-token") == "USER_KEY"
    assert await server.resolve_bearer_api_key("some-token") == "USER_KEY"
    assert calls == ["some-token"]


async def test_resolve_bearer_api_key_does_not_cache_rejections(monkeypatch):
    calls = []

    async def introspect(token):
        calls.append(token)
        return None

    monkeypatch.setattr(server, "introspect_token", introspect)
    monkeypatch.setattr(server, "is_valid_api_key", lambda key: fut(False))
    assert await server.resolve_bearer_api_key("bad-token") is None
    assert await server.resolve_bearer_api_key("bad-token") is None
    assert calls == ["bad-token", "bad-token"]


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("auth", ["bearer", "path"])
async def test_legacy_keys_work_with_or_without_oauth(monkeypatch, enabled, auth):
    monkeypatch.setattr(server, "OAUTH_INTROSPECTION_ENABLED", enabled)
    introspected = []
    validated = []

    async def introspect(token):
        introspected.append(token)
        return None

    def account(*, api_key, timeout):
        validated.append(api_key)
        assert timeout == 5.0
        return {"api_key": api_key}

    monkeypatch.setattr(server, "introspect_token", introspect)
    monkeypatch.setattr(serpapi, "account", account)
    request = real_request(
        path="/RAW_KEY/mcp" if auth == "path" else "/mcp",
        headers={"Authorization": "Bearer RAW_KEY"} if auth == "bearer" else {},
    )
    mw = server.ApiKeyMiddleware(app=lambda *a, **k: None)
    assert await mw.dispatch(request, passthrough) == "OK"
    assert request.state.api_key == "RAW_KEY"
    assert request.scope["path"] == "/mcp"
    expected = ["RAW_KEY"] if enabled and auth == "bearer" else []
    assert introspected == validated == expected


@pytest.mark.parametrize("introspection_down", [False, True])
@pytest.mark.parametrize("valid_key", [False, True])
async def test_bearer_fallback_only_accepts_verified_keys(
    monkeypatch, introspection_down, valid_key
):
    monkeypatch.setattr(server, "OAUTH_INTROSPECTION_ENABLED", True)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: FakeAsyncClient(
            response=FakeIntrospectionResponse({"active": False}),
            exc=httpx.ConnectError("down") if introspection_down else None,
        ),
    )

    def account(*, api_key, timeout):
        assert api_key == "CREDENTIAL"
        if not valid_key:
            raise make_serpapi_http_error(401, {"error": "Invalid API key."})
        return {"api_key": api_key}

    monkeypatch.setattr(serpapi, "account", account)
    request = real_request(headers={"Authorization": "Bearer CREDENTIAL"})
    mw = server.ApiKeyMiddleware(app=lambda *a, **k: None)
    response = await mw.dispatch(request, passthrough)
    if valid_key:
        assert response == "OK"
        assert request.state.api_key == "CREDENTIAL"
    else:
        assert response.status_code == 401
        assert not hasattr(request.state, "api_key")


async def test_empty_bearer_does_not_call_authentication_services(monkeypatch):
    monkeypatch.setattr(server, "OAUTH_INTROSPECTION_ENABLED", True)

    async def unexpected_lookup(value):
        pytest.fail("Empty credentials must not trigger upstream requests")

    monkeypatch.setattr(server, "introspect_token", unexpected_lookup)
    monkeypatch.setattr(server, "is_valid_api_key", unexpected_lookup)
    request = real_request(headers={"Authorization": "Bearer   "})
    mw = server.ApiKeyMiddleware(app=lambda *a, **k: None)
    response = await mw.dispatch(request, passthrough)
    assert response.status_code == 401


@pytest.mark.parametrize(
    "account",
    [None, [], {}, {"error": "Invalid key"}, {"api_key": "OTHER_KEY"}],
)
async def test_api_key_validation_requires_matching_account_key(monkeypatch, account):
    monkeypatch.setattr(serpapi, "account", lambda **kwargs: account)
    assert not await server.is_valid_api_key("RAW_KEY")


@pytest.mark.parametrize(
    "error",
    [
        make_serpapi_http_error(401, {"error": "Invalid API key."}),
        make_serpapi_http_error(403, {"error": "Forbidden"}),
        make_serpapi_http_error(429, {"error": "Too many requests"}),
        make_serpapi_http_error(500, {"error": "Server error"}),
        serpapi.exceptions.HTTPConnectionError(
            requests.exceptions.ConnectionError("secret-key-in-url")
        ),
        serpapi.exceptions.TimeoutError("secret-key-in-url"),
        ValueError("secret-key-in-url"),
    ],
)
async def test_api_key_validation_fails_closed_without_logging_keys(
    monkeypatch, caplog, error
):
    def account(**kwargs):
        raise error

    monkeypatch.setattr(serpapi, "account", account)
    assert not await server.is_valid_api_key("secret-key-in-url")
    assert "API-key validation failed" in caplog.text
    assert "secret-key-in-url" not in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {"active": "false", "api_key": "KEY"},
        {"active": True},
        {"active": True, "api_key": []},
        {"active": True, "api_key": " "},
    ],
)
async def test_introspection_rejects_malformed_responses(monkeypatch, body):
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: FakeAsyncClient(response=FakeIntrospectionResponse(body)),
    )
    assert await server.introspect_token("some-token") is None


# --- MCP Apps: shared error mapping ----------------------------------------


@pytest.mark.parametrize(
    "status, fragment",
    [
        (429, "Rate limit exceeded"),
        (401, "Invalid SerpApi API key"),
        (403, "forbidden"),
    ],
)
def test_map_search_error_maps_known_statuses(status, fragment):
    out = mcp_tools.map_search_error(make_serpapi_http_error(status, {"error": "x"}))
    assert out.startswith("Error:")
    assert fragment in out


def test_map_search_error_falls_back_to_json_body():
    out = mcp_tools.map_search_error(
        make_serpapi_http_error(500, {"error": "server boom"})
    )
    assert "server boom" in out


def test_map_search_error_handles_generic_exception():
    assert mcp_tools.map_search_error(ValueError("weird")) == "Error: weird"


# --- MCP Apps: pure view-model helpers -------------------------------------


def test_organic_rows_flattens_results():
    data = {
        "organic_results": [
            {
                "position": 1,
                "title": "A",
                "link": "https://a.com/x",
                "source": "A Co",
                "snippet": "s1",
            },
            {"position": 2, "title": "B", "link": "https://b.com/y", "snippet": "s2"},
        ]
    }
    rows = mcp_apps.organic_rows(data)
    assert rows[0] == {
        "position": 1,
        "title": "A",
        "link": "https://a.com/x",
        "source": "A Co",
        "snippet": "s1",
    }
    # source falls back to the link host (www stripped) when not provided.
    assert rows[1]["source"] == "b.com"


def test_organic_rows_strips_www_from_derived_source():
    data = {"organic_results": [{"title": "x", "link": "https://www.example.com/p"}]}
    assert mcp_apps.organic_rows(data)[0]["source"] == "example.com"


def test_organic_rows_empty_without_results():
    assert mcp_apps.organic_rows({}) == []
    assert mcp_apps.organic_rows({"organic_results": None}) == []


def test_source_breakdown_counts_and_limits():
    rows = [{"source": "a"}, {"source": "a"}, {"source": "b"}, {"source": ""}]
    breakdown = mcp_apps.source_breakdown(rows, limit=1)
    assert breakdown == [{"source": "a", "count": 2}]


def test_dashboard_summary_shape():
    data = {
        "search_parameters": {"q": "coffee", "engine": "google_light"},
        "search_information": {"total_results": 999},
        "organic_results": [{"title": "A", "link": "https://a.com", "source": "A"}],
    }
    summary = mcp_apps.dashboard_summary(data)
    assert summary["query"] == "coffee"
    assert summary["engine"] == "google_light"
    assert summary["total_results"] == 999
    assert summary["result_count"] == 1
    assert summary["sources"] == [{"source": "A", "count": 1}]


# --- MCP Apps: tool behavior -----------------------------------------------


def ui_json(app):
    """Serialize a Prefab app via its canonical serializer for assertions."""
    return json.dumps(app.to_json(), ensure_ascii=False)


_SAMPLE_PAYLOAD = {
    "search_parameters": {"q": "coffee", "engine": "google_light"},
    "search_information": {"total_results": 12345},
    "organic_results": [
        {
            "position": 1,
            "title": "Best Coffee",
            "link": "https://example.com/a",
            "snippet": "beans",
        },
    ],
}


async def test_search_table_returns_results_app(monkeypatch):
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, lambda params: serp_results(_SAMPLE_PAYLOAD))
    app = await mcp_apps.search_table(params={"q": "coffee"})
    assert app.title == "Search results"
    body = ui_json(app)
    assert "DataTable" in body
    assert "Best Coffee" in body


async def test_search_dashboard_returns_dashboard_app(monkeypatch):
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, lambda params: serp_results(_SAMPLE_PAYLOAD))
    app = await mcp_apps.search_dashboard(params={"q": "coffee"})
    assert app.title == "Search dashboard"
    # click-to-expand detail panel starts collapsed.
    assert app.state == {"selected": None}
    body = ui_json(app)
    assert "Best Coffee" in body
    assert "coffee" in body  # query surfaced in a metric


async def test_search_table_without_api_key_renders_error_app(monkeypatch):
    use_request(monkeypatch, real_request(state={}))
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    app = await mcp_apps.search_table(params={"q": "x"})
    assert app.title == "Search error"
    assert "Unable to access API key" in ui_json(app)


async def test_search_dashboard_maps_http_error_to_error_app(monkeypatch):
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, raiser(make_serpapi_http_error(429, {"error": "x"})))
    app = await mcp_apps.search_dashboard(params={"q": "x"})
    assert app.title == "Search error"
    assert "Rate limit exceeded" in ui_json(app)


# --- MCP Apps: Flights-specific helpers and builder -------------------------

_SAMPLE_FLIGHTS_PAYLOAD = {
    "search_parameters": {
        "engine": "google_flights",
        "departure_id": "SFO",
        "arrival_id": "JFK",
    },
    "best_flights": [
        {
            "flights": [
                {
                    "departure_airport": {
                        "name": "San Francisco",
                        "id": "SFO",
                        "time": "2026-07-01 08:00",
                    },
                    "arrival_airport": {
                        "name": "New York JFK",
                        "id": "JFK",
                        "time": "2026-07-01 16:30",
                    },
                    "airline": "United",
                    "flight_number": "UA 123",
                    "duration": 330,
                }
            ],
            "layovers": [],
            "total_duration": 330,
            "price": 289,
            "type": "One way",
            "carbon_emissions": {
                "this_flight": 250000,
                "typical_for_this_route": 280000,
                "difference_percent": -11,
            },
        },
        {
            "flights": [
                {
                    "departure_airport": {
                        "name": "San Francisco",
                        "id": "SFO",
                        "time": "2026-07-01 06:00",
                    },
                    "arrival_airport": {
                        "name": "Denver",
                        "id": "DEN",
                        "time": "2026-07-01 09:30",
                    },
                    "airline": "Delta",
                    "duration": 150,
                },
                {
                    "departure_airport": {
                        "name": "Denver",
                        "id": "DEN",
                        "time": "2026-07-01 10:45",
                    },
                    "arrival_airport": {
                        "name": "New York JFK",
                        "id": "JFK",
                        "time": "2026-07-01 16:00",
                    },
                    "airline": "Delta",
                    "duration": 195,
                },
            ],
            "layovers": [
                {"duration": 75, "name": "Denver International Airport", "id": "DEN"}
            ],
            "total_duration": 420,
            "price": 199,
            "type": "One way",
            "carbon_emissions": {
                "this_flight": 310000,
                "typical_for_this_route": 280000,
                "difference_percent": 11,
            },
        },
    ],
    "other_flights": [
        {
            "flights": [
                {
                    "departure_airport": {
                        "name": "San Francisco",
                        "id": "SFO",
                        "time": "2026-07-01 14:00",
                    },
                    "arrival_airport": {
                        "name": "New York JFK",
                        "id": "JFK",
                        "time": "2026-07-01 22:45",
                    },
                    "airline": "JetBlue",
                    "duration": 345,
                }
            ],
            "layovers": [],
            "total_duration": 345,
            "price": 329,
            "type": "One way",
            "carbon_emissions": {},
        },
    ],
    "price_insights": {
        "lowest_price": 199,
        "price_level": "low",
        "typical_price_range": [250, 420],
        "price_history": [
            [1719792000, 310],
            [1719878400, 305],
            [1719964800, 289],
            [1720051200, 275],
            [1720137600, 199],
        ],
    },
}


def test_flights_rows_extracts_all_flights():
    rows = mcp_apps.flights_rows(_SAMPLE_FLIGHTS_PAYLOAD)
    assert len(rows) == 3
    # Direct flight
    assert rows[0]["airline"] == "United"
    assert rows[0]["route"] == "SFO → JFK"
    assert rows[0]["price"] == 289
    assert rows[0]["price_fmt"] == "$289"
    assert rows[0]["stops"] == "Direct"
    assert rows[0]["departure"] == "2026-07-01 08:00"
    assert rows[0]["arrival"] == "2026-07-01 16:30"
    assert rows[0]["duration"] == "5h 30m"
    assert rows[0]["carbon_delta"] == -11
    assert rows[0]["carbon_fmt"] == "-11% vs typical"
    assert rows[0]["type"] == "One way"
    # Multi-segment flight
    assert rows[1]["airline"] == "Delta"
    assert rows[1]["stops"] == "1 stop"
    assert rows[1]["price"] == 199
    assert rows[1]["arrival"] == "2026-07-01 16:00"
    assert rows[1]["duration"] == "7h 0m"
    assert rows[1]["carbon_fmt"] == "+11% vs typical"
    # other_flights section
    assert rows[2]["airline"] == "JetBlue"
    assert rows[2]["carbon_fmt"] == "—"


def test_flights_rows_handles_empty_data():
    assert mcp_apps.flights_rows({}) == []
    assert mcp_apps.flights_rows({"best_flights": None, "other_flights": None}) == []


def test_flights_rows_handles_missing_airports():
    data = {
        "best_flights": [
            {"flights": [], "layovers": [], "total_duration": 0, "price": 100}
        ]
    }
    rows = mcp_apps.flights_rows(data)
    assert len(rows) == 1
    assert rows[0]["route"] == "? → ?"
    assert rows[0]["price"] == 100
    assert rows[0]["departure"] == ""
    assert rows[0]["arrival"] == ""


def test_flights_rows_zero_price_defaults():
    data = {"best_flights": [{"flights": [], "layovers": [], "total_duration": 120}]}
    rows = mcp_apps.flights_rows(data)
    assert rows[0]["price"] == 0
    assert rows[0]["price_fmt"] == "—"


def test_format_duration():
    assert mcp_apps._format_duration(330) == "5h 30m"
    assert mcp_apps._format_duration(45) == "45m"
    assert mcp_apps._format_duration(60) == "1h 0m"
    assert mcp_apps._format_duration(None) == "—"
    assert mcp_apps._format_duration(0) == "—"


def test_price_history_points_converts_timestamps():
    points = mcp_apps.price_history_points(_SAMPLE_FLIGHTS_PAYLOAD)
    assert len(points) == 5
    assert points[0]["price"] == 310
    assert "date" in points[0]
    # Dates should be human-readable month/day format
    assert len(points[0]["date"]) > 0


def test_price_history_points_handles_empty():
    assert mcp_apps.price_history_points({}) == []
    assert mcp_apps.price_history_points({"price_insights": {}}) == []
    assert (
        mcp_apps.price_history_points({"price_insights": {"price_history": []}}) == []
    )


def test_price_history_points_skips_malformed_entries():
    data = {
        "price_insights": {"price_history": [[1719792000], "bad", [1719878400, 300]]}
    }
    points = mcp_apps.price_history_points(data)
    assert len(points) == 1
    assert points[0]["price"] == 300


def test_flights_price_insights_extracts_metrics():
    insights = mcp_apps.flights_price_insights(_SAMPLE_FLIGHTS_PAYLOAD)
    assert insights["lowest_price"] == 199
    assert insights["price_level"] == "low"
    assert insights["typical_low"] == 250
    assert insights["typical_high"] == 420


def test_flights_price_insights_handles_missing():
    insights = mcp_apps.flights_price_insights({})
    assert insights["lowest_price"] is None
    assert insights["price_level"] == "unknown"
    assert insights["typical_low"] is None
    assert insights["typical_high"] is None


def test_build_flights_app_produces_valid_app():
    app = mcp_apps.build_flights_app(_SAMPLE_FLIGHTS_PAYLOAD)
    assert "SFO → JFK" in app.title
    assert app.state == {"selected": None}
    body = ui_json(app)
    assert "AreaChart" in body
    assert "DataTable" in body
    assert "United" in body
    # Numeric price in table for correct sorting
    assert "199" in body
    # Carbon and arrival visible in detail panel
    assert "carbon_fmt" in body
    assert "Arrival" in body
    assert "Carbon emissions" in body


def test_build_flights_app_without_price_history():
    data = {
        "search_parameters": {
            "engine": "google_flights",
            "departure_id": "LAX",
            "arrival_id": "ORD",
        },
        "best_flights": [
            {
                "flights": [
                    {
                        "departure_airport": {"id": "LAX", "time": "10:00"},
                        "arrival_airport": {"id": "ORD", "time": "16:00"},
                        "airline": "AA",
                    }
                ],
                "layovers": [],
                "total_duration": 240,
                "price": 350,
            }
        ],
        "price_insights": {},
    }
    app = mcp_apps.build_flights_app(data)
    body = ui_json(app)
    # Should still render table without crashing, just no chart
    assert "DataTable" in body
    assert "AreaChart" not in body


def test_build_flights_app_generic_title_without_route():
    data = {"search_parameters": {"engine": "google_flights"}, "best_flights": []}
    app = mcp_apps.build_flights_app(data)
    assert app.title == "Flights dashboard"


@pytest.mark.parametrize(
    "currency,symbol",
    [("GBP", "£"), ("EUR", "€"), ("INR", "₹"), ("JPY", "¥"), (None, "$")],
)
def test_flights_currency_is_consistent_across_dashboard(currency, symbol):
    data = {
        "search_parameters": {
            "engine": "google_flights",
            "departure_id": "COK",
            "arrival_id": "DXB",
        },
        "best_flights": [
            {
                "flights": [
                    {
                        "departure_airport": {"id": "COK", "time": "10:00"},
                        "arrival_airport": {"id": "DXB", "time": "13:00"},
                        "airline": "IndiGo",
                    }
                ],
                "layovers": [],
                "total_duration": 240,
                "price": 35906,
            }
        ],
        "price_insights": {
            "lowest_price": 35758,
            "typical_price_range": [20500, 44000],
            "price_level": "typical",
            "price_history": [[1726358400, 35758]],
        },
    }
    if currency is not None:
        data["search_parameters"]["currency"] = currency
    rows = mcp_apps.flights_rows(data)
    assert rows[0]["price"] == 35906
    assert rows[0]["price_fmt"] == f"{symbol}35,906"
    app = mcp_apps.build_flights_app(data)
    body = ui_json(app)
    assert f"{symbol}35,758" in body
    assert f"{symbol}20,500" in body
    assert f"Price ({symbol})" in body
    assert f'"format": "currency:{currency or "USD"}"' in body


async def test_search_dashboard_dispatches_to_flights(monkeypatch):
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, lambda params: serp_results(_SAMPLE_FLIGHTS_PAYLOAD))
    app = await mcp_apps.search_dashboard(
        params={"engine": "google_flights", "departure_id": "SFO", "arrival_id": "JFK"}
    )
    assert "SFO → JFK" in app.title
    body = ui_json(app)
    assert "AreaChart" in body


async def test_search_dashboard_falls_back_to_generic(monkeypatch):
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, lambda params: serp_results(_SAMPLE_PAYLOAD))
    app = await mcp_apps.search_dashboard(params={"q": "coffee"})
    assert app.title == "Search dashboard"


# --- MCP Apps: Jobs-specific helpers and builder ----------------------------

_SAMPLE_JOBS_PAYLOAD = {
    "search_parameters": {
        "engine": "google_jobs",
        "q": "software engineer",
    },
    "jobs_results": [
        {
            "title": "Senior Software Engineer",
            "company_name": "Acme Corp",
            "location": "Austin, TX",
            "via": "LinkedIn",
            "extensions": [
                "3 days ago",
                "120K–160K a year",
                "Full-time",
                "Health insurance",
                "Dental insurance",
                "Paid time off",
            ],
            "detected_extensions": {
                "posted_at": "3 days ago",
                "salary": "120K–160K a year",
                "schedule_type": "Full-time",
            },
            "description": "We are looking for a senior engineer to join our platform team and build scalable distributed systems.",
            "job_highlights": [
                {
                    "title": "Qualifications",
                    "items": ["5+ years experience", "Python or Go proficiency"],
                },
                {
                    "title": "Benefits",
                    "items": ["Health insurance", "401(k) matching", "Remote-friendly"],
                },
            ],
            "apply_options": [
                {"title": "LinkedIn", "link": "https://linkedin.com/jobs/123"},
                {"title": "Indeed", "link": "https://indeed.com/jobs/456"},
            ],
            "source_link": "https://acme.com/careers/senior-swe",
        },
        {
            "title": "Frontend Developer",
            "company_name": "StartupCo",
            "location": "Remote",
            "via": "Indeed",
            "extensions": ["1 day ago", "Work from home", "Contract"],
            "detected_extensions": {
                "posted_at": "1 day ago",
                "schedule_type": "Contract",
                "work_from_home": True,
            },
            "description": "Build beautiful user interfaces with React and TypeScript.",
            "job_highlights": [],
            "apply_options": [],
            "source_link": "",
        },
        {
            "title": "Junior Developer",
            "company_name": "BigTech",
            "location": "San Francisco, CA",
            "via": "Glassdoor",
            "extensions": ["5 days ago", "Full-time", "No degree mentioned"],
            "detected_extensions": {
                "posted_at": "5 days ago",
                "schedule_type": "Full-time",
                "qualifications": "No degree mentioned",
            },
            "description": "Entry-level position for new graduates.",
        },
    ],
}


def test_jobs_rows_extracts_all_jobs():
    rows = mcp_apps.jobs_rows(_SAMPLE_JOBS_PAYLOAD)
    assert len(rows) == 3

    # Rich job with salary and benefits
    assert rows[0]["title"] == "Senior Software Engineer"
    assert rows[0]["company"] == "Acme Corp"
    assert rows[0]["location"] == "Austin, TX"
    assert rows[0]["salary"] == "120K–160K a year"
    assert rows[0]["schedule"] == "Full-time"
    assert rows[0]["posted"] == "3 days ago"
    assert rows[0]["work_from_home"] is False
    assert "Health insurance" in rows[0]["benefits"]
    assert "Dental insurance" in rows[0]["benefits"]
    assert "Paid time off" in rows[0]["benefits"]
    assert (
        rows[0]["benefits_fmt"] == "Health insurance, Dental insurance, Paid time off"
    )
    assert rows[0]["source_link"] == "https://acme.com/careers/senior-swe"
    assert len(rows[0]["highlights"]) == 2
    assert len(rows[0]["apply_options"]) == 2

    # Remote job
    assert rows[1]["work_from_home"] is True
    assert rows[1]["salary"] == ""
    assert rows[1]["benefits_fmt"] == "—"

    # Minimal job (no highlights, no apply_options keys)
    assert rows[2]["qualifications"] == "No degree mentioned"
    assert rows[2]["highlights"] == []
    assert rows[2]["apply_options"] == []


def test_jobs_rows_handles_empty_data():
    assert mcp_apps.jobs_rows({}) == []
    assert mcp_apps.jobs_rows({"jobs_results": None}) == []


def test_jobs_rows_handles_missing_extensions():
    data = {
        "jobs_results": [
            {
                "title": "Intern",
                "company_name": "Small Co",
                "location": "Remote",
            }
        ]
    }
    rows = mcp_apps.jobs_rows(data)
    assert len(rows) == 1
    assert rows[0]["salary"] == ""
    assert rows[0]["schedule"] == ""
    assert rows[0]["posted"] == ""
    assert rows[0]["benefits"] == []
    assert rows[0]["description"] == ""


def test_jobs_summary_computes_metrics():
    summary = mcp_apps.jobs_summary(_SAMPLE_JOBS_PAYLOAD)
    assert summary["total"] == 3
    assert summary["with_salary"] == 1
    assert summary["remote"] == 1
    assert summary["salary_pct"] == "33%"
    assert summary["remote_pct"] == "33%"
    assert len(summary["rows"]) == 3
    # Schedule breakdown for pie chart
    breakdown = summary["schedule_breakdown"]
    assert len(breakdown) == 2
    assert {"schedule": "Full-time", "count": 2} in breakdown
    assert {"schedule": "Contract", "count": 1} in breakdown


def test_jobs_summary_handles_empty():
    summary = mcp_apps.jobs_summary({})
    assert summary["total"] == 0
    assert summary["salary_pct"] == "—"
    assert summary["remote_pct"] == "—"
    assert summary["schedule_breakdown"] == []


def test_jobs_schedule_breakdown_groups_unspecified():
    rows = [{"schedule": ""}, {"schedule": ""}, {"schedule": "Full-time"}]
    breakdown = mcp_apps.jobs_schedule_breakdown(rows)
    assert {"schedule": "Unspecified", "count": 2} in breakdown
    assert {"schedule": "Full-time", "count": 1} in breakdown


def test_build_jobs_app_produces_valid_app():
    app = mcp_apps.build_jobs_app(_SAMPLE_JOBS_PAYLOAD)
    assert app.title == "Jobs: software engineer"
    assert app.state == {"selected": None}
    body = ui_json(app)
    assert "PieChart" in body
    assert "DataTable" in body
    assert "Senior Software Engineer" in body
    assert "Acme Corp" in body
    assert "120K" in body
    # Detail panel elements
    assert "selected.salary" in body
    assert "selected.title" in body
    assert "source_link" in body


def test_build_jobs_app_without_query():
    data = {"search_parameters": {"engine": "google_jobs"}, "jobs_results": []}
    app = mcp_apps.build_jobs_app(data)
    assert app.title == "Jobs dashboard"


def test_build_jobs_app_description_truncated():
    long_desc = "x" * 500
    data = {
        "search_parameters": {"q": "test"},
        "jobs_results": [
            {
                "title": "Role",
                "company_name": "Co",
                "location": "NYC",
                "description": long_desc,
            }
        ],
    }
    rows = mcp_apps.jobs_rows(data)
    assert len(rows[0]["description"]) == 300


async def test_search_dashboard_dispatches_to_jobs(monkeypatch):
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, lambda params: serp_results(_SAMPLE_JOBS_PAYLOAD))
    app = await mcp_apps.search_dashboard(
        params={"engine": "google_jobs", "q": "software engineer"}
    )
    assert "software engineer" in app.title
    body = ui_json(app)
    assert "Senior Software Engineer" in body
    assert "DataTable" in body


# --- MCP Apps: Shopping-specific helpers and builder ------------------------

_SAMPLE_SHOPPING_PAYLOAD = {
    "search_parameters": {
        "engine": "google_shopping",
        "q": "Sony WH-1000XM5",
    },
    "shopping_results": [
        {
            "position": 1,
            "title": "Sony WH-1000XM5 Wireless Headphones",
            "source": "Best Buy",
            "price": "$278.00",
            "extracted_price": 278.0,
            "old_price": "$398",
            "extracted_old_price": 398,
            "rating": 4.6,
            "reviews": 26000,
            "snippet": "Good sound quality",
            "extensions": ["30% OFF", "Nearby, 11 mi"],
            "product_link": "https://google.com/shopping/product/123",
        },
        {
            "position": 2,
            "title": "Sony WH-1000XM5 Wireless Headphones",
            "source": "Amazon",
            "price": "$298.00",
            "extracted_price": 298.0,
            "rating": 4.7,
            "reviews": 45000,
            "snippet": "Comfortable fit",
            "product_link": "https://google.com/shopping/product/456",
        },
        {
            "position": 3,
            "title": "Sony WH-1000XM5 Wireless Headphones - Black",
            "source": "Walmart",
            "price": "$249.99",
            "extracted_price": 249.99,
            "old_price": "$349.99",
            "extracted_old_price": 349.99,
            "rating": 4.5,
            "reviews": 8200,
            "extensions": ["29% OFF"],
            "product_link": "",
        },
        {
            "position": 4,
            "title": "Sony WH-1000XM5 Refurbished",
            "source": "eBay",
            "price": "$189.00",
            "extracted_price": 189.0,
            "rating": 0,
            "reviews": 0,
            "product_link": "https://google.com/shopping/product/789",
        },
    ],
}


def test_shopping_rows_extracts_all_products():
    rows = mcp_apps.shopping_rows(_SAMPLE_SHOPPING_PAYLOAD)
    assert len(rows) == 4

    # Product with discount
    assert rows[0]["title"] == "Sony WH-1000XM5 Wireless Headphones"
    assert rows[0]["source"] == "Best Buy"
    assert rows[0]["price"] == 278.0
    assert rows[0]["price_fmt"] == "$278.00"
    assert rows[0]["old_price_fmt"] == "$398"
    assert rows[0]["discount"] == "30% OFF"
    assert rows[0]["rating"] == 4.6
    assert rows[0]["reviews"] == 26000
    assert rows[0]["snippet"] == "Good sound quality"

    # Product without discount
    assert rows[1]["source"] == "Amazon"
    assert rows[1]["old_price_fmt"] == ""
    assert rows[1]["discount"] == ""

    # Product with no rating
    assert rows[3]["rating"] == 0
    assert rows[3]["reviews"] == 0


def test_shopping_rows_handles_empty():
    assert mcp_apps.shopping_rows({}) == []
    assert mcp_apps.shopping_rows({"shopping_results": None}) == []


def test_shopping_rows_handles_missing_fields():
    data = {"shopping_results": [{"title": "Widget", "source": "Store"}]}
    rows = mcp_apps.shopping_rows(data)
    assert rows[0]["price"] == 0
    assert rows[0]["price_fmt"] == "—"
    assert rows[0]["rating"] == 0
    assert rows[0]["discount"] == ""


def test_shopping_summary_computes_metrics():
    summary = mcp_apps.shopping_summary(_SAMPLE_SHOPPING_PAYLOAD)
    assert summary["total"] == 4
    assert summary["price_min"] == 189.0
    assert summary["price_max"] == 298.0
    assert summary["on_sale"] == 2
    assert summary["avg_rating"] == 4.6  # (4.6+4.7+4.5)/3 rounded
    assert len(summary["price_chart"]) == 4
    # Chart sorted by cheapest first
    assert summary["price_chart"][0]["source"] == "eBay"
    assert summary["price_chart"][0]["price"] == 189.0


def test_shopping_summary_handles_empty():
    summary = mcp_apps.shopping_summary({})
    assert summary["total"] == 0
    assert summary["price_min"] == 0
    assert summary["price_max"] == 0
    assert summary["price_chart"] == []


def test_build_shopping_app_produces_valid_app():
    app = mcp_apps.build_shopping_app(_SAMPLE_SHOPPING_PAYLOAD)
    assert app.title == "Shopping: Sony WH-1000XM5"
    assert app.state == {"selected": None}
    body = ui_json(app)
    assert "BarChart" in body
    assert "DataTable" in body
    assert "Best Buy" in body
    assert "278" in body
    # Detail panel
    assert "selected.discount" in body
    assert "selected.product_link" in body


def test_build_shopping_app_without_query():
    data = {"search_parameters": {"engine": "google_shopping"}, "shopping_results": []}
    app = mcp_apps.build_shopping_app(data)
    assert app.title == "Shopping dashboard"


def test_build_shopping_app_no_chart_without_prices():
    data = {
        "search_parameters": {"q": "test"},
        "shopping_results": [{"title": "Free thing", "source": "Store"}],
    }
    app = mcp_apps.build_shopping_app(data)
    body = ui_json(app)
    assert "BarChart" not in body
    assert "DataTable" in body


def test_shopping_currency_inr():
    """Shopping with INR prices should show ₹ in metrics, not $."""
    data = {
        "search_parameters": {"q": "headphones", "gl": "in"},
        "shopping_results": [
            {
                "title": "Sony WH-1000XM5",
                "source": "Amazon India",
                "price": "₹24,990",
                "extracted_price": 24990,
                "rating": 4.5,
                "reviews": 100,
            },
            {
                "title": "Sony WH-1000XM4",
                "source": "Flipkart",
                "price": "₹19,990",
                "extracted_price": 19990,
                "rating": 4.4,
                "reviews": 200,
            },
        ],
    }
    summary = mcp_apps.shopping_summary(data)
    assert summary["currency_symbol"] == "₹"

    app = mcp_apps.build_shopping_app(data)
    body = ui_json(app)
    assert "₹19,990" in body
    assert "₹24,990" in body
    import re

    assert not re.search(r"\$\d", body)


def test_extract_currency_prefix_various():
    assert (
        mcp_apps._extract_currency_prefix({"shopping_results": [{"price": "$99.00"}]})
        == "$"
    )
    assert (
        mcp_apps._extract_currency_prefix({"shopping_results": [{"price": "₹6,999"}]})
        == "₹"
    )
    assert (
        mcp_apps._extract_currency_prefix({"shopping_results": [{"price": "€49.99"}]})
        == "€"
    )
    assert (
        mcp_apps._extract_currency_prefix({"shopping_results": [{"price": "R$150"}]})
        == "R$"
    )
    assert mcp_apps._extract_currency_prefix({}) == "$"


async def test_search_dashboard_dispatches_to_shopping(monkeypatch):
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, lambda params: serp_results(_SAMPLE_SHOPPING_PAYLOAD))
    app = await mcp_apps.search_dashboard(
        params={"engine": "google_shopping", "q": "Sony WH-1000XM5"}
    )
    assert "Sony WH-1000XM5" in app.title
    body = ui_json(app)
    assert "BarChart" in body
    assert "Best Buy" in body
