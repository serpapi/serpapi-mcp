"""Run the refresh workflow's rollout guards as real shell scripts."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/refresh-catalog.yml").read_text())


@pytest.mark.parametrize("supports_lock", [True, False])
def test_refresh_rejects_releases_without_the_deployment_lock(tmp_path, supports_lock):
    path = tmp_path / ".github/workflows/release.yml"
    path.parent.mkdir(parents=True)
    path.write_text(
        "jobs:\n  deploy:\n    concurrency:\n      group: "
        + ("mcp-server-prod" if supports_lock else "release-v2")
        + "\n"
    )
    script = next(
        step["run"]
        for step in WORKFLOW["jobs"]["refresh"]["steps"]
        if step.get("id") == "verify_release_lock"
    )
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == (0 if supports_lock else 1)
    if not supports_lock:
        assert "::error::" in result.stdout


@pytest.mark.parametrize(
    ("latest", "project_version", "expected_code"),
    [("v2.0.0", "2.0.0", 0), ("v2.1.0", "2.0.0", 1), ("v2.0.0", "1.9.0", 1)],
    ids=["matching-release", "unpublished-newer-tag", "tag-version-mismatch"],
)
def test_refresh_requires_latest_tag_to_match_project_version(
    tmp_path, latest, project_version, expected_code
):
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nversion = "{project_version}"\n'
    )
    # Substitute only the remote listing; execute the workflow's real release checks.
    script = next(
        step["run"]
        for step in WORKFLOW["jobs"]["refresh"]["steps"]
        if step.get("id") == "verify_latest_tag"
    )
    listing = f"git() {{ echo 'abc refs/tags/{latest}'; }}\n"
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", listing + script],
        cwd=tmp_path,
        env={**os.environ, "RELEASE_TAG": "v2.0.0"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == expected_code, result.stdout + result.stderr
    if expected_code:
        assert "::error::" in result.stdout
