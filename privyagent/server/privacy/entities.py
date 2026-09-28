"""Entity model shared by every detector in the firewall.

A detector's only job is to return Entity objects whose ``start``/``end`` are
character offsets into the ORIGINAL input string. Nothing in this package ever
rebuilds the input; redaction is pure slice-and-splice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

# Detection sources, ordered by how much we trust them when spans collide.
# HTML structure ("this is the value= of an input named password") is the
# strongest evidence we have; a bare NER guess is the weakest.
SOURCE_PRIORITY = {
    "structure": 30,
    "rule": 20,
    "ner": 10,
}

LABELS = (
    "USERNAME", "PASSWORD", "PERSON_NAME", "DATE_OF_BIRTH", "EMAIL",
    "PHONE", "ADDRESS", "LOCATION", "GOVERNMENT_ID", "CREDIT_CARD",
    "BANK_ACCOUNT", "IP_ADDRESS", "PERSONAL_ID", "SECRET",
)


@dataclass
class Entity:
    start: int
    end: int
    label: str
    source: str = "rule"
    score: float = 1.0
    text: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError(f"empty or inverted span: {self.start}..{self.end}")
        if self.label not in LABELS:
            raise ValueError(f"unknown label: {self.label}")

    @property
    def priority(self) -> int:
        return SOURCE_PRIORITY.get(self.source, 0)

    @property
    def length(self) -> int:
        return self.end - self.start

    def as_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "label": self.label,
            "source": self.source,
            "score": round(self.score, 4),
        }


def attach_text(entities: Iterable[Entity], source_text: str) -> list[Entity]:
    out = []
    for entity in entities:
        entity.text = source_text[entity.start:entity.end]
        out.append(entity)
    return out


def merge(entities: Iterable[Entity]) -> list[Entity]:
    """Resolve overlaps.

    Two detectors very often find the same thing (the regex finds the email,
    the NER model finds the email, the structural pass finds the email because
    it sits in value= of an input named "email"). Keeping all three would
    corrupt the offsets during redaction, so overlapping spans collapse to one
    winner: highest source priority, then longest span, then highest score.
    """
    ordered = sorted(
        entities,
        key=lambda e: (-e.priority, -e.length, -e.score, e.start),
    )

    kept: list[Entity] = []
    for candidate in ordered:
        if any(candidate.start < k.end and k.start < candidate.end for k in kept):
            continue
        kept.append(candidate)

    kept.sort(key=lambda e: e.start)
    return kept


def redact(text: str, entities: Iterable[Entity], replacement) -> str:
    """Replace each span, highest offset first so earlier spans stay valid.

    ``replacement`` is called with the Entity and returns the literal string to
    splice in. Everything outside the spans — tags, CSS, indentation, the lot —
    is copied through byte for byte.
    """
    result = text
    for entity in sorted(entities, key=lambda e: e.start, reverse=True):
        result = result[:entity.start] + replacement(entity) + result[entity.end:]
    return result


@dataclass
class DetectionReport:
    """What the firewall found, for logging and for the demo UI.

    Deliberately carries counts and labels only — never the detected values —
    so that enabling debug logging can't leak the very data we just redacted.
    """

    total: int = 0
    by_label: dict = field(default_factory=dict)
    by_source: dict = field(default_factory=dict)
    ner_available: bool = False

    @classmethod
    def build(cls, entities: list[Entity], ner_available: bool) -> "DetectionReport":
        report = cls(total=len(entities), ner_available=ner_available)
        for entity in entities:
            report.by_label[entity.label] = report.by_label.get(entity.label, 0) + 1
            report.by_source[entity.source] = report.by_source.get(entity.source, 0) + 1
        return report

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "by_label": self.by_label,
            "by_source": self.by_source,
            "ner_available": self.ner_available,
        }
