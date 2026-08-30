"""API-level tests. No LinkedIn credentials are configured in this environment,
so these only exercise routing, input validation, and the no-session error path
-- not a real profile fetch."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.exceptions import ProfileNotFoundError
from app.main import _cache, app

FIXTURE = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "sample_profile_response.json").read_text(encoding="utf-8")
)


class FakeVoyagerClient:
    """Stands in for VoyagerClient: `behavior` maps public_id -> a raw
    profileView dict to return, or an exception instance to raise."""

    def __init__(self, behavior):
        self.behavior = behavior
        self.calls: list[str] = []

    async def get_profile_view(self, public_id):
        self.calls.append(public_id)
        outcome = self.behavior.get(public_id)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is None:
            raise ProfileNotFoundError(f"no such profile: {public_id}")
        return outcome

    async def aclose(self):
        pass


@pytest.fixture
def client():
    # Lifespan (startup/shutdown) only runs when TestClient is used as a
    # context manager -- needed so app.state.client is set before requests.
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_response_cache():
    # _cache is module-level state in app.main, shared across every test in
    # the process -- clear it so one test's cached result can't leak into
    # the next and mask whether the (fake) client was actually called.
    _cache.clear()
    yield
    _cache.clear()


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


def test_get_profile_uses_server_session_when_configured(client):
    app.state.client = FakeVoyagerClient({"jane-doe": FIXTURE})
    try:
        res = client.get("/v1/profile", params={"url": "jane-doe"})
    finally:
        app.state.client = None
    assert res.status_code == 200
    assert res.json()["name"] == "Jane Doe"


def test_post_profile_without_li_at_falls_back_to_server_session(client):
    """This is the home-page default: no cookie override, use the server's own session."""
    fake = FakeVoyagerClient({"jane-doe": FIXTURE})
    app.state.client = fake
    try:
        res = client.post("/v1/profile", json={"url": "jane-doe"})
    finally:
        app.state.client = None
    assert res.status_code == 200
    assert res.json()["name"] == "Jane Doe"


def test_post_profile_without_li_at_and_no_server_session_returns_500(client):
    res = client.post("/v1/profile", json={"url": "jane-doe"})
    assert res.status_code == 500


def test_post_profile_rejects_invalid_url_before_checking_cookie(client):
    res = client.post("/v1/profile", json={"url": "not a real url", "li_at": "fake"})
    assert res.status_code == 400
