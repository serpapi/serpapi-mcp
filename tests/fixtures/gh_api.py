"""Local GitHub API stand-in; execute the workflow's jq expressions unchanged."""

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


args = sys.argv[1:]
data = json.loads(Path(os.environ["GH_FIXTURE"]).read_text())
endpoint = next(arg for arg in args if arg.startswith("repos/"))
url = urlsplit(endpoint)
fields = dict(
    args[i + 1].split("=", 1) for i, arg in enumerate(args) if arg in ("-f", "-F")
)
if url.path == "repos/example/repo/actions/workflows/release.yml/runs":
    # gh defaults to POST when fields are supplied unless GET is explicit.
    assert args[args.index("--method") + 1] == "GET"
    runs = [
        run
        for run in data["runs"]
        if all(
            run[key] == fields[field]
            for field, key in (("branch", "head_branch"), ("head_sha", "head_sha"))
            if field in fields
        )
    ]
    pages = [{"workflow_runs": runs[: int(fields.get("per_page", 30))]}]
elif url.path == "repos/example/repo/actions/runs/123/jobs":
    # Only the latest attempt is admissible, not a successful older attempt.
    assert parse_qs(url.query).get("filter") == ["latest"]
    if data["fail_jobs"]:
        raise SystemExit(1)
    pages = data["jobs"] if "--paginate" in args else data["jobs"][:1]
else:
    raise AssertionError(f"Unexpected GitHub endpoint: {endpoint}")

query = args[args.index("--jq") + 1]
for page in pages:
    subprocess.run(["jq", "-r", query], input=json.dumps(page), text=True, check=True)
