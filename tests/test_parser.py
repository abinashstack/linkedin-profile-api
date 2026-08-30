import json
from pathlib import Path

import pytest

from app.exceptions import InvalidProfileURLError
from app.parser import extract_public_id, parse_profile

FIXTURE = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "sample_profile_response.json").read_text()
)
PROFILE_URL = "https://www.linkedin.com/in/jane-doe/"


def _parse():
    return parse_profile(FIXTURE, public_id="jane-doe", profile_url=PROFILE_URL)


def test_extract_public_id_valid():
    assert extract_public_id("https://www.linkedin.com/in/jane-doe/") == "jane-doe"
    assert extract_public_id("https://linkedin.com/in/jane-doe") == "jane-doe"


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
    assert profile.profile_picture.url.endswith("400_400.jpg")


def test_parse_profile_experience():
    profile = _parse()
    assert len(profile.experience) == 2
    current = next(e for e in profile.experience if e.ends_at is None)
    assert current.title == "Senior Software Engineer"
    assert current.starts_at == "2021-03"
    past = next(e for e in profile.experience if e.ends_at is not None)
    assert past.ends_at == "2021-02"


def test_parse_profile_education_skills_certs_languages():
    profile = _parse()
    assert profile.education[0].school == "State University"
    assert profile.education[0].starts_at == "2014"
    assert "Python" in profile.skills
    assert profile.certifications[0].name == "AWS Certified Solutions Architect"
    assert profile.certifications[0].starts_at == "2022-05"
    assert profile.languages[0].proficiency == "NATIVE_OR_BILINGUAL"
