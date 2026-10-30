"""Generate the local engine catalog from the SerpApi Playground."""

from __future__ import annotations

import html
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
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
ENGINES_DIR = Path(__file__).resolve().parents[1] / "engines"
TIMEOUT_SECONDS = 30
USER_AGENT = "Mozilla/5.0"
logger = logging.getLogger(__name__)


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


def generate_engines(
    output_dir: Path = ENGINES_DIR, *, only_if_missing: bool = False
) -> int:
    """Refresh schemas, removing retired engines after the new files are ready."""
    existing = list(output_dir.glob("*.json"))
    if only_if_missing and existing:
        return len(existing)

    props = fetch_props(PLAYGROUND_URL)
    if not isinstance(params := props.get("parameters"), dict) or not params:
        raise RuntimeError("Playground props missing a nonempty 'parameters' map.")

    schemas = {}

    for engine, payload in sorted(params.items()):
        if not isinstance(engine, str) or engine in EXCLUDED_ENGINES:
            continue
        if not isinstance(payload, dict):
            continue
        schemas[f"{engine}.json"] = json.dumps(
            normalize_engine(engine, payload), indent=2, ensure_ascii=False
        )

    if not schemas:
        raise RuntimeError("Playground returned no supported engines.")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output_dir.parent, prefix=".engines-") as temporary:
        staging = Path(temporary) / "engines"
        staging.mkdir()
        for name, content in schemas.items():
            (staging / name).write_text(content, encoding="utf-8")
        generated_at = staging / ".generated-at"
        generated_at.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
        if not existing:
            # Publish the first catalog all at once. Remove an empty destination
            # first because Windows cannot rename a directory onto one.
            try:
                try:
                    output_dir.rmdir()
                except FileNotFoundError:
                    pass
                staging.rename(output_dir)
            except OSError:
                # Another starter may have published its complete catalog first.
                if not any(output_dir.glob("*.json")):
                    raise
            return len(list(output_dir.glob("*.json")))

        # Replace complete files so readers never see partially written JSON.
        for name in schemas:
            (staging / name).replace(output_dir / name)
        for path in output_dir.glob("*.json"):
            if path.name not in schemas:
                path.unlink(missing_ok=True)
        generated_at.replace(output_dir / generated_at.name)
    return len(schemas)


def ensure_engines() -> None:
    """Generate missing schemas, or warn when an existing catalog needs refreshing."""
    if not any(ENGINES_DIR.glob("*.json")):
        generate_engines(ENGINES_DIR, only_if_missing=True)
        return

    try:
        generated_at = datetime.fromisoformat(
            (ENGINES_DIR / ".generated-at").read_text(encoding="utf-8").strip()
        )
        age = datetime.now(UTC) - generated_at
    except (OSError, ValueError, TypeError):
        reason = "generation date is unknown"
    else:
        if age <= timedelta(days=14):
            return
        reason = f"was generated {age.days} days ago and may be outdated"

    logger.warning(
        "Engine catalog %s. Refresh from the project root: uv run python build-engines.py "
        "(for Docker or MCP Bundle installs, rebuild the image or bundle).",
        reason,
    )
