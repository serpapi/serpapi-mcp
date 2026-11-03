"""Regressions for catalog repair and overlapping writers, using real processes."""

import json
import multiprocessing
from pathlib import Path
from unittest.mock import patch

import pytest

from src import engine_catalog
from tests.catalog_support import PARAMETERS, PROPS, snapshot


@pytest.mark.parametrize("engine", ["google", "extract"])
def test_generated_catalog_advertises_only_supported_output_formats(engine):
    payload = {
        **PARAMETERS,
        "serpapi_parameters": {
            "parameters": {
                "output": {
                    "type": "select",
                    "options": ["json", "html", "md"],
                    "html": "Return JSON, raw HTML, or Markdown.",
                }
            }
        },
    }
    schemas = engine_catalog.build_schemas(
        {"parameters": {**PROPS["parameters"], engine: payload}}
    )
    schema = json.loads(schemas[f"{engine}.json"])
    output = schema["common_params"]["output"]
    assert output["options"] == ["json", "md"]
    assert "html" not in output["description"].lower()
    assert "optimized for LLMs and AI agents" in output["description"]


def _start(engines, entered, release, fetched, publisher):
    from filelock import FileLock

    engine_catalog.ENGINES_DIR = engines
    parameters = {
        **PROPS["parameters"],
        ("first_only" if publisher else "second_only"): PARAMETERS,
    }

    def fetch(url):
        fetched.set()
        return {"parameters": parameters}

    engine_catalog.fetch_props = fetch
    replace, acquire = Path.replace, FileLock.acquire

    def publish(source, target):
        result = replace(source, target)
        if publisher and not entered.is_set():
            entered.set()
            assert release.wait(30), "Publisher was not released"
        return result

    def lock(self, *args, **kwargs):
        if not publisher:
            entered.set()
        return acquire(self, *args, **kwargs)

    with (
        patch.object(Path, "replace", publish),
        patch.object(FileLock, "acquire", lock),
    ):
        engine_catalog.ensure_engines()


@pytest.mark.parametrize("initial", ["missing", "damaged"])
@pytest.mark.parametrize("ending", ["complete", "killed"])
def test_overlapping_starters_wait_for_publication_and_recover_a_killed_writer(
    tmp_path, monkeypatch, initial, ending
):
    engines = tmp_path / "engines"
    monkeypatch.setattr(engine_catalog, "ENGINES_DIR", engines)
    monkeypatch.setattr(engine_catalog, "fetch_props", lambda url: PROPS)
    if initial == "damaged":
        engine_catalog.ensure_engines()
        (engines / "google.json").write_text("old checkout")

    context = multiprocessing.get_context("spawn")
    entered, waiting, release, first_fetch, second_fetch = [
        context.Event() for _ in range(5)
    ]
    first = context.Process(
        target=_start, args=(engines, entered, release, first_fetch, True)
    )
    second = context.Process(
        target=_start, args=(engines, waiting, release, second_fetch, False)
    )
    first.start()
    try:
        assert entered.wait(20), "First writer did not publish a file"
        assert engine_catalog.catalog_generated_at(engines) is None
        second.start()
        assert waiting.wait(20), "Second starter did not try to acquire the writer lock"
        if ending == "killed":
            first.kill()
        else:
            release.set()
        first.join(20)
        second.join(20)
        assert second.exitcode == 0
        if ending == "complete":
            assert first.exitcode == 0
        assert second_fetch.is_set() == (ending == "killed")
        expected = {
            **PROPS["parameters"],
            ("first_only" if ending == "complete" else "second_only"): PARAMETERS,
        }
        assert {
            p.name: p.read_bytes() for p in engines.glob("*.json")
        } == engine_catalog.build_schemas({"parameters": expected})
        assert engine_catalog.catalog_generated_at(engines) is not None
        before = snapshot(engines)
        monkeypatch.setattr(
            engine_catalog,
            "fetch_props",
            lambda url: pytest.fail("Complete catalog must work offline"),
        )
        engine_catalog.ensure_engines()
        assert snapshot(engines) == before
    finally:
        for process in (first, second):
            if process.pid is not None:
                if process.is_alive():
                    process.kill()
                process.join(5)
            process.close()


@pytest.mark.parametrize(
    "damage", ["checkout-deletions", "checkout-overwrites", "leftovers-only", "legacy"]
)
def test_startup_repairs_catalog_damage_and_then_reuses_it_offline(
    tmp_path, monkeypatch, damage
):
    engines = tmp_path / "engines"
    monkeypatch.setattr(engine_catalog, "ENGINES_DIR", engines)
    monkeypatch.setattr(engine_catalog, "fetch_props", lambda url: PROPS)
    engine_catalog.ensure_engines()
    expected = {p.name: p.read_bytes() for p in engines.glob("*.json")}
    if damage == "checkout-deletions":
        for path in sorted(engines.glob("*.json"))[:80]:
            path.unlink()
    elif damage == "checkout-overwrites":
        (engines / "google.json").write_text('{"engine":"google","params":{}}')
    elif damage == "leftovers-only":
        for path in engines.glob("*.json"):
            path.unlink()
        (engines / ".DS_Store").write_bytes(b"fixture")
        (engines / ".generated-at").write_text("2026-10-30T00:00:00+00:00")
    else:
        (engines / ".catalog").unlink()
    calls = []
    monkeypatch.setattr(
        engine_catalog, "fetch_props", lambda url: calls.append(url) or PROPS
    )
    engine_catalog.ensure_engines()
    assert len(calls) == 1
    assert {p.name: p.read_bytes() for p in engines.glob("*.json")} == expected
    assert engine_catalog.catalog_generated_at(engines) is not None
    before = snapshot(engines)
    monkeypatch.setattr(
        engine_catalog, "fetch_props", lambda url: pytest.fail("Unexpected fetch")
    )
    engine_catalog.ensure_engines()
    assert snapshot(engines) == before


def test_failed_publication_is_not_accepted_and_can_be_retried(tmp_path, monkeypatch):
    engines = tmp_path / "engines"
    monkeypatch.setattr(engine_catalog, "ENGINES_DIR", engines)
    monkeypatch.setattr(engine_catalog, "fetch_props", lambda url: PROPS)
    with patch.object(
        Path, "replace", side_effect=PermissionError("Cannot publish catalog")
    ):
        with pytest.raises(
            RuntimeError, match="uv run python build-engines.py"
        ) as failure:
            engine_catalog.ensure_engines()
        assert isinstance(failure.value.__cause__, PermissionError)
    assert engine_catalog.catalog_generated_at(engines) is None
    engine_catalog.ensure_engines()
    assert engine_catalog.catalog_generated_at(engines) is not None
