import json
import logging
import re
from pathlib import Path

from fastmcp.resources import ResourceContent, ResourceResult, resource
from mcp import MCPError
from mcp.types import (
    INVALID_PARAMS,
    Annotations,
    CompletionArgument,
    CompletionContext,
    PromptReference,
    ResourceTemplateReference,
)


logger = logging.getLogger(__name__)

ENGINES_DIR = Path(__file__).resolve().parents[2] / "engines"


def _get_engine_files() -> list[Path]:
    if not ENGINES_DIR.exists():
        logger.warning("Engines directory not found: %s", ENGINES_DIR)
        return []
    return sorted(ENGINES_DIR.glob("*.json"))


def complete_engine_name(
    ref: PromptReference | ResourceTemplateReference,
    argument: CompletionArgument,
    context: CompletionContext | None,
) -> list[str] | None:
    """Suggest engine identifiers for the engine resource template."""
    if (
        isinstance(ref, ResourceTemplateReference)
        and ref.uri == "serpapi://engines/{engine_name}"
        and argument.name == "engine_name"
    ):
        return [
            path.stem
            for path in _get_engine_files()
            if path.stem.startswith(argument.value)
        ]
    return None


# Omit optional priority hints: Codex can reject otherwise valid resource lists.
@resource(
    "serpapi://engines",
    name="serpapi-engines-index",
    description="Index of available SerpApi engine identifiers.",
    mime_type="application/json",
    annotations=Annotations(
        audience=["assistant"],
    ),
)
def engines_index() -> ResourceResult:
    engine_files = _get_engine_files()
    engines = [path.stem for path in engine_files]
    resource_content = json.dumps(
        {
            "count": len(engines),
            "engines": engines,
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


@resource(
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
    ),
)
def get_engine_schema(engine_name: str) -> ResourceResult:
    if not re.fullmatch(r"[a-z0-9_]+", engine_name):
        raise MCPError(
            code=INVALID_PARAMS,
            message=f"Invalid engine name: {engine_name!r}. Expected [a-z0-9_]+.",
        )
    engine_path = ENGINES_DIR / f"{engine_name}.json"
    if not engine_path.exists():
        raise MCPError(
            code=INVALID_PARAMS,
            message=f"Unknown engine: {engine_name!r}. See serpapi://engines for the full list.",
        )
    engine_schema = json.loads(engine_path.read_text())
    engine_schema.get("common_params", {}).pop("api_key", None)
    return ResourceResult(
        contents=[
            # Re-encode the file as single-line JSON to keep LLM context compact.
            ResourceContent(
                content=json.dumps(engine_schema),
                mime_type="application/json",
            ),
        ]
    )
