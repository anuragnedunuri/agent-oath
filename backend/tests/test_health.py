from fastapi.testclient import TestClient

from app.main import SERVICE_NAME, VERSION


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": SERVICE_NAME,
        "version": VERSION,
        "app_env": "dev",
    }


def test_health_response_matches_declared_schema(client: TestClient) -> None:
    """The response model is enforced, so extra or missing fields would fail here."""
    body = client.get("/health").json()

    assert set(body) == {"status", "service", "version", "app_env"}


def test_openapi_document_builds(client: TestClient) -> None:
    """Catches malformed Pydantic models before they reach a running server."""
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "/health" in response.json()["paths"]
