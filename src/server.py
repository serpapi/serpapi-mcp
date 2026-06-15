import json
import logging
import os
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import serpapi
import uvicorn
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.exceptions import NotFoundError
from fastmcp.resources import ResourceContent, ResourceResult
from fastmcp.server.dependencies import get_http_request
from mcp.types import Annotations, ToolAnnotations
from prefab_ui.actions import SetState
from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    H3,
    Alert,
    AlertDescription,
    AlertTitle,
    Card,
    CardContent,
    CardHeader,
    Column,
    DataTable,
    DataTableColumn,
    Grid,
    If,
    Link,
    Metric,
    Small,
    Text,
)
from prefab_ui.components.charts import PieChart
from prefab_ui.rx import STATE, Rx
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

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
    annotations=Annotations(
        audience=["assistant"],
        priority=0.3,
    ),
)
def engines_index() -> ResourceResult:
    engine_files = _get_engine_files()
    engines = [path.stem for path in engine_files]
    resource_content = json.dumps(
        {
            "count": len(engines),
            "engines": engines,
            "resources": [f"serpapi://engines/{engine}" for engine in engines],
            "schema": {
                "note": "Each engine resource uses a flat schema: params are engine-specific; common_params are shared SerpApi parameters.",
                "params_key": "params",
                "common_params_key": "common_params",
            },
        }
    )
    return ResourceResult(
        contents=[
            ResourceContent(content=resource_content, mime_type="application/json"),
        ]
    )


@mcp.resource(
    "serpapi://engines/{engine_name}",
    name="serpapi-engine",
    description=(
        "SerpApi engine specification. The URI parameter {engine_name} "
        "is the engine identifier (e.g. 'google', 'bing', 'walmart'). "
        "Use serpapi://engines to list valid values."
    ),
    mime_type="application/json",
    annotations=Annotations(
        audience=["assistant"],
        priority=0.3,
    ),
)
def get_engine_schema(engine_name: str) -> ResourceResult:
    if not re.fullmatch(r"[a-z0-9_]+", engine_name):
        raise NotFoundError(
            f"Invalid engine name: {engine_name!r}. Expected [a-z0-9_]+."
        )
    engine_path = ENGINES_DIR / f"{engine_name}.json"
    if not engine_path.exists():
        raise NotFoundError(
            f"Unknown engine: {engine_name!r}. See serpapi://engines for the full list."
        )
    return ResourceResult(
        contents=[
            # The json dump and load chain looks redundant - but it will help remove newlines from the file at `engine_path`,
            # making the response context efficient for LLMs
            ResourceContent(
                content=json.dumps(json.loads(engine_path.read_text())),
                mime_type="application/json",
            ),
        ]
    )


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
            return (
                "Error: SerpApi API key forbidden. "
                "Verify your subscription and key validity."
            )
    return f"Error: {extract_error_response(exception)}"


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


@mcp.tool(
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

    if params is None:
        params = {}

    request = get_http_request()
    api_key = getattr(getattr(request, "state", None), "api_key", None)
    if not api_key:
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

    except Exception as e:
        return map_search_error(e)


# ---------------------------------------------------------------------------
# MCP Apps (SEP-1865): interactive UI variants of `search`.
#
# These are opt-in: the plain-text `search` tool above is unchanged and stays
# the default. App-aware hosts can call `search_table` / `search_dashboard`
# to get an interactive UI rendered in the conversation; the bulk SERP JSON
# never enters the model context window. Hosts that don't support the Apps
# extension simply ignore these tools.
# ---------------------------------------------------------------------------


def _result_source(result: dict[str, Any]) -> str:
    """Best-effort source label for an organic result (explicit source or host)."""
    source = result.get("source")
    if source:
        return str(source)
    host = urlparse(result.get("link", "") or "").netloc
    return host[4:] if host.startswith("www.") else host


def organic_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten SerpApi organic_results into compact, table-ready rows."""
    rows: list[dict[str, Any]] = []
    for index, result in enumerate(data.get("organic_results") or [], start=1):
        rows.append(
            {
                "position": result.get("position", index),
                "title": result.get("title", ""),
                "link": result.get("link", ""),
                "source": _result_source(result),
                "snippet": result.get("snippet", ""),
            }
        )
    return rows


def source_breakdown(
    rows: list[dict[str, Any]], limit: int = 8
) -> list[dict[str, Any]]:
    """Count results per source for the dashboard pie chart (top `limit`)."""
    counts = Counter(row["source"] for row in rows if row["source"])
    return [
        {"source": source, "count": count}
        for source, count in counts.most_common(limit)
    ]


def dashboard_summary(data: dict[str, Any]) -> dict[str, Any]:
    """Derive the dashboard view-model from a SerpApi response."""
    params = data.get("search_parameters") or {}
    info = data.get("search_information") or {}
    rows = organic_rows(data)
    return {
        "query": params.get("q", ""),
        "engine": params.get("engine", ""),
        "total_results": info.get("total_results"),
        "result_count": len(rows),
        "rows": rows,
        "sources": source_breakdown(rows),
    }


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


def _error_app(message: str) -> PrefabApp:
    """Render an upstream/search error as an Apps alert instead of raw text."""
    with PrefabApp(title="Search error") as app:
        with Alert(variant="destructive"):
            AlertTitle(content="Search failed")
            AlertDescription(content=message)
    return app


_ORGANIC_COLUMNS = [
    DataTableColumn(key="position", header="#", sortable=True, width="64px"),
    DataTableColumn(key="title", header="Title", sortable=True),
    DataTableColumn(key="source", header="Source", sortable=True),
    DataTableColumn(key="snippet", header="Snippet"),
]


@mcp.tool(
    app=True,
    description=(
        "Interactive UI variant of `search`: returns organic results as a "
        "sortable, searchable table rendered in the conversation. Same params "
        "as `search`. Use when the host supports MCP Apps and the user wants "
        "to browse results visually rather than read JSON."
    ),
    annotations=ToolAnnotations(
        title="SerpApi search (table)",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def search_table(params: dict[str, Any] = None) -> PrefabApp:
    try:
        data = fetch_search_data(params)
    except Exception as exc:
        return _error_app(
            str(exc) if isinstance(exc, RuntimeError) else map_search_error(exc)
        )

    rows = organic_rows(data)
    with PrefabApp(title="Search results") as app:
        with Column(gap=4, css_class="p-4"):
            DataTable(
                columns=_ORGANIC_COLUMNS,
                rows=rows,
                search=True,
                paginated=True,
                page_size=10,
            )
    return app


@mcp.tool(
    app=True,
    description=(
        "Interactive dashboard variant of `search`: returns summary metrics, a "
        "source breakdown chart, and a results table with a click-to-expand "
        "detail panel, all rendered in the conversation. Same params as "
        "`search`. Use for a richer visual overview of a query's results."
    ),
    annotations=ToolAnnotations(
        title="SerpApi search (dashboard)",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def search_dashboard(params: dict[str, Any] = None) -> PrefabApp:
    try:
        data = fetch_search_data(params)
    except Exception as exc:
        return _error_app(
            str(exc) if isinstance(exc, RuntimeError) else map_search_error(exc)
        )

    summary = dashboard_summary(data)
    total = summary["total_results"]

    with PrefabApp(title="Search dashboard", state={"selected": None}) as app:
        with Column(gap=4, css_class="p-4"):
            with Grid(columns=[1, 1, 1], gap=4):
                Metric(label="Query", value=summary["query"] or "—")
                Metric(label="Engine", value=summary["engine"] or "—")
                Metric(
                    label="Results shown",
                    value=str(summary["result_count"]),
                    description=(
                        f"of ~{total:,} total" if isinstance(total, int) else None
                    ),
                )

            with Grid(columns=[1, 2], gap=4):
                if summary["sources"]:
                    PieChart(
                        data=summary["sources"],
                        data_key="count",
                        name_key="source",
                        show_legend=True,
                    )
                DataTable(
                    columns=_ORGANIC_COLUMNS,
                    rows=summary["rows"],
                    search=True,
                    on_row_click=SetState("selected", Rx("$event")),
                )

            with If(STATE.selected):
                with Card():
                    with CardHeader():
                        H3(Rx("selected.title"))
                        Small(content=Rx("selected.source"))
                    with CardContent():
                        with Column(gap=2):
                            Text(content=Rx("selected.snippet"))
                            Link(
                                content=Rx("selected.link"),
                                href=Rx("selected.link"),
                                target="_blank",
                            )
    return app


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
