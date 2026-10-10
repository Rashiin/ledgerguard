from decimal import Decimal

import pytest

from ledgerguard.money import MoneyError, format_eur, from_cents, round_money, to_cents, to_money


def test_round_trip_through_cents() -> None:
    for text in ("0.00", "0.01", "46854.00", "-3180.55", "99999999.99"):
        assert from_cents(to_cents(to_money(text))) == Decimal(text)


@pytest.mark.parametrize("bad", [0.1, 1.0, 5, True, None, [1]])
def test_non_decimal_inputs_are_rejected(bad: object) -> None:
    with pytest.raises(MoneyError):
        to_money(bad)


@pytest.mark.parametrize("bad", ["1.005", "abc", "NaN", "Infinity", ""])
def test_unrepresentable_strings_are_rejected(bad: str) -> None:
    with pytest.raises(MoneyError):
        to_money(bad)


def test_from_cents_refuses_floats() -> None:
    with pytest.raises(MoneyError):
        from_cents(1.0)  # type: ignore[arg-type]


def test_round_money_rounds_half_up() -> None:
    assert round_money(Decimal("806.405")) == Decimal("806.41")
    assert round_money(Decimal("806.4049")) == Decimal("806.40")


def test_format() -> None:
    assert format_eur(Decimal("-3180.00")) == "-€3,180.00"
    assert format_eur(Decimal("46854")) == "€46,854.00"
