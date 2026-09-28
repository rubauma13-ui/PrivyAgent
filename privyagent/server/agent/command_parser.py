"""Member 3's command parser and HTML cleaner, carried over unchanged.

Lines 133-503 of MainFullPython.py, verbatim except for line endings. The
function and constant names are preserved exactly as the integration brief
requires: separate_command, split_commands, clean_html, _normalise,
_strip_filler, _strip_all_filler, _hidden_here, _is_hidden, _clean_attributes,
_flatten_text, _form_key.

What moved OUT of this file and why:

* call_agent      -> agent/llm.py. It now returns a structured action rather
                     than a JavaScript string, because the Chrome extension
                     deliberately refuses to execute arbitrary JS.
* process_single_step -> agent/orchestrator.py, which owns per-session state.
* main()/read_html_block()/get_html_for_step() -> demo/cli.py. The extension
  supplies page state now; nobody pastes HTML by hand in the integrated system.
* The hard-coded OpenRouter API key is gone. See agent/llm.py.

clean_html() is still used, but only on the raw-HTML path (/redact and the CLI).
The extension path never produces HTML — see agent/element_prompt.py.
"""

import re

from bs4 import BeautifulSoup, Comment


# ============================================================
# COMMAND VOCABULARY
# ============================================================

CLICK_WORDS = ("click", "press", "tap", "open", "go to", "goto", "visit")
SEARCH_WORDS = ("search", "look up", "lookup", "find")
TYPE_WORDS = ("type", "enter", "write", "input", "fill")
SELECT_WORDS = ("select", "choose", "pick")

# Every verb that can legitimately start a new step. Used by split_commands
# so that "mac and cheese" is not torn into two commands.
ALL_ACTION_WORDS = CLICK_WORDS + SEARCH_WORDS + TYPE_WORDS + SELECT_WORDS

_VERB_ALTERNATION = "|".join(
    re.escape(word) for word in sorted(ALL_ACTION_WORDS, key=len, reverse=True)
)


def _normalise(text):
    return " ".join(text.strip().split())


def _strip_filler(text):
    """Remove leading filler words: 'for the cat' -> 'cat'."""
    return re.sub(
        r"^(?:for|the|a|an|on|to)\s+",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()


def _strip_all_filler(text):
    previous = None
    while previous != text:
        previous = text
        text = _strip_filler(text)
    return text


# ============================================================
# SEPARATE ONE COMMAND
# ============================================================

def separate_command(user_command):
    """
    Turn one step into {ACTION, TARGET, VALUE}.

    Patterns carrying an explicit target are matched FIRST, otherwise
    'type hello in the search box' would be swallowed by the bare
    'type' branch and the whole phrase would land in VALUE.
    """
    user_command = _normalise(user_command)

    if not user_command:
        return {"ACTION": "UNKNOWN", "TARGET": "", "VALUE": ""}

    # ---- targeted TYPE: "type X in/into the Y" ----
    match = re.match(
        r"^(?:type|enter|write|input|fill)\s+(.+?)\s+"
        r"(?:in|into|on)\s+(?:the\s+)?(.+)$",
        user_command,
        re.IGNORECASE,
    )
    if match:
        return {
            "ACTION": "TYPE",
            "TARGET": _strip_all_filler(match.group(2)),
            "VALUE": match.group(1).strip().strip("\"'"),
        }

    # ---- targeted SELECT: "select X from/in the Y" ----
    match = re.match(
        r"^(?:select|choose|pick)\s+(.+?)\s+"
        r"(?:from|in|on)\s+(?:the\s+)?(.+)$",
        user_command,
        re.IGNORECASE,
    )
    if match:
        return {
            "ACTION": "SELECT",
            "TARGET": _strip_all_filler(match.group(2)),
            "VALUE": match.group(1).strip().strip("\"'"),
        }

    # ---- targeted SEARCH: "search X in/on the Y" ----
    match = re.match(
        r"^(?:search|look up|lookup|find)\s+(?:for\s+)?(.+?)\s+"
        r"(?:in|on|using)\s+(?:the\s+)?(.+)$",
        user_command,
        re.IGNORECASE,
    )
    if match:
        return {
            "ACTION": "SEARCH",
            "TARGET": _strip_all_filler(match.group(2)),
            "VALUE": match.group(1).strip().strip("\"'"),
        }

    lowered = user_command.lower()

    # ---- bare verbs ----
    for word in CLICK_WORDS:
        if lowered.startswith(word + " "):
            return {
                "ACTION": "CLICK",
                "TARGET": _strip_all_filler(user_command[len(word):]),
                "VALUE": "",
            }

    for word in SEARCH_WORDS:
        if lowered.startswith(word + " "):
            return {
                "ACTION": "SEARCH",
                "TARGET": "",
                "VALUE": _strip_all_filler(user_command[len(word):]),
            }

    for word in TYPE_WORDS:
        if lowered.startswith(word + " "):
            return {
                "ACTION": "TYPE",
                "TARGET": "",
                "VALUE": user_command[len(word):].strip().strip("\"'"),
            }

    for word in SELECT_WORDS:
        if lowered.startswith(word + " "):
            return {
                "ACTION": "SELECT",
                "TARGET": "",
                "VALUE": _strip_all_filler(user_command[len(word):]),
            }

    return {"ACTION": "UNKNOWN", "TARGET": user_command, "VALUE": ""}


# ============================================================
# SPLIT MULTI-STEP USER COMMAND
# ============================================================

def split_commands(user_command):
    """
    Split a multi-step request into sequential steps.

    Only splits on 'and'/'then'/';' when the next word is an action verb,
    so 'search mac and cheese' stays as one step while
    'open youtube and search mr beast' becomes two.
    """
    user_command = _normalise(user_command)

    if not user_command:
        return []

    pattern = (
        r"\s*(?:,\s*)?(?:\band\s+then\b|\bthen\b|\band\b|;)\s+"
        rf"(?=(?:{_VERB_ALTERNATION})\b)"
    )

    parts = re.split(pattern, user_command, flags=re.IGNORECASE)

    return [part.strip(" ,;") for part in parts if part.strip(" ,;")]


# ============================================================
# CLEAN HTML USING BEAUTIFUL SOUP
# ============================================================

# Deleted outright, together with their contents.
JUNK_TAGS = (
    "script", "style", "noscript", "svg", "path", "iframe", "canvas",
    "video", "audio", "source", "track", "object", "embed", "template",
    "meta", "link", "head", "title", "picture", "img", "figure",
)

# Elements worth sending to the model.
INTERACTIVE_TAGS = ("button", "input", "textarea", "select", "label", "a")

USEFUL_ATTRIBUTES = {
    "id", "name", "type", "value", "placeholder", "aria-label",
    "aria-labelledby", "aria-describedby", "role", "for", "selected",
    "checked", "disabled", "href", "title", "alt", "action", "method",
    "contenteditable",
}

MAX_ATTR_LENGTH = 120
MAX_TEXT_LENGTH = 80
MAX_OPTIONS = 40
MAX_OUTPUT_CHARS = 24000


def _hidden_here(tag):
    if getattr(tag, "attrs", None) is None:
        return False
    if str(tag.get("type", "")).lower() == "hidden":
        return True
    if tag.has_attr("hidden"):
        return True
    if str(tag.get("aria-hidden", "")).lower() == "true":
        return True
    # NOTE: inline display:none / visibility:hidden is deliberately NOT
    # treated as "hidden" here.
    #
    # In SPA-style pages where every "page"/section lives in the same
    # document and is toggled visible/invisible via JS, the markup a user
    # copies by hand between steps very often still carries the section's
    # original/static inline style (e.g. `style="display:none"`) even
    # though that section is the one actually on screen right now.
    #
    # Trusting that inline style caused entire visible sections (and every
    # input/button inside them) to be filtered out of clean_html's output,
    # leaving the model with nothing to work with -> CANNOT_IDENTIFY.
    #
    # `hidden` attribute and aria-hidden="true" are explicit, deliberate
    # signals and are still respected above.
    return False


def _is_hidden(tag):
    """True if the element or any ancestor is hidden."""
    if _hidden_here(tag):
        return True
    for parent in tag.parents:
        if _hidden_here(parent):
            return True
    return False


def _clean_attributes(tag):
    for attribute in list(tag.attrs):
        keep = attribute in USEFUL_ATTRIBUTES or attribute.startswith("data-")

        if not keep:
            del tag.attrs[attribute]
            continue

        value = tag.attrs[attribute]
        if isinstance(value, list):
            value = " ".join(value)
        value = " ".join(str(value).split())

        if not value and attribute not in ("selected", "checked", "disabled"):
            del tag.attrs[attribute]
            continue

        if len(value) > MAX_ATTR_LENGTH:
            value = value[:MAX_ATTR_LENGTH] + "..."

        tag.attrs[attribute] = value


def _flatten_text(tag):
    """Collapse nested markup inside a button/link into one short label."""
    text = " ".join(tag.get_text(" ", strip=True).split())
    if len(text) > MAX_TEXT_LENGTH:
        text = text[:MAX_TEXT_LENGTH] + "..."
    tag.clear()
    if text:
        tag.string = text


def _form_key(tag):
    """Identify the form that owns this element, for grouped output."""
    form = tag.find_parent("form")
    if form is None:
        return None, "NO FORM"

    label_parts = []
    for attribute in ("id", "name", "action", "role"):
        value = form.get(attribute)
        if value:
            if isinstance(value, list):
                value = " ".join(value)
            label_parts.append(f'{attribute}="{value[:60]}"')

    # If the form has none of the identifying attributes above, do NOT
    # emit a bare word like "unnamed" here. A bare word next to
    # "--- FORM: ---" reads exactly like a quotable attribute value, and
    # the model previously copied it verbatim into a selector such as
    # form[id="unnamed"] (which does not exist in the real DOM, so
    # querySelector returned null and .click() threw). This phrasing
    # can't be mistaken for an attribute and tells the model how to
    # target the button instead.
    label = (
        " ".join(label_parts)
        if label_parts
        else "no id/name/action on this form - target its button via "
             "the input's .form or .closest('form'), not a form selector"
    )
    return id(form), label


def clean_html(raw_html):
    """
    Reduce a full page to just the interactive elements, grouped by the
    form that contains them.

    Structural tags are never decomposed — they are simply not emitted —
    so nothing nested inside a div/li/p is lost.
    """
    soup = BeautifulSoup(raw_html, "html.parser")

    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    for tag_name in JUNK_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    candidates = list(soup.find_all(INTERACTIVE_TAGS))
    candidates += soup.select('[role="button"], [role="link"], [role="combobox"], [role="searchbox"], [contenteditable="true"]')

    groups = {}
    group_labels = {}
    seen = set()
    emitted = set()
    total = 0

    for element in candidates:
        if id(element) in emitted:
            continue

        # Skip children of an element already emitted (e.g. option in select).
        if any(id(parent) in emitted for parent in element.parents):
            continue

        if _is_hidden(element):
            continue

        # Links with neither href nor text are noise.
        if element.name == "a" and not element.get("href") and not element.get_text(strip=True):
            continue

        emitted.add(id(element))

        key, label = _form_key(element)

        if element.name == "select":
            options = element.find_all("option")
            for extra in options[MAX_OPTIONS:]:
                extra.decompose()
            for option in element.find_all("option"):
                _clean_attributes(option)
                _flatten_text(option)
        elif element.name in ("button", "a", "label") or element.get("role") or element.get("contenteditable"):
            _flatten_text(element)

        _clean_attributes(element)

        serialised = " ".join(str(element).split())

        if serialised in seen:
            continue
        seen.add(serialised)

        total += len(serialised)
        if total > MAX_OUTPUT_CHARS:
            break

        groups.setdefault(key, []).append(serialised)
        group_labels[key] = label

    if not groups:
        return "NO INTERACTIVE ELEMENTS FOUND"

    blocks = []
    for key, elements in groups.items():
        header = f"--- FORM: {group_labels[key]} ---"
        blocks.append(header + "\n" + "\n".join(elements))

    return "\n\n".join(blocks)
