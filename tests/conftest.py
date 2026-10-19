"""Integration fixtures: each test receives a fresh, seeded SQLite database."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(
        db_path=str(tmp_path / "supportdesk-test.sqlite3"),
        llm_provider="mock",
        reviewer_api_key="test-reviewer-secret",
    )


@pytest.fixture
def app(settings):
    return create_app(settings=settings)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def login(client):
    def authenticate(persona="alex"):
        response = client.post("/demo/sessions", json={"persona": persona})
        assert response.status_code == 200, response.text
        token = response.json()["access_token"]
        assert token
        return {"Authorization": f"Bearer {token}"}

    return authenticate
