# FabDoc Organizer — User Guide

How the tool works, how to drive it so the output is trustworthy, and where to
look when you need to prove why a row says what it says.

For a short feature list see [README.md](README.md). For the code and the design
reasoning behind it see [HANDOVER.md](HANDOVER.md).

---

## Contents

1. [What it does](#1-what-it-does)
2. [The three ways to run it](#2-the-three-ways-to-run-it)
3. [How it works, step by step](#3-how-it-works-step-by-step)
4. [Working effectively — the eight rules](#4-working-effectively--the-eight-rules)
5. [Tracking a package across issues](#5-tracking-a-package-across-issues)
6. [Logs and the audit trail](#6-logs-and-the-audit-trail)
7. [Troubleshooting](#7-troubleshooting)
8. [Quick reference](#8-quick-reference)

---

## 1. What it does

You point it at an issue folder. It reads the project details out of the folder
name, finds the drawing categories inside it, opens every PDF and pulls out the
member mark and revision, and writes one Excel register with a worksheet per
category.

From there it can also:

- **compare that register against the member list** exported from Tekla or SDS2,
  to catch members that were modelled but never drawn;
- **compare two issues** of the same package, to see what was added, dropped or
  revised between them;
- **track a package across its whole life** — every approval round, then every
  fabrication release — in one cumulative workbook.

A package that takes an engineer days takes this about a minute. That speed is
only worth anything if you can trust the result, which is why most of this guide
is about how to check it.

---

## 2. The three ways to run it

All three share the same engine. A register generated in the browser is identical
to one generated on the desktop.

### Desktop app

Double-click **`FabDoc Organizer.bat`**, or:

```bash
python -m fabdoc
```

Four tabs: Generate Register, Validate Members, Compare Issues, Extraction
Settings. This is the only one with the settings/calibration tab.

### Web app

Double-click **`web\FabDoc Web.bat`**, or:

```bash
cd web
python -m uvicorn app.main:app --port 8000
```

Then open <http://127.0.0.1:8000>. Same four tabs minus settings. Use this when
several people need it from their own machines.

### Command line

Any argument switches to CLI mode. This is the one to use for batch runs and for
anything you want to script.

```bash
python -m fabdoc scan     "D:\Projects\25. 2026-07-06 Stairs for Re Approval"
python -m fabdoc generate "D:\Projects\25. 2026-07-06 Stairs for Re Approval"
```

---

## 3. How it works, step by step

### 3.1 The folder name becomes the project details

The parser looks for the tokens it recognises — a date, zone numbers, a package
code, a leading issue number — and treats what is left as the title.

| Folder name | Title | Date | Zone |
| --- | --- | --- | --- |
| `Skyline Tower - Zone B - PKG-03 - 12-05-2024` | Skyline Tower | 12-May-2024 | B |
| `25. 2026-07-06 Stairs at Zone 1 and Zone 2 for Re Approval` | Stairs at Zone 1 and Zone 2 for Re Approval | 06-Jul-2026 | 1, 2 |

Ambiguous numeric dates are read **day-first**: `05-03-2024` is 5 March. Flip
`day_first_dates` in settings for month-first packages.

Everything it finds is shown in editable boxes before anything is written, so a
miss is a correction rather than a failure.

### 3.2 Subfolders become worksheets

Any subfolder containing PDFs becomes a category, matched loosely — `Assembly`,
`Assembly Drawings` and `Shop Drawings` all land on **Assembly**.

- One category folder → one worksheet. Three → three.
- A folder matching nothing known is still processed, under its own name.
- PDFs sitting loose in the package folder are collected as **Drawings**.

**Assembly and Erection are deliberately separate.** An assembly drawing details
one fabricated assembly for the shop; an erection drawing shows where assemblies
go on site. A folder named `Assembly` will never come out labelled `Erection`.

### 3.3 Each PDF becomes a row

For every drawing the extractor tries four tiers and stops at the first that
works:

| Tier | Where it looked | Flagged? |
| --- | --- | --- |
| 1 | A labelled field inside the title block | No |
| 2 | A labelled field anywhere on the page | No |
| 3 | The largest mark-shaped text in the title block | No |
| 4 | The filename | **Yes — amber** |
| — | Nothing found, or the file would not open | **Yes — amber / orange** |

Every row records which tier produced it. **This is the part that makes the
register auditable**, and it is why the colours matter:

- **Amber row** — the mark came from the filename, or was not found at all.
  Confirm it against the drawing before you trust it.
- **Orange row** — the file could not be read. The error is on the row.
- **No colour** — the mark was read off the sheet itself.

A register you cannot audit is worse than no register, because it gets trusted.

### 3.4 Zones come out of the mark

Many detailers encode the erection sequence into the mark: `17172C172` is job
`17`, sequence `172`, member `C172` — and the leading digit of the sequence gives
**Zone 1**. Sequence `270` gives Zone 2.

When a package spans several zones they are clustered in one worksheet, each
under a green banded header, with `S.No` restarting at 1 per zone.

Sequences of two or three digits are both understood, so `17120B163` (seq 120)
and `1710B84` (seq 10) both land in Zone 1.

> **If you see a second table with no green band**, it is the rows whose marks did
> not match the sequence pattern, so no zone could be derived. Nothing is lost —
> the counts still add up — but see [rule 6](#rule-6-if-a-second-unbanded-table-appears-fix-the-mark-pattern).

### 3.5 The register

One workbook, one worksheet per category, three columns:
`S.No | Member Name | Revision No`, plus a **Summary** sheet with per-category
counts.

The workbook is named after the package folder, so
`25. 2026-07-06 Stairs for Re Approval` writes
`25. 2026-07-06 Stairs for Re Approval.xlsx`.

---

## 4. Working effectively — the eight rules

### Rule 1: Check the review count before you check anything else

Every run reports how many rows were flagged. The Summary sheet has the same
figure per category.

- **0 flagged** — the title blocks are being read properly. Spot-check a couple
  and move on.
- **A handful** — look at those rows. Usually odd sheets in an otherwise normal set.
- **Most or all flagged** — stop. The patterns do not fit this drawing template.
  Calibrate (rule 2) rather than accepting the output.

The count is the single most useful number the tool produces. Everything else
assumes it is low.

### Rule 2: Calibrate once per drawing template

The default patterns assume a conventional title block. A new detailer will
likely need one calibration pass, which you then keep.

```bash
python -m fabdoc calibrate "D:\...\one-representative-drawing.pdf" --full-text
```

This prints the largest text spans with their positions as page fractions, the
full page text, and what the current patterns extract. Adjust in the desktop
app's **Extraction Settings** tab (same inspector, with a live Test button), then
**Save Settings**. The profile persists to `~/.fabdoc/settings.json`.

> **If `calibrate` reports no text at all**, the PDF is a scanned image with no
> text layer. No pattern can read it. Those files are flagged
> `no extractable text (scanned image?)` and need OCR before this tool can help.

### Rule 3: Match the model export level to the drawing category

This is the one mistake that makes a whole validation report useless.

| Drawing category | Export level | Tekla field |
| --- | --- | --- |
| Erection / GA | Assembly | `ASSEMBLY_POS` |
| Structural / Shop | Assembly | `ASSEMBLY_POS` |
| Part / Single Part | **Part** | `PART_POS` |

Ask the modeller for: **Manage → Organizer → Object Browser**, columns *Assembly
Position* (or *Part Position*), *Phase*, *Quantity*, *Profile* → Export to Excel.
Have them **filter to this package's phase or zone** and **run full numbering
first** — stale marks mismatch wholesale.

**A total mismatch is the signature of a wrong-level export, not a real problem.**
If you see 0 matched / 120 missing / 40 extra, do not start chasing 120 missing
drawings — check the export level first.

Because part and erection drawings are validated against different exports, use
the category filter to compare one at a time.

### Rule 4: Name issue folders consistently

The tool is tolerant, but consistency costs nothing and makes the tracker work:

```
25. 2026-07-06 Stairs at Zone 1 and Zone 2 for Re Approval (Seqs 172,173,270,271)
│   │          │                                │
│   │          │                                └── purpose — stripped for the project name
│   │          └── title
│   └── date
└── issue round
```

Keep the **title identical** across issues of one package. The purpose clause
(`for Approval`, `for Re Approval`, `for Fabrication`) and a leading stage code
(`IFF-2`) are stripped automatically, so those can change freely — but if the
title itself changes, the issues will not chain into one tracker.

### Rule 5: Re-processing a folder is always safe

Re-running the same folder **replaces** that issue in the tracker rather than
adding a duplicate. Tune your patterns, run it again, and the history stays
correct. There is no cleanup step.

### Rule 6: If a second unbanded table appears, fix the mark pattern

A worksheet showing one green `ZONE` table plus a second table with no band means
some marks did not match `member_seq_pattern`, so they got no zone.

Check the counts first — they will still add up. Then either widen the pattern in
**Extraction Settings** to match how your marks encode the sequence, or set
`group_by_zone` to `false` for one flat table with no zone clustering at all.

### Rule 7: Turn on the trace columns when you are chasing something

`Source File` and `Notes` are off by default because the register is a
deliverable. When you are investigating flagged rows, set
`include_source_column` to `true` in settings and re-run — every row then carries
the file it came from and any notes recorded against it.

### Rule 8: State the stage honestly when tracking

IFA or IFF is never guessed from the folder name, and you cannot leave it blank.
Marking an approval issue as IFF rewrites the approved baseline and reports the
rest of the package as unshipped. Marking a fabrication release as IFA loses the
on-hold tracking entirely.

---

## 5. Tracking a package across issues

A package is not one delivery. It goes out for approval, comes back, goes out
revised, and repeats — then moves to fabrication, which ships in slices.

Tick **Add this issue to the tracker** (or pass `--track`) and each upload does
two things: writes its own register, *and* appends itself to one cumulative
tracker.

```bash
python -m fabdoc generate "<folder>" --track --stage IFA --round 25
python -m fabdoc generate "<folder>" --track --stage IFF --round 1
python -m fabdoc track "Package Tracker.xlsx"
```

### The tracker's four sheets

| Sheet | What it answers |
| --- | --- |
| **Tracker** | One row per issue: stage, round, date, added / revised / removed / released / on hold |
| **Member History** | Member down the side, issue across the top, revision in the cell |
| **Change Log** | Every change, flattened, issue by issue |
| **On Hold** | Approved members not yet released, and why |

Member History is the one to read in a progress meeting:

```
17172C1   zone 1   A  B  C  D  E  |  E  -  -  -     climbed A→E, shipped in IFF-0
17172C6   zone 1   A  B  -  -  -  |  -  -  -  -     dropped during approval
17271X10  zone 2   -  -  -  A  A  |  -  -  -  -     entered late, still not shipped
```

### Fabrication releases: absent is not removed

The first fabrication release rarely carries the whole package — 50 of 119 is
normal. **Those other 69 were not dropped, they have not shipped yet.**

So once a package reaches IFF, the last approval issue becomes the **approved
baseline**, each release adds to a running released set, and whatever is left is
**on hold**. A dialog asks why, taking one reason for the whole batch with
per-member overrides, and the answers land in the On Hold sheet.

Releases accumulate rather than replacing each other, so release 2 is compared
against the baseline and everything shipped so far — never against release 1,
which would report the entire first shipment as removed. Each release asks about
a smaller balance, with reasons already given carried forward:

```
IFA-15   107 drawings   first issue
IFA-25   119 drawings   12 added, 106 revised
IFF-1     50 drawings   50 released, 69 on hold
IFF-2     25 drawings   25 released, 44 on hold
```

Cancelling the dialog leaves the register written but records no reasons. Rows
with no reason are highlighted amber in the On Hold sheet, so nothing goes
missing quietly. You can supply them later:

```bash
python -m fabdoc track "Package Tracker.xlsx" --reason "Client hold - core redesign"
```

That fills only the blanks. Add `--overwrite-reasons` to replace existing ones.

---

## 6. Logs and the audit trail

There are two different things worth calling "logs": the **run log**, which says
what the tool did, and the **audit trail inside the workbook**, which says why
each row reads as it does. The second one is the one that settles arguments.

### 6.1 The audit trail in the workbook

This travels with the deliverable, so it is still there weeks later.

| Where | What it tells you |
| --- | --- |
| Row colour | Amber = read from the filename or not found. Orange = file would not open. |
| **Summary** sheet | Per-category drawing count, needs-review count, error count |
| **Summary** sheet | Source folder, original folder name, and when it was generated |
| `Notes` column | Why a row is flagged — see the note vocabulary below |
| `Source File` column | The exact PDF a row came from |

Turn on the last two with `include_source_column: true`.

The notes you will see:

| Note | Meaning |
| --- | --- |
| `no extractable text (scanned image?)` | The PDF has no text layer. Needs OCR. |
| `member name not found` | All four tiers failed on this drawing. |
| `revision defaulted` | No revision found; recorded as `0`. |
| `sequence assigned by file order` | No sequence found; S.No is a position, not a printed number. |
| `<ErrorType>: <message>` | The file could not be opened at all. |

### 6.2 The run log

**Desktop app** — the black **Log** panel at the bottom of tab 1. It records the
folder scanned, categories selected, per-category counts, review counts, the
saved path, the tracker chain, and full tracebacks if something fails. It clears
at the start of each run, so copy anything you want to keep before re-running.

**Command line** — progress goes to `stderr`, results to `stdout`, so you can
separate them:

```bash
python -m fabdoc generate "<folder>" > run.log 2> progress.log
```

Exit codes, for scripting:

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | Error — no drawings found, bad path, unreadable member list |
| `2` | Discrepancies found, and `--strict` was passed |
| `130` | Cancelled with Ctrl+C |

`--strict` is what makes `validate` and `diff` fail a build when something is off:

```bash
python -m fabdoc validate "<folder>" "<members.xlsx>" --strict
```

**Web app** — the console window running uvicorn is the log. Every request is
logged, and failures return the error to the page rather than hiding it. Keep
that window open; closing it stops the server.

### 6.3 Where files are written

**Desktop and CLI** — the register goes next to the package folder unless you say
otherwise (`-o`, or the Save-as box). The tracker defaults to `Package
Tracker.xlsx` in the same output folder, overridable with `--tracker`.

**Web app** — everything lands under `web\data\`:

```
web/data/
  uploads/    one workspace per upload, deleted once its register is written
  output/     generated registers and reports
  trackers/   one tracker + chain state per project
```

### 6.4 The chain state file

Beside every tracker sits a `.chain.json` holding each issue's members, revisions
and the hold reasons. This is the tracker's real memory:

- it lets the workbook be rebuilt **without rescanning a single PDF**;
- it is what makes re-processing a folder update in place;
- it is plain JSON, so you can read it directly if you need to see exactly what
  was recorded for an issue.

Keep it with the tracker. Delete it and the history is gone, even though the
workbook still exists.

### 6.5 Settings

```bash
python -m fabdoc settings --show     # print the active profile
python -m fabdoc settings --reset    # back to defaults
```

Stored at `~/.fabdoc/settings.json`. Pass `--settings <path>` to use a
project-specific file — that is how you share a calibrated profile with a team.

---

## 7. Troubleshooting

| Symptom | Cause | What to do |
| --- | --- | --- |
| Most rows amber | Patterns do not fit this template | Calibrate (rule 2) |
| `no extractable text` | Scanned PDFs, no text layer | Needs OCR; out of scope |
| 0 matched / everything missing | Wrong model export level | Check assembly vs part (rule 3) |
| Two tables, second has no zone band | Marks did not match the sequence pattern | Widen it, or turn off zone grouping (rule 6) |
| "Cannot write … open in another program" | The workbook is open in Excel | Close it and save again |
| Categories all merged into one | Uploaded the drawings, not the folder | Pick the issue folder itself |
| Issues not chaining into one tracker | The title differs between issues | Keep titles identical (rule 4); the project box is editable |
| An IFF release reports members removed | Stage was set to IFA | Re-run that issue as IFF |
| `.xls` rejected | Legacy format needs another dependency | Re-save as `.xlsx` or `.csv` |
| Zone wrong or missing | Mark encodes the sequence differently | Edit `member_seq_pattern` |

---

## 8. Quick reference

```bash
# look before you write
python -m fabdoc scan     "<folder>"

# the register
python -m fabdoc generate "<folder>"
python -m fabdoc generate "<folder>" -o "D:\Registers\out.xlsx" -c Assembly

# against the model
python -m fabdoc validate "<folder|register.xlsx>" "<members.xlsx>" --strict
python -m fabdoc validate "<folder>" "<members.xlsx>" --column "Assembly Mark"

# issue against issue
python -m fabdoc diff "<older>" "<newer>"

# the package tracker
python -m fabdoc generate "<folder>" --track --stage IFA --round 25
python -m fabdoc generate "<folder>" --track --stage IFF --round 1
python -m fabdoc track "Package Tracker.xlsx" --reason "Client hold"

# fitting a new drawing template
python -m fabdoc calibrate "<one-drawing.pdf>" --full-text
python -m fabdoc settings --show

# test data to practise on
python tools/make_chain_sample.py "samples/chain-sample"
```

### The five settings worth knowing

| Setting | Default | Change it when |
| --- | --- | --- |
| `include_source_column` | `false` | You are chasing flagged rows and need the file each came from |
| `group_by_zone` | `true` | You want one flat table instead of zone clusters |
| `day_first_dates` | `true` | Your packages use US month-first dates |
| `member_seq_pattern` | 2–3 digit sequence | Your marks encode the sequence differently |
| `compare_strip_leading_zeros` | `false` | Your model and drawings disagree on `B007` vs `B7` |

### If you remember three things

1. **Read the review count first.** Everything downstream assumes it is low.
2. **Match the export level to the drawing category.** A total mismatch is a
   wrong export, not 120 missing drawings.
3. **On hold is not removed.** Once fabrication starts, an absent member has not
   shipped yet — say why, and the tracker keeps asking until it ships.
