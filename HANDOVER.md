# FabDoc Organizer — Handover

Written for whoever (human or agent) picks this up next, cold. Read this before
touching code. It records not just what exists but **why**, because several
decisions here look wrong until you know what they're defending against.

**Status as of 2026-08-07:** feature-complete and **calibrated against a real
drawing package**, 87 tests passing, working tree clean, `main` pushed to
`origin`.

The extraction was validated on 119 real drawings: **0 wrong member marks, 0
wrong revisions**. The title-block patterns needed no change. What did need
changing was everything around them — zones, categories and columns — see §6.

---

## 1. What this does

Steel fabrication packages arrive as a folder per issue/revision, containing
subfolders of drawing PDFs. Engineers manually open every drawing, copy the
sequence number, member mark and revision into an Excel register, then compare
that register against the member list exported from the structural model
(Tekla/SDS2) to catch members that were modelled but never drawn.

This tool automates all of it: folder name → project metadata, subfolders →
worksheets, PDFs → register rows, register vs model export → discrepancy report.

Measured throughput: **~73 drawings/sec** (1000 drawings in 13.6s). A package
that takes an engineer days takes this about a minute.

---

## 2. Quick start

```bash
pip install -r requirements.txt      # PyMuPDF + openpyxl only

python -m fabdoc                     # desktop GUI (no args)
python -m fabdoc scan     <folder>   # dry run: show metadata + categories
python -m fabdoc generate <folder>   # write the register workbook
python -m fabdoc validate <folder|register.xlsx> <members.xlsx>
python -m fabdoc diff     <old-issue> <new-issue>   # register vs register
python -m fabdoc calibrate <one-drawing.pdf> --full-text

python tools/make_sample_project.py sample   # generate test data
python -m pytest -q                          # 87 tests, ~4s
```

`python -m fabdoc` with **no arguments opens the GUI**; any argument switches to
CLI. `FabDoc Organizer.bat` is the double-click launcher for engineers who don't
use terminals.

Environment this was built and verified on: Windows 11, Python 3.11.9, PyMuPDF
1.28.0, openpyxl 3.1.5, tkinter from stdlib.

---

## 3. Architecture

Strict one-way dependency flow. Nothing below imports anything above it.

```
config.py ──────────────► everything (settings + tunable extraction profile)
folder_meta.py ─┐
categories.py ──┴───────► register.py ◄─── extract.py
                              │
                    ┌─────────┴──────────┐
              excel_out.py         validate.py ◄─── memberlist.py
              register_io.py             │
                    └─────────┬──────────┘
                        cli.py / gui.py
```

| File | Lines | Responsibility |
| --- | ---: | --- |
| [config.py](fabdoc/config.py) | 216 | `ExtractionProfile` (regexes, title-block geometry, stopwords) + `AppSettings`. Persists to `~/.fabdoc/settings.json`. **All extraction tuning is data here, not logic elsewhere.** |
| [folder_meta.py](fabdoc/folder_meta.py) | 174 | Folder name → title, issue date, zone, package |
| [categories.py](fabdoc/categories.py) | 156 | Subfolders → drawing categories → worksheet names |
| [extract.py](fabdoc/extract.py) | 313 | **The heart.** One PDF → `DrawingRecord` via a 4-tier cascade |
| [register.py](fabdoc/register.py) | 131 | Orchestrates a full folder scan; sorting, progress, cancellation |
| [excel_out.py](fabdoc/excel_out.py) | 345 | Register workbook + validation report workbook |
| [register_io.py](fabdoc/register_io.py) | 104 | Reads a register workbook *back* into a `Register` |
| [memberlist.py](fabdoc/memberlist.py) | 192 | Model export import (xlsx/csv/tsv/txt) + column auto-detect |
| [validate.py](fabdoc/validate.py) | 158 | Register vs **model**: missing / extra / matched / duplicates |
| [compare.py](fabdoc/compare.py) | 168 | Register vs **register**: added / removed / revised between two issues |
| [cli.py](fabdoc/cli.py) | 291 | 6 subcommands |
| [gui.py](fabdoc/gui.py) | 1030 | tkinter, 4 tabs, threaded worker |

### The extraction cascade (extract.py)

This is the part that matters. Four tiers, first hit wins:

| Tier | Source | Constant | Flagged for review? |
| --- | --- | --- | --- |
| 1 | Labelled field inside title-block region | `SOURCE_TITLEBLOCK` | No |
| 2 | Labelled field anywhere on page | `SOURCE_PAGE` | No |
| 3 | Largest mark-shaped text in title block | `SOURCE_LARGEST` | No |
| 4 | Filename | `SOURCE_FILENAME` | **Yes** |
| — | Nothing found | `SOURCE_NONE` | **Yes** |

Every `DrawingRecord` stores which tier produced each field
(`member_source`, `revision_source`, `seq_source`). `needs_review` drives amber
highlighting in the workbook. **This audit trail is the point** — a register you
can't audit is worse than no register, because it's trusted.

---

## 4. Design decisions — do not undo without asking

These look like mistakes if you don't know the reason.

| Decision | Why |
| --- | --- |
| **tkinter, not PySide6/Qt** | Zero extra dependencies. PyMuPDF + openpyxl were already on the machine; tkinter ships with CPython. Engineers install nothing. Swapping to Qt adds a heavy dep for cosmetics. |
| **Extraction rules are data in `config.py`, not code** | Every detailer's title block differs. Hardcoding a parser guarantees a rewrite per client. The profile is regex + geometry, editable in the GUI and persisted as JSON. |
| **Duplicates are a warning, not a mismatch** | `is_clean` deliberately ignores `duplicates_in_drawings`. The same mark on two drawings is usually a legitimate re-issue, not a failure. |
| **Zones are left in the title** | Real folders read "Stairs at Zone 1 and Zone 2 for Re Approval". Cutting the zone tokens out to store them separately mangles the title. They are captured *and* left in place. |
| **Assembly ≠ Erection** | A folder named `Assembly` must never come out labelled `Erection`. Different deliverables. |
| **Model list de-duplicates silently** | Tekla assembly lists have **one row per instance** — 40 marks can be 280 rows. Verified: 280 → 40 unique → PASS. Do not "fix" this into a count mismatch. |
| **`Source File` + `Notes` columns exceed the spec** | The spec asked for 5 columns. A flagged row is worthless if you can't trace it to a drawing. Switchable via `include_source_column: false`. |
| **`.xls` is rejected, not parsed** | Legacy `.xls` needs `xlrd`, another dep, for a format users can re-save in 5 seconds. The error message says exactly that. |
| **Ambiguous dates read day-first** | `05-03-2024` = 5 March. Indian/European convention. Flip `day_first_dates` for US packages. |
| **Corrupt PDFs are recorded, not raised** | One bad file in a 1000-drawing package must not cost the run. Errors land on their own row, highlighted orange. |
| **Tests build real PDFs with PyMuPDF, never mock** | Mocked extraction tests pass while extraction is broken. Every real bug below was caught *because* the tests render actual PDFs. |

---

## 5. Bug traps — already fixed, easy to reintroduce

Four real bugs were found by the test suite. Each has a regression test. If you
refactor these areas, understand the trap first.

**1. `\b` does not fire against underscore** — [folder_meta.py](fabdoc/folder_meta.py)

`_` is a word character, so `\b(\d{4})` never matches in `Deck_2024-11-02`.
Underscore-separated folder names are the norm in this industry, so this silently
lost both date and zone. Fixed with explicit lookarounds `_LB`/`_RB`.
**Never reintroduce `\b` in this file.**

**2. Filename regex backtracking** — [config.py](fabdoc/config.py) `filename_patterns`

`no_rev.pdf` yielded revision `EV`: `(?:REV|R)` matched `REV`, found nothing left
for the value, backtracked to `R`, and captured `EV`. The revision group is now
`\d{1,3}|[A-Z]\d{0,2}`, not `[0-9A-Za-z]{1,3}`. Widening it reintroduces this.

**3. Label vocabulary captured as the mark** — [config.py](fabdoc/config.py)

`MEMBER ID : B-101` extracted `ID` — the qualifier alternation lacked `ID`, so
the capture landed on the label word. Fixed by widening the alternation *and*
adding label vocabulary (`ID`, `NO`, `NAME`, `MARK`, `POS`, `REF`, …) to
`member_stopwords`, so any future gap falls through to the next tier instead of
producing a confident wrong answer. **The stopword list is a safety net — don't
trim it.**

**4. Inconsistent leading zeros** — [extract.py](fabdoc/extract.py) `_tidy_seq`

`012` from a filename vs `12` from a title block broke sorting and comparison.
Numeric sequence numbers are normalised through `_tidy_seq`.

---

## 6. Calibration results — what the real data taught us

Calibration is **done**. A real package (job 17172/17271, "Stairs at Zone 1 and
Zone 2", 107 and 119 drawings across two issues) was processed end to end.

**Extraction was already correct: 0/119 wrong on member mark, 0/119 on
revision.** The real title block is a *table* — labels in one text block,
values in another — so label-adjacency regexes never reach it. It works anyway
because tier 3 (largest mark-shaped text in the title block) picks up the
Assembly # value at x≈0.89, y≈0.94, and the filename tier confirms the revision
from `17172C172  - Rev B.pdf`. **Do not assume the labelled tiers are what is
carrying this client** — they are not.

Four things around the extractor were wrong and are now fixed:

1. **Zones** — only the first of "Zone 1 and Zone 2" was captured, and excising
   the zone tokens mangled the title into "Stairs at  and Zone 2". Zones are now
   a list and are deliberately *not* removed from the title.
2. **Sequence and zone come from the mark**, not the PDF. There is no printed
   sequence number anywhere in these drawings — `seq`/`sheet`/`zone` keywords
   return zero hits. `17172C172` = job 17, seq 172, zone 1 (leading digit).
3. **"Assembly" was being relabelled "Erection"** — it was an alias. Now its own
   category.
4. **Register columns** cut to `S.No | Member Name | Revision No`.

### Still unproven

- **Only one client's template has been seen.** A second detailer will likely
  need `calibrate` run again. The tiered cascade means it will probably degrade
  to the filename tier rather than fail outright — check `member_source`.
- **The zone rule is an inference.** Leading digit of a 3-digit sequence, with a
  2-digit job prefix, confirmed by the user. `member_seq_pattern` in settings is
  where to change it if another project encodes marks differently.
- **No scanned/raster PDFs have been encountered yet.**

What is proven against synthetic-but-realistic drawings:

- 11/11 mark shapes: `B-101`, `B/101`, `P.301`, `BM_204`, `ASSY-12A`, `1001`, bare `12`, `SC-1001-A`, …
- 9/9 title-block label wordings: `ASSEMBLY MARK`, `MEMBER ID`, `PIECE MARK`, `SHIPPING MARK`, …
- Title block in bottom-left or top-right still resolves via the tier-2 fallback
- Multi-page drawings (1, 3, 12 pages)
- 1000-drawing volume run, 0 review flags

**The calibration workflow (already built, just needs running):**

```bash
python -m fabdoc calibrate "path/to/one-real-drawing.pdf" --full-text
```

Prints the largest text spans with positions as page fractions, the full page
text, and what current patterns extract. Adjust patterns in GUI tab 3 (same
inspector, live Test button) or edit `~/.fabdoc/settings.json`. Save once — the
profile persists.

If `calibrate` reports **no text at all**, the PDFs are raster scans with no text
layer. No regex can read those; they're flagged
`no extractable text (scanned image?)` and would need an OCR pass (out of scope
as built — would mean adding Tesseract or similar).

---

## 7. What the team must export from the model

Getting this wrong is the one mistake that makes the whole report useless.
**Match the export level to the drawing category:**

| Drawing category | Export level | Tekla field | SDS2 |
| --- | --- | --- | --- |
| Erection / GA | Assembly | `ASSEMBLY_POS` | Member piece mark |
| Structural / Shop | Assembly | `ASSEMBLY_POS` | Member piece mark |
| Part / Single Part | **Part** | `PART_POS` | Submaterial mark |

Verified failure mode: a part-level list against erection drawings gives
**0 matched / 120 missing / 40 extra**. Total mismatch is the signature of a
wrong-level export, not a real discrepancy.

**Ask the modeller for:** Tekla → Manage → Organizer → Object Browser → columns
*Assembly Position* (or *Part Position*), *Phase*, *Quantity*, *Profile* →
Export to Excel. **Filter to the drawing package's phase/zone**, and **run full
numbering first** — stale marks mismatch wholesale.

Column auto-detection matches any header containing *assembly*, *part*, *member*,
*mark*, *position*, *piece*, *name* or *pos*; otherwise it's a dropdown in the
GUI or `--column` on the CLI.

---

## 8. Suggested next steps, in priority order

1. **Get one real Tekla/SDS2 export** and confirm column auto-detection picks the
   right field. This is now the only unverified part of the pipeline — the
   drawing side is calibrated, the model side is not.
2. **Push to `origin`.** `git push -u origin main` — 10 commits are local only.
3. **Calibrate on a second client's drawings** when one arrives. Expect the
   labelled tiers to miss and the largest-text tier to carry it, as here.
4. **Per-project profiles.** `--settings <path>` already works; the GUI always
   uses `~/.fabdoc/settings.json`. Add a profile picker so teams can share a
   calibrated profile per client. Small change, mostly GUI.
5. **Quantity validation.** Currently compares mark *presence* only. Comparing
   model instance counts against drawing counts would catch a different class of
   error. Requires a quantity column and a decision about what a mismatch means.
6. **OCR fallback** for scanned packages. Only if step 1 reveals scanned PDFs.
   Significant scope — new dependency, much slower, needs its own accuracy story.
7. **`.gitattributes`** to pin line endings if a non-Windows machine ever joins.
   Git currently warns about CRLF on every commit (harmless).

---

## 9. Requirement traceability

| Requirement | Where | State |
| --- | --- | --- |
| 1. Folder metadata from folder name | `folder_meta.py` | Done, editable in GUI |
| 2. Dynamic category → worksheet detection | `categories.py` | Done, 1/2/3+ categories, unknown names kept |
| 3. Extract S.No / Member / Revision | `extract.py` | Done — **calibrated, 0/119 errors** |
| 4. Excel register, sorted by S.No | `excel_out.py` | Done, + Summary sheet |
| 5. Member list import & comparison | `memberlist.py`, `validate.py` | Done, + duplicate detection |
| Extra: multi-zone clustering | `folder_meta.py`, `excel_out.py` | Done, verified on real data |
| Extra: issue-to-issue comparison | `compare.py` | Done, verified on real pair |

---

## 10. Notes for an agent working on this

- **Read `config.py` first.** It's the control surface; most "the extractor is
  wrong" problems are a pattern fix there, not a code change.
- **Run `python -m pytest -q` after any change to `extract.py`, `config.py` or
  `folder_meta.py`.** ~4 seconds, and the regexes are genuinely subtle.
- **`gui.py` is the largest file but the least risky.** It's presentation over
  the same functions the CLI calls. To exercise it headlessly: construct
  `FabDocApp(tk.Tk())` with `root.withdraw()`, stub `messagebox`, drive methods
  directly, pump `root.update()` in a loop while the worker thread runs. That
  full flow was verified this way.
- **Do not add dependencies casually.** Zero-install-friction is a deliberate
  property; the users are engineers on locked-down corporate Windows machines.
