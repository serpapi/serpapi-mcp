# GitHub Actions Workflows

This directory contains GitHub Actions workflows for continuous integration and deployment.

## Workflows

### Python Package CI (`python-package.yml`)

Runs on every push to `main` and on all pull requests.

#### Jobs

1. **test** - Runs the e2e test suite
   - Sets up Python 3.13
   - Installs dependencies including pytest
   - Configures Docker Buildx for optimal caching
   - Runs the comprehensive e2e test suite (`tests/test_e2e_docker.py`)
   - Tests Docker container build, startup, and functionality

2. **lint** - Code quality checks
   - Sets up Python 3.13
   - Installs Ruff linter
   - Runs linting checks on the codebase
   - Continues on error to not block the build

3. **docker-build** - Docker image validation
   - Sets up Docker Buildx with GitHub Actions caching
   - Builds the Docker image
   - Tests basic container functionality
   - Validates API key requirement
   - Confirms container starts successfully

#### Best Practices Implemented

- ✅ **Matrix strategy**: Easy to add multiple Python versions if needed
- ✅ **Dependency caching**: Speeds up workflow runs using pip cache
- ✅ **Docker layer caching**: Uses GitHub Actions cache for Docker builds
- ✅ **Parallel jobs**: Test, lint, and docker-build run in parallel
- ✅ **Latest actions**: Uses latest versions of GitHub Actions (v4, v5)
- ✅ **Clear job names**: Easy to understand what each job does
- ✅ **Fail-fast disabled**: All test scenarios run even if one fails
- ✅ **Continue on error for lint**: Linting doesn't block the build

## Monitoring

Check the [Actions tab](https://github.com/ilyazub/serpapi-mcp-server/actions) in the repository to monitor workflow runs and review results.

## Badge

The build status badge in the README shows the current status of the `python-package.yml` workflow:

```markdown
[![Build](https://github.com/ilyazub/serpapi-mcp-server/actions/workflows/python-package.yml/badge.svg)](https://github.com/ilyazub/serpapi-mcp-server/actions/workflows/python-package.yml)
```
