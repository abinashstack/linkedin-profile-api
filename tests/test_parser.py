import json
from pathlib import Path

import pytest

from app.exceptions import InvalidProfileURLError
from app.parser import extract_public_id, parse_profile

FIXTURE = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "sample_profile_response.json").read_text(encoding="utf-8")
)
PROFILE_URL = "https://www.linkedin.com/in/jane-doe/"


def _parse():
    return parse_profile(FIXTURE, public_id="jane-doe", profile_url=PROFILE_URL)


def test_extract_public_id_valid():
    assert extract_public_id("https://www.linkedin.com/in/jane-doe/") == "jane-doe"
    assert extract_public_id("https://linkedin.com/in/jane-doe") == "jane-doe"


def test_extract_public_id_bare_handle():
    assert extract_public_id("jane-doe") == "jane-doe"
    assert extract_public_id("  jane-doe  ") == "jane-doe"
    assert extract_public_id("@jane-doe") == "jane-doe"


def test_extract_public_id_rejects_malformed_handle():
    with pytest.raises(InvalidProfileURLError):
        extract_public_id("jane doe")
    with pytest.raises(InvalidProfileURLError):
        extract_public_id("")


def test_extract_public_id_rejects_non_linkedin():
    with pytest.raises(InvalidProfileURLError):
        extract_public_id("https://example.com/in/jane-doe")


def test_extract_public_id_rejects_non_profile_path():
    with pytest.raises(InvalidProfileURLError):
        extract_public_id("https://www.linkedin.com/company/example/")


def test_parse_profile_basic_fields():
    profile = _parse()
    assert profile.name == "Jane Doe"
    assert profile.headline == "Senior Software Engineer at Example Corp"
    assert profile.location == "San Francisco, California, United States"
    assert profile.about == "Backend engineer focused on distributed systems."
    assert profile.profile_picture is not None
    assert profile.profile_picture.url == "https://media.licdn.com/dms/image/abc/400_400.jpg"


def test_parse_profile_experience():
    profile = _parse()
    assert len(profile.experience) == 2
    current = profile.experience[0]
    assert current.title == "Senior Software Engineer"
    assert current.company == "Example Corp"
    assert current.employment_type == "Full-time"
    assert current.date_range == "2021 - Present"
    assert current.location == "San Francisco, CA"
    assert current.description == "Working on backend systems."


def test_parse_profile_education():
    profile = _parse()
    assert len(profile.education) == 1
    assert profile.education[0].degree == "B.S., Computer Science"
    assert profile.education[0].school == "State University"
    assert profile.education[0].date_range == "2014 - 2018"


def test_parse_profile_skills_certs_languages_are_empty():
    # Not implemented against the current LinkedIn source yet -- always
    # empty, not a silently-wrong guess. See README "Known limitations".
    profile = _parse()
    assert profile.skills == []
    assert profile.certifications == []
    assert profile.languages == []


def test_parse_profile_missing_og_image_gives_no_photo():
    fixture = json.loads(json.dumps(FIXTURE))
    fixture["meta"]["og_image"] = None
    profile = parse_profile(fixture, public_id="jane-doe", profile_url=PROFILE_URL)
    assert profile.profile_picture is None


def test_parse_profile_title_without_dash_has_no_headline():
    fixture = json.loads(json.dumps(FIXTURE))
    fixture["meta"]["og_title"] = "Jane Doe | LinkedIn"
    profile = parse_profile(fixture, public_id="jane-doe", profile_url=PROFILE_URL)
    assert profile.name == "Jane Doe"
    assert profile.headline is None
