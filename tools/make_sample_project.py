"""Generate a sample drawing package for trying the app out.

    python tools/make_sample_project.py <output-folder>

Creates a three-category package with a metadata-rich folder name, a matching
model member list, and a few deliberately awkward drawings (missing revision,
unlabelled mark, a member absent from the model) so the validation report has
something to report.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import fitz

PAGE_W, PAGE_H = 842.0, 595.0

STRUCTURAL = ["C-101", "C-102", "C-103", "C-110", "C-111"]
ERECTION = ["E-201", "E-202", "E-203"]
PART = ["P-301", "P-302", "P-303", "P-304"]

# In the drawings but not the model - the validator must catch this.
ORPHAN = "P-999"
# In the model but never drawn - the validator must catch this too.
UNDRAWN = "C-500"


def make_drawing(path: Path, member: str, revision, seq: int,
                 labelled: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.draw_rect(fitz.Rect(20, 20, PAGE_W - 20, PAGE_H - 20), color=(0, 0, 0), width=1)
    page.insert_text((40, 60), f"MEMBER {member} - ELEVATION", fontsize=12)
    page.insert_text((40, 82), "SCALE 1:25", fontsize=9)
    page.insert_text((40, 104), "ALL DIMENSIONS IN MM", fontsize=9)

    block = fitz.Rect(PAGE_W * 0.58, PAGE_H * 0.62, PAGE_W - 20, PAGE_H - 20)
    page.draw_rect(block, color=(0, 0, 0), width=1)
    y = block.y0 + 26
    if labelled:
        page.insert_text((block.x0 + 12, y), f"ASSEMBLY MARK : {member}", fontsize=11)
        y += 24
        if revision is not None:
            page.insert_text((block.x0 + 12, y), f"REVISION NO : {revision}", fontsize=11)
            y += 24
        page.insert_text((block.x0 + 12, y), f"S.No : {seq}", fontsize=11)
    else:
        page.insert_text((block.x0 + 12, block.y0 + 60), member, fontsize=28)
    page.insert_text((block.x0 + 12, block.y1 - 10), "DRAWN BY : AB   CHECKED : CD", fontsize=8)
    doc.save(path)
    doc.close()


def main(argv: list[str]) -> int:
    base = Path(argv[1]) if len(argv) > 1 else Path.cwd() / "sample"
    root = base / "Skyline Tower - Zone B - PKG-03 - 12-05-2024"

    for i, member in enumerate(STRUCTURAL, start=1):
        # One drawing carries no revision field, to exercise the default.
        rev = None if member == "C-110" else i - 1
        make_drawing(root / "Structural Drawings" / f"{i:03d}_{member}.pdf", member, rev, i)

    for i, member in enumerate(ERECTION, start=1):
        # One drawing has an unlabelled mark, to exercise the largest-text tier.
        make_drawing(root / "Erection Drawings" / f"{i:03d}_{member}.pdf", member, "A", i,
                     labelled=(member != "E-203"))

    for i, member in enumerate(PART + [ORPHAN], start=1):
        make_drawing(root / "Part Drawings" / f"{i:03d}_{member}.pdf", member, 0, i)

    model = base / "Model Member List.csv"
    with open(model, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Phase", "Assembly Mark", "Profile", "Weight (kg)"])
        for member in STRUCTURAL + ERECTION + PART + [UNDRAWN]:
            writer.writerow([1, member, "ISMB 300", 250])

    total = len(STRUCTURAL) + len(ERECTION) + len(PART) + 1
    print(f"Sample package: {root}")
    print(f"  {total} drawings across 3 categories")
    print(f"Model member list: {model}")
    print(f"  {ORPHAN} is drawn but not modelled; {UNDRAWN} is modelled but not drawn.")
    print()
    print("Try it:")
    print(f'  python -m fabdoc generate "{root}"')
    print(f'  python -m fabdoc validate "{root}" "{model}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
