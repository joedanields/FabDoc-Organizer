# FabDoc Organizer — Web

A FastAPI + HTML front end for the same engine the desktop app uses.

Nothing about extraction, registers, validation or tracking is reimplemented
here. `app/main.py` imports `fabdoc` from the checkout one level up and is
transport only, so a rule fixed in the package is fixed in all three front ends
at once — CLI, tkinter and browser.

## Run

Double-click **`FabDoc Web.bat`**, or:

```
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8000
```

Then open <http://127.0.0.1:8000>. The launcher installs the dependencies on
first run and opens the browser for you.

## Why it is a separate folder

The desktop app deliberately depends on nothing but PyMuPDF and openpyxl —
engineers install it on locked-down corporate Windows machines, and tkinter ships
with CPython. FastAPI, uvicorn and their transitive dependencies would break that
property for everyone, including people who never open a browser.

Keeping the web app here means `pip install -r requirements.txt` at the repo root
still gets you a working desktop app with two dependencies, and only someone who
wants the browser version pays for the rest.

## The four tabs

| Tab | What it does |
| --- | --- |
| 1. Generate & Track | Upload an issue folder, check the metadata and categories, write the register, and append the issue to its package tracker |
| 2. Package Trackers | Every tracker on the server, with its full issue chain and what is still on hold |
| 3. Validate Members | A register workbook against a model member list |
| 4. Compare Issues | Two register workbooks, issue against issue |

## Uploading a folder

Tab 1 uses a directory picker (`webkitdirectory`), so pick the **issue folder
itself**, not the drawings inside it. The subfolder structure is what category
detection reads — an `assembly` subfolder becomes an Assembly worksheet — so the
browser sends each file's relative path alongside it and the server rebuilds the
tree before scanning. Flattening the upload would collapse every category into
one worksheet.

Supported in Chrome, Edge and Firefox. The PDFs are deleted as soon as the
register is written: everything the tracker needs is already in the chain state.

## Fabrication releases and the hold dialog

When an issue is tracked as **IFF** and the release does not carry the whole
approved scope, a dialog opens listing the members left behind. They are **on
hold, not removed** — fabrication ships the approved scope in slices, and calling
them removed would be a false alarm on the one document the shop floor acts on.

One reason fills the whole batch; individual rows can be overridden. Reasons
given in an earlier release arrive pre-filled, so each release asks about a
smaller balance rather than the same list again. Reasons land in the tracker's
**On Hold** sheet, amber where nobody has said why yet.

The stage is never inferred from the folder name. An approval issue mistaken for
a fabrication release would rewrite the approved baseline and report the rest of
the package as unshipped, so the picker starts blank and refuses to run until set.

## Where things are written

```
web/data/
  uploads/    one workspace per upload, deleted once its register is written
  output/     generated registers and reports, served from /download/register/
  trackers/   one tracker + chain state per project, served from /download/tracker/
```

Trackers are keyed by **project**, which is how issues chain: issue 20 uploaded
today finds the tracker issue 10 created last week. The project is derived from
the folder name — a leading stage code and a trailing "for Approval" clause are
stripped, since both change every issue while the package does not — and stays
editable on tab 1 in case a naming convention needs correcting.

## Scope

Meant for one team on a trusted network, in the way the desktop app is meant for
one engineer. There is no authentication, no per-user separation, and no upload
size cap; trackers are shared by everyone who can reach the port. Bind it to
`127.0.0.1` (as the launcher does) unless you have added those things yourself.

Extraction patterns are read from `~/.fabdoc/settings.json`, the same profile the
desktop app calibrates. There is no settings tab here yet — calibrate with the
desktop app or `python -m fabdoc calibrate`.
