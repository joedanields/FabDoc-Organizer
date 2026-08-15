# FabDoc Organizer — Handover

Written for whoever (human or agent) picks this up next, cold. Read this before
touching code. It records not just what exists but **why**, because several
decisions here look wrong until you know what they're defending against.

**Status as of 2026-08-15:** feature-complete and **calibrated against a real
drawing package end to end**, 261 tests passing, working tree clean.

Package tracking (IFA rounds → IFF partial releases, with on-hold reasons) is
built and **verified against two real IFF folders** — a release and a revised
re-release — not just synthetic slices carved out of the approved package. See
§6. The one side of the pipeline still unverified is the model side: no real
Tekla/SDS2 export has been compared yet.

The extraction was validated on 119 real drawings: **0 wrong member marks, 0
wrong revisions**. The title-block patterns needed no change. What did need
changing was everything around them — zones, categories, columns, and the
single-part title block, which is a column table rather than a title block at
all — see §6.

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
python -m fabdoc generate <folder> --track --stage IFF --round 1
                                     # ... and append the issue to its tracker
python -m fabdoc track     <tracker.xlsx> --reason "why held"   # rebuild + reasons
python -m fabdoc validate <folder|register.xlsx> <members.xlsx>
python -m fabdoc diff     <old-issue> <new-issue>   # register vs register
python -m fabdoc calibrate <one-drawing.pdf> --full-text
python -m fabdoc settings --show     # inspect the active profile

python tools/make_sample_project.py sample   # generate test data
python -m pytest -q                          # 261 tests, ~20s
```

`python -m fabdoc` with **no arguments opens the GUI**; any argument switches to
CLI. `FabDoc Organizer.bat` is the double-click launcher for engineers who don't
use terminals; `web\FabDoc Web.bat` launches the browser version (FastAPI), which
can also be packaged as a Windows app with `web\Build FabDoc Web.bat`.

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
          ┌───────────────────┼───────────────────┐
    excel_out.py        validate.py ◄─── memberlist.py
    register_io.py            │                   │
                        compare.py          tracking.py
                              │             tracking_out.py
                              └─────────┬─────────┘
                                  cli.py / gui.py
```

`compare.py` answers "what changed between these two issues". `tracking.py`
answers "what has happened to this package" across every issue, and is the only
place that knows about IFA/IFF stages.

| File | Lines | Responsibility |
| --- | ---: | --- |
| [config.py](fabdoc/config.py) | 357 | `ExtractionProfile` (regexes, title-block geometry, stopwords) + `AppSettings`. Persists to `~/.fabdoc/settings.json`. **All extraction tuning is data here, not logic elsewhere.** |
| [folder_meta.py](fabdoc/folder_meta.py) | 210 | Folder name → title, issue date, zone, package |
| [categories.py](fabdoc/categories.py) | 156 | Subfolders → drawing categories → worksheet names |
| [extract.py](fabdoc/extract.py) | 441 | **The heart.** One PDF → `DrawingRecord` via a 4-tier cascade |
| [sequencing.py](fabdoc/sequencing.py) | 56 | What band a mark sits in: sequence for assemblies, type for single parts |
| [register.py](fabdoc/register.py) | 215 | Orchestrates a full folder scan; sorting, progress, cancellation |
| [excel_out.py](fabdoc/excel_out.py) | 587 | Register workbook + validation/comparison report workbooks |
| [register_io.py](fabdoc/register_io.py) | 115 | Reads a register workbook *back* into a `Register` |
| [memberlist.py](fabdoc/memberlist.py) | 192 | Model export import (xlsx/csv/tsv/txt) + column auto-detect |
| [validate.py](fabdoc/validate.py) | 158 | Register vs **model**: missing / extra / matched / duplicates |
| [compare.py](fabdoc/compare.py) | 159 | Register vs **register**: added / removed / revised between two issues |
| [tracking.py](fabdoc/tracking.py) | 550 | **The whole package chain**: IFA rounds then IFF releases. Where "absent = on hold, not removed" lives |
| [tracking_out.py](fabdoc/tracking_out.py) | 518 | Tracker workbook: Tracker / History-by-category / Change Log / On Hold |
| [cli.py](fabdoc/cli.py) | 429 | 7 subcommands: scan, generate, track, validate, diff, calibrate, settings |
| [gui.py](fabdoc/gui.py) | 1434 | tkinter, 4 tabs, threaded worker |

The web app adds a third front end without touching any of that. `web/app/main.py`
is transport only — it imports `fabdoc` from the checkout and calls the same
functions the CLI and GUI call, so a rule fixed in the package is fixed in all
three. `web/run_app.py` is the entry point a frozen PyInstaller build uses;
`web/Build FabDoc Web.bat` produces the Windows app under `web/dist/`. See
`web/README.md`.

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
| **Absent ≠ removed once fabrication starts** | The first IFF release rarely carries the whole package — 50 of 119 is normal. The other 69 have not shipped yet; calling them "removed" is a false alarm on the one document the shop floor acts on. They become on-hold rows with a reason. **Do not simplify this back into `compare_issues`.** |
| **IFF releases accumulate, they do not chain pairwise** | Release 2 carries the *next* slice. Diffing it against release 1 would report the whole first shipment as removed. The last IFA issue is the baseline; each release adds to a running released set; outstanding is baseline − released. |
| **The stage is never inferred from the folder name** | An IFA folder misread as IFF silently rewrites the approved baseline and reports the rest of the package as unshipped. Wrong here is worse than asking, so `--stage` is required and the GUI picker starts blank. |
| **Chain state is JSON, not the workbook** | Appending an issue and rebuilding the whole chain are one code path over one file, and history redraws after a reason edit without rescanning thousands of PDFs. |
| **An assembly and a part of the same mark are separate items** | `Assembly::17172C172` and `Part::17172C172` are different deliverables — one is fabricated, the other is cut. Keyed by mark alone they collapsed into one entry, so whichever was read second silently overwrote the first and the release counts were wrong. Category-scoped identity, and history/change/on-hold sheets read it back out. |
| **A mark is recorded as the drawing carries it** | The mark is *not* normalised on the way in. History is keyed by a separate comparison key, so a member the detailer spells `17CH104` one issue and `17ch104` the next is one row, not two — but the sheet shows the latest spelling, exactly as drawn. Normalising the display would make the tracker disagree with the drawings the shop uses. |
| **Single parts band by type, not sequence** | A part is not erected in a sequence; it is cut for an assembly, and its mark carries a type instead — `17ch104` is job 17, type CH, piece 104. 92 of 124 part drawings in a real package are named this way. `part_type_pattern` groups them, `TYPE CH`, the way `member_seq_pattern` groups assemblies by `SEQ`. |
| **On-hold and dropped states are written, not just coloured** | The cell says `H` or `D` as well as being orange or grey. A printed tracker and a colour-blind reader both lose a fill, and on hold is the one state the shop floor acts on. |
| **Erection drawings are deliberately untracked** | An erection drawing shows where assemblies go on site — it is not a fabricated item, so it has no approved scope to release and nothing to hold. Tracking it put site drawings into the released and on-hold counts the shop reads. `untracked_categories` in settings. |
| **A release with no prior approval issue is its own baseline** | If a package reaches IFF with no IFA issue in the chain, the release is treated as its own baseline rather than inventing an empty one that would report every member as new and on hold. |
| **The web app lives in its own folder, on purpose** | FastAPI + uvicorn and their transitive dependencies would break the zero-install desktop property for everyone, including people who never open a browser. `web/` is where the browser version pays for itself. |

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

**5. A part drawing named after its material** — [config.py](fabdoc/config.py)
`member_reject_patterns`

Single-part drawings have a title block that is a *column table* — `Part #`,
`Qty`, `In Assembly`, `Material` as headings with the values on the row below —
so a same-line label pattern matched the heading and captured the material beside
it. 121 of 125 part drawings came out named after a steel section or a length:
`HSS4X4X1/2`, `PIPE1-1/2SCH40`, `29.00`, `134/`. The reject patterns throw those
shapes out so the cascade falls through to the largest text in the title block,
which is the mark. **Widening the accept path without keeping these rejections
reintroduces this wholesale.**

**6. The same mark as assembly and part collapsed** — [tracking.py](fabdoc/tracking.py) `member_id`

Once chains were keyed by the bare mark, an assembly and a single-part drawing of
the same mark landed on one entry and the second one read silently overwrote the
first — the release counts lost whole members. Identity is now
`"Assembly::17172C172"`. The dangerous fix is "simplifying" the key back to the
mark; `split_member_id` exists because the display and the identity must be
different things.

---

## 6. Calibration results — what the real data taught us

Calibration is **done**, end to end. A real package (job 17172/17271, "Stairs at
Zone 1 and Zone 2", 105 and 119 drawings across the two approval issues) was
processed through approval, then through **two real IFF folders** — a release
(`26.2026-08-07 Zone 1 Partial Stair Tower for Fabrication (Seq 172)`) and a
revised re-release (`31.2026-08-10 Zone 1 Revised … for Fabrication (Seq 172)`) —
with hold reasons recorded against the balance. The folders, registers, tracker
and `.chain.json` live under `sample\` on this machine (gitignored, so the
evidence is here, not in the repo).

**Extraction was already correct: 0/119 wrong on member mark, 0/119 on
revision.** The real title block is a *table* — labels in one text block,
values in another — so label-adjacency regexes never reach it. It works anyway
because tier 3 (largest mark-shaped text in the title block) picks up the
Assembly # value at x≈0.89, y≈0.94, and the filename tier confirms the revision
from `17172C172  - Rev B.pdf`. **Do not assume the labelled tiers are what is
carrying this client** — they are not.

Five things around the extractor were wrong and are now fixed:

1. **Zones** — only the first of "Zone 1 and Zone 2" was captured, and excising
   the zone tokens mangled the title into "Stairs at  and Zone 2". Zones are now
   a list and are deliberately *not* removed from the title.
2. **Sequence and zone come from the mark**, not the PDF. There is no printed
   sequence number anywhere in these drawings — `seq`/`sheet`/`zone` keywords
   return zero hits. `17172C172` = job 17, seq 172, zone 1 (leading digit).
3. **"Assembly" was being relabelled "Erection"** — it was an alias. Now its own
   category.
4. **Register columns** cut to `S.No | Member Name | Revision No`.
5. **Single-part drawings look like a spreadsheet, not a title block.** The
   `Part # | Qty | In Assembly | Material` column table made the same-line label
   pattern capture the material cell as the mark — 121 of 125 parts came out
   named after a section (`HSS4X4X1/2`) or a length (`29.00`). `member_reject_patterns`
   throws those shapes out so the cascade falls through to the largest text,
   which is the mark. Single parts are then banded by **type** (`TYPE CH`), not
   sequence — a part is cut for an assembly, not erected in a sequence —
   see `part_type_pattern`.

### The fabrication side is now proven on real data

The real IFF folders did not just confirm the synthetic tests; they corrected
them:

- **A fabrication release is not a subset of the approved scope.** The IFA issues
  carried only assemblies (105, then 119). IFF-1 carried 32 assemblies **and 292
  single-part drawings that had never been issued before**. The parts read as
  *added* — correctly, they have no approved baseline of their own — and the
  assembly numbers are the ones that matter for hold tracking. When a real IFF
  folder arrives for another package, expect the same: judge the release by the
  approved categories, not the total count.
- **Of the 119 approved assemblies, IFF-1 released 27; 92 went on hold, and a
  reason is recorded against every one.** The absent-is-not-removed rule behaved
  exactly as designed on real data: nobody was reported removed, and the balance
  the tracker asks about is the outstanding set, not the whole list repeating.
- **IFF-2 was a revised re-release, not a new shipment.** All 17 members (9
  assemblies, 8 parts) had already shipped in IFF-1 and came back at rev `1` —
  "revised as noted". It reported 8 revised and **0 newly released**, so nothing
  from the first shipment read as removed. Fabrication revisions restart the
  letters: approved at `B`/`A`, released at `0`, revised at `1`.
- **Part marks are zone-less and typed** (`17hsp1`, `17p270`, `17ch104`). They
  land in the `TYPE` band with no zone — that is the correct reading, not a
  missing-zone failure.

### Still unproven

- **No real Tekla/SDS2 export has been compared yet.** This is now the only
  unverified part of the whole pipeline. See §7.
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
2. ~~**Push to `origin`.**~~ **Done.** `main` is up to date with `origin/main`.
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
7. ~~**`.gitattributes`** to pin line endings.~~ **Done.** `*.bat` and `*.cmd`
   are pinned to `eol=crlf` so a launcher is CRLF whatever a machine has
   `core.autocrlf` set to — a second workstation checked them out as LF. Keep
   `* text=auto` above the batch rules: the last matching pattern wins.

   Measured while doing it, in case it saves the next person the detour:
   cmd.exe **does** tolerate LF for multi-line `set "X=%X% ..."` accumulation and
   for `^` line continuation. If a build ever comes out with only the last
   `SKIP=` line applied, line endings are not the cause — check that the running
   process was actually started from the current version of the file.

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
| Extra: IFA/IFF package chain + on-hold reasons | `tracking.py`, `tracking_out.py` | Done — verified on **two real IFF folders**: a partial release and a revised re-release |

---

## 10. Notes for an agent working on this

- **Read `config.py` first.** It's the control surface; most "the extractor is
  wrong" problems are a pattern fix there, not a code change.
- **Run `python -m pytest -q` after any change to `extract.py`, `config.py` or
  `folder_meta.py`.** 261 tests, ~20 seconds, and the regexes are genuinely
  subtle.
- **`gui.py` is the largest file but the least risky.** It's presentation over
  the same functions the CLI calls. To exercise it headlessly: construct
  `FabDocApp(tk.Tk())` with `root.withdraw()`, stub `messagebox`, drive methods
  directly, pump `root.update()` in a loop while the worker thread runs. That
  full flow was verified this way.
- **Do not add dependencies casually.** Zero-install-friction is a deliberate
  property; the users are engineers on locked-down corporate Windows machines.
