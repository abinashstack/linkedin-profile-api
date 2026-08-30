"""Tests for POST /v1/profiles/batch, using a fake VoyagerClient so no real
LinkedIn calls are made."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.exceptions import ChallengeRequiredError, ProfileNotFoundError
from app.main import _cache, app

FIXTURE = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "sample_profile_response.json").read_text()
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


@pytest.fixture(autouse=True)
def _no_delay(monkeypatch):
    # Keep tests fast; the delay is only meaningful against the real API.
    monkeypatch.setattr(settings, "batch_delay_seconds", 0)


@pytest.fixture(autouse=True)
def _clear_response_cache():
    # _cache is module-level state in app.main, shared across every test in
    # the process -- without clearing it, one test's cached "jane-doe" result
    # leaks into the next and masks whether the fake client was actually called.
    _cache.clear()
    yield
    _cache.clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_batch_rejects_empty_list(client):
    res = client.post("/v1/profiles/batch", json={"urls": []})
    assert res.status_code == 400


def test_batch_rejects_oversized_list(client):
    urls = [f"person-{i}" for i in range(settings.batch_max_size + 1)]
    res = client.post("/v1/profiles/batch", json={"urls": urls})
    assert res.status_code == 400
    assert "too large" in res.json()["detail"].lower()


def test_batch_happy_path(client):
    fake = FakeVoyagerClient({"jane-doe": FIXTURE, "other-person": FIXTURE})
    app.state.client = fake
    try:
        res = client.post("/v1/profiles/batch", json={"urls": ["jane-doe", "other-person"]})
    finally:
        app.state.client = None

    assert res.status_code == 200
    results = res.json()["results"]
    assert len(results) == 2
    assert all(r["ok"] for r in results)
    assert results[0]["profile"]["name"] == "Jane Doe"
    assert fake.calls == ["jane-doe", "other-person"]


def test_batch_mixed_success_and_not_found(client):
    fake = FakeVoyagerClient({"jane-doe": FIXTURE, "ghost": None})
    app.state.client = fake
    try:
        res = client.post("/v1/profiles/batch", json={"urls": ["jane-doe", "ghost"]})
    finally:
        app.state.client = None

    results = res.json()["results"]
    assert results[0]["ok"] is True
    assert results[1]["ok"] is False
    assert "no such profile" in results[1]["error"]


def test_batch_aborts_on_challenge_and_skips_remainder(client):
    fake = FakeVoyagerClient({"jane-doe": ChallengeRequiredError("challenged")})
    app.state.client = fake
    try:
        res = client.post(
            "/v1/profiles/batch", json={"urls": ["jane-doe", "never-called-1", "never-called-2"]}
        )
    finally:
        app.state.client = None

    results = res.json()["results"]
    assert len(results) == 3
    assert results[0]["ok"] is False and "challenged" in results[0]["error"]
    assert all(not r["ok"] and "Skipped" in r["error"] for r in results[1:])
    # The client was only ever asked about the first profile.
    assert fake.calls == ["jane-doe"]


def test_batch_invalid_url_does_not_call_client(client):
    fake = FakeVoyagerClient({"jane-doe": FIXTURE})
    app.state.client = fake
    try:
        res = client.post("/v1/profiles/batch", json={"urls": ["not a real url", "jane-doe"]})
    finally:
        app.state.client = None

    results = res.json()["results"]
    assert results[0]["ok"] is False
    assert results[1]["ok"] is True
    assert fake.calls == ["jane-doe"]
