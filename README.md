# FabDoc Organizer

Automated drawing register generation and member validation for structural steel
fabrication packages.

Point it at an issue folder. It reads the project metadata out of the folder
name, finds the drawing categories inside it, extracts the sequence number,
member name and revision from every PDF, and writes one Excel workbook with a
worksheet per category. Then it compares that register against the member list
exported from the structural model and reports what is missing, what is extra,
and what matched.

## Install

Requires Python 3.10 or later.
```
pip install -r requirements.txt
```

Or install as a package, which also puts `fabdoc` on your PATH:

```
pip install -e .
```

## Run

**Desktop app** - double-click `FabDoc Organizer.bat`, or:

```
python -m fabdoc
```

**Command line** - any argument switches to CLI mode:

```
python -m fabdoc scan     "D:\Projects\Skyline Tower - Zone B - PKG-03 - 12-05-2024"
python -m fabdoc generate "D:\Projects\Skyline Tower - Zone B - PKG-03 - 12-05-2024"
python -m fabdoc validate "D:\Projects\...\issue-25" "D:\Model\members.xlsx"
python -m fabdoc diff     "D:\Projects\...\issue-15" "D:\Projects\...\issue-25"
python -m fabdoc track    "D:\Projects\Package Tracker.xlsx"
```

`validate` also accepts an already-generated register workbook in place of the
folder, so you do not have to re-scan thousands of PDFs to re-run a comparison.

Try it on generated sample data first:

```
python tools/make_sample_project.py sample
```

## How it works

### 1. Folder metadata

The master folder name is parsed for title, issue date, zone and package. It is
tolerant about layout - it looks for the tokens it recognises and treats what is
left as the title:

| Folder name | Title | Date | Zone | Package |
| --- | --- | --- | --- | --- |
| `Skyline Tower - Zone B - PKG-03 - 12-05-2024` | Skyline Tower | 12-May-2024 | B | 03 |
| `Bridge Deck_Zone 4_2024-11-02` | Bridge Deck | 02-Nov-2024 | 4 | |
| `Tower A - 09-Jan-2025` | Tower A | 09-Jan-2025 | | |

Ambiguous numeric dates are read day-first (`05-03-2024` is 5 March). Flip
`day_first_dates` in the settings file if your packages use month-first.
Everything the parser finds is shown in editable fields before the register is
written, so a miss is a correction rather than a failure.

### 2. Category detection

Subfolders containing PDFs become worksheets. Names are matched loosely, so
`Assembly`, `Assembly Drawings` and `Shop Drawings` all land on **Assembly**.

**Assembly and Erection are separate categories.** An assembly drawing details
one fabricated assembly for the shop; an erection drawing shows where assemblies
go on site. A folder named `Assembly` is never relabelled `Erection`.

- One category present → one worksheet.
- Three present → three worksheets.
- A folder matching nothing known is still processed, under its own name.
- PDFs sitting loose in the project folder are collected as **Drawings**.

Nothing is mandatory and nothing is silently dropped.

### 3. Field extraction

For each PDF the extractor tries four tiers, stopping at the first that works:

1. Labelled fields in the title block region (bottom-right by default).
2. Labelled fields anywhere on the page.
3. The largest mark-shaped text in the title block - how most templates present
   an unlabelled assembly mark.
4. The filename.

Every row records which tier produced it. Rows that came from the filename, or
that yielded nothing, are **highlighted amber** in the workbook; files that could
not be read at all are **orange**. A register you cannot audit is worse than no
register, so the uncertainty is on the page rather than hidden.

### 4. The register

One workbook, one worksheet per category, three columns:
`S.No | Member Name | Revision No`.

The workbook takes the name of the package folder, so
`Skyline Tower - Zone B - PKG-03 - 12-05-2024` writes
`Skyline Tower - Zone B - PKG-03 - 12-05-2024.xlsx` beside it. Pass `-o` on the
CLI, or edit the output box in the app, to put it somewhere else.

Project title, issue date and zones are stated **once** in the header band above
the table, not repeated on every row. Set `include_source_column` to `true` in
settings to add `Source File` and `Notes` when you need to trace a flagged row
back to its drawing.

### Zones

Many detailers encode the erection sequence into the member mark itself:
`17172C172` is job `17`, sequence `172`, member `C172`, and the leading digit of
the sequence gives **Zone 1**. Sequence `270` gives Zone 2, `471` gives Zone 4.

When a package spans several zones they are **clustered in one worksheet**, each
under a banded zone header, with `S.No` restarting at 1 per zone:

```
ZONE 1   (Seq 172, 173)   -   64 drawing(s)
S.No | Member Name | Revision No
  1  | 17172C172   |      B
  2  | 17172C242   |      B
...
ZONE 2   (Seq 270, 271)   -   55 drawing(s)
S.No | Member Name | Revision No
  1  | 17270C335   |      B
```

The mark pattern is `member_seq_pattern` in settings; clear it to switch this
off, or set `group_by_zone: false` for one flat table.

### Comparing two issues

Packages get re-issued constantly - "for Approval" then "for Re Approval". The
`diff` command compares two issues and reports which drawings are **added**,
**removed**, came back at a **different revision**, or changed in **quantity**:

```
python -m fabdoc diff "<older issue>" "<newer issue>"
```

Either side can be a package folder or an already-generated register workbook.

### Tracking a package across every issue

`diff` compares two issues. A package has many: it goes out **IFA** (issued for
approval), comes back, goes out revised, and repeats until it is signed off -
then it moves to **IFF** (issued for fabrication), which ships in slices.

Tick **Add this issue to the tracker** and every upload does two things: writes
its own register, *and* appends itself to one cumulative tracking workbook.

```
python -m fabdoc generate "<folder>" --track --stage IFA --round 25
python -m fabdoc generate "<folder>" --track --stage IFF --round 1
python -m fabdoc track "Package Tracker.xlsx"
```

The stage is never guessed from the folder name. An approval issue mistaken for
a fabrication release would rewrite the approved baseline and report the rest of
the package as unshipped, so it must be stated.

The tracker has four sheets:

| Sheet | What it answers |
| --- | --- |
| Tracker | One row per issue: stage, round, date, added / revised / removed / released / on hold |
| Member History | Member down the side, issue across the top, revision in the cell |
| Change Log | Every change, flattened, issue by issue |
| On Hold | Approved members not yet released, and why |

#### Fabrication releases: absent is not removed

The first fabrication release rarely carries the whole package - 50 of 119 is
normal. Those other 69 members were **not dropped**, they have not shipped yet,
and reporting them as *removed* would be a false alarm on the one document the
shop floor acts on.

So once a package reaches IFF, the last IFA issue becomes the **approved
baseline**, each release adds to a running *released* set, and whatever is left
is **on hold**. A dialog asks why, taking one reason for the whole batch with
per-member overrides, and the answers land in the On Hold sheet.

Releases accumulate rather than replacing each other, so release 2 is compared
against the baseline and everything shipped so far - never against release 1,
which would report the entire first shipment as removed. Each round asks about a
smaller balance, with reasons already given carried forward:

```
IFA-15   107 drawings   first issue
IFA-25   119 drawings   12 added, 106 revised
IFF-1     50 drawings   50 released, 69 on hold
IFF-2     25 drawings   25 released, 44 on hold
```

Chain state is kept in a `.chain.json` beside the tracker, so the workbook can be
redrawn after reasons are edited without rescanning a single PDF.

### 5. Member validation

Import the model export (`.xlsx`, `.csv`, `.tsv` or `.txt`). The member column
is guessed from the header - *Assembly Mark*, *Piece Mark*, *Member Name* and
similar - and you can override it. The comparison reports:

- **Missing in drawings** - modelled but never drawn. The one that stops steel.
- **Not in model** - drawn but not modelled. Usually a superseded member.
- **Matched**.
- **Duplicates** - the same mark on more than one drawing. Reported as a warning,
  not a mismatch.

Marks are compared ignoring case and spaces by default; leading-zero
insensitivity (`B007` = `B7`) is optional. Because part and erection drawings are
normally validated against different exports, you can restrict the comparison to
one category.

## Calibrating for your drawing template

The default patterns assume a reasonably conventional title block. If your
drawings use different labels, calibrate once and save:

```
python -m fabdoc calibrate "D:\...\a-representative-drawing.pdf" --full-text
```

This prints the largest text spans with their positions as page fractions, the
full page text, and what the current patterns extract. Then either edit the
patterns in the **Extraction Settings** tab (which has the same inspector with a
live Test button) or edit `~/.fabdoc/settings.json` directly.

Patterns are ordinary regular expressions, one per line, matched
case-insensitively; the first that matches wins and capture group 1 is the
value. If the mark you want sits outside the highlighted title-block region,
widen `title_block_rect` - it is `(left, top, right, bottom)` as fractions of the
page, origin top-left.

**Scanned drawings.** If `calibrate` reports no text at all, the PDF is a raster
scan with no text layer. No regex can read it; those files are flagged
`no extractable text (scanned image?)` and need OCR before this tool can help.

## Settings

Stored at `~/.fabdoc/settings.json`, written by the Extraction Settings tab or
`python -m fabdoc settings --show` / `--reset`. Pass `--settings <path>` to use a
project-specific file, which is the way to share a calibrated profile with a
team.

## Tests

```
python -m pytest
```

111 tests covering folder parsing, category detection, all four extraction tiers,
sorting, workbook naming, structure and round-trip, member-list import, the
validation logic, zone derivation, issue-to-issue comparison, and the IFA/IFF
package chain. They build real
PDFs with PyMuPDF rather than mocking, so the extraction cascade is genuinely
exercised.
