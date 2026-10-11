"""Build the submission PDFs from their Markdown sources.

    uv run python scripts/build_docs.py

Each document in DOCUMENTS is converted to HTML with a print stylesheet, next to
its source, and printed to PDF by headless Chrome. Images are referenced
relative to docs/, so the HTML and the PDF show the same screenshots as the
repository. The Markdown stays the source. The PDFs are regenerated from it.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

DOCUMENTS = (
    (
        "technical_documentation.md",
        "technical_documentation",
        "LedgerGuard: technical documentation",
    ),
    (
        "SECURITY_SELF_ASSESSMENT.md",
        "security_self_assessment",
        "LedgerGuard: security self-assessment",
    ),
)

CSS = """
@page { size: A4; margin: 18mm 17mm 18mm 17mm; }
body { font-family: Inter, -apple-system, "Helvetica Neue", Arial, sans-serif; color: #0f172a;
       font-size: 10.2pt; line-height: 1.5; }
h1 { font-size: 21pt; margin: 0 0 4pt; letter-spacing: -0.01em; }
h1 + p { color: #475569; margin-top: 0; }
h2 { font-size: 13.5pt; margin: 18pt 0 6pt; padding-top: 6pt; border-top: 1px solid #e2e8f0;
     break-after: avoid; }
h3 { font-size: 11pt; margin: 12pt 0 4pt; break-after: avoid; }
p, li { orphans: 3; widows: 3; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 10pt; font-size: 9pt;
        break-inside: avoid; }
th, td { border: 1px solid #e2e8f0; padding: 4pt 6pt; vertical-align: top; text-align: left; }
th { background: #f8fafc; font-weight: 600; }
code { font-family: "JetBrains Mono", Menlo, monospace; font-size: 8.6pt; background: #f1f5f9;
       padding: 0 2pt; border-radius: 2pt; }
pre { background: #f8fafc; border: 1px solid #e2e8f0; padding: 6pt; font-size: 8.4pt;
      overflow: hidden; white-space: pre-wrap; }
img { max-width: 100%; border: 1px solid #e2e8f0; border-radius: 4pt; margin: 6pt 0;
      break-inside: avoid; }
.footer { color: #64748b; font-size: 8.5pt; margin-top: 24pt; border-top: 1px solid #e2e8f0;
          padding-top: 6pt; }
"""


def build(source: str, stem: str, title: str) -> Path:
    text = (DOCS / source).read_text(encoding="utf-8")
    if source == "SECURITY_SELF_ASSESSMENT.md":
        # The register is kept in its own file and appended here, so the two never drift.
        register = (DOCS / "RISK_REGISTER.md").read_text(encoding="utf-8")
        text += "\n" + register.split("\n", 1)[1]
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])
    html = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{title}</title><style>{CSS}</style></head><body>{body}"
        f"<p class='footer'>Built from docs/{source} on {date.today().isoformat()}. "
        "The repository holds the source, the code and the evidence.</p></body></html>"
    )
    html_path = DOCS / f"{stem}.html"
    html_path.write_text(html, encoding="utf-8")
    pdf_path = DOCS / f"{stem}.pdf"
    subprocess.run(
        [
            CHROME,
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={pdf_path}",
            html_path.as_uri(),
        ],
        check=True,
        capture_output=True,
    )
    return pdf_path


def print_pdf(html_path: Path, pdf_path: Path) -> Path:
    subprocess.run(
        [
            CHROME,
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={pdf_path}",
            html_path.as_uri(),
        ],
        check=True,
        capture_output=True,
    )
    return pdf_path


def main() -> int:
    for source, stem, title in DOCUMENTS:
        if (DOCS / source).exists():
            print(build(source, stem, title))
    # The deck is written as HTML slides directly, one 16:9 page per slide.
    print(print_pdf(DOCS / "deck.html", DOCS / "deck.pdf"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
