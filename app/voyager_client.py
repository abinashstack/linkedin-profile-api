"""
Thin client for LinkedIn's internal "Voyager" API -- the JSON API
linkedin.com's own web front-end calls under the hood. This talks to it
directly over HTTPS with plain requests; nothing here renders a page or
drives a browser.
"""
from __future__ import annotations

import httpx

from app.config import settings
from app.exceptions import (
    ChallengeRequiredError,
    ProfileNotFoundError,
    RateLimitedError,
    SessionExpiredError,
    UpstreamError,
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

PROFILE_VIEW_URL = "https://www.linkedin.com/voyager/api/identity/profiles/{public_id}/profileView"


class VoyagerClient:
    def __init__(self, cookies: dict[str, str]):
        csrf_token = cookies["JSESSIONID"].strip('"')
        self._client = httpx.AsyncClient(
            cookies=cookies,
            headers={
                "User-Agent": USER_AGENT,
                "csrf-token": csrf_token,
                "x-restli-protocol-version": "2.0.0",
                "x-li-lang": "en_US",
                "accept": "application/vnd.linkedin.normalized+json+2.1",
            },
            timeout=settings.request_timeout_seconds,
        )

    async def get_profile_view(self, public_id: str) -> dict:
        response = await self._client.get(PROFILE_VIEW_URL.format(public_id=public_id))

        if response.status_code == 200:
            return response.json()
        if response.status_code == 404:
            raise ProfileNotFoundError(f"No profile found for '{public_id}' (or it's private).")
        if response.status_code in (401, 403):
            raise SessionExpiredError(
                "LinkedIn rejected the session cookie (expired or invalid). Refresh LINKEDIN_LI_AT."
            )
        if response.status_code == 429:
            raise RateLimitedError("LinkedIn rate-limited this session. Back off and retry later.")
        if response.status_code == 999:
            raise ChallengeRequiredError("LinkedIn returned its anti-scraping challenge page (HTTP 999).")
        raise UpstreamError(f"Unexpected response from LinkedIn: HTTP {response.status_code}")

    async def aclose(self) -> None:
        await self._client.aclose()
