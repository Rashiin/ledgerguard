"""Neutralising instruction-shaped text found in ledger data.

Invoice descriptions, counterparty names, transaction notes and document text
are written by outsiders. They reach the model inside tool results, and since
mandatory facts are shown in the verifier's own words, they can also reach the
owner inside a sentence the system vouches for. Both paths go through
``neutralise`` first.

This is one layer of four. The text is also fenced in <data> tags, the turn's
allowlist comes only from the owner's request, and the verifier and the rules
never read the prompt at all. Pattern matching will miss some phrasings. The
deterministic layers do not depend on it catching them.
"""

from __future__ import annotations

import re

REMOVED = "[instruction-like text removed]"

_PATTERNS = re.compile(
    "|".join(
        (
            r"\b(?:ignore|disregard|forget|override)\b[^.;\n]{0,60}\b(?:instructions?|rules?|"
            r"prompt|policy|polic(?:y|ies)|limits?|previous|above)\b[^.;\n]*",
            r"\b(?:you are now|act as|pretend to be|from now on)\b[^.;\n]*",
            r"\b(?:new|updated|revised) instructions?\b[^.;\n]*",
            r"\b(?:system|assistant|developer)\s*:",
            r"</?\s*(?:data|system|instructions?|tool)\s*>",
            r"\b(?:transfer|wire|send|pay|move)\b[^.;\n]{0,40}\b(?:all|the|any|remaining)\b[^.;\n]{0,20}"
            r"\b(?:funds|money|balance|cash)\b[^.;\n]*",
            r"\b(?:approve|confirm|execute|initiate)\b[^.;\n]{0,30}\b(?:immediately|automatically|"
            r"without (?:approval|confirmation|review))\b[^.;\n]*",
            r"\btier\s*[:=]\s*T[0-3]\b",
            r"`{3,}",
        )
    ),
    re.IGNORECASE,
)


def neutralise(text: str) -> tuple[str, list[str]]:
    """Return the text with instruction-shaped spans replaced, and the spans removed."""
    removed: list[str] = []

    def _swap(match: re.Match[str]) -> str:
        removed.append(match.group(0))
        return REMOVED

    return _PATTERNS.sub(_swap, text), removed
