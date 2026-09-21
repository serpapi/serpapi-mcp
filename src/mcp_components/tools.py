import json
import os
from typing import Any

import serpapi
from fastmcp import Context
from fastmcp.server.dependencies import get_http_request
from fastmcp.tools import ToolResult, tool
from mcp.types import InputRequiredResult, ToolAnnotations
from serpapi.models import SerpResults

from src.search_input import prepare_search_input


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
                "Check the key in the request path or Authorization header, "
                "or in SERPAPI_API_KEY for stdio hosts."
            )
        if "403" in text:
            return (
                "Error: SerpApi API key forbidden. "
                "Verify your subscription and key validity."
            )
    return f"Error: {extract_error_response(exception)}"


def _text_result(content: str, *, is_error: bool = False) -> ToolResult:
    """Preserve the response wrapper produced by the original string-returning tool."""
    return ToolResult(
        content=content,
        structured_content={"result": content},
        is_error=is_error,
    )


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
                - output: Optional response format. Omit for JSON (default), or set to "md" for Markdown.
    
            Engine-specific parameters are available via MCP resources:
                - serpapi://engines lists all supported engines.
                - serpapi://engines/<engine> provides parameters and options for one engine.
    
        mode: Response mode. Defaults to "complete".
            - "complete": Return the full SerpApi response.
            - "compact": Remove metadata fields from JSON responses. Markdown is returned unchanged.
    
    Output schema:
        structuredContent.result contains the response string: serialized JSON or unchanged Markdown.
        The same string is included in text content. Tool failures preserve this wrapper and set isError to true.

    Guided search:
        Searches can request missing engine parameters from supporting clients using the engine catalog and engine-specific rules.
        Flights collect airports and dates, hotels collect the destination and stay dates, and directions collect missing endpoints.
        Other clients receive a missing-parameter error. Cancellation does not run a search.

    Examples:
        Weather: {"params": {"q": "weather in London", "engine": "google"}, "mode": "complete"}
        Stock: {"params": {"q": "AAPL stock", "engine": "google"}, "mode": "complete"}
        General: {"params": {"q": "coffee shops", "engine": "google_light", "location": "Austin, TX"}, "mode": "complete"}
        Compact: {"params": {"q": "news"}, "mode": "compact"}
        Markdown: {"params": {"q": "news", "output": "md"}}

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
    output_schema={
        "type": "object",
        "properties": {"result": {"type": "string"}},
        "required": ["result"],
        "x-fastmcp-wrap-result": True,
    },
    annotations=ToolAnnotations(
        title="SerpApi search",
        readOnlyHint=True,  # search is read-only; no state mutation
        destructiveHint=False,  # nothing deleted or modified
        idempotentHint=False,  # SERP can change between calls; cache is 1h
        openWorldHint=True,  # talks to external search engines
    ),
)
async def search(
    params: dict[str, Any] | None = None,
    mode: str = "complete",
    ctx: Context | None = None,
) -> ToolResult | InputRequiredResult:
    """Universal search tool supporting all SerpApi engines and result types.

    Args:
        params: Dictionary of SerpApi engine-specific parameters. Common parameters include:
            - q: Search query (required for most engines)
            - engine: Search engine to use (default: "google_light")
            - location: Geographic location filter
            - output: Response format; omit for JSON or set to "md" for Markdown

        mode: Response mode (default: "complete")
            - "complete": Returns the full response
            - "compact": Removes metadata fields from JSON responses; Markdown is unchanged

    Returns:
        A wrapped response string, a wrapped tool error, or a search input request.
    """

    # Validate mode parameter
    if mode not in ["complete", "compact"]:
        return _text_result(
            content="Error: Invalid mode. Must be 'complete' or 'compact'",
            is_error=True,
        )

    output = (params or {}).get("output", "json")
    if output not in ("json", "md"):
        return _text_result(
            content="Error: Invalid output. Use either 'md' or 'json' for the output parameter.",
            is_error=True,
        )

    try:
        prepared = prepare_search_input(params or {}, ctx)
        if isinstance(prepared, InputRequiredResult):
            return prepared
        if isinstance(prepared, ToolResult):
            return _text_result(prepared.content[0].text, is_error=prepared.is_error)
        params = prepared
        response = fetch_search_response(params)
        if isinstance(response, str):
            if output == "md":
                return _text_result(content=response)
            return _text_result(
                content="Error: SerpApi returned text when JSON output was requested.",
                is_error=True,
            )

        data = response.as_dict()
        # Successful searches with no results can also contain an error message.
        status = data.get("search_metadata", {}).get("status")
        if data.get("error") and status != "Success":
            return _text_result(content=f"Error: {data['error']}", is_error=True)

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

        return _text_result(content=json.dumps(data, indent=2, ensure_ascii=False))

    except RuntimeError as e:
        return _text_result(content=str(e), is_error=True)
    except Exception as e:
        return _text_result(content=map_search_error(e), is_error=True)


def resolve_api_key() -> str:
    """Return the caller's SerpApi key, or raise naming the fix.

    Over HTTP the key is the one ``ApiKeyMiddleware`` attached to the request.
    Stdio hosts (the Claude Desktop bundle) have no request and use
    ``SERPAPI_API_KEY`` instead; the hosted server never does.
    """
    try:
        request = get_http_request()
    except RuntimeError:  # no HTTP request: running over stdio
        api_key = os.getenv("SERPAPI_API_KEY")
        hint = "Set the SERPAPI_API_KEY environment variable."
    else:
        api_key = getattr(getattr(request, "state", None), "api_key", None)
        hint = (
            "Use path format /{API_KEY}/mcp or Authorization: Bearer {API_KEY} header."
        )
    if not api_key:
        raise RuntimeError(f"Error: Missing API key. {hint}")
    return api_key


def fetch_search_response(params: dict[str, Any] | None) -> SerpResults | str:
    """Run a SerpApi search using the caller's API key. Raises on failure."""
    api_key = resolve_api_key()

    # api_key set last so caller params can never override the trusted key.
    search_params = {
        "engine": "google_light",
        **(params or {}),
        "api_key": api_key,
    }
    # The SDK returns raw text for non-JSON responses, including Markdown.
    return serpapi.search(search_params)


def fetch_search_data(params: dict[str, Any] | None) -> dict[str, Any]:
    """Return structured JSON data for MCP Apps, regardless of text output params."""
    json_params = {**(params or {}), "output": "json"}
    return fetch_search_response(json_params).as_dict()
