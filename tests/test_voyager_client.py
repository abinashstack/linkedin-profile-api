"""Tests for the HTML-page extraction in app/voyager_client.py, against
synthetic HTML modeled on the real structure found on two live profiles
(not the real HTML itself)."""
from app.voyager_client import _extract_headline, _extract_name, _parse_profile_header

SAMPLE_HTML = """
<html><head><title>Jane Doe | LinkedIn</title></head>
<body>
<figure aria-hidden="true"></figure>
<div class="aa13b50b"><p class="_02484ad3 _61558a10">Jane Doe</p><div class="_1736033f">
<p class="_02484ad3 _4e33f71b"><span>Senior Software Engineer at Example Corp</span></p>
</div></div>
</body></html>
"""


def test_extract_name_strips_trailing_linkedin_suffix():
    assert _extract_name(SAMPLE_HTML) == "Jane Doe"


def test_extract_name_returns_none_without_title_tag():
    assert _extract_name("<html><body>no title here</body></html>") is None


def test_extract_headline_finds_span_after_name_paragraph():
    assert _extract_headline(SAMPLE_HTML, "Jane Doe") == "Senior Software Engineer at Example Corp"


def test_extract_headline_returns_none_without_a_name():
    assert _extract_headline(SAMPLE_HTML, None) is None


def test_extract_headline_returns_none_when_name_not_found_in_html():
    assert _extract_headline(SAMPLE_HTML, "Someone Else") is None


def test_parse_profile_header_combines_both():
    header = _parse_profile_header(SAMPLE_HTML)
    assert header["name"] == "Jane Doe"
    assert header["headline"] == "Senior Software Engineer at Example Corp"
    assert header["location"] is None  # not resolved yet -- see README
    assert header["photo"] is None  # no og:image in this sample, no fallback available


def test_parse_profile_header_falls_back_to_og_tags_when_title_absent():
    html = (
        '<html><head>'
        '<meta property="og:title" content="Jane Doe | LinkedIn" />'
        '<meta property="og:description" content="Senior Software Engineer" />'
        '<meta property="og:image" content="https://example.com/photo.jpg" />'
        '</head><body>no matching name paragraph here</body></html>'
    )
    header = _parse_profile_header(html)
    assert header["name"] == "Jane Doe"  # fallback also strips the " | LinkedIn" suffix
    assert header["headline"] == "Senior Software Engineer"
    assert header["photo"] == "https://example.com/photo.jpg"
