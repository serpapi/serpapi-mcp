"""Generate the local engine catalog from the SerpApi Playground."""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen

PLAYGROUND_URL = "https://serpapi.com/playground"
EXCLUDED_ENGINES = {
    "google_scholar_profiles",
    "google_light_fast",
    "google_lens_image_sources",
}
PARAM_KEEP_KEYS = {"html", "type", "options", "required"}
ENGINES_DIR = Path(__file__).resolve().parents[1] / "engines"
CATALOG_MANIFEST = ".catalog"
# The Playground exposes over 140 engines. Also protect clean builds with no baseline.
MIN_ENGINES = 100
REQUIRED_ENGINES = {"google", "google_light"}
TIMEOUT_SECONDS = 30
USER_AGENT = "Mozilla/5.0"
logger = logging.getLogger(__name__)


def html_to_markdown(value: str) -> str:
    """Convert HTML to markdown, normalizing whitespace."""
    from markdownify import markdownify

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
    from bs4 import BeautifulSoup

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
                if group_name == "serpapi_parameters" and param_name == "output":
                    # The upstream API also supports HTML, which the MCP tools reject.
                    filtered["options"] = ["json", "md"]
                    filtered["description"] = (
                        "Parameter defines the format of the result. It can be set to `json` "
                        "to get a structured JSON of the results, or `md` to get a markdown-formatted "
                        "version optimized for LLMs and AI agents."
                    )
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


def catalog_generated_at(directory: Path) -> datetime | None:
    """Return the generation date only when every schema matches the manifest."""
    try:
        manifest = json.loads((directory / CATALOG_MANIFEST).read_bytes())
        expected = manifest["files"]
        files = list(directory.glob("*.json"))
        if not isinstance(expected, dict) or not expected:
            return None
        if {path.name for path in files} != set(expected):
            return None
        for path in files:
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected[path.name]:
                return None
        generated_at = datetime.fromisoformat(manifest["generated_at"])
        if generated_at.tzinfo is None:
            return None
        return generated_at
    except (OSError, ValueError, TypeError, KeyError):
        return None


def build_schemas(props: dict) -> dict[str, bytes]:
    """Reject incomplete upstream data before changing any existing schemas."""
    if not isinstance(params := props.get("parameters"), dict) or not params:
        raise RuntimeError("Playground props missing a nonempty 'parameters' map.")

    schemas = {}

    for engine, payload in params.items():
        if engine in EXCLUDED_ENGINES:
            continue
        if not re.fullmatch(r"[a-z0-9_]+", engine) or not isinstance(payload, dict):
            logger.warning("Skipping unsupported Playground engine: %r", engine)
            continue
        schema = normalize_engine(engine, payload)
        if not schema["params"]:
            logger.warning("Skipping Playground engine %r with no parameters", engine)
            continue
        schemas[f"{engine}.json"] = json.dumps(
            schema, indent=2, ensure_ascii=False
        ).encode("utf-8")

    missing = REQUIRED_ENGINES - {name.removesuffix(".json") for name in schemas}
    if len(schemas) < MIN_ENGINES or missing:
        raise RuntimeError(
            f"Incomplete Playground catalog: {len(schemas)} engines, expected at least "
            f"{MIN_ENGINES}; missing core engines: {sorted(missing)}. "
            "Existing catalog was not changed."
        )
    return dict(sorted(schemas.items()))


def generate_engines(
    output_dir: Path = ENGINES_DIR, *, only_if_missing: bool = False
) -> int:
    """Publish a validated catalog, serializing writers across local processes."""
    from filelock import FileLock

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(output_dir.with_name(f".{output_dir.name}.lock"), timeout=60):
        # A different starter may have completed the catalog while we waited.
        if only_if_missing and catalog_generated_at(output_dir) is not None:
            return len(list(output_dir.glob("*.json")))
        schemas = build_schemas(fetch_props(PLAYGROUND_URL))
        manifest = {
            "generated_at": datetime.now(UTC).isoformat(),
            "files": {
                name: hashlib.sha256(content).hexdigest()
                for name, content in schemas.items()
            },
        }
        with TemporaryDirectory(dir=output_dir.parent, prefix=".engines-") as temporary:
            staging = Path(temporary)
            for name, content in schemas.items():
                (staging / name).write_bytes(content)
            (staging / CATALOG_MANIFEST).write_bytes(
                json.dumps(manifest).encode("utf-8")
            )
            output_dir.mkdir(exist_ok=True)
            # A killed writer leaves no valid manifest; the next starter repairs it.
            (output_dir / CATALOG_MANIFEST).unlink(missing_ok=True)
            for name in schemas:
                (staging / name).replace(output_dir / name)
            for path in output_dir.glob("*.json"):
                if path.name not in schemas:
                    path.unlink(missing_ok=True)
            (output_dir / ".generated-at").unlink(missing_ok=True)
            (staging / CATALOG_MANIFEST).replace(output_dir / CATALOG_MANIFEST)
    return len(schemas)


def ensure_engines() -> None:
    """Repair incomplete catalogs, or warn when a complete catalog needs refreshing."""
    generated_at = catalog_generated_at(ENGINES_DIR)
    if generated_at is None:
        try:
            generate_engines(ENGINES_DIR, only_if_missing=True)
        except Exception as exc:
            raise RuntimeError(
                f"Engine catalog is missing or incomplete and generation failed: {exc}. "
                "Retry from a source checkout: uv run python build-engines.py. "
                "For MCP Bundle installs, install an updated bundle or rebuild it from source."
            ) from exc
        return

    if (age := datetime.now(UTC) - generated_at) > timedelta(days=14):
        logger.warning(
            "Engine catalog was generated %s days ago and may be outdated. "
            "Refresh from the project root: uv run python build-engines.py "
            "(Docker: rebuild with docker build --no-cache and redeploy; "
            "MCP Bundle: install an updated bundle or rebuild it from source).",
            age.days,
        )
