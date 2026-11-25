"""Engine requirements and validation shared by guided search inputs."""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from src import engine_catalog

Params = dict[str, Any]
RequiredFields = tuple[str, ...]
CONTROL_FIELDS = {"api_key", "engine", "output"}


def is_missing(value: Any) -> bool:
    return value is None or (
        isinstance(value, (str, list, dict))
        and not (value.strip() if isinstance(value, str) else value)
    )


@dataclass(frozen=True)
class EngineInputRules:
    required: RequiredFields | Callable[[Params], RequiredFields] | None = None
    alternatives: dict[str, tuple[str, ...] | Callable[[Params], bool]] = field(
        default_factory=dict
    )
    defaulted: tuple[str, ...] = ()
    fields: dict[str, Params] = field(default_factory=dict)
    validate: Callable[[Params, RequiredFields], str | None] | None = None

    def has_alternative(self, name: str, params: Params) -> bool:
        alternative = self.alternatives.get(name, ())
        if callable(alternative):
            return alternative(params)
        return any(not is_missing(params.get(other)) for other in alternative)


def _flight_fields(params: Params) -> RequiredFields:
    flight_type = str(params.get("type", 1))
    if flight_type not in {"1", "2"} or any(
        params.get(name)
        for name in (
            "departure_token",
            "booking_token",
            "multi_city_json",
            "selected_flights_json",
        )
    ):
        return ()
    fields = ("departure_id", "arrival_id", "outbound_date")
    return fields + ("return_date",) if flight_type == "1" else fields


def _maps_fields(params: Params) -> RequiredFields:
    if any(not is_missing(params.get(name)) for name in ("place_id", "data_cid")):
        return ()
    mode = params.get("type", "search")
    if mode in (None, "", "search"):
        data = params.get("data")
        # A Maps data filter only identifies a place if it contains a data_id.
        if isinstance(data, str) and re.search(
            r"!1s0x[\da-f]+(?::|%3a)0x[\da-f]+(?:!|$)", data, re.IGNORECASE | re.ASCII
        ):
            return ()
        if mode == "search" or not is_missing(params.get("q")):
            return ("q",)
    if mode == "place":
        return ("type", "data")
    return ("type",)


def _trends_category_search(params: Params) -> bool:
    category = params.get("cat")
    if not isinstance(category, (int, str)) or not re.fullmatch(
        r"0|[1-9][0-9]*", str(category)
    ):
        return False
    data_type = params.get("data_type")
    if data_type in ("RELATED_QUERIES", "RELATED_TOPICS"):
        return True
    return data_type in (None, "", "TIMESERIES") and category not in (0, "0")


def _related_image_search(params: Params) -> bool:
    tbs = params.get("tbs")
    return isinstance(tbs, str) and bool(re.search(r"(?:^|,)rimg:[^,\s]+(?:,|$)", tbs))


def _google_image_search(params: Params) -> bool:
    tbm, udm = params.get("tbm"), params.get("udm")
    image_mode = (tbm == "isch" and is_missing(udm)) or (
        str(udm) == "2" and is_missing(tbm)
    )
    return image_mode and _related_image_search(params)


def _scholar_citation_search(params: Params) -> bool:
    citation = params.get("citation_id")
    return (
        params.get("view_op") == "view_citation"
        and isinstance(citation, str)
        and not is_missing(citation)
    )


def _date_error(params: Params, names: RequiredFields) -> str | None:
    for name in names:
        value = params.get(name)
        if is_missing(value):
            continue
        if not isinstance(value, str):
            return f"{name} must be a string."
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError
            date.fromisoformat(value)
        except ValueError:
            return f"{name} must be a valid date in YYYY-MM-DD format."
    return None


def _validate_flights(params: Params, fields: RequiredFields) -> str | None:
    error = _date_error(
        params, tuple(name for name in fields if name.endswith("_date"))
    )
    if error:
        return error
    for name in (name for name in fields if name.endswith("_id")):
        value = params.get(name)
        if is_missing(value):
            continue
        if not isinstance(value, str):
            return f"{name} must be a string."
        if any(
            not re.fullmatch(r"[A-Z]{3}|/[mg]/[A-Za-z0-9_]+", item.strip())
            for item in value.split(",")
        ):
            return f"{name} must contain airport codes or /m/ or /g/ location IDs."
    if (
        "return_date" in fields
        and not is_missing(params.get("outbound_date"))
        and not is_missing(params.get("return_date"))
        and params["return_date"] < params["outbound_date"]
    ):
        return "return_date must be on or after outbound_date."
    return None


def _query_token_error(params: Params, name: str, query: str = "q") -> str | None:
    token = params.get(name)
    if (
        is_missing(params.get(query))
        and not is_missing(token)
        and not isinstance(token, str)
    ):
        return f"{name} must be a string."
    return None


def _validate_hotels(params: Params, fields: RequiredFields) -> str | None:
    error = _date_error(params, ("check_in_date", "check_out_date"))
    if error:
        return error
    if (
        not is_missing(params.get("check_in_date"))
        and not is_missing(params.get("check_out_date"))
        and params["check_out_date"] <= params["check_in_date"]
    ):
        return "check_out_date must be after check_in_date."
    return _query_token_error(params, "property_token")


# These overrides cover defaults and alternatives absent from the catalog flags.
ENGINE_INPUT_RULES = {
    "google": EngineInputRules(alternatives={"q": _google_image_search}),
    "google_flights": EngineInputRules(
        required=_flight_fields,
        fields={
            "departure_id": {
                "title": "Departure airport or location",
                "description": "Airport codes such as LHR, or /m/ or /g/ location IDs. Separate multiple locations with commas.",
            },
            "arrival_id": {
                "title": "Arrival airport or location",
                "description": "Airport codes such as NRT, or /m/ or /g/ location IDs. Separate multiple locations with commas.",
            },
            "outbound_date": {
                "title": "Departure date",
                "description": "Departure date in YYYY-MM-DD format.",
            },
            "return_date": {
                "title": "Return date",
                "description": "Return date in YYYY-MM-DD format, on or after departure.",
            },
        },
        validate=_validate_flights,
    ),
    "google_hotels": EngineInputRules(
        alternatives={"q": ("property_token",)},
        fields={
            "q": {"title": "Destination or hotel"},
            "check_in_date": {
                "title": "Check-in date",
                "description": "Check-in date in YYYY-MM-DD format.",
            },
            "check_out_date": {
                "title": "Check-out date",
                "description": "Check-out date in YYYY-MM-DD format, after check-in.",
            },
        },
        validate=_validate_hotels,
    ),
    "google_maps_directions": EngineInputRules(
        required=("start_addr", "end_addr"),
        alternatives={
            "start_addr": ("start_data_id", "start_coords"),
            "end_addr": ("end_data_id", "end_coords"),
        },
        fields={
            "start_addr": {"title": "Starting address"},
            "end_addr": {"title": "Destination address"},
        },
    ),
    "google_maps": EngineInputRules(required=_maps_fields),
    "google_images": EngineInputRules(alternatives={"q": _related_image_search}),
    "bing_maps": EngineInputRules(alternatives={"q": ("place_id",)}),
    "amazon": EngineInputRules(required=("k",), alternatives={"k": ("node",)}),
    "ebay": EngineInputRules(alternatives={"_nkw": ("category_id",)}),
    "walmart": EngineInputRules(alternatives={"query": ("cat_id",)}),
    "google_scholar": EngineInputRules(alternatives={"q": ("cites", "cluster")}),
    "google_scholar_author": EngineInputRules(
        alternatives={"author_id": _scholar_citation_search}
    ),
    "google_shopping": EngineInputRules(alternatives={"q": ("shoprs",)}),
    "google_shopping_light": EngineInputRules(
        alternatives={"q": ("shoprs",)},
        validate=lambda params, _: _query_token_error(params, "shoprs"),
    ),
    "google_trends": EngineInputRules(alternatives={"q": _trends_category_search}),
    "yandex_images": EngineInputRules(alternatives={"text": ("url",)}),
    "youtube": EngineInputRules(
        alternatives={"search_query": ("sp",)},
        validate=lambda params, _: _query_token_error(params, "sp", "search_query"),
    ),
    "youtube_music": EngineInputRules(
        alternatives={"q": ("next_page_token",)},
        validate=lambda params, _: _query_token_error(params, "next_page_token"),
    ),
    "google_lens": EngineInputRules(
        defaulted=("type",), alternatives={"url": ("image_id",)}
    ),
    "google_play_product": EngineInputRules(defaulted=("store",)),
    "google_trends_trending_now": EngineInputRules(defaulted=("geo", "frequency")),
    "google_local_services": EngineInputRules(fields={"data_cid": {"type": "string"}}),
}


@dataclass(frozen=True)
class SearchInputSpec:
    engine: str
    fields: dict[str, Params]
    rules: EngineInputRules

    def missing_fields(self, params: Params) -> list[str]:
        return [
            name
            for name in self.fields
            if is_missing(params.get(name))
            and not self.rules.has_alternative(name, params)
        ]

    def validation_error(self, params: Params) -> str | None:
        return (
            self.rules.validate(params, tuple(self.fields))
            if self.rules.validate
            else None
        )


def _form_field(name: str, metadata: Params) -> Params:
    kind = metadata.get("type", "string")
    if kind in ("select", "location"):
        kind = "string"
    schema = {
        "type": kind,
        "title": metadata.get("title", name.replace("_", " ").strip().capitalize()),
    }
    if metadata.get("description"):
        schema["description"] = metadata["description"]
    if metadata.get("type") == "select" and metadata.get("options"):
        schema["enum"] = [
            str(option[0] if isinstance(option, list) else option)
            for option in metadata["options"]
        ]
    return schema


def get_search_input_spec(params: Params) -> SearchInputSpec | None:
    engine = params.get("engine", "google_light")
    if not isinstance(engine, str) or not re.fullmatch(r"[a-z0-9_]+", engine):
        return None
    path = engine_catalog.ENGINES_DIR / f"{engine}.json"
    if not path.is_file():
        return None
    metadata = json.loads(path.read_text())["params"]
    rules = ENGINE_INPUT_RULES.get(engine, EngineInputRules())
    required = rules.required
    if callable(required):
        required = required(params)
    if required is None:
        required = tuple(
            name for name, value in metadata.items() if value.get("required") is True
        )
    fields = {}
    for name in required:
        value = {**metadata.get(name, {}), **rules.fields.get(name, {})}
        if name in CONTROL_FIELDS or name in rules.defaulted or "default" in value:
            continue
        fields[name] = _form_field(name, value)
    return SearchInputSpec(engine, fields, rules)
