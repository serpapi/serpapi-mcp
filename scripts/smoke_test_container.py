"""Build and check the production image locally; never push or deploy it."""

import subprocess
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECK = r"""
import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from fastmcp import Client

deadline = time.monotonic() + 60
while True:
    try:
        httpx.get("http://127.0.0.1:8000/healthcheck", timeout=1).raise_for_status()
        break
    except httpx.HTTPError:
        if time.monotonic() >= deadline:
            raise
        time.sleep(0.5)

async def check():
    generated_at = datetime.fromisoformat(Path("/app/engines/.generated-at").read_text())
    assert timedelta(0) <= datetime.now(UTC) - generated_at < timedelta(minutes=15)
    files = sorted(Path("/app/engines").glob("*.json"))
    assert files, "Image contains no schemas"
    for file in files:
        assert json.loads(file.read_text())["engine"] == file.stem
    async with Client("http://127.0.0.1:8000/mcp", timeout=15) as client:
        index = json.loads((await client.read_resource("serpapi://engines"))[0].text)
        assert index["engines"] == [file.stem for file in files]
        assert index["count"] == len(files)
        served = json.loads(
            (await client.read_resource("serpapi://engines/google_light"))[0].text
        )
        baked = json.loads(Path("/app/engines/google_light.json").read_text())
        baked.get("common_params", {}).pop("api_key", None)
        assert served == baked
    print(f"Read-only amd64 container served {len(files)} baked-in schemas without external networking")

asyncio.run(check())
"""


def main():
    image = f"serpapi-mcp-smoke:{uuid.uuid4().hex[:12]}"
    container = None
    built = False
    try:
        subprocess.run(
            [
                "docker",
                "build",
                "--platform",
                "linux/amd64",
                "--no-cache",
                "-t",
                image,
                ".",
            ],
            cwd=ROOT,
            check=True,
            timeout=600,
        )
        built = True
        container = subprocess.check_output(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--platform",
                "linux/amd64",
                "--read-only",
                "--tmpfs",
                "/tmp",
                "--network",
                "none",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
                image,
            ],
            text=True,
            timeout=30,
        ).strip()
        subprocess.run(
            ["docker", "exec", "-i", container, "python", "-"],
            input=CHECK,
            text=True,
            check=True,
            timeout=90,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        if container:
            subprocess.run(["docker", "logs", "--tail", "60", container], timeout=15)
        raise
    finally:
        if container:
            subprocess.run(["docker", "rm", "--force", container], timeout=15)
        if built:
            subprocess.run(["docker", "image", "rm", image], timeout=30)


if __name__ == "__main__":
    main()
