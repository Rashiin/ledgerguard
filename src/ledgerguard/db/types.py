"""Column types that keep money exact and stored JSON canonical."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Integer, String, Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from ledgerguard.money import MoneyError, from_cents, to_cents


class Money(TypeDecorator[Decimal]):
    """Decimal euros in Python, integer cents in SQLite."""

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> int | None:
        if value is None:
            return None
        if not isinstance(value, Decimal):
            raise MoneyError(f"money columns take Decimal, got {type(value).__name__}")
        return to_cents(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        return from_cents(int(value))


class DecimalText(TypeDecorator[Decimal]):
    """Decimals that are not money, such as rates, stored as exact text and never as REAL."""

    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError(f"decimal columns take a finite Decimal, got {value!r}")
        return str(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        return None if value is None else Decimal(value)


def _reject_float(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"cannot store {type(value).__name__} in canonical JSON")


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, Decimals as strings, floats refused."""
    _assert_no_float(value)
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_reject_float
    )


def _assert_no_float(value: Any) -> None:
    if isinstance(value, float):
        raise TypeError("float values are not allowed in stored JSON")
    if isinstance(value, dict):
        for item in value.values():
            _assert_no_float(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _assert_no_float(item)


class CanonicalJSON(TypeDecorator[Any]):
    """JSON stored as canonical text; numbers with a fraction come back as Decimal."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        return None if value is None else canonical_json(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        return None if value is None else json.loads(value, parse_float=Decimal)


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC timestamps stored as ISO-8601 text."""

    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(UTC).isoformat(timespec="microseconds")

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        return None if value is None else datetime.fromisoformat(value)
