"""
Turns the raw dict VoyagerClient.get_profile_view() returns into our
ProfileResponse.

That raw dict combines two very different sources (see voyager_client.py):
- `meta`: {name, headline, photo, location} already extracted from the
  profile's HTML page by voyager_client.py.
- `about`/`experience`/`education`/`skills`: text already extracted from
  LinkedIn's SDUI "Flight protocol" component responses by
  app/sdui_parser.py.

Certifications and languages are not implemented against this new source
yet -- see the README's "Known limitations". They're always empty lists
here, not silently-wrong guesses.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.exceptions import InvalidProfileURLError
from app.models import Education, Experience, ProfileImage, ProfileResponse

_PROFILE_PATH_RE = re.compile(r"^/in/([^/?#]+)/?$")


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

    image_url = meta.get("photo")
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
        # LinkedIn renders the school name as the entry's bold "title" and
        # the degree as its "subtitle" -- the reverse of Experience, where
        # title=role and subtitle=company. Confirmed against a real
        # response (a mismatched title/degree pairing surfaced this).
        education.append(
            Education(
                school=entry.get("title"),
                degree=entry.get("subtitle"),
                date_range=entry.get("dates"),
                description=entry.get("description"),
            )
        )

    return ProfileResponse(
        public_id=public_id,
        profile_url=profile_url,
        name=meta.get("name"),
        headline=meta.get("headline"),
        location=meta.get("location"),
        about=raw.get("about"),
        profile_picture=profile_picture,
        background_image=None,
        experience=experience,
        education=education,
        skills=raw.get("skills", []),
        certifications=[],
        languages=[],
    )
