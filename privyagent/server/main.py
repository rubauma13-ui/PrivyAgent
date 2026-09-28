"""PrivyAgent local server.

Replaces the mock FastAPI service the extension was pointed at. It is the only
process that ever holds the raw page capture, and it is bound to loopback: the
"on-device" guarantee is that this server runs on the user's own machine, and
that the only thing it forwards to OpenRouter is redacted text.

Endpoints
    POST /vision   the extension's existing contract, extended with session_id
    POST /redact   the privacy module's standalone contract: html in, html out
    GET  /health   what is loaded, what is missing, which backend is live
"""

from __future__ import annotations

import os

# Simple .env loader to avoid needing python-dotenv
try:
    with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip("'\"")
except FileNotFoundError:
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .agent import llm
from .agent.egress import EgressViolation
from .agent.orchestrator import Orchestrator
from .privacy.firewall import PIIFirewall
from .privacy.vault import Vault

BIND_HOST = os.environ.get("PRIVYAGENT_HOST", "127.0.0.1")

firewall = PIIFirewall()
orchestrator = Orchestrator(firewall)

app = FastAPI(title="PrivyAgent local server", version="1.0.0")

# Chrome extensions send an chrome-extension:// origin. Loopback only; this must
# never be widened to "*" with a public bind address.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^chrome-extension://.*$",
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)


# --------------------------------------------------------------------- models
# These mirror extension/src/shared/types.ts field for field. If one side
# changes, this file is the place the mismatch should surface.


class ScreenshotCapture(BaseModel):
    success: bool = False
    dataUrl: str | None = None
    error: str | None = None


class UIElement(BaseModel):
    id: int
    tag: str
    text: str = ""
    type: str | None = None
    placeholder: str | None = None
    name: str | None = None
    ariaLabel: str | None = None
    href: str | None = None
    bbox: list[int] = Field(default_factory=list)


class PageCapture(BaseModel):
    elements: list[UIElement] = Field(default_factory=list)
    screenshot: ScreenshotCapture | None = None
    url: str = ""
    title: str = ""


class VisionRequest(BaseModel):
    task: str
    page: PageCapture
    session_id: str | None = None


class RedactRequest(BaseModel):
    html: str
    mode: str = "label"  # "label" | "placeholder"


# ------------------------------------------------------------------ endpoints


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "llm_backend": llm.BACKEND,
        "llm_model": llm.MODEL if llm.BACKEND == "openrouter" else "local-planner",
        "openrouter_key_present": bool(os.environ.get("OPENROUTER_API_KEY")),
        "nvidia_key_present": bool(os.environ.get("NVIDIA_API_KEY")),
        "ner_available": firewall.ner_available,
        "ner_status": firewall.ner_status,
        "detectors": ["rules", "html_structure"] + (["ner"] if firewall.ner_available else []),
    }


@app.post("/redact")
def redact(request: RedactRequest) -> dict:
    """The privacy module's own contract, unchanged: content in, sanitized out."""
    vault = Vault() if request.mode == "placeholder" else None
    result = firewall.process(request.html, is_html=True, vault=vault, mode=request.mode)
    return result.as_dict()


@app.post("/vision")
def vision(request: VisionRequest) -> dict:
    """One turn of the agent loop."""
    elements = [element.model_dump(exclude_none=False) for element in request.page.elements]

    if request.page.screenshot and request.page.screenshot.dataUrl:
        # Deliberately dropped. Nothing in this pipeline redacts pixels — there
        # is no face or on-screen-text detector wired in — so forwarding a raw
        # screenshot to a remote model would defeat the entire privacy claim.
        # See README, "Screenshots".
        pass

    try:
        return orchestrator.handle_turn(
            task=request.task,
            elements=elements,
            session_id=request.session_id,
            page_url=request.page.url,
        )
    except EgressViolation as error:
        return {
            "status": "error",
            "reason": f"Blocked by egress guard: {error}",
            "session_id": request.session_id,
        }


@app.post("/session/end")
def end_session(payload: dict) -> dict:
    session_id = payload.get("session_id")
    if session_id:
        orchestrator.end(session_id)
    return {"status": "ok"}
