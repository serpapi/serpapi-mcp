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
Clients that support [argument completion](https://gofastmcp.com/servers/completions) can request engine-name suggestions for `serpapi://engines/{engine_name}`. For example, the prefix `google_f` suggests matching engine identifiers. This completes the resource URI parameter, not arbitrary search queries.

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

Search responses preserve the existing MCP `structuredContent.result` string and include the same string in text content. For JSON output, `result` contains serialized JSON; existing clients can continue parsing it with `JSON.parse(response.structuredContent.result)`. For Markdown output, it contains the unchanged Markdown. Errors and cancellations use the same wrapper. Search execution failures set `isError: true`; clients using FastMCP's high-level `call_tool()` should handle `ToolError`, or use `call_tool_mcp()` to inspect the result flag. See [MCP tool results](https://modelcontextprotocol.io/specification/2025-06-18/server/tools).

`search` uses the engine catalog and engine-specific rules to identify missing parameters. Supporting MCP 2026-07-28 clients receive a form before any search runs. Accepted answers are validated; decline or cancellation runs no search. Legacy clients and clients without form elicitation receive an error listing the missing parameters so the agent can ask in conversation. See [MCP input requests](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/mrtr).

- [Google Flights](https://serpapi.com/google-flights-api): departure and arrival identifiers, departure date, and a return date for round trips. Dates and airport identifiers are checked. Token-based searches, multi-city itineraries, and `selected_flights_json` retain their existing behavior.
- [Google Hotels](https://serpapi.com/google-hotels-api): destination or hotel query, check-in date, and check-out date. Check-out must follow check-in. Guest counts and other optional filters keep the caller's values or the API defaults.
- [Google Maps Directions](https://serpapi.com/google-maps-directions-api): missing start and destination addresses. Coordinates or place data IDs already supplied satisfy the corresponding endpoint.
- Other catalog engines use their required fields, such as YouTube's `search_query`, Yelp's `find_loc`, and Amazon's `k`. Engine rules account for known defaults and alternatives, including Amazon category nodes, eBay categories, and Google Scholar citation searches.

The form is derived from the original arguments on each request. It uses no `requestState` or process-local continuation storage, so a retry can run on another replica without a shared state-protection key. Authentication is applied on every HTTP request, and only answers for requested fields are used. If an answer introduces another requirement, the tool lists the remaining fields for the agent to supply in a new call.

To extend guided search, add required fields, descriptions, types, and options to the engine's `engines/<engine>.json` file. Add an `EngineInputRules` entry in [`src/engine_input_rules.py`](src/engine_input_rules.py) when requirements depend on other parameters, defaults, or alternatives. The shared MCP handler in [`src/search_input.py`](src/search_input.py) needs no engine-specific branches. Forms support strings, numbers, booleans, and single-choice fields; unsupported complex fields receive the missing-parameter error. Unknown engines pass through to SerpApi.

## Interactive UI (MCP Apps)

The `search` tool returns JSON by default. For hosts that support the [MCP Apps extension](https://modelcontextprotocol.io/seps/1865-mcp-apps-interactive-user-interfaces-for-mcp) (SEP-1865), two opt-in tools render results as an interactive UI directly in the conversation, so the bulk SERP JSON never enters the model's context window:

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

## Privacy Policy

- **Sent**: only the parameters the MCP host passes to a tool call. The server never sees the rest of the conversation, or files, memory or history on the host.
- **Forwarded**: each search goes to `serpapi.com` with your API key; results come back unchanged. See the [SerpApi Privacy Policy](https://serpapi.com/legal#privacy-policy) for how SerpApi handles searches and accounts.
- **Kept**: `mcp.serpapi.com` records request metrics (method, status code, duration) and stores no queries or results. A key in the URL path can appear in request logs, so prefer the header.
- **Local bundle**: the Claude Desktop extension runs on your machine, keeps the key in Claude Desktop's settings and calls `serpapi.com` directly. Nothing passes through `mcp.serpapi.com`.
- **Contact**: [privacy@serpapi.com](mailto:privacy@serpapi.com), or open an [issue](https://github.com/serpapi/serpapi-mcp/issues).

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
