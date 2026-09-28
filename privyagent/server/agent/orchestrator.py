"""Per-session agent loop.

This is the component that did not exist before. Member 1's background worker
did exactly one action per task and stopped; Member 3's `main()` walked a list of
steps but asked a human to paste HTML between them. Neither could run
"open youtube and search mr beast" to completion.

The orchestrator holds the step list, the turn counter and the PII vault for a
session. The extension calls /vision once per turn, carrying the session id back
each time along with a freshly extracted page. That keeps element IDs honest:
they are re-derived from the live DOM every turn, so an id can never be executed
against a page it wasn't extracted from.

Ordering inside one turn, which is the part that matters for the privacy claim:

    raw capture -> redact (vault fills) -> build prompt -> EGRESS GUARD
        -> model -> action with placeholders -> restore locally -> validate
        -> action with real values, returned to the extension only
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from ..privacy.capture import redact_elements, redact_task
from ..privacy.firewall import PIIFirewall
from ..privacy.vault import Vault
from . import llm
from .command_parser import separate_command, split_commands
from .element_prompt import build_page_data

MAX_TURNS = 12
MAX_TEXT = 10_000
MAX_VALUE = 1_000
MAX_SCROLL = 2_000
SESSION_TTL_SECONDS = 15 * 60


@dataclass
class Session:
    session_id: str
    task: str
    steps: list[str]
    index: int = 0
    turns: int = 0
    vault: Vault = field(default_factory=Vault)
    created_at: float = field(default_factory=time.time)
    history: list[dict] = field(default_factory=list)

    @property
    def finished(self) -> bool:
        return self.index >= len(self.steps)

    @property
    def current_step(self) -> str:
        return self.steps[self.index] if not self.finished else ""


class Orchestrator:
    def __init__(self, firewall: PIIFirewall):
        self.firewall = firewall
        self._sessions: dict[str, Session] = {}

    # --------------------------------------------------------------- sessions

    def _sweep(self) -> None:
        cutoff = time.time() - SESSION_TTL_SECONDS
        for session_id in [k for k, v in self._sessions.items() if v.created_at < cutoff]:
            # Dropping the session drops the vault, and with it every raw value
            # this process was holding for that page.
            del self._sessions[session_id]

    def start(self, task: str) -> Session:
        self._sweep()
        steps = split_commands(task) or [task]
        session = Session(session_id=uuid.uuid4().hex, task=task, steps=steps)
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        return self._sessions.get(session_id)

    def end(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    # ------------------------------------------------------------------- turn

    def handle_turn(self, task: str, elements: list[dict], session_id: str | None,
                    page_url: str = "") -> dict:
        session = self.get(session_id) or self.start(task)

        if session.finished:
            return self._response(session, {"action": "done"}, "done",
                                  "All steps completed.", None, {})

        session.turns += 1
        if session.turns > MAX_TURNS:
            return self._response(session, None, "error",
                                  f"Stopped after {MAX_TURNS} turns without completing the task.",
                                  None, {})

        # ---- privacy: everything below this line works on redacted data only
        redacted_step, task_counts = redact_task(session.current_step, self.firewall, session.vault)
        redacted_elements, element_counts = redact_elements(elements, self.firewall, session.vault)

        counts: dict[str, int] = dict(element_counts)
        for label, count in task_counts.items():
            counts[label] = counts.get(label, 0) + count

        parsed = separate_command(session.current_step)
        redacted_parsed = separate_command(redacted_step)

        # Redact page_url too — it can contain vault values (e.g. usernames in URLs)
        redacted_url = page_url
        if page_url:
            url_result = self.firewall.process(page_url, is_html=False, vault=session.vault, mode="placeholder")
            redacted_url = url_result.redacted_content
            for entity in url_result.entities:
                counts[entity.label] = counts.get(entity.label, 0) + 1

        page_data = build_page_data(redacted_parsed, redacted_elements, redacted_url)

        action, backend = llm.decide(
            redacted_parsed,
            redacted_step,
            page_data,
            redacted_elements,
            forbidden=session.vault.raw_values,
            history=session.history,
        )

        # Keep a copy of the raw action for history so we don't leak restored PII
        raw_action = dict(action)

        # ---- restore: placeholders become real values, on-device, after the model
        action = self._restore(action, session.vault)

        status, reason = self._validate(action, elements)

        # ---- loop detection: if AI repeats the same action 3+ times, stop it
        if status == "action" and len(session.history) >= 2:
            current_sig = (raw_action.get("action"), raw_action.get("element_id"), raw_action.get("text"))
            recent = session.history[-2:]
            if all(
                (h.get("action_type"), h.get("element_id"), h.get("text")) == current_sig
                for h in recent
            ):
                status = "error"
                reason = (f"Loop detected: repeated {raw_action.get('action')} on element "
                          f"{raw_action.get('element_id')} three times. Stopping.")

        more = False  # default: stop after this turn

        if status == "action":
            # Store rich history so the LLM knows exactly what it did (using raw action!)
            history_entry = {
                "step": redacted_step,
                "action_type": raw_action.get("action"),
            }
            if raw_action.get("action") == "type":
                history_entry["text"] = raw_action.get("text", "")
                history_entry["element_id"] = raw_action.get("element_id")
            elif raw_action.get("action") == "click":
                history_entry["element_id"] = raw_action.get("element_id")
            elif raw_action.get("action") == "scroll":
                history_entry["direction"] = raw_action.get("direction")
                history_entry["amount"] = raw_action.get("amount")
            session.history.append(history_entry)
            more = True
            
            # The local offline planner assumes 1 action = 1 step, and never outputs "done".
            if backend == "local":
                session.index += 1

        elif status == "done":
            session.index += 1
            if not session.finished:
                more = True
                status = "action"
                action = {"action": "scroll", "direction": "down", "amount": 1}
        # else: status is "error" or "cannot_identify" — more stays False

        return self._response(session, action if status == "action" else None,
                              status, reason, backend, counts, more_steps=more,
                              parsed=redacted_parsed)

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _restore(action: dict, vault: Vault) -> dict:
        restored = dict(action)
        for key in ("text", "value"):
            if isinstance(restored.get(key), str):
                restored[key] = vault.restore(restored[key])
        return restored

    @staticmethod
    def _validate(action: dict, elements: list[dict]) -> tuple[str, str]:
        """Mirror the extension's own validation so a bad action is rejected here.

        content.ts checks this again before touching the DOM. Both checks are
        deliberate: this one gives a useful error message, that one is the
        security boundary.
        """
        kind = action.get("action")

        if kind == "cannot_identify":
            return "cannot_identify", str(action.get("reason") or "element not identified")
        if kind == "done":
            return "done", "Task complete."

        if kind == "scroll":
            if action.get("direction") not in ("up", "down"):
                return "error", "scroll direction must be 'up' or 'down'"
            amount = action.get("amount")
            if not isinstance(amount, int) or not 1 <= amount <= MAX_SCROLL:
                return "error", f"scroll amount must be an integer in 1..{MAX_SCROLL}"
            return "action", ""

        if kind not in ("click", "type", "select"):
            return "error", f"unsupported action: {kind!r}"

        element_id = action.get("element_id")
        if not isinstance(element_id, int) or isinstance(element_id, bool) or element_id <= 0:
            return "error", "element_id must be a positive integer"

        known = {element.get("id") for element in elements}
        if element_id not in known:
            return "error", f"element_id {element_id} is not in the current page capture"

        if kind == "type":
            text = action.get("text")
            if not isinstance(text, str) or len(text) > MAX_TEXT:
                return "error", f"type text must be a string of at most {MAX_TEXT} characters"
        if kind == "select":
            value = action.get("value")
            if not isinstance(value, str) or len(value) > MAX_VALUE:
                return "error", f"select value must be a string of at most {MAX_VALUE} characters"

        return "action", ""

    def _response(self, session: Session, action, status: str, reason: str,
                  backend, counts: dict, more_steps: bool = False,
                  parsed: dict | None = None) -> dict:
        body = {
            "session_id": session.session_id,
            "status": status,
            "step_index": session.index,
            "total_steps": len(session.steps),
            "steps": session.steps,
            "more_steps": more_steps,
            "backend": backend,
            "privacy": {
                "redacted_values": len(session.vault),
                "by_label": counts,
                "vault_summary": session.vault.summary(),
                "ner_available": self.firewall.ner_available,
                "ner_status": self.firewall.ner_status,
            },
        }
        if parsed:
            body["parsed"] = parsed
        if reason:
            body["reason"] = reason
        if action is not None:
            body["action"] = action
        return body
