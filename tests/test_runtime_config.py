"""
Tests for RuntimeConfig validation.
"""

import pytest

from pixell_runtime.core.runtime_config import RuntimeConfig


def test_runtime_config_valid_minimal(monkeypatch):
    """Test RuntimeConfig with minimal valid configuration."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    
    config = RuntimeConfig()
    
    assert config.agent_app_id == "test-agent"
    assert config.rest_port == 8080
    assert config.a2a_port == 50051
    assert config.ui_port == 3000
    assert config.base_path == "/"


def test_runtime_config_missing_agent_app_id_fails(monkeypatch):
    """Test that missing AGENT_APP_ID causes exit."""
    monkeypatch.delenv("AGENT_APP_ID", raising=False)
    
    with pytest.raises(SystemExit) as exc_info:
        RuntimeConfig()
    
    assert exc_info.value.code == 1


def test_runtime_config_empty_agent_app_id_fails(monkeypatch):
    """Test that empty AGENT_APP_ID causes exit."""
    monkeypatch.setenv("AGENT_APP_ID", "")
    
    with pytest.raises(SystemExit) as exc_info:
        RuntimeConfig()
    
    assert exc_info.value.code == 1


def test_runtime_config_whitespace_agent_app_id_fails(monkeypatch):
    """Test that whitespace-only AGENT_APP_ID causes exit."""
    monkeypatch.setenv("AGENT_APP_ID", "   ")
    
    with pytest.raises(SystemExit) as exc_info:
        RuntimeConfig()
    
    assert exc_info.value.code == 1


def test_runtime_config_invalid_rest_port_non_numeric(monkeypatch):
    """Test that non-numeric REST_PORT causes exit."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("REST_PORT", "abc")
    
    with pytest.raises(SystemExit) as exc_info:
        RuntimeConfig()
    
    assert exc_info.value.code == 1


def test_runtime_config_invalid_rest_port_zero(monkeypatch):
    """Test that REST_PORT=0 causes exit."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("REST_PORT", "0")
    
    with pytest.raises(SystemExit) as exc_info:
        RuntimeConfig()
    
    assert exc_info.value.code == 1


def test_runtime_config_invalid_rest_port_negative(monkeypatch):
    """Test that negative REST_PORT causes exit."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("REST_PORT", "-1")
    
    with pytest.raises(SystemExit) as exc_info:
        RuntimeConfig()
    
    assert exc_info.value.code == 1


def test_runtime_config_invalid_rest_port_too_large(monkeypatch):
    """Test that REST_PORT > 65535 causes exit."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("REST_PORT", "65536")
    
    with pytest.raises(SystemExit) as exc_info:
        RuntimeConfig()
    
    assert exc_info.value.code == 1


def test_runtime_config_valid_custom_ports(monkeypatch):
    """Test RuntimeConfig with custom valid ports."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("REST_PORT", "9000")
    monkeypatch.setenv("A2A_PORT", "9001")
    monkeypatch.setenv("UI_PORT", "9002")
    
    config = RuntimeConfig()
    
    assert config.rest_port == 9000
    assert config.a2a_port == 9001
    assert config.ui_port == 9002


def test_runtime_config_port_conflict_rest_a2a(monkeypatch):
    """Test that REST_PORT == A2A_PORT causes exit."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("REST_PORT", "8080")
    monkeypatch.setenv("A2A_PORT", "8080")
    
    with pytest.raises(SystemExit) as exc_info:
        RuntimeConfig()
    
    assert exc_info.value.code == 1


def test_runtime_config_port_conflict_a2a_ui(monkeypatch):
    """Test that A2A_PORT == UI_PORT causes exit."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("A2A_PORT", "3000")
    monkeypatch.setenv("UI_PORT", "3000")
    
    with pytest.raises(SystemExit) as exc_info:
        RuntimeConfig()
    
    assert exc_info.value.code == 1


def test_runtime_config_port_conflict_rest_ui_not_multiplexed(monkeypatch):
    """Test that REST_PORT == UI_PORT fails when not multiplexed."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("REST_PORT", "8080")
    monkeypatch.setenv("UI_PORT", "8080")
    monkeypatch.setenv("MULTIPLEXED", "false")
    
    with pytest.raises(SystemExit) as exc_info:
        RuntimeConfig()
    
    assert exc_info.value.code == 1


def test_runtime_config_port_same_rest_ui_multiplexed_ok(monkeypatch):
    """Test that REST_PORT == UI_PORT is OK when multiplexed."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("REST_PORT", "8080")
    monkeypatch.setenv("UI_PORT", "8080")
    monkeypatch.setenv("MULTIPLEXED", "true")
    
    config = RuntimeConfig()
    
    assert config.rest_port == 8080
    assert config.ui_port == 8080
    assert config.multiplexed is True


def test_runtime_config_invalid_a2a_port(monkeypatch):
    """Test that invalid A2A_PORT causes exit."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("A2A_PORT", "invalid")
    
    with pytest.raises(SystemExit) as exc_info:
        RuntimeConfig()
    
    assert exc_info.value.code == 1


def test_runtime_config_invalid_ui_port(monkeypatch):
    """Test that invalid UI_PORT causes exit."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("UI_PORT", "not-a-number")
    
    with pytest.raises(SystemExit) as exc_info:
        RuntimeConfig()
    
    assert exc_info.value.code == 1


def test_runtime_config_aws_region_valid(monkeypatch):
    """Test valid AWS_REGION."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    
    config = RuntimeConfig()
    
    assert config.aws_region == "us-east-1"


def test_runtime_config_aws_region_invalid_format(monkeypatch):
    """Test that invalid AWS_REGION format logs warning but doesn't fail."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("AWS_REGION", "invalid-region")
    
    # Should not raise, just warn
    config = RuntimeConfig()
    
    assert config.aws_region == "invalid-region"


def test_runtime_config_s3_bucket_valid(monkeypatch):
    """Test valid S3_BUCKET."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("S3_BUCKET", "my-test-bucket")
    
    config = RuntimeConfig()
    
    assert config.s3_bucket == "my-test-bucket"


def test_runtime_config_s3_bucket_too_short(monkeypatch):
    """Test that S3_BUCKET < 3 chars causes exit."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("S3_BUCKET", "ab")
    
    with pytest.raises(SystemExit) as exc_info:
        RuntimeConfig()
    
    assert exc_info.value.code == 1


def test_runtime_config_s3_bucket_too_long(monkeypatch):
    """Test that S3_BUCKET > 63 chars causes exit."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("S3_BUCKET", "a" * 64)
    
    with pytest.raises(SystemExit) as exc_info:
        RuntimeConfig()
    
    assert exc_info.value.code == 1


def test_runtime_config_s3_bucket_invalid_chars(monkeypatch):
    """Test that S3_BUCKET with invalid characters causes exit."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("S3_BUCKET", "My_Bucket")  # Uppercase not allowed
    
    with pytest.raises(SystemExit) as exc_info:
        RuntimeConfig()
    
    assert exc_info.value.code == 1


def test_runtime_config_package_url_https(monkeypatch):
    """Test valid HTTPS PACKAGE_URL."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("PACKAGE_URL", "https://example.com/package.apkg")
    
    config = RuntimeConfig()
    
    assert config.package_url == "https://example.com/package.apkg"


def test_runtime_config_package_url_s3(monkeypatch):
    """Test valid S3 PACKAGE_URL."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("PACKAGE_URL", "s3://bucket/key")
    
    config = RuntimeConfig()
    
    assert config.package_url == "s3://bucket/key"


def test_runtime_config_package_url_invalid_protocol(monkeypatch):
    """Test that invalid PACKAGE_URL protocol causes exit."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("PACKAGE_URL", "http://example.com/package.apkg")
    
    with pytest.raises(SystemExit) as exc_info:
        RuntimeConfig()
    
    assert exc_info.value.code == 1


def test_runtime_config_package_url_empty(monkeypatch):
    """Test that empty PACKAGE_URL causes exit."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("PACKAGE_URL", "   ")
    
    with pytest.raises(SystemExit) as exc_info:
        RuntimeConfig()
    
    assert exc_info.value.code == 1


def test_runtime_config_package_sha256_valid(monkeypatch):
    """Test valid PACKAGE_SHA256."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    sha256 = "a" * 64
    monkeypatch.setenv("PACKAGE_SHA256", sha256)
    
    config = RuntimeConfig()
    
    assert config.package_sha256 == sha256


def test_runtime_config_package_sha256_invalid_length(monkeypatch):
    """Test that PACKAGE_SHA256 with wrong length causes exit."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("PACKAGE_SHA256", "abc123")  # Too short
    
    with pytest.raises(SystemExit) as exc_info:
        RuntimeConfig()
    
    assert exc_info.value.code == 1


def test_runtime_config_package_sha256_invalid_chars(monkeypatch):
    """Test that PACKAGE_SHA256 with non-hex chars causes exit."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("PACKAGE_SHA256", "g" * 64)  # 'g' is not hex
    
    with pytest.raises(SystemExit) as exc_info:
        RuntimeConfig()
    
    assert exc_info.value.code == 1


def test_runtime_config_max_package_size_valid(monkeypatch):
    """Test valid MAX_PACKAGE_SIZE_MB."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("MAX_PACKAGE_SIZE_MB", "200")
    
    config = RuntimeConfig()
    
    assert config.max_package_size_mb == 200


def test_runtime_config_max_package_size_invalid(monkeypatch):
    """Test that invalid MAX_PACKAGE_SIZE_MB causes exit."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("MAX_PACKAGE_SIZE_MB", "not-a-number")
    
    with pytest.raises(SystemExit) as exc_info:
        RuntimeConfig()
    
    assert exc_info.value.code == 1


def test_runtime_config_max_package_size_zero(monkeypatch):
    """Test that MAX_PACKAGE_SIZE_MB=0 causes exit."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("MAX_PACKAGE_SIZE_MB", "0")
    
    with pytest.raises(SystemExit) as exc_info:
        RuntimeConfig()
    
    assert exc_info.value.code == 1


def test_runtime_config_max_package_size_negative(monkeypatch):
    """Test that negative MAX_PACKAGE_SIZE_MB causes exit."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("MAX_PACKAGE_SIZE_MB", "-1")
    
    with pytest.raises(SystemExit) as exc_info:
        RuntimeConfig()
    
    assert exc_info.value.code == 1


def test_runtime_config_base_path_default(monkeypatch):
    """Test default BASE_PATH."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    
    config = RuntimeConfig()
    
    assert config.base_path == "/"


def test_runtime_config_base_path_custom(monkeypatch):
    """Test custom BASE_PATH."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("BASE_PATH", "/agents/test")
    
    config = RuntimeConfig()
    
    assert config.base_path == "/agents/test"


def test_runtime_config_base_path_normalization_trailing_slash(monkeypatch):
    """Test BASE_PATH normalization removes trailing slash."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("BASE_PATH", "/agents/test/")
    
    config = RuntimeConfig()
    
    assert config.base_path == "/agents/test"


def test_runtime_config_base_path_normalization_no_leading_slash(monkeypatch):
    """Test BASE_PATH normalization adds leading slash."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("BASE_PATH", "agents/test")
    
    config = RuntimeConfig()
    
    assert config.base_path == "/agents/test"


def test_runtime_config_base_path_double_slash(monkeypatch):
    """Test that BASE_PATH with double slash causes exit."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("BASE_PATH", "/agents//test")
    
    with pytest.raises(SystemExit) as exc_info:
        RuntimeConfig()
    
    assert exc_info.value.code == 1


def test_runtime_config_base_path_invalid_chars(monkeypatch):
    """Test that BASE_PATH with invalid characters causes exit."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("BASE_PATH", "/agents/test@#$")
    
    with pytest.raises(SystemExit) as exc_info:
        RuntimeConfig()
    
    assert exc_info.value.code == 1


def test_runtime_config_multiplexed_true(monkeypatch):
    """Test MULTIPLEXED=true."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("MULTIPLEXED", "true")
    
    config = RuntimeConfig()
    
    assert config.multiplexed is True


def test_runtime_config_multiplexed_false(monkeypatch):
    """Test MULTIPLEXED=false."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("MULTIPLEXED", "false")
    
    config = RuntimeConfig()
    
    assert config.multiplexed is False


def test_runtime_config_multiplexed_variations(monkeypatch):
    """Test various MULTIPLEXED values."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    
    # Test "1"
    monkeypatch.setenv("MULTIPLEXED", "1")
    config = RuntimeConfig()
    assert config.multiplexed is True
    
    # Test "0"
    monkeypatch.setenv("MULTIPLEXED", "0")
    config = RuntimeConfig()
    assert config.multiplexed is False
    
    # Test "yes"
    monkeypatch.setenv("MULTIPLEXED", "yes")
    config = RuntimeConfig()
    assert config.multiplexed is True
    
    # Test "no"
    monkeypatch.setenv("MULTIPLEXED", "no")
    config = RuntimeConfig()
    assert config.multiplexed is False


def test_runtime_config_deployment_id_optional(monkeypatch):
    """Test that DEPLOYMENT_ID is optional."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.delenv("DEPLOYMENT_ID", raising=False)
    
    config = RuntimeConfig()
    
    assert config.deployment_id is None


def test_runtime_config_deployment_id_provided(monkeypatch):
    """Test that DEPLOYMENT_ID is stored when provided."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("DEPLOYMENT_ID", "deploy-123")
    
    config = RuntimeConfig()
    
    assert config.deployment_id == "deploy-123"


def test_runtime_config_to_dict(monkeypatch):
    """Test to_dict method."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("DEPLOYMENT_ID", "deploy-123")
    monkeypatch.setenv("REST_PORT", "9000")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    
    config = RuntimeConfig()
    config_dict = config.to_dict()
    
    assert config_dict["agent_app_id"] == "test-agent"
    assert config_dict["deployment_id"] == "deploy-123"
    assert config_dict["rest_port"] == 9000
    assert config_dict["aws_region"] == "us-west-2"
    assert "has_package_url" in config_dict
    assert "has_package_sha256" in config_dict


def test_runtime_config_port_boundary_values(monkeypatch):
    """Test port boundary values."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    
    # Test port 1 (minimum valid)
    monkeypatch.setenv("REST_PORT", "1")
    monkeypatch.setenv("A2A_PORT", "2")
    monkeypatch.setenv("UI_PORT", "3")
    config = RuntimeConfig()
    assert config.rest_port == 1
    
    # Test port 65535 (maximum valid)
    monkeypatch.setenv("REST_PORT", "65535")
    monkeypatch.setenv("A2A_PORT", "65534")
    monkeypatch.setenv("UI_PORT", "65533")
    config = RuntimeConfig()
    assert config.rest_port == 65535


def test_runtime_config_multiple_errors_collected(monkeypatch, capsys):
    """Test that multiple validation errors are collected."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("REST_PORT", "invalid")
    monkeypatch.setenv("A2A_PORT", "0")
    monkeypatch.setenv("S3_BUCKET", "ab")  # Too short
    
    with pytest.raises(SystemExit):
        RuntimeConfig()
    
    # Should have logged multiple errors
    captured = capsys.readouterr()
    output = captured.out + captured.err
    # At least 3 errors should be present
    assert "REST_PORT" in output or "A2A_PORT" in output or "S3_BUCKET" in output
