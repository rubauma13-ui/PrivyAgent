"""The reversible half of the firewall.

Plain `[REDACTED_EMAIL]` labels are lossy in two ways that break an agent:

1.  Two different emails on one page collapse to the same token, so the model
    cannot say which one it means.
2.  There is no way back. If the user's task is "log in with my email", the
    model's answer has to contain the email, and a one-way redaction leaves the
    extension typing the literal string "[REDACTED_EMAIL]" into the form.

So the agent pipeline redacts to numbered placeholders — `[PII_EMAIL_1]` — and
keeps the mapping in a Vault that never leaves the device. The model reasons
over placeholders; when it returns `{"action":"type","text":"[PII_EMAIL_1]"}`,
the orchestrator restores the real value locally, immediately before the
extension executes the action.

The vault is per-session, in-process, and never serialised into any response.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PLACEHOLDER_RE = re.compile(r"\[PII_([A-Z_]+)_(\d+)\]")


@dataclass
class Vault:
    _by_value: dict[str, str] = field(default_factory=dict)
    _by_placeholder: dict[str, str] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)

    def placeholder_for(self, label: str, value: str) -> str:
        """Return a stable placeholder for this value, minting one if needed."""
        key = f"{label}\u0000{value}"
        if key in self._by_value:
            return self._by_value[key]
        index = self._counters.get(label, 0) + 1
        self._counters[label] = index
        placeholder = f"[PII_{label}_{index}]"
        self._by_value[key] = placeholder
        self._by_placeholder[placeholder] = value
        return placeholder

    def restore(self, text: str) -> str:
        """Swap placeholders back for their real values."""
        if not text:
            return text

        def replace(match: re.Match) -> str:
            return self._by_placeholder.get(match.group(0), match.group(0))

        return PLACEHOLDER_RE.sub(replace, text)

    def contains_placeholder(self, text: str) -> bool:
        return bool(text) and bool(PLACEHOLDER_RE.search(text))

    @property
    def raw_values(self) -> list[str]:
        """Every real value held. Used by the egress guard, never returned."""
        return list(self._by_placeholder.values())

    def __len__(self) -> int:
        return len(self._by_placeholder)

    def summary(self) -> dict:
        """Counts by label. Deliberately contains no values."""
        counts: dict[str, int] = {}
        for placeholder in self._by_placeholder:
            match = PLACEHOLDER_RE.match(placeholder)
            if match:
                counts[match.group(1)] = counts.get(match.group(1), 0) + 1
        return counts
