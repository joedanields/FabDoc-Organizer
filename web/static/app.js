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
let trackerPath = "";
let lastWritten = { register: "", tracker: "", validation: "", diff: "" };

document.querySelectorAll(LOCAL ? ".remote-alt-note" : ".local-only")
  .forEach((el) => (el.hidden = true));
if (LOCAL) document.querySelectorAll(".remote-alt").forEach((el) => (el.hidden = true));

/* ------------------------------------------------------------------ tabs */

document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $("panel-" + tab.dataset.panel).classList.add("active");
    if (tab.dataset.panel === "trackers") loadTrackers();
    if (tab.dataset.panel === "settings") loadSettings();
    if (tab.dataset.panel === "validate") refreshLastRegister();
  };
});

function status(el, text, kind) {
  el.textContent = text;
  el.className = "status" + (kind ? " " + kind : "");
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
$("pick-up").onclick = async () => {
  const q = new URLSearchParams({ path: pickHere });
  const d = await send("/api/browse?" + q.toString());
  loadPick(d.parent || d.path);
};
$("pick-new").onclick = async () => {
  const name = prompt("Name for the new folder:");
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
    alert(err.message);
  }
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

  $("m-title").value = data.meta.title;
  $("m-date").value = data.meta.date;
  $("m-zone").value = data.meta.zone;
  $("m-package").value = data.meta.package;
  $("m-project").value = data.meta.project || "";
  $("round").value = data.meta.issue_no || "";
  $("stage").value = "";

  $("out-folder").value = data.output.folder || "";
  $("out-name").value = data.output.name || "";
  $("track-folder").value = data.output.tracker_folder || "";
  $("track-name").value = data.output.tracker_name || "";

  const body = $("cats").querySelector("tbody");
  body.innerHTML = data.categories.map((c) => `
    <tr>
      <td><input type="checkbox" class="cat" value="${esc(c.name)}" checked></td>
      <td>${esc(c.name)}</td>
      <td>${esc(c.folder)}</td>
      <td class="num">${c.count}</td>
      <td>${c.recognised ? esc(c.matched) : "name used as-is"}</td>
    </tr>`).join("");

  ["meta-box", "cat-box", "out-box", "track-box", "run-box"]
    .forEach((id) => ($(id).hidden = false));
  ["open-book", "reveal-book", "open-tracker"].forEach((id) => ($(id).disabled = true));
  $("log").hidden = true;
  status($("gen-status"), "");

  const n = data.categories.length;
  status($("scan-status"),
    `${data.folder} - ${n} categor${n === 1 ? "y" : "ies"}, ${data.pdfs} PDF(s).`,
    n ? "good" : "bad");
}

async function scanLocal() {
  const folder = $("src-folder").value.trim();
  if (!folder) {
    status($("scan-status"), "Choose the project (issue) folder first.", "bad");
    return;
  }
  $("scan-local").disabled = true;
  status($("scan-status"), "Scanning...");
  try {
    applyScan(await send("/api/scan/local", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder }),
    }));
  } catch (err) {
    status($("scan-status"), err.message, "bad");
  } finally {
    $("scan-local").disabled = false;
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

  $("scan").disabled = true;
  status($("scan-status"), `Uploading ${files.length} drawing(s)...`);
  try {
    applyScan(await send("/api/scan", { method: "POST", body: fd }));
  } catch (err) {
    status($("scan-status"), err.message, "bad");
  } finally {
    $("scan").disabled = false;
  }
};

$("cat-all").onclick = () =>
  document.querySelectorAll(".cat").forEach((c) => (c.checked = true));
$("cat-none").onclick = () =>
  document.querySelectorAll(".cat").forEach((c) => (c.checked = false));

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
  $("out-folder").onchange = () => checkFolderField($("out-folder"), $("gen-status"));
  $("track-folder").onchange = () => checkFolderField($("track-folder"), $("gen-status"));
  $("v-out-folder").onchange = () => checkFolderField($("v-out-folder"), $("v-status"));
  $("d-out-folder").onchange = () => checkFolderField($("d-out-folder"), $("d-status"));
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
    status($("gen-status"),
      "Choose IFA or IFF before tracking this issue - it is never guessed.", "bad");
    return;
  }
  const categories = [...document.querySelectorAll(".cat:checked")].map((c) => c.value);
  if (!categories.length) {
    status($("gen-status"), "Select at least one drawing category.", "bad");
    return;
  }
  project = $("m-project").value;

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
  });

  $("generate").disabled = true;
  $("log").hidden = false;
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
    $("log").textContent += "\n" + err.message;
  } finally {
    genJob = null;
    $("generate").disabled = false;
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
    lines.push(`Tracker: ${data.chain.project || "(unnamed package)"}`);
    data.chain.issues.forEach((i) => {
      lines.push(`  ${i.n}. ${i.code.padEnd(8)} ${i.label.slice(0, 44).padEnd(46)}` +
        `${String(i.total).padStart(4)}   ${i.verdict}`);
    });
    if (data.chain.outstanding)
      lines.push(`${data.chain.outstanding} approved member(s) still on hold (not removed).`);
    lines.push(`Tracker saved: ${data.chain.tracker_path || data.chain.tracker}`);
    trackerPath = data.chain.tracker_path || "";
    lastWritten.tracker = trackerPath;
  }
  $("log").hidden = false;
  $("log").textContent = lines.join("\n");

  lastWritten.register = data.register_path || "";
  if (LOCAL) {
    $("open-book").disabled = !lastWritten.register;
    $("reveal-book").disabled = !lastWritten.register;
    $("open-tracker").disabled = !lastWritten.tracker;
  }

  const links = [downloadLink(data, "register", "register_path", "Download register")];
  if (data.chain) {
    const href = data.chain.tracker_sandboxed === false
      ? "/download/file?path=" + encodeURIComponent(data.chain.tracker_path)
      : "/download/tracker/" + encodeURIComponent(data.chain.tracker);
    links.push(`<a href="${href}">Download tracker</a>`);
  }
  $("gen-status").innerHTML =
    `Register written: ${esc(data.register)} &nbsp; ` + links.join(" &nbsp;|&nbsp; ");
  $("gen-status").className = "status good";
  refreshLastRegister();
}

$("open-book").onclick = () => openWritten(lastWritten.register, false);
$("reveal-book").onclick = () => openWritten(lastWritten.register, true);
$("open-tracker").onclick = () => openWritten(lastWritten.tracker, false);

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
    <tr data-member="${esc(m.member)}">
      <td><input type="checkbox" class="holdsel"></td>
      <td>${esc(m.member)}</td>
      <td class="num">${esc(m.zone || "-")}</td>
      <td class="num">${esc(m.revision || "-")}</td>
      <td>${esc(m.since)}</td>
      <td><input type="text" class="reason" value="${esc(m.reason)}"></td>
    </tr>`).join("");
  body.querySelectorAll(".reason").forEach((i) => (i.oninput = countHolds));
  countHolds();
  $("hold-backdrop").hidden = false;
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
  $("hold-save").disabled = true;
  try {
    const data = await send("/api/tracker/reasons", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, reasons, tracker_path: trackerPath }),
    });
    $("hold-backdrop").hidden = true;
    $("log").textContent += `\n\nReasons recorded for ${data.saved} member(s).` +
      (data.without_reason ? `\n${data.without_reason} still without a reason.` : "");
  } catch (err) {
    status($("hold-count"), err.message, "bad");
  } finally {
    $("hold-save").disabled = false;
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
      <td>${esc(h.member)}</td><td class="num">${esc(h.zone)}</td>
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
    status($("v-status"), "Choose a register workbook, or generate one on tab 1.", "bad");
    return;
  }
  if (!mem && !memPath) {
    status($("v-status"), "Choose the model member list.", "bad");
    return;
  }

  $("validate").disabled = true;
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
  } catch (err) {
    status($("v-status"), err.message, "bad");
  } finally {
    $("validate").disabled = false;
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

  $("diff").disabled = true;
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
  } catch (err) {
    status($("d-status"), err.message, "bad");
  } finally {
    diffJob = null;
    $("diff").disabled = false;
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
  $("p-seqgroups").value = (s.sequence_groups || [])
    .map(([digit, name]) => `${digit} = ${name}`).join("\n");
}

/* "7 = Misc. / Stair Steel" per line. Line order is erection order, so the
   list is kept as typed rather than sorted. */
function collectSequenceGroups() {
  return $("p-seqgroups").value.split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const at = line.indexOf("=");
      if (at < 0) throw new Error(`"${line}" needs the form: digit = name`);
      return [line.slice(0, at).trim(), line.slice(at + 1).trim()];
    });
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
  let sequenceGroups;
  try {
    sequenceGroups = collectSequenceGroups();
  } catch (err) {
    status($("s-status"), err.message, "bad");
    return;
  }
  try {
    const d = await send("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        profile: collectProfile(),
        sequence_groups: sequenceGroups,
        day_first_dates: $("s-daryfirst").checked,
        include_source_column: $("s-source").checked,
        group_by_zone: $("s-zone").checked,
        default_output_folder: $("s-outdir").value,
        default_tracker_folder: $("s-trackdir").value,
      }),
    });
    status($("s-status"), `Saved to ${d.saved_to}`, "good");
  } catch (err) {
    status($("s-status"), err.message, "bad");
  }
};

$("reset-settings").onclick = async () => {
  if (!confirm("Restore the default extraction patterns? Nothing is saved until " +
               "you press Save settings.")) return;
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
  $("run-test").disabled = true;
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
    $("run-test").disabled = false;
  }
};
$("t-path").onchange = () => $("run-test").click();
$("t-pdf").onchange = () => $("run-test").click();

/* ------------------------------------------------------------- start-up */

updateRegSource();
refreshLastRegister();
