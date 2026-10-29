"""Exercise the refresh workflow's deployment gate without contacting AWS."""

import os
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
    if step["name"] == "Verify the release was deployed successfully"
)


@pytest.mark.parametrize(
    "run_id,deploy_result,api_failure,expected_error",
    [
        ("123", "success", "", None),
        ("", "success", "", "No release workflow run found"),
        ("123", "failure", "", "no successful Deploy hosted server job"),
        ("123", "skipped", "", "no successful Deploy hosted server job"),
        ("123", "null", "", "no successful Deploy hosted server job"),
        ("123", "", "", "no successful Deploy hosted server job"),
        ("123", "success", "runs", "API unavailable"),
        ("123", "success", "jobs", "API unavailable"),
    ],
)
def test_refresh_requires_successful_deployment(
    tmp_path, run_id, deploy_result, api_failure, expected_error
):
    gh = tmp_path / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "kind = 'jobs' if any('/jobs?' in arg for arg in sys.argv) else 'runs'\n"
        "if os.environ['API_FAILURE'] == kind:\n"
        "    print('API unavailable', file=sys.stderr)\n"
        "    sys.exit(1)\n"
        "print(os.environ['DEPLOY_RESULT' if kind == 'jobs' else 'RUN_ID'])\n"
    )
    gh.chmod(0o755)
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", DEPLOY_CHECK],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "GITHUB_REPOSITORY": "example/repo",
            "RELEASE_TAG": "v2.0.0",
            "RUN_ID": run_id,
            "DEPLOY_RESULT": deploy_result,
            "API_FAILURE": api_failure,
        },
        capture_output=True,
        text=True,
    )
    if expected_error is None:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
        assert expected_error in result.stdout + result.stderr
