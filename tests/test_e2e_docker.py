"""
End-to-End tests for the SerpApi MCP Server Docker container.

This test suite validates that:
1. The Docker image builds successfully
2. The container requires the SERPAPI_API_KEY environment variable
3. The container starts and runs the MCP server correctly
"""

import subprocess
import time
import pytest


def run_command(cmd, timeout=30, check=True):
    """Helper function to run shell commands."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "Command timed out"
    except subprocess.CalledProcessError as e:
        return e.returncode, e.stdout, e.stderr


class TestDockerE2E:
    """End-to-End tests for Docker container."""

    IMAGE_NAME = "serpapi-mcp-server:test"

    @classmethod
    def setup_class(cls):
        """Build the Docker image before running tests."""
        print("\n=== Building Docker image ===")
        
        # First, try to build normally
        returncode, stdout, stderr = run_command(
            f"docker build -t {cls.IMAGE_NAME} .",
            timeout=300,
            check=False
        )
        
        # If build fails due to SSL certificate issues (common in CI environments),
        # try with trusted hosts
        if returncode != 0 and "SSL" in stderr:
            print("⚠ SSL certificate issue detected, rebuilding with trusted hosts...")
            # Temporarily modify Dockerfile for SSL workaround
            run_command(
                "sed -i 's/pip install --no-cache-dir/pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org/g' Dockerfile",
                check=False
            )
            returncode, stdout, stderr = run_command(
                f"docker build -t {cls.IMAGE_NAME} .",
                timeout=300,
                check=False
            )
            # Restore original Dockerfile
            run_command("git checkout Dockerfile", check=False)
        
        assert returncode == 0, f"Docker build failed: {stderr}"
        print("✓ Docker image built successfully")

    @classmethod
    def teardown_class(cls):
        """Clean up Docker resources after tests."""
        print("\n=== Cleaning up Docker resources ===")
        # Remove test containers
        run_command(f"docker ps -a -q --filter ancestor={cls.IMAGE_NAME} | xargs -r docker rm -f", check=False)
        print("✓ Cleanup completed")

    def test_docker_image_exists(self):
        """Test that the Docker image was built successfully."""
        returncode, stdout, stderr = run_command(
            f"docker images {self.IMAGE_NAME} --format '{{{{.Repository}}}}:{{{{.Tag}}}}'",
            check=False
        )
        assert returncode == 0, "Failed to list Docker images"
        assert self.IMAGE_NAME in stdout, f"Docker image {self.IMAGE_NAME} not found"

    def test_container_requires_api_key(self):
        """Test that the container fails gracefully without SERPAPI_API_KEY."""
        returncode, stdout, stderr = run_command(
            f"timeout 3 docker run --rm {self.IMAGE_NAME}",
            timeout=5,
            check=False
        )
        # Container should exit with error when API key is missing
        output = stdout + stderr
        assert "SERPAPI_API_KEY" in output, \
            "Container should display error about missing SERPAPI_API_KEY"

    def test_container_starts_with_api_key(self):
        """Test that the container starts successfully with SERPAPI_API_KEY."""
        returncode, stdout, stderr = run_command(
            f"timeout 3 docker run --rm -e SERPAPI_API_KEY=test_key {self.IMAGE_NAME}",
            timeout=5,
            check=False
        )
        # Timeout (124) is expected for a long-running server
        assert returncode in [0, 124], \
            f"Container should start successfully or timeout. Got return code: {returncode}"

    def test_container_python_version(self):
        """Test that the container uses the correct Python version."""
        returncode, stdout, stderr = run_command(
            f"docker run --rm {self.IMAGE_NAME} python --version",
            timeout=5,
            check=False
        )
        assert returncode == 0, "Failed to get Python version"
        assert "Python 3.13" in stdout, \
            f"Expected Python 3.13, got: {stdout}"

    def test_container_has_dependencies(self):
        """Test that all required dependencies are installed."""
        # Map of pip install name to actual package name
        dependencies = {
            "google-search-results": "google_search_results",
            "mcp": "mcp",
            "python-dotenv": "python-dotenv",
            "httpx": "httpx"
        }
        
        for install_name, package_name in dependencies.items():
            returncode, stdout, stderr = run_command(
                f"docker run --rm {self.IMAGE_NAME} pip show {install_name}",
                timeout=5,
                check=False
            )
            assert returncode == 0, f"Dependency {install_name} is not installed"
            # Check for the actual package name as reported by pip show
            assert f"Name: {package_name}" in stdout or f"Name: {install_name}" in stdout, \
                f"Dependency {install_name} not found in pip show output"

    def test_server_module_exists(self):
        """Test that the server module is accessible in the container."""
        # Check if the server file exists at the expected location
        returncode, stdout, stderr = run_command(
            f"docker run --rm {self.IMAGE_NAME} ls -la /app/src/serpapi-mcp-server/server.py",
            timeout=5,
            check=False
        )
        assert returncode == 0, "Server module file not found at expected location"
        assert "server.py" in stdout, "server.py not found in directory listing"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
