"""
Turns the raw dict VoyagerClient.get_profile_view() returns into our
ProfileResponse.

That raw dict combines two very different sources (see voyager_client.py):
- `meta`: standard `<title>` / Open Graph tags from the profile's HTML page,
  for name/headline/location/photo.
- `about`/`experience`/`education`: text already extracted from LinkedIn's
  SDUI "Flight protocol" component responses by app/sdui_parser.py.

Skills, certifications, and languages are not implemented against this new
source yet -- see the README's "Known limitations". They're always empty
lists here, not silently-wrong guesses.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.exceptions import InvalidProfileURLError
from app.models import Education, Experience, ProfileImage, ProfileResponse

_PROFILE_PATH_RE = re.compile(r"^/in/([^/?#]+)/?$")
_TRAILING_LINKEDIN_RE = re.compile(r"\s*\|\s*LinkedIn\s*$", re.IGNORECASE)


def extract_public_id(value: str) -> str:
    """Pull the vanity public identifier out of either a linkedin.com/in/<id>
    URL or a bare handle (e.g. "satyanadella")."""
    value = value.strip()

    if not re.match(r"^https?://", value, re.IGNORECASE):
        handle = value.strip("/ ").removeprefix("in/").lstrip("@")
        if not handle or "/" in handle or " " in handle:
            raise InvalidProfileURLError(
                f"'{value}' doesn't look like a LinkedIn profile URL or handle."
            )
        return handle

    parsed = urlparse(value)
    host = (parsed.netloc or "").lower()
    if not host.endswith("linkedin.com"):
        raise InvalidProfileURLError(f"Not a linkedin.com URL: {value!r}")
    match = _PROFILE_PATH_RE.match(parsed.path)
    if not match:
        raise InvalidProfileURLError(
            f"Expected a profile URL like https://www.linkedin.com/in/<id>/, got: {value!r}"
        )
    return match.group(1)


def _split_name_and_headline(og_title: str | None, title: str | None) -> tuple[str | None, str | None]:
    """LinkedIn's og:title (or <title>) is conventionally "<Name> - <Headline>
    | LinkedIn". Best-effort: not re-verified against a live capture in this
    project's own testing -- see the README's "Known limitations"."""
    raw = og_title or title
    if not raw:
        return None, None
    raw = _TRAILING_LINKEDIN_RE.sub("", raw.strip())
    if " - " in raw:
        name, headline = raw.split(" - ", 1)
        return name.strip() or None, headline.strip() or None
    return raw or None, None


def _extract_location(og_description: str | None) -> str | None:
    """Best-effort: LinkedIn's og:description conventionally leads with the
    location before a separator like "·". Not re-verified this session."""
    if not og_description:
        return None
    first_segment = re.split(r"[·|]", og_description, maxsplit=1)[0].strip()
    return first_segment or None


def _split_subtitle(subtitle: str | None) -> tuple[str | None, str | None]:
    """Experience subtitles look like "Company Name · Full-time"; the
    employment type suffix is optional."""
    if not subtitle:
        return None, None
    if " · " in subtitle:
        primary, suffix = subtitle.split(" · ", 1)
        return primary.strip() or None, suffix.strip() or None
    return subtitle.strip() or None, None


def parse_profile(raw: dict[str, Any], public_id: str, profile_url: str) -> ProfileResponse:
    meta = raw.get("meta") or {}
    name, headline = _split_name_and_headline(meta.get("og_title"), meta.get("title"))
    location = _extract_location(meta.get("og_description"))

    image_url = meta.get("og_image")
    profile_picture = ProfileImage(url=image_url) if image_url else None

    experience = []
    for entry in raw.get("experience", []):
        company, employment_type = _split_subtitle(entry.get("subtitle"))
        experience.append(
            Experience(
                title=entry.get("title"),
                company=company,
                employment_type=employment_type,
                location=entry.get("location"),
                date_range=entry.get("dates"),
                description=entry.get("description"),
            )
        )

    education = []
    for entry in raw.get("education", []):
        education.append(
            Education(
                degree=entry.get("title"),
                school=entry.get("subtitle"),
                date_range=entry.get("dates"),
                description=entry.get("description"),
            )
        )

    return ProfileResponse(
        public_id=public_id,
        profile_url=profile_url,
        name=name,
        headline=headline,
        location=location,
        about=raw.get("about"),
        profile_picture=profile_picture,
        background_image=None,
        experience=experience,
        education=education,
        skills=[],
        certifications=[],
        languages=[],
    )
