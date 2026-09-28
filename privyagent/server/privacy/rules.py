"""Deterministic detection.

Every pattern here is paired with a validator where one exists (Luhn for cards,
Verhoeff for Aadhaar, octet range for IPv4). A regex that matches 16 digits and
stops there will redact order numbers, product SKUs and timestamps, which on a
shopping page is most of the page.
"""

from __future__ import annotations

import re

from .entities import Entity

EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}"
)

IPV4_RE = re.compile(r"(?<![\w.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\w.])")

# 13-19 digits in groups of 2-6 separated by nothing, a space or a hyphen.
CARD_RE = re.compile(r"(?<![\d\-])(?:\d[ \-]?){12,18}\d(?![\d\-])")

# Indian mobile (optionally +91 / 0 prefixed) and generic international.
PHONE_RE = re.compile(
    r"(?<![\w])(?:\+?91[\s\-]?|0)?[6-9]\d{9}(?![\d])"
    r"|(?<![\w])\+\d{1,3}[\s\-]?\d{6,12}(?![\d])"
)

AADHAAR_RE = re.compile(r"(?<![\d])[2-9]\d{3}[\s\-]?\d{4}[\s\-]?\d{4}(?![\d])")
PAN_RE = re.compile(r"(?<![A-Z0-9])[A-Z]{5}\d{4}[A-Z](?![A-Z0-9])")
IFSC_RE = re.compile(r"(?<![A-Z0-9])[A-Z]{4}0[A-Z0-9]{6}(?![A-Z0-9])")

DOB_RE = re.compile(
    r"(?<![\d/\-])(?:"
    r"(?:0?[1-9]|[12]\d|3[01])[/\-.](?:0?[1-9]|1[0-2])[/\-.](?:19|20)\d{2}"
    r"|(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r")(?![\d/\-])"
)

# Credential-shaped secrets. These are high-confidence because the prefixes are
# vendor-assigned and do not occur in ordinary page text.
SECRET_RES = (
    re.compile(r"sk-or-v1-[A-Za-z0-9]{32,}"),
    re.compile(r"sk-[A-Za-z0-9]{32,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"ey[A-Za-z0-9_\-]{10,}\.ey[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)

_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6), (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8), (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2), (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4), (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2), (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0), (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5), (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def luhn_valid(digits: str) -> bool:
    numbers = [int(character) for character in digits if character.isdigit()]
    if not 13 <= len(numbers) <= 19:
        return False
    checksum = 0
    for index, digit in enumerate(reversed(numbers)):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def verhoeff_valid(digits: str) -> bool:
    numbers = [int(character) for character in digits if character.isdigit()]
    if len(numbers) != 12:
        return False
    checksum = 0
    for index, digit in enumerate(reversed(numbers)):
        checksum = _VERHOEFF_D[checksum][_VERHOEFF_P[index % 8][digit]]
    return checksum == 0


def detect(text: str) -> list[Entity]:
    found: list[Entity] = []

    for match in EMAIL_RE.finditer(text):
        found.append(Entity(match.start(), match.end(), "EMAIL", "rule"))

    for match in IPV4_RE.finditer(text):
        if all(0 <= int(group) <= 255 for group in match.groups()):
            found.append(Entity(match.start(), match.end(), "IP_ADDRESS", "rule"))

    for match in CARD_RE.finditer(text):
        if luhn_valid(match.group()):
            found.append(Entity(match.start(), match.end(), "CREDIT_CARD", "rule", 0.99))

    for match in AADHAAR_RE.finditer(text):
        if verhoeff_valid(match.group()):
            found.append(Entity(match.start(), match.end(), "GOVERNMENT_ID", "rule", 0.99))

    for match in PAN_RE.finditer(text):
        found.append(Entity(match.start(), match.end(), "GOVERNMENT_ID", "rule", 0.9))

    for match in IFSC_RE.finditer(text):
        found.append(Entity(match.start(), match.end(), "BANK_ACCOUNT", "rule", 0.85))

    for match in PHONE_RE.finditer(text):
        found.append(Entity(match.start(), match.end(), "PHONE", "rule", 0.85))

    for match in DOB_RE.finditer(text):
        found.append(Entity(match.start(), match.end(), "DATE_OF_BIRTH", "rule", 0.7))

    for pattern in SECRET_RES:
        for match in pattern.finditer(text):
            found.append(Entity(match.start(), match.end(), "SECRET", "rule", 0.99))

    return found
