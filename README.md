# <img src="https://user-images.githubusercontent.com/307597/154772945-1b7dba5f-21cf-41d0-bb2e-65b6eff4aaaf.png" width="30" height="30"/> SerpApi MCP Server

A Model Context Protocol (MCP) server implementation that integrates with [SerpApi](https://serpapi.com) for comprehensive search engine results and data extraction.

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Install in VS Code](https://img.shields.io/badge/Install%20in-VS%20Code-blue?logo=visualstudiocode)](https://insiders.vscode.dev/redirect/mcp/install?name=serpapi-mcp&config=%7B%22type%22%3A%22http%22%2C%22url%22%3A%22https%3A%2F%2Fmcp.serpapi.com%2FYOUR_SERPAPI_API_KEY%2Fmcp%22%7D)
[![Install in Cursor](https://img.shields.io/badge/Install%20in-Cursor-blue?logo=cursor)](https://cursor.com/en-US/install-mcp?name=serpapi-mcp&config=eyJ0eXBlIjoiaHR0cCIsInVybCI6Imh0dHBzOi8vbWNwLnNlcnBhcGkuY29tL1lPVVJfU0VSUEFQSV9BUElfS0VZL21jcCJ9)

## Features

- **Multi-Engine Search**: Google, Bing, Yahoo, DuckDuckGo, YouTube, eBay, and [more](https://serpapi.com/search-engine-apis)
- **Engine Resources**: Per-engine parameter schemas available via MCP resources (see Search Tool)
- **Real-time Weather Data**: Location-based weather with forecasts via search queries
- **Stock Market Data**: Company financials and market data through search integration
- **Dynamic Result Processing**: Automatically detects and formats different result types
- **Flexible Response Modes**: Complete or compact JSON responses
- **JSON Responses**: Structured JSON output with complete or compact modes
- **Interactive UI (MCP Apps)**: Opt-in `search_table` and `search_dashboard` tools that render results as an interactive UI in supporting hosts

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
openclaw mcp add serpapi --url https://mcp.serpapi.com/YOUR_SERPAPI_API_KEY/mcp
```

**Claude Code**
```bash
claude mcp add --transport http serpapi https://mcp.serpapi.com/YOUR_SERPAPI_API_KEY/mcp
```

**Hermes**
```bash
hermes mcp add serpapi --url https://mcp.serpapi.com/YOUR_SERPAPI_API_KEY/mcp
```

**Codex**
```bash
codex mcp add serpapi --url https://mcp.serpapi.com/YOUR_SERPAPI_API_KEY/mcp
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

## Authentication

Two methods are supported:
- **Path-based**: `/YOUR_API_KEY/mcp` (recommended)
- **Header-based**: `Authorization: Bearer YOUR_API_KEY`

**Examples:**
```bash
# Path-based
curl "https://mcp.serpapi.com/your_key/mcp" -d '...'

# Header-based  
curl "https://mcp.serpapi.com/mcp" -H "Authorization: Bearer your_key" -d '...'
```

## Search Tool

The MCP server has one main Search Tool that supports all SerpApi engines and result types. You can find all available parameters on the [SerpApi API reference](https://serpapi.com/search-api).
Engine parameter schemas are also exposed as MCP resources: `serpapi://engines` (index) and `serpapi://engines/<engine>`.

The parameters you can provide are specific for each API engine. Some sample parameters are provided below:

- `params.q` (required): Search query
- `params.engine`: Search engine (default: "google_light") 
- `params.location`: Geographic filter
- `mode`: Response mode - "complete" (default) or "compact"
- ...see other parameters on the [SerpApi API reference](https://serpapi.com/search-api)

**Examples:**

```json
{"name": "search", "arguments": {"params": {"q": "coffee shops", "location": "Austin, TX"}}}
{"name": "search", "arguments": {"params": {"q": "weather in London"}}}
{"name": "search", "arguments": {"params": {"q": "AAPL stock"}}}
{"name": "search", "arguments": {"params": {"q": "news"}, "mode": "compact"}}
{"name": "search", "arguments": {"params": {"q": "detailed search"}, "mode": "complete"}}
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
