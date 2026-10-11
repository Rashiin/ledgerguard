"""Build the demo video from the captured screens.

    uv run python scripts/build_video.py

Each scene is a real screenshot from docs/media, captured from the running app
by scripts/capture_screens.mjs, with a caption bar. Chrome renders each scene
to a frame and ffmpeg joins them, so the video is scripted and has no live
typing. The scene list below is the demo script.
"""

from __future__ import annotations

import html
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MEDIA = ROOT / "docs" / "media"
OUT = ROOT / "docs" / "demo.mp4"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
W, H = 1920, 1200

# (image or None for a title card, seconds, heading, caption)
SCENES: list[tuple[str | None, int, str, str]] = [
    (
        None,
        7,
        "LedgerGuard",
        "An SME finance copilot in which the language model never acts directly. "
        "It proposes. Deterministic code recomputes, checks and decides.",
    ),
    (
        "architecture.svg",
        12,
        "Two planes",
        "The model drafts a typed action. The verifier recomputes every "
        "figure, runs eight rules and sets the tier. Only the Executor writes, and only sealed actions.",
    ),
    (
        "00_console_catalogue.png",
        8,
        "The demo, offline",
        "Every request has a recorded model response. "
        "The verifier, the rules and the ledger run live.",
    ),
    (
        "02_week7_omission_put_back.png",
        15,
        "What must be said",
        "Asked why week 7 is bad, the model listed "
        "the payables and left out the shortfall. The verifier put it back first, in its own words.",
    ),
    (
        "03_payroll_protection_block.png",
        18,
        "A T3 payment, blocked",
        "The rules run on the draft first. "
        "Paying would leave payroll €1,031.43 short. The block cannot be approved away.",
    ),
    (
        "04_trust_receipt_rules.png",
        10,
        "The Trust Receipt",
        "Rows read, every rule and its result, the tier and the flags, for every turn.",
    ),
    (
        "05_injection_contained.png",
        16,
        "Injection contained",
        "A supplier invoice tells the assistant to pay "
        "a new account. The text is removed before the model reads it, logged, and shown to the owner.",
    ),
    (
        "06_attached_memo_contained.png",
        10,
        "A poisoned memo",
        "An attached PDF memo asks to ignore limits and "
        "pay everyone. It is outside content. The owner's words alone decide what may happen.",
    ),
    (
        "07_accept_financing_t3_cost_stated.png",
        12,
        "Accepting financing",
        "The outbound ceiling blocked "
        "€220,000. At €150,000 the verifier states the cost being confirmed: €3,012.75.",
    ),
    (
        "09_inbox_diff.png",
        12,
        "Approval with the exact diff",
        "The diff comes from the Executor's own code, "
        "run and rolled back. Approving re-runs every rule.",
    ),
    (
        "10_inbox_t3_confirmation.png",
        10,
        "T3 needs a person, a phrase and a pause",
        "A typed confirmation after a 60-second cool-off.",
    ),
    (
        "11_radar_projection.png",
        10,
        "Cash-Flow Radar",
        "Contracted, expected and modelled lines are never "
        "merged. Alerts name the rows behind them.",
    ),
    (
        "12_radar_scenario.png",
        10,
        "What if",
        "Brennan pays 30 days late: the low deepens and new alerts appear. "
        "Nothing in the ledger changes.",
    ),
    (
        "13_radar_financing.png",
        10,
        "Financing by total cost",
        "Every option in euros over the real drawdown "
        "period, with a do-nothing line. The catalogue is synthetic.",
    ),
    (
        "16_wrong_total_masked.png",
        15,
        "A wrong total, masked",
        "Sampled at a higher temperature, the model "
        "added the invoices up to €124,147.48. The payment is €98,970.61. The wrong figure never reaches the owner.",
    ),
    (
        "17a_correction_before_fix.png",
        12,
        "The verifier's own mistake",
        "First time recomputation fired on real output, it wrote one invoice's €35,973.77 into a sentence about three invoices. The safety layer made the sentence false.",
    ),
    (
        "17_figure_corrected.png",
        15,
        "After the fix: a correction is a marker",
        "The model's €106,954.71 is struck through, the cited figure is stated in a marker, "
        "and nothing is put in the model's mouth. A regression test reproduces the case.",
    ),
    (
        "14_audit_chain_intact.png",
        12,
        "The audit chain verifies",
        "Every event is hashed with the one before. Changing any stored event breaks the chain.",
    ),
    (
        "15_audit_trail_answer.png",
        10,
        "Asking for the trail",
        "The answer states the live chain, computed when it is shown.",
    ),
    (
        None,
        8,
        "LedgerGuard",
        "Runs on the owner's own machine. Every figure recomputed. Every action gated. "
        "Every event chained.",
    ),
]

PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html, body {{ margin: 0; width: {w}px; height: {h}px; overflow: hidden; background: {bg};
  font-family: Inter, -apple-system, "Helvetica Neue", Arial, sans-serif; }}
.frame {{ position: absolute; inset: 0 0 190px 0; display: flex; align-items: center; justify-content: center;
  background: #e2e8f0; }}
.frame img {{ max-width: 96%; max-height: 94%; border-radius: 10px; box-shadow: 0 6px 30px rgba(0,0,0,0.18);
  background: white; }}
.bar {{ position: absolute; left: 0; right: 0; bottom: 0; height: 190px; background: #0f172a; color: #f8fafc;
  padding: 26px 56px; box-sizing: border-box; }}
.bar h2 {{ margin: 0 0 10px; font-size: 38px; letter-spacing: -0.01em; }}
.bar p {{ margin: 0; font-size: 27px; line-height: 1.35; color: #cbd5e1; }}
.title {{ position: absolute; inset: 0; display: flex; flex-direction: column; justify-content: center;
  padding: 0 140px; color: #f8fafc; }}
.title h1 {{ font-size: 110px; margin: 0 0 30px; letter-spacing: -0.02em; }}
.title p {{ font-size: 40px; line-height: 1.4; color: #cbd5e1; max-width: 1500px; margin: 0; }}
</style></head><body>{body}</body></html>"""


def frame(image: str | None, heading: str, caption: str, out: Path, work: Path) -> None:
    if image is None:
        body = (
            f'<div class="title"><h1>{html.escape(heading)}</h1><p>{html.escape(caption)}</p></div>'
        )
        bg = "#0f172a"
    else:
        body = (
            f'<div class="frame"><img src="{(MEDIA / image).as_uri()}"></div>'
            f'<div class="bar"><h2>{html.escape(heading)}</h2><p>{html.escape(caption)}</p></div>'
        )
        bg = "#e2e8f0"
    page = work / f"{out.stem}.html"
    page.write_text(PAGE.format(w=W, h=H, bg=bg, body=body), encoding="utf-8")
    subprocess.run(
        [
            CHROME,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--allow-file-access-from-files",
            f"--window-size={W},{H}",
            f"--screenshot={out}",
            page.as_uri(),
        ],
        check=True,
        capture_output=True,
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        lines = []
        for n, (image, seconds, heading, caption) in enumerate(SCENES):
            png = work / f"scene_{n:02d}.png"
            frame(image, heading, caption, png, work)
            lines += [f"file '{png}'", f"duration {seconds}"]
        lines.append(f"file '{work / f'scene_{len(SCENES) - 1:02d}.png'}'")
        listing = work / "scenes.txt"
        listing.write_text("\n".join(lines) + "\n")
        subprocess.run(
            [
                FFMPEG,
                "-y",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-vf",
                f"scale={W}:{H},format=yuv420p,fps=30",
                "-c:v",
                "libx264",
                "-crf",
                "22",
                "-movflags",
                "+faststart",
                str(OUT),
            ],
            check=True,
        )
    total = sum(s[1] for s in SCENES)
    print(f"{OUT} {total} seconds, {len(SCENES)} scenes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
