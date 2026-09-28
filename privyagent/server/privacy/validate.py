"""False-positive filtering.

The prototype's own notes record the failure this addresses: raw NER predictions
over a whole webpage produce tiny bogus entities inside CSS, script bodies, class
names and encoding strings. Those are not PII and redacting them corrupts the
page we hand to the agent.

Filtering applies to `source == "ner"` only. A deterministic rule that validated
a Luhn checksum, or a structural hit on `value=` of a password field, is not
second-guessed here — an API key inside a <script> block is exactly the thing we
most need to catch.
"""

from __future__ import annotations

import re

from .entities import Entity

SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]{0,4000}>", re.DOTALL)
ATTR_VALUE_RE = re.compile(r"""=\s*(?:"([^"]*)"|'([^']*)')""")

# Technical vocabulary that the model sometimes labels as names or usernames.
TECHNICAL_TERMS = {
    "utf-8", "utf8", "iso-8859-1", "arial", "helvetica", "sans-serif", "serif",
    "monospace", "roboto", "verdana", "tahoma", "georgia", "inherit", "initial",
    "html", "body", "div", "span", "form", "input", "button", "submit", "reset",
    "header", "footer", "nav", "main", "section", "article", "img", "svg",
    "http", "https", "www", "com", "org", "net", "javascript", "json", "xml",
    "css", "js", "api", "url", "uri", "src", "href", "class", "style", "type",
    "true", "false", "null", "none", "undefined", "function", "return", "const",
    "var", "let", "async", "await", "px", "rem", "em", "rgba", "rgb", "hex",
    "google", "facebook", "amazon", "chrome", "mozilla", "webkit", "react",
    "search", "login", "signin", "sign in", "menu", "home", "cart", "checkout",
    "add to cart", "buy now", "sponsored", "advertisement",
}

MIN_NER_LENGTH = 3


def _suppressed_regions(html: str) -> list[tuple[int, int]]:
    """Regions where a bare NER prediction is presumed to be markup noise.

    Script and style bodies, plus everything inside a tag's angle brackets
    except the quoted attribute values (a quoted value can legitimately hold a
    person's name, so it stays inspectable).
    """
    regions: list[tuple[int, int]] = []

    for match in SCRIPT_STYLE_RE.finditer(html):
        regions.append((match.start(2), match.end(2)))

    for match in TAG_RE.finditer(html):
        tag_text = match.group()
        cursor = match.start()
        allowed: list[tuple[int, int]] = []
        for value_match in ATTR_VALUE_RE.finditer(tag_text):
            group_index = 1 if value_match.group(1) is not None else 2
            allowed.append((cursor + value_match.start(group_index), cursor + value_match.end(group_index)))

        position = match.start()
        for value_start, value_end in allowed:
            if value_start > position:
                regions.append((position, value_start))
            position = value_end
        if position < match.end():
            regions.append((position, match.end()))

    return regions


def _overlaps(entity: Entity, regions: list[tuple[int, int]]) -> bool:
    return any(entity.start < end and start < entity.end for start, end in regions)


def filter_entities(entities: list[Entity], source_text: str, is_html: bool) -> list[Entity]:
    regions = _suppressed_regions(source_text) if is_html else []
    kept: list[Entity] = []

    for entity in entities:
        if entity.source != "ner":
            kept.append(entity)
            continue

        value = source_text[entity.start:entity.end].strip()

        if len(value) < MIN_NER_LENGTH:
            continue
        if not re.search(r"[A-Za-z0-9]", value):
            continue
        if value.lower() in TECHNICAL_TERMS:
            continue
        # A fragment that is mostly punctuation or markup leftovers.
        if len(re.sub(r"[^A-Za-z0-9]", "", value)) < MIN_NER_LENGTH:
            continue
        if _overlaps(entity, regions):
            continue

        kept.append(entity)

    return kept
