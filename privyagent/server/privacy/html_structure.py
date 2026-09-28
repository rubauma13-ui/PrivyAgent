"""HTML- and code-structure-aware detection.

This is the strongest detector in the firewall and the one the NER model cannot
replace. `value="BlueSky!42Secret"` looks like ordinary gibberish to a language
model, but the surrounding markup says `type="password"`, which settles it.

Everything works on the RAW string with regex rather than on a parse tree,
because the contract is that redaction splices exact character spans back into
the original document. A parser hands back a tree whose serialisation is not
byte-identical to the input (attribute order, quoting, whitespace, self-closing
style all drift), and re-serialising is exactly what this component must not do.

Only VALUES are ever returned as spans. `name="password"` stays `name="password"`.
"""

from __future__ import annotations

import re

from .entities import Entity

ANY_TAG_RE = re.compile(r"<[^>]{0,4000}>", re.DOTALL)
TAG_RE = re.compile(r"<(input|option|textarea|select)\b([^>]*)>", re.IGNORECASE)
ATTR_RE = re.compile(
    r"""([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>`=]+))"""
)
TEXTAREA_RE = re.compile(r"<textarea\b([^>]*)>(.*?)</textarea>", re.IGNORECASE | re.DOTALL)

# name/id/autocomplete keyword -> label. Checked as substrings, longest first,
# so "confirm_password" and "new-password" both land on PASSWORD.
KEYWORD_LABELS: tuple[tuple[str, str], ...] = (
    ("current-password", "PASSWORD"), ("new-password", "PASSWORD"),
    ("passwd", "PASSWORD"), ("password", "PASSWORD"), ("pwd", "PASSWORD"),
    ("username", "USERNAME"), ("user-name", "USERNAME"), ("user_name", "USERNAME"),
    ("userid", "USERNAME"), ("user_id", "USERNAME"), ("loginid", "USERNAME"),
    ("login", "USERNAME"), ("handle", "USERNAME"),
    ("email", "EMAIL"), ("e-mail", "EMAIL"), ("mail", "EMAIL"),
    ("mobile", "PHONE"), ("phone", "PHONE"), ("telephone", "PHONE"), ("tel", "PHONE"),
    ("aadhaar", "GOVERNMENT_ID"), ("aadhar", "GOVERNMENT_ID"), ("uidai", "GOVERNMENT_ID"),
    ("passport", "GOVERNMENT_ID"), ("ssn", "GOVERNMENT_ID"), ("pan-number", "GOVERNMENT_ID"),
    ("pannumber", "GOVERNMENT_ID"), ("nationalid", "GOVERNMENT_ID"),
    ("cc-number", "CREDIT_CARD"), ("cardnumber", "CREDIT_CARD"),
    ("card-number", "CREDIT_CARD"), ("creditcard", "CREDIT_CARD"), ("cvv", "CREDIT_CARD"),
    ("ifsc", "BANK_ACCOUNT"), ("iban", "BANK_ACCOUNT"),
    ("accountnumber", "BANK_ACCOUNT"), ("account-number", "BANK_ACCOUNT"),
    ("bank-account", "BANK_ACCOUNT"),
    ("bday", "DATE_OF_BIRTH"), ("birthdate", "DATE_OF_BIRTH"),
    ("date-of-birth", "DATE_OF_BIRTH"), ("dateofbirth", "DATE_OF_BIRTH"),
    ("dob", "DATE_OF_BIRTH"),
    ("street-address", "ADDRESS"), ("address-line", "ADDRESS"),
    ("postal-code", "ADDRESS"), ("pincode", "ADDRESS"), ("zipcode", "ADDRESS"),
    ("address", "ADDRESS"),
    ("given-name", "PERSON_NAME"), ("family-name", "PERSON_NAME"),
    ("firstname", "PERSON_NAME"), ("lastname", "PERSON_NAME"),
    ("fullname", "PERSON_NAME"), ("full-name", "PERSON_NAME"),
    ("access_token", "SECRET"), ("accesstoken", "SECRET"), ("api_key", "SECRET"),
    ("apikey", "SECRET"), ("api-key", "SECRET"), ("csrf", "SECRET"),
    ("private_key", "SECRET"), ("authenticity_token", "SECRET"),
    ("session", "SECRET"), ("secret", "SECRET"), ("token", "SECRET"),
)

TYPE_LABELS = {
    "password": "PASSWORD",
    "email": "EMAIL",
    "tel": "PHONE",
}

# Keys that make a JS/JSON/source assignment sensitive. Matched with word
# boundaries, so `password_policy` and `user_count` never match.
CODE_KEY_LABELS = {
    "password": "PASSWORD", "passwd": "PASSWORD", "pwd": "PASSWORD",
    "username": "USERNAME", "user_name": "USERNAME", "userid": "USERNAME",
    "user_id": "USERNAME", "login": "USERNAME",
    "email": "EMAIL", "mail": "EMAIL",
    "phone": "PHONE", "mobile": "PHONE",
    "dob": "DATE_OF_BIRTH", "date_of_birth": "DATE_OF_BIRTH", "birthdate": "DATE_OF_BIRTH",
    "aadhaar": "GOVERNMENT_ID", "aadhar": "GOVERNMENT_ID", "ssn": "GOVERNMENT_ID",
    "card_number": "CREDIT_CARD", "cardnumber": "CREDIT_CARD",
    "account_number": "BANK_ACCOUNT", "ifsc": "BANK_ACCOUNT", "iban": "BANK_ACCOUNT",
    "api_key": "SECRET", "apikey": "SECRET", "access_token": "SECRET",
    "secret": "SECRET", "token": "SECRET", "private_key": "SECRET",
    "client_secret": "SECRET", "auth_token": "SECRET",
    "address": "ADDRESS",
    "name": "PERSON_NAME", "full_name": "PERSON_NAME", "fullname": "PERSON_NAME",
}

_CODE_KEY_ALTERNATION = "|".join(
    sorted((re.escape(key) for key in CODE_KEY_LABELS), key=len, reverse=True)
)

# key: "literal"  /  key = 'literal'  /  "key": "literal"
# The value must be a *quoted literal*. That single requirement is what keeps
# `username = get_username()` out of the results.
CODE_KV_RE = re.compile(
    rf"""["'`]?\b(?P<key>{_CODE_KEY_ALTERNATION})\b["'`]?\s*[:=]\s*"""
    r"""(?P<quote>["'`])(?P<value>(?:(?!(?P=quote))[^\\]|\\.)*)(?P=quote)""",
    re.IGNORECASE,
)

# Values that are placeholders or framework noise rather than real data.
NON_VALUES = {
    "", "null", "none", "undefined", "true", "false", "nan",
    "your password", "your username", "enter password", "enter username",
    "password", "username", "email", "changeme", "xxxx", "n/a", "na",
    "[redacted]", "***", "******",
}

MIN_VALUE_LENGTH = 2


def _label_for_field(attributes: dict[str, str]) -> str | None:
    """Decide what kind of PII this field's value would be, or None."""
    field_type = attributes.get("type", "").lower()
    if field_type in TYPE_LABELS:
        return TYPE_LABELS[field_type]

    haystacks = [
        attributes.get("autocomplete", ""),
        attributes.get("name", ""),
        attributes.get("id", ""),
        attributes.get("aria-label", ""),
        attributes.get("placeholder", ""),
        attributes.get("data-testid", ""),
    ]
    for haystack in haystacks:
        lowered = haystack.lower()
        if not lowered:
            continue
        for keyword, label in KEYWORD_LABELS:
            if keyword in lowered:
                return label
    return None


def _parse_attributes(blob: str, blob_offset: int):
    """Yield (name, value, value_start, value_end) with absolute offsets."""
    for match in ATTR_RE.finditer(blob):
        name = match.group(1).lower()
        for group_index in (2, 3, 4):
            if match.group(group_index) is not None:
                value = match.group(group_index)
                start = blob_offset + match.start(group_index)
                yield name, value, start, start + len(value)
                break


def _is_attribute_position(html: str, position: int, tag_spans: list[tuple[int, int]]) -> bool:
    if not any(start < position < end for start, end in tag_spans):
        return False
    preceding = html[position - 1] if position else "<"
    return preceding.isspace() or preceding == "<"


def _is_real_value(value: str) -> bool:
    stripped = value.strip()
    if len(stripped) < MIN_VALUE_LENGTH:
        return False
    if stripped.lower() in NON_VALUES:
        return False
    # Template placeholders left in server-rendered markup: {{ user.email }}, ${x}, <%= y %>
    if re.fullmatch(r"[{$<%].*[}>%]", stripped):
        return False
    return True


def detect(html: str) -> list[Entity]:
    found: list[Entity] = []

    for tag_match in TAG_RE.finditer(html):
        blob = tag_match.group(2)
        blob_offset = tag_match.start(2)

        attributes: dict[str, str] = {}
        spans: dict[str, tuple[int, int]] = {}
        for name, value, start, end in _parse_attributes(blob, blob_offset):
            attributes.setdefault(name, value)
            spans.setdefault(name, (start, end))

        label = _label_for_field(attributes)
        if label is None:
            continue

        for carrier in ("value", "data-value"):
            if carrier not in spans:
                continue
            value = attributes[carrier]
            if not _is_real_value(value):
                continue
            start, end = spans[carrier]
            found.append(
                Entity(start, end, label, "structure", 0.97, note=f"{carrier}@{attributes.get('name') or attributes.get('id') or 'field'}")
            )

    for area_match in TEXTAREA_RE.finditer(html):
        attributes = {
            name: value
            for name, value, _, _ in _parse_attributes(area_match.group(1), area_match.start(1))
        }
        label = _label_for_field(attributes)
        if label is None:
            continue
        inner = area_match.group(2)
        if not _is_real_value(inner):
            continue
        found.append(
            Entity(area_match.start(2), area_match.end(2), label, "structure", 0.9)
        )

    tag_spans = [(m.start(), m.end()) for m in ANY_TAG_RE.finditer(html)]

    for kv_match in CODE_KV_RE.finditer(html):
        # `name="csrf_token"` is an HTML attribute, not a `name = "..."`
        # assignment, and redacting its value destroys the field identity the
        # structural pass depends on. An attribute sits directly after
        # whitespace or `<` inside a tag; a genuine key inside an embedded JSON
        # blob (data-config='{"password":"x"}') is preceded by a brace or quote,
        # so that case is still caught.
        if _is_attribute_position(html, kv_match.start(), tag_spans):
            continue

        label = CODE_KEY_LABELS[kv_match.group("key").lower()]
        value = kv_match.group("value")
        if not _is_real_value(value):
            continue
        found.append(
            Entity(
                kv_match.start("value"),
                kv_match.end("value"),
                label,
                "structure",
                0.92,
                note=f"assignment:{kv_match.group('key').lower()}",
            )
        )

    return found
