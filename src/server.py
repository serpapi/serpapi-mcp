import uvicorn
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.requests import Request
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from dotenv import load_dotenv
import os
import json
from typing import Any
import serpapi
import httpx
import logging
from datetime import datetime

load_dotenv()

mcp = FastMCP("SerpApi MCP Server", stateless_http=True, json_response=True)
logger = logging.getLogger(__name__)


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip authentication for healthcheck endpoint
        if request.url.path == "/healthcheck":
            return await call_next(request)

        api_key = None

        auth = request.headers.get("Authorization")
        if auth and auth.startswith("Bearer "):
            api_key = auth.split(" ", 1)[1].strip()

        original_path = request.scope.get("path", "")
        path_parts = original_path.strip("/").split("/") if original_path else []

        if not api_key and len(path_parts) >= 2 and path_parts[1] == "mcp":
            api_key = path_parts[0]

            new_path = "/" + "/".join(path_parts[1:])
            request.scope["path"] = new_path
            request.scope["raw_path"] = new_path.encode("utf-8")

        # 3. Validate API key exists
        if not api_key:
            return JSONResponse(
                {
                    "error": "Missing API key. Use path format /{API_KEY}/mcp or Authorization: Bearer {API_KEY} header"
                },
                status_code=401,
            )

        # Store API key in request state for tools to access
        request.state.api_key = api_key
        return await call_next(request)


@mcp.tool()
async def search(params: dict[str, Any] = {}, mode: str = "complete") -> str:
    """Universal search tool supporting all SerpApi engines and result types.

    This tool consolidates weather, stock, and general search functionality into a single interface.
    It processes multiple result types and returns structured JSON output.

    Args:
        params: Dictionary of engine-specific parameters. Common parameters include:
            - q: Search query (required for most engines)
            - engine: Search engine to use (default: "google_light")
            - location: Geographic location filter
            - num: Number of results to return

        mode: Response mode (default: "complete")
            - "complete": Returns full JSON response with all fields
            - "compact": Returns JSON response with metadata fields removed

    Returns:
        A JSON string containing search results or an error message.

    Examples:
        Weather: {"params": {"q": "weather in London", "engine": "google"}, "mode": "complete"}
        Stock: {"params": {"q": "AAPL stock", "engine": "google"}, "mode": "complete"}
        General: {"params": {"q": "coffee shops", "engine": "google_light", "location": "Austin, TX"}, "mode": "complete"}
        Compact: {"params": {"q": "news"}, "mode": "compact"}

    Supported engines:
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

    # Validate mode parameter
    if mode not in ["complete", "compact"]:
        return "Error: Invalid mode. Must be 'complete' or 'compact'"

    request = get_http_request()
    if hasattr(request, "state") and request.state.api_key:
        api_key = request.state.api_key
    else:
        return "Error: Unable to access API key from request context"

    search_params = {
        "api_key": api_key,
        "engine": "google_light",  # Fastest engine by default
        **params,  # Include any additional parameters
    }

    try:
        data = serpapi.search(search_params).as_dict()

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

    except serpapi.exceptions.HTTPError as e:
        if "429" in str(e):
            return "Error: Rate limit exceeded. Please try again later."
        elif "401" in str(e):
            return "Error: Invalid SerpApi API key. Check your API key in the path or Authorization header."
        elif "403" in str(e):
            return "Error: SerpApi API key forbidden. Verify your subscription and key validity."
        else:
            return f"Error: {str(e)}"
    except Exception as e:
        return f"Error: {str(e)}"


async def healthcheck_handler(request):
    return JSONResponse(
        {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "service": "SerpApi MCP Server",
        }
    )


def main():
    middleware = [
        Middleware(ApiKeyMiddleware),
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        ),
    ]
    starlette_app = mcp.http_app(middleware=middleware)

    starlette_app.add_route("/healthcheck", healthcheck_handler, methods=["GET"])

    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8000"))

    uvicorn.run(starlette_app, host=host, port=port)


if __name__ == "__main__":
    main()
