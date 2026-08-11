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

## The five tabs

They mirror the desktop app one for one, plus a trackers tab it has no room for.

| Tab | What it does |
| --- | --- |
| 1. Generate & Track | Point at an issue folder, check the metadata and categories, write the register, and append the issue to its package tracker |
| 2. Validate Members | A register against a model member list — worksheet and column pickers, the three normalisation options, a category filter, and every row of every result |
| 3. Compare Issues | Two issues, each side a package folder or a register workbook |
| 4. Extraction Settings | The pattern editor and the Test button, saved to the same profile the desktop app and the CLI read |
| 5. Package Trackers | Every tracker on the server, with its full issue chain and what is still on hold |

Long runs — a register build, a folder-to-folder comparison — report
`[412/968] Assembly: 17172C172.pdf` into a progress bar and can be cancelled,
the same as the desktop app.

## Two ways to run it, and what changes between them

**Locally** (the launcher, and the packaged executable) the browser and the
server are the same machine, so the app behaves like the desktop one:

* Point tab 1 at a folder path and it is **read where it sits** — no upload. A
  package is thousands of PDFs, and copying them to a server running on the same
  disk they already live on is minutes of nothing.
* Every destination is a path field with a **Browse…** button that walks the real
  filesystem, so the register lands in `P:\Jobs\17172` instead of Downloads.
* **Remember this as the default** on the output and tracker folders writes the
  choice to `~/.fabdoc/settings.json`, so the path is not retyped every issue.
  The desktop app reads the same setting.
* **Open workbook** and **Show in folder** open what was just written.

**As a shared server** (`FABDOC_LOCAL=0`, or any client that is not on the
machine) none of that is offered. The folder is uploaded, the register is written
to the sandbox under `web/data/`, and everything comes back as a download link.
Path browsing, local reads and local writes are refused with a 403: letting a
remote browser enumerate and write to the server's disk is a different program
with different risks.

Opening and downloading are restricted to files the app itself wrote, on either
setting. A path in a request is not enough to fetch it.

## Uploading a folder

The upload path (`webkitdirectory`) wants the **issue folder itself**, not the
drawings inside it. The subfolder structure is what category detection reads — an
`assembly` subfolder becomes an Assembly worksheet — so the browser sends each
file's relative path alongside it and the server rebuilds the tree before
scanning. Flattening the upload would collapse every category into one worksheet.

Supported in Chrome, Edge and Firefox. The PDFs are deleted as soon as the
register is written: everything the tracker needs is already in the chain state.

## Fabrication releases and the hold dialog

When an issue is tracked as **IFF** and the release does not carry the whole
approved scope, a dialog opens listing the members left behind. They are **on
hold, not removed** — fabrication ships the approved scope in slices, and calling
them removed would be a false alarm on the one document the shop floor acts on.

One reason fills the whole batch, ticked rows can be overridden together, and
individual rows can be edited. Reasons given in an earlier release arrive
pre-filled, so each release asks about a smaller balance rather than the same
list again. Reasons land in the tracker's **On Hold** sheet, amber where nobody
has said why yet.

The stage is never inferred from the folder name. An approval issue mistaken for
a fabrication release would rewrite the approved baseline and report the rest of
the package as unshipped, so the picker starts blank and refuses to run until set.

## Where things are written

Anything you gave a path to goes to that path. Everything else goes here:

```
web/data/
  uploads/    one workspace per upload, deleted once its register is written
  output/     registers and reports written without a folder, served from /download/register/
  trackers/   trackers written without a folder, served from /download/tracker/
```

Set `FABDOC_DATA` to move that directory. A frozen executable keeps it beside the
`.exe` rather than in the temporary unpack directory, which would take the
package chain with it on exit.

Trackers written into `web/data/trackers/` are keyed by **project**, which is how
issues chain: issue 20 uploaded today finds the tracker issue 10 created last
week. The project is derived from the folder name — a leading stage code and a
trailing "for Approval" clause are stripped, since both change every issue while
the package does not — and stays editable on tab 1 in case a naming convention
needs correcting. When you choose a tracker path yourself, that file is the
chain; keep naming the same file and the issues keep chaining onto it.

Tab 5 lists only the trackers under `web/data/trackers/`. One you saved into a
job folder is opened from that folder.

## Scope

Meant for one engineer on their own machine, or one team on a trusted network, in
the way the desktop app is meant for one engineer. There is no authentication, no
per-user separation, and no upload size cap; trackers under `web/data/` are shared
by everyone who can reach the port. Bind it to `127.0.0.1` (as the launcher does)
unless you have added those things yourself.
