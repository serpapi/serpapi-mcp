# <img src="https://user-images.githubusercontent.com/307597/154772945-1b7dba5f-21cf-41d0-bb2e-65b6eff4aaaf.png" width="30" height="30"/> SerpApi MCP Server

A Model Context Protocol (MCP) server implementation that integrates with [SerpApi](https://serpapi.com) for comprehensive search engine results and data extraction.

[![Build](https://github.com/serpapi/mcp-server/actions/workflows/python-package.yml/badge.svg)](https://github.com/serpapi/mcp-server/actions/workflows/python-package.yml)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## Features

- **Multi-Engine Search**: Google, Bing, Yahoo, DuckDuckGo, Yandex, Baidu, YouTube, eBay, Walmart, and more
- **Real-time Weather Data**: Location-based weather with forecasts via search queries
- **Stock Market Data**: Company financials and market data through search integration
- **Dynamic Result Processing**: Automatically detects and formats different result types
- **Raw JSON Support**: Option to return full unprocessed API responses
- **Structured Results**: Clean, formatted output optimized for AI consumption
- **Rate Limit Handling**: Automatic retry logic with exponential backoff
- **Error Recovery**: Comprehensive error handling and user feedback

## Installation

```bash
git clone https://github.com/serpapi/mcp-server.git
cd mcp-server
uv sync
```

## Configuration

### Environment Variables

#### Required
- `SERPAPI_API_KEY`: Your SerpApi API key from [serpapi.com/manage-api-key](https://serpapi.com/manage-api-key)

### Setup Steps

1. **Get API Key**: Sign up at [SerpApi](https://serpapi.com) and get your API key
2. **Create .env file**:
   ```bash
   SERPAPI_API_KEY=your_api_key_here
   ```
3. **Run Server**:
   ```bash
   uv run src/server.py
   ```

## Running with Docker

```bash
# Build the image
docker build -t serpapi-mcp-server .

# Run the container
docker run -e SERPAPI_API_KEY=<your_api_key> -p 8000:8000 serpapi-mcp-server
```

## Client Configurations

### Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "serpapi": {
      "command": "uv",
      "args": ["run", "/path/to/mcp-server/src/server.py"],
      "env": {
        "SERPAPI_API_KEY": "your_api_key_here"
      }
    }
  }
}
```


## Available Tools

### Universal Search Tool (`search`)

The consolidated search tool that handles all search types through a single interface.

**Best for:**
- Any type of search query (web, weather, stock, images, news, shopping)
- Unified interface across all search engines and result types
- Both formatted output and raw JSON responses

**Parameters:**
- `params` (Dict): Search parameters including:
  - `q` (str): Search query (required)
  - `engine` (str): Search engine (default: "google_light")
  - `location` (str): Geographic location filter
  - `num` (int): Number of results (default: 10)
- `raw` (bool): Return raw JSON response (default: false)

**Usage Examples:**

#### General Search
```json
{
  "name": "search",
  "arguments": {
    "params": {
      "q": "best coffee shops",
      "engine": "google",
      "location": "Austin, TX"
    }
  }
}
```

#### Weather Search
```json
{
  "name": "search",
  "arguments": {
    "params": {
      "q": "weather in London",
      "engine": "google"
    }
  }
}
```

#### Stock Market Search
```json
{
  "name": "search",
  "arguments": {
    "params": {
      "q": "AAPL stock price",
      "engine": "google"
    }
  }
}
```

#### News Search
```json
{
  "name": "search",
  "arguments": {
    "params": {
      "q": "latest AI developments",
      "engine": "google",
      "tbm": "nws"
    }
  }
}
```

#### Raw JSON Output
```json
{
  "name": "search",
  "arguments": {
    "params": {
      "q": "machine learning",
      "engine": "google"
    },
    "raw": true
  }
}
```

## Supported Search Engines

- **Google** (`google`) - Full Google search results
- **Google Light** (`google_light`) - Faster, lightweight Google results (default)
- **Bing** (`bing`) - Microsoft Bing search
- **Yahoo** (`yahoo`) - Yahoo search results
- **DuckDuckGo** (`duckduckgo`) - Privacy-focused search
- **Yandex** (`yandex`) - Russian search engine
- **Baidu** (`baidu`) - Chinese search engine
- **YouTube** (`youtube_search`) - Video search
- **eBay** (`ebay`) - Product search
- **Walmart** (`walmart`) - Product search

For a complete list, visit [SerpApi Engines](https://serpapi.com/search-engines).

## Result Types

The search tool automatically detects and formats different result types:

- **Answer Box**: Weather data, stock prices, knowledge graph, calculations
- **Organic Results**: Traditional web search results
- **News Results**: News articles with source and date
- **Image Results**: Images with thumbnails and links
- **Shopping Results**: Product listings with prices and sources

Results are prioritized and formatted for optimal readability.

## Error Handling

The server provides comprehensive error handling:

- **Rate Limiting**: Automatic retry with exponential backoff
- **Authentication**: Clear API key validation messages  
- **Network Issues**: Graceful degradation and error reporting
- **Invalid Parameters**: Helpful parameter validation

Common error responses:
```json
{
  "error": "Rate limit exceeded. Please try again later."
}
```

## Development

### Running in Development Mode

```bash
# Install dependencies
uv sync

# Run server directly
uv run src/server.py
```

### Using MCP Inspector

The MCP Inspector provides a web interface for testing MCP tools.

```bash
# Install (requires Node.js)
npm install -g @modelcontextprotocol/inspector

# Run inspector
npx @modelcontextprotocol/inspector
```

Then configure: URL `localhost:8000/mcp`, Transport "Streamable HTTP transport", and click "List tools".

### Project Structure

```
serpapi-mcp-server/
├── src/
│   └── server.py           # Main MCP server implementation
├── pyproject.toml         # Project configuration  
├── README.md              # This file
├── LICENSE               # MIT License
└── .env.example          # Environment template
```

## Usage Examples

### Basic Search
```python
# Search for information
result = await client.call_tool("search", {
    "params": {
        "q": "MCP protocol documentation",
        "engine": "google"
    }
})
```

### Weather Query
```python
# Get weather information
weather = await client.call_tool("search", {
    "params": {
        "q": "weather in San Francisco with forecast",
        "engine": "google"
    }
})
```

### Stock Information
```python
# Get stock data
stock = await client.call_tool("search", {
    "params": {
        "q": "Tesla stock price and market cap",
        "engine": "google"
    }
})
```

### Raw JSON Response
```python
# Get full API response
raw_data = await client.call_tool("search", {
    "params": {
        "q": "artificial intelligence",
        "engine": "google"
    },
    "raw": True
})
```

## Troubleshooting

### Common Issues

**"Invalid API key" Error:**
- Verify your API key at [serpapi.com/manage-api-key](https://serpapi.com/manage-api-key)
- Check that `SERPAPI_API_KEY` is set in your environment

**"Rate limit exceeded" Error:**
- Wait for the retry period
- Consider upgrading your SerpApi plan
- Reduce request frequency

**"Module not found" Error:**
- Ensure dependencies are installed: `uv install` or `pip install mcp serpapi python-dotenv`
- Check Python version compatibility (3.13+ required)

**"No results found" Error:**
- Try adjusting your search query
- Use a different search engine
- Check if the query is valid for the selected engine

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

