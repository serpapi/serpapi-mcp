# Contributing

Fork the repository and create a branch for your changes. Run the relevant checks before opening a pull request, and use the [PR template](.github/pull_request_template.md) to describe the change, test results, and any AI assistance.

## Local setup

Install uv, clone your fork, and run these commands from the project root. uv uses the Python version in `.python-version`.

```bash
uv sync --group dev --frozen
uv run src/server.py
```

The HTTP server listens on `http://localhost:8000/mcp`. See the [README](README.md#authentication) for authentication and MCP client configuration. For stdio, use `src/stdio.py` and set `SERPAPI_API_KEY` in the environment.

## Engine catalog

The generated `engines/` folder is ignored by Git. HTTP and stdio startup call `ensure_engines()` to generate or repair it from the SerpApi Playground when files are missing or changed. `engines/.catalog` records the generation date and each schema's checksum. Catalogs without this manifest are regenerated once. The first local run and repairs need network access; a complete catalog works offline and on a read-only filesystem. Docker and MCP Bundle builds generate the catalog ahead of time.

To refresh an existing catalog, run:

```bash
uv run python build-engines.py
```

Generation rejects responses with fewer than 100 supported engines or missing `google` or `google_light` schemas. Fetch and validation failures preserve the existing catalog. A file lock serializes writers, and startup validates schema checksums against the manifest. Interrupted writes are repaired on the next start. Generated files are disposable, so local edits are overwritten during repair or refresh. A running server can read individual updated schemas during a manual refresh; restart it afterward for a consistent catalog.

Startup warns on stderr when a complete catalog is more than 14 days old, including the refresh command. Age alone does not trigger regeneration or block startup. The age check runs only at startup. For Docker, [rebuild without cache and recreate the container](#docker). For an MCP Bundle, install an updated bundle or [rebuild it from source](#building-the-mcp-bundle).

### Engine input rules

Engine parameter metadata comes from the SerpApi Playground and is generated into `engines/<engine>.json`. Add an `EngineInputRules` entry in [`src/engine_input_rules.py`](src/engine_input_rules.py) when requirements depend on other parameters, defaults, or alternatives. The shared MCP handler in [`src/search_input.py`](src/search_input.py) needs no engine-specific branches.

The form is derived from the original arguments on each request. It uses no `requestState` or process-local continuation storage, so a retry can run on another replica without a shared state-protection key. Authentication is applied on every HTTP request, and only answers for requested fields are used.

## Testing

Run the default suite:

```bash
uv run pytest -q
```

Tests use small, fixed catalogs in temporary directories and do not download schemas or change your local catalog. To check current Playground data, generate a fresh catalog and run the live-catalog checks:

```bash
uv run python build-engines.py
uv run pytest -q --live-catalog
```

CI runs the offline suite separately from the Docker smoke test. Releases and scheduled refreshes also run the live-catalog checks.

For an interactive check, start the local server and launch MCP Inspector:

```bash
npx @modelcontextprotocol/inspector
```

Select **Streamable HTTP** and use `http://localhost:8000/mcp`. Supply an `Authorization: Bearer YOUR_SERPAPI_API_KEY` header to test search calls.

Preview the MCP Apps locally without an MCP host:

```bash
uv run fastmcp dev apps src/server.py
```

## Docker

Build and run the server with a fresh catalog:

```bash
docker build --no-cache -t serpapi-mcp .
docker run --rm -p 8000:8000 serpapi-mcp
```

Use `--no-cache` when refreshing schemas. A normal rebuild can reuse the schema-generation layer even when Playground data has changed. Docker documents this behavior in its [build cache guide](https://docs.docker.com/build/cache/invalidation/#run-instructions). Recreate an existing container from the rebuilt image; restarting the old container keeps its old catalog.

To check the production image locally:

```bash
uv run --no-project python scripts/smoke_test_container.py
```

This builds a fresh Linux amd64 image, starts it with a read-only filesystem and external networking disabled, then reads the baked catalog over MCP. It removes its test container and image afterward.

## Building the MCP Bundle

Node.js is needed for the MCPB CLI. Run from the project root:

```bash
uv run --no-project mcpb/build.py
```

The build writes `dist/serpapi-mcp-<version>.mcpb`. It regenerates schemas from the Playground, validates [`mcpb/manifest.json`](mcpb/manifest.json), and packs git-tracked files minus [`.mcpbignore`](.mcpbignore). It then installs the bundle into a temporary directory and starts it over stdio to check it works. Add new runtime files to Git before building so they are included.

`--no-rebuild-engines` reuses a valid local catalog; `--no-smoke` skips the install-and-start check. See [`mcpb/README.md`](mcpb/README.md) for the bundle layout. Pull requests run the manifest and stdio entry point tests in [`tests/test_mcpb.py`](tests/test_mcpb.py) but do not pack a bundle.

## Releases

For maintainers, the version helper updates `pyproject.toml`, `server.json`, `mcpb/manifest.json`, and `uv.lock` together. Replace `X.Y.Z` with the new version:

```bash
uv run --no-sync scripts/bump_version.py X.Y.Z
# Review and commit the changes before tagging the release.
git tag vX.Y.Z
git push origin vX.Y.Z
```

A plain push to `main` does not publish a release. The [release workflow](.github/workflows/release.yml) checks version alignment and runs the tests before deploying the hosted server, publishing `server.json` to the MCP Registry, and building and attaching the MCP Bundle to the GitHub release. Manual runs must select a release tag and can choose which publishing jobs to run.

### Weekly catalog refresh

The [Refresh engine catalog workflow](.github/workflows/refresh-catalog.yml) rebuilds and redeploys the hosted server every Monday at 09:00 IST (03:30 UTC), or through **Run workflow** in GitHub Actions. It checks out the latest published release and requires its tag to match both the newest `v*` tag and `v` followed by the version in `pyproject.toml`. Previous deployment results do not block a fresh attempt, so a scheduled or manual refresh can retry a failed release deployment. The release must include the shared deployment lock and generate a catalog manifest. Older releases stop before deployment.

The workflow generates and tests a live catalog, then uses a fresh GitHub-hosted runner to build a Docker image and deploy it to `prod` with a unique image tag. Docker fetches again during its build and applies the same catalog validation, so the tested and deployed catalogs can differ if the Playground changes between those steps. Both deployment workflows use a pinned, checksum-verified Copilot CLI.

The refresh and release deployment jobs share a concurrency group. Publish a release containing these changes, then test a manual refresh. Pause refreshes during an intentional production rollback, since the next refresh retries the latest published release. Avoid rerunning deployments from older tags that predate the shared lock. GitHub can disable scheduled workflows after 60 days without repository activity; check the workflow remains enabled if refreshes stop.
