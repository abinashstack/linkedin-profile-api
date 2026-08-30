"""
Parses LinkedIn's current profile-card responses: a React Server
Components ("Flight" protocol) wire format, not a plain JSON data model.

Format: newline-separated `<hex-id>:<payload>` lines. A payload starting
with `I[` declares a client-module reference: `<id>:I["<hash>",[],"<Export>"]`
says "id <id> is an alias for the module with this content hash". Later,
element tuples reference component types as `"$L<id>"` -- e.g. `["$",
"$L20", null, {...}]` means "render the component alias 20 points to,
with these props". Crucially, **that alias number is assigned per response,
not globally** -- the exact same generic "Text" component was seen aliased
"$L20" in one captured response and "$Ld" in a different one, both
resolving to the identical module hash (`85b20fca...`). Matching on the
literal alias string (an earlier version of this module did exactly that)
works for the one response it was built against and silently breaks on
every other one. This version resolves every element to its module hash
first and matches on that instead.

Real content (job titles, company names, skill names, dates, descriptions)
lives inside element tuples as plain rendered text, not as named data
fields -- there is no `companyName`, `startDate`, or `skillName` key
anywhere in this response. This module recovers that content by
recognizing a handful of recurring component "shapes" (a `<p>` with a
specific className = a title or subtitle; the generic Text component in a
bold/medium style = a skill name, the same component in a normal/small
style = supporting detail; the generic Text component with an
`expansionKey` = an expandable description) rather than by resolving the
full component tree, which would require reimplementing React's renderer.

This was reverse-engineered from real captured responses for three
components (`aboutTopLevelSection`, `experienceTopLevelSection`,
`skillsTopLevelSection`) -- `educationTopLevelSection` is assumed to follow
the `experienceTopLevelSection` shape (same design system, same naming
convention, same team) but has not been independently confirmed. The
className tokens in particular (`c2d1c236` for a title, `_61558a10` for a
subtitle) are CSS-in-JS generated hashes tied to LinkedIn's current
frontend build and WILL change on their next rebuild, at which point this
parser will need updating the same way voyager_client.py's old REST
integration did. See the README's "Known limitations".
"""
from __future__ import annotations

import json
import re
from typing import Any

_LINE_RE = re.compile(r"^([0-9a-fA-F]+):(.*)$")
_MODULE_REF_RE = re.compile(r'^I\["([0-9a-fA-F]+)"')

_TITLE_CLASS_MARKER = "c2d1c236"
_SUBTITLE_CLASS_MARKER = "_61558a10"

# Stable module hashes for two generic design-system components, resolved
# per-response from that response's own module-declaration lines rather
# than a hardcoded alias number (see module docstring for why).
_TEXT_COMPONENT_HASH = "85b20fca39223dffe536dd03122e5f56"
_EXPANDABLE_TEXT_HASH = "1e9b95c01e7f142c1ba9a289f4714a9c"


def parse_flight_response(text: str) -> tuple[dict[str, Any], dict[str, str]]:
    """Splits a Flight-protocol response into (chunks, alias_to_hash).

    `chunks` is {chunk_id: parsed_json_value} for every data-bearing line
    (module-reference lines are excluded -- they carry no content, only
    the alias mapping). `alias_to_hash` is {alias_id: module_hash} built
    from those module-reference lines, for resolving a `"$L<id>"` element
    type to the actual component it refers to."""
    chunks: dict[str, Any] = {}
    alias_to_hash: dict[str, str] = {}
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        match = _LINE_RE.match(line)
        if not match:
            continue
        chunk_id, payload = match.group(1), match.group(2)
        module_match = _MODULE_REF_RE.match(payload)
        if module_match:
            alias_to_hash[chunk_id] = module_match.group(1)
            continue
        if payload.startswith('"$S'):
            continue
        try:
            chunks[chunk_id] = json.loads(payload)
        except json.JSONDecodeError:
            continue
    return chunks, alias_to_hash


def _resolve(element_type: Any, alias_to_hash: dict[str, str]) -> Any:
    """A "$L<id>" element type is an alias; resolve it to the module hash
    it points to (falling back to the alias itself if undeclared) so
    matching doesn't depend on which number a given response happened to
    assign it."""
    if isinstance(element_type, str) and element_type.startswith("$L"):
        return alias_to_hash.get(element_type[2:], element_type)
    return element_type


def _single_text_child(children: Any) -> str | None:
    if isinstance(children, list) and len(children) == 1 and isinstance(children[0], str):
        return children[0]
    return None


def _classify(value: Any, alias_to_hash: dict[str, str]) -> tuple[str, str] | None:
    """Returns (kind, text) for a chunk recognized as title/subtitle/
    smalltext/description, or None for anything else (layout wrappers,
    buttons, images, tracking metadata, ...)."""
    if not (isinstance(value, list) and len(value) == 4 and value[0] == "$"):
        return None
    element_type, props = value[1], value[3]
    if not isinstance(props, dict):
        return None
    resolved = _resolve(element_type, alias_to_hash)

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

    if resolved == _TEXT_COMPONENT_HASH:
        text_props = props.get("textProps", {})
        text = _single_text_child(text_props.get("children"))
        if text is None:
            return None
        # A description carries an expansionKey (the "show more" toggle);
        # dates/location don't. Same component either way -- see module docstring.
        if "expansionKey" in props:
            return ("description", text)
        return ("smalltext", text)

    if resolved == _EXPANDABLE_TEXT_HASH:
        text = _single_text_child(props.get("textProps", {}).get("children"))
        return ("description", text) if text is not None else None

    return None


def extract_card_entries(
    chunks: dict[str, Any], alias_to_hash: dict[str, str]
) -> list[dict[str, str | None]]:
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
        result = _classify(value, alias_to_hash)
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


def extract_about_text(chunks: dict[str, Any], alias_to_hash: dict[str, str]) -> str | None:
    """The About card is just a single expandable description block, no
    title/subtitle -- returns its text, or None if the profile has no About
    section filled in (a real, common case, not a parsing failure)."""
    for _, value in sorted(chunks.items(), key=lambda item: int(item[0], 16)):
        result = _classify(value, alias_to_hash)
        if result and result[0] == "description":
            return result[1]
    return None


def extract_skills(chunks: dict[str, Any], alias_to_hash: dict[str, str]) -> list[str]:
    """Skills has a different layout than Experience/Education -- no
    title/subtitle <p> pair, just the generic Text component reused at two
    font weights: bold+medium for the skill name itself, normal+small for
    supporting detail (where it was used, endorsement counts). fontWeight
    is what distinguishes a skill name here, since (unlike Experience)
    there's no distinct className for it.

    Only returns skills LinkedIn already expanded into this response --
    profiles with more skills than the default view shows have a "Show all"
    button that navigates to a separate screen this doesn't follow."""
    skills: list[str] = []
    for value in chunks.values():
        if not (isinstance(value, list) and len(value) == 4 and value[0] == "$"):
            continue
        element_type, props = value[1], value[3]
        if not isinstance(props, dict):
            continue
        if _resolve(element_type, alias_to_hash) != _TEXT_COMPONENT_HASH:
            continue
        text_props = props.get("textProps", {})
        if text_props.get("fontWeight") != "bold":
            continue
        text = _single_text_child(text_props.get("children"))
        if text:
            skills.append(text)
    return skills
