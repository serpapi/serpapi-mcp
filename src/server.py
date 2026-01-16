import uvicorn
import time
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.requests import Request
from fastmcp import FastMCP
from fastmcp.resources.resource import Resource
from fastmcp.server.dependencies import get_http_request
from dotenv import load_dotenv
import os
import json
from typing import Any
import serpapi
import logging
from datetime import datetime
from pathlib import Path
import re

load_dotenv()

mcp = FastMCP("SerpApi MCP Server")
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

ENGINES_DIR = Path(__file__).resolve().parents[1] / "engines"


def _get_engine_files() -> list[Path]:
    if not ENGINES_DIR.exists():
        logger.warning("Engines directory not found: %s", ENGINES_DIR)
        return []
    return sorted(ENGINES_DIR.glob("*.json"))


@mcp.resource(
    "serpapi://engines",
    name="serpapi-engines-index",
    description="Index of available SerpApi engines and their resource URIs.",
    mime_type="application/json",
)
def engines_index() -> dict[str, Any]:
    engine_files = _get_engine_files()
    engines = [path.stem for path in engine_files]
    return {
        "count": len(engines),
        "engines": engines,
        "resources": [f"serpapi://engines/{engine}" for engine in engines],
        "schema": {
            "note": "Each engine resource uses a flat schema: params are engine-specific; common_params are shared SerpApi parameters.",
            "params_key": "params",
            "common_params_key": "common_params",
        },
    }


def _engine_resource_factory(engine: str, engine_path: Path) -> Resource:
    def _load_engine() -> dict[str, Any]:
        return json.loads(engine_path.read_text())

    return Resource.from_function(
        fn=_load_engine,
        uri=f"serpapi://engines/{engine}",
        name=f"serpapi-engine-{engine}",
        description=f"SerpApi engine specification for {engine}.",
        mime_type="application/json",
    )


for _engine_path in _get_engine_files():
    _engine_name = _engine_path.stem
    # Only allow alphanumeric and underscores in engine names.
    if not re.fullmatch(r"[a-z0-9_]+", _engine_name):
        logger.warning("Skipping invalid engine filename: %s", _engine_name)
        continue
    mcp.add_resource(_engine_resource_factory(_engine_name, _engine_path))


def emit_metric(namespace: str, metrics: dict, dimensions: dict = {}):
    emf_event = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": namespace,
                    "Dimensions": [list(dimensions.keys())] if dimensions else [],
                    "Metrics": [
                        {"Name": name, "Unit": unit}
                        for name, (_, unit) in metrics.items()
                    ],
                }
            ],
        },
        **dimensions,
        **{name: value for name, (value, _) in metrics.items()},
    }

    logger.info(json.dumps(emf_event))


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


class RequestMetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration = time.time() - start

        emit_metric(
            namespace="mcp",
            metrics={
                "RequestCount": (1, "Count"),
                "ResponseTime": (duration * 1000, "Milliseconds"),
            },
            dimensions={
                "Service": "mcp-server-api",
                "Method": request.method,
                "StatusCode": str(response.status_code),
            },
        )

        return response


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

    Engine params are available via resources at serpapi://engines/<engine> (index: serpapi://engines).
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
            return f"Error: Rate limit exceeded. Please try again later."
        elif "401" in str(e):
            return f"Error: Invalid SerpApi API key. Check your API key in the path or Authorization header."
        elif "403" in str(e):
            return f"Error: SerpApi API key forbidden. Verify your subscription and key validity."
        else:
            error_msg = extract_error_response(e)
            return f"Error: {error_msg}"
    except Exception as e:
        error_msg = extract_error_response(e)
        return f"Error: {error_msg}"


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
        Middleware(RequestMetricsMiddleware),
        Middleware(ApiKeyMiddleware),
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        ),
    ]
    starlette_app = mcp.http_app(
        middleware=middleware, stateless_http=True, json_response=True
    )

    starlette_app.add_route("/healthcheck", healthcheck_handler, methods=["GET"])

    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8000"))

    uvicorn.run(starlette_app, host=host, port=port, ws="none")


if __name__ == "__main__":
    main()
