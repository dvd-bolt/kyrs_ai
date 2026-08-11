import pytest
from fastapi.testclient import TestClient
from web_app import app

client = TestClient(app)

def test_web_app_home():
    response = client.get("/")
    assert response.status_code == 200
    assert "Step 1" in response.text or "Configurator" in response.text

def test_web_app_steps():
    for step_num in range(1, 7):
        response = client.get(f"/step/{step_num}")
        assert response.status_code == 200

def test_web_app_api_state():
    response = client.get("/api/state")
    assert response.status_code == 200
    data = response.json()
    assert "topic" in data
    assert "project_type" in data
