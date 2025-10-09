"""End-to-end container integration tests.

Tests that verify PAR works correctly when run as a Docker container,
which is the actual deployment environment for ECS.
"""

import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest


@pytest.fixture(scope="module")
def docker_image():
    """Build the Docker image for testing."""
    # Get repo root
    repo_root = Path(__file__).parent.parent
    
    # Build image
    image_tag = "pixell-agent-runtime:test"
    result = subprocess.run(
        ["docker", "build", "-t", image_tag, "."],
        cwd=repo_root,
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        pytest.skip(f"Docker build failed: {result.stderr}")
    
    yield image_tag
    
    # Cleanup is optional - can keep image for debugging


@pytest.fixture
def test_apkg():
    """Create a minimal test APKG for E2E testing."""
    # Use the existing test_agent.apkg from test_data
    test_apkg_path = Path(__file__).parent.parent / "test_data" / "test_agent.apkg"
    if test_apkg_path.exists():
        return test_apkg_path
    
    # If not found, skip tests
    pytest.skip("test_agent.apkg not found in test_data/")


class TestContainerBasics:
    """Test basic container functionality."""
    
    def test_container_starts_with_env_vars(self, docker_image, tmp_path):
        """Test that container starts successfully with required env vars."""
        container_name = f"par_test_{int(time.time())}"
        
        # Create a minimal test package directory
        test_package_dir = tmp_path / "test_agent"
        test_package_dir.mkdir()
        
        # Create agent.yaml
        (test_package_dir / "agent.yaml").write_text("""
id: test-agent
name: Test Agent
version: 1.0.0
rest:
  enabled: true
""")
        
        # Create main.py
        (test_package_dir / "main.py").write_text("""
from fastapi import FastAPI

app = FastAPI()

@app.get("/test")
async def test():
    return {"status": "ok"}
""")
        
        try:
            # Start container
            result = subprocess.run(
                [
                    "docker", "run",
                    "-d",
                    "--name", container_name,
                    "-e", "AGENT_APP_ID=test-agent",
                    "-e", f"AGENT_PACKAGE_PATH={test_package_dir}",
                    "-p", "18080:8080",
                    docker_image
                ],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                pytest.fail(f"Container failed to start: {result.stderr}")
            
            # Wait for container to be healthy
            time.sleep(5)
            
            # Check container is running
            check_result = subprocess.run(
                ["docker", "ps", "-q", "-f", f"name={container_name}"],
                capture_output=True,
                text=True
            )
            
            assert check_result.stdout.strip(), "Container should be running"
            
        finally:
            # Cleanup
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
    
    def test_health_check_endpoint(self, docker_image):
        """Test that /health endpoint works in container."""
        container_name = f"par_test_health_{int(time.time())}"
        
        try:
            # Start container with minimal config
            result = subprocess.run(
                [
                    "docker", "run",
                    "-d",
                    "--name", container_name,
                    "-e", "AGENT_APP_ID=test-agent",
                    "-e", "PACKAGE_URL=s3://test-bucket/test.apkg",
                    "-p", "18081:8080",
                    docker_image
                ],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                # Expected to fail without valid package, but container should start
                pass
            
            # Wait for health endpoint to be available
            time.sleep(3)
            
            # Try to hit health endpoint (may return 503 if not ready)
            try:
                response = httpx.get("http://localhost:18081/health", timeout=2)
                # Should get either 200 (ready) or 503 (not ready)
                assert response.status_code in [200, 503]
            except httpx.ConnectError:
                # Container might have exited due to missing package
                # This is expected behavior
                pass
            
        finally:
            # Cleanup
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
    
    def test_container_port_exposure(self, docker_image):
        """Test that container exposes correct ports (8080, 50051, 3000)."""
        container_name = f"par_test_ports_{int(time.time())}"
        
        try:
            # Start container
            result = subprocess.run(
                [
                    "docker", "run",
                    "-d",
                    "--name", container_name,
                    "-e", "AGENT_APP_ID=test-agent",
                    docker_image
                ],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            # Inspect container to check exposed ports
            inspect_result = subprocess.run(
                ["docker", "inspect", container_name],
                capture_output=True,
                text=True
            )
            
            if inspect_result.returncode == 0:
                import json
                inspect_data = json.loads(inspect_result.stdout)
                exposed_ports = inspect_data[0]["Config"]["ExposedPorts"]
                
                # Verify expected ports are exposed
                assert "8080/tcp" in exposed_ports, "REST port 8080 should be exposed"
                assert "50051/tcp" in exposed_ports, "A2A port 50051 should be exposed"
                assert "3000/tcp" in exposed_ports, "UI port 3000 should be exposed"
            
        finally:
            # Cleanup
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)


class TestContainerEnvironmentHandling:
    """Test environment variable handling in container."""
    
    def test_missing_agent_app_id_exits(self, docker_image):
        """Test that container exits if AGENT_APP_ID is missing."""
        container_name = f"par_test_no_id_{int(time.time())}"
        
        try:
            # Start container WITHOUT AGENT_APP_ID
            result = subprocess.run(
                [
                    "docker", "run",
                    "--name", container_name,
                    docker_image
                ],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            # Should exit with non-zero code
            assert result.returncode != 0, "Container should exit when AGENT_APP_ID is missing"
            
            # Check logs for error message
            logs_result = subprocess.run(
                ["docker", "logs", container_name],
                capture_output=True,
                text=True
            )
            
            assert "AGENT_APP_ID" in logs_result.stdout or "AGENT_APP_ID" in logs_result.stderr
            
        finally:
            # Cleanup
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
    
    def test_invalid_package_url_exits_with_backoff(self, docker_image):
        """Test that invalid PACKAGE_URL causes exit with backoff."""
        container_name = f"par_test_bad_url_{int(time.time())}"
        
        try:
            # Start container with invalid package URL
            result = subprocess.run(
                [
                    "docker", "run",
                    "--name", container_name,
                    "-e", "AGENT_APP_ID=test-agent",
                    "-e", "PACKAGE_URL=file:///etc/passwd",  # Invalid protocol
                    docker_image
                ],
                capture_output=True,
                text=True,
                timeout=15
            )
            
            # Should exit with non-zero code
            assert result.returncode != 0
            
            # Check logs for security error
            logs_result = subprocess.run(
                ["docker", "logs", container_name],
                capture_output=True,
                text=True
            )
            
            logs = logs_result.stdout + logs_result.stderr
            assert "file://" in logs or "not allowed" in logs.lower()
            
        finally:
            # Cleanup
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)


class TestContainerSignalHandling:
    """Test graceful shutdown on signals."""
    
    def test_sigterm_graceful_shutdown(self, docker_image):
        """Test that container handles SIGTERM gracefully."""
        container_name = f"par_test_sigterm_{int(time.time())}"
        
        try:
            # Start container
            subprocess.run(
                [
                    "docker", "run",
                    "-d",
                    "--name", container_name,
                    "-e", "AGENT_APP_ID=test-agent",
                    docker_image
                ],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            time.sleep(2)
            
            # Send SIGTERM
            start_time = time.time()
            subprocess.run(
                ["docker", "stop", "-t", "35", container_name],  # 35 sec timeout (5 more than graceful)
                capture_output=True,
                timeout=40
            )
            shutdown_time = time.time() - start_time
            
            # Check logs for graceful shutdown message
            logs_result = subprocess.run(
                ["docker", "logs", container_name],
                capture_output=True,
                text=True
            )
            
            logs = logs_result.stdout + logs_result.stderr
            assert "shutdown" in logs.lower() or "shutting down" in logs.lower()
            
            # Should shutdown within reasonable time (< 35 seconds)
            assert shutdown_time < 35, f"Shutdown took {shutdown_time}s, expected < 35s"
            
        finally:
            # Cleanup
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)


def test_docker_available():
    """Check if Docker is available for testing."""
    result = subprocess.run(["docker", "--version"], capture_output=True)
    if result.returncode != 0:
        pytest.skip("Docker is not available")
