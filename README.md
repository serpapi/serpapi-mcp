# <img src="https://user-images.githubusercontent.com/307597/154772945-1b7dba5f-21cf-41d0-bb2e-65b6eff4aaaf.png" width="30" height="30"/> SerpApi MCP Server

A Model Context Protocol (MCP) server implementation that integrates with [SerpApi](https://serpapi.com) for comprehensive search engine results and data extraction.

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Install in VS Code](https://img.shields.io/badge/Install%20in-VS%20Code-blue?logo=visualstudiocode)](https://insiders.vscode.dev/redirect/mcp/install?name=serpapi-mcp&config=%7B%22type%22%3A%22http%22%2C%22url%22%3A%22https%3A%2F%2Fmcp.serpapi.com%2Fmcp%22%2C%22headers%22%3A%7B%22Authorization%22%3A%22Bearer%20YOUR_SERPAPI_API_KEY%22%7D%7D)
[![Install in Cursor](https://img.shields.io/badge/Install%20in-Cursor-blue?logo=cursor)](https://cursor.com/en-US/install-mcp?name=serpapi-mcp&config=eyJ0eXBlIjoiaHR0cCIsInVybCI6Imh0dHBzOi8vbWNwLnNlcnBhcGkuY29tL21jcCIsImhlYWRlcnMiOnsiQXV0aG9yaXphdGlvbiI6IkJlYXJlciBZT1VSX1NFUlBBUElfQVBJX0tFWSJ9fQ==)

## Features

- **Multi-Engine Search**: Google, Bing, Yahoo, DuckDuckGo, YouTube, eBay, and [more](https://serpapi.com/search-engine-apis)
- **Engine Resources**: Per-engine parameter schemas available via MCP resources (see Search Tool)
- **Real-time Weather Data**: Location-based weather with forecasts via search queries
- **Stock Market Data**: Company financials and market data through search integration
- **Dynamic Result Processing**: Automatically detects and formats different result types
- **Flexible Response Modes**: Complete or compact JSON responses
- **JSON Responses (default)**: Structured JSON output with complete or compact modes
- **Markdown Responses**: Cut token usage by 50% on average and by more than 90% for APIs with complex nested JSON.
- **Interactive UI (MCP Apps)**: Opt-in `search_table` and `search_dashboard` tools that render results as an interactive UI in supporting hosts
- **Claude Desktop Extension**: One-click local install from an [MCP Bundle](https://github.com/modelcontextprotocol/mcpb) (`.mcpb`), see below

## Quick Start

SerpApi MCP Server is available as a hosted service at [mcp.serpapi.com](https://mcp.serpapi.com). In order to connect to it, you need to provide an API key. You can find your API key on your [SerpApi dashboard](https://serpapi.com/dashboard).

You can configure Claude Desktop to use the hosted server:

```json
{
  "mcpServers": {
    "serpapi": {
      "type": "http",
      "url": "https://mcp.serpapi.com/YOUR_SERPAPI_API_KEY/mcp"
    }
  }
}
```

You can also add the hosted server to these MCP clients:

**OpenClaw**
```bash
openclaw mcp add serpapi --url https://mcp.serpapi.com/YOUR_SERPAPI_API_KEY/mcp --transport streamable-http
```

**Claude Code**
```bash
claude mcp add --transport http serpapi https://mcp.serpapi.com/mcp --header "Authorization: Bearer YOUR_SERPAPI_API_KEY"
```

**Hermes**
```bash
hermes mcp add serpapi --url https://mcp.serpapi.com/YOUR_SERPAPI_API_KEY/mcp
```

**Codex** (reads the key from `SERPAPI_API_KEY` in your shell)
```bash
codex mcp add serpapi --url https://mcp.serpapi.com/mcp --bearer-token-env-var SERPAPI_API_KEY
```

### Self-Hosting
```bash
git clone https://github.com/serpapi/serpapi-mcp.git
cd serpapi-mcp
uv sync && uv run src/server.py
```

Configure Claude Desktop:
```json
{
  "mcpServers": {
    "serpapi": {
      "type": "http",
      "url": "http://localhost:8000/YOUR_SERPAPI_API_KEY/mcp"
    }
  }
}
```

Get your API key: [serpapi.com/manage-api-key](https://serpapi.com/manage-api-key)

### Claude Desktop Extension (MCP Bundle)

For a local, one-click install, download the `.mcpb` bundle from the [latest release](https://github.com/serpapi/serpapi-mcp/releases/latest) (or build it as below) and open it with Claude Desktop (or drop it onto **Settings → Extensions**). Claude Desktop asks for your SerpApi API key during install, stores it as a sensitive setting, and runs the server locally over stdio. The bundle uses the MCPB `uv` runtime: it ships only the source, `pyproject.toml` and `uv.lock`, and Claude Desktop provisions Python and the locked dependencies with uv at install time, so nothing is vendored and one bundle works on macOS, Windows and Linux.

```bash
uv run mcpb/build.py   # needs Node.js for the MCPB CLI; writes dist/serpapi-mcp-<version>.mcpb
```

Everything bundle-related lives in [mcpb/](mcpb/), plus [.mcpbignore](.mcpbignore) at the project root. The build regenerates the engine schemas from the SerpApi Playground (`--no-rebuild-engines` bundles `engines/` from the working tree instead), validates [mcpb/manifest.json](mcpb/manifest.json), packs the git-tracked files minus [.mcpbignore](.mcpbignore) with the manifest at the bundle root, then installs it into a temp dir and starts it over stdio to make sure it works (`--no-smoke` skips that last step). The bundle is only built at release time: pushing a `v<version>` tag runs the release workflow, which runs the test suite and then deploys the hosted server, publishes the MCP Registry entry, and builds the bundle and attaches it to the GitHub release. Pull requests run the manifest and stdio entry point tests in `tests/test_mcpb.py` but do not pack a bundle.

The same stdio entry point works with any local MCP host that launches servers as a subprocess:

```json
{
  "mcpServers": {
    "serpapi": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/serpapi-mcp", "--frozen", "--no-dev", "src/stdio.py"],
      "env": { "SERPAPI_API_KEY": "YOUR_SERPAPI_API_KEY" }
    }
  }
}
```

## Authentication

Two methods are supported:
- **Header-based**: `Authorization: Bearer YOUR_API_KEY` (recommended: the key stays out of URLs and logs)
- **Path-based**: `/YOUR_API_KEY/mcp`, for clients that cannot set headers

**Examples:**
```bash
# Header-based
curl "https://mcp.serpapi.com/mcp" -H "Authorization: Bearer your_key" -d '...'

# Path-based
curl "https://mcp.serpapi.com/your_key/mcp" -d '...'
```

No key is needed to connect, list tools or read resources. `search` and the App tools need one and return an error without it.

## Search Tool

The MCP server has one main Search Tool that supports all SerpApi engines and result types. You can find all available parameters on the [SerpApi API reference](https://serpapi.com/search-api).
Engine parameter schemas are also exposed as MCP resources: `serpapi://engines` (index) and `serpapi://engines/<engine>`.

The parameters you can provide are specific for each API engine. Some sample parameters are provided below:

- `params.q` (required): Search query
- `params.engine`: Search engine (default: "google_light") 
- `params.location`: Geographic filter
- `params.output`: Response format; omit for JSON (default), or set to `"md"` for Markdown
- `mode`: Response mode; `"compact"` removes metadata from JSON, while Markdown is returned unchanged
- ...see other parameters on the [SerpApi API reference](https://serpapi.com/search-api)

**Examples:**

```json
{"name": "search", "arguments": {"params": {"q": "coffee shops", "location": "Austin, TX"}}}
{"name": "search", "arguments": {"params": {"q": "weather in London"}}}
{"name": "search", "arguments": {"params": {"q": "AAPL stock"}}}
{"name": "search", "arguments": {"params": {"q": "news"}, "mode": "compact"}}
{"name": "search", "arguments": {"params": {"q": "detailed search"}, "mode": "complete"}}
{"name": "search", "arguments": {"params": {"q": "news", "output": "md"}}}
{"name": "search", "arguments": {"params": {"engine": "amazon", "k": "mechanical keyboards", "amazon_domain": "amazon.com", "output": "md"}}}
{"name": "search", "arguments": {"params": {"engine": "google_scholar", "q": "retrieval augmented generation"}}}
{"name": "search", "arguments": {"params": {"engine": "youtube", "search_query": "how to make espresso"}}}
{"name": "search", "arguments": {"params": {"engine": "apple_app_store", "term": "habit tracker"}}}
{"name": "search", "arguments": {"params": {"engine": "ebay", "_nkw": "vintage mechanical keyboard"}}}
```

**Supported Engines:** Google, Bing, Yahoo, DuckDuckGo, YouTube, eBay, and more (see `serpapi://engines`).

**Result Types:** Answer boxes, organic results, news, images, shopping - automatically detected and formatted.

## Interactive UI (MCP Apps)

The default `search` tool returns JSON and is unchanged. For hosts that support the [MCP Apps extension](https://modelcontextprotocol.io/seps/1865-mcp-apps-interactive-user-interfaces-for-mcp) (SEP-1865), two opt-in tools render results as an interactive UI directly in the conversation, so the bulk SERP JSON never enters the model's context window:

- `search_table`: organic results as a sortable, searchable table.
- `search_dashboard`: summary metrics, a source-breakdown chart, and a results table with a click-to-expand detail panel.

Both accept the same `params` as `search`. Hosts that don't support MCP Apps simply ignore these tools.

Preview them locally without an MCP host:

```bash
uv run fastmcp dev apps src/server.py
```

## Development

```bash
# Local development
uv sync && uv run src/server.py

# Docker
docker build -t serpapi-mcp . && docker run -p 8000:8000 serpapi-mcp

# Build the Claude Desktop extension (MCP Bundle); rebuilds engines, needs Node.js for the MCPB CLI
uv run mcpb/build.py

# Release: bump the version in pyproject.toml, server.json and mcpb/manifest.json, then tag it.
# Nothing ships on a plain push to main. The tag runs the release workflow, which runs the test
# suite and then deploys the hosted server, publishes server.json to the MCP Registry, and builds
# the MCP Bundle and attaches it to the GitHub release.
git tag v1.0.2 && git push origin v1.0.2

# Regenerate engine resources (Playground scrape)
python build-engines.py

# Testing with MCP Inspector
npx @modelcontextprotocol/inspector
# Configure: URL mcp.serpapi.com/YOUR_KEY/mcp, Transport "Streamable HTTP transport"
```

## Troubleshooting

- **"Missing API key"**: Include key in URL path `/{YOUR_KEY}/mcp` or header `Bearer YOUR_KEY`
- **"Invalid key"**: Verify at [serpapi.com/dashboard](https://serpapi.com/dashboard)  
- **"Rate limit exceeded"**: Wait or upgrade your SerpApi plan
- **"No results"**: Try different query or engine

## Contributing

1. Fork the repository
2. Create your feature branch: `git checkout -b feature/amazing-feature`
3. Install dependencies: `uv install`
4. Make your changes
5. Commit changes: `git commit -m 'Add amazing feature'`
6. Push to branch: `git push origin feature/amazing-feature`
7. Open a Pull Request

## License

MIT License - see [LICENSE](LICENSE) file for details.
