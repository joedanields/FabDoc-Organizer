"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// Running on the same machine as the browser: real paths are usable, so the
// register can be written straight into the job folder instead of a sandbox.
const LOCAL = document.body.dataset.local === "yes";

let session = null;        // set when the package was uploaded
let localPath = "";        // set when the package was read in place
let project = "";
let packageLabel = "";     // the issue folder's own name, as the chain keys it
let trackerPath = "";
let lastWritten = { register: "", tracker: "", validation: "", diff: "" };

document.querySelectorAll(LOCAL ? ".remote-alt-note" : ".local-only")
  .forEach((el) => (el.hidden = true));
if (LOCAL) document.querySelectorAll(".remote-alt").forEach((el) => (el.hidden = true));

/* ---------------------------------------------------------------- sheets */

/* Generating a register is the whole job, so it is the page. The four
   occasional screens open over it one at a time and close back to it. */

const SHEETS = {
  trackers: "Package Trackers",
  validate: "Validate Members",
  diff:     "Compare Issues",
  settings: "Extraction Settings",
};

let sheetReturn = null;          // what had focus before the sheet opened

function openSheet(name) {
  if (!SHEETS[name]) return;
  sheetReturn = document.activeElement;
  document.querySelectorAll(".sheet-body .panel")
    .forEach((p) => p.classList.toggle("active", p.id === "panel-" + name));
  $("sheet-title").textContent = SHEETS[name];
  $("sheet-host").hidden = false;
  document.body.classList.add("sheet-open");
  $("sheet-close").focus();
  if (name === "trackers") loadTrackers();
  if (name === "settings") loadSettings();
  if (name === "validate") refreshLastRegister();
}

function closeSheet() {
  $("sheet-host").hidden = true;
  document.body.classList.remove("sheet-open");
  // Back to the button that opened it, so a keyboard run does not restart at
  // the top of the document every time a screen is closed.
  if (sheetReturn && document.contains(sheetReturn)) sheetReturn.focus();
  sheetReturn = null;
}

// Header icons and the "Next" buttons on a finished run both open a screen.
document.querySelectorAll("button[data-sheet]").forEach((b) => {
  b.onclick = () => openSheet(b.dataset.sheet);
});
$("sheet-close").onclick = closeSheet;
$("sheet-host").onclick = (e) => { if (e.target === $("sheet-host")) closeSheet(); };

/* Keeping Tab inside whatever is on top. Without it the invisible page behind
   a dialog is still tabbable, and three presses put focus somewhere the user
   cannot see it. */
const FOCUSABLE = 'a[href],button:not(:disabled),input:not(:disabled),' +
                  'select:not(:disabled),textarea:not(:disabled),summary,[tabindex]:not([tabindex="-1"])';

function topLayer() {
  for (const id of ["ask-backdrop", "pick-backdrop", "hold-backdrop", "sheet-host"]) {
    if (!$(id).hidden) return $(id);
  }
  return null;
}

document.addEventListener("keydown", (e) => {
  const layer = topLayer();

  if (e.key === "Escape") {
    if (!layer) return;
    if (layer.id === "ask-backdrop") { closeAsk(null); return; }
    if (layer.id === "pick-backdrop") { closePick(null); return; }
    if (layer.id === "hold-backdrop") return;   // reasons would be lost silently
    closeSheet();
    return;
  }

  if (e.key === "Tab" && layer) {
    const items = [...layer.querySelectorAll(FOCUSABLE)]
      .filter((el) => el.offsetParent !== null);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
});

function status(el, text, kind) {
  el.textContent = text;
  el.className = "status" + (kind ? " " + kind : "");
}

/* ---------------------------------------------------------------- toasts */

/* Reading a package is minutes of work, and the tab is rarely the one being
   looked at when it lands. The outcome shows up where the eye goes next; the
   status line under the button still holds the detail. */

function toast(message, kind, ms) {
  const el = document.createElement("div");
  el.className = "toast" + (kind ? " " + kind : "");
  el.innerHTML =
    `<span class="mark">${kind === "bad" ? "&#9888;" : kind === "good" ? "&#10003;" : "&#8505;"}</span>` +
    `<span class="msg">${esc(message)}</span>` +
    `<button class="x" aria-label="Dismiss">&times;</button>`;
  const drop = () => el.remove();
  el.querySelector(".x").onclick = drop;
  $("toasts").appendChild(el);
  setTimeout(drop, ms || (kind === "bad" ? 9000 : 5000));
}

/* ------------------------------------------------------------------- ask */

/* alert(), confirm() and prompt() freeze the page, can open behind the window
   on a second monitor, and look nothing like the app. One dialog does all
   three; it resolves to null on cancel, and to the typed text (or true) on OK. */

let askResolve = null;

function ask({ title, body = "", value = null, ok = "OK", cancel = "Cancel",
               alt = "" }) {
  $("ask-title").textContent = title;
  $("ask-body").textContent = body;
  $("ask-body").hidden = !body;
  const wantsText = value !== null;
  $("ask-input").hidden = !wantsText;
  $("ask-input").value = wantsText ? value : "";
  $("ask-ok").textContent = ok;
  $("ask-cancel").hidden = !cancel;
  $("ask-cancel").textContent = cancel || "Cancel";
  // The third answer resolves to "alt", so a caller can tell it apart from the
  // plain OK it is offered beside.
  $("ask-alt").hidden = !alt;
  $("ask-alt").textContent = alt || "";
  $("ask-backdrop").hidden = false;
  (wantsText ? $("ask-input") : $("ask-ok")).focus();
  if (wantsText) $("ask-input").select();
  return new Promise((resolve) => (askResolve = resolve));
}

function closeAsk(value) {
  $("ask-backdrop").hidden = true;
  if (askResolve) askResolve(value);
  askResolve = null;
}

$("ask-ok").onclick = () =>
  closeAsk($("ask-input").hidden ? true : $("ask-input").value.trim() || null);
$("ask-cancel").onclick = () => closeAsk(null);
$("ask-alt").onclick = () => closeAsk("alt");
$("ask-input").onkeydown = (e) => { if (e.key === "Enter") $("ask-ok").click(); };
$("ask-backdrop").onclick = (e) => { if (e.target === $("ask-backdrop")) closeAsk(null); };

/* Buttons that start something say so themselves, so the pointer does not have
   to travel to a status line to find out whether the click landed. */
function busy(btn, on) {
  btn.classList.toggle("busy", !!on);
  btn.disabled = !!on;
}

async function send(url, options) {
  const res = await fetch(url, options);
  let body;
  try { body = await res.json(); } catch { body = {}; }
  if (!res.ok) throw new Error(body.error || body.detail || `HTTP ${res.status}`);
  return body;
}

function form(pairs) {
  const fd = new FormData();
  Object.entries(pairs).forEach(([k, v]) => {
    if (v === undefined || v === null) return;
    if (Array.isArray(v)) v.forEach((x) => fd.append(k, x));
    else fd.append(k, typeof v === "boolean" ? String(v) : v);
  });
  return fd;
}

/* --------------------------------------------------------- folder picker */

/* The desktop app opens a native Browse... dialog. A page cannot, and a browser
   file input hands back a name with no path, so the picker walks the server's
   own filesystem - which on a locally-run copy is the engineer's filesystem. */

let pickResolve = null;
let pickHere = "";
let pickKind = "folder";
let pickCursor = -1;             // arrow-key position in the listing

const SUFFIXES = {
  file: ".xlsx,.xlsm,.csv,.tsv,.txt,.pdf",
  folder: "",
};

function pickFolder(start, kind) {
  pickKind = kind || "folder";
  $("pick-title").textContent =
    pickKind === "folder" ? "Choose a folder" : "Choose a file";
  // In file mode clicking a file is the usual way out, but the button still
  // works so a folder can be picked on the compare tab, where a side may be
  // either a package folder or a register workbook.
  $("pick-ok").textContent =
    pickKind === "folder" ? "Use this folder" : "Use this path";
  $("pick-new").hidden = pickKind !== "folder";
  $("pick-backdrop").hidden = false;
  loadPick(start || "");
  return new Promise((resolve) => (pickResolve = resolve));
}

async function loadPick(path) {
  status($("pick-status"), "Reading...");
  try {
    const q = new URLSearchParams({ path, suffixes: SUFFIXES[pickKind] || "" });
    const d = await send("/api/browse?" + q.toString());
    pickHere = d.path;
    $("pick-path").value = d.path;
    $("pick-drives").innerHTML = d.drives
      .map((x) => `<button class="link drive" data-path="${esc(x)}">${esc(x)}</button>`)
      .join(" ");
    $("pick-drives").querySelectorAll(".drive").forEach((b) => {
      b.onclick = () => loadPick(b.dataset.path);
    });

    const dirs = d.dirs.map((n) =>
      `<li><a href="#" class="dir" data-name="${esc(n)}">&#128193; ${esc(n)}</a></li>`);
    const files = (d.files || []).map((n) =>
      `<li><a href="#" class="pfile" data-name="${esc(n)}">&#128196; ${esc(n)}</a></li>`);
    const all = dirs.concat(files);
    $("pick-list").innerHTML = all.length
      ? all.join("")
      : "<li class='hint'>Nothing here.</li>";
    $("pick-list").querySelectorAll(".dir").forEach((a) => {
      a.onclick = (e) => { e.preventDefault(); loadPick(join(pickHere, a.dataset.name)); };
    });
    $("pick-list").querySelectorAll(".pfile").forEach((a) => {
      a.onclick = (e) => { e.preventDefault(); closePick(join(pickHere, a.dataset.name)); };
    });
    pickCursor = -1;
    status($("pick-status"),
      d.error || (d.writable ? "" : "This folder is read-only."),
      d.error || !d.writable ? "bad" : "");
  } catch (err) {
    status($("pick-status"), err.message, "bad");
  }
}

function join(base, name) {
  const sep = base.includes("\\") ? "\\" : "/";
  return base.endsWith(sep) ? base + name : base + sep + name;
}

function closePick(value) {
  $("pick-backdrop").hidden = true;
  if (pickResolve) pickResolve(value);
  pickResolve = null;
}

$("pick-go").onclick = () => loadPick($("pick-path").value.trim());
$("pick-path").onkeydown = (e) => { if (e.key === "Enter") loadPick($("pick-path").value.trim()); };

async function pickUp() {
  const q = new URLSearchParams({ path: pickHere });
  const d = await send("/api/browse?" + q.toString());
  loadPick(d.parent || d.path);
}
$("pick-up").onclick = pickUp;

/* A folder tree is a list, and a list is walked with the arrow keys. Without
   this the only way down a deep job path is a click per level. */
$("pick-backdrop").addEventListener("keydown", (e) => {
  if ($("pick-backdrop").hidden) return;
  const items = [...$("pick-list").querySelectorAll("a")];
  const typing = e.target.tagName === "INPUT";

  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    if (!items.length) return;
    e.preventDefault();
    pickCursor = e.key === "ArrowDown"
      ? Math.min(pickCursor + 1, items.length - 1)
      : Math.max(pickCursor - 1, 0);
    items.forEach((a, i) => a.classList.toggle("cursor", i === pickCursor));
    items[pickCursor].scrollIntoView({ block: "nearest" });
  } else if (e.key === "Enter" && pickCursor >= 0 && !typing) {
    e.preventDefault();
    items[pickCursor].click();
  } else if (e.key === "Backspace" && !typing) {
    e.preventDefault();
    pickUp();
  }
});

$("pick-new").onclick = async () => {
  const name = await ask({
    title: "New folder",
    body: `It will be created in ${pickHere}.`,
    value: "",
    ok: "Create",
  });
  if (!name) return;
  try {
    const d = await send("/api/browse/mkdir", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ parent: pickHere, name }),
    });
    loadPick(d.path);
  } catch (err) {
    status($("pick-status"), err.message, "bad");
  }
};
$("pick-ok").onclick = () => closePick($("pick-path").value.trim() || pickHere);
$("pick-cancel").onclick = () => closePick(null);

document.querySelectorAll("button.browse").forEach((btn) => {
  btn.onclick = async () => {
    const field = $(btn.dataset.target);
    const chosen = await pickFolder(field.value.trim(), btn.dataset.kind);
    if (chosen) {
      field.value = chosen;
      field.dispatchEvent(new Event("change"));
    }
  };
});

/* ------------------------------------------------------- open what we wrote */

async function openWritten(path, reveal) {
  if (!path) return;
  try {
    await send("/api/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, reveal: !!reveal }),
    });
  } catch (err) {
    toast(err.message, "bad");
  }
}

/* ---------------------------------------------------------------- stepper */

/* The five steps stay on the page instead of disappearing on the first scan:
   half way down a long form, "which of these still needs an answer" is a live
   question. Everything before the current step is ticked. */

function setStep(n) {
  $("steps").classList.toggle("roomy", n === 1);
  [...$("steps").children].forEach((li, i) => {
    li.classList.toggle("on", i + 1 === n);
    li.classList.toggle("done", i + 1 < n);
  });
}

/* Which step the form is on, judged by what still has no answer. Called from
   the controls that answer one, so the highlight only ever moves because the
   engineer did something. */
function refreshStep() {
  if (!session && !localPath) { setStep(1); return; }
  if ($("track").checked && !$("stage").value) { setStep(4); return; }
  setStep(5);
}

/* ------------------------------------------------------------- job polling */

/* Reading a thousand drawings is minutes of work. The server runs it on a
   worker and reports [done/total] the way the desktop progress bar does. */

async function runJob(jobId, { bar, statusEl, cancelBtn, label }) {
  if (bar) { bar.hidden = false; bar.value = 0; bar.max = 100; }
  if (cancelBtn) cancelBtn.disabled = false;
  try {
    while (true) {
      const j = await send("/api/job/" + jobId);
      if (j.state === "running") {
        if (bar) { bar.max = Math.max(j.total || 1, 1); bar.value = j.done || 0; }
        status(statusEl, j.total
          ? `[${j.done}/${j.total}] ${j.label}`
          : (j.label || label || "Working..."));
        await new Promise((r) => setTimeout(r, 350));
        continue;
      }
      if (j.state === "cancelled") throw new Error("Cancelled.");
      if (j.state === "error") throw new Error(j.error);
      return j.result;
    }
  } finally {
    if (bar) bar.hidden = true;
    if (cancelBtn) cancelBtn.disabled = true;
  }
}

function cancelJob(jobId) {
  if (jobId) send("/api/job/" + jobId + "/cancel", { method: "POST" }).catch(() => {});
}

/* ------------------------------------------------------------ 1. scanning */

function applyScan(data) {
  session = data.session || null;
  localPath = data.local_path || "";
  project = data.meta.project;
  packageLabel = data.folder || "";

  $("m-title").value = data.meta.title;
  $("m-date").value = data.meta.date;
  $("m-zone").value = data.meta.zone;
  $("m-package").value = data.meta.package;
  $("m-project").value = data.meta.project || "";
  $("round").value = data.meta.issue_no || "";
  // The stage is cleared on every scan on purpose: carrying the previous
  // issue's answer over is exactly how an approval issue gets released as a
  // fabrication one.
  $("stage").value = "";
  document.querySelectorAll('input[name="stagepick"]').forEach((r) => (r.checked = false));

  $("out-folder").value = data.output.folder || "";
  $("out-name").value = data.output.name || "";
  // A tracker already chosen is left alone. The suggested name is derived from
  // this issue's folder title, and every issue of a package titles itself
  // differently ("for Approval", then "for Fabrication") - overwriting the
  // field is how a release ends up in a second tracker with no approved
  // baseline, silently reporting nothing as on hold.
  if (!$("track-folder").value) $("track-folder").value = data.output.tracker_folder || "";
  if (!$("track-name").value) $("track-name").value = data.output.tracker_name || "";
  describeTracker();

  const body = $("cats").querySelector("tbody");
  body.innerHTML = data.categories.map((c) => `
    <tr>
      <td><input type="checkbox" class="cat" value="${esc(c.name)}" checked
                 aria-label="Include ${esc(c.name)}"></td>
      <td>${esc(c.name)}</td>
      <td>${esc(c.folder)}</td>
      <td class="num">${c.count}</td>
      <td>${c.recognised ? esc(c.matched) : "name used as-is"}</td>
    </tr>`).join("");
  countCats();

  ["meta-box", "cat-box", "out-box", "track-box", "run-box"]
    .forEach((id) => ($(id).hidden = false));
  if (LOCAL) $("rescan").hidden = false;
  ["open-tracker"].forEach((id) => ($(id).disabled = true));
  $("result").hidden = true;
  status($("gen-status"), "");
  setStep(2);          // the details and the categories are the next read

  const n = data.categories.length;
  status($("scan-status"),
    `${data.folder} - ${n} categor${n === 1 ? "y" : "ies"}, ${data.pdfs} PDF(s).`,
    n ? "good" : "bad");
  if (!n) toast("No drawing categories found in that folder.", "bad");
  $("meta-box").scrollIntoView({ block: "nearest" });
}

/* How many worksheets the run will write, said next to the tick boxes rather
   than left to be counted by eye. */
function countCats() {
  const all = [...document.querySelectorAll(".cat")];
  const on = all.filter((c) => c.checked);
  const pdfs = on.reduce((sum, c) => {
    const cell = c.closest("tr").querySelector("td.num");
    return sum + (parseInt(cell.textContent, 10) || 0);
  }, 0);
  all.forEach((c) => c.closest("tr").classList.toggle("off", !c.checked));
  status($("cat-count"), all.length
    ? `${on.length} of ${all.length} selected - ${pdfs} drawing(s) will be read.`
    : "", on.length ? "" : "bad");
}

// The tick box is a 15px target in a 40px row, so the row is the target.
$("cats").addEventListener("click", (e) => {
  const row = e.target.closest("tbody tr");
  if (!row) return;
  const box = row.querySelector(".cat");
  if (e.target !== box) box.checked = !box.checked;
  countCats();
});

async function scanLocal() {
  const folder = $("src-folder").value.trim();
  if (!folder) {
    status($("scan-status"), "Choose the project (issue) folder first.", "bad");
    return;
  }
  busy($("scan-local"), true);
  status($("scan-status"), "Scanning...");
  try {
    applyScan(await send("/api/scan/local", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder }),
    }));
  } catch (err) {
    status($("scan-status"), err.message, "bad");
    toast(err.message, "bad");
  } finally {
    busy($("scan-local"), false);
  }
}

if (LOCAL) {
  $("scan-local").onclick = scanLocal;
  $("rescan").onclick = scanLocal;
  $("src-folder").onkeydown = (e) => { if (e.key === "Enter") scanLocal(); };
}

$("scan").onclick = async () => {
  const files = [...$("folder").files].filter((f) => f.name.toLowerCase().endsWith(".pdf"));
  if (!files.length) {
    status($("scan-status"), "Choose a folder containing PDFs.", "bad");
    return;
  }
  const fd = new FormData();
  files.forEach((f) => {
    fd.append("files", f);
    // The relative path is what preserves the category subfolders; without it
    // every drawing would land in one flat folder and become a single worksheet.
    fd.append("paths", f.webkitRelativePath || f.name);
  });

  busy($("scan"), true);
  status($("scan-status"), `Uploading ${files.length} drawing(s)...`);
  try {
    applyScan(await send("/api/scan", { method: "POST", body: fd }));
  } catch (err) {
    status($("scan-status"), err.message, "bad");
    toast(err.message, "bad");
  } finally {
    busy($("scan"), false);
  }
};

/* --------------------------------------------------- existing tracker */

/* Where the tracker is, asked of the server rather than composed here. The
   browser cannot know that an empty name box falls back to a derived one, or
   that a name typed without .xlsx gets the extension - and a path that differs
   by either of those reports on a chain the run is not going to touch. */
function trackerQuery(extra) {
  return {
    folder: LOCAL ? $("track-folder").value.trim() : "",
    name: $("track-name").value.trim(),
    project: $("m-project").value.trim(),
    ...extra,
  };
}

async function describeTracker() {
  const el = $("track-info");
  if (!$("track-name").value.trim()) { status(el, ""); return; }
  try {
    const d = await send("/api/tracker/info", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(trackerQuery({
        stage: $("stage").value, round: $("round").value.trim(),
        label: packageLabel,
      })),
    });
    if (d.clash) {
      // Said here, not at the end of the run: this is the one thing about a
      // chosen tracker that changes what the engineer should do next.
      status(el, `${d.clash.code} is already tracked as "${d.clash.label}" ` +
        `(${d.clash.drawings} drawing(s)). You will be asked whether to ` +
        `overwrite it or add this as ${$("stage").value}-${d.next_round}.`, "bad");
    } else if (d.tracked) {
      status(el, `Adding to "${d.project}" - ${d.issues} issue(s) already tracked` +
        (d.last ? `, last: ${d.last}` : "") + ".", "good");
    } else if (d.exists) {
      status(el, "That file exists but has no chain beside it - it will be replaced.",
        "bad");
    } else {
      status(el, "New tracker - this issue starts the chain.");
    }
  } catch {
    status(el, "");
  }
}

// addEventListener, not onchange: the local-only folder check below binds to
// the same field, and an assignment there silently replaced this one - so on a
// locally-run copy the "adding to which chain" line stopped updating as soon
// as the tracker folder was changed.
$("track-folder").addEventListener("change", describeTracker);
$("track-name").addEventListener("change", describeTracker);
$("round").addEventListener("change", describeTracker);

// Answering anything in the destination or tracker boxes moves the stepper on.
["out-box", "track-box"].forEach((id) =>
  $(id).addEventListener("change", refreshStep));

// Two named cards write the code the API wants into one hidden field, so the
// rest of the client keeps reading $("stage").value.
document.querySelectorAll('input[name="stagepick"]').forEach((r) => {
  r.onchange = () => {
    $("stage").value = r.value;
    status($("gen-status"), "");
    refreshStep();
    describeTracker();          // a stage change moves which round is claimed
  };
});

// Untracked issues have nothing to say about stages, rounds or tracker files.
$("track").onchange = () => {
  $("track-body").hidden = !$("track").checked;
  refreshStep();
};

$("cat-all").onclick = () => {
  document.querySelectorAll(".cat").forEach((c) => (c.checked = true));
  countCats();
};
$("cat-none").onclick = () => {
  document.querySelectorAll(".cat").forEach((c) => (c.checked = false));
  countCats();
};

/* A mistyped destination should be caught while the field still has focus, not
   after five minutes of reading PDFs. */
async function checkFolderField(field, statusEl) {
  const folder = field.value.trim();
  if (!LOCAL || !folder) return;
  try {
    const d = await send("/api/check-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder }),
    });
    if (d.message) status(statusEl, d.message, d.ok ? "" : "bad");
    else if (statusEl.classList.contains("bad")) status(statusEl, "");
  } catch { /* the run itself will report it */ }
}

if (LOCAL) {
  [["out-folder", "gen-status"], ["track-folder", "gen-status"],
   ["v-out-folder", "v-status"], ["d-out-folder", "d-status"]].forEach(([f, s]) =>
    $(f).addEventListener("change", () => checkFolderField($(f), $(s))));
}

/* The stage and round the engineer typed may already belong to another folder:
   the wrong tracker was picked, or that round is being re-issued. The two want
   opposite things done to a chain the whole workbook replays from, so it is
   asked before a single PDF is read - by then the answer would be about a chain
   that had already changed. Returns the answer, or null to call the run off. */
async function askRound() {
  const stage = $("stage").value;
  const round = $("round").value.trim();
  if (!stage || !round) return "next";
  let d;
  try {
    d = await send("/api/tracker/info", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(trackerQuery({ stage, round, label: packageLabel })),
    });
  } catch {
    return "next";              // the run itself will report a bad tracker path
  }
  if (!d.clash) return "next";

  const answer = await ask({
    title: `${d.clash.code} is already tracked`,
    body: `This tracker already holds ${d.clash.code} for "${d.clash.label}" ` +
          `(${d.clash.drawings} drawing(s)), and you are adding ` +
          `"${packageLabel}". Overwrite replaces that issue with this folder and ` +
          `keeps its place in the chain. Adding as ${stage}-${d.next_round} ` +
          `keeps both.`,
    ok: `Add as ${stage}-${d.next_round}`,
    alt: `Overwrite ${d.clash.code}`,
    cancel: "Cancel",
  });
  if (answer === null) return null;
  return answer === "alt" ? "overwrite" : "next";
}

/* ---------------------------------------------------------- 2. generate */

let genJob = null;

$("gen-cancel").onclick = () => {
  cancelJob(genJob);
  status($("gen-status"), "Cancelling...");
};

$("generate").onclick = async () => {
  if (!session && !localPath) return;
  const tracking = $("track").checked;
  if (tracking && !$("stage").value) {
    const msg = "Say what this issue is for before tracking it - it is never guessed.";
    status($("gen-status"), msg, "bad");
    toast(msg, "bad");
    $("track-box").scrollIntoView({ block: "center" });
    document.querySelector('input[name="stagepick"]').focus();
    return;
  }
  const categories = [...document.querySelectorAll(".cat:checked")].map((c) => c.value);
  if (!categories.length) {
    status($("gen-status"), "Select at least one drawing category.", "bad");
    toast("Select at least one drawing category.", "bad");
    $("cat-box").scrollIntoView({ block: "center" });
    return;
  }
  project = $("m-project").value;

  let onClash = "next";
  if (tracking) {
    onClash = await askRound();
    if (onClash === null) {
      status($("gen-status"), "Left alone - nothing was written.");
      return;
    }
  }

  const fd = form({
    session: session || "",
    local_path: localPath,
    title: $("m-title").value,
    date: $("m-date").value,
    zone: $("m-zone").value,
    package: $("m-package").value,
    categories,
    output_folder: LOCAL ? $("out-folder").value : "",
    output_name: $("out-name").value,
    save_default_output: $("out-default").checked,
    track: tracking,
    stage: $("stage").value,
    round_no: $("round").value,
    project,
    tracker_folder: LOCAL ? $("track-folder").value : "",
    tracker_name: $("track-name").value,
    save_default_tracker: $("track-default").checked,
    on_clash: onClash,
  });

  busy($("generate"), true);
  $("result").hidden = false;
  $("kpis").innerHTML = "";
  $("saved-lines").innerHTML = "";
  $("log").textContent = `Processing ${localPath || "the uploaded package"}\n` +
    `Categories: ${categories.join(", ")}\n`;
  try {
    const { job } = await send("/api/generate", { method: "POST", body: fd });
    genJob = job;
    const data = await runJob(job, {
      bar: $("gen-progress"), statusEl: $("gen-status"),
      cancelBtn: $("gen-cancel"), label: "Reading drawings...",
    });
    session = null;                       // an uploaded package is consumed server-side
    renderGenerate(data);
    if (data.holds) openHolds(data.holds);
  } catch (err) {
    status($("gen-status"), err.message, "bad");
    toast(err.message, "bad");
    $("rundetail").open = true;
    $("log").textContent += "\n" + err.message;
  } finally {
    genJob = null;
    busy($("generate"), false);
  }
};

function downloadLink(data, key, pathKey, label) {
  const name = data[key];
  if (!name) return "";
  const href = data.sandboxed === false && data[pathKey]
    ? "/download/file?path=" + encodeURIComponent(data[pathKey])
    : "/download/register/" + encodeURIComponent(name);
  return `<a href="${href}">${label}</a>`;
}

/* The headline numbers of a run. They were a paragraph inside a black log
   pane, where "7 rows need review" reads the same as every other line; the
   count that decides whether to tune the patterns and run again now has to be
   looked at. Tiles, not a chart: four values with nothing to plot between
   them. Colour is only ever state, and always with the word beside it. */
function renderKpis(data) {
  const tiles = [
    { v: data.total, l: "Drawings read" },
    { v: data.categories.length, l: `Worksheet${data.categories.length === 1 ? "" : "s"}` },
  ];
  tiles.push(data.review
    ? { v: data.review, l: "Need review", kind: "warn" }
    : { v: 0, l: "Need review", kind: "good" });
  if (data.chain) {
    tiles.push({ v: data.chain.outstanding || 0, l: "Still on hold",
                 kind: data.chain.outstanding ? "warn" : "good" });
  }
  $("kpis").innerHTML = tiles.map((t) =>
    `<div class="kpi${t.kind ? " " + t.kind : ""}">
       <div class="v">${esc(t.v)}</div><div class="l">${esc(t.l)}</div>
     </div>`).join("");
}

/* Where a file went, and the three things worth doing to it, on one line. */
function savedLine(label, path, download, actions) {
  const el = document.createElement("div");
  el.className = "saved";
  el.innerHTML =
    `<span class="mark">&#10003;</span><strong>${esc(label)}</strong>` +
    `<span class="path">${esc(path)}</span>`;
  (actions || []).forEach(([text, fn]) => {
    const b = document.createElement("button");
    b.textContent = text;
    b.onclick = fn;
    el.appendChild(b);
  });
  if (download) el.insertAdjacentHTML("beforeend", download);
  $("saved-lines").appendChild(el);
}

function renderGenerate(data) {
  const lines = [];
  data.categories.forEach((c) => {
    lines.push(`  ${c.name.padEnd(20)} ${String(c.total).padStart(5)} row(s)` +
      (c.review ? `   (${c.review} need review)` : ""));
  });
  lines.push("");
  lines.push(`Total ${data.total} drawing(s).`);
  if (data.review)
    lines.push(`${data.review} row(s) could not be read confidently and are ` +
      `highlighted in the workbook. If that count is high, tune the patterns ` +
      `on the Extraction Settings tab and run again.`);
  lines.push(`Saved: ${data.register_path || data.register}`);

  if (data.chain) {
    lines.push("");
    // Say where it landed. Renumbering is the right answer to a clash and also
    // the quiet one: the workbook would show an issue the engineer did not ask
    // for, with no line anywhere saying who moved it.
    if (data.round) {
      lines.push(data.round.overwritten
        ? `${data.round.taken} was already tracked - this issue replaced it.`
        : `${data.round.taken} was already tracked - this issue was added as ` +
          `${data.round.code} instead.`);
    }
    lines.push(`Tracker: ${data.chain.project || "(unnamed package)"}`);
    data.chain.issues.forEach((i) => {
      lines.push(`  ${i.n}. ${i.code.padEnd(8)} ${i.label.slice(0, 44).padEnd(46)}` +
        `${String(i.total).padStart(4)}   ${i.verdict}`);
    });
    if (data.chain.outstanding)
      lines.push(`${data.chain.outstanding} approved member(s) still on hold (not removed).`);
    const moved = (data.chain.spec_changes || []).filter(
      (c) => c.issue === data.chain.issues[data.chain.issues.length - 1].label);
    if (moved.length) {
      // What changed, not just that something did: these are the numbers the
      // shop cuts to, and they are worth reading before the workbook is opened.
      lines.push(`${moved.length} quantity/length change(s) in this issue:`);
      moved.slice(0, 10).forEach((c) => lines.push(
        `  ${c.member.padEnd(14)} ${c.field.padEnd(7)} ${c.old} -> ${c.new}` +
        (c.delta ? `   (${c.delta})` : "")));
      if (moved.length > 10) lines.push(`  ... and ${moved.length - 10} more`);
    }
    lines.push(`Tracker saved: ${data.chain.tracker_path || data.chain.tracker}`);
    trackerPath = data.chain.tracker_path || "";
    lastWritten.tracker = trackerPath;
  }
  $("log").textContent = lines.join("\n");

  lastWritten.register = data.register_path || "";
  if (LOCAL) $("open-tracker").disabled = !lastWritten.tracker;

  renderKpis(data);
  $("saved-lines").innerHTML = "";
  savedLine("Register", data.register_path || data.register,
    downloadLink(data, "register", "register_path", "Download"),
    LOCAL && lastWritten.register
      ? [["Open workbook", () => openWritten(lastWritten.register, false)],
         ["Show in folder", () => openWritten(lastWritten.register, true)]]
      : []);

  if (data.chain) {
    const href = data.chain.tracker_sandboxed === false
      ? "/download/file?path=" + encodeURIComponent(data.chain.tracker_path)
      : "/download/tracker/" + encodeURIComponent(data.chain.tracker);
    savedLine("Tracker", data.chain.tracker_path || data.chain.tracker,
      `<a href="${href}">Download</a>`,
      LOCAL && lastWritten.tracker
        ? [["Open tracker", () => openWritten(lastWritten.tracker, false)]]
        : []);
  }

  $("result").hidden = false;
  setStep(6);          // past the last one: every step ticked

  status($("gen-status"),
    data.review
      ? `${data.review} row(s) could not be read confidently - check the highlighted ` +
        "rows, or tune the patterns under Settings and run again."
      : "", data.review ? "bad" : "");

  if (data.round) {
    toast(data.round.overwritten
      ? `${data.round.taken} was overwritten by this issue.`
      : `${data.round.taken} was taken - tracked as ${data.round.code}.`, "good");
  }
  toast(`Register written: ${data.register}`, "good");
  $("result").scrollIntoView({ behavior: "smooth", block: "nearest" });
  refreshLastRegister();
}

$("open-tracker").onclick = () => openWritten(lastWritten.tracker, false);

// The long way down a form is not a reason to reach for the mouse.
document.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || !(e.ctrlKey || e.metaKey)) return;
  if (topLayer()) return;
  if ($("run-box").hidden || $("generate").disabled) return;
  e.preventDefault();
  $("generate").click();
});

/* ------------------------------------------------------- 3. hold dialog */

function openHolds(holds) {
  trackerPath = holds.tracker_path || trackerPath;
  $("hold-intro").innerHTML =
    `<strong>${esc(holds.release)}</strong><br>` +
    `${holds.released} member(s) released, ${holds.members.length} approved member(s) ` +
    `not in this release. They are <strong>on hold, not removed</strong> - fabrication ` +
    `ships the approved scope in slices. State why they are waiting; the reason is ` +
    `written into the tracker.`;

  const body = $("hold-table").querySelector("tbody");
  body.innerHTML = holds.members.map((m) => `
    <tr data-member="${esc(m.id || m.member)}">
      <td><input type="checkbox" class="holdsel"></td>
      <td>${esc(m.member)}</td>
      <td class="num">${esc(m.category || "-")}</td>
      <td class="num">${esc(m.zone || "-")}</td>
      <td class="num">${esc(m.revision || "-")}</td>
      <td>${esc(m.since)}</td>
      <td><input type="text" class="reason" value="${esc(m.reason)}"></td>
    </tr>`).join("");
  body.querySelectorAll(".reason").forEach((i) => (i.oninput = countHolds));
  body.querySelectorAll(".holdsel").forEach((c) => {
    c.setAttribute("aria-label", "Select " + c.closest("tr").dataset.member);
    c.onchange = () => c.closest("tr").classList.toggle("sel", c.checked);
  });
  countHolds();
  $("hold-backdrop").hidden = false;
  $("hold-all").focus();
}

function countHolds() {
  const inputs = [...document.querySelectorAll("#hold-table .reason")];
  const blank = inputs.filter((i) => !i.value.trim()).length;
  status($("hold-count"), blank
    ? `${blank} member(s) still have no reason - they will be highlighted in the tracker.`
    : "Every member has a reason.");
}

$("apply-all").onclick = () => {
  const value = $("hold-all").value.trim();
  if (!value) return;
  document.querySelectorAll("#hold-table .reason").forEach((i) => (i.value = value));
  countHolds();
};

$("apply-sel").onclick = () => {
  const value = $("hold-sel").value.trim();
  if (!value) return;
  document.querySelectorAll("#hold-table tbody tr").forEach((tr) => {
    if (tr.querySelector(".holdsel").checked) tr.querySelector(".reason").value = value;
  });
  countHolds();
};

$("hold-cancel").onclick = () => {
  $("hold-backdrop").hidden = true;
  $("log").textContent += "\n\nTracker written, but hold reasons were not recorded.";
  toast("Tracker written. Hold reasons were not recorded.");
};

$("hold-save").onclick = async () => {
  if (!project && !trackerPath) {        // nothing tracked yet: nothing to save against
    $("hold-backdrop").hidden = true;
    return;
  }
  const reasons = {};
  document.querySelectorAll("#hold-table tbody tr").forEach((tr) => {
    const value = tr.querySelector(".reason").value.trim();
    if (value) reasons[tr.dataset.member] = value;
  });
  busy($("hold-save"), true);
  try {
    const data = await send("/api/tracker/reasons", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, reasons, tracker_path: trackerPath }),
    });
    $("hold-backdrop").hidden = true;
    $("log").textContent += `\n\nReasons recorded for ${data.saved} member(s).` +
      (data.without_reason ? `\n${data.without_reason} still without a reason.` : "");
    toast(`Reasons recorded for ${data.saved} member(s).`, "good");
  } catch (err) {
    status($("hold-count"), err.message, "bad");
  } finally {
    busy($("hold-save"), false);
  }
};

/* --------------------------------------------------------- 4. trackers */

async function loadTrackers() {
  try {
    const data = await send("/api/trackers");
    $("tracker-list").innerHTML = data.trackers.length
      ? data.trackers.map((t) => `
          <li><a href="#" data-name="${esc(t.name)}">${esc(t.name)}</a>
              &nbsp;<a href="/download/tracker/${encodeURIComponent(t.file)}">download</a></li>`).join("")
      : "<li class='hint'>No trackers on the server. Trackers you saved to a folder " +
        "of your own are not listed here - open them from that folder.</li>";
    $("tracker-list").querySelectorAll("a[data-name]").forEach((a) => {
      a.onclick = (e) => { e.preventDefault(); showTracker(a.dataset.name); };
    });
  } catch (err) {
    $("tracker-list").innerHTML = `<li class="hint">${esc(err.message)}</li>`;
  }
}
$("refresh-trackers").onclick = loadTrackers;

async function showTracker(name) {
  const data = await send("/api/tracker/" + encodeURIComponent(name));
  const rows = data.issues.map((i) => `
    <tr class="row-${i.stage.toLowerCase()}">
      <td class="num">${i.n}</td>
      <td><span class="pill ${i.stage.toLowerCase()}">${esc(i.code)}</span></td>
      <td>${esc(i.label)}</td><td>${esc(i.date)}</td>
      <td class="num">${i.total}</td><td class="num">${i.added}</td>
      <td class="num">${i.revised}</td><td class="num">${i.removed}</td>
      <td class="num">${i.released || ""}</td><td class="num">${i.on_hold || ""}</td>
      <td>${esc(i.verdict)}</td>
    </tr>`).join("");

  const held = data.holds.filter((h) => !h.released);
  const holdRows = held.map((h) => `
    <tr class="${h.reason ? "row-hold" : "row-warn"}">
      <td>${esc(h.member)}</td><td class="num">${esc(h.category || "-")}</td>
      <td class="num">${esc(h.zone)}</td>
      <td class="num">${esc(h.revision)}</td><td>${esc(h.since)}</td>
      <td>${esc(h.reason) || "<em>no reason recorded</em>"}</td>
    </tr>`).join("");

  $("tracker-detail").innerHTML = `
    <fieldset><legend>${esc(data.project)}</legend>
      <p class="hint">Approved baseline: <strong>${esc(data.baseline) || "(none yet)"}</strong></p>
      <div class="tablewrap"><table><thead><tr>
        <th class="num">#</th><th>Code</th><th>Issue</th><th>Date</th>
        <th class="num">Drawings</th><th class="num">Added</th><th class="num">Revised</th>
        <th class="num">Removed</th><th class="num">Released</th><th class="num">On hold</th>
        <th>Status</th></tr></thead><tbody>${rows}</tbody></table></div>
    </fieldset>
    ${held.length ? `<fieldset><legend>On hold &mdash; ${held.length} approved member(s) not yet released</legend>
      <div class="tablewrap"><table><thead><tr><th>Member</th><th class="num">Zone</th><th class="num">Rev</th>
      <th>On hold since</th><th>Reason</th></tr></thead><tbody>${holdRows}</tbody></table></div>
    </fieldset>` : ""}`;
}

/* --------------------------------------------------------- 5. validate */

/* A result pane with one tab per bucket, the way the desktop app shows it. A
   count alone does not tell an engineer *which* member is missing, and that is
   the only question this screen exists to answer. */

function resultTabs(host, groups) {
  const id = host.id;
  host.innerHTML = `
    <nav class="subtabs">${groups.map((g, i) =>
      `<button class="subtab${i ? "" : " active"}" data-i="${i}">${esc(g.title)} (${g.count})</button>`
    ).join("")}</nav>
    ${groups.map((g, i) => `
      <div class="subpanel${i ? "" : " active"}" data-i="${i}">
        ${g.rows.length ? `<div class="tablewrap"><table>
          <thead><tr>${g.columns.map((c) => `<th>${esc(c.label)}</th>`).join("")}</tr></thead>
          <tbody>${g.rows.map((r) => `<tr>${g.columns.map((c) =>
            `<td${c.num ? ' class="num"' : ""}>${esc(r[c.key])}</td>`).join("")}</tr>`).join("")}
          </tbody></table></div>
          ${g.count > g.rows.length
            ? `<p class="hint">Showing the first ${g.rows.length} of ${g.count}. The
               workbook has them all.</p>` : ""}`
          : `<p class="hint">Nothing in this list.</p>`}
      </div>`).join("")}`;

  host.querySelectorAll(".subtab").forEach((b) => {
    b.onclick = () => {
      host.querySelectorAll(".subtab").forEach((x) => x.classList.remove("active"));
      host.querySelectorAll(".subpanel").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      host.querySelector(`.subpanel[data-i="${b.dataset.i}"]`).classList.add("active");
    };
  });
  host.id = id;
}

const DRAWING_COLS = [
  { key: "member", label: "Member Name" },
  { key: "category", label: "Category" },
  { key: "seq", label: "S.No", num: true },
  { key: "rev", label: "Rev", num: true },
  { key: "file", label: "Source File" },
];

async function refreshLastRegister() {
  try {
    const d = await send("/api/last-register");
    if (!d.available) {
      $("v-last").textContent = "No register has been generated yet.";
      $("v-src-last").disabled = true;
      document.querySelector('input[name="regsrc"][value="file"]').checked = true;
      updateRegSource();
      return;
    }
    $("v-src-last").disabled = false;
    $("v-last").textContent =
      `${d.label} - ${d.total} drawing(s) across ${d.categories.length} ` +
      `categor${d.categories.length === 1 ? "y" : "ies"}.`;
    if ($("v-src-last").checked) fillCategories(d.categories);
  } catch { /* the panel still works without it */ }
}

function fillCategories(names) {
  $("v-category").innerHTML = ["(all)"].concat(names)
    .map((n) => `<option>${esc(n)}</option>`).join("");
}

function updateRegSource() {
  const useFile = document.querySelector('input[name="regsrc"]:checked').value === "file";
  $("v-register").disabled = !useFile;
  $("v-register-path").disabled = !useFile;
  $("v-reg-pathrow").querySelector("button").disabled = !useFile;
  if (!useFile) refreshLastRegister();
}
document.querySelectorAll('input[name="regsrc"]').forEach((r) => (r.onchange = updateRegSource));

async function previewRegister() {
  const file = $("v-register").files[0];
  const path = $("v-register-path").value.trim();
  if (!file && !path) return;
  try {
    const d = await send("/api/register/preview", {
      method: "POST",
      body: form({ register_file: file, path }),
    });
    fillCategories(d.categories);
    status($("v-status"), `Loaded ${d.label}: ${d.total} drawing(s).`, "good");
  } catch (err) {
    status($("v-status"), err.message, "bad");
  }
}
$("v-register").onchange = previewRegister;
$("v-register-path").onchange = previewRegister;

async function previewMembers() {
  const file = $("v-members").files[0];
  const path = $("v-members-path").value.trim();
  if (!file && !path) return;
  try {
    const d = await send("/api/members/preview", {
      method: "POST",
      body: form({ members: file, path, sheet: $("v-sheet").value }),
    });
    // A CSV or a text list has no worksheets, and an empty dropdown reads as
    // something that failed to load rather than something that does not apply.
    $("v-sheet").disabled = !d.sheets.length;
    $("v-sheet").innerHTML = d.sheets.length
      ? d.sheets.map((s) =>
          `<option${s === d.sheet ? " selected" : ""}>${esc(s)}</option>`).join("")
      : "<option value=''>(not a workbook)</option>";
    $("v-column").innerHTML = d.columns
      .map((c) => `<option${c === d.column ? " selected" : ""}>${esc(c)}</option>`).join("");
    $("v-model-status").textContent =
      `${d.count} member(s) read from column '${d.column}'.`;
  } catch (err) {
    $("v-model-status").textContent = err.message;
  }
}
$("v-members").onchange = () => { $("v-sheet").innerHTML = ""; previewMembers(); };
$("v-members-path").onchange = () => { $("v-sheet").innerHTML = ""; previewMembers(); };
$("v-sheet").onchange = previewMembers;

$("validate").onclick = async () => {
  const useLast = document.querySelector('input[name="regsrc"]:checked').value === "last";
  const reg = $("v-register").files[0];
  const regPath = $("v-register-path").value.trim();
  const mem = $("v-members").files[0];
  const memPath = $("v-members-path").value.trim();

  if (!useLast && !reg && !regPath) {
    status($("v-status"), "Choose a register workbook, or generate one first.", "bad");
    return;
  }
  if (!mem && !memPath) {
    status($("v-status"), "Choose the model member list.", "bad");
    return;
  }

  busy($("validate"), true);
  status($("v-status"), "Comparing...");
  try {
    const d = await send("/api/validate", {
      method: "POST",
      body: form({
        register_file: reg, members: mem,
        register_path: regPath, members_path: memPath,
        use_last: useLast,
        sheet: $("v-sheet").value, column: $("v-column").value,
        category: $("v-category").value,
        case_insensitive: $("v-ci").checked,
        ignore_whitespace: $("v-ws").checked,
        strip_leading_zeros: $("v-lz").checked,
        output_folder: LOCAL ? $("v-out-folder").value : "",
        output_name: $("v-out-name").value,
      }),
    });
    lastWritten.validation = d.report_path || "";
    if (LOCAL) $("v-open").disabled = !lastWritten.validation;

    const href = d.sandboxed === false
      ? "/download/file?path=" + encodeURIComponent(d.report_path)
      : "/download/register/" + encodeURIComponent(d.report);
    $("v-status").innerHTML =
      `<span class="${d.clean ? "good" : "bad"}">${esc(d.verdict)}</span> ` +
      `&nbsp;<a href="${href}">Download report</a>`;
    $("v-status").className = "status";

    $("v-result").innerHTML = `
      <p class="hint">${esc(d.register)} against column <strong>${esc(d.column)}</strong>
         &mdash; ${d.model_count} model member(s), ${d.drawing_count} drawing member(s).
         ${d.unnamed ? `<span class="bad">${d.unnamed} drawing(s) had no member name and
         were left out - check the highlighted rows in the register.</span>` : ""}</p>
      <div id="v-tabs"></div>`;
    resultTabs($("v-tabs"), [
      { title: "Missing in Drawings", count: d.counts.missing, rows: d.missing_rows,
        columns: [{ key: "member", label: "Member Name (in model)" }] },
      { title: "Not in Model", count: d.counts.extra, rows: d.extra_rows,
        columns: DRAWING_COLS },
      { title: "Matched", count: d.counts.matched, rows: d.matched_rows,
        columns: DRAWING_COLS },
      { title: "Duplicates", count: d.counts.duplicates, rows: d.duplicate_rows,
        columns: [{ key: "member", label: "Member Name" },
                  { key: "count", label: "Count", num: true },
                  { key: "file", label: "Source Files" }] },
    ]);
    toast(d.verdict, d.clean ? "good" : "bad");
  } catch (err) {
    status($("v-status"), err.message, "bad");
    toast(err.message, "bad");
  } finally {
    busy($("validate"), false);
  }
};
$("v-open").onclick = () => openWritten(lastWritten.validation, false);

/* -------------------------------------------------------------- 6. diff */

let diffJob = null;
$("d-cancel").onclick = () => { cancelJob(diffJob); status($("d-status"), "Cancelling..."); };

$("diff").onclick = async () => {
  const older = $("d-old").files[0], newer = $("d-new").files[0];
  const oldPath = $("d-old-path").value.trim(), newPath = $("d-new-path").value.trim();
  if ((!older && !oldPath) || (!newer && !newPath)) {
    status($("d-status"), "Choose both the older and the newer issue.", "bad");
    return;
  }

  busy($("diff"), true);
  status($("d-status"), "Comparing...");
  try {
    const { job } = await send("/api/diff", {
      method: "POST",
      body: form({
        old: older, new: newer, old_path: oldPath, new_path: newPath,
        case_insensitive: $("d-ci").checked,
        ignore_whitespace: $("d-ws").checked,
        strip_leading_zeros: $("d-lz").checked,
        output_folder: LOCAL ? $("d-out-folder").value : "",
        output_name: $("d-out-name").value,
      }),
    });
    diffJob = job;
    const d = await runJob(job, {
      bar: $("d-progress"), statusEl: $("d-status"),
      cancelBtn: $("d-cancel"), label: "Comparing...",
    });

    lastWritten.diff = d.report_path || "";
    if (LOCAL) $("d-open").disabled = !lastWritten.diff;
    const href = d.sandboxed === false
      ? "/download/file?path=" + encodeURIComponent(d.report_path)
      : "/download/register/" + encodeURIComponent(d.report);
    $("d-status").innerHTML =
      `<span class="${d.identical ? "good" : "bad"}">${esc(d.verdict)}</span> ` +
      `&nbsp;[${d.old_total} drawing(s) &rarr; ${d.new_total}] ` +
      `&nbsp;<a href="${href}">Download report</a>`;
    $("d-status").className = "status";

    $("d-result").innerHTML = `<div id="d-tabs"></div>`;
    const cols = [
      { key: "member", label: "Member Name" },
      { key: "zone", label: "Zone", num: true },
      { key: "old", label: "Old Rev", num: true },
      { key: "new", label: "New Rev", num: true },
      { key: "qty", label: "Qty Old / New", num: true },
    ];
    resultTabs($("d-tabs"), [
      { title: "Added", count: d.counts.added, rows: d.added_rows, columns: cols },
      { title: "Removed", count: d.counts.removed, rows: d.removed_rows, columns: cols },
      { title: "Revision Changed", count: d.counts.revised, rows: d.revised_rows, columns: cols },
      { title: "Unchanged", count: d.counts.unchanged, rows: d.unchanged_rows, columns: cols },
    ]);
    toast(d.verdict, d.identical ? "good" : "bad");
  } catch (err) {
    status($("d-status"), err.message, "bad");
    toast(err.message, "bad");
  } finally {
    diffJob = null;
    busy($("diff"), false);
  }
};
$("d-open").onclick = () => openWritten(lastWritten.diff, false);

/* ---------------------------------------------------------- 7. settings */

const PATTERN_FIELDS = {
  "p-member": "member_patterns",
  "p-revision": "revision_patterns",
  "p-sequence": "sequence_patterns",
  "p-filename": "filename_patterns",
};

function applySettings(s) {
  Object.entries(PATTERN_FIELDS).forEach(([id, key]) => {
    $(id).value = (s.profile[key] || []).join("\n");
  });
  const rect = s.profile.title_block_rect || [0, 0, 1, 1];
  ["r-left", "r-top", "r-right", "r-bottom"].forEach((id, i) => ($(id).value = rect[i]));
  $("p-largest").checked = !!s.profile.use_largest_text_fallback;
  $("s-daryfirst").checked = !!s.day_first_dates;
  $("s-source").checked = !!s.include_source_column;
  $("s-zone").checked = !!s.group_by_zone;
  $("s-outdir").value = s.default_output_folder || "";
  $("s-trackdir").value = s.default_tracker_folder || "";
}

function collectProfile() {
  const profile = {};
  Object.entries(PATTERN_FIELDS).forEach(([id, key]) => {
    profile[key] = $(id).value.split("\n").map((l) => l.trim()).filter(Boolean);
  });
  profile.title_block_rect = ["r-left", "r-top", "r-right", "r-bottom"]
    .map((id) => parseFloat($(id).value));
  profile.use_largest_text_fallback = $("p-largest").checked;
  return profile;
}

let settingsLoaded = false;
async function loadSettings(force) {
  if (settingsLoaded && !force) return;
  try {
    applySettings(await send("/api/settings"));
    settingsLoaded = true;
    status($("s-status"), "");
  } catch (err) {
    status($("s-status"), err.message, "bad");
  }
}

$("save-settings").onclick = async () => {
  const rect = ["r-left", "r-top", "r-right", "r-bottom"].map((id) => parseFloat($(id).value));
  if (rect.some((v) => Number.isNaN(v))) {
    status($("s-status"), "The title block region must be four numbers.", "bad");
    return;
  }
  try {
    const d = await send("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        profile: collectProfile(),
        day_first_dates: $("s-daryfirst").checked,
        include_source_column: $("s-source").checked,
        group_by_zone: $("s-zone").checked,
        default_output_folder: $("s-outdir").value,
        default_tracker_folder: $("s-trackdir").value,
      }),
    });
    status($("s-status"), `Saved to ${d.saved_to}`, "good");
    toast("Settings saved.", "good");
  } catch (err) {
    status($("s-status"), err.message, "bad");
    toast(err.message, "bad");
  }
};

$("reset-settings").onclick = async () => {
  const yes = await ask({
    title: "Restore defaults?",
    body: "The default extraction patterns replace what is in the boxes. " +
          "Nothing is written to disk until you press Save settings.",
    ok: "Restore",
  });
  if (!yes) return;
  try {
    applySettings(await send("/api/settings/defaults"));
    status($("s-status"), "Defaults restored (not yet saved).");
  } catch (err) {
    status($("s-status"), err.message, "bad");
  }
};

$("run-test").onclick = async () => {
  const file = $("t-pdf").files[0];
  const path = $("t-path").value.trim();
  if (!file && !path) {
    status($("t-status"), "Choose a drawing PDF to test against.", "bad");
    return;
  }
  busy($("run-test"), true);
  status($("t-status"), "Reading...");
  try {
    const d = await send("/api/settings/test", {
      method: "POST",
      body: form({ pdf: file, path, profile: JSON.stringify(collectProfile()) }),
    });
    $("t-out").textContent = d.text;
    status($("t-status"), d.ok ? "Member name found." : "No member name came out.",
      d.ok ? "good" : "bad");
  } catch (err) {
    status($("t-status"), err.message, "bad");
    $("t-out").textContent = err.message;
  } finally {
    busy($("run-test"), false);
  }
};
$("t-path").onchange = () => $("run-test").click();
$("t-pdf").onchange = () => $("run-test").click();

/* ------------------------------------------------------------- start-up */

updateRegSource();
refreshLastRegister();
setStep(1);
