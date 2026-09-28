"""Redaction for the extension's structured page capture.

The privacy module was specified against an HTML string, but the Chrome
extension does not send HTML — it sends `UIElement[]`, a JSON array of element
descriptors, plus a screenshot. Redacting the descriptor fields is a different
job from redacting a document, and this adapter is the missing piece between
the two components.

Two invariants:

* `id`, `tag`, `type` and `bbox` are never touched. They are how the extension
  maps an action back onto a live DOM node; changing them breaks execution.
* Only the free-text fields — `text`, `placeholder`, `name`, `ariaLabel`,
  `href` — pass through the firewall.
"""

from __future__ import annotations

from .firewall import PIIFirewall
from .vault import Vault

TEXT_FIELDS = ("text", "placeholder", "ariaLabel")
# `name` is a form field name, not user data. It is left intact so the model can
# still reason about the field ("this is the one called q"), and because
# redacting it would destroy the structural signal, not protect anything.
URL_FIELDS = ("href",)

SENSITIVE_INPUT_TYPES = {"password"}


def redact_elements(
    elements: list[dict],
    firewall: PIIFirewall,
    vault: Vault,
) -> tuple[list[dict], dict]:
    """Return (redacted_elements, label_counts)."""
    redacted: list[dict] = []
    counts: dict[str, int] = {}

    for element in elements:
        clean = dict(element)

        for field in TEXT_FIELDS + URL_FIELDS:
            value = clean.get(field)
            if not isinstance(value, str) or not value.strip():
                continue
            result = firewall.process(value, is_html=False, vault=vault, mode="placeholder")
            if result.entities:
                clean[field] = result.redacted_content
                for entity in result.entities:
                    counts[entity.label] = counts.get(entity.label, 0) + 1

        # A password field's contents are never in the capture to begin with —
        # `describeElement` in dom.ts does not read `.value`. Flag it so the
        # model knows the field exists without ever seeing what is in it.
        if str(clean.get("type", "")).lower() in SENSITIVE_INPUT_TYPES:
            clean["sensitive"] = True

        redacted.append(clean)

    return redacted, counts


def redact_task(task: str, firewall: PIIFirewall, vault: Vault) -> tuple[str, dict]:
    """The user's own instruction can carry PII too.

    "log in as raghul@example.com" must not reach the remote model verbatim.
    It becomes "log in as [PII_EMAIL_1]", and the placeholder is restored on
    the way back out if the model asks to type it.
    """
    result = firewall.process(task, is_html=False, vault=vault, mode="placeholder")
    counts: dict[str, int] = {}
    for entity in result.entities:
        counts[entity.label] = counts.get(entity.label, 0) + 1
    return result.redacted_content, counts
