"""Offline unit tests for src/server.py.

The SerpApi client and the HTTP request are built from the real library types
(serpapi.SerpResults, a real requests/serpapi HTTPError, a real starlette
Request), so the suite pins the actual library contract without a network call
or an API key.
"""

import json

import pytest
import requests
import serpapi
from serpapi.models import SerpResults
from starlette.requests import Request

import src.server as server


def make_serpapi_http_error(
    status, body, reason="Error", url="https://serpapi.com/search?q=x"
):
    """Build the exception the way the serpapi client does: a requests HTTPError
    from raise_for_status(), wrapped in serpapi's HTTPError. The wrapper's own
    .response is None; the body lives on args[0].response."""
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


def use_request(monkeypatch, request):
    monkeypatch.setattr(server, "get_http_request", lambda: request)


def use_search(monkeypatch, fn):
    monkeypatch.setattr(server.serpapi, "search", fn)


def raiser(exc):
    def _search(params):
        raise exc

    return _search


class _Wrap(Exception):
    """An exception whose single arg is its inner cause — mirrors how serpapi
    wraps a requests error in args[0]. extract_error_response walks this chain."""


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


def test_extract_error_response_reads_json_body_from_wrapped_request_error():
    err = make_serpapi_http_error(400, {"error": "Invalid API key."})
    assert (
        err.response is None
    )  # the wrapper has no response; the body is one level down
    assert json.loads(server.extract_error_response(err)) == {
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
    assert server.extract_error_response(err) == "upstream boom"


def test_extract_error_response_falls_back_to_str():
    assert server.extract_error_response(ValueError("plain message")) == "plain message"


def test_extract_error_response_terminates_and_returns_innermost_message():
    err = ValueError("deepest")
    for _ in range(20):
        err = ValueError(err)
    # 20 levels deep with no .response anywhere: the walk must terminate (not
    # hang) and fall back to the chain's message string.
    assert server.extract_error_response(err) == "deepest"


def test_extract_error_response_finds_response_at_depth_cap_boundary():
    leaf = _WithResponse(_Resp({"error": "deep"}))
    # index 9 is the last position the depth cap (10) still inspects.
    err = nest(9, leaf)
    assert json.loads(server.extract_error_response(err)) == {"error": "deep"}


def test_extract_error_response_stops_one_past_the_depth_cap():
    leaf = _WithResponse(_Resp({"error": "too deep"}))
    # index 10 is one past the cap: the body must never be reached.
    err = nest(10, leaf)
    out = server.extract_error_response(err)
    assert "too deep" not in out  # cap enforced, not just "returns a string"
    assert out == "boom"  # falls back to str() of the chain


async def test_search_rejects_invalid_mode():
    out = await server.search(params={"q": "x"}, mode="bogus")
    assert out == "Error: Invalid mode. Must be 'complete' or 'compact'"


async def test_search_without_api_key_returns_graceful_error(monkeypatch):
    # A real starlette Request with empty state: request.state.api_key would raise
    # AttributeError, so the guard must use getattr, not attribute access.
    use_request(monkeypatch, real_request(state={}))
    out = await server.search(params={"q": "x"})
    assert out == "Error: Unable to access API key from request context"


async def test_search_complete_returns_full_payload(monkeypatch):
    payload = {"search_metadata": {"id": "1"}, "organic_results": [{"title": "hit"}]}
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, lambda params: serp_results(payload))
    assert json.loads(await server.search(params={"q": "x"})) == payload


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
    out = json.loads(await server.search(params={"q": "x"}, mode="compact"))
    assert out == {"organic_results": [{"title": "hit"}]}


async def test_search_compact_does_not_mutate_the_live_result(monkeypatch):
    payload = {"search_metadata": {"id": "1"}, "organic_results": [{"title": "hit"}]}
    results = serp_results(payload)
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, lambda params: results)
    await server.search(params={"q": "x"}, mode="compact")
    assert "search_metadata" in results.as_dict()


async def test_search_forwards_api_key_and_default_engine(monkeypatch):
    captured = {}

    def capture(params):
        captured.update(params)
        return serp_results({"ok": True})

    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, capture)
    await server.search(params={"q": "x"})
    assert captured["api_key"] == "KEY"
    assert captured["engine"] == "google_light"
    assert captured["q"] == "x"


async def test_search_caller_overrides_default_engine(monkeypatch):
    captured = {}

    def capture(params):
        captured.update(params)
        return serp_results({})

    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, capture)
    await server.search(params={"q": "x", "engine": "google_news"})
    assert captured["engine"] == "google_news"


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
    out = await server.search(params={"q": "x"})
    assert out.startswith("Error:")
    assert fragment in out


async def test_search_unmapped_http_error_returns_json_body(monkeypatch):
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(
        monkeypatch, raiser(make_serpapi_http_error(500, {"error": "server boom"}))
    )
    out = await server.search(params={"q": "x"})
    assert out.startswith("Error:")
    assert "server boom" in out


async def test_search_generic_exception_uses_extractor(monkeypatch):
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, raiser(ValueError("weird failure")))
    assert await server.search(params={"q": "x"}) == "Error: weird failure"


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
