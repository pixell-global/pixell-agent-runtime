from fastapi.testclient import TestClient
from pixell_runtime.rest.server import create_rest_app


def test_health_gated_until_ready():
    app = create_rest_app()
    client = TestClient(app)

    # Force not ready
    app.state.runtime_ready = False
    r = client.get("/health")
    assert r.status_code == 503

    # Flip ready
    app.state.runtime_ready = True
    r2 = client.get("/health")
    assert r2.status_code == 200
    assert r2.json().get("ok") is True


def test_base_path_health_and_no_double_prefix():
    app = create_rest_app(base_path="/agents/app")
    client = TestClient(app)
    app.state.runtime_ready = True

    # Built-in health under base path works
    r = client.get("/agents/app/health")
    assert r.status_code == 200

    # Double prefix should 404
    r2 = client.get("/agents/app/agents/app/health")
    assert r2.status_code == 404
