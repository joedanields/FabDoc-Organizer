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

87 tests covering folder parsing, category detection, all four extraction tiers,
sorting, workbook naming, structure and round-trip, member-list import, and the
validation logic, zone derivation and issue-to-issue comparison. They build real
PDFs with PyMuPDF rather than mocking, so the extraction cascade is genuinely
exercised.
