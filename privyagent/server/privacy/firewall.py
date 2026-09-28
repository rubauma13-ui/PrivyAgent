"""The on-device PII privacy firewall.

    html -> detect -> merge -> validate -> redact exact spans -> sanitized html

The original HTML is never reparsed or regenerated. Tags, attributes, CSS,
script bodies, JSON, whitespace and indentation all survive byte for byte;
only the detected value spans are spliced out.

Interfaces kept to the shape the privacy module already declared:

    firewall.redact(html)   -> str
    firewall.process(html)  -> {"redacted_content": ..., "entities": [...]}
"""

from __future__ import annotations

from dataclasses import dataclass

from . import html_structure, rules, validate
from .entities import DetectionReport, Entity, attach_text, merge, redact
from .ner import NERDetector
from .vault import Vault


@dataclass
class FirewallResult:
    redacted_content: str
    entities: list[Entity]
    report: DetectionReport

    def as_dict(self) -> dict:
        return {
            "redacted_content": self.redacted_content,
            "entities": [entity.as_dict() for entity in self.entities],
            "report": self.report.as_dict(),
        }


class PIIFirewall:
    def __init__(self, model_dir=None, use_ner: bool = True, min_score: float = 0.60):
        self.ner = NERDetector(model_dir, min_score=min_score) if use_ner else None

    # ------------------------------------------------------------- properties

    @property
    def ner_available(self) -> bool:
        return bool(self.ner and self.ner.available)

    @property
    def ner_status(self) -> str:
        if not self.ner:
            return "disabled"
        return self.ner.reason

    # ------------------------------------------------------------- detection

    def detect(self, content: str, is_html: bool = True) -> list[Entity]:
        found: list[Entity] = []
        found.extend(rules.detect(content))
        if is_html:
            found.extend(html_structure.detect(content))
        else:
            # Source/JSON assignments appear in plain text too ("password: x").
            found.extend(html_structure.detect(content))
        if self.ner_available:
            found.extend(self.ner.detect(content))

        found = validate.filter_entities(found, content, is_html)
        found = merge(found)
        return attach_text(found, content)

    # ------------------------------------------------------------- redaction

    def process(
        self,
        content: str,
        is_html: bool = True,
        vault: Vault | None = None,
        mode: str = "label",
    ) -> FirewallResult:
        """Detect and redact.

        mode="label"       -> [REDACTED_EMAIL]     (one-way, human readable)
        mode="placeholder" -> [PII_EMAIL_1]        (reversible via `vault`)
        """
        if not content:
            return FirewallResult("", [], DetectionReport.build([], self.ner_available))

        entities = self.detect(content, is_html=is_html)

        if mode == "placeholder":
            if vault is None:
                raise ValueError("placeholder mode requires a Vault")

            def replacement(entity: Entity) -> str:
                return vault.placeholder_for(entity.label, entity.text)
        else:
            def replacement(entity: Entity) -> str:
                return f"[REDACTED_{entity.label}]"

        redacted = redact(content, entities, replacement)
        return FirewallResult(redacted, entities, DetectionReport.build(entities, self.ner_available))

    def redact(self, content: str, is_html: bool = True) -> str:
        """The one-liner the browser/integration side is meant to call."""
        return self.process(content, is_html=is_html).redacted_content


_DEFAULT: PIIFirewall | None = None


def get_firewall() -> PIIFirewall:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = PIIFirewall()
    return _DEFAULT
