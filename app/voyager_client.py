"""
Client for LinkedIn's current profile-rendering system.

The old approach here hit `/voyager/api/identity/profiles/<id>/profileView`
(a REST endpoint) and later a GraphQL query -- both are gone; LinkedIn now
renders profile pages through a React Server Components ("Flight" protocol)
action endpoint instead. See app/sdui_parser.py for what that response
looks like and why it needs a different kind of parsing than a normal JSON
API. This client fetches:

1. The plain profile HTML page, for name and headline. The original plan
   here was Open Graph meta tags (`og:title`, `og:description`) -- verified
   live to NOT exist at all once authenticated (LinkedIn only serves them
   on the logged-out view, for link-preview purposes; an authenticated
   fetch, which is all this client ever does, gets none of them). What
   actually works, confirmed against two different real profiles: the
   `<title>` tag is just `"<Name> | LinkedIn"`, and the headline is plain
   server-rendered HTML immediately after -- a bare `<span>` with no
   distinct className to anchor on, so it's found by searching just past
   the name's own text. Location and photo are not resolved at all yet;
   see the README's "Known limitations".
2. Four SDUI "component" actions (about/experience/education/skills),
   parsed by app/sdui_parser.py.

Both are needed for one profile lookup, so a single call here makes five
requests to LinkedIn, not one -- relevant to how aggressively this gets
called (see BATCH_DELAY_SECONDS in app/config.py).
"""
from __future__ import annotations

import html
import re

import httpx

from app.config import settings
from app.exceptions import (
    ChallengeRequiredError,
    ProfileNotFoundError,
    RateLimitedError,
    SessionExpiredError,
    UpstreamError,
)
from app.sdui_parser import (
    extract_about_text,
    extract_card_entries,
    extract_skills,
    parse_flight_response,
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

PROFILE_PAGE_URL = "https://www.linkedin.com/in/{public_id}/"
COMPONENT_URL = "https://www.linkedin.com/flagship-web/rsc-action/actions/component"

# aboutTopLevelSection, experienceTopLevelSection, and skillsTopLevelSection
# have been verified against real captured responses; educationTopLevelSection
# is assumed to follow experienceTopLevelSection's shape (same design system,
# same naming convention) but has not been independently confirmed.
COMPONENT_IDS = {
    "about": "com.linkedin.sdui.generated.profile.dsl.impl.aboutTopLevelSection",
    "experience": "com.linkedin.sdui.generated.profile.dsl.impl.experienceTopLevelSection",
    "education": "com.linkedin.sdui.generated.profile.dsl.impl.educationTopLevelSection",
    "skills": "com.linkedin.sdui.generated.profile.dsl.impl.skillsTopLevelSection",
}

_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_TRAILING_LINKEDIN_RE = re.compile(r"\s*\|\s*LinkedIn\s*$", re.IGNORECASE)
_HEADLINE_SPAN_RE = re.compile(r"<span>([^<]+)</span>")
_HEADLINE_SEARCH_WINDOW = 500  # chars scanned after the name's own </p> for the headline span

# Kept as a fallback only -- confirmed live these don't exist on an
# authenticated page load, but cost nothing to still check.
_OG_TITLE_RE = re.compile(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', re.IGNORECASE)
_OG_DESCRIPTION_RE = re.compile(r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"', re.IGNORECASE)
_OG_IMAGE_RE = re.compile(r'<meta[^>]+property="og:image"[^>]+content="([^"]*)"', re.IGNORECASE)


def _raise_for_status(response: httpx.Response, context: str) -> None:
    if response.status_code == 200:
        return
    if response.status_code == 404:
        raise ProfileNotFoundError(f"No profile found ({context}), or it's private.")
    if response.status_code in (401, 403):
        raise SessionExpiredError(
            f"LinkedIn rejected the session cookie ({context}, expired or invalid). Refresh LINKEDIN_LI_AT."
        )
    if response.status_code == 429:
        raise RateLimitedError(f"LinkedIn rate-limited this session ({context}).")
    if response.status_code == 999:
        raise ChallengeRequiredError(f"LinkedIn returned its anti-scraping challenge page ({context}, HTTP 999).")
    if response.status_code == 410:
        raise UpstreamError(
            f"LinkedIn returned HTTP 410 Gone ({context}) -- this endpoint or componentId has likely "
            "been retired or renamed since this was last checked. See the README's 'Known limitations'."
        )
    raise UpstreamError(f"Unexpected response from LinkedIn ({context}): HTTP {response.status_code}")


def _unescape(match: re.Match | None) -> str | None:
    return html.unescape(match.group(1)).strip() if match else None


def _extract_name(page_html: str) -> str | None:
    """<title> is just "<Name> | LinkedIn" on an authenticated page load --
    no headline in it. Confirmed against two different real profiles."""
    name = _unescape(_TITLE_RE.search(page_html))
    if not name:
        return None
    return _TRAILING_LINKEDIN_RE.sub("", name).strip() or None


def _extract_headline(page_html: str, name: str | None) -> str | None:
    """The headline isn't behind a meta tag or a separate component fetch
    -- it's plain server-rendered HTML immediately after the name's own
    paragraph, as a bare <span> with no distinct className to match on
    instead. Anchored on the actual name text since that's the only stable
    marker available; confirmed against two different real profiles."""
    if not name:
        return None
    idx = page_html.find(f">{name}</p>")
    if idx == -1:
        return None
    window = page_html[idx : idx + _HEADLINE_SEARCH_WINDOW]
    return _unescape(_HEADLINE_SPAN_RE.search(window))


def _parse_profile_header(page_html: str) -> dict[str, str | None]:
    name = _extract_name(page_html)
    headline = _extract_headline(page_html, name)
    # Fallback only -- confirmed live these tags don't exist once
    # authenticated, but harmless to still check.
    if not name:
        og_title = _unescape(_OG_TITLE_RE.search(page_html))
        name = _TRAILING_LINKEDIN_RE.sub("", og_title).strip() or None if og_title else None
    if not headline:
        headline = _unescape(_OG_DESCRIPTION_RE.search(page_html))
    photo = _unescape(_OG_IMAGE_RE.search(page_html))
    return {"name": name, "headline": headline, "photo": photo, "location": None}


class VoyagerClient:
    def __init__(self, cookies: dict[str, str]):
        csrf_token = cookies["JSESSIONID"].strip('"')
        self._cookies = cookies
        self._csrf_token = csrf_token
        self._client = httpx.AsyncClient(
            cookies=cookies,
            headers={"User-Agent": USER_AGENT, "csrf-token": csrf_token},
            timeout=settings.request_timeout_seconds,
            follow_redirects=True,
        )

    async def _fetch_component(self, component_key: str, public_id: str) -> tuple[dict, dict]:
        component_id = COMPONENT_IDS[component_key]
        params = {"componentId": component_id, "sduiid": component_id}
        body = {
            "clientArguments": {
                "payload": {"isSelfView": False, "vanityName": public_id},
                "states": [],
                "requestMetadata": {"$type": "proto.sdui.common.RequestMetadata"},
                "screenId": "com.linkedin.sdui.flagshipnav.home.Home",
                "knownTemplateIds": [],
            }
        }
        response = await self._client.post(
            COMPONENT_URL,
            params=params,
            json=body,
            headers={"content-type": "application/json"},
        )
        _raise_for_status(response, component_key)
        return parse_flight_response(response.text)

    async def get_profile_view(self, public_id: str) -> dict:
        """Fetches everything needed for one profile: the HTML page (for
        name/headline/location/photo) plus the about/experience/education/
        skills SDUI cards. Returns a raw dict for app/parser.py to turn
        into a ProfileResponse."""
        page_response = await self._client.get(PROFILE_PAGE_URL.format(public_id=public_id))
        _raise_for_status(page_response, "profile page")
        meta = _parse_profile_header(page_response.text)

        about_chunks, about_aliases = await self._fetch_component("about", public_id)
        experience_chunks, experience_aliases = await self._fetch_component("experience", public_id)
        education_chunks, education_aliases = await self._fetch_component("education", public_id)
        skills_chunks, skills_aliases = await self._fetch_component("skills", public_id)

        return {
            "meta": meta,
            "about": extract_about_text(about_chunks, about_aliases),
            "experience": extract_card_entries(experience_chunks, experience_aliases),
            "education": extract_card_entries(education_chunks, education_aliases),
            "skills": extract_skills(skills_chunks, skills_aliases),
        }

    async def aclose(self) -> None:
        await self._client.aclose()
