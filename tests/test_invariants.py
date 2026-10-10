"""BUILD_SPEC 9.3: invariants that must hold, and where each one is proven.

* The Executor rejects anything that is not a verifier-sealed VerifiedAction:
  test_executor.py (not an action, unsealed, forged, tampered, mutated payload).
* No runtime ledger write outside the Executor: test_write_paths.py (the
  read-only session refuses writes; engines exist only in named files; the
  running app never loads provisioning).
* A BLOCK can never be overridden by any approval:
  test_executor.py::test_block_cannot_be_overridden_by_any_approval and
  test_pipeline.py (scenario 9, forged queue entry).
* INITIATE_PAYMENT and ACCEPT_FINANCING_OFFER are always T3:
  test_rules.py::test_payment_and_offer_acceptance_are_always_t3 and
  test_executor.py::test_payment_and_financing_acceptance_must_be_t3.
* Audit verification fails if any event is mutated:
  test_audit.py::test_tampering_breaks_the_chain.
* No float in any monetary code path: this file.
"""

from __future__ import annotations

import ast
from pathlib import Path

from .conftest import ROOT

SRC = ROOT / "src" / "ledgerguard"
# The one float in the codebase is the LLM sampling temperature in config.py. It is
# not money; _allowed() admits exactly that line.


def _float_uses(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    # ``isinstance(x, float)`` is how floats are refused; that use is allowed.
    refusals = {
        id(arg)
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "isinstance"
        for arg in ast.walk(call.args[1])
    }
    found: list[str] = []
    for node in ast.walk(tree):
        if id(node) in refusals:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            found.append(f"line {node.lineno}: float literal {node.value!r}")
        elif isinstance(node, ast.Name) and node.id == "float":
            found.append(f"line {node.lineno}: name 'float'")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("round", "pow")
        ):
            found.append(f"line {node.lineno}: {node.func.id}() (float-prone)")
    return found


def _allowed(rel: str, line: str, path: Path) -> bool:
    if rel != "config.py":
        return False
    lineno = int(line.split()[1].rstrip(":"))
    return "temperature" in path.read_text().splitlines()[lineno - 1]


def test_no_float_anywhere_in_the_package_except_the_sampling_temperature() -> None:
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        rel = str(path.relative_to(SRC))
        for use in _float_uses(path):
            if not _allowed(rel, use, path):
                offenders.append(f"{rel} {use}")
    assert offenders == []


def test_the_scan_catches_floats(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text(
        "a = 0.1\nb = float('1')\nc: float = 1\nd = round(x)\nok = isinstance(v, float)\n"
    )
    assert len(_float_uses(sample)) == 4
