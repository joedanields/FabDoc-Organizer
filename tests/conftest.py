"""Shared fixtures: synthetic drawing packages built with PyMuPDF.

The generated PDFs imitate a typical detailing title block - labelled fields in
the bottom-right corner plus a large unlabelled mark - so the extraction cascade
is exercised for real rather than mocked.
"""

from __future__ import annotations

import sys
from pathlib import Path

import fitz
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PAGE_W, PAGE_H = 842.0, 595.0  # A4 landscape, points


def make_drawing(
    path: Path,
    member: str,
    revision: str | int = 0,
    seq: int | None = None,
    labelled: bool = True,
    big_mark: bool = False,
    blank: bool = False,
    qty: str = "",
    length: str = "",
) -> Path:
    """Write a single-page PDF that looks like a steel detail drawing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)

    if not blank:
        page.draw_rect(fitz.Rect(20, 20, PAGE_W - 20, PAGE_H - 20), color=(0, 0, 0), width=1)
        page.insert_text((40, 60), "SECTION A-A", fontsize=10)
        page.insert_text((40, 80), "SCALE 1:25", fontsize=9)

        # Title block, bottom-right quadrant.
        block = fitz.Rect(PAGE_W * 0.58, PAGE_H * 0.62, PAGE_W - 20, PAGE_H - 20)
        page.draw_rect(block, color=(0, 0, 0), width=1)
        y = block.y0 + 24
        if labelled:
            page.insert_text((block.x0 + 12, y), f"ASSEMBLY MARK : {member}", fontsize=11)
            y += 22
            page.insert_text((block.x0 + 12, y), f"REVISION NO : {revision}", fontsize=11)
            y += 22
            if seq is not None:
                page.insert_text((block.x0 + 12, y), f"S.No : {seq}", fontsize=11)
                y += 22
        # The fabrication table: headings in one row, the values in the row
        # beneath them. Written as real text at real coordinates, because that
        # column alignment is the whole of how the two are found.
        if qty or length:
            x = block.x0 + 12
            head_y = block.y1 - 52
            for label, value in (("Qty", qty), ("Profile", "L3X3X3/16"),
                                 ("Length", length)):
                page.insert_text((x, head_y), label, fontsize=9)
                if value:
                    page.insert_text((x, head_y + 13), value, fontsize=9)
                x += 80

        if big_mark:
            page.insert_text((block.x0 + 12, block.y1 - 20), member, fontsize=26)
        page.insert_text((block.x0 + 12, block.y1 - 4), "DRAWN BY : AB", fontsize=8)

    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def project_folder(tmp_path: Path) -> Path:
    """A three-category package with a metadata-rich folder name."""
    root = tmp_path / "Skyline Tower - Zone B - PKG-03 - 12-05-2024"

    structural = root / "Structural Drawings"
    for i, member in enumerate(["C-101", "C-102", "C-110"], start=1):
        make_drawing(structural / f"{i:03d}_{member}.pdf", member, revision=i - 1, seq=i)

    erection = root / "Erection Drawings"
    for i, member in enumerate(["E-201", "E-202"], start=1):
        make_drawing(erection / f"{i:03d}_{member}.pdf", member, revision="A", seq=i)

    part = root / "Part Drawings"
    for i, member in enumerate(["P-301", "P-302"], start=1):
        make_drawing(part / f"{i:03d}_{member}.pdf", member, revision=0, seq=i)

    return root


@pytest.fixture
def single_category_folder(tmp_path: Path) -> Path:
    """One category only - the workbook must still come out with one sheet."""
    root = tmp_path / "Bridge Deck_Zone 4_2024-11-02"
    for i, member in enumerate(["B1", "B2"], start=1):
        make_drawing(root / "Erection" / f"{i}_{member}.pdf", member, revision=1, seq=i)
    return root
