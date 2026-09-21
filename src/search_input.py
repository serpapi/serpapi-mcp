"""Collect missing engine parameters before running a SerpApi search."""

import math
from typing import Any

from fastmcp import Context
from fastmcp.tools import ToolResult
from mcp.types import (
    ElicitRequest,
    ElicitRequestFormParams,
    ElicitResult,
    InputRequiredResult,
)
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from src.engine_input_rules import get_search_input_spec, is_missing


def _answer_error(name: str, value: Any, schema: dict[str, Any]) -> str | None:
    if is_missing(value):
        return None
    kind = schema["type"]
    valid = {
        "string": isinstance(value, str),
        "number": type(value) is int or (type(value) is float and math.isfinite(value)),
        "integer": type(value) is int,
        "boolean": type(value) is bool,
    }
    if not valid.get(kind, False):
        return f"{name} must be a {kind}."
    if "enum" in schema and value not in schema["enum"]:
        return f"{name} must be one of the offered choices."
    return None


def prepare_search_input(
    params: dict[str, Any], ctx: Context | None
) -> dict[str, Any] | ToolResult | InputRequiredResult:
    """Rebuild requirements from request arguments without storing continuation state."""
    spec = get_search_input_spec(params)
    if spec is None:
        return params
    error = spec.validation_error(params)
    if error:
        return ToolResult(content=f"Error: {error}", is_error=True)
    missing = spec.missing_fields(params)
    if not missing:
        return params

    label = spec.engine.replace("_", " ").title()

    def missing_error(names: list[str]) -> ToolResult:
        return ToolResult(
            content=f"Error: Missing {label} parameters: {', '.join(names)}. Ask for these values and retry. See serpapi://engines/{spec.engine}.",
            is_error=True,
        )

    rc = ctx.request_context if ctx is not None else None
    if rc is None or rc.protocol_version not in MODERN_PROTOCOL_VERSIONS:
        return missing_error(missing)
    capabilities = ctx.session.client_capabilities
    elicitation = capabilities.elicitation if capabilities else None
    if elicitation is None or elicitation.form is None:
        return missing_error(missing)
    if any(
        spec.fields[name]["type"] not in {"string", "number", "integer", "boolean"}
        for name in missing
    ):
        return missing_error(missing)

    responses = ctx.input_responses
    if responses is not None:
        answer = responses.get("search_details")
        if not isinstance(answer, ElicitResult):
            return ToolResult(
                content="Error: Missing or invalid search input response.",
                is_error=True,
            )
        if answer.action != "accept":
            return ToolResult(content="Search cancelled. No search was run.")
        content = answer.content or {}
        # Only answers for requested fields can change the search parameters.
        prepared = dict(params)
        for name in missing:
            if name in content:
                error = _answer_error(name, content[name], spec.fields[name])
                if error:
                    return ToolResult(content=f"Error: {error}", is_error=True)
                prepared[name] = content[name]
        updated_spec = get_search_input_spec(prepared)
        error = updated_spec.validation_error(prepared)
        if error:
            return ToolResult(content=f"Error: {error}", is_error=True)
        remaining = updated_spec.missing_fields(prepared)
        if remaining:
            return missing_error(remaining)
        return prepared

    return InputRequiredResult(
        result_type="input_required",
        input_requests={
            "search_details": ElicitRequest(
                method="elicitation/create",
                params=ElicitRequestFormParams(
                    message=f"Provide the missing {label} details before running the search.",
                    requested_schema={
                        "type": "object",
                        "properties": {name: spec.fields[name] for name in missing},
                        "required": missing,
                    },
                ),
            )
        },
    )
