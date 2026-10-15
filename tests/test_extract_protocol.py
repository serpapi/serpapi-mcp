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
def extract_upstream(monkeypatch, tmp_path):
    # The published catalog is generated at build time. Keep these checks
    # independent of a developer's generated or untracked engine files.
    (tmp_path / "extract.json").write_text(
        json.dumps({"params": {"url": {"required": True}}})
    )
    monkeypatch.setattr(input_rules, "ENGINES_DIR", tmp_path)
    monkeypatch.setenv("SERPAPI_API_KEY", "EXTRACT_TEST_KEY")
    calls = []

    def search(params):
        calls.append(dict(params))
        if params.get("output") == "md":
            return "# Example page\n\nPage content.\n"
        text_content = {"full": "Page content."}
        if params.get("include_markdown", False):
            text_content["markdown"] = "Page content."
        payload = {
            "search_metadata": {"status": "Success"},
            "page_information": {"title": "Example page"},
            "text_content": text_content,
        }
        restrictor = params.get("json_restrictor")
        if restrictor == "text_content.markdown":
            payload = (
                {"text_content": {"markdown": text_content["markdown"]}}
                if "markdown" in text_content
                else {}
            )
        elif restrictor:
            payload = {restrictor: payload[restrictor]}
        return SerpResults(payload, client=None)

    monkeypatch.setattr(tools.serpapi, "search", search)
    return calls


@pytest.mark.parametrize("protocol", ["auto", "legacy"])
@pytest.mark.parametrize(
    "options,response_mode",
    [
        ({"include_markdown": True}, "complete"),
        (
            {"include_markdown": True, "json_restrictor": "text_content.markdown"},
            "compact",
        ),
        ({"output": "md", "include_markdown": True}, "compact"),
        (
            {"include_markdown": True, "json_restrictor": "text_content.markdown"},
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
async def test_extract_markdown_defaults_for_content_modes(
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
    elif extraction_mode == "map":
        assert "include_markdown" not in extract_upstream[0]
    else:
        assert extract_upstream[0]["include_markdown"] is True
    assert extract_upstream[1] == {
        **params,
        "engine": "extract",
        "api_key": "EXTRACT_TEST_KEY",
    }


@pytest.mark.parametrize("protocol", ["auto", "legacy"])
@pytest.mark.parametrize(
    "options,expected_defaults",
    [
        ({}, {"include_markdown": True, "json_restrictor": "text_content.markdown"}),
        (
            {"mode": "scrape", "output": "json"},
            {"include_markdown": True, "json_restrictor": "text_content.markdown"},
        ),
        ({"include_markdown": False}, {"json_restrictor": "text_content"}),
        ({"json_restrictor": "text_content"}, {"include_markdown": True}),
        ({"json_restrictor": ""}, {"include_markdown": True}),
        ({"include_markdown": False, "json_restrictor": "page_information"}, {}),
        ({"include_markdown": False, "json_restrictor": ""}, {}),
    ],
)
async def test_extract_compact_defaults_preserve_overrides_and_search(
    extract_upstream, protocol, options, expected_defaults
):
    params = {"url": "https://example.com", **options}
    async with Client(mcp, mode=protocol) as client:
        extracted = await client.call_tool_mcp(
            "extract", {"params": params, "mode": "compact"}
        )
        searched = await client.call_tool_mcp(
            "search", {"params": {**params, "engine": "extract"}, "mode": "compact"}
        )
    authenticated = {**params, "engine": "extract", "api_key": "EXTRACT_TEST_KEY"}
    assert extract_upstream == [{**authenticated, **expected_defaults}, authenticated]
    assert not extracted.is_error
    assert not searched.is_error
    assert extracted.structured_content == {"result": extracted.content[0].text}
    data = json.loads(extracted.content[0].text)
    assert data
    assert "search_metadata" not in data
    if options.get("include_markdown") is False:
        assert "markdown" not in data.get("text_content", {})
        if "json_restrictor" not in options:
            assert data == {"text_content": {"full": "Page content."}}


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
async def test_extract_guided_url_preserves_engine_and_context(
    extract_upstream, action, response_mode
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

    async with Client(mcp, elicitation_handler=answer) as client:
        result = await client.call_tool_mcp("extract", {"mode": response_mode})
    assert not result.is_error
    if action == "accept":
        expected = {
            "engine": "extract",
            "url": "https://example.com",
            "api_key": "EXTRACT_TEST_KEY",
            "include_markdown": True,
        }
        if response_mode == "compact":
            expected["json_restrictor"] = "text_content.markdown"
        assert extract_upstream == [expected]
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
