"""The planted demo situations, checked against the seeded database."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerguard.analytics.cashflow import project
from ledgerguard.analytics.sources import projection_flows
from ledgerguard.db.engine import make_readonly_sessionmaker
from ledgerguard.db.models import (
    Account,
    Business,
    Counterparty,
    FinancingProduct,
    Invoice,
    Obligation,
    PayrollRun,
    Transaction,
)
from ledgerguard.domain.enums import (
    AccountType,
    Confidence,
    Direction,
    InvoiceStatus,
    InvoiceType,
    PayrollStatus,
)
from ledgerguard.seed.nordvik import SeedResult

from .conftest import ROOT


@pytest.fixture(scope="module")
def session(seeded_db: Path) -> Session:
    return make_readonly_sessionmaker(seeded_db)()


def _all(session: Session, model: type) -> list:  # type: ignore[type-arg]
    return list(session.scalars(select(model)))


def _projection(session: Session) -> list:  # type: ignore[type-arg]
    business = session.scalars(select(Business)).one()
    txns = _all(session, Transaction)
    cash = Decimal("0.00")
    for acc in _all(session, Account):
        if acc.type is AccountType.CREDIT_LINE:
            continue
        cash += acc.opening_balance
        for t in txns:
            if t.account_id == acc.id and t.booked_on <= business.data_as_of:
                cash += t.amount if t.direction is Direction.IN else -t.amount
    flows = projection_flows(
        business.data_as_of,
        13,
        _all(session, Invoice),
        txns,
        _all(session, PayrollRun),
        _all(session, Obligation),
    )
    return project(business.data_as_of, cash, flows)


def test_seed_is_byte_identical(tmp_path: Path) -> None:
    from scripts.seed import main

    # Three runs in one process: the second run is where reflection-order bugs in
    # migrations used to show up.
    digests = []
    for run in ("a", "b", "c"):
        db = tmp_path / run / "ledger.sqlite"
        assert (
            main(
                [
                    "--seed",
                    "42",
                    "--db",
                    str(db),
                    "--manifest",
                    str(tmp_path / run / "manifest.json"),
                ]
            )
            == 0
        )
        digests.append(hashlib.sha256(db.read_bytes()).hexdigest())
    assert digests[0] == digests[1] == digests[2]


def test_different_seed_gives_different_ledger(tmp_path: Path) -> None:
    from scripts.seed import main

    digests = []
    for seed in ("42", "7"):
        db = tmp_path / seed / "ledger.sqlite"
        main(["--seed", seed, "--db", str(db), "--manifest", str(tmp_path / seed / "m.json")])
        digests.append(hashlib.sha256(db.read_bytes()).hexdigest())
    assert digests[0] != digests[1]


def test_shortfall_in_weeks_6_to_8_only(session: Session) -> None:
    weeks = _projection(session)
    assert len(weeks) == 13
    negative = [w.index for w in weeks if w.closing < 0]
    assert negative == [6, 7, 8]
    worst = min(weeks, key=lambda w: w.closing)
    assert worst.index == 7


def test_projection_matches_manifest(session: Session, seed_result: SeedResult) -> None:
    weeks = _projection(session)
    m = seed_result.manifest
    assert [str(w.closing) for w in weeks] == m.weekly_closing
    assert [str(w.closing_with_expected) for w in weeks] == m.weekly_closing_with_expected
    assert [str(w.closing_contracted) for w in weeks] == m.weekly_closing_contracted


def test_all_three_confidence_bands_are_present_and_separate(session: Session) -> None:
    weeks = _projection(session)
    classes = {f.confidence for w in weeks for f in w.flows}
    assert classes == set(Confidence)
    for w in weeks:
        for f in w.flows:
            assert f.amount > 0
        assert w.total_inflow == sum(
            (f.amount for f in w.flows if f.direction is Direction.IN), Decimal(0)
        )
    # The nested balances only coincide when a week has no lines of the outer class.
    assert any(w.closing_contracted != w.closing for w in weeks)


def test_speculative_lines_cite_last_years_invoice(session: Session) -> None:
    for w in _projection(session):
        for f in w.flows:
            if f.confidence is Confidence.SPECULATIVE:
                source = session.get(Invoice, f.source_id)
                assert source is not None and source.status is InvoiceStatus.PAID
                assert (f.on - source.due_on).days >= 364


def test_payroll_run_inside_shortfall_window(session: Session, seed_result: SeedResult) -> None:
    weeks = {w.index: w for w in _projection(session)}
    run = session.get(PayrollRun, seed_result.manifest.payroll_in_shortfall)
    assert run is not None and run.status is PayrollStatus.SCHEDULED
    assert weeks[6].starts_on <= run.pay_date <= weeks[8].ends_on


def test_one_customer_above_thirty_percent(session: Session) -> None:
    as_of = session.scalars(select(Business)).one().data_as_of
    since = as_of - timedelta(days=365)
    revenue: dict[str, Decimal] = defaultdict(Decimal)
    for inv in _all(session, Invoice):
        if inv.type is InvoiceType.RECEIVABLE and since < inv.issued_on <= as_of:
            revenue[inv.counterparty_id] += inv.net_amount
    total = sum(revenue.values(), Decimal(0))
    shares = {cp: amount / total for cp, amount in revenue.items()}
    above = [cp for cp, share in shares.items() if share > Decimal("0.30")]
    assert above == ["cp_brennan"]
    assert shares["cp_brennan"] < Decimal("0.45")


def test_three_overdue_and_one_disputed_receivable(
    session: Session, seed_result: SeedResult
) -> None:
    receivables = [i for i in _all(session, Invoice) if i.type is InvoiceType.RECEIVABLE]
    overdue = sorted(i.id for i in receivables if i.status is InvoiceStatus.OVERDUE)
    disputed = [i.id for i in receivables if i.status is InvoiceStatus.DISPUTED]
    assert overdue == seed_result.manifest.overdue_receivables
    assert len(overdue) == 3
    assert disputed == [seed_result.manifest.disputed_receivable]
    as_of = seed_result.manifest.as_of
    past_due_open = [
        i
        for i in receivables
        if i.due_on < as_of and i.status not in (InvoiceStatus.PAID, InvoiceStatus.DRAFT)
    ]
    assert len(past_due_open) == 4


def test_invoice_0412_is_open_brennan_receivable(session: Session) -> None:
    inv = session.get(Invoice, "inv_0412")
    assert inv is not None
    assert inv.counterparty_id == "cp_brennan"
    assert inv.type is InvoiceType.RECEIVABLE
    assert inv.status is InvoiceStatus.OVERDUE


def test_duplicate_supplier_invoice_pair(session: Session, seed_result: SeedResult) -> None:
    legit_id, dup_id = seed_result.manifest.duplicate_pair
    assert dup_id == "inv_0377"
    legit, dup = session.get(Invoice, legit_id), session.get(Invoice, dup_id)
    assert legit is not None and dup is not None
    assert legit.type is dup.type is InvoiceType.PAYABLE
    assert legit.counterparty_id == dup.counterparty_id
    assert legit.gross_amount == dup.gross_amount
    assert (dup.issued_on - legit.issued_on).days == 3
    assert legit.status is InvoiceStatus.PAID
    assert dup.status is InvoiceStatus.OVERDUE
    payment = session.scalars(select(Transaction).where(Transaction.invoice_id == legit_id)).one()
    assert payment.amount == legit.gross_amount
    # Paying the duplicate on the as-of date falls inside the 14-day duplicate window.
    assert (seed_result.manifest.as_of - payment.booked_on).days <= 14
    others = [
        i
        for i in _all(session, Invoice)
        if i.type is InvoiceType.PAYABLE
        and i.gross_amount == legit.gross_amount
        and i.id not in (legit_id, dup_id)
    ]
    assert others == []


def _mad_z(values: list[Decimal], x: Decimal) -> Decimal:
    ordered = sorted(values)
    n = len(ordered)
    median = (ordered[(n - 1) // 2] + ordered[n // 2]) / 2
    deviations = sorted(abs(v - median) for v in ordered)
    mad = (deviations[(n - 1) // 2] + deviations[n // 2]) / 2
    return (x - median) / (Decimal("1.4826") * mad)


def test_single_anomalous_transaction_near_eight_sigma(
    session: Session, seed_result: SeedResult
) -> None:
    groups: dict[tuple[str, str, str], list[Transaction]] = defaultdict(list)
    for t in _all(session, Transaction):
        groups[(t.direction.value, t.category, t.counterparty_id)].append(t)
    flagged = []
    for txns in groups.values():
        if len(txns) < 12:
            continue
        amounts = [t.amount for t in txns]
        if len(set(amounts)) < len(amounts) // 2:
            continue  # fixed contract amounts such as rent and the loan have nothing to score
        for t in txns:
            z = _mad_z(amounts, t.amount)
            if abs(z) > 5:
                flagged.append((t.id, z))
    assert [f[0] for f in flagged] == [seed_result.manifest.anomalous_transaction]
    assert Decimal("7.5") <= flagged[0][1] <= Decimal("8.5")


def test_financing_catalogue_is_synthetic(session: Session) -> None:
    products = _all(session, FinancingProduct)
    assert len(products) == 12
    assert len({p.kind for p in products}) == 5
    assert all(p.provider.startswith("Synthetic ") for p in products)


def test_counterparties_whitelisting(session: Session) -> None:
    for cp in _all(session, Counterparty):
        assert cp.is_whitelisted is (cp.type.value != "customer")


def test_money_columns_hold_decimals(session: Session) -> None:
    inv = session.get(Invoice, "inv_0412")
    assert inv is not None
    assert isinstance(inv.gross_amount, Decimal)
    assert inv.gross_amount == inv.net_amount + inv.tax_amount


def test_fixtures_readme_lists_every_hook(seed_result: SeedResult) -> None:
    readme = (ROOT / "fixtures" / "README.md").read_text()
    m = seed_result.manifest
    for value in (
        *m.overdue_receivables,
        m.disputed_receivable,
        *m.duplicate_pair,
        m.anomalous_transaction,
        m.payroll_in_shortfall,
        m.big_supplier_payable,
        m.brennan_refit_milestone,
        m.vat_obligation,
    ):
        assert value in readme, value


@pytest.mark.parametrize("seed", [1, 3, 7, 100, 2026])
def test_other_seeds_also_calibrate(seed: int) -> None:
    from ledgerguard.seed import nordvik

    from .conftest import CATALOGUE

    result = nordvik.build(seed, CATALOGUE)
    closings = [Decimal(c) for c in result.manifest.weekly_closing]
    assert [i for i, c in enumerate(closings, start=1) if c < 0] == [6, 7, 8]
