"""
FastAPI app exposing:
- a browser UI (/) for pasting a session cookie + a profile URL/handle
- GET  /v1/profile  -- uses the server's own configured LinkedIn session
- POST /v1/profile  -- uses a session cookie supplied in the request body

Both call LinkedIn's own Voyager API directly; nothing here renders a page
or drives a browser.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from app.auth import get_session_cookies, make_jsessionid
from app.config import settings
from app.exceptions import (
    AuthenticationError,
    ChallengeRequiredError,
    InvalidProfileURLError,
    LinkedInAPIError,
    ProfileNotFoundError,
    RateLimitedError,
    SessionExpiredError,
    UpstreamError,
)
from app.models import ProfileRequest, ProfileResponse
from app.parser import extract_public_id, parse_profile
from app.voyager_client import VoyagerClient

_cache: dict[str, tuple[float, ProfileResponse]] = {}
_STATIC_DIR = Path(__file__).parent / "static"

_ERROR_STATUS = {
    InvalidProfileURLError: 400,
    ProfileNotFoundError: 404,
    SessionExpiredError: 401,
    ChallengeRequiredError: 423,
    RateLimitedError: 429,
    AuthenticationError: 500,
    UpstreamError: 502,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = None
    app.state.auth_error: str | None = None
    try:
        cookies = get_session_cookies()
        app.state.client = VoyagerClient(cookies)
    except AuthenticationError as exc:
        # Don't crash the process on boot -- surface the problem per-request
        # instead, so /health still responds and the deploy doesn't loop-crash.
        app.state.auth_error = str(exc)
    yield
    if app.state.client:
        await app.state.client.aclose()


app = FastAPI(
    title="LinkedIn Profile API",
    description=(
        "Accepts a public LinkedIn profile URL and returns structured profile data, "
        "fetched directly from LinkedIn's internal Voyager JSON API (no browser)."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/", response_class=HTMLResponse)
async def index():
    return (_STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/health")
async def health():
    return {"status": "ok", "authenticated": app.state.client is not None}


async def _fetch_and_parse(client: VoyagerClient, public_id: str) -> ProfileResponse:
    try:
        raw = await client.get_profile_view(public_id)
    except LinkedInAPIError as exc:
        raise HTTPException(status_code=_ERROR_STATUS.get(type(exc), 502), detail=str(exc))
    return parse_profile(raw, public_id=public_id, profile_url=f"https://www.linkedin.com/in/{public_id}/")


@app.get("/v1/profile", response_model=ProfileResponse)
async def get_profile(
    url: str = Query(..., description="A LinkedIn profile URL or handle, e.g. https://www.linkedin.com/in/someone/ or 'someone'")
):
    """Looks up a profile using the session configured on the server (LINKEDIN_LI_AT / login)."""
    try:
        public_id = extract_public_id(url)
    except InvalidProfileURLError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if app.state.client is None:
        raise HTTPException(status_code=500, detail=f"LinkedIn session unavailable: {app.state.auth_error}")

    cached = _cache.get(public_id)
    if cached and (time.time() - cached[0]) < settings.cache_ttl_seconds:
        return cached[1]

    profile = await _fetch_and_parse(app.state.client, public_id)
    _cache[public_id] = (time.time(), profile)
    return profile


@app.post("/v1/profile", response_model=ProfileResponse)
async def post_profile(payload: ProfileRequest):
    """Looks up a profile using a session cookie supplied in the request body.

    The cookie is used only to build a throwaway client for this one request
    and is never written to disk, logged, or cached -- only the parsed
    profile output is cached, keyed by public_id."""
    try:
        public_id = extract_public_id(payload.url)
    except InvalidProfileURLError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not payload.li_at:
        raise HTTPException(status_code=400, detail="li_at is required for POST /v1/profile.")

    client = VoyagerClient({"li_at": payload.li_at, "JSESSIONID": make_jsessionid(payload.li_at)})
    try:
        return await _fetch_and_parse(client, public_id)
    finally:
        await client.aclose()
