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
from dataclasses import dataclass

REMOVED = "[instruction-like text removed]"

# Grouped by what the text is trying to do, not by any one phrasing. The first
# run against the adversarial fixtures missed whole categories, recorded in the
# security self-assessment, and the groups after the first block were added then.
_GROUPS = (
    # Overriding the instructions or the model's role.
    r"\b(?:ignore|disregard|forget|override)\b[^.;\n]{0,60}\b(?:instructions?|rules?|"
    r"prompt|policy|polic(?:y|ies)|limits?|previous|above)\b[^.;\n]*",
    r"\b(?:you are now|act as|pretend to be|from now on)\b[^.;\n]*",
    r"\b(?:new|updated|revised) instructions?\b[^.;\n]*",
    r"\b(?:system|assistant|developer)\s*:",
    r"</?\s*(?:data|system|instructions?|tool)\s*>",
    r"\btier\s*[:=]\s*T[0-3]\b",
    r"`{3,}",
    # Text addressed to the AI rather than to a person.
    r"\b(?:note|message|instructions?|request)s?\s+(?:for|to)\s+(?:the\s+)?"
    r"(?:ai|assistant|model|copilot|agent|bot)\b[^.;\n]*",
    r"\b(?:ai|assistant|copilot|agent)\s+instructions?\b[^.;\n]*",
    # Moving money or acting on records.
    r"\b(?:transfer|wire|send|pay|move)\b[^.;\n]{0,40}\b(?:all|the|any|remaining)\b"
    r"[^.;\n]{0,20}\b(?:funds|money|balance|cash)\b[^.;\n]*",
    r"\b(?:approve|confirm|execute|initiate)\b[^.;\n]{0,30}\b(?:immediately|automatically|"
    r"without (?:approval|confirmation|review))\b[^.;\n]*",
    r"\b(?:initiate|make|release|schedule)\s+(?:a\s+|the\s+)?payments?\b[^.;\n]*",
    r"\baccept\b[^.;\n]{0,60}\b(?:offer|loan|financing|factoring|credit)\b[^.;\n]*",
    r"\bmark\s+(?:invoice|inv_\w+|it|this|them)\b[^.;\n]{0,40}\bas\b[^.;\n]*",
    r"\brecord\b[^.;\n]{0,40}\bpayments?\b[^.;\n]*",
    r"\broute\b[^.;\n]{0,40}\bpayments?\b[^.;\n]*",
    # Claiming approval or authority that only the owner can give.
    r"\b(?:already|pre-?)\s*approved\b[^.;\n]*",
    r"\bis\s+now\s+approved\b[^.;\n]*",
    r"\bno\s+(?:confirmation|approval|review|sign-?off)\s+is\s+(?:needed|required)\b[^.;\n]*",
    r"\bwithout\s+(?:review|approval|confirmation|checking)\b[^.;\n]*",
    # Payment-redirection fraud.
    r"\b(?:bank|account|payment)\s+details\s+(?:have|has)\s+changed\b[^.;\n]*",
    r"\bnew\s+(?:vendor|supplier|bank|beneficiary)?\s*account\b[^.;\n]*",
    # Planting memory or hiding the attempt.
    r"\b(?:as\s+(?:we\s+)?(?:discussed|agreed)|in\s+your\s+previous\s+message)\b[^.;\n]*",
    r"\bremember\s+this\b[^.;\n]*",
    r"\b(?:for|in)\s+the\s+next\s+(?:request|message|turn|session)\b[^.;\n]*",
    r"\bdo\s+not\s+(?:mention|tell|reveal|show|disclose)\b[^.;\n]*",
)

_PATTERNS = re.compile("|".join(_GROUPS), re.IGNORECASE)


def neutralise(text: str) -> tuple[str, list[str]]:
    """Return the text with instruction-shaped spans replaced, and the spans removed."""
    removed: list[str] = []

    def _swap(match: re.Match[str]) -> str:
        removed.append(match.group(0))
        return REMOVED

    return _PATTERNS.sub(_swap, text), removed


@dataclass(frozen=True)
class Attachment:
    """Text extracted from a document the owner attached, such as a PDF memo.

    It is outside content even though the owner supplied it: the owner attaches
    a document, they do not write it. It is fenced and neutralised like ledger
    text, and it never feeds the allowlist, which reads only the owner's words.
    """

    name: str
    text: str

    def cleaned(self) -> tuple[Attachment, list[str]]:
        name, removed_name = neutralise(self.name)
        text, removed_text = neutralise(self.text)
        return Attachment(name, text), removed_name + removed_text
