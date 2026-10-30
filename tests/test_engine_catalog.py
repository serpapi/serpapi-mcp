"""Regression tests for publishing a catalog during concurrent cold starts."""

import json
import multiprocessing
from pathlib import Path
from unittest.mock import patch

import pytest

from src import engine_catalog


PROPS = {"parameters": {"a_fixture": {}, "b_fixture": {}}}


def _paused_start(engines, ready, release, pause):
    """Pause a real process at its first publication to force the race."""
    engine_catalog.ENGINES_DIR = engines
    engine_catalog.fetch_props = lambda url: PROPS

    def wait():
        ready.set()
        assert release.wait(20), "Test did not release the generator"

    generate = engine_catalog.generate_engines

    def generate_after_check(*args, **kwargs):
        if pause == "before-generation":
            wait()
        return generate(*args, **kwargs)

    def publish(original, source, target):
        first = not ready.is_set()
        if first and pause == "before":
            wait()
        result = original(source, target)
        if first and pause == "after":
            wait()
        return result

    rename, replace = Path.rename, Path.replace
    with (
        patch.object(engine_catalog, "generate_engines", generate_after_check),
        patch.object(
            Path, "rename", lambda source, target: publish(rename, source, target)
        ),
        patch.object(
            Path, "replace", lambda source, target: publish(replace, source, target)
        ),
    ):
        engine_catalog.ensure_engines()


@pytest.mark.parametrize("pause", ["before-generation", "before", "after"])
def test_overlapping_startups_and_interruption_keep_a_complete_catalog(
    tmp_path, monkeypatch, pause
):
    engines = tmp_path / "engines"
    parameters = dict(PROPS["parameters"])
    if pause != "after":
        engines.mkdir()
        parameters["winner_only"] = {}  # The loser must not remove newer engines.
    rename = Path.rename

    def windows_rename(source, target):
        if Path(target).exists():
            raise FileExistsError(target)
        return rename(source, target)

    # Windows refuses even an empty target; POSIX normally replaces it.
    monkeypatch.setattr(Path, "rename", windows_rename)
    monkeypatch.setattr(engine_catalog, "ENGINES_DIR", engines)
    monkeypatch.setattr(
        engine_catalog, "fetch_props", lambda url: {"parameters": parameters}
    )
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    process = context.Process(
        target=_paused_start, args=(engines, ready, release, pause)
    )
    process.start()
    try:
        assert ready.wait(20), "Generator did not reach publication"
        # The second starter either generates everything or reuses everything.
        engine_catalog.ensure_engines()
        assert {
            file.stem: json.loads(file.read_text()) for file in engines.glob("*.json")
        } == {
            name: {"engine": name, "params": {}, "common_params": {}}
            for name in parameters
        }
        assert (engines / ".generated-at").is_file()
        before = {
            file.name: (file.read_bytes(), file.stat().st_mtime_ns)
            for file in engines.iterdir()
        }

        if pause != "after":
            release.set()
            process.join(20)
            assert process.exitcode == 0
        else:
            # A crash at publication must leave a usable catalog for later startups.
            process.kill()
            process.join(5)

        def offline(url):
            pytest.fail("A completed catalog must be reused without fetching")

        monkeypatch.setattr(engine_catalog, "fetch_props", offline)
        engine_catalog.ensure_engines()
        assert {
            file.name: (file.read_bytes(), file.stat().st_mtime_ns)
            for file in engines.iterdir()
        } == before
    finally:
        if process.is_alive():
            process.kill()
            process.join(5)
        process.close()


def test_failed_first_publication_does_not_fall_back_to_partial_writes(
    tmp_path, monkeypatch
):
    engines = tmp_path / "engines"
    monkeypatch.setattr(engine_catalog, "ENGINES_DIR", engines)
    monkeypatch.setattr(engine_catalog, "fetch_props", lambda url: PROPS)

    def fail_rename(source, target):
        raise PermissionError("Cannot publish catalog")

    with monkeypatch.context() as failure:
        failure.setattr(Path, "rename", fail_rename)
        with pytest.raises(PermissionError):
            engine_catalog.ensure_engines()
    assert not list(engines.glob("*.json"))

    engine_catalog.ensure_engines()
    assert {file.stem for file in engines.glob("*.json")} == set(PROPS["parameters"])
