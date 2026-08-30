"""
FastAPI app exposing a single endpoint that turns a LinkedIn profile URL
into structured JSON, by calling LinkedIn's own Voyager API directly.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from app.auth import get_session_cookies
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
from app.models import ProfileResponse
from app.parser import extract_public_id, parse_profile
from app.voyager_client import VoyagerClient

_cache: dict[str, tuple[float, ProfileResponse]] = {}

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


@app.get("/health")
async def health():
    return {"status": "ok", "authenticated": app.state.client is not None}


@app.get("/v1/profile", response_model=ProfileResponse)
async def get_profile(
    url: str = Query(..., description="A LinkedIn profile URL, e.g. https://www.linkedin.com/in/someone/")
):
    try:
        public_id = extract_public_id(url)
    except InvalidProfileURLError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if app.state.client is None:
        raise HTTPException(status_code=500, detail=f"LinkedIn session unavailable: {app.state.auth_error}")

    cached = _cache.get(public_id)
    if cached and (time.time() - cached[0]) < settings.cache_ttl_seconds:
        return cached[1]

    try:
        raw = await app.state.client.get_profile_view(public_id)
        profile = parse_profile(raw, public_id=public_id, profile_url=url)
    except LinkedInAPIError as exc:
        status_code = _ERROR_STATUS.get(type(exc), 502)
        raise HTTPException(status_code=status_code, detail=str(exc))

    _cache[public_id] = (time.time(), profile)
    return profile
