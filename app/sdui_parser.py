"""
Parses LinkedIn's current profile-card responses: a React Server
Components ("Flight" protocol) wire format, not a plain JSON data model.

Format: newline-separated `<hex-id>:<payload>` lines. A payload starting
with `I[` is a client-module reference (declaring which component type a
later `"$L<id>"` element name refers to) and carries no data; everything
else is a JSON value, most commonly a React-element tuple
`["$", type, key, props]`.

Real content (job titles, company names, dates, descriptions) lives inside
these element tuples as plain rendered text, not as named data fields --
there is no `companyName` or `startDate` key anywhere in this response.
This module recovers that content by recognizing a handful of
design-system component "shapes" (a `<p>` with a specific className = a
title or subtitle; a `$L20`-type element = a short text field; a
`$L46`-type element with an `expansionKey` = an expandable description)
rather than by resolving the full component tree, which would require
reimplementing React's renderer.

This was reverse-engineered from real captured responses (aboutTopLevelSection
and experienceTopLevelSection) for one component, `experienceTopLevelSection` --
educationTopLevelSection is assumed to follow the identical shape (same
design system, same naming convention, same team) but has not been
independently confirmed. The className tokens in particular (`c2d1c236` for
a title, `_61558a10` for a subtitle) are CSS-in-JS generated hashes that
WILL change on LinkedIn's next frontend rebuild, at which point this parser
will need updating the same way voyager_client.py's old REST integration
did. See the README's "Known limitations".
"""
from __future__ import annotations

import json
import re
from typing import Any

_LINE_RE = re.compile(r"^([0-9a-fA-F]+):(.*)$")

_TITLE_CLASS_MARKER = "c2d1c236"
_SUBTITLE_CLASS_MARKER = "_61558a10"


def parse_flight_chunks(text: str) -> dict[str, Any]:
    """Splits a Flight-protocol response into {chunk_id: parsed_json_value},
    skipping client-module-reference lines (which carry no data) and any
    line that doesn't parse as JSON (some chunks -- notably the root tree --
    aren't needed for text extraction and aren't worth failing the whole
    response over)."""
    chunks: dict[str, Any] = {}
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        match = _LINE_RE.match(line)
        if not match:
            continue
        chunk_id, payload = match.group(1), match.group(2)
        if payload.startswith("I[") or payload.startswith('"$S'):
            continue
        try:
            chunks[chunk_id] = json.loads(payload)
        except json.JSONDecodeError:
            continue
    return chunks


def _single_text_child(children: Any) -> str | None:
    if isinstance(children, list) and len(children) == 1 and isinstance(children[0], str):
        return children[0]
    return None


def _classify(value: Any) -> tuple[str, str] | None:
    """Returns (kind, text) for a chunk recognized as title/subtitle/
    smalltext/description, or None for anything else (layout wrappers,
    buttons, images, tracking metadata, ...)."""
    if not (isinstance(value, list) and len(value) == 4 and value[0] == "$"):
        return None
    element_type, props = value[1], value[3]
    if not isinstance(props, dict):
        return None

    if element_type == "p":
        text = _single_text_child(props.get("children"))
        if text is None:
            return None
        class_name = props.get("className", "")
        if _TITLE_CLASS_MARKER in class_name:
            return ("title", text)
        if _SUBTITLE_CLASS_MARKER in class_name:
            return ("subtitle", text)
        return None

    if element_type == "$L20":
        text = _single_text_child(props.get("textProps", {}).get("children"))
        return ("smalltext", text) if text is not None else None

    if element_type == "$L46":
        text = _single_text_child(props.get("textProps", {}).get("children"))
        return ("description", text) if text is not None else None

    return None


def extract_card_entries(chunks: dict[str, Any]) -> list[dict[str, str | None]]:
    """Groups classified chunks (in numeric chunk-id order) into
    position/education-style entries: {title, subtitle, dates, location,
    description}.

    Titles and subtitles come in matched pairs, each immediately followed
    by zero to two "smalltext" chunks (dates, then location) in the same
    id run -- this grouping is reliable. Descriptions are a known weak
    spot: LinkedIn assigns them lower chunk ids than the entry they belong
    to, in the same relative order as the entries, but with no id in
    either the description or the entry that ties the two together
    directly. This maps them onto entries positionally (1st description to
    1st entry, 2nd to 2nd, ...), which is correct as long as every entry
    up to the last one that has a description also has one -- an entry in
    the middle of the list with no description will shift every
    description after it onto the wrong entry. Confirming this properly
    would require walking the full component tree instead of pattern-
    matching chunk shapes; this is a deliberate, documented tradeoff, not
    an oversight."""
    classified: list[tuple[int, str, str]] = []
    for chunk_id, value in chunks.items():
        result = _classify(value)
        if result:
            classified.append((int(chunk_id, 16), result[0], result[1]))
    classified.sort(key=lambda item: item[0])

    entries: list[dict[str, str | None]] = []
    descriptions: list[str] = []
    i = 0
    while i < len(classified):
        _, kind, text = classified[i]
        if kind == "title":
            entry: dict[str, str | None] = {
                "title": text,
                "subtitle": None,
                "dates": None,
                "location": None,
                "description": None,
            }
            i += 1
            if i < len(classified) and classified[i][1] == "subtitle":
                entry["subtitle"] = classified[i][2]
                i += 1
            if i < len(classified) and classified[i][1] == "smalltext":
                entry["dates"] = classified[i][2]
                i += 1
            if i < len(classified) and classified[i][1] == "smalltext":
                entry["location"] = classified[i][2]
                i += 1
            entries.append(entry)
        elif kind == "description":
            descriptions.append(text)
            i += 1
        else:
            i += 1

    for entry, description in zip(entries, descriptions):
        entry["description"] = description

    return entries


def extract_about_text(chunks: dict[str, Any]) -> str | None:
    """The About card is just a single expandable description block, no
    title/subtitle -- returns its text, or None if the profile has no About
    section filled in (a real, common case, not a parsing failure)."""
    for _, value in sorted(chunks.items(), key=lambda item: int(item[0], 16)):
        result = _classify(value)
        if result and result[0] == "description":
            return result[1]
    return None
