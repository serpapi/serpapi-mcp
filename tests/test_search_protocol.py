"""Exercise search results, guided input, and completion through real MCP clients."""

import json
from contextlib import AsyncExitStack

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.elicitation import ElicitResult
from fastmcp.exceptions import ToolError
from mcp.types import ResourceTemplateReference
from serpapi.models import SerpResults
from starlette.middleware import Middleware

import src.mcp_components.resources as resources
import src.mcp_components.tools as tools
import src.engine_input_rules as input_rules
from src.server import ApiKeyMiddleware, mcp


FLIGHT_PARAMS = {
    "engine": "google_flights",
    "type": 2,
    "departure_id": "LHR",
    "arrival_id": "NRT",
    "outbound_date": "2030-10-01",
}
HOTEL_PARAMS = {
    "engine": "google_hotels",
    "q": "Bali resorts",
    "check_in_date": "2030-10-01",
    "check_out_date": "2030-10-08",
}


@pytest.fixture
def upstream(monkeypatch):
    calls = []
    monkeypatch.setenv("SERPAPI_API_KEY", "TEST_KEY")

    def search(params):
        calls.append(dict(params))
        return SerpResults({"best_flights": [{"price": 400}]}, client=None)

    monkeypatch.setattr(tools.serpapi, "search", search)
    return calls


@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize(
    "engine,payload",
    [
        ("google_light", {"organic_results": [{"title": "Coffee"}]}),
        ("google_jobs", {"jobs_results": [{"title": "Engineer"}]}),
    ],
)
async def test_json_results_preserve_the_string_wrapper_and_text(
    monkeypatch, upstream, mode, engine, payload
):
    monkeypatch.setattr(
        tools.serpapi, "search", lambda params: SerpResults(payload, client=None)
    )
    async with Client(mcp, mode=mode) as client:
        result = await client.call_tool_mcp(
            "search", {"params": {"engine": engine, "q": "x"}}
        )
    assert not result.is_error
    assert result.structured_content == {"result": result.content[0].text}
    assert json.loads(result.structured_content["result"]) == payload


@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("output", ["json", "md"])
async def test_search_matches_original_string_tool_contract(
    monkeypatch, upstream, mode, output
):
    payload = {"organic_results": [{"title": "Café"}], "result": {"nested": True}}
    text = (
        json.dumps(payload, indent=2, ensure_ascii=False)
        if output == "json"
        else "## Café\n\n- Coffee\n"
    )
    monkeypatch.setattr(
        tools.serpapi,
        "search",
        lambda params: SerpResults(payload, client=None) if output == "json" else text,
    )
    reference = FastMCP("Response compatibility")

    @reference.tool
    async def previous_search() -> str:
        return text

    reference.add_tool(await mcp.get_tool("search"))
    async with Client(reference, mode=mode) as client:
        schemas = {tool.name: tool.output_schema for tool in await client.list_tools()}
        original = await client.call_tool_mcp("previous_search", {})
        args = {"params": {"q": "coffee", "output": output}}
        actual = await client.call_tool_mcp("search", args)
        parsed = await client.call_tool("search", args)
    assert schemas["search"] == schemas["previous_search"]
    assert actual.model_dump(exclude={"meta"}) == original.model_dump(exclude={"meta"})
    assert actual.meta == (
        {key: value for key, value in (original.meta or {}).items() if key != "fastmcp"}
        or None
    )
    assert parsed.data == text
    assert parsed.structured_content == {"result": text}


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_markdown_and_compact_json_preserve_the_string_wrapper(
    monkeypatch, upstream, mode
):
    markdown = "## Results\n\n- Coffee\n"
    payload = {"search_metadata": {"id": "1"}, "organic_results": [{"title": "Coffee"}]}

    def search(params):
        return (
            markdown
            if params.get("output") == "md"
            else SerpResults(payload, client=None)
        )

    monkeypatch.setattr(tools.serpapi, "search", search)
    async with Client(mcp, mode=mode) as client:
        text = await client.call_tool_mcp(
            "search", {"params": {"q": "x", "output": "md"}, "mode": "compact"}
        )
        data = await client.call_tool_mcp(
            "search", {"params": {"q": "x"}, "mode": "compact"}
        )
    assert not text.is_error and not data.is_error
    assert text.content[0].text == markdown
    assert text.structured_content == {"result": markdown}
    assert data.structured_content == {"result": data.content[0].text}
    assert json.loads(data.structured_content["result"]) == {
        "organic_results": [{"title": "Coffee"}]
    }


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_validation_and_api_errors_set_the_tool_error_flag(
    monkeypatch, upstream, mode
):
    async with Client(mcp, mode=mode) as client:
        invalid = await client.call_tool_mcp("search", {"mode": "bogus"})
        assert invalid.is_error and not upstream
        assert "Invalid mode" in invalid.content[0].text
        assert invalid.structured_content == {"result": invalid.content[0].text}
        with pytest.raises(ToolError, match="Invalid mode"):
            await client.call_tool("search", {"mode": "bogus"})
        monkeypatch.setattr(
            tools.serpapi,
            "search",
            lambda params: SerpResults({"error": "Search failed"}, client=None),
        )
        failed = await client.call_tool_mcp("search", {"params": {"q": "x"}})
    assert failed.is_error
    assert failed.content[0].text == "Error: Search failed"
    assert failed.structured_content == {"result": "Error: Search failed"}


@pytest.mark.parametrize("flight_type", [1, "2"])
async def test_guided_flights_only_collect_missing_fields(upstream, flight_type):
    seen = []
    original = {
        "engine": "google_flights",
        "type": flight_type,
        "arrival_id": "NRT",
        "currency": "GBP",
    }

    async def answer(message, response_type, params, context):
        seen.append(params.requested_schema["required"])
        assert not upstream
        return ElicitResult(
            action="accept",
            content={
                "departure_id": "LHR",
                "outbound_date": "2030-10-01",
                "return_date": "2030-10-08",
                "arrival_id": "SFO",
                "api_key": "UNTRUSTED",
                "engine": "google",
                "output": "md",
            },
        )

    async with Client(mcp, elicitation_handler=answer) as client:
        result = await client.call_tool_mcp("search", {"params": original})
    expected_fields = ["departure_id", "outbound_date"] + (
        ["return_date"] if flight_type == 1 else []
    )
    assert seen == [expected_fields]
    assert not result.is_error
    assert result.structured_content == {"result": result.content[0].text}
    assert json.loads(result.structured_content["result"]) == {
        "best_flights": [{"price": 400}]
    }
    assert len(upstream) == 1
    assert upstream[0]["arrival_id"] == "NRT"
    assert upstream[0]["engine"] == "google_flights"
    assert upstream[0]["api_key"] == "TEST_KEY"
    assert upstream[0]["currency"] == "GBP"
    assert ("return_date" in upstream[0]) == (flight_type == 1)
    assert "output" not in upstream[0]
    assert "departure_id" not in original


@pytest.mark.parametrize("action", ["decline", "cancel"])
@pytest.mark.parametrize(
    "engine", ["google_flights", "google_hotels", "google_maps_directions", "youtube"]
)
async def test_cancelled_search_input_never_searches(upstream, action, engine):
    async def answer(*args):
        return ElicitResult(action=action)

    async with Client(mcp, elicitation_handler=answer) as client:
        result = await client.call_tool_mcp("search", {"params": {"engine": engine}})
    assert not result.is_error
    assert "cancelled" in result.content[0].text
    assert result.structured_content == {"result": result.content[0].text}
    assert not upstream


@pytest.mark.parametrize(
    "answer_data,fragment",
    [
        ({}, "Missing Google Flights parameters"),
        ({"outbound_date": "2030-02-30"}, "valid date"),
        ({"outbound_date": 20301001}, "must be a string"),
        ({"outbound_date": "20301001"}, "YYYY-MM-DD"),
    ],
)
async def test_invalid_flight_answers_never_search(upstream, answer_data, fragment):
    original = {k: v for k, v in FLIGHT_PARAMS.items() if k != "outbound_date"}

    async def answer(*args):
        return ElicitResult(action="accept", content=answer_data)

    async with Client(mcp, elicitation_handler=answer) as client:
        result = await client.call_tool_mcp("search", {"params": original})
    assert result.is_error
    assert fragment in result.content[0].text
    assert result.structured_content == {"result": result.content[0].text}
    assert not upstream


@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize(
    "complete_params,missing_name",
    [
        (FLIGHT_PARAMS, "departure_id"),
        (HOTEL_PARAMS, "check_in_date"),
        ({"engine": "youtube", "search_query": "coffee"}, "search_query"),
    ],
)
async def test_clients_without_guided_input_receive_actionable_errors(
    upstream, mode, complete_params, missing_name
):
    async def should_not_ask(*args):
        raise AssertionError(
            "Legacy clients should receive the missing-parameter error"
        )

    async with Client(
        mcp, mode=mode, elicitation_handler=should_not_ask if mode == "legacy" else None
    ) as client:
        result = await client.call_tool_mcp(
            "search", {"params": {"engine": complete_params["engine"]}}
        )
        complete = await client.call_tool_mcp("search", {"params": complete_params})
    assert result.is_error
    assert (
        missing_name in result.content[0].text and "Ask for" in result.content[0].text
    )
    assert not complete.is_error
    assert len(upstream) == 1


@pytest.mark.parametrize(
    "params",
    [
        FLIGHT_PARAMS,
        {
            **FLIGHT_PARAMS,
            "departure_id": "LHR,LGW,/m/04jpl",
            "arrival_id": "/g/11b6bp8ckd",
        },
        {"engine": "google_flights", "booking_token": "booking"},
        {"engine": "google_flights", "departure_token": "departure"},
        {"engine": "google_flights", "selected_flights_json": '{"outbound":[]}'},
        {"engine": "google_flights", "type": 3, "multi_city_json": "[]"},
        {"engine": "google_light", "q": "coffee"},
        HOTEL_PARAMS,
        {**HOTEL_PARAMS, "property_token": "property"},
        {
            "engine": "google_maps_directions",
            "start_coords": "30.19,-97.66",
            "end_data_id": "place",
        },
        {"engine": "amazon", "node": "6563140011"},
        {"engine": "ebay", "category_id": "1"},
        {"engine": "walmart", "cat_id": "1"},
        {"engine": "google_scholar", "cites": "123"},
        {"engine": "google_scholar", "cluster": "123"},
        {"engine": "google_shopping", "shoprs": "filter"},
        {"engine": "yandex_images", "url": "https://example.com/image.jpg"},
        {"engine": "google_lens", "url": "https://example.com/image.jpg"},
        {"engine": "google_lens", "image_id": "image"},
        {"engine": "google_play_product", "product_id": "com.example.app"},
        {"engine": "google_trends_trending_now"},
        {"engine": "google_maps", "place_id": "place"},
        {"engine": "google_maps", "data_cid": "123"},
        {"engine": "bing_maps", "place_id": "place"},
        {"engine": "google_news"},
        {"engine": "future_engine", "future_parameter": "value"},
    ],
)
async def test_complete_and_alternate_searches_do_not_prompt(upstream, params):
    async def should_not_ask(*args):
        raise AssertionError("This search should not request input")

    async with Client(mcp, elicitation_handler=should_not_ask) as client:
        result = await client.call_tool_mcp("search", {"params": params})
    assert not result.is_error
    assert len(upstream) == 1


@pytest.mark.parametrize(
    "params,fragment",
    [
        ({**FLIGHT_PARAMS, "type": 1, "return_date": "2030-09-01"}, "on or after"),
        ({**FLIGHT_PARAMS, "departure_id": "London"}, "airport codes"),
    ],
)
async def test_invalid_supplied_flight_fields_never_search(upstream, params, fragment):
    async with Client(mcp) as client:
        result = await client.call_tool_mcp("search", {"params": params})
    assert result.is_error and fragment in result.content[0].text
    assert not upstream


@pytest.mark.parametrize(
    "original,answers",
    [
        (
            {
                "engine": "google_hotels",
                "q": "Bali resorts",
                "adults": 4,
                "children": 0,
                "currency": "GBP",
            },
            {"check_in_date": "2030-10-01", "check_out_date": "2030-10-08"},
        ),
        (
            {
                "engine": "google_hotels",
                "check_in_date": "2030-10-01",
                "check_out_date": "2030-10-08",
            },
            {"q": "Bali resorts"},
        ),
        (
            {
                "engine": "google_maps_directions",
                "start_coords": "30.19,-97.66",
                "travel_mode": 0,
            },
            {"end_addr": "Austin city hall"},
        ),
        (
            {"engine": "google_maps_directions", "end_data_id": "place"},
            {"start_addr": "Austin airport"},
        ),
        (
            {"engine": "google_maps_directions"},
            {"start_addr": "Austin airport", "end_addr": "Austin city hall"},
        ),
        ({"engine": "youtube"}, {"search_query": "coffee"}),
        ({"engine": "amazon"}, {"k": "coffee"}),
        ({"engine": "yelp", "find_desc": "coffee"}, {"find_loc": "Austin, TX"}),
        ({"engine": "google_sports", "kgmid": "/m/123"}, {"sp": "ft", "type": "team"}),
        ({"engine": "google_maps", "type": "search"}, {"q": "coffee"}),
        ({"engine": "google_maps", "type": "place"}, {"data": "place data"}),
        ({}, {"q": "coffee"}),
    ],
)
async def test_guided_search_uses_engine_requirements_and_preserves_arguments(
    upstream, original, answers
):
    prompts = []
    original_copy = dict(original)

    async def answer(message, response_type, params, context):
        prompts.append(params.requested_schema)
        assert set(params.requested_schema["required"]) == set(answers)
        assert not upstream
        assert "TEST_KEY" not in json.dumps(params.requested_schema)
        return ElicitResult(
            action="accept",
            content={
                **original,
                "api_key": "UNTRUSTED",
                "engine": "google",
                "output": "md",
                **answers,
            },
        )

    async with Client(mcp, elicitation_handler=answer) as client:
        result = await client.call_tool_mcp("search", {"params": original})
    assert not result.is_error
    assert len(prompts) == 1
    assert original == original_copy
    assert upstream == [
        {"engine": "google_light", **original, **answers, "api_key": "TEST_KEY"}
    ]


@pytest.mark.parametrize(
    "date_value", ["2030-09-30", "2030-10-01", "2030-02-30", "20301008", 20301008]
)
@pytest.mark.parametrize("via_form", [True, False])
async def test_invalid_hotel_dates_never_search(upstream, date_value, via_form):
    original = {k: v for k, v in HOTEL_PARAMS.items() if k != "check_out_date"}

    async def answer(*args):
        return ElicitResult(action="accept", content={"check_out_date": date_value})

    if not via_form:
        original["check_out_date"] = date_value
    async with Client(mcp, elicitation_handler=answer) as client:
        result = await client.call_tool_mcp("search", {"params": original})
    assert result.is_error
    assert "check_out_date" in result.content[0].text
    assert not upstream


@pytest.mark.parametrize("answers", [{}, {"sp": "invented"}, {"sp": 0}])
async def test_missing_or_invalid_catalog_answers_never_search(upstream, answers):
    async def answer(*args):
        return ElicitResult(action="accept", content=answers)

    async with Client(mcp, elicitation_handler=answer) as client:
        result = await client.call_tool_mcp(
            "search",
            {"params": {"engine": "google_sports", "kgmid": "/m/123", "type": "team"}},
        )
    assert result.is_error and not upstream


async def test_newly_required_fields_are_checked_after_answer(upstream):
    async def answer(*args):
        return ElicitResult(
            action="accept", content={"type": "place", "data": "unsolicited"}
        )

    async with Client(mcp, elicitation_handler=answer) as client:
        result = await client.call_tool_mcp(
            "search", {"params": {"engine": "google_maps"}}
        )
    assert result.is_error
    assert "parameters: data" in result.content[0].text
    assert not upstream


async def test_new_catalog_engine_needs_no_mcp_handler_changes(
    monkeypatch, tmp_path, upstream
):
    metadata = {
        "params": {
            "query": {"required": True},
            "count": {"required": True, "type": "number"},
            "enabled": {"required": True, "type": "boolean"},
            "choice": {
                "required": True,
                "type": "select",
                "options": [["a", "Choice A"], ["b", "Choice B"]],
            },
            "limit": {"required": True, "type": "number", "default": 10},
            "api_key": {"required": True},
            "engine": {"required": True},
            "output": {"required": True},
        },
        "common_params": {"api_key": {"required": True}},
    }
    (tmp_path / "example.json").write_text(json.dumps(metadata))
    monkeypatch.setattr(input_rules, "ENGINES_DIR", tmp_path)
    responses = {"count": 0, "enabled": False, "choice": "b"}

    async def answer(message, response_type, params, context):
        assert params.requested_schema["required"] == list(responses)
        assert params.requested_schema["properties"]["choice"]["enum"] == ["a", "b"]
        return ElicitResult(action="accept", content=responses)

    async with Client(mcp, elicitation_handler=answer) as client:
        result = await client.call_tool_mcp(
            "search", {"params": {"engine": "example", "query": "x"}}
        )
    assert not result.is_error
    assert upstream == [
        {"engine": "example", "query": "x", **responses, "api_key": "TEST_KEY"}
    ]


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_engine_completion_matches_catalog_and_handles_unknown_references(mode):
    ref = ResourceTemplateReference(
        type="ref/resource", uri="serpapi://engines/{engine_name}"
    )
    engines = sorted(path.stem for path in resources.ENGINES_DIR.glob("*.json"))
    async with Client(mcp, mode=mode) as client:
        assert client.server_capabilities.completions is not None
        for prefix in ("google_f", "google_flights", "does_not_exist", ""):
            result = await client.complete(
                ref=ref, argument={"name": "engine_name", "value": prefix}
            )
            expected = [engine for engine in engines if engine.startswith(prefix)]
            assert result.values == expected[:100]
            if len(expected) > 100:
                assert result.has_more and result.total == len(expected)
        for uri, name in (
            (ref.uri, "q"),
            ("serpapi://other/{engine_name}", "engine_name"),
        ):
            result = await client.complete(
                ref=ResourceTemplateReference(type="ref/resource", uri=uri),
                argument={"name": name, "value": "google"},
            )
            assert result.values == []


@pytest.mark.parametrize("auth", ["bearer", "path"])
@pytest.mark.parametrize(
    "complete_params,missing_name",
    [
        (FLIGHT_PARAMS, "outbound_date"),
        (HOTEL_PARAMS, "check_out_date"),
        (
            {
                "engine": "google_maps_directions",
                "start_addr": "Austin airport",
                "end_addr": "Austin city hall",
            },
            "end_addr",
        ),
    ],
)
async def test_search_continuation_runs_on_another_http_replica(
    upstream, auth, complete_params, missing_name
):
    replicas = [FastMCP("Search replica"), FastMCP("Search replica")]
    headers = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": "tools/call",
        "Mcp-Name": "search",
    }
    path = "/HTTP_KEY/mcp" if auth == "path" else "/mcp"
    if auth == "bearer":
        headers["Authorization"] = "Bearer HTTP_KEY"
    params = {
        "name": "search",
        "arguments": {
            "params": {k: v for k, v in complete_params.items() if k != missing_name}
        },
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
            "io.modelcontextprotocol/clientCapabilities": {"elicitation": {"form": {}}},
        },
    }
    async with AsyncExitStack() as stack:
        clients = []
        for replica in replicas:
            replica.add_tool(await mcp.get_tool("search"))
            app = replica.http_app(
                middleware=[Middleware(ApiKeyMiddleware)],
                stateless_http=True,
                json_response=True,
            )
            await stack.enter_async_context(app.router.lifespan_context(app))
            clients.append(
                await stack.enter_async_context(
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://test",
                        headers=headers,
                    )
                )
            )
        first = await clients[0].post(
            path,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params},
        )
        assert first.status_code == 200
        prompt = first.json()["result"]
        assert prompt.get("resultType") == "input_required", prompt
        assert not prompt.get("requestState")
        assert "HTTP_KEY" not in first.text
        assert not upstream
        params["inputResponses"] = {
            "search_details": {
                "action": "accept",
                "content": {missing_name: complete_params[missing_name]},
            }
        }
        second = await clients[1].post(
            path,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": params},
        )
    assert second.status_code == 200
    result = second.json()["result"]
    assert not result.get("isError", False)
    assert result["structuredContent"] == {"result": result["content"][0]["text"]}
    assert json.loads(result["structuredContent"]["result"]) == {
        "best_flights": [{"price": 400}]
    }
    assert len(upstream) == 1
    assert upstream[0]["api_key"] == "HTTP_KEY"
