"""VAT for the sandbox business (Ireland, standard rate)."""

from __future__ import annotations

from decimal import Decimal

from ledgerguard.money import round_money

STANDARD_RATE = Decimal("0.23")


def vat_on(net: Decimal) -> Decimal:
    return round_money(net * STANDARD_RATE)
