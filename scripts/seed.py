"""Create the sandbox ledger.

    uv run python scripts/seed.py --profile nordvik --seed 42

Deletes and recreates the database file, runs migrations, loads the synthetic
rows, and writes a manifest of the planted demo hooks. Output is identical
for identical arguments.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

from ledgerguard.provisioning import create_fresh
from ledgerguard.seed import nordvik

PROFILES = {"nordvik": nordvik.build}
ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", choices=sorted(PROFILES), default="nordvik")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite")
    parser.add_argument("--manifest", type=Path, default=ROOT / "fixtures" / "manifest.json")
    args = parser.parse_args(argv)

    result = PROFILES[args.profile](args.seed, ROOT / "fixtures" / "financing_products.json")
    create_fresh(args.db, result.rows)

    manifest = {"profile": args.profile, "seed": args.seed, **asdict(result.manifest)}
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")

    digest = hashlib.sha256(args.db.read_bytes()).hexdigest()
    print(f"seeded {args.profile} (seed {args.seed}) -> {args.db}")
    print(f"rows: {len(result.rows)}  sha256: {digest}")
    print(f"manifest -> {args.manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
