"""Keeps the MCP Bundle manifest (mcpb/manifest.json) and the stdio entry point honest.

Claude Desktop installs the server from mcpb/manifest.json and starts src/stdio.py
over stdio with the API key in SERPAPI_API_KEY, so these tests pin the manifest
to what the server actually exposes and start the entry point for real.
"""

import json
import os
import tomllib
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import PythonStdioTransport

import src.server as server

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "mcpb" / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_version_matches_pyproject_and_registry():
    with (ROOT / "pyproject.toml").open("rb") as pyproject:
        project_version = tomllib.load(pyproject)["project"]["version"]
    registry_version = json.loads((ROOT / "server.json").read_text())["version"]
    assert MANIFEST["version"] == project_version == registry_version


def test_mcpbignore_sits_at_project_root_and_drops_non_runtime_files():
    # The MCPB CLI only honours .mcpbignore at the root of the packed directory.
    patterns = {
        line.strip()
        for line in (ROOT / ".mcpbignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert {"tests/", ".github/", "mcpb/", ".env", ".venv/"} <= patterns


def test_manifest_uses_uv_runtime_and_launches_existing_entry_point():
    manifest_server = MANIFEST["server"]
    assert MANIFEST["manifest_version"] == "0.4"  # first version with the uv runtime
    assert manifest_server["type"] == "uv"
    entry_point = manifest_server["entry_point"]
    assert (ROOT / entry_point).is_file()
    args = manifest_server["mcp_config"]["args"]
    assert manifest_server["mcp_config"]["command"] == "uv"
    assert args[:3] == ["run", "--directory", "${__dirname}"]
    assert args[-1] == entry_point


def test_manifest_icon_is_staged_next_to_it():
    # build.py copies mcpb/icon.png to the bundle root, where the manifest points.
    assert MANIFEST["icon"] == "icon.png"
    assert (ROOT / "mcpb" / "icon.png").is_file()


def test_manifest_wires_api_key_from_user_config_into_env():
    env = MANIFEST["server"]["mcp_config"]["env"]
    assert env == {"SERPAPI_API_KEY": "${user_config.serpapi_api_key}"}
    option = MANIFEST["user_config"]["serpapi_api_key"]
    assert option["type"] == "string"
    assert option["required"] is True
    assert option["sensitive"] is True


async def test_manifest_tools_match_server_tools():
    server_tools = {tool.name for tool in await server.mcp.list_tools()}
    assert {tool["name"] for tool in MANIFEST["tools"]} == server_tools


async def test_stdio_entry_point_serves_tools_and_resources(tmp_path):
    # Start from an unrelated cwd so only the entry point's own sys.path
    # bootstrap can make the `src.*` imports resolve, as in an installed bundle.
    transport = PythonStdioTransport(
        ROOT / MANIFEST["server"]["entry_point"],
        env={**os.environ, "SERPAPI_API_KEY": "test-key"},
        cwd=str(tmp_path),
        keep_alive=False,
    )
    async with Client(transport) as client:
        tools = {tool.name for tool in await client.list_tools()}
        engines = await client.read_resource("serpapi://engines")

    assert {"search", "search_table", "search_dashboard"} <= tools
    assert json.loads(engines[0].text)["count"] > 0
