"""
Obtains a LinkedIn session (an `li_at` cookie) without a browser.

Two paths, tried in this order:

1. LINKEDIN_LI_AT env var -- you logged into linkedin.com in a real browser
   once and copied the cookie out of devtools. Most reliable; recommended.
2. LINKEDIN_EMAIL / LINKEDIN_PASSWORD -- the backend reproduces the plain
   web login flow with raw HTTP requests (GET the login page for an
   anti-forgery token, POST credentials to the same endpoint the login form
   itself submits to). No browser is launched anywhere in this path either.
   LinkedIn frequently answers this with a security checkpoint (CAPTCHA /
   email verification) instead of a session, especially from an unfamiliar
   or datacenter IP -- when that happens we raise ChallengeRequiredError
   rather than trying to solve it. See the README's "Known limitations".

A successful credential login is cached to disk (SESSION_CACHE_PATH) so a
process restart doesn't force another login attempt.
"""
from __future__ import annotations

import hashlib
import json
import re

import httpx

from app.config import settings
from app.exceptions import AuthenticationError, ChallengeRequiredError

LOGIN_PAGE_URL = "https://www.linkedin.com/uas/login"
LOGIN_SUBMIT_URL = "https://www.linkedin.com/uas/login-submit"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_CSRF_RE = re.compile(r'name="loginCsrfParam"\s+value="([^"]*)"')


def make_jsessionid(seed: str) -> str:
    """Voyager accepts any JSESSIONID as long as it matches the csrf-token header
    sent with each request; it does not have to come from LinkedIn's server. We
    derive one deterministically from the li_at value so it's stable across
    restarts."""
    digest = hashlib.sha256(seed.encode()).hexdigest()[:16]
    return f'"ajax:{int(digest, 16) % 10**19}"'


def _load_cached_session() -> dict[str, str] | None:
    path = settings.session_cache_path
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if data.get("li_at") and data.get("JSESSIONID"):
        return data
    return None


def _save_cached_session(cookies: dict[str, str]) -> None:
    path = settings.session_cache_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cookies))


def _login_with_credentials(email: str, password: str) -> dict[str, str]:
    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=settings.request_timeout_seconds,
    ) as client:
        login_page = client.get(LOGIN_PAGE_URL)
        match = _CSRF_RE.search(login_page.text)
        if not match:
            raise AuthenticationError(
                "Could not find loginCsrfParam on LinkedIn's login page; its markup may have changed."
            )

        response = client.post(
            LOGIN_SUBMIT_URL,
            data={
                "session_key": email,
                "session_password": password,
                "loginCsrfParam": match.group(1),
            },
        )

        li_at = client.cookies.get("li_at")
        jsessionid = client.cookies.get("JSESSIONID")

        if not li_at:
            if "checkpoint" in str(response.url):
                raise ChallengeRequiredError(
                    "LinkedIn responded with a security checkpoint (CAPTCHA / verification code) "
                    "instead of a session cookie. Programmatic login cannot solve this. Log in "
                    "manually in a browser once and set LINKEDIN_LI_AT instead."
                )
            raise AuthenticationError(f"Login did not return a session cookie (ended at {response.url}).")

        cookies = {"li_at": li_at, "JSESSIONID": jsessionid or make_jsessionid(li_at)}
        _save_cached_session(cookies)
        return cookies


def get_session_cookies() -> dict[str, str]:
    if settings.li_at:
        return {"li_at": settings.li_at, "JSESSIONID": make_jsessionid(settings.li_at)}

    cached = _load_cached_session()
    if cached:
        return cached

    if settings.email and settings.password:
        return _login_with_credentials(settings.email, settings.password)

    raise AuthenticationError(
        "No LinkedIn session available. Set LINKEDIN_LI_AT, or LINKEDIN_EMAIL + LINKEDIN_PASSWORD."
    )
