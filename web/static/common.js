"use strict";

/* Shared by every page: the helpers, the folder picker, the progress poller
   and the result tabs. Split out of app.js when Part Specs became a page of
   its own rather than a panel over this one - two pages needing the same
   picker is not a reason for either to carry the other's screens. */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// Running on the same machine as the browser: real paths are usable, so the
// register can be written straight into the job folder instead of a sandbox.
const LOCAL = document.body.dataset.local === "yes";

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

/* Keeping Tab inside whatever is on top. Without it the invisible page behind
   a dialog is still tabbable, and three presses put focus somewhere the user
   cannot see it. */
const FOCUSABLE = 'a[href],button:not(:disabled),input:not(:disabled),' +
                  'select:not(:disabled),textarea:not(:disabled),summary,[tabindex]:not([tabindex="-1"])';

function topLayer() {
  // A page need not have every layer: Part Specs is its own page with a picker
  // and nothing else over it.
  for (const id of ["ask-backdrop", "pick-backdrop", "hold-backdrop", "sheet-host"]) {
    const el = $(id);
    if (el && !el.hidden) return el;
  }
  return null;
}

document.addEventListener("keydown", (e) => {
  const layer = topLayer();

  if (e.key === "Escape") {
    if (!layer) return;
    if (layer.id === "pick-backdrop") { closePick(null); return; }
    if (layer.id === "hold-backdrop") return;   // reasons would be lost silently
    if (layer.id === "ask-backdrop") { closeAsk(null); return; }
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
