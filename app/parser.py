"""
Turns a raw LinkedIn Voyager `profileView` response into our ProfileResponse.

LinkedIn's Voyager API returns a "normalized" document: a flat `included`
list of typed entities (each tagged with a `$type`, e.g.
`com.linkedin.voyager.identity.profile.Profile`), cross-referenced by URN
rather than nested. Rather than threading every reference field (fragile --
LinkedIn renames these often), we scan `included` for entities whose `$type`
ends in a known suffix and treat every match as one row of that section.
This is resilient to reference-shape changes but does not guarantee the same
ordering the profile page shows. See the README's "Known limitations".
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


def extract_public_id(url: str) -> str:
    """Pull the vanity public identifier out of a linkedin.com/in/<id> URL."""
    parsed = urlparse(url.strip())
    host = (parsed.netloc or "").lower()
    if not host.endswith("linkedin.com"):
        raise InvalidProfileURLError(f"Not a linkedin.com URL: {url!r}")
    match = _PROFILE_PATH_RE.match(parsed.path)
    if not match:
        raise InvalidProfileURLError(
            f"Expected a profile URL like https://www.linkedin.com/in/<id>/, got: {url!r}"
        )
    return match.group(1)


def _entities_by_type_suffix(included: list[dict], suffix: str) -> list[dict]:
    return [e for e in included if str(e.get("$type", "")).endswith(suffix)]


def _find_profile_entity(included: list[dict]) -> dict:
    matches = _entities_by_type_suffix(included, ".identity.profile.Profile")
    return matches[0] if matches else {}


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


def _extract_vector_image(obj: dict | None) -> dict | None:
    if not obj:
        return None
    if "artifacts" in obj:
        return obj
    if "vectorImage" in obj:
        return _extract_vector_image(obj["vectorImage"])
    if "displayImageReference" in obj:
        return _extract_vector_image(obj["displayImageReference"])
    return None


def _best_image(obj: dict | None) -> ProfileImage | None:
    """Pick the highest-resolution artifact out of a Voyager vectorImage object."""
    vector_image = _extract_vector_image(obj)
    if not vector_image:
        return None
    root_url = vector_image.get("rootUrl", "")
    artifacts = vector_image.get("artifacts", [])
    if not root_url or not artifacts:
        return None
    best = max(artifacts, key=lambda a: a.get("width", 0))
    segment = best.get("fileIdentifyingUrlPathSegment", "")
    if not segment:
        return None
    return ProfileImage(url=root_url + segment, width=best.get("width"), height=best.get("height"))


def parse_profile(raw: dict[str, Any], public_id: str, profile_url: str) -> ProfileResponse:
    included: list[dict] = raw.get("included", [])
    profile_entity = _find_profile_entity(included)

    name = " ".join(
        p for p in [profile_entity.get("firstName"), profile_entity.get("lastName")] if p
    ) or None

    experience: list[Experience] = []
    for pos in _entities_by_type_suffix(included, ".identity.profile.Position"):
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
    for edu in _entities_by_type_suffix(included, ".identity.profile.Education"):
        start, end = _get_period(edu)
        education.append(
            Education(
                school=edu.get("schoolName"),
                degree=edu.get("degreeName"),
                field_of_study=edu.get("fieldOfStudy"),
                starts_at=_date_to_str(start),
                ends_at=_date_to_str(end),
                description=edu.get("description"),
                activities=edu.get("activities"),
            )
        )

    skills = [
        s["name"]
        for s in _entities_by_type_suffix(included, ".identity.profile.Skill")
        if s.get("name")
    ]

    certifications: list[Certification] = []
    for cert in _entities_by_type_suffix(included, ".identity.profile.Certification"):
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
        for lang in _entities_by_type_suffix(included, ".identity.profile.Language")
    ]

    return ProfileResponse(
        public_id=public_id,
        profile_url=profile_url,
        name=name,
        headline=profile_entity.get("headline"),
        location=profile_entity.get("geoLocationName") or profile_entity.get("locationName"),
        about=profile_entity.get("summary"),
        profile_picture=_best_image(profile_entity.get("profilePicture")),
        background_image=_best_image(profile_entity.get("backgroundImage")),
        experience=experience,
        education=education,
        skills=skills,
        certifications=certifications,
        languages=languages,
    )
