"""
🔤 MCQ OPTION NORMALIZER

The LLM sometimes returns the four answer choices for a multiple-choice
question as ONE merged string (e.g. ``"A) Filters... B) Groups... C) Sorts...
D) Calculates..."``) instead of a proper 4-element array. When that single
blob is stored, the UI renders all options on one row and index-based grading
breaks.

``normalize_mcq_options`` repairs this: it splits a merged blob back into
separate options and strips any leading ``A)`` / ``(B)`` / ``C.`` letter
markers so the result matches the normal ``["text", "text", ...]`` shape the
rest of the app expects. It is idempotent — a list that is already clean is
returned unchanged (aside from whitespace trimming).

Apply it everywhere options are read (serving + grading) and where they are
generated, so existing rows and new rows both come out right.
"""

import json
import re

# A lettered option marker: optional "(", a single A-H letter, then ) . or :
# followed by whitespace.  Used both to split a blob and to strip per-option
# prefixes.
_MARKER = re.compile(r"\(?([A-Ha-h])[\)\.\:]\s+")
_LEADING_MARKER = re.compile(r"^\(?[A-Ha-h][\)\.\:]\s+")


def _split_merged(text: str):
    """If ``text`` contains several lettered markers (A) .. B) .. C) ..),
    split it into the individual option texts; otherwise return None."""
    markers = list(_MARKER.finditer(text))
    if len(markers) < 2:
        return None
    # Require the run to start at A/a — guards against splitting prose that
    # merely happens to contain something like "see point B) above".
    if markers[0].group(1).upper() != "A":
        return None
    parts = []
    for i, m in enumerate(markers):
        start = m.end()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        opt = text[start:end].strip().rstrip(";, ").strip()
        if opt:
            parts.append(opt)
    return parts if len(parts) >= 2 else None


def normalize_mcq_options(options):
    """Return a clean list of option strings.

    Accepts a JSON string, a Python list, or a single string. Splits a merged
    ``"A) .. B) .. C) .."`` blob into separate options and strips leading
    letter markers when every option carries one.
    """
    # Coerce a JSON-encoded string into a Python value first.
    if isinstance(options, str):
        stripped = options.strip()
        if stripped.startswith("["):
            try:
                options = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                options = [options]
        else:
            options = [options]

    if options is None:
        return []
    if not isinstance(options, list):
        return []

    items = [str(o).strip() for o in options if str(o).strip()]
    if not items:
        return []

    # Case 1: the whole option set was crammed into a single merged string.
    if len(items) == 1:
        split = _split_merged(items[0])
        if split:
            items = split

    # Case 2: each option is fine but carries a redundant "A)"/"B)" prefix
    # (the UI renders its own letter label). Strip only when EVERY option has
    # one, so legitimate text is never mangled.
    if len(items) >= 2 and all(_LEADING_MARKER.match(it) for it in items):
        items = [_LEADING_MARKER.sub("", it).strip() for it in items]

    return items
