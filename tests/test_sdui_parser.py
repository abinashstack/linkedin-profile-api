"""Tests for app/sdui_parser.py, against synthetic fixtures modeled on a
real captured response's structure (not real personal data -- see the
README's "Approach" section for how the real shape was reverse-engineered)."""
from pathlib import Path

from app.sdui_parser import extract_about_text, extract_card_entries, parse_flight_chunks

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_parse_flight_chunks_skips_module_refs_and_keeps_data():
    text = '1:I["abc123",[],"default"]\n9:["$","p",null,{"children":["hi"]}]\n'
    chunks = parse_flight_chunks(text)
    assert "1" not in chunks  # module ref, no data
    assert chunks["9"] == ["$", "p", None, {"children": ["hi"]}]


def test_extract_card_entries_from_real_shape():
    text = (FIXTURES / "sdui_experience_response.txt").read_text(encoding="utf-8")
    chunks = parse_flight_chunks(text)
    entries = extract_card_entries(chunks)

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
    entries = extract_card_entries(parse_flight_chunks(text))
    assert all(e["description"] for e in entries)


def test_extract_about_text():
    text = (FIXTURES / "sdui_about_response.txt").read_text(encoding="utf-8")
    chunks = parse_flight_chunks(text)
    assert extract_about_text(chunks) == "I help teams ship reliable backend systems."


def test_extract_about_text_returns_none_when_absent():
    # A profile with no About section filled in -- a real, common case,
    # not a parsing failure.
    assert extract_about_text({}) is None
