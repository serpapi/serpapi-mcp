"""Run the deployment gate against GitHub-shaped responses and its real jq queries."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/refresh-catalog.yml").read_text())
DEPLOY_CHECK = next(
    step["run"]
    for step in WORKFLOW["jobs"]["refresh"]["steps"]
    if step.get("id") == "verify_deployment"
)


@pytest.fixture
def github_api(tmp_path):
    assert shutil.which("jq"), "These workflow tests require jq, also used by CI."
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    data = {
        "runs": [
            {"id": 999, "head_branch": "v9.0.0", "head_sha": sha},
            {"id": 998, "head_branch": "v2.0.0", "head_sha": "wrong-commit"},
            {"id": 123, "head_branch": "v2.0.0", "head_sha": sha},
        ],
        "jobs": [
            {"jobs": [{"name": "Tests", "conclusion": "success"}]},
            {"jobs": [{"name": "Deploy hosted server", "conclusion": "success"}]},
        ],
        "fail_jobs": False,
    }
    gh = tmp_path / "gh"
    gh.write_text(
        f"#!{sys.executable}\n" + (ROOT / "tests/fixtures/gh_api.py").read_text()
    )
    gh.chmod(0o755)

    def run():
        fixture = tmp_path / "github.json"
        fixture.write_text(json.dumps(data))
        return subprocess.run(
            [
                "bash",
                "--noprofile",
                "--norc",
                "-e",
                "-o",
                "pipefail",
                "-c",
                DEPLOY_CHECK,
            ],
            cwd=ROOT,
            env={
                **os.environ,
                "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
                "GITHUB_REPOSITORY": "example/repo",
                "RELEASE_TAG": "v2.0.0",
                "GH_FIXTURE": str(fixture),
            },
            capture_output=True,
            text=True,
            timeout=15,
        )

    return data, run


def test_refresh_selects_exact_release_and_finds_deployment_on_later_page(github_api):
    _, run = github_api
    result = run()
    assert result.returncode == 0, result.stderr


def test_refresh_rejects_a_release_without_a_matching_run(github_api):
    data, run = github_api
    data["runs"].pop()  # The other tag and other commit must not authorize deployment.
    assert run().returncode != 0


def test_refresh_does_not_treat_successful_tests_as_a_successful_deploy(github_api):
    data, run = github_api
    data["jobs"][1]["jobs"][0]["conclusion"] = None
    assert run().returncode != 0


def test_refresh_stops_when_github_cannot_return_deployment_jobs(github_api):
    data, run = github_api
    data["fail_jobs"] = True
    assert run().returncode != 0
