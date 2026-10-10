"""Figures inside free text.

The model writes prose (claim statements, the rationale) and prose can carry
numbers. Every money-like number in that prose must equal a figure the
verifier has itself computed:

* a number that matches a model figure the verifier **corrected** is replaced
  with the recomputed value, and the correction is recorded;
* a number that matches a verified figure is left alone;
* anything else is masked with a visible marker.

So no unchecked amount reaches the user, even inside a sentence.

Matching respects the precision the text shows: "€46.9k" matches 46,854.00,
"34.8%" matches 34.81. Dates, row ids ("inv_0412"), years and small counts
("week 7", "26 days") are not treated as figures.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from ledgerguard.money import format_eur

UNVERIFIED = "[figure removed: not verified]"

_NUMBER = re.compile(
    r"""
    (?<![\w.\-/])                       # not inside an id, a date or another number
    (?P<cur>€\s?|EUR\s?)?
    (?P<sign>[-−])?
    (?P<int>\d{1,3}(?:,\d{3})+|\d+)
    (?P<frac>\.\d+)?
    (?P<suffix>\s?[kKmM](?![a-zA-Z]))?
    (?P<pct>\s?%)?
    (?P<eur>\s?(?:EUR|euros?)\b)?
    (?![\w\-/]|\.\d)                    # not followed by more id or date characters
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class Figure:
    value: Decimal  # absolute value
    quantum: Decimal  # the precision the text shows
    percent: bool
    start: int
    end: int
    text: str


def find_figures(text: str) -> list[Figure]:
    found = []
    for m in _NUMBER.finditer(text):
        digits = m["int"].replace(",", "")
        frac = m["frac"] or ""
        money = bool(m["cur"] or m["eur"])
        percent = bool(m["pct"])
        suffix = (m["suffix"] or "").strip().lower()
        plain = int(digits)
        is_figure = (
            money or percent or bool(frac) or "," in m["int"] or bool(suffix) or plain >= 1000
        )
        if not is_figure:
            continue  # small counts: weeks, days, employees
        if not (money or percent or frac or suffix or "," in m["int"]) and 1900 <= plain <= 2100:
            continue  # a bare year
        value = Decimal(digits + frac)
        quantum = Decimal(1).scaleb(-len(frac) + 1) if frac else Decimal(1)
        multiplier = {"k": Decimal(1000), "m": Decimal(1_000_000)}.get(suffix, Decimal(1))
        found.append(
            Figure(
                value * multiplier, quantum * multiplier, percent, m.start(), m.end(), m.group(0)
            )
        )
    return found


def _same(figure: Figure, verified: Decimal) -> bool:
    rounded = (abs(verified) / figure.quantum).to_integral_value(rounding=ROUND_HALF_UP)
    return rounded * figure.quantum == figure.value


@dataclass(frozen=True)
class TextFix:
    field: str
    original: str
    replacement: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {
            "field": self.field,
            "original": self.original,
            "replacement": self.replacement,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Verified:
    """The figures prose may mention."""

    money: tuple[Decimal, ...]
    percents: tuple[Decimal, ...]
    corrections: tuple[tuple[Decimal, Decimal, bool], ...]  # (model, recomputed, percent)


def _render(value: Decimal, percent: bool) -> str:
    return f"{value}%" if percent else format_eur(value)


def check_text(field: str, text: str, verified: Verified) -> tuple[str, list[TextFix]]:
    fixes: list[TextFix] = []
    out: list[str] = []
    cursor = 0
    for fig in find_figures(text):
        pool = verified.percents if fig.percent else verified.money
        if any(_same(fig, v) for v in pool):
            continue
        replacement, reason = UNVERIFIED, "no verified figure matches"
        for model, recomputed, is_pct in verified.corrections:
            if is_pct == fig.percent and _same(fig, model):
                replacement = _render(recomputed, is_pct)
                reason = f"model figure {model} corrected to recomputed {recomputed}"
                break
        out.append(text[cursor : fig.start])
        out.append(replacement)
        cursor = fig.end
        fixes.append(TextFix(field, fig.text, replacement, reason))
    out.append(text[cursor:])
    return "".join(out), fixes


def verified_pool(
    money: Iterable[Decimal],
    percents: Iterable[Decimal],
    corrections: Iterable[tuple[Decimal, Decimal, bool]],
) -> Verified:
    return Verified(tuple(money), tuple(percents), tuple(corrections))
