# MCP Bundle (Claude Desktop extension)

Everything needed to package this server as an [MCP Bundle](https://github.com/modelcontextprotocol/mcpb) (`.mcpb`) for Claude Desktop lives in this folder, so the project root stays a plain Python project. The one exception is [`.mcpbignore`](../.mcpbignore), which sits at the project root where the MCPB CLI looks for it (like `.gitignore`).

| File | Purpose |
| --- | --- |
| `manifest.json` | The MCPB manifest. It uses the `uv` runtime: Claude Desktop runs `uv run --directory <install dir> --frozen --no-dev src/stdio.py` and installs the locked dependencies itself, so nothing is vendored. |
| `build.py` | Builds `dist/serpapi-mcp-<version>.mcpb`: rebuilds the engine schemas, lays out the bundle, packs it with the MCPB CLI, verifies the archive and smoke-tests it over stdio. |
| [`../.mcpbignore`](../.mcpbignore) | Trims the git-tracked files down to what the server needs at runtime; tests, CI, deployment files and this folder stay out. |

The stdio entry point is [`src/stdio.py`](../src/stdio.py); it belongs to the server (any local MCP host can launch it), not to the packaging. The manifest tests live in [`tests/test_mcpb.py`](../tests/test_mcpb.py).

## Build

Run from the project root. Node.js is needed for the MCPB CLI (via `npx`), and the default build needs network access to regenerate the engine schemas.

```bash
uv run mcpb/build.py                       # dist/serpapi-mcp-<version>.mcpb
uv run mcpb/build.py --no-rebuild-engines  # bundle engines/ from the working tree
uv run mcpb/build.py --no-smoke            # skip the install-and-start check
```

## Bundle layout

`manifest.json` has to sit at the root of the packed directory, so the build copies it there next to the git-tracked files (`.mcpbignore` is already at the project root, so it is staged like any other tracked file):

```
serpapi-mcp-<version>.mcpb
├── manifest.json        <- mcpb/manifest.json
├── pyproject.toml, uv.lock, .python-version, LICENSE, README.md
├── src/                 <- git-tracked files minus .mcpbignore (git add anything new that must ship)
└── engines/             <- regenerated from the SerpApi Playground at build time
```

## Releasing

Bump the version in `pyproject.toml`, `server.json` and `mcpb/manifest.json` (CI checks that they match), then push a matching tag:

```bash
git tag v1.0.2 && git push origin v1.0.2
```

The [release workflow](../.github/workflows/release.yml) builds the bundle, creates the GitHub release if it does not exist yet and attaches the `.mcpb` to it; the same workflow also deploys the hosted server and publishes `server.json` to the MCP Registry, so a tag is the only thing that ships anything. The [MCP Bundle workflow](../.github/workflows/mcpb.yml) runs the same build on every pull request as a check.
