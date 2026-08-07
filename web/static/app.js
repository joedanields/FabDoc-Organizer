"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let session = null;
let project = "";

/* ------------------------------------------------------------------ tabs */

document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $("panel-" + tab.dataset.panel).classList.add("active");
    if (tab.dataset.panel === "trackers") loadTrackers();
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

/* -------------------------------------------------------------- 1. scan */

$("scan").onclick = async () => {
  const files = [...$("folder").files].filter((f) => f.name.toLowerCase().endsWith(".pdf"));
  if (!files.length) {
    status($("scan-status"), "Choose a folder containing PDFs.", "bad");
    return;
  }
  const form = new FormData();
  files.forEach((f) => {
    form.append("files", f);
    // The relative path is what preserves the category subfolders; without it
    // every drawing would land in one flat folder and become a single worksheet.
    form.append("paths", f.webkitRelativePath || f.name);
  });

  $("scan").disabled = true;
  status($("scan-status"), `Uploading ${files.length} drawing(s)...`);
  try {
    const data = await send("/api/scan", { method: "POST", body: form });
    session = data.session;
    project = data.meta.project;

    $("m-title").value = data.meta.title;
    $("m-date").value = data.meta.date;
    $("m-zone").value = data.meta.zone;
    $("m-package").value = data.meta.package;
    $("m-project").value = data.meta.project || "";
    $("round").value = data.meta.issue_no || "";
    $("stage").value = "";

    const body = $("cats").querySelector("tbody");
    body.innerHTML = data.categories.map((c) => `
      <tr>
        <td><input type="checkbox" class="cat" value="${esc(c.name)}" checked></td>
        <td>${esc(c.name)}</td>
        <td>${esc(c.folder)}</td>
        <td class="num">${c.count}</td>
        <td>${c.recognised ? esc(c.matched) : "name used as-is"}</td>
      </tr>`).join("");

    ["meta-box", "cat-box", "track-box", "run-box"].forEach((id) => ($(id).hidden = false));
    status($("scan-status"),
      `${data.folder} - ${data.categories.length} categor${data.categories.length === 1 ? "y" : "ies"}, ${data.pdfs} PDF(s).`,
      "good");
  } catch (err) {
    status($("scan-status"), err.message, "bad");
  } finally {
    $("scan").disabled = false;
  }
};

/* ---------------------------------------------------------- 2. generate */

$("generate").onclick = async () => {
  if (!session) return;
  const tracking = $("track").checked;
  if (tracking && !$("stage").value) {
    status($("gen-status"),
      "Choose IFA or IFF before tracking this issue - it is never guessed.", "bad");
    return;
  }
  const form = new FormData();
  form.append("session", session);
  form.append("title", $("m-title").value);
  form.append("zone", $("m-zone").value);
  form.append("package", $("m-package").value);
  form.append("track", tracking ? "true" : "false");
  form.append("stage", $("stage").value);
  form.append("round_no", $("round").value);
  form.append("project", $("m-project").value);
  project = $("m-project").value;
  document.querySelectorAll(".cat:checked").forEach((c) => form.append("categories", c.value));

  $("generate").disabled = true;
  status($("gen-status"), "Reading drawings...");
  try {
    const data = await send("/api/generate", { method: "POST", body: form });
    session = null;                       // the upload is consumed server-side
    renderGenerate(data);
    if (data.holds) openHolds(data.holds);
  } catch (err) {
    status($("gen-status"), err.message, "bad");
  } finally {
    $("generate").disabled = false;
  }
};

function renderGenerate(data) {
  const lines = [];
  data.categories.forEach((c) => {
    lines.push(`  ${c.name.padEnd(20)} ${String(c.total).padStart(5)} row(s)` +
      (c.review ? `   (${c.review} need review)` : ""));
  });
  lines.push("");
  lines.push(`Total ${data.total} drawing(s).`);
  if (data.review) lines.push(`${data.review} row(s) flagged for review - highlighted amber.`);
  lines.push(`Register: ${data.register}`);
  if (data.chain) {
    lines.push("");
    lines.push(`Tracker: ${data.chain.project}`);
    data.chain.issues.forEach((i) => {
      lines.push(`  ${i.n}. ${i.code.padEnd(8)} ${i.label.slice(0, 44).padEnd(46)}` +
        `${String(i.total).padStart(4)}   ${i.verdict}`);
    });
    if (data.chain.outstanding)
      lines.push(`${data.chain.outstanding} approved member(s) on hold (not removed).`);
  }
  $("log").hidden = false;
  $("log").textContent = lines.join("\n");

  const links = [`<a href="/download/register/${encodeURIComponent(data.register)}">Download register</a>`];
  if (data.chain)
    links.push(`<a href="/download/tracker/${encodeURIComponent(data.chain.tracker)}">Download tracker</a>`);
  $("gen-status").innerHTML = links.join(" &nbsp;|&nbsp; ");
  $("gen-status").className = "status good";
}

/* ------------------------------------------------------- 3. hold dialog */

function openHolds(holds) {
  $("hold-intro").innerHTML =
    `<strong>${esc(holds.release)}</strong><br>` +
    `${holds.released} member(s) released, ${holds.members.length} approved member(s) ` +
    `not in this release. They are <strong>on hold, not removed</strong> - fabrication ` +
    `ships the approved scope in slices. State why they are waiting; the reason is ` +
    `written into the tracker.`;

  const body = $("hold-table").querySelector("tbody");
  body.innerHTML = holds.members.map((m) => `
    <tr data-member="${esc(m.member)}">
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

$("hold-cancel").onclick = () => {
  $("hold-backdrop").hidden = true;
  status($("gen-status"), "Register written. Hold reasons were not recorded.", "bad");
};

$("hold-save").onclick = async () => {
  if (!project) {                       // nothing tracked yet: nothing to save against
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
      body: JSON.stringify({ project, reasons }),
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
      : "<li class='hint'>No trackers yet. Generate an issue with tracking on.</li>";
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
      <table><thead><tr>
        <th class="num">#</th><th>Code</th><th>Issue</th><th>Date</th>
        <th class="num">Drawings</th><th class="num">Added</th><th class="num">Revised</th>
        <th class="num">Removed</th><th class="num">Released</th><th class="num">On hold</th>
        <th>Status</th></tr></thead><tbody>${rows}</tbody></table>
    </fieldset>
    ${held.length ? `<fieldset><legend>On hold &mdash; ${held.length} approved member(s) not yet released</legend>
      <table><thead><tr><th>Member</th><th class="num">Zone</th><th class="num">Rev</th>
      <th>On hold since</th><th>Reason</th></tr></thead><tbody>${holdRows}</tbody></table>
    </fieldset>` : ""}`;
}

/* --------------------------------------------------------- 5. validate */

$("validate").onclick = async () => {
  const reg = $("v-register").files[0], mem = $("v-members").files[0];
  if (!reg || !mem) {
    status($("v-status"), "Choose both a register and a member list.", "bad");
    return;
  }
  const form = new FormData();
  form.append("register_file", reg);
  form.append("members", mem);
  form.append("column", $("v-column").value);

  $("validate").disabled = true;
  status($("v-status"), "Comparing...");
  try {
    const d = await send("/api/validate", { method: "POST", body: form });
    status($("v-status"), "", "");
    $("v-status").innerHTML =
      `<a href="/download/register/${encodeURIComponent(d.report)}">Download report</a>`;
    $("v-result").innerHTML = `
      <fieldset><legend>${d.clean ? "PASS" : "Review required"}</legend>
        <p class="${d.clean ? "status good" : "status bad"}">${esc(d.verdict)}</p>
        <p class="hint">Member column read: <strong>${esc(d.column)}</strong></p>
        <table><thead><tr><th>Result</th><th class="num">Count</th></tr></thead><tbody>
          <tr><td>Members in model</td><td class="num">${d.model_count}</td></tr>
          <tr><td>Members in drawings</td><td class="num">${d.drawing_count}</td></tr>
          <tr class="row-iff"><td>Matched</td><td class="num">${d.matched}</td></tr>
          <tr class="row-hold"><td>Missing in drawings</td><td class="num">${d.missing.length}</td></tr>
          <tr class="row-warn"><td>Not in model</td><td class="num">${d.extra.length}</td></tr>
          <tr><td>Duplicated in drawings</td><td class="num">${d.duplicates}</td></tr>
        </tbody></table>
        ${d.missing.length ? `<p class="hint">Missing: ${esc(d.missing.slice(0, 60).join(", "))}</p>` : ""}
        ${d.extra.length ? `<p class="hint">Not in model: ${esc(d.extra.slice(0, 60).join(", "))}</p>` : ""}
      </fieldset>`;
  } catch (err) {
    status($("v-status"), err.message, "bad");
  } finally {
    $("validate").disabled = false;
  }
};

/* -------------------------------------------------------------- 6. diff */

$("diff").onclick = async () => {
  const older = $("d-old").files[0], newer = $("d-new").files[0];
  if (!older || !newer) {
    status($("d-status"), "Choose both register workbooks.", "bad");
    return;
  }
  const form = new FormData();
  form.append("old", older);
  form.append("new", newer);

  $("diff").disabled = true;
  status($("d-status"), "Comparing...");
  try {
    const d = await send("/api/diff", { method: "POST", body: form });
    $("d-status").innerHTML =
      `<a href="/download/register/${encodeURIComponent(d.report)}">Download report</a>`;
    $("d-result").innerHTML = `
      <fieldset><legend>${d.identical ? "Identical" : "Changed"}</legend>
        <p class="${d.identical ? "status good" : "status bad"}">${esc(d.verdict)}</p>
        <table><thead><tr><th>Result</th><th class="num">Count</th></tr></thead><tbody>
          <tr><td>Drawings in old issue</td><td class="num">${d.old_total}</td></tr>
          <tr><td>Drawings in new issue</td><td class="num">${d.new_total}</td></tr>
          <tr class="row-warn"><td>Added</td><td class="num">${d.added.length}</td></tr>
          <tr class="row-hold"><td>Removed</td><td class="num">${d.removed.length}</td></tr>
          <tr><td>Revision changed</td><td class="num">${d.revised.length}</td></tr>
          <tr class="row-iff"><td>Unchanged</td><td class="num">${d.unchanged}</td></tr>
        </tbody></table>
        ${d.added.length ? `<p class="hint">Added: ${esc(d.added.slice(0, 60).join(", "))}</p>` : ""}
        ${d.removed.length ? `<p class="hint">Removed: ${esc(d.removed.slice(0, 60).join(", "))}</p>` : ""}
      </fieldset>`;
  } catch (err) {
    status($("d-status"), err.message, "bad");
  } finally {
    $("diff").disabled = false;
  }
};
