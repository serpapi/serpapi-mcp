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
    out = server.map_search_error(make_serpapi_http_error(status, {"error": "x"}))
    assert out.startswith("Error:")
    assert fragment in out


def test_map_search_error_falls_back_to_json_body():
    out = server.map_search_error(
        make_serpapi_http_error(500, {"error": "server boom"})
    )
    assert "server boom" in out


def test_map_search_error_handles_generic_exception():
    assert server.map_search_error(ValueError("weird")) == "Error: weird"


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
    rows = server.organic_rows(data)
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
    assert server.organic_rows(data)[0]["source"] == "example.com"


def test_organic_rows_empty_without_results():
    assert server.organic_rows({}) == []
    assert server.organic_rows({"organic_results": None}) == []


def test_source_breakdown_counts_and_limits():
    rows = [{"source": "a"}, {"source": "a"}, {"source": "b"}, {"source": ""}]
    breakdown = server.source_breakdown(rows, limit=1)
    assert breakdown == [{"source": "a", "count": 2}]


def test_dashboard_summary_shape():
    data = {
        "search_parameters": {"q": "coffee", "engine": "google_light"},
        "search_information": {"total_results": 999},
        "organic_results": [{"title": "A", "link": "https://a.com", "source": "A"}],
    }
    summary = server.dashboard_summary(data)
    assert summary["query"] == "coffee"
    assert summary["engine"] == "google_light"
    assert summary["total_results"] == 999
    assert summary["result_count"] == 1
    assert summary["sources"] == [{"source": "A", "count": 1}]


# --- MCP Apps: tool behavior -----------------------------------------------


def ui_json(app):
    """Serialize a Prefab app via its canonical serializer for assertions."""
    return json.dumps(app.to_json())


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
    app = await server.search_table(params={"q": "coffee"})
    assert app.title == "Search results"
    body = ui_json(app)
    assert "DataTable" in body
    assert "Best Coffee" in body


async def test_search_dashboard_returns_dashboard_app(monkeypatch):
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, lambda params: serp_results(_SAMPLE_PAYLOAD))
    app = await server.search_dashboard(params={"q": "coffee"})
    assert app.title == "Search dashboard"
    # click-to-expand detail panel starts collapsed.
    assert app.state == {"selected": None}
    body = ui_json(app)
    assert "Best Coffee" in body
    assert "coffee" in body  # query surfaced in a metric


async def test_search_table_without_api_key_renders_error_app(monkeypatch):
    use_request(monkeypatch, real_request(state={}))
    app = await server.search_table(params={"q": "x"})
    assert app.title == "Search error"
    assert "Unable to access API key" in ui_json(app)


async def test_search_dashboard_maps_http_error_to_error_app(monkeypatch):
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, raiser(make_serpapi_http_error(429, {"error": "x"})))
    app = await server.search_dashboard(params={"q": "x"})
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
    rows = server.flights_rows(_SAMPLE_FLIGHTS_PAYLOAD)
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
    assert server.flights_rows({}) == []
    assert server.flights_rows({"best_flights": None, "other_flights": None}) == []


def test_flights_rows_handles_missing_airports():
    data = {
        "best_flights": [
            {"flights": [], "layovers": [], "total_duration": 0, "price": 100}
        ]
    }
    rows = server.flights_rows(data)
    assert len(rows) == 1
    assert rows[0]["route"] == "? → ?"
    assert rows[0]["price"] == 100
    assert rows[0]["departure"] == ""
    assert rows[0]["arrival"] == ""


def test_flights_rows_zero_price_defaults():
    data = {"best_flights": [{"flights": [], "layovers": [], "total_duration": 120}]}
    rows = server.flights_rows(data)
    assert rows[0]["price"] == 0
    assert rows[0]["price_fmt"] == "—"


def test_format_duration():
    assert server._format_duration(330) == "5h 30m"
    assert server._format_duration(45) == "45m"
    assert server._format_duration(60) == "1h 0m"
    assert server._format_duration(None) == "—"
    assert server._format_duration(0) == "—"


def test_price_history_points_converts_timestamps():
    points = server.price_history_points(_SAMPLE_FLIGHTS_PAYLOAD)
    assert len(points) == 5
    assert points[0]["price"] == 310
    assert "date" in points[0]
    # Dates should be human-readable month/day format
    assert len(points[0]["date"]) > 0


def test_price_history_points_handles_empty():
    assert server.price_history_points({}) == []
    assert server.price_history_points({"price_insights": {}}) == []
    assert server.price_history_points({"price_insights": {"price_history": []}}) == []


def test_price_history_points_skips_malformed_entries():
    data = {
        "price_insights": {"price_history": [[1719792000], "bad", [1719878400, 300]]}
    }
    points = server.price_history_points(data)
    assert len(points) == 1
    assert points[0]["price"] == 300


def test_flights_price_insights_extracts_metrics():
    insights = server.flights_price_insights(_SAMPLE_FLIGHTS_PAYLOAD)
    assert insights["lowest_price"] == 199
    assert insights["price_level"] == "low"
    assert insights["typical_low"] == 250
    assert insights["typical_high"] == 420


def test_flights_price_insights_handles_missing():
    insights = server.flights_price_insights({})
    assert insights["lowest_price"] is None
    assert insights["price_level"] == "unknown"
    assert insights["typical_low"] is None
    assert insights["typical_high"] is None


def test_build_flights_app_produces_valid_app():
    app = server.build_flights_app(_SAMPLE_FLIGHTS_PAYLOAD)
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
    app = server.build_flights_app(data)
    body = ui_json(app)
    # Should still render table without crashing, just no chart
    assert "DataTable" in body
    assert "AreaChart" not in body


def test_build_flights_app_generic_title_without_route():
    data = {"search_parameters": {"engine": "google_flights"}, "best_flights": []}
    app = server.build_flights_app(data)
    assert app.title == "Flights dashboard"


async def test_search_dashboard_dispatches_to_flights(monkeypatch):
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, lambda params: serp_results(_SAMPLE_FLIGHTS_PAYLOAD))
    app = await server.search_dashboard(
        params={"engine": "google_flights", "departure_id": "SFO", "arrival_id": "JFK"}
    )
    assert "SFO → JFK" in app.title
    body = ui_json(app)
    assert "AreaChart" in body


async def test_search_dashboard_falls_back_to_generic(monkeypatch):
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, lambda params: serp_results(_SAMPLE_PAYLOAD))
    app = await server.search_dashboard(params={"q": "coffee"})
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
    rows = server.jobs_rows(_SAMPLE_JOBS_PAYLOAD)
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
    assert server.jobs_rows({}) == []
    assert server.jobs_rows({"jobs_results": None}) == []


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
    rows = server.jobs_rows(data)
    assert len(rows) == 1
    assert rows[0]["salary"] == ""
    assert rows[0]["schedule"] == ""
    assert rows[0]["posted"] == ""
    assert rows[0]["benefits"] == []
    assert rows[0]["description"] == ""


def test_jobs_summary_computes_metrics():
    summary = server.jobs_summary(_SAMPLE_JOBS_PAYLOAD)
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
    summary = server.jobs_summary({})
    assert summary["total"] == 0
    assert summary["salary_pct"] == "—"
    assert summary["remote_pct"] == "—"
    assert summary["schedule_breakdown"] == []


def test_jobs_schedule_breakdown_groups_unspecified():
    rows = [{"schedule": ""}, {"schedule": ""}, {"schedule": "Full-time"}]
    breakdown = server.jobs_schedule_breakdown(rows)
    assert {"schedule": "Unspecified", "count": 2} in breakdown
    assert {"schedule": "Full-time", "count": 1} in breakdown


def test_build_jobs_app_produces_valid_app():
    app = server.build_jobs_app(_SAMPLE_JOBS_PAYLOAD)
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
    app = server.build_jobs_app(data)
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
    app = server.build_jobs_app(data)
    rows = server.jobs_rows(data)
    assert len(rows[0]["description"]) == 300


async def test_search_dashboard_dispatches_to_jobs(monkeypatch):
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, lambda params: serp_results(_SAMPLE_JOBS_PAYLOAD))
    app = await server.search_dashboard(
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
    rows = server.shopping_rows(_SAMPLE_SHOPPING_PAYLOAD)
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
    assert server.shopping_rows({}) == []
    assert server.shopping_rows({"shopping_results": None}) == []


def test_shopping_rows_handles_missing_fields():
    data = {"shopping_results": [{"title": "Widget", "source": "Store"}]}
    rows = server.shopping_rows(data)
    assert rows[0]["price"] == 0
    assert rows[0]["price_fmt"] == "—"
    assert rows[0]["rating"] == 0
    assert rows[0]["discount"] == ""


def test_shopping_summary_computes_metrics():
    summary = server.shopping_summary(_SAMPLE_SHOPPING_PAYLOAD)
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
    summary = server.shopping_summary({})
    assert summary["total"] == 0
    assert summary["price_min"] == 0
    assert summary["price_max"] == 0
    assert summary["price_chart"] == []


def test_build_shopping_app_produces_valid_app():
    app = server.build_shopping_app(_SAMPLE_SHOPPING_PAYLOAD)
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
    app = server.build_shopping_app(data)
    assert app.title == "Shopping dashboard"


def test_build_shopping_app_no_chart_without_prices():
    data = {
        "search_parameters": {"q": "test"},
        "shopping_results": [{"title": "Free thing", "source": "Store"}],
    }
    app = server.build_shopping_app(data)
    body = ui_json(app)
    assert "BarChart" not in body
    assert "DataTable" in body


async def test_search_dashboard_dispatches_to_shopping(monkeypatch):
    use_request(monkeypatch, real_request(state={"api_key": "KEY"}))
    use_search(monkeypatch, lambda params: serp_results(_SAMPLE_SHOPPING_PAYLOAD))
    app = await server.search_dashboard(
        params={"engine": "google_shopping", "q": "Sony WH-1000XM5"}
    )
    assert "Sony WH-1000XM5" in app.title
    body = ui_json(app)
    assert "BarChart" in body
    assert "Best Buy" in body
