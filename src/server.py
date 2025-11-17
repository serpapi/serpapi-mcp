import uvicorn
from starlette.middleware.cors import CORSMiddleware
from fastmcp import FastMCP
from dotenv import load_dotenv
import os
import json
from typing import Dict, Any
from serpapi import SerpApiClient as SerpApiSearch
import httpx

load_dotenv()
API_KEY = os.getenv("SERPAPI_API_KEY")

if not API_KEY:
    raise ValueError(
        "SERPAPI_API_KEY not found in environment variables. Please set it in the .env file."
    )

mcp = FastMCP("SerpApi MCP Server", stateless_http=True, json_response=True)


def format_answer_box(answer_box: Dict[str, Any]) -> str:
    """Format answer_box results for weather, finance, and other structured data."""
    if answer_box.get("type") == "weather_result":
        result = f"Temperature: {answer_box.get('temperature', 'N/A')}\n"
        result += f"Unit: {answer_box.get('unit', 'N/A')}\n"
        result += f"Precipitation: {answer_box.get('precipitation', 'N/A')}\n"
        result += f"Humidity: {answer_box.get('humidity', 'N/A')}\n"
        result += f"Wind: {answer_box.get('wind', 'N/A')}\n"
        result += f"Location: {answer_box.get('location', 'N/A')}\n"
        result += f"Date: {answer_box.get('date', 'N/A')}\n"
        result += f"Weather: {answer_box.get('weather', 'N/A')}"

        # Add forecast if available
        if "forecast" in answer_box:
            result += "\n\nDaily Forecast:\n"
            for day in answer_box["forecast"]:
                result += f"{day.get('day', 'N/A')}: {day.get('weather', 'N/A')} "
                if "temperature" in day:
                    high = day["temperature"].get("high", "N/A")
                    low = day["temperature"].get("low", "N/A")
                    result += f"(High: {high}, Low: {low})"
                result += "\n"

        return result

    elif answer_box.get("type") == "finance_results":
        result = f"Title: {answer_box.get('title', 'N/A')}\n"
        result += f"Exchange: {answer_box.get('exchange', 'N/A')}\n"
        result += f"Stock: {answer_box.get('stock', 'N/A')}\n"
        result += f"Currency: {answer_box.get('currency', 'N/A')}\n"
        result += f"Price: {answer_box.get('price', 'N/A')}\n"
        result += f"Previous Close: {answer_box.get('previous_close', 'N/A')}\n"

        if "price_movement" in answer_box:
            pm = answer_box["price_movement"]
            result += f"Price Movement: {pm.get('price', 'N/A')} ({pm.get('percentage', 'N/A')}%) {pm.get('movement', 'N/A')}\n"

        if "table" in answer_box:
            result += "\nFinancial Metrics:\n"
            for row in answer_box["table"]:
                result += f"{row.get('name', 'N/A')}: {row.get('value', 'N/A')}\n"

        return result
    else:
        # Generic answer box formatting
        result = ""
        for key, value in answer_box.items():
            if key != "type":
                result += f"{key.replace('_', ' ').title()}: {value}\n"
        return result


def format_organic_results(organic_results: list) -> str:
    """Format organic search results."""
    formatted_results = []
    for result in organic_results:
        title = result.get("title", "No title")
        link = result.get("link", "No link")
        snippet = result.get("snippet", "No snippet")
        formatted_results.append(f"Title: {title}\nLink: {link}\nSnippet: {snippet}\n")
    return "\n".join(formatted_results) if formatted_results else ""


def format_news_results(news_results: list) -> str:
    """Format news search results."""
    formatted_results = []
    for result in news_results:
        title = result.get("title", "No title")
        link = result.get("link", "No link")
        snippet = result.get("snippet", "No snippet")
        date = result.get("date", "No date")
        source = result.get("source", "No source")
        formatted_results.append(
            f"Title: {title}\nSource: {source}\nDate: {date}\nLink: {link}\nSnippet: {snippet}\n"
        )
    return "\n".join(formatted_results) if formatted_results else ""


def format_images_results(images_results: list) -> str:
    """Format image search results."""
    formatted_results = []
    for result in images_results:
        title = result.get("title", "No title")
        link = result.get("link", "No link")
        thumbnail = result.get("thumbnail", "No thumbnail")
        formatted_results.append(
            f"Title: {title}\nImage: {link}\nThumbnail: {thumbnail}\n"
        )
    return "\n".join(formatted_results) if formatted_results else ""


@mcp.tool()
async def search(params: Dict[str, Any] = {}, raw: bool = False) -> str:
    """Universal search tool supporting all SerpApi engines and result types.

    This tool consolidates weather, stock, and general search functionality into a single interface.
    It dynamically processes multiple result types and provides structured output.

    Args:
        params: Dictionary of engine-specific parameters. Common parameters include:
            - q: Search query (required for most engines)
            - engine: Search engine to use (default: "google_light")
            - location: Geographic location filter
            - num: Number of results to return

        raw: If True, returns the raw JSON response from SerpApi (default: False)

    Returns:
        A formatted string of search results or raw JSON data, or an error message.

    Examples:
        Weather: {"q": "weather in London", "engine": "google"}
        Stock: {"q": "AAPL stock", "engine": "google"}
        General: {"q": "coffee shops", "engine": "google_light", "location": "Austin, TX"}
    """

    search_params = {
        "api_key": API_KEY,
        "engine": "google_light",  # Fastest engine by default
        **params,  # Include any additional parameters
    }

    try:
        search_client = SerpApiSearch(search_params)
        data = search_client.get_dict()

        # Return raw JSON if requested
        if raw:
            return json.dumps(data, indent=2, ensure_ascii=False)

        # Process results in priority order
        formatted_output = ""

        # 1. Answer box (weather, finance, knowledge graph, etc.) - highest priority
        if "answer_box" in data:
            formatted_output += "=== Answer Box ===\n"
            formatted_output += format_answer_box(data["answer_box"])
            formatted_output += "\n\n"

        # 2. News results
        if "news_results" in data and data["news_results"]:
            formatted_output += "=== News Results ===\n"
            formatted_output += format_news_results(data["news_results"])
            formatted_output += "\n\n"

        # 3. Organic results
        if "organic_results" in data and data["organic_results"]:
            formatted_output += "=== Search Results ===\n"
            formatted_output += format_organic_results(data["organic_results"])
            formatted_output += "\n\n"

        # 4. Image results
        if "images_results" in data and data["images_results"]:
            formatted_output += "=== Image Results ===\n"
            formatted_output += format_images_results(data["images_results"])
            formatted_output += "\n\n"

        # 5. Shopping results
        if "shopping_results" in data and data["shopping_results"]:
            formatted_output += "=== Shopping Results ===\n"
            shopping_results = []
            for result in data["shopping_results"]:
                title = result.get("title", "No title")
                price = result.get("price", "No price")
                link = result.get("link", "No link")
                source = result.get("source", "No source")
                shopping_results.append(
                    f"Title: {title}\nPrice: {price}\nSource: {source}\nLink: {link}\n"
                )
            formatted_output += "\n".join(shopping_results) + "\n\n"

        # Return formatted output or fallback message
        if formatted_output.strip():
            return formatted_output.strip()
        else:
            return "No results found for the given query. Try adjusting your search parameters or engine."

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            return "Error: Rate limit exceeded. Please try again later."
        elif e.response.status_code == 401:
            return "Error: Invalid API key. Please check your SERPAPI_API_KEY."
        else:
            return f"Error: {e.response.status_code} - {e.response.text}"
    except Exception as e:
        return f"Error: {str(e)}"


def main():
    starlette_app = mcp.http_app()

    starlette_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8000"))

    uvicorn.run(starlette_app, host=host, port=port)


if __name__ == "__main__":
    main()
