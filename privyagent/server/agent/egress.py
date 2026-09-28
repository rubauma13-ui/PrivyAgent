"""Last line of defence before the network.

The privacy claim in this project is not "we redact things", it is "the remote
model never receives the original sensitive value". A claim like that is worth
enforcing mechanically rather than trusting the detectors to have been perfect
upstream, because a single missed span silently falsifies the whole premise.

So: every real value the firewall pulled out of this session is held in the
Vault, and every outbound payload is checked against that list before the socket
is opened. A hit aborts the request instead of degrading quietly.

This does NOT catch PII the detectors never found — nothing can. It catches the
much more likely bug: a value redacted in one field and reassembled, logged, or
copied into the prompt somewhere else in the pipeline.
"""

from __future__ import annotations

MIN_GUARDED_LENGTH = 4


class EgressViolation(RuntimeError):
    """Raised when a payload bound for a remote service still holds raw PII."""


def find_leaks(payload: str, forbidden: list[str]) -> list[str]:
    """Return the labels of any forbidden values present. Never the values."""
    leaks: list[str] = []
    lowered = payload.lower()
    for index, value in enumerate(forbidden):
        if not value or len(value.strip()) < MIN_GUARDED_LENGTH:
            continue
        if value.lower() in lowered:
            leaks.append(f"vault#{index}")
    return leaks


def sweep_payload(payload: str, forbidden: list[str]) -> str:
    """Proactively replace any forbidden values missed by upstream detectors."""
    import re
    swept = payload
    for value in forbidden:
        if not value or len(value.strip()) < MIN_GUARDED_LENGTH:
            continue
        pattern = re.compile(re.escape(value), re.IGNORECASE)
        swept = pattern.sub("[EGRESS_REDACTED]", swept)
    return swept

def assert_clean(payload: str, forbidden: list[str]) -> None:
    leaks = find_leaks(payload, forbidden)
    if leaks:
        # Log the details server-side only (never sent to the client)
        for leak in leaks:
            idx = int(leak.split('#')[1])
            print(f"[Egress Guard] LEAK DETECTED: {leak} = '{forbidden[idx]}'")
        raise EgressViolation(
            "Outbound payload still contains "
            f"{len(leaks)} unredacted sensitive value(s) ({', '.join(leaks)}). "
            "Request aborted before transmission."
        )
