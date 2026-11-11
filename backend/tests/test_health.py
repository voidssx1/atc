from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_root_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "healthy"
    assert payload["service"] == "atc-dsa-backend"


def test_health_api_v1_endpoint():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "healthy"
    assert payload["service"] == "atc-dsa-backend"
