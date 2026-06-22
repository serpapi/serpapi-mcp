import json
from typing import Any

import serpapi
from fastmcp.server.dependencies import get_http_request
from fastmcp.tools import tool
from mcp.types import ToolAnnotations


def extract_error_response(exception) -> str:
    """
    Helper function to extract meaningful error information from nested exceptions.

    Traverses exception.args[0] chain until it finds a valid .response object,
    then attempts to extract JSON from response.json(). Falls back to str(e).

    Args:
        exception: The exception to process

    Returns:
        str: Formatted error message with response data if available
    """
    current = exception
    max_depth = 10
    depth = 0

    while depth < max_depth:
        if hasattr(current, "response") and current.response is not None:
            try:
                response_data = current.response.json()
                return json.dumps(response_data, indent=2)
            except (ValueError, AttributeError, TypeError):
                try:
                    return current.response.text
                except (AttributeError, TypeError):
                    pass

        if hasattr(current, "args") and current.args and len(current.args) > 0:
            current = current.args[0]
            depth += 1
        else:
            break

    # Fallback
    return str(exception)


def map_search_error(exception) -> str:
    """Map a SerpApi/transport exception to a user-facing 'Error: ...' string.

    Shared by the text `search` tool and the App tools so all entry points
    surface identical messages for the same upstream failure.
    """
    if isinstance(exception, serpapi.exceptions.HTTPError):
        text = str(exception)
        if "429" in text:
            return "Error: Rate limit exceeded. Please try again later."
        if "401" in text:
            return (
                "Error: Invalid SerpApi API key. "
                "Check your API key in the path or Authorization header."
            )
        if "403" in text:
            return "Error: SerpApi API key forbidden. " "Verify your subscription and key validity."
    return f"Error: {extract_error_response(exception)}"


search_tool_description = """Universal search tool supporting all SerpApi engines and result types.

    When to use:
        - Any query needing live, structured SERP data: web results, news, product listings, job postings, local businesses, flight/hotel prices, video results, images, stock/weather cards, knowledge graph entities.
    
    Engine discovery via MCP resources:
        - serpapi://engines lists all engines supported by this tool.
        - serpapi://engines/<engine> provides engine-specific parameters and supported options.
        - Example: serpapi://engines/google_news

    Input schema:
        params: JSON object containing SerpApi engine parameters.
            Common parameters:
                - q: Search query. Required for most engines.
                - engine: SerpApi engine name. Defaults to "google_light".
                - location: Optional geographic location for localized results.
                - num: Optional number of results to return.
    
            Engine-specific parameters are available via MCP resources:
                - serpapi://engines lists all supported engines.
                - serpapi://engines/<engine> provides parameters and options for one engine.
    
        mode: Response mode. Defaults to "complete".
            - "complete": Return the full SerpApi JSON response.
            - "compact": Return a reduced response with metadata removed.
    
    Output schema:
        JSON string containing search results, structured engine output, or an error message.

    Examples:
        Weather: {"params": {"q": "weather in London", "engine": "google"}, "mode": "complete"}
        Stock: {"params": {"q": "AAPL stock", "engine": "google"}, "mode": "complete"}
        General: {"params": {"q": "coffee shops", "engine": "google_light", "location": "Austin, TX"}, "mode": "complete"}
        Compact: {"params": {"q": "news"}, "mode": "compact"}

    Supported engines include (not limited to):
        - google
        - google_light
        - google_flights
        - google_hotels
        - google_images
        - google_news
        - google_local
        - google_shopping
        - google_jobs
        - bing
        - yahoo
        - duckduckgo
        - youtube_search
        - baidu
        - ebay
    """


@tool(
    description=search_tool_description,
    annotations=ToolAnnotations(
        title="SerpApi search",
        readOnlyHint=True,  # search is read-only; no state mutation
        destructiveHint=False,  # nothing deleted or modified
        idempotentHint=False,  # SERP can change between calls; cache is 1h
        openWorldHint=True,  # talks to external search engines
    ),
)
async def search(params: dict[str, Any] = None, mode: str = "complete") -> str:
    """Universal search tool supporting all SerpApi engines and result types.

    Args:
        params: Dictionary of SerpApi engine-specific parameters. Common parameters include:
            - q: Search query (required for most engines)
            - engine: Search engine to use (default: "google_light")
            - location: Geographic location filter
            - num: Number of results to return

        mode: Response mode (default: "complete")
            - "complete": Returns full JSON response with all fields
            - "compact": Returns JSON response with metadata fields removed

    Returns:
        A JSON string containing search results or an error message.
    """

    # Validate mode parameter
    if mode not in ["complete", "compact"]:
        return "Error: Invalid mode. Must be 'complete' or 'compact'"

    try:
        data = fetch_search_data(params)

        # Apply mode-specific filtering
        if mode == "compact":
            # Remove specified fields for compact mode
            fields_to_remove = [
                "search_metadata",
                "search_parameters",
                "search_information",
                "pagination",
                "serpapi_pagination",
            ]
            for field in fields_to_remove:
                data.pop(field, None)

        # Return JSON response for both modes
        return json.dumps(data, indent=2, ensure_ascii=False)

    except RuntimeError as e:
        return str(e)
    except Exception as e:
        return map_search_error(e)


def fetch_search_data(params: dict[str, Any] | None) -> dict[str, Any]:
    """Run a SerpApi search using the request's API key. Raises on failure."""
    request = get_http_request()
    api_key = getattr(getattr(request, "state", None), "api_key", None)
    if not api_key:
        raise RuntimeError("Error: Unable to access API key from request context")

    search_params = {
        "api_key": api_key,
        "engine": "google_light",
        **(params or {}),
    }
    return serpapi.search(search_params).as_dict()
