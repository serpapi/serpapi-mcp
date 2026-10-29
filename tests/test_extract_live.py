"""Opt-in Extract contract checks through the MCP protocol and live SerpApi."""

import json
import os

import pytest
from fastmcp import Client

from src.server import mcp

pytestmark = pytest.mark.skipif(
    not os.getenv("SERPAPI_KEY"), reason="set SERPAPI_KEY to run live Extract tests"
)


@pytest.fixture(autouse=True)
def extract_key(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", os.environ["SERPAPI_KEY"])


@pytest.mark.parametrize("response_mode", ["complete", "compact"])
async def test_live_extract_json(response_mode):
    async with Client(mcp) as client:
        result = await client.call_tool_mcp(
            "extract",
            {
                "params": {"url": "https://example.com/", "output": "json"},
                "mode": response_mode,
            },
        )
    assert not result.is_error
    payload = json.loads(result.content[0].text)
    assert "documentation examples" in payload["markdown"]
    if response_mode == "compact":
        assert set(payload) == {"markdown"}
    else:
        assert payload["page_information"]["title"] == "Example Domain"


@pytest.mark.parametrize("disabled", [False, "false"])
async def test_live_extract_compact_without_markdown(disabled):
    async with Client(mcp) as client:
        result = await client.call_tool_mcp(
            "extract",
            {
                "params": {
                    "url": "https://example.com/",
                    "include_markdown": disabled,
                    "output": "json",
                },
                "mode": "compact",
            },
        )
    assert not result.is_error
    payload = json.loads(result.content[0].text)
    assert "markdown" not in payload
    assert "search_metadata" not in payload
    assert payload["page_information"]["title"] == "Example Domain"


async def test_live_extract_native_markdown():
    async with Client(mcp) as client:
        result = await client.call_tool_mcp(
            "extract", {"params": {"url": "https://example.com/", "output": "md"}}
        )
    assert not result.is_error
    text = result.content[0].text
    assert "documentation examples" in text
    assert "search_metadata:" not in text
    assert result.structured_content == {"result": text}


async def test_live_extract_crawl_page_markdown():
    async with Client(mcp) as client:
        result = await client.call_tool_mcp(
            "extract",
            {
                "params": {
                    "url": "https://example.com/",
                    "mode": "crawl",
                    "max_pages": 1,
                    "max_depth": 0,
                    "output": "json",
                },
                "mode": "compact",
            },
        )
    assert not result.is_error
    payload = json.loads(result.content[0].text)
    assert len(payload["pages"]) == 1
    assert "documentation examples" in payload["pages"][0]["markdown"]


async def test_live_extract_compact_error():
    async with Client(mcp) as client:
        result = await client.call_tool_mcp(
            "extract", {"params": {"url": "http://127.0.0.1/"}, "mode": "compact"}
        )
    assert result.is_error
    assert "publicly reachable" in result.content[0].text
    assert os.environ["SERPAPI_KEY"] not in result.content[0].text


@pytest.mark.parametrize("output", [None, "json"])
async def test_live_extract_compact_redirected_engine(output):
    params = {"url": "https://www.google.com/search?q=coffee"}
    if output is not None:
        params["output"] = output
    async with Client(mcp) as client:
        result = await client.call_tool_mcp(
            "extract",
            {"params": params, "mode": "compact"},
        )
    assert not result.is_error
    text = result.content[0].text
    if output == "json":
        payload = json.loads(text)
        assert payload["organic_results"][0]["title"]
        assert payload["organic_results"][0]["link"]
        assert "search_metadata" not in payload
    else:
        assert "coffee" in text.lower()
        assert "https://" in text
        assert not text.lstrip().startswith("{")
    assert result.structured_content == {"result": result.content[0].text}


async def test_live_extract_compact_defaults_to_markdown():
    async with Client(mcp) as client:
        result = await client.call_tool_mcp(
            "extract", {"params": {"url": "https://example.com/"}, "mode": "compact"}
        )
    assert not result.is_error
    text = result.content[0].text
    assert "documentation examples" in text
    assert not text.lstrip().startswith("{")
    assert "search_metadata:" not in text
    assert result.structured_content == {"result": text}
