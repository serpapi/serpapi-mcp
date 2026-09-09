#!/usr/bin/env python3
"""Build the MCP Bundle (.mcpb) that installs this server into Claude Desktop.

The bundle uses the MCPB ``uv`` runtime (https://github.com/modelcontextprotocol/mcpb):
it ships the source tree plus ``pyproject.toml`` and ``uv.lock``, and the host
installs the locked dependencies with uv when the bundle is installed. Nothing
is vendored, so one bundle works on every platform and architecture.

Packing uses the official MCPB CLI through ``npx`` (Node.js is needed to build
the bundle, not to run it).

Usage (from the project root):
    uv run mcpb/build.py                       # writes dist/serpapi-mcp-<version>.mcpb
    uv run mcpb/build.py --no-rebuild-engines  # bundle engines/ from the working tree
    uv run mcpb/build.py --no-smoke            # skip the install-and-start check

What goes in (manifest.json has to sit at the bundle root, so it is copied
out of this folder):
  * mcpb/manifest.json, copied to the bundle root;
  * every git-tracked file (with its working-tree contents), so `git add`
    anything new that must ship; the project-root .mcpbignore then drops what
    the server does not need at runtime (tests, CI, deployment files, this
    folder, ...);
  * engines/, regenerated from the SerpApi Playground with build-engines.py
    (as the Dockerfile does), whether or not it has been committed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import tomllib
import zipfile
from pathlib import Path

MCPB_DIR = Path(__file__).resolve().parent
ROOT = MCPB_DIR.parent
MANIFEST = MCPB_DIR / "manifest.json"
MCPBIGNORE = ROOT / ".mcpbignore"
DIST_DIR = ROOT / "dist"
ENGINES_DIR = "engines"
MCPB_CLI = "@anthropic-ai/mcpb@2.1.2"
SMOKE_TIMEOUT_SECONDS = 600  # first run may download a Python and all wheels

# Files the bundle cannot work without, and prefixes that must never ship.
REQUIRED_ENTRIES = {
    "manifest.json",
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    "src/stdio.py",
    "src/server.py",
    "engines/google.json",
}
FORBIDDEN_PREFIXES = (
    ".venv/",
    ".env",
    ".github/",
    "tests/",
    "dist/",
    "mcpb/",
    "server/lib/",
    "server/venv/",
)


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, **kwargs)  # type: ignore[call-overload]


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as pyproject:
        return tomllib.load(pyproject)["project"]["version"]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def check_manifest(manifest: dict, version: str) -> None:
    """Fail early on the mistakes a packed bundle would only reveal at install time."""
    registry_version = load_json(ROOT / "server.json")["version"]
    for name, other in (
        ("mcpb/manifest.json", manifest["version"]),
        ("server.json", registry_version),
    ):
        if other != version:
            fail(
                f"pyproject.toml is {version} but {name} is {other}; "
                "keep pyproject.toml, server.json and mcpb/manifest.json aligned"
            )

    server = manifest["server"]
    if server["type"] != "uv":
        fail(
            "manifest server.type must be 'uv'; this script does not vendor dependencies"
        )
    entry_point = server["entry_point"]
    if not (ROOT / entry_point).is_file():
        fail(f"manifest entry_point {entry_point!r} does not exist")
    if entry_point not in server["mcp_config"].get("args", []):
        fail(f"manifest mcp_config.args does not launch entry_point {entry_point!r}")


def tracked_files() -> list[Path]:
    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return [Path(name) for name in listing.decode().split("\0") if name]


def stage_sources(staging: Path) -> int:
    """Stage every git-tracked file, plus manifest.json at the root."""
    staging.mkdir(parents=True)
    count = 0
    for relative in tracked_files():
        if relative.parts[0] == ENGINES_DIR:  # populated by stage_engines instead
            continue
        source = ROOT / relative
        if not source.is_file():  # deleted locally but not yet `git rm`-ed
            continue
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        count += 1
    # The MCPB CLI expects both at the root of the directory it packs.
    # .mcpbignore is git-tracked at the project root, so the loop above already
    # staged it; copying it again makes the build fail loudly if it ever goes
    # missing instead of silently packing the whole tree.
    shutil.copy2(MANIFEST, staging / "manifest.json")
    shutil.copy2(MCPBIGNORE, staging / ".mcpbignore")
    return count


def stage_engines(staging: Path, rebuild: bool) -> int:
    """Populate ``staging/engines`` with every engine schema, committed or not."""
    target = staging / ENGINES_DIR
    if rebuild:
        # build-engines.py writes to ./engines relative to its cwd, so run it
        # inside the staging dir with the project's environment; the repo's own
        # engines/ directory is left untouched.
        try:
            run(
                [
                    uv(),
                    "run",
                    "--project",
                    str(ROOT),
                    "--frozen",
                    "--no-dev",
                    str(ROOT / "build-engines.py"),
                ],
                cwd=staging,
            )
        except subprocess.CalledProcessError:
            fail(
                "rebuilding the engine schemas failed (it needs network access to "
                "serpapi.com); pass --no-rebuild-engines to bundle engines/ from "
                "the working tree instead"
            )
    else:
        target.mkdir(parents=True, exist_ok=True)
        for schema in sorted((ROOT / ENGINES_DIR).glob("*.json")):
            shutil.copy2(schema, target / schema.name)

    count = len(list(target.glob("*.json")))
    if count == 0:
        fail("no engine schemas to bundle")
    return count


def which(tool: str, hint: str) -> str:
    path = shutil.which(tool)
    if not path:
        fail(f"{tool} not found; {hint}")
    return path


def npx() -> str:
    return which("npx", "install Node.js to run the MCPB CLI")


def uv() -> str:
    return which("uv", "install uv (https://docs.astral.sh/uv/)")


def verify_archive(bundle: Path) -> list[str]:
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
    missing = sorted(REQUIRED_ENTRIES - set(names))
    if missing:
        fail(f"bundle is missing required files: {', '.join(missing)}")
    leaked = sorted(name for name in names if name.startswith(FORBIDDEN_PREFIXES))
    if leaked:
        fail(f"bundle contains files that must not ship: {', '.join(leaked[:10])}")
    return names


def substitute(value: str, variables: dict[str, str]) -> str:
    for key, replacement in variables.items():
        value = value.replace("${" + key + "}", replacement)
    return value


def mcp_handshake(proc: subprocess.Popen) -> tuple[dict, list[str]]:
    """Speak just enough MCP over stdio to initialize and list tools."""
    assert proc.stdin is not None and proc.stdout is not None

    def send(message: dict) -> None:
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    def request(request_id: int, method: str, params: dict | None = None) -> dict:
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
        while True:
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError(f"server exited before answering {method}")
            try:
                reply = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"non-JSON output on stdout while waiting for {method}: {line.strip()!r}"
                ) from exc
            if reply.get("id") != request_id:
                continue  # notification or unrelated message
            if "error" in reply:
                raise RuntimeError(f"{method} failed: {reply['error']}")
            return reply["result"]

    init = request(
        1,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "mcpb-build", "version": "0"},
        },
    )
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    tools = request(2, "tools/list")
    return init, [tool["name"] for tool in tools["tools"]]


def smoke_test(bundle: Path, manifest: dict) -> None:
    """Install the packed bundle into a temp dir and start it exactly as a host would."""
    uv()  # the manifest command is `uv run ...`

    with tempfile.TemporaryDirectory(prefix="serpapi-mcpb-") as tmp:
        install_dir = Path(tmp) / "bundle"
        with zipfile.ZipFile(bundle) as archive:
            archive.extractall(install_dir)

        variables = {
            "__dirname": str(install_dir),
            "user_config.serpapi_api_key": "smoke-test-key",
        }
        mcp_config = manifest["server"]["mcp_config"]
        command = [substitute(mcp_config["command"], variables)]
        command += [substitute(arg, variables) for arg in mcp_config.get("args", [])]
        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
        env.update(
            {k: substitute(v, variables) for k, v in mcp_config.get("env", {}).items()}
        )

        print("+", " ".join(command), flush=True)
        stderr_log = Path(tmp) / "server.stderr"
        with stderr_log.open("w+", encoding="utf-8") as stderr:
            proc = subprocess.Popen(
                command,
                cwd=tmp,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr,
                text=True,
            )
            watchdog = threading.Timer(SMOKE_TIMEOUT_SECONDS, proc.kill)
            watchdog.start()
            try:
                init, tools = mcp_handshake(proc)
            except Exception as exc:
                proc.kill()
                stderr.seek(0)
                print(stderr.read(), file=sys.stderr)
                fail(f"smoke test failed: {exc}")
            finally:
                watchdog.cancel()
                if proc.stdin:
                    proc.stdin.close()  # EOF: the server shuts down cleanly
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()

    if "search" not in tools:
        fail(f"smoke test: bundle does not expose the search tool (got {tools})")
    info = init.get("serverInfo", {})
    print(
        f"smoke test OK: {info.get('name')} {info.get('version')} "
        f"started over stdio and lists tools {tools}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--no-rebuild-engines",
        action="store_true",
        help="bundle engines/ from the working tree instead of regenerating it",
    )
    parser.add_argument(
        "--no-smoke",
        action="store_true",
        help="skip installing and starting the packed bundle",
    )
    args = parser.parse_args()

    version = project_version()
    manifest = load_json(MANIFEST)
    check_manifest(manifest, version)

    DIST_DIR.mkdir(exist_ok=True)
    bundle = DIST_DIR / f"{manifest['name']}-{version}.mcpb"
    bundle.unlink(missing_ok=True)

    with tempfile.TemporaryDirectory(prefix="serpapi-mcpb-src-") as tmp:
        staging = Path(tmp) / manifest["name"]
        staged = stage_sources(staging)
        engines = stage_engines(staging, rebuild=not args.no_rebuild_engines)
        source = "rebuilt from the SerpApi Playground"
        if args.no_rebuild_engines:
            source = "copied from the working tree"
        print(
            f"staged {staged} git-tracked files and {engines} engine schemas ({source})"
        )
        npx_bin = npx()
        run([npx_bin, "--yes", MCPB_CLI, "validate", "manifest.json"], cwd=staging)
        run([npx_bin, "--yes", MCPB_CLI, "pack", str(staging), str(bundle)], cwd=tmp)

    names = verify_archive(bundle)
    size_kib = bundle.stat().st_size / 1024
    print(
        f"built {bundle.relative_to(ROOT)} "
        f"({size_kib:.0f} KiB, {len(names)} files, {engines} engines)"
    )

    if not args.no_smoke:
        smoke_test(bundle, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
