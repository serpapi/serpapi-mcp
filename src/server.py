import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from fastmcp import FastMCP
from fastmcp.server.providers import FileSystemProvider
from mcp.types import Icon

from src.version import __version__

COMPONENTS_DIR = Path(__file__).parent / "mcp_components"


mcp = FastMCP(
    "SerpApi MCP Server",
    version=__version__,
    website_url="https://serpapi.com/integrations/mcp",
    icons=[
        Icon(
            src="https://serpapi.com/android-chrome-512x512.png",
            mimeType="image/png",
            sizes=["512x512"],
        )
    ],
    instructions=(
        "Use the search tool for live SerpApi results. Use the "
        "`serpapi://engines` resource to discover valid SerpApi engine names, then read "
        "the `serpapi://engines/{engine_name}` resource template with the desired engine "
        "name to inspect its supported parameters."
    ),
    providers=[FileSystemProvider(COMPONENTS_DIR)],
)

load_dotenv()

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


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


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Attach the caller's SerpApi key to the request.

    The key comes from ``Authorization: Bearer {API_KEY}`` or the
    ``/{API_KEY}/mcp`` path form, which is rewritten to ``/mcp``. A request
    without a key passes through: the handshake needs none, and tools check
    for it themselves.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # /.well-known/mcp/server-card.json would otherwise match /{API_KEY}/mcp.
        if path == "/healthcheck" or path.startswith("/.well-known/"):
            return await call_next(request)

        api_key = None
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() == "bearer":  # auth schemes are case-insensitive (RFC 7235)
            api_key = token.strip()

        original_path = request.scope.get("path", "")
        path_parts = original_path.strip("/").split("/") if original_path else []

        if not api_key and len(path_parts) >= 2 and path_parts[1] == "mcp":
            api_key = path_parts[0]

            new_path = "/" + "/".join(path_parts[1:])
            request.scope["path"] = new_path
            request.scope["raw_path"] = new_path.encode("utf-8")

        request.state.api_key = api_key or None
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


async def healthcheck_handler(request):
    return JSONResponse(
        {
            "status": "healthy",
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "service": "SerpApi MCP Server",
        }
    )


middleware = [
    Middleware(RequestMetricsMiddleware),
    Middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    ),
    Middleware(ApiKeyMiddleware),
]
starlette_app = mcp.http_app(
    middleware=middleware, stateless_http=True, json_response=True
)

starlette_app.add_route("/healthcheck", healthcheck_handler, methods=["GET"])

if __name__ == "__main__":
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8000"))

    uvicorn.run(starlette_app, host=host, port=port, ws="none")
