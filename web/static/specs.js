"use strict";

/* The part spec tracker: two folders in, one report out.

   Its own page rather than a panel over the register screen, because it is its
   own job. Nothing here is shared with a register run - no uploaded package, no
   chain, no stage and round - and a screen with none of that on it should not
   be reached through the screen that has all of it.

   Which folder is OLD and which is NEW is decided by their names, on the
   server: read backwards, every increase in the report would be printed as a
   decrease. */

let specsJob = null;
let lastReport = "";

$("s-cancel").onclick = () => {
  cancelJob(specsJob);
  status($("s-status"), "Cancelling...");
};

$("specs").onclick = async () => {
  const first = $("s-first").value.trim(), second = $("s-second").value.trim();
  if (!first || !second) {
    status($("s-status"), "Choose both issue folders.", "bad");
    return;
  }

  busy($("specs"), true);
  status($("s-status"), "Reading both issues...");
  try {
    const { job } = await send("/api/specs", {
      method: "POST",
      body: form({
        first, second,
        output_folder: LOCAL ? $("s-out-folder").value : "",
        output_name: $("s-out-name").value,
      }),
    });
    specsJob = job;
    const d = await runJob(job, {
      bar: $("s-progress"), statusEl: $("s-status"),
      cancelBtn: $("s-cancel"), label: "Reading drawings...",
    });

    lastReport = d.report_path || "";
    if (LOCAL) $("s-open").disabled = !lastReport;
    const href = d.sandboxed === false
      ? "/download/file?path=" + encodeURIComponent(d.report_path)
      : "/download/register/" + encodeURIComponent(d.report);
    const aside = [
      d.only_in_old ? `${d.only_in_old} only in OLD` : "",
      d.only_in_new ? `${d.only_in_new} only in NEW` : "",
    ].filter(Boolean).join(", ");
    const verdict = d.identical
      ? "Nothing moved - every part is the same in both issues."
      : `${d.moved} value(s) moved across ${d.parts} part(s)` +
        (aside ? ` (${aside})` : "") + ".";
    $("s-status").innerHTML =
      `<span class="${d.identical ? "good" : "bad"}">${esc(verdict)}</span> ` +
      `&nbsp;&ldquo;${esc(d.old_label)}&rdquo; &rarr; &ldquo;${esc(d.new_label)}&rdquo; ` +
      `&nbsp;<a href="${href}">Download report</a>`;
    $("s-status").className = "status";

    // One tab per value, each listing what moved and nothing else - the same
    // as the workbook. A tab reading "0" is the answer for that value.
    $("s-result").innerHTML = `<div id="s-tabs"></div>`;
    const cols = [
      { key: "member", label: "Member Name" },
      { key: "old", label: "OLD", num: true },
      { key: "new", label: "NEW", num: true },
      { key: "change", label: "Change", num: true },
    ];
    resultTabs($("s-tabs"), d.fields.map((f) => ({
      title: f.name,
      count: f.rows.length,
      rows: f.rows,
      columns: cols,
    })));
    toast(verdict, d.identical ? "good" : "bad");
  } catch (err) {
    status($("s-status"), err.message, "bad");
    toast(err.message, "bad");
  } finally {
    specsJob = null;
    busy($("specs"), false);
  }
};

$("s-open").onclick = () => openWritten(lastReport, false);
