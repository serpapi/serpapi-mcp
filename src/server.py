import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import serpapi
import uvicorn
from dotenv import load_dotenv
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from fastmcp import FastMCP
from fastmcp.server.providers import FileSystemProvider

from src.mcp_components.resources import complete_engine_name
from src.version import __version__

COMPONENTS_DIR = Path(__file__).parent / "mcp_components"

load_dotenv()

PUBLIC_ORIGIN = os.getenv("MCP_PUBLIC_ORIGIN", "").rstrip("/")

# Authorization server for RFC 9728 discovery (serpapi/SerpApi#10015).
OAUTH_AUTHORIZATION_SERVER = os.getenv(
    "MCP_OAUTH_AUTHORIZATION_SERVER", "https://serpapi.com"
)
OAUTH_PROTECTED_RESOURCE_PATH = "/.well-known/oauth-protected-resource"
OAUTH_INTROSPECTION_URL = os.getenv(
    "MCP_OAUTH_INTROSPECTION_URL", f"{OAUTH_AUTHORIZATION_SERVER}/oauth/introspect"
)
OAUTH_CLIENT_ID = os.getenv("MCP_OAUTH_CLIENT_ID")
OAUTH_CLIENT_SECRET = os.getenv("MCP_OAUTH_CLIENT_SECRET")
OAUTH_INTROSPECTION_ENABLED = bool(OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET)


mcp = FastMCP(
    "SerpApi MCP Server",
    version=__version__,
    website_url="https://github.com/serpapi/mcp-server",
    instructions=(
        "Use the search tool for live SerpApi results. Use the "
        "`serpapi://engines` resource to discover valid SerpApi engine names, then read "
        "the `serpapi://engines/{engine_name}` resource template with the desired engine "
        "name to inspect its supported parameters."
    ),
    providers=[FileSystemProvider(COMPONENTS_DIR)],
)
mcp.completion(complete_engine_name)

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


def public_origin(request: Request) -> str:
    return PUBLIC_ORIGIN or f"{request.url.scheme}://{request.url.netloc}"


def resource_metadata_url(request: Request) -> str:
    return f"{public_origin(request)}{OAUTH_PROTECTED_RESOURCE_PATH}"


async def oauth_protected_resource_handler(request: Request):
    return JSONResponse(
        {
            "resource": f"{public_origin(request)}/mcp",
            "authorization_servers": [OAUTH_AUTHORIZATION_SERVER],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["search"],
        }
    )


async def introspect_token(token: str) -> str | None:
    """Resolve an OAuth access token to the resource owner's SerpApi api_key.

    Returns None if the token is inactive or the authorization server is
    unreachable. Caller is responsible for checking OAUTH_INTROSPECTION_ENABLED.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                OAUTH_INTROSPECTION_URL,
                data={"token": token},
                auth=(OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET),
            )
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("OAuth introspection failed (%s)", type(exc).__name__)
        return None

    if not isinstance(body, dict):
        logger.warning("OAuth introspection returned an invalid response")
        return None
    if body.get("active") is not True:
        return None
    api_key = body.get("api_key")
    if not isinstance(api_key, str) or not api_key.strip():
        logger.warning("OAuth introspection returned no valid API key")
        return None
    return api_key


async def is_valid_api_key(api_key: str) -> bool:
    """Verify a legacy bearer key without consuming search credits."""
    try:
        account = await asyncio.to_thread(serpapi.account, api_key=api_key, timeout=5.0)
    except (serpapi.exceptions.SerpApiError, ValueError) as exc:
        # Exception messages can contain the request URL, including the API key.
        logger.warning("SerpApi API-key validation failed (%s)", type(exc).__name__)
        return False

    if not isinstance(account, dict) or account.get("api_key") != api_key:
        logger.warning("SerpApi Account API did not confirm the API key")
        return False
    return True


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip authentication for healthcheck and OAuth discovery endpoints
        if request.url.path in ("/healthcheck", OAUTH_PROTECTED_RESOURCE_PATH):
            return await call_next(request)

        api_key = None

        auth = request.headers.get("Authorization")
        if auth and auth.startswith("Bearer "):
            bearer_value = auth.split(" ", 1)[1].strip()
            if bearer_value and OAUTH_INTROSPECTION_ENABLED:
                api_key = await introspect_token(bearer_value)
                if not api_key and await is_valid_api_key(bearer_value):
                    api_key = bearer_value
            else:
                api_key = bearer_value

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
                headers={
                    "WWW-Authenticate": (
                        f'Bearer resource_metadata="{resource_metadata_url(request)}"'
                    )
                },
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
starlette_app.add_route(
    OAUTH_PROTECTED_RESOURCE_PATH, oauth_protected_resource_handler, methods=["GET"]
)

if __name__ == "__main__":
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8000"))

    uvicorn.run(starlette_app, host=host, port=port, ws="none")
