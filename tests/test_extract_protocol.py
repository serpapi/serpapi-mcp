"""Exercise the Extract convenience tool through real MCP clients."""

import json

import pytest
from fastmcp import Client
from fastmcp.client.elicitation import ElicitResult
from serpapi.models import SerpResults

import src.engine_input_rules as input_rules
import src.mcp_components.tools as tools
from src.server import mcp


@pytest.fixture
def extract_upstream(monkeypatch):
    # Exercise the shipped catalog, including missing-URL guided input.
    monkeypatch.setenv("SERPAPI_API_KEY", "EXTRACT_TEST_KEY")
    calls = []

    def search(params):
        calls.append(dict(params))
        if params.get("output") == "md":
            return "# Example page\n\nPage content.\n"
        payload = {
            "search_metadata": {"status": "Success"},
            "page_information": {"title": "Example page"},
            "inline_links": [{"link": "https://example.com/next"}],
        }
        if str(params.get("include_markdown", True)).lower() != "false":
            payload["markdown"] = "Page content."
        restrictor = params.get("json_restrictor")
        if restrictor:
            payload = {
                key: payload[key] for key in restrictor.split(",") if key in payload
            }
        return SerpResults(payload, client=None)

    monkeypatch.setattr(tools.serpapi, "search", search)
    return calls


@pytest.mark.parametrize("protocol", ["auto", "legacy"])
@pytest.mark.parametrize(
    "options,response_mode",
    [
        ({"include_markdown": True}, "complete"),
        (
            {"json_restrictor": "markdown,error"},
            "compact",
        ),
        ({"output": "md", "include_markdown": True}, "compact"),
        (
            {"json_restrictor": "markdown,error"},
            "complete",
        ),
        ({"mode": "map", "include_sitemap": False}, "complete"),
        ({"mode": "map", "include_sitemap": False}, "compact"),
        (
            {
                "mode": "crawl",
                "max_pages": 2,
                "timeout": 1000,
                "include_markdown": True,
            },
            "compact",
        ),
    ],
)
async def test_extract_mirrors_search_and_forces_engine(
    extract_upstream, protocol, options, response_mode
):
    params = {
        "url": "https://example.com",
        "engine": "google_light",
        "api_key": "UNTRUSTED",
        "output": "json",
        **options,
    }
    async with Client(mcp, mode=protocol) as client:
        extracted = await client.call_tool_mcp(
            "extract", {"params": params, "mode": response_mode}
        )
        searched = await client.call_tool_mcp(
            "search",
            {"params": {**params, "engine": "extract"}, "mode": response_mode},
        )
    assert (
        extract_upstream
        == [{**params, "engine": "extract", "api_key": "EXTRACT_TEST_KEY"}] * 2
    )
    assert not extracted.is_error
    assert extracted.structured_content == searched.structured_content
    assert extracted.content == searched.content
    assert extracted.structured_content == {"result": extracted.content[0].text}


@pytest.mark.parametrize("response_mode", ["complete", "compact"])
async def test_extract_does_not_mutate_callers_params(extract_upstream, response_mode):
    params = {"url": "https://example.com", "engine": "google_light"}
    original = dict(params)
    result = await tools.extract(params=params, mode=response_mode)
    assert params == original
    assert not result.is_error
    assert extract_upstream[0]["engine"] == "extract"


@pytest.mark.parametrize("extraction_mode", [None, "scrape", "crawl", "map"])
@pytest.mark.parametrize("response_mode", ["complete", "compact"])
@pytest.mark.parametrize("output", ["json", "md"])
@pytest.mark.parametrize("disable_markdown", [False, True])
async def test_extract_uses_api_markdown_defaults(
    extract_upstream, extraction_mode, response_mode, output, disable_markdown
):
    params = {"url": "https://example.com", "output": output}
    if extraction_mode is not None:
        params["mode"] = extraction_mode
    if disable_markdown:
        params["include_markdown"] = False
    original = dict(params)

    async with Client(mcp) as client:
        extracted = await client.call_tool_mcp(
            "extract", {"params": params, "mode": response_mode}
        )
        searched = await client.call_tool_mcp(
            "search", {"params": {**params, "engine": "extract"}, "mode": response_mode}
        )

    assert not extracted.is_error
    assert not searched.is_error
    assert params == original
    if disable_markdown:
        assert extract_upstream[0]["include_markdown"] is False
    else:
        assert "include_markdown" not in extract_upstream[0]
    assert extract_upstream[1] == {
        **params,
        "engine": "extract",
        "api_key": "EXTRACT_TEST_KEY",
    }


@pytest.mark.parametrize("protocol", ["auto", "legacy"])
@pytest.mark.parametrize(
    "options",
    [
        {},
        {"mode": "scrape", "output": "json"},
        {"include_markdown": False},
        {"include_markdown": "false"},
        {"json_restrictor": "page_information"},
        {"json_restrictor": ""},
        {"include_markdown": False, "json_restrictor": "page_information"},
        {"include_markdown": False, "json_restrictor": ""},
    ],
)
async def test_extract_compact_json_preserves_overrides_and_search(
    extract_upstream, protocol, options
):
    params = {"url": "https://example.com", "output": "json", **options}
    async with Client(mcp, mode=protocol) as client:
        extracted = await client.call_tool_mcp(
            "extract", {"params": params, "mode": "compact"}
        )
        searched = await client.call_tool_mcp(
            "search", {"params": {**params, "engine": "extract"}, "mode": "compact"}
        )
    authenticated = {**params, "engine": "extract", "api_key": "EXTRACT_TEST_KEY"}
    assert extract_upstream == [authenticated, authenticated]
    assert not extracted.is_error
    assert not searched.is_error
    assert extracted.structured_content == {"result": extracted.content[0].text}
    data = json.loads(extracted.content[0].text)
    assert data
    assert "search_metadata" not in data
    if "json_restrictor" in options:
        assert extracted.structured_content == searched.structured_content
    elif str(options.get("include_markdown", True)).lower() != "false":
        assert data == {"markdown": "Page content."}
        assert "page_information" in json.loads(searched.content[0].text)
    if str(options.get("include_markdown", True)).lower() == "false":
        assert "markdown" not in data
        if "json_restrictor" not in options:
            assert data["page_information"]["title"] == "Example page"
            assert data["inline_links"]


@pytest.mark.parametrize("protocol", ["auto", "legacy"])
@pytest.mark.parametrize(
    "arguments,message",
    [
        ({}, "Missing Extract parameters: url"),
        ({"params": {"url": "https://example.com"}, "mode": "crawl"}, "Invalid mode"),
        (
            {"params": {"url": "https://example.com", "output": "html"}},
            "Invalid output",
        ),
    ],
)
async def test_extract_rejects_invalid_input_without_searching(
    extract_upstream, protocol, arguments, message
):
    async with Client(mcp, mode=protocol) as client:
        result = await client.call_tool_mcp("extract", arguments)
    assert result.is_error
    assert message in result.content[0].text
    assert result.structured_content == {"result": result.content[0].text}
    assert not extract_upstream


@pytest.mark.parametrize("action", ["accept", "cancel"])
@pytest.mark.parametrize("response_mode", ["complete", "compact"])
@pytest.mark.parametrize("output", [None, "json"])
async def test_extract_guided_url_preserves_engine_and_context(
    extract_upstream, action, response_mode, output
):
    async def answer(message, response_type, params, context):
        assert params.requested_schema["required"] == ["url"]
        assert not extract_upstream
        return ElicitResult(
            action=action,
            content={
                "url": "https://example.com",
                "engine": "google",
                "api_key": "UNTRUSTED",
            },
        )

    arguments = {"mode": response_mode}
    if output is not None:
        arguments["params"] = {"output": output}
    async with Client(mcp, elicitation_handler=answer) as client:
        result = await client.call_tool_mcp("extract", arguments)
    assert not result.is_error
    if action == "accept":
        expected = {
            "engine": "extract",
            "url": "https://example.com",
            "api_key": "EXTRACT_TEST_KEY",
        }
        if output is not None:
            expected["output"] = output
        elif response_mode == "compact":
            expected["output"] = "md"
        assert extract_upstream == [expected]
        if response_mode == "compact":
            if output == "json":
                assert json.loads(result.content[0].text) == {
                    "markdown": "Page content."
                }
            else:
                assert result.content[0].text == "# Example page\n\nPage content.\n"
    else:
        assert "cancelled" in result.content[0].text
        assert not extract_upstream


async def test_extract_requires_trusted_authentication(extract_upstream, monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY")
    async with Client(mcp) as client:
        result = await client.call_tool_mcp(
            "extract",
            {"params": {"url": "https://example.com", "api_key": "UNTRUSTED"}},
        )
    assert result.is_error
    assert "Missing API key" in result.content[0].text
    assert not extract_upstream


async def test_extract_preserves_upstream_error_wrapper(extract_upstream, monkeypatch):
    monkeypatch.setattr(
        tools.serpapi,
        "search",
        lambda params: SerpResults({"error": "Extraction failed"}, client=None),
    )
    async with Client(mcp) as client:
        result = await client.call_tool_mcp(
            "extract", {"params": {"url": "https://example.com"}}
        )
    assert result.is_error
    assert result.structured_content == {"result": "Error: Extraction failed"}
    assert result.content[0].text == "Error: Extraction failed"


async def test_extract_resource_and_guided_input_use_shipped_schema():
    spec = input_rules.get_search_input_spec({"engine": "extract"})
    assert spec is not None
    assert "url" in spec.fields
    async with Client(mcp) as client:
        contents = await client.read_resource("serpapi://engines/extract")
    schema = json.loads(contents[0].text)
    assert schema["params"]["url"]["required"] is True
    assert "Default: `true`" in schema["params"]["include_markdown"]["description"]
    assert "include_inline_assets" not in schema["params"]


async def test_extract_compact_keeps_upstream_errors(extract_upstream, monkeypatch):
    def fail(params):
        assert "json_restrictor" not in params
        return SerpResults({"error": "Extraction failed"}, client=None)

    monkeypatch.setattr(tools.serpapi, "search", fail)
    async with Client(mcp) as client:
        result = await client.call_tool_mcp(
            "extract", {"params": {"url": "https://example.com"}, "mode": "compact"}
        )
    assert result.is_error
    assert "Extraction failed" in result.content[0].text


@pytest.mark.parametrize("protocol", ["auto", "legacy"])
async def test_extract_compact_preserves_redirected_engine_results(
    extract_upstream, monkeypatch, protocol
):
    organic_results = [{"title": "Coffee", "link": "https://example.com/coffee"}]

    def redirected_search(params):
        payload = {
            "search_metadata": {"status": "Success"},
            "search_parameters": {"engine": "google"},
            "organic_results": organic_results,
        }
        if restrictor := params.get("json_restrictor"):
            payload = {
                key: payload[key] for key in restrictor.split(",") if key in payload
            }
        return SerpResults(payload, client=None)

    monkeypatch.setattr(tools.serpapi, "search", redirected_search)
    async with Client(mcp, mode=protocol) as client:
        result = await client.call_tool_mcp(
            "extract",
            {
                "params": {
                    "url": "https://www.google.com/search?q=coffee",
                    "output": "json",
                },
                "mode": "compact",
            },
        )
    assert not result.is_error
    assert json.loads(result.content[0].text) == {"organic_results": organic_results}
    assert result.structured_content == {"result": result.content[0].text}


@pytest.mark.parametrize("protocol", ["auto", "legacy"])
@pytest.mark.parametrize("extraction_mode", ["scrape", "map", "crawl"])
@pytest.mark.parametrize("response_mode", ["complete", "compact"])
async def test_extract_default_output(
    extract_upstream, protocol, extraction_mode, response_mode
):
    params = {"url": "https://example.com", "mode": extraction_mode}
    async with Client(mcp, mode=protocol) as client:
        result = await client.call_tool_mcp(
            "extract", {"params": params, "mode": response_mode}
        )
    assert not result.is_error
    expected = {**params, "engine": "extract", "api_key": "EXTRACT_TEST_KEY"}
    if response_mode == "compact":
        expected["output"] = "md"
        assert result.content[0].text == "# Example page\n\nPage content.\n"
    else:
        assert (
            json.loads(result.content[0].text)["page_information"]["title"]
            == "Example page"
        )
    assert extract_upstream == [expected]
    assert result.structured_content == {"result": result.content[0].text}
