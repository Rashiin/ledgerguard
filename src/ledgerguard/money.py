"""Money handling.

Every monetary value in LedgerGuard is a ``Decimal`` with exactly two decimal
places. Storage is integer minor units (cents). ``float`` is rejected at every
entry point: there is no code path that turns a float into money.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")


class MoneyError(ValueError):
    """Raised when a value cannot be represented exactly as money."""


def to_money(value: object) -> Decimal:
    """Parse ``value`` into a two-place Decimal.

    Accepts ``Decimal`` and ``str`` only. Integers are rejected because
    ``5`` is ambiguous between five euros and five cents; floats are rejected
    because they cannot represent most cent amounts exactly. Sub-cent
    precision is an error, not something to round away silently.
    """
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, str):
        try:
            amount = Decimal(value.strip())
        except InvalidOperation as exc:
            raise MoneyError(f"not a decimal amount: {value!r}") from exc
    else:
        raise MoneyError(f"money must be Decimal or str, got {type(value).__name__}")
    if not amount.is_finite():
        raise MoneyError(f"money must be finite, got {value!r}")
    if amount != amount.quantize(CENT):
        raise MoneyError(f"sub-cent precision is not allowed: {value!r}")
    return amount.quantize(CENT)


def round_money(amount: Decimal) -> Decimal:
    """Round a computed Decimal (for example a tax or interest result) to cents."""
    if not isinstance(amount, Decimal):
        raise MoneyError(f"round_money takes Decimal, got {type(amount).__name__}")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def to_cents(amount: Decimal) -> int:
    """Convert money to integer cents for storage."""
    return int(to_money(amount).scaleb(2))


def from_cents(cents: int) -> Decimal:
    """Convert stored integer cents back to money."""
    if not isinstance(cents, int) or isinstance(cents, bool):
        raise MoneyError(f"cents must be int, got {type(cents).__name__}")
    return Decimal(cents).scaleb(-2).quantize(CENT)


def format_eur(amount: Decimal) -> str:
    """Human-readable euro string, e.g. ``€-3,180.00``."""
    return f"€{to_money(amount):,.2f}"
