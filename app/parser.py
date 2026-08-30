"""
Turns a raw LinkedIn Voyager `profileView` response into our ProfileResponse.

An earlier version of this parser targeted a different response shape: a
flat `included` array of `$type`-tagged entities, requested by sending
`accept: application/vnd.linkedin.normalized+json+2.1`. Against a live
account that request now gets HTTP 410 Gone from LinkedIn -- not 404 --
which is Rest.li's way of saying that specific representation has been
retired, not that the endpoint or the profile is gone.

The endpoint itself (`/identity/profiles/<public_id>/profileView`) is
unchanged; dropping that `accept` override gets LinkedIn's current default
representation instead: one `profile` object plus separate `<section>View`
objects (`positionView`, `educationView`, `skillView`, `certificationView`,
`languageView`), each holding `{"elements": [...]}`. This shape (including
the exact field names below) is corroborated by open-linkedin-api
(https://github.com/EseToni/open-linkedin-api), a community fork of the
project this was originally modeled on -- that project's own upstream
(tomquirk/linkedin-api) has since gone private, presumably for the same
reason this parser needed rewriting. This still hasn't been verified
against a live response from this codebase's own test account; see the
README's "Known limitations".
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.exceptions import InvalidProfileURLError
from app.models import (
    Certification,
    Education,
    Experience,
    Language,
    ProfileImage,
    ProfileResponse,
)

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


def _elements(raw: dict, view_key: str) -> list[dict]:
    """e.g. raw["positionView"]["elements"], defaulting to [] if the section
    is absent (LinkedIn omits empty sections' views entirely for some profiles)."""
    return (raw.get(view_key) or {}).get("elements", [])


def _date_to_str(date_obj: dict | None) -> str | None:
    if not date_obj:
        return None
    year = date_obj.get("year")
    month = date_obj.get("month")
    if not year:
        return None
    return f"{year:04d}-{month:02d}" if month else f"{year:04d}"


def _get_period(entity: dict) -> tuple[dict | None, dict | None]:
    """Positions/education use `dateRange`; certifications often use `timePeriod`."""
    period = entity.get("dateRange") or entity.get("timePeriod") or {}
    start = period.get("start") or period.get("startDate")
    end = period.get("end") or period.get("endDate")
    return start, end


def _find_vector_image(node: Any) -> dict | None:
    """Recursively hunt for a Voyager VectorImage dict (identified by having
    an "artifacts" list) inside `node`. LinkedIn wraps this union-typed field
    under different key spellings in different places (miniProfile's cached
    thumbnail vs. a full profilePicture reference), so this checks each one
    rather than assuming a single fixed path."""
    if not isinstance(node, dict):
        return None
    if "artifacts" in node:
        return node
    for key in ("com.linkedin.common.VectorImage", "vectorImage", "displayImageReference"):
        if key in node:
            found = _find_vector_image(node[key])
            if found:
                return found
    return None


def _image_from_vector(vector: dict | None) -> ProfileImage | None:
    """Pick the highest-resolution artifact out of a Voyager VectorImage object."""
    if not vector:
        return None
    root_url = vector.get("rootUrl", "")
    artifacts = vector.get("artifacts", [])
    if not root_url or not artifacts:
        return None
    best = max(artifacts, key=lambda a: a.get("width", 0))
    segment = best.get("fileIdentifyingUrlPathSegment", "")
    if not segment:
        return None
    return ProfileImage(url=root_url + segment, width=best.get("width"), height=best.get("height"))


def _profile_image(node: Any) -> ProfileImage | None:
    return _image_from_vector(_find_vector_image(node))


def parse_profile(raw: dict[str, Any], public_id: str, profile_url: str) -> ProfileResponse:
    profile = raw.get("profile") or {}
    mini_profile = profile.get("miniProfile") or {}

    name = " ".join(p for p in [profile.get("firstName"), profile.get("lastName")] if p) or None

    # A full profilePicture reference (if present) is generally higher-res
    # than miniProfile's cached thumbnail, so prefer it when both exist.
    profile_picture = _profile_image(profile.get("profilePicture")) or _profile_image(
        mini_profile.get("picture")
    )
    background_image = _profile_image(profile.get("backgroundImage"))

    experience: list[Experience] = []
    for pos in _elements(raw, "positionView"):
        start, end = _get_period(pos)
        experience.append(
            Experience(
                title=pos.get("title"),
                company=pos.get("companyName"),
                company_urn=pos.get("companyUrn"),
                location=pos.get("locationName"),
                employment_type=pos.get("employmentType"),
                starts_at=_date_to_str(start),
                ends_at=_date_to_str(end),
                description=pos.get("description"),
            )
        )

    education: list[Education] = []
    for edu in _elements(raw, "educationView"):
        start, end = _get_period(edu)
        # schoolName is usually a denormalized top-level field, but fall back
        # to the nested school reference if a profile is missing it.
        school_name = edu.get("schoolName") or (edu.get("school") or {}).get("schoolName")
        education.append(
            Education(
                school=school_name,
                degree=edu.get("degreeName"),
                field_of_study=edu.get("fieldOfStudy"),
                starts_at=_date_to_str(start),
                ends_at=_date_to_str(end),
                description=edu.get("description"),
                activities=edu.get("activities"),
            )
        )

    skills = [s["name"] for s in _elements(raw, "skillView") if s.get("name")]

    certifications: list[Certification] = []
    for cert in _elements(raw, "certificationView"):
        start, end = _get_period(cert)
        certifications.append(
            Certification(
                name=cert.get("name"),
                authority=cert.get("authority"),
                starts_at=_date_to_str(start),
                ends_at=_date_to_str(end),
                license_number=cert.get("licenseNumber"),
                url=cert.get("url"),
            )
        )

    languages = [
        Language(name=lang.get("name"), proficiency=lang.get("proficiency"))
        for lang in _elements(raw, "languageView")
    ]

    return ProfileResponse(
        public_id=public_id,
        profile_url=profile_url,
        name=name,
        headline=profile.get("headline"),
        location=profile.get("geoLocationName") or profile.get("locationName"),
        about=profile.get("summary"),
        profile_picture=profile_picture,
        background_image=background_image,
        experience=experience,
        education=education,
        skills=skills,
        certifications=certifications,
        languages=languages,
    )
