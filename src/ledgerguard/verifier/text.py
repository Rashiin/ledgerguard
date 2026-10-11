"""Figures inside free text.

The model writes claim statements and a rationale, and that prose can carry
numbers. Every money-like number in that prose must equal a figure the
verifier has itself computed:

* a number that matches a model figure the verifier **corrected** is replaced
  with the recomputed value, and the correction is recorded;
* a number that matches a verified figure is left alone;
* anything else is masked with a visible marker.

No unchecked amount reaches the user, even inside a sentence.

Matching respects the precision the text shows. "€46.9k" matches 46,854.00
and "34.8%" matches 34.81. Dates, row ids such as inv_0412, years and small
counts such as "week 7" or "26 days" are not treated as figures.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from ledgerguard.money import format_eur

UNVERIFIED = "[figure removed: not verified]"

# Currency marks the pattern recognises, and the ISO code each one means.
CURRENCY_MARKS = {
    "€": "EUR",
    "$": "USD",
    "£": "GBP",
    "¥": "JPY",
    "EUR": "EUR",
    "USD": "USD",
    "GBP": "GBP",
    "JPY": "JPY",
    "CHF": "CHF",
    "euro": "EUR",
    "euros": "EUR",
    "dollar": "USD",
    "dollars": "USD",
    "pound": "GBP",
    "pounds": "GBP",
}

_NUMBER = re.compile(
    r"""
    (?<![\w.\-/])                       # not inside an id, a date or another number
    (?P<cur>(?:[€$£¥]|EUR|USD|GBP|JPY|CHF)\s?)?
    (?P<sign>[-−])?
    (?P<int>\d{1,3}(?:,\d{3})+|\d+)
    (?P<frac>\.\d+)?
    (?P<suffix>\s?[kKmM](?![a-zA-Z]))?
    (?P<pct>\s?%)?
    (?P<post>\s?(?:EUR|USD|GBP|JPY|CHF|euros?|dollars?|pounds?)\b)?
    (?![\w\-/]|\.\d)                    # not followed by more id or date characters
    """,
    re.VERBOSE,
)
# A foreign currency named anywhere, with or without a number next to it. "Pound"
# alone is left out because it is also a unit of weight.
_FOREIGN_WORDS = re.compile(r"[$£¥]|\b(?:USD|GBP|JPY|CHF|dollars?)\b")


@dataclass(frozen=True)
class Figure:
    value: Decimal  # absolute value
    quantum: Decimal  # the precision the text shows
    percent: bool
    start: int
    end: int
    text: str
    currency: str | None  # ISO code of the mark written next to it, if any


def find_figures(text: str) -> list[Figure]:
    found = []
    for m in _NUMBER.finditer(text):
        digits = m["int"].replace(",", "")
        frac = m["frac"] or ""
        mark = (m["cur"] or m["post"] or "").strip()
        currency = CURRENCY_MARKS.get(mark) or CURRENCY_MARKS.get(mark.lower())
        money = currency is not None
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
                value * multiplier,
                quantum * multiplier,
                percent,
                m.start(),
                m.end(),
                m.group(0),
                currency,
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
    # Verified numbers that are neither money nor a percentage, such as days.
    plain: tuple[Decimal, ...]
    # Each correction is the model's figure, the recomputed figure and whether it is a percent.
    corrections: tuple[tuple[Decimal, Decimal, bool], ...]


def _render(value: Decimal, percent: bool) -> str:
    return f"{value}%" if percent else format_eur(value)


def foreign_currency(text: str, currency: str) -> bool:
    """True if the text names a currency other than the ledger's."""
    return any(
        CURRENCY_MARKS.get(m.group(0), m.group(0)) != currency
        for m in _FOREIGN_WORDS.finditer(text)
    )


def check_text(
    field: str,
    text: str,
    verified: Verified,
    currency: str = "EUR",
    own: tuple[Decimal, Decimal, bool] | None = None,
) -> tuple[str, list[TextFix]]:
    """Check every figure in ``text``. ``currency`` is the ledger's ISO code.

    A figure written in another currency is a finding in its own right. If the
    amount matches a verified figure it is shown again in the ledger's currency
    and the change is recorded. Otherwise it is masked with the rest.

    ``own`` is the correction for the claim this text belongs to. It applies
    before the pool is consulted: a claim that cites cash on hand but quotes week
    5's closing has quoted a true number for the wrong thing.
    """
    fixes: list[TextFix] = []
    out: list[str] = []
    cursor = 0
    for fig in find_figures(text):
        foreign = fig.currency is not None and fig.currency != currency
        if own is not None and own[2] == fig.percent and _same(fig, own[0]):
            replacement = _render(own[1], own[2])
            out.append(text[cursor : fig.start])
            out.append(replacement)
            cursor = fig.end
            reason = f"model figure {own[0]} corrected to recomputed {own[1]}"
            fixes.append(TextFix(field, fig.text, replacement, reason))
            continue
        if fig.percent:
            pool = verified.percents
        elif fig.currency is None:
            pool = verified.money + verified.plain
        else:
            pool = verified.money
        match = next((v for v in pool if _same(fig, v)), None)
        if match is not None and not foreign:
            continue
        if match is not None:
            replacement = _render(match, fig.percent)
            reason = (
                f"currency: {fig.currency} written for a {currency} amount, shown in {currency}"
            )
        else:
            replacement, reason = UNVERIFIED, "no verified figure matches"
            for model, recomputed, is_pct in verified.corrections:
                if is_pct == fig.percent and _same(fig, model):
                    replacement = _render(recomputed, is_pct)
                    reason = f"model figure {model} corrected to recomputed {recomputed}"
                    break
            if foreign:
                reason = f"currency: {fig.currency} written in a {currency} ledger, and " + reason
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
    plain: Iterable[Decimal] = (),
) -> Verified:
    return Verified(tuple(money), tuple(percents), tuple(plain), tuple(corrections))
