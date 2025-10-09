import json
from structlog.contextvars import bind_contextvars, clear_contextvars

from pixell_runtime.utils.logging import setup_logging, bind_runtime_context


def test_structured_logs_include_correlation(monkeypatch, capsys):
    monkeypatch.setenv("AGENT_APP_ID", "app-123")
    monkeypatch.setenv("DEPLOYMENT_ID", "dep-456")
    setup_logging("INFO", "json")
    bind_runtime_context("app-123", "dep-456")

    import structlog
    logger = structlog.get_logger()
    logger.info("test_event", foo="bar")
    out = capsys.readouterr().out.strip().splitlines()[-1]
    data = json.loads(out)
    assert data["event"] == "test_event"
    assert data["agentAppId"] == "app-123"
    assert data["deploymentId"] == "dep-456"
    assert data["foo"] == "bar"


def test_redaction(monkeypatch, capsys):
    setup_logging("INFO", "json")
    bind_runtime_context(None, None)
    import structlog
    logger = structlog.get_logger()
    logger.info("leak_test", password="secret", token="abc")
    out = capsys.readouterr().out.strip().splitlines()[-1]
    data = json.loads(out)
    assert data["password"] == "[REDACTED]"
    assert data["token"] == "[REDACTED]"

