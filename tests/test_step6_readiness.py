from fastapi.testclient import TestClient
from pixell_runtime.rest.server import create_rest_app


def test_health_503_until_ready_then_200():
    app = create_rest_app()
    client = TestClient(app)

    # Initially not ready
    r1 = client.get("/health")
    assert r1.status_code == 503

    # Flip to ready
    app.state.runtime_ready = True
    r2 = client.get("/health")
    assert r2.status_code == 200
    assert r2.json().get("ok") is True


