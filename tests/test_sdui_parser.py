"""Tests for app/sdui_parser.py, against synthetic fixtures modeled on real
captured responses' structure (not real personal data -- see the README's
"Approach" section for how the real shape was reverse-engineered)."""
from pathlib import Path

from app.sdui_parser import (
    extract_about_text,
    extract_card_entries,
    extract_skills,
    parse_flight_response,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_parse_flight_response_separates_module_refs_from_data():
    text = '1:I["abc123",[],"default"]\n9:["$","p",null,{"children":["hi"]}]\n'
    chunks, alias_to_hash = parse_flight_response(text)
    assert "1" not in chunks  # module ref, no data
    assert alias_to_hash["1"] == "abc123"
    assert chunks["9"] == ["$", "p", None, {"children": ["hi"]}]


def test_extract_card_entries_from_real_shape():
    text = (FIXTURES / "sdui_experience_response.txt").read_text(encoding="utf-8")
    chunks, alias_to_hash = parse_flight_response(text)
    entries = extract_card_entries(chunks, alias_to_hash)

    assert len(entries) == 3
    assert entries[0]["title"] == "Senior Software Engineer"
    assert entries[0]["subtitle"] == "Example Corp · Full-time"
    assert entries[0]["dates"] == "2021 - Present"
    assert entries[0]["location"] == "Remote"
    assert entries[0]["description"] == "Led backend team and shipped the flagship product."

    assert entries[1]["title"] == "Software Engineer"
    assert entries[2]["title"] == "Security Consultant"


def test_extract_card_entries_description_matching_is_positional_not_semantic():
    """Documents the known limitation in sdui_parser.extract_card_entries:
    descriptions are matched to entries by position (1st to 1st, 2nd to
    2nd, ...), not by any id that actually ties a description to its
    entry. This fixture happens to have one description per entry, so the
    positional match is correct here -- but it would silently misattribute
    a description if a *middle* entry had none. That's a deliberate,
    documented tradeoff (see the module docstring), not something this
    test claims to fix."""
    text = (FIXTURES / "sdui_experience_response.txt").read_text(encoding="utf-8")
    chunks, alias_to_hash = parse_flight_response(text)
    entries = extract_card_entries(chunks, alias_to_hash)
    assert all(e["description"] for e in entries)


def test_extract_about_text():
    text = (FIXTURES / "sdui_about_response.txt").read_text(encoding="utf-8")
    chunks, alias_to_hash = parse_flight_response(text)
    assert extract_about_text(chunks, alias_to_hash) == "I help teams ship reliable backend systems."


def test_extract_about_text_returns_none_when_absent():
    # A profile with no About section filled in -- a real, common case,
    # not a parsing failure.
    assert extract_about_text({}, {}) is None


def test_extract_skills_uses_bold_weight_not_a_className():
    text = (FIXTURES / "sdui_skills_response.txt").read_text(encoding="utf-8")
    chunks, alias_to_hash = parse_flight_response(text)
    skills = extract_skills(chunks, alias_to_hash)
    assert skills == ["Backend Development", "Distributed Systems"]
    # Supporting detail (endorsements, "used at") must not leak in as a skill.
    assert "Used at Example Corp" not in skills
    assert "Endorsed by 3 people" not in skills


def test_alias_numbers_are_per_response_not_global():
    """The whole point of resolving by module hash instead of the literal
    "$L<id>" string: the fixture uses alias "d" for the Text component,
    while the experience fixture uses alias "20" for the exact same
    component (same module hash) -- both must resolve correctly."""
    exp_chunks, exp_aliases = parse_flight_response(
        (FIXTURES / "sdui_experience_response.txt").read_text(encoding="utf-8")
    )
    skill_chunks, skill_aliases = parse_flight_response(
        (FIXTURES / "sdui_skills_response.txt").read_text(encoding="utf-8")
    )
    assert exp_aliases["20"] == skill_aliases["d"]  # same module hash, different alias
    assert extract_card_entries(exp_chunks, exp_aliases)[0]["dates"] == "2021 - Present"
    assert extract_skills(skill_chunks, skill_aliases) == ["Backend Development", "Distributed Systems"]
