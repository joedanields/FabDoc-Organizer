"""Generate a full IFA/IFF package chain for exercising the tracker.

    python tools/make_chain_sample.py <output-folder>

Five approval rounds (rev A through E) followed by four fabrication releases
(IFF-0 to IFF-3), built to mirror the real package this tool was calibrated on:
marks encode job/sequence/zone, the mark sits unlabelled in the title block, and
the revision rides on the filename as ``17172C1  - Rev B.pdf``.

The chain is deliberately awkward in the ways real ones are:

* new members keep entering at rev A while existing ones climb, so no issue has
  one uniform revision letter;
* one member is dropped mid-approval, which must report as **removed**;
* the fabrication releases together cover only part of the approved scope, so
  members stay **on hold** to the end - that is the case the pairwise diff got
  wrong and the whole tracker exists to handle.

Nothing here is random: re-running produces byte-identical folders, so a
regression is a real change rather than a reshuffle.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import fitz

PAGE_W, PAGE_H = 842.0, 595.0

# Marks follow the calibrated convention: job 17, 3-digit sequence, then the
# member. The leading digit of the sequence is the zone, so 172/173 are zone 1
# and 270/271 are zone 2.
MARKS: list[str] = (
    [f"17172C{i}" for i in range(1, 13)]      # zone 1, seq 172
    + [f"17173R{i}" for i in range(1, 9)]     # zone 1, seq 173
    + [f"17270S{i}" for i in range(1, 11)]    # zone 2, seq 270
    + [f"17271X{i}" for i in range(1, 11)]    # zone 2, seq 271
)

DROPPED = MARKS[5]      # dropped at round C - must report as removed, not on hold

# (folder round, date, issue purpose, members present, how many climb a revision)
IFA_ROUNDS = [
    ("10", "2026-01-12", "Approval",    32, 0),
    ("20", "2026-02-09", "Re Approval", 36, 28),
    ("30", "2026-03-16", "Re Approval", 38, 24),
    ("40", "2026-04-13", "Re Approval", 40, 20),
    ("50", "2026-05-18", "Re Approval", 40, 16),
]

# Each release ships the next slice of the approved scope. They do not add up to
# the whole package: the balance stays on hold, which is the point.
IFF_RELEASES = [
    ("0", "2026-06-01", 0, 12),
    ("1", "2026-06-22", 12, 22),
    ("2", "2026-07-13", 22, 30),
    ("3", "2026-08-03", 30, 35),
]

TITLE = "Stairs at Zone 1 and Zone 2"
SEQS = "(Seqs 172,173,270,271)"


def make_drawing(path: Path, mark: str, revision: str) -> None:
    """A minimal sheet shaped like the real ones: big unlabelled mark, bottom right.

    The mark is picked up by the largest-text tier and the revision by the
    filename tier, which is exactly how the calibrated client's drawings read.
    Writing a labelled ``REVISION NO`` field would exercise a tier the real
    package never reaches.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.draw_rect(fitz.Rect(20, 20, PAGE_W - 20, PAGE_H - 20), color=(0, 0, 0), width=1)
    page.insert_text((40, 60), "ASSEMBLY DETAIL", fontsize=11)
    page.insert_text((40, 80), "SCALE 1:25", fontsize=9)

    block = fitz.Rect(PAGE_W * 0.58, PAGE_H * 0.62, PAGE_W - 20, PAGE_H - 20)
    page.draw_rect(block, color=(0, 0, 0), width=1)
    page.insert_text((block.x0 + 14, block.y1 - 46), mark, fontsize=26)
    page.insert_text((block.x0 + 14, block.y1 - 12), "DRAWN BY : AB   CHECKED : CD",
                     fontsize=8)
    doc.save(path)
    doc.close()


def _next_rev(rev: str) -> str:
    return chr(ord(rev) + 1) if rev < "E" else "E"


def build(base: Path) -> tuple[list[Path], dict[str, str]]:
    """Write every issue folder. Returns the folders and the approved scope."""
    written: list[Path] = []
    revs: dict[str, str] = {}

    for round_no, date, purpose, count, climbing in IFA_ROUNDS:
        present = [m for m in MARKS[:count] if not (round_no >= "30" and m == DROPPED)]
        for mark in present[:climbing]:
            revs[mark] = _next_rev(revs.get(mark, "A"))
        for mark in present:
            revs.setdefault(mark, "A")     # a member new this round enters at A

        folder = base / f"{round_no}. {date} {TITLE} for {purpose} {SEQS}"
        for mark in present:
            make_drawing(folder / "assembly" / f"{mark}  - Rev {revs[mark]}.pdf",
                         mark, revs[mark])
        written.append(folder)
        letter = IFA_ROUNDS[[r[0] for r in IFA_ROUNDS].index(round_no)][0]
        print(f"  IFA {round_no:<3} {date}  {len(present):>3} drawings  "
              f"{folder.name[:44]}")

    approved = {m: revs[m] for m in MARKS if m != DROPPED}
    order = list(approved)

    for round_no, date, start, end in IFF_RELEASES:
        slice_marks = order[start:end]
        folder = base / f"IFF-{round_no} {date} {TITLE} for Fabrication"
        for mark in slice_marks:
            make_drawing(folder / "assembly" / f"{mark}  - Rev {approved[mark]}.pdf",
                         mark, approved[mark])
        written.append(folder)
        print(f"  IFF {round_no:<3} {date}  {len(slice_marks):>3} drawings  "
              f"{folder.name[:44]}")

    return written, approved


def main(argv: list[str]) -> int:
    base = Path(argv[1]) if len(argv) > 1 else Path.cwd() / "chain-sample"
    print(f"Building the chain under {base}\n")
    folders, approved = build(base)

    model = base / "Model Member List.csv"
    with open(model, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Phase", "Assembly Mark", "Profile", "Weight (kg)"])
        for mark in approved:
            writer.writerow([1, mark, "ISMB 300", 250])

    released = sum(end - start for _, _, start, end in IFF_RELEASES)
    print(f"\nApproved scope : {len(approved)} members (rev A..E)")
    print(f"Dropped at C   : {DROPPED} - reports as removed, not on hold")
    print(f"Released       : {released} across {len(IFF_RELEASES)} fabrication releases")
    print(f"Never released : {len(approved) - released} - stay on hold to the end")
    print(f"Model list     : {model.name}")

    tracker = base / "Package Tracker.xlsx"
    print("\nTrack the whole chain:")
    for folder in folders:
        if folder.name.startswith("IFF-"):
            stage, rnd = "IFF", folder.name.split()[0].split("-")[1]
        else:
            stage, rnd = "IFA", folder.name.split(".")[0]
        print(f'  python -m fabdoc generate "{folder}" --track --stage {stage} '
              f'--round {rnd} --tracker "{tracker}" -q')
    print(f'\n  python -m fabdoc track "{tracker}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
