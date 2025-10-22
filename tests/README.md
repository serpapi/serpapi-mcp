# End-to-End Tests

This directory contains end-to-end tests for the SerpApi MCP Server.

## Running Tests

### Prerequisites

Install test dependencies:

```bash
pip install pytest
```

### Run All Tests

```bash
pytest tests/ -v
```

### Run Specific Test File

```bash
pytest tests/test_e2e_docker.py -v
```

## Test Coverage

### Docker E2E Tests (`test_e2e_docker.py`)

Tests the Docker container functionality:

- **test_docker_image_exists**: Verifies the Docker image builds successfully
- **test_container_requires_api_key**: Ensures container validates the required `SERPAPI_API_KEY` environment variable
- **test_container_starts_with_api_key**: Confirms container starts properly when API key is provided
- **test_container_python_version**: Validates Python 3.13 is used in the container
- **test_container_has_dependencies**: Checks all required dependencies are installed
- **test_server_module_exists**: Verifies the server module is present and accessible

## CI/CD

These tests are designed to run in CI/CD pipelines and handle environment-specific issues like SSL certificate verification in restricted networks.
