"""Keep ordinary pytest offline and separate from the developer's generated catalog."""

import json
from pathlib import Path

import pytest

from src import engine_catalog
from tests.catalog_support import write_catalog


def pytest_addoption(parser):
    parser.addoption(
        "--live-catalog",
        action="store_true",
        help="Check the already generated project catalog instead of offline fixtures.",
    )


@pytest.fixture(scope="session")
def protocol_catalog(tmp_path_factory, pytestconfig):
    if pytestconfig.getoption("--live-catalog"):
        directory = Path(__file__).resolve().parents[1] / "engines"
        assert engine_catalog.catalog_generated_at(directory) is not None, (
            "Run build-engines.py before --live-catalog"
        )
        return directory
    directory = tmp_path_factory.mktemp("protocol-catalog") / "engines"
    parameters = json.loads(
        (Path(__file__).parent / "fixtures/engine_params.json").read_text()
    )
    write_catalog(
        directory,
        {
            name: {
                "engine": name,
                "params": params,
                "common_params": {"api_key": {"required": True}},
            }
            for name, params in parameters.items()
        },
    )
    return directory


@pytest.fixture(autouse=True)
def use_protocol_catalog(monkeypatch, protocol_catalog):
    monkeypatch.setattr(engine_catalog, "ENGINES_DIR", protocol_catalog)

    def unexpected_fetch(url):
        pytest.fail(
            "Unit tests must use fixtures; generate schemas before --live-catalog"
        )

    monkeypatch.setattr(engine_catalog, "fetch_props", unexpected_fetch)
