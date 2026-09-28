"""Turn the extension's `UIElement[]` into prompt text.

The raw-HTML path in `command_parser.clean_html()` exists to squeeze a whole
document down to its interactive parts. On the extension path that work has
already happened in the browser: `dom.ts` walked the live DOM, checked computed
styles, discarded anything invisible or non-interactable, and handed back a
numbered list. Re-serialising that into fake HTML just to run clean_html over it
would throw away the element IDs, which are the only thing the extension can
execute against.

So this module renders the descriptors directly, and the integer `id` in the
listing is the same `element_id` the model must return.
"""

from __future__ import annotations

MAX_ELEMENTS = 120
MAX_FIELD = 60          # Shorter = less PII surface area sent to the LLM
MAX_OUTPUT_CHARS = 20000


def _shorten(value: str) -> str:
    text = " ".join(str(value).split())
    return text[:MAX_FIELD] + "..." if len(text) > MAX_FIELD else text


def _describe(element: dict) -> str:
    parts = [f"[{element.get('id')}]", f"<{element.get('tag', '?')}"]

    for key, attribute in (
        ("type", "type"),
        ("name", "name"),
        ("placeholder", "placeholder"),
        ("ariaLabel", "aria-label"),
        ("href", "href"),
    ):
        value = element.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(f'{attribute}="{_shorten(value)}"')

    if element.get("sensitive"):
        parts.append('sensitive="true"')

    parts.append(">")
    head = " ".join(parts).replace("< ", "<").replace(" >", ">")

    text = element.get("text")
    if isinstance(text, str) and text.strip():
        head += f" {_shorten(text)}"

    bbox = element.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        head += f"  @({bbox[0]},{bbox[1]} {bbox[2]}x{bbox[3]})"

    return head


def render_elements(elements: list[dict]) -> str:
    if not elements:
        return "NO INTERACTIVE ELEMENTS FOUND"

    lines: list[str] = []
    total = 0
    for element in elements[:MAX_ELEMENTS]:
        line = _describe(element)
        total += len(line)
        if total > MAX_OUTPUT_CHARS:
            lines.append(f"... {len(elements) - len(lines)} more elements omitted")
            break
        lines.append(line)

    return "\n".join(lines)


def build_page_data(task: dict, elements: list[dict], page_url: str = "") -> str:
    """The user-side prompt body. Mirrors Member 3's PAGE DATA layout."""
    return f"""PAGE URL:
{page_url or "(not supplied)"}

ACTION:
{task.get("ACTION", "")}

TARGET:
{task.get("TARGET", "")}

VALUE:
{task.get("VALUE", "")}

INTERACTIVE ELEMENTS:
{render_elements(elements)}
"""
