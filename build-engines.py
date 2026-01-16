#!/usr/bin/env python3
"""Build SerpApi engine parameter data for MCP usage."""

from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from markdownify import markdownify

PLAYGROUND_URL = "https://serpapi.com/playground"
EXCLUDED_ENGINES = {
    "google_scholar_profiles",
    "google_light_fast",
    "google_lens_image_sources",
}
PARAM_KEEP_KEYS = {"html", "type", "options", "required"}
OUTPUT_DIR = Path("engines")
TIMEOUT_SECONDS = 30
USER_AGENT = "Mozilla/5.0"


def html_to_markdown(value: str) -> str:
    """Convert HTML to markdown, normalizing whitespace."""
    md = markdownify(html.unescape(value), strip=["a"])
    return " ".join(md.split())


def normalize_options(options: list[object]) -> list[object]:
    """Normalize option values, simplifying [value, label] pairs where possible."""
    normalized = []
    for option in options:
        if isinstance(option, list) and option:
            value = option[0]
            label = option[1] if len(option) > 1 else None
            if (
                label is not None
                and (
                    isinstance(value, (int, float))
                    or (isinstance(value, str) and value.isdigit())
                )
                and value != label
            ):
                normalized.append(option)
            else:
                normalized.append(value)
        else:
            normalized.append(option)
    return normalized


def fetch_props(url: str) -> dict[str, object]:
    """Fetch playground HTML and extract React props."""
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        page_html = resp.read().decode("utf-8", errors="ignore")
    soup = BeautifulSoup(page_html, "html.parser")
    node = soup.find(attrs={"data-react-props": True})
    if not node:
        raise RuntimeError("Failed to locate data-react-props in playground HTML.")
    return json.loads(html.unescape(node["data-react-props"]))


def normalize_engine(engine: str, payload: dict[str, object]) -> dict[str, object]:
    """Normalize engine payload, extracting relevant parameter metadata."""
    normalized_params: dict[str, dict[str, object]] = {}
    common_params: dict[str, dict[str, object]] = {}
    if isinstance(payload, dict):
        for group_name, group in payload.items():
            if not isinstance(group, dict):
                continue
            if not isinstance(params := group.get("parameters"), dict):
                continue
            for param_name, param in params.items():
                if not isinstance(param, dict):
                    continue
                filtered = {k: v for k, v in param.items() if k in PARAM_KEEP_KEYS}
                if isinstance(options := filtered.get("options"), list):
                    filtered["options"] = normalize_options(options)
                if isinstance(html_value := filtered.pop("html", None), str):
                    filtered["description"] = html_to_markdown(html_value)
                if filtered:
                    filtered["group"] = group_name
                    if group_name == "serpapi_parameters":
                        common_params[param_name] = filtered
                    else:
                        normalized_params[param_name] = filtered

    return {
        "engine": engine,
        "params": normalized_params,
        "common_params": common_params,
    }


def main() -> int:
    """Main entry point: fetch playground data and generate engine files."""
    props = fetch_props(PLAYGROUND_URL)
    if not isinstance(params := props.get("parameters"), dict):
        raise RuntimeError("Playground props missing 'parameters' map.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    engines = []

    for engine, payload in sorted(params.items()):
        if not isinstance(engine, str) or engine in EXCLUDED_ENGINES:
            continue
        if not isinstance(payload, dict):
            continue
        (OUTPUT_DIR / f"{engine}.json").write_text(
            json.dumps(normalize_engine(engine, payload), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        engines.append(engine)

    print(f"Wrote {len(engines)} engine files to {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
