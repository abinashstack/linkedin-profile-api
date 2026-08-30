"""API-level tests. No LinkedIn credentials are configured in this environment,
so these only exercise routing, input validation, and the no-session error path
-- not a real profile fetch."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    # Lifespan (startup/shutdown) only runs when TestClient is used as a
    # context manager -- needed so app.state.client is set before requests.
    with TestClient(app) as c:
        yield c


def test_index_serves_html(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "LinkedIn Profile Lookup" in res.text


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_get_profile_rejects_invalid_url(client):
    res = client.get("/v1/profile", params={"url": "https://example.com/in/jane-doe"})
    assert res.status_code == 400


def test_get_profile_without_server_session_returns_500(client):
    # No LINKEDIN_LI_AT / credentials are set in the test environment.
    res = client.get("/v1/profile", params={"url": "jane-doe"})
    assert res.status_code == 500


def test_post_profile_requires_li_at(client):
    res = client.post("/v1/profile", json={"url": "jane-doe"})
    assert res.status_code == 400
    assert "li_at" in res.json()["detail"]


def test_post_profile_rejects_invalid_url_before_checking_cookie(client):
    res = client.post("/v1/profile", json={"url": "not a real url", "li_at": "fake"})
    assert res.status_code == 400
