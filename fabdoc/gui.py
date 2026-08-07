"""Tkinter desktop application.

Four tabs mirroring the workflow: generate a register from a drawing package,
validate it against the model member list, compare two issues of the same
package, and tune the extraction rules when a new drawing template turns up.
"""

from __future__ import annotations

import queue
import threading
import traceback
import webbrowser
from dataclasses import replace
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __app_name__, __version__
from .categories import discover_categories
from .config import AppSettings, ExtractionProfile, load_settings, save_settings
from .compare import IssueComparison, compare_issues
from .excel_out import (suggest_register_name, write_comparison_report,
                       write_register, write_validation_report)
from .extract import dump_text, extract_drawing
from .folder_meta import ProjectMeta, parse_folder
from .memberlist import MemberList, preview_columns, read_member_list, sheet_names
from .register import Register, build_register
from .register_io import read_register
from .tracking import (STAGE_IFA, STAGE_IFF, STAGES, ChainState, HoldRecord,
                       IssueEntry, apply_reasons, build_chain, load_state,
                       project_name_from, save_state, snapshot_register,
                       state_path_for)
from .tracking_out import write_tracker
from .validate import ValidationResult, validate

PAD = 8


class HoldReasonDialog(tk.Toplevel):
    """Ask why approved members were left out of a fabrication release.

    Fabrication ships the approved scope in slices, so a release routinely
    leaves members behind. They are not dropped drawings and must not be
    reported as removed - but somebody has to say *why* each one is waiting,
    because that is the question the shop floor asks.

    Most of a release is held for one shared reason, so a batch entry fills
    everything at once and individual rows can then be overridden. Reasons
    already recorded in an earlier release are pre-filled rather than asked
    again from scratch.
    """

    def __init__(self, master: tk.Misc, holds: list[HoldRecord], release_label: str,
                 released: int) -> None:
        super().__init__(master)
        self.title(f"{__app_name__} - members on hold")
        self.transient(master)
        self.geometry("880x560")
        self.minsize(700, 440)
        self.result: dict[str, str] | None = None
        self._holds = holds

        frame = ttk.Frame(self, padding=PAD)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text=f"{release_label}\n\n"
                 f"{released} member(s) released, {len(holds)} approved member(s) not in "
                 f"this release.\n"
                 f"They are on hold, not removed - fabrication ships the approved scope in "
                 f"slices. State why they are waiting; the reason is written into the "
                 f"tracker.",
            style="Sub.TLabel", wraplength=830, justify="left",
        ).pack(fill="x", pady=(0, PAD))

        batch = ttk.LabelFrame(frame, text="Reason for all outstanding members", padding=PAD)
        batch.pack(fill="x")
        self.batch_var = tk.StringVar()
        entry = ttk.Entry(batch, textvariable=self.batch_var)
        entry.pack(side="left", fill="x", expand=True, padx=(0, PAD))
        ttk.Button(batch, text="Apply to all", command=self._apply_all).pack(side="left")
        entry.focus_set()

        cols = ("member", "zone", "rev", "since", "reason")
        tree_box = ttk.LabelFrame(
            frame, text="Select rows to give them a different reason", padding=4)
        tree_box.pack(fill="both", expand=True, pady=(PAD, 0))
        self.tree = ttk.Treeview(tree_box, columns=cols, show="headings",
                                 selectmode="extended")
        for name, heading, width, anchor in [
            ("member", "Member Name", 150, "w"),
            ("zone", "Zone", 60, "center"),
            ("rev", "Approved Rev", 100, "center"),
            ("since", "On Hold Since", 200, "w"),
            ("reason", "Reason", 300, "w"),
        ]:
            self.tree.heading(name, text=heading)
            self.tree.column(name, width=width, anchor=anchor)
        sb = ttk.Scrollbar(tree_box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        for hold in holds:
            self.tree.insert("", "end", iid=hold.member_name, values=(
                hold.member_name, hold.zone or "-", hold.revision or "-",
                hold.held_since, hold.reason,
            ))

        sel = ttk.Frame(frame)
        sel.pack(fill="x", pady=(PAD, 0))
        ttk.Label(sel, text="Reason for selected").pack(side="left", padx=(0, 4))
        self.sel_var = tk.StringVar()
        ttk.Entry(sel, textvariable=self.sel_var).pack(
            side="left", fill="x", expand=True, padx=(0, PAD))
        ttk.Button(sel, text="Apply to selected",
                   command=self._apply_selected).pack(side="left")

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(PAD, 0))
        self.count_label = ttk.Label(buttons, text="", style="Sub.TLabel")
        self.count_label.pack(side="left")
        ttk.Button(buttons, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(buttons, text="Save Reasons",
                   command=self._accept).pack(side="right", padx=(0, PAD))

        self._refresh_count()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda _e: self._cancel())
        self.grab_set()

    # -- helpers ------------------------------------------------------------

    def _set_reason(self, iid: str, reason: str) -> None:
        values = list(self.tree.item(iid, "values"))
        values[4] = reason
        self.tree.item(iid, values=values)

    def _apply_all(self) -> None:
        reason = self.batch_var.get().strip()
        if not reason:
            return
        for iid in self.tree.get_children():
            self._set_reason(iid, reason)
        self._refresh_count()

    def _apply_selected(self) -> None:
        reason = self.sel_var.get().strip()
        selected = self.tree.selection()
        if not reason or not selected:
            return
        for iid in selected:
            self._set_reason(iid, reason)
        self._refresh_count()

    def _collected(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for iid in self.tree.get_children():
            reason = str(self.tree.item(iid, "values")[4]).strip()
            if reason:
                out[iid] = reason
        return out

    def _refresh_count(self) -> None:
        blank = len(self.tree.get_children()) - len(self._collected())
        self.count_label.configure(
            text="Every member has a reason."
            if not blank else
            f"{blank} member(s) still have no reason - they will be highlighted "
            f"in the tracker."
        )

    def _accept(self) -> None:
        self.result = self._collected()
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


class FabDocApp(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=PAD)
        self.master.title(f"{__app_name__} {__version__}")
        self.master.geometry("1120x780")
        self.master.minsize(940, 640)
        self.pack(fill="both", expand=True)

        self.settings: AppSettings = load_settings()
        self.register: Register | None = None
        self.register_path: Path | None = None
        self.member_list: MemberList | None = None
        self.result: ValidationResult | None = None
        self.comparison: IssueComparison | None = None
        self.tracker_path: Path | None = None

        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()

        self._build_ui()
        self.after(100, self._drain_queue)

    # -- layout -------------------------------------------------------------

    def _build_ui(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Heading.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Sub.TLabel", foreground="#666666")
        style.configure("Good.TLabel", foreground="#1E7B34", font=("Segoe UI", 10, "bold"))
        style.configure("Bad.TLabel", foreground="#B00020", font=("Segoe UI", 10, "bold"))

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        self.nb.add(self._build_generate_tab(), text="  1. Generate Register  ")
        self.nb.add(self._build_validate_tab(), text="  2. Validate Members  ")
        self.nb.add(self._build_compare_tab(), text="  3. Compare Issues  ")
        self.nb.add(self._build_settings_tab(), text="  4. Extraction Settings  ")

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(PAD, 0))
        self.status = ttk.Label(bar, text="Ready.", style="Sub.TLabel")
        self.status.pack(side="left")
        ttk.Label(bar, text=f"{__app_name__} {__version__}", style="Sub.TLabel").pack(side="right")

    # ------------------------------------------------------------------ tab 1

    def _build_generate_tab(self) -> ttk.Frame:
        tab = ttk.Frame(self, padding=PAD)

        box = ttk.LabelFrame(tab, text="Project folder", padding=PAD)
        box.pack(fill="x")
        self.folder_var = tk.StringVar()
        entry = ttk.Entry(box, textvariable=self.folder_var)
        entry.pack(side="left", fill="x", expand=True, padx=(0, PAD))
        ttk.Button(box, text="Browse...", command=self._pick_folder).pack(side="left")
        ttk.Button(box, text="Rescan", command=self._scan_folder).pack(side="left", padx=(PAD, 0))

        meta_box = ttk.LabelFrame(
            tab, text="Project details (read from the folder name - edit if needed)", padding=PAD
        )
        meta_box.pack(fill="x", pady=(PAD, 0))
        self.meta_vars = {
            "title": tk.StringVar(), "date": tk.StringVar(),
            "zone": tk.StringVar(), "package": tk.StringVar(),
        }
        grid = ttk.Frame(meta_box)
        grid.pack(fill="x")
        for col, (key, label, width) in enumerate([
            ("title", "Title", 44), ("date", "Date", 16),
            ("zone", "Zone", 12), ("package", "Package", 12),
        ]):
            ttk.Label(grid, text=label).grid(row=0, column=col * 2, sticky="w", padx=(0, 4))
            ttk.Entry(grid, textvariable=self.meta_vars[key], width=width).grid(
                row=0, column=col * 2 + 1, sticky="we", padx=(0, PAD * 2)
            )
        grid.columnconfigure(1, weight=1)

        cat_box = ttk.LabelFrame(
            tab, text="Drawing categories found (one worksheet each - select to include)",
            padding=PAD,
        )
        cat_box.pack(fill="both", expand=True, pady=(PAD, 0))
        cols = ("category", "folder", "count", "match")
        self.cat_tree = ttk.Treeview(cat_box, columns=cols, show="headings", height=7,
                                     selectmode="extended")
        for name, text, width, anchor in [
            ("category", "Worksheet / Category", 220, "w"),
            ("folder", "Source subfolder", 380, "w"),
            ("count", "PDFs", 80, "center"),
            ("match", "Detected as", 200, "w"),
        ]:
            self.cat_tree.heading(name, text=text)
            self.cat_tree.column(name, width=width, anchor=anchor)
        vsb = ttk.Scrollbar(cat_box, orient="vertical", command=self.cat_tree.yview)
        self.cat_tree.configure(yscrollcommand=vsb.set)
        self.cat_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")

        out_box = ttk.LabelFrame(tab, text="Output workbook", padding=PAD)
        out_box.pack(fill="x", pady=(PAD, 0))
        self.output_var = tk.StringVar()
        ttk.Entry(out_box, textvariable=self.output_var).pack(
            side="left", fill="x", expand=True, padx=(0, PAD))
        ttk.Button(out_box, text="Save as...", command=self._pick_output).pack(side="left")
        self.save_default_var = tk.BooleanVar()
        ttk.Checkbutton(out_box, text="Save as default output folder",
                        variable=self.save_default_var).pack(side="left", padx=(PAD, 0))

        trk = ttk.LabelFrame(
            tab, text="Package tracker (chains every issue of this package)", padding=PAD)
        trk.pack(fill="x", pady=(PAD, 0))
        self.track_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(trk, text="Add this issue to the tracker",
                        variable=self.track_var).grid(row=0, column=0, sticky="w")
        ttk.Label(trk, text="Stage").grid(row=0, column=1, sticky="e", padx=(PAD * 2, 4))
        # Deliberately blank: an IFA folder mistaken for IFF would rewrite the
        # approved baseline and report the unshipped balance as on hold.
        self.stage_combo = ttk.Combobox(trk, state="readonly", width=8, values=list(STAGES))
        self.stage_combo.grid(row=0, column=2, sticky="w")
        ttk.Label(trk, text="Round").grid(row=0, column=3, sticky="e", padx=(PAD * 2, 4))
        self.round_var = tk.StringVar()
        ttk.Entry(trk, textvariable=self.round_var, width=8).grid(row=0, column=4, sticky="w")
        ttk.Label(trk, text="IFA = for approval,  IFF = for fabrication",
                  style="Sub.TLabel").grid(row=0, column=5, sticky="w", padx=(PAD * 2, 0))

        ttk.Label(trk, text="Tracker").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.tracker_var = tk.StringVar()
        ttk.Entry(trk, textvariable=self.tracker_var).grid(
            row=1, column=1, columnspan=5, sticky="we", pady=(6, 0), padx=(0, PAD))
        ttk.Button(trk, text="Browse...", command=self._pick_tracker).grid(
            row=1, column=6, pady=(6, 0))
        ttk.Button(trk, text="Open Tracker", command=self._open_tracker).grid(
            row=0, column=6, sticky="e")
        trk.columnconfigure(5, weight=1)

        run = ttk.Frame(tab)
        run.pack(fill="x", pady=(PAD, 0))
        self.gen_btn = ttk.Button(run, text="Generate Register",
                                  command=self._start_generate)
        self.gen_btn.pack(side="left")
        self.cancel_btn = ttk.Button(run, text="Cancel", command=self._request_cancel,
                                     state="disabled")
        self.cancel_btn.pack(side="left", padx=(PAD, 0))
        self.open_btn = ttk.Button(run, text="Open Workbook", command=self._open_output,
                                   state="disabled")
        self.open_btn.pack(side="left", padx=(PAD, 0))
        self.progress = ttk.Progressbar(run, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(PAD * 2, 0))

        log_box = ttk.LabelFrame(tab, text="Log", padding=PAD)
        log_box.pack(fill="both", expand=True, pady=(PAD, 0))
        self.log = tk.Text(log_box, height=8, wrap="none", state="disabled",
                           font=("Consolas", 9))
        log_sb = ttk.Scrollbar(log_box, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        log_sb.pack(side="left", fill="y")
        return tab

    # ------------------------------------------------------------------ tab 2

    def _build_validate_tab(self) -> ttk.Frame:
        tab = ttk.Frame(self, padding=PAD)

        src = ttk.LabelFrame(tab, text="Drawing register", padding=PAD)
        src.pack(fill="x")
        self.reg_source_var = tk.StringVar(value="current")
        ttk.Radiobutton(src, text="Use the register generated in tab 1",
                        variable=self.reg_source_var, value="current",
                        command=self._update_reg_source).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(src, text="Load an existing register workbook",
                        variable=self.reg_source_var, value="file",
                        command=self._update_reg_source).grid(row=1, column=0, sticky="w")
        self.reg_file_var = tk.StringVar()
        self.reg_file_entry = ttk.Entry(src, textvariable=self.reg_file_var, state="disabled")
        self.reg_file_entry.grid(row=1, column=1, sticky="we", padx=PAD)
        self.reg_file_btn = ttk.Button(src, text="Browse...", command=self._pick_register,
                                       state="disabled")
        self.reg_file_btn.grid(row=1, column=2)
        src.columnconfigure(1, weight=1)
        self.reg_status = ttk.Label(src, text="No register loaded.", style="Sub.TLabel")
        self.reg_status.grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))

        mdl = ttk.LabelFrame(tab, text="Structural model member list", padding=PAD)
        mdl.pack(fill="x", pady=(PAD, 0))
        self.model_var = tk.StringVar()
        ttk.Entry(mdl, textvariable=self.model_var).grid(row=0, column=0, columnspan=3,
                                                         sticky="we", padx=(0, PAD))
        ttk.Button(mdl, text="Browse...", command=self._pick_members).grid(row=0, column=3)
        ttk.Label(mdl, text="Worksheet").grid(row=1, column=0, sticky="w", pady=(PAD, 0))
        self.sheet_combo = ttk.Combobox(mdl, state="readonly", width=26)
        self.sheet_combo.grid(row=1, column=1, sticky="w", pady=(PAD, 0), padx=(0, PAD))
        self.sheet_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_columns())
        ttk.Label(mdl, text="Member column").grid(row=1, column=2, sticky="e", pady=(PAD, 0))
        self.column_combo = ttk.Combobox(mdl, state="readonly", width=30)
        self.column_combo.grid(row=1, column=3, sticky="w", pady=(PAD, 0))
        mdl.columnconfigure(1, weight=1)
        self.model_status = ttk.Label(mdl, text="No member list loaded.", style="Sub.TLabel")
        self.model_status.grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))

        opt = ttk.LabelFrame(tab, text="Comparison options", padding=PAD)
        opt.pack(fill="x", pady=(PAD, 0))
        self.ci_var = tk.BooleanVar(value=self.settings.compare_case_insensitive)
        self.ws_var = tk.BooleanVar(value=self.settings.compare_ignore_whitespace)
        self.lz_var = tk.BooleanVar(value=self.settings.compare_strip_leading_zeros)
        ttk.Checkbutton(opt, text="Ignore case", variable=self.ci_var).pack(side="left")
        ttk.Checkbutton(opt, text="Ignore spaces", variable=self.ws_var).pack(
            side="left", padx=(PAD * 2, 0))
        ttk.Checkbutton(opt, text="Ignore leading zeros (B007 = B7)",
                        variable=self.lz_var).pack(side="left", padx=(PAD * 2, 0))
        ttk.Label(opt, text="Categories").pack(side="left", padx=(PAD * 3, 4))
        self.val_cat_combo = ttk.Combobox(opt, state="readonly", width=24,
                                          values=["(all)"])
        self.val_cat_combo.set("(all)")
        self.val_cat_combo.pack(side="left")

        run = ttk.Frame(tab)
        run.pack(fill="x", pady=(PAD, 0))
        ttk.Button(run, text="Compare", command=self._run_compare).pack(side="left")
        self.export_btn = ttk.Button(run, text="Export Report...",
                                     command=self._export_report, state="disabled")
        self.export_btn.pack(side="left", padx=(PAD, 0))
        self.verdict = ttk.Label(run, text="", style="Sub.TLabel")
        self.verdict.pack(side="left", padx=(PAD * 2, 0))

        res = ttk.Frame(tab)
        res.pack(fill="both", expand=True, pady=(PAD, 0))
        self.res_nb = ttk.Notebook(res)
        self.res_nb.pack(fill="both", expand=True)
        self.res_trees: dict[str, ttk.Treeview] = {}
        for key, title, columns in [
            ("missing", "Missing in Drawings", [("member", "Member Name (in model)", 260)]),
            ("extra", "Not in Model", [("member", "Member Name (in drawing)", 220),
                                       ("category", "Category", 130),
                                       ("seq", "S.No", 70),
                                       ("rev", "Rev", 60),
                                       ("file", "Source File", 320)]),
            ("matched", "Matched", [("member", "Member Name", 220),
                                    ("category", "Category", 130),
                                    ("seq", "S.No", 70),
                                    ("rev", "Rev", 60),
                                    ("file", "Source File", 320)]),
            ("dupes", "Duplicates", [("member", "Member Name", 220),
                                     ("count", "Count", 70),
                                     ("file", "Source Files", 420)]),
        ]:
            frame = ttk.Frame(self.res_nb, padding=4)
            names = [c[0] for c in columns]
            tree = ttk.Treeview(frame, columns=names, show="headings")
            for name, heading, width in columns:
                tree.heading(name, text=heading)
                tree.column(name, width=width,
                            anchor="center" if name in ("seq", "rev", "count") else "w")
            sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            tree.pack(side="left", fill="both", expand=True)
            sb.pack(side="left", fill="y")
            self.res_nb.add(frame, text=f"  {title} (0)  ")
            self.res_trees[key] = tree
        return tab

    # ------------------------------------------------------------------ tab 3

    def _build_compare_tab(self) -> ttk.Frame:
        tab = ttk.Frame(self, padding=PAD)

        ttk.Label(
            tab,
            text="Compare two issues of the same package - \"for Approval\" against "
                 "\"for Re Approval\" - to see which drawings are new, which were "
                 "dropped, and which came back at a different revision. Each side can "
                 "be a package folder or a register workbook.",
            style="Sub.TLabel", wraplength=1020, justify="left",
        ).pack(fill="x", pady=(0, PAD))

        self.cmp_vars: dict[str, tk.StringVar] = {}
        for key, label in [("old", "Older issue"), ("new", "Newer issue")]:
            box = ttk.LabelFrame(tab, text=label, padding=PAD)
            box.pack(fill="x", pady=(0, PAD))
            var = tk.StringVar()
            self.cmp_vars[key] = var
            ttk.Entry(box, textvariable=var).pack(side="left", fill="x", expand=True,
                                                  padx=(0, PAD))
            ttk.Button(box, text="Folder...",
                       command=lambda k=key: self._pick_cmp_folder(k)).pack(side="left")
            ttk.Button(box, text="Workbook...",
                       command=lambda k=key: self._pick_cmp_file(k)).pack(
                           side="left", padx=(4, 0))

        run = ttk.Frame(tab)
        run.pack(fill="x")
        self.cmp_btn = ttk.Button(run, text="Compare Issues", command=self._start_compare)
        self.cmp_btn.pack(side="left")
        self.cmp_export_btn = ttk.Button(run, text="Export Report...",
                                         command=self._export_comparison, state="disabled")
        self.cmp_export_btn.pack(side="left", padx=(PAD, 0))
        self.cmp_progress = ttk.Progressbar(run, mode="determinate")
        self.cmp_progress.pack(side="left", fill="x", expand=True, padx=(PAD * 2, 0))

        self.cmp_verdict = ttk.Label(tab, text="", style="Sub.TLabel")
        self.cmp_verdict.pack(fill="x", pady=(PAD, 0))

        res = ttk.Frame(tab)
        res.pack(fill="both", expand=True, pady=(PAD, 0))
        self.cmp_nb = ttk.Notebook(res)
        self.cmp_nb.pack(fill="both", expand=True)
        self.cmp_trees: dict[str, ttk.Treeview] = {}
        columns = [("member", "Member Name", 220), ("zone", "Zone", 70),
                   ("old", "Old Rev", 80), ("new", "New Rev", 80),
                   ("qty", "Qty Old / New", 120)]
        for key, title in [("added", "Added"), ("removed", "Removed"),
                           ("revised", "Revision Changed"), ("unchanged", "Unchanged")]:
            frame = ttk.Frame(self.cmp_nb, padding=4)
            tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings")
            for name, heading, width in columns:
                tree.heading(name, text=heading)
                tree.column(name, width=width, anchor="w" if name == "member" else "center")
            sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            tree.pack(side="left", fill="both", expand=True)
            sb.pack(side="left", fill="y")
            self.cmp_nb.add(frame, text=f"  {title} (0)  ")
            self.cmp_trees[key] = tree
        return tab

    def _pick_cmp_folder(self, key: str) -> None:
        chosen = filedialog.askdirectory(title=f"Select the {key} issue folder")
        if chosen:
            self.cmp_vars[key].set(chosen)

    def _pick_cmp_file(self, key: str) -> None:
        chosen = filedialog.askopenfilename(
            title=f"Select the {key} issue register",
            filetypes=[("Excel workbook", "*.xlsx *.xlsm")],
        )
        if chosen:
            self.cmp_vars[key].set(chosen)

    def _start_compare(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        old_path, new_path = self.cmp_vars["old"].get().strip(), self.cmp_vars["new"].get().strip()
        if not old_path or not new_path:
            messagebox.showerror(__app_name__, "Choose both the older and newer issue.")
            return
        for p in (old_path, new_path):
            if not Path(p).exists():
                messagebox.showerror(__app_name__, f"Not found:\n{p}")
                return

        self.cmp_btn.configure(state="disabled")
        self.cmp_export_btn.configure(state="disabled")
        self.cmp_progress.configure(value=0, maximum=100)
        self._cancel.clear()
        settings = self._settings_from_ui()

        def load(path: str) -> Register:
            p = Path(path)
            if p.is_dir():
                return build_register(
                    p, settings=settings,
                    progress=lambda d, t, label: self._queue.put(
                        ("cmp_progress", d, t, f"{p.name[:28]}: {label}")),
                    should_cancel=self._cancel.is_set,
                )
            return read_register(p)

        def work() -> None:
            try:
                result = compare_issues(
                    load(old_path), load(new_path), settings=settings,
                    old_label=Path(old_path).name, new_label=Path(new_path).name,
                )
                self._queue.put(("cmp_done", result))
            except Exception:
                self._queue.put(("cmp_error", traceback.format_exc()))

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()

    def _show_comparison(self, result: IssueComparison) -> None:
        self.comparison = result
        for tree in self.cmp_trees.values():
            for item in tree.get_children():
                tree.delete(item)

        for key, deltas in [("added", result.added), ("removed", result.removed),
                            ("revised", result.revision_changed),
                            ("unchanged", result.unchanged)]:
            for d in deltas:
                self.cmp_trees[key].insert(
                    "", "end",
                    values=(d.member_name, d.zone or "-", d.old_revision or "-",
                            d.new_revision or "-", f"{d.old_count} / {d.new_count}"),
                )

        for idx, (title, count) in enumerate([
            ("Added", len(result.added)),
            ("Removed", len(result.removed)),
            ("Revision Changed", len(result.revision_changed)),
            ("Unchanged", len(result.unchanged)),
        ]):
            self.cmp_nb.tab(idx, text=f"  {title} ({count})  ")

        self.cmp_verdict.configure(
            text=f"{result.verdict}    "
                 f"[{result.old_total} drawing(s) -> {result.new_total}]",
            style="Good.TLabel" if result.is_identical else "Bad.TLabel",
        )
        self.cmp_btn.configure(state="normal")
        self.cmp_export_btn.configure(state="normal")
        self._set_status(result.verdict)

    def _export_comparison(self) -> None:
        if not self.comparison:
            return
        chosen = filedialog.asksaveasfilename(
            title="Save comparison report", defaultextension=".xlsx",
            filetypes=[("Excel workbook", "*.xlsx")],
            initialfile="Issue Comparison Report.xlsx",
        )
        if not chosen:
            return
        try:
            path = write_comparison_report(self.comparison, chosen)
        except OSError as exc:
            messagebox.showerror(__app_name__, f"Could not save the report:\n{exc}")
            return
        self._set_status(f"Comparison report written: {Path(path).name}")
        if messagebox.askyesno(__app_name__, f"Report saved to:\n{path}\n\nOpen it now?"):
            webbrowser.open(Path(path).as_uri())

    # ------------------------------------------------------------------ tab 4

    def _build_settings_tab(self) -> ttk.Frame:
        tab = ttk.Frame(self, padding=PAD)

        ttk.Label(
            tab,
            text="Drawing templates differ between detailers. Test one representative "
                 "drawing below, then adjust the patterns until all three fields come out "
                 "right - one regular expression per line, first match wins, group 1 is the value.",
            style="Sub.TLabel", wraplength=1020, justify="left",
        ).pack(fill="x", pady=(0, PAD))

        panes = ttk.Panedwindow(tab, orient="horizontal")
        panes.pack(fill="both", expand=True)

        left = ttk.Frame(panes, padding=(0, 0, PAD, 0))
        panes.add(left, weight=1)
        self.pattern_boxes: dict[str, tk.Text] = {}
        for key, label, height in [
            ("member_patterns", "Member name patterns", 7),
            ("revision_patterns", "Revision patterns", 4),
            ("sequence_patterns", "Sequence number patterns", 4),
            ("filename_patterns", "Filename fallback patterns (groups: seq, member, rev)", 5),
        ]:
            box = ttk.LabelFrame(left, text=label, padding=4)
            box.pack(fill="both", expand=True, pady=(0, 6))
            text = tk.Text(box, height=height, wrap="none", font=("Consolas", 9))
            sb = ttk.Scrollbar(box, orient="vertical", command=text.yview)
            text.configure(yscrollcommand=sb.set)
            text.pack(side="left", fill="both", expand=True)
            sb.pack(side="left", fill="y")
            self.pattern_boxes[key] = text

        geo = ttk.LabelFrame(left, text="Title block region (page fractions, 0-1)", padding=4)
        geo.pack(fill="x")
        self.rect_vars = [tk.StringVar() for _ in range(4)]
        for idx, label in enumerate(["left", "top", "right", "bottom"]):
            ttk.Label(geo, text=label).grid(row=0, column=idx * 2, padx=(0, 4))
            ttk.Entry(geo, textvariable=self.rect_vars[idx], width=7).grid(
                row=0, column=idx * 2 + 1, padx=(0, PAD))
        self.largest_var = tk.BooleanVar()
        ttk.Checkbutton(geo, text="Fall back to the largest mark-shaped text in the title block",
                        variable=self.largest_var).grid(row=1, column=0, columnspan=8,
                                                        sticky="w", pady=(4, 0))

        right = ttk.Frame(panes)
        panes.add(right, weight=1)
        test = ttk.LabelFrame(right, text="Test drawing", padding=4)
        test.pack(fill="x")
        self.test_pdf_var = tk.StringVar()
        ttk.Entry(test, textvariable=self.test_pdf_var).pack(
            side="left", fill="x", expand=True, padx=(0, PAD))
        ttk.Button(test, text="Browse...", command=self._pick_test_pdf).pack(side="left")
        ttk.Button(test, text="Test", command=self._run_test).pack(side="left", padx=(PAD, 0))

        out = ttk.LabelFrame(right, text="What the parser sees / what it extracted", padding=4)
        out.pack(fill="both", expand=True, pady=(6, 0))
        self.test_out = tk.Text(out, wrap="none", font=("Consolas", 9), state="disabled")
        sb = ttk.Scrollbar(out, orient="vertical", command=self.test_out.yview)
        self.test_out.configure(yscrollcommand=sb.set)
        self.test_out.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

        btns = ttk.Frame(tab)
        btns.pack(fill="x", pady=(PAD, 0))
        ttk.Button(btns, text="Save Settings", command=self._save_settings).pack(side="left")
        ttk.Button(btns, text="Restore Defaults",
                   command=self._reset_settings).pack(side="left", padx=(PAD, 0))
        self.settings_status = ttk.Label(btns, text="", style="Sub.TLabel")
        self.settings_status.pack(side="left", padx=(PAD * 2, 0))

        self._load_profile_into_ui(self.settings.profile)
        return tab

    # -- helpers ------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        self.status.configure(text=text)

    def _log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # -- tab 1 actions ------------------------------------------------------

    def _pick_folder(self) -> None:
        chosen = filedialog.askdirectory(title="Select the project (issue) folder")
        if chosen:
            self.folder_var.set(chosen)
            self._scan_folder()

    def _scan_folder(self) -> None:
        folder = self.folder_var.get().strip()
        if not folder:
            return
        path = Path(folder)
        if not path.is_dir():
            messagebox.showerror(__app_name__, f"Not a folder:\n{folder}")
            return

        meta = parse_folder(path, day_first=self.settings.day_first_dates)
        self.meta_vars["title"].set(meta.title)
        self.meta_vars["date"].set(meta.date_display)
        self.meta_vars["zone"].set(meta.zone)
        self.meta_vars["package"].set(meta.package)

        for item in self.cat_tree.get_children():
            self.cat_tree.delete(item)
        try:
            cats = discover_categories(path, aliases=self.settings.category_aliases,
                                       order=self.settings.category_order)
        except OSError as exc:
            messagebox.showerror(__app_name__, str(exc))
            return

        for cat in cats:
            detected = f"{cat.matched_alias or 'name used as-is'}"
            self.cat_tree.insert(
                "", "end", values=(cat.name, str(cat.folder), cat.count, detected)
            )
        for item in self.cat_tree.get_children():
            self.cat_tree.selection_add(item)

        total = sum(c.count for c in cats)
        if not cats:
            self._set_status("No PDFs found in this folder.")
            self._log(f"No drawing PDFs found under {path}")
        else:
            self._set_status(
                f"{len(cats)} categor{'y' if len(cats) == 1 else 'ies'}, {total} PDF(s) found."
            )
            self._log(f"Scanned {path}: {len(cats)} categories, {total} PDFs.")

        temp = Register(meta=meta, project_folder=path)
        default_folder = self.settings.default_output_folder
        if default_folder and Path(default_folder).is_dir():
            out_dir = Path(default_folder)
        else:
            out_dir = path
        self.output_var.set(str(out_dir / suggest_register_name(temp)))

        # The round is the leading "25." of the folder name; the stage is not
        # guessed, so it stays blank until the engineer says which it is.
        self.round_var.set(meta.issue_no)
        if not self.tracker_var.get().strip():
            self.tracker_var.set(str(out_dir / "Package Tracker.xlsx"))

    def _pick_output(self) -> None:
        current = Path(self.output_var.get()) if self.output_var.get() else None
        chosen = filedialog.asksaveasfilename(
            title="Save register as", defaultextension=".xlsx",
            filetypes=[("Excel workbook", "*.xlsx")],
            initialfile=current.name if current else "Drawing Register.xlsx",
            initialdir=str(current.parent) if current else None,
        )
        if chosen:
            self.output_var.set(chosen)

    def _current_meta(self) -> ProjectMeta:
        folder = Path(self.folder_var.get().strip())
        base = parse_folder(folder, day_first=self.settings.day_first_dates)
        typed_date = self.meta_vars["date"].get().strip()
        # "zone" is a display property over the zones list, so it cannot go
        # through dataclasses.replace - build the list explicitly.
        zones = [z.strip() for z in self.meta_vars["zone"].get().split(",") if z.strip()]
        return replace(
            base,
            title=self.meta_vars["title"].get().strip(),
            zones=zones,
            package=self.meta_vars["package"].get().strip(),
            date_text=typed_date,
            issue_date=base.issue_date if base.date_display == typed_date else None,
        )

    def _selected_categories(self) -> list[str]:
        return [self.cat_tree.item(i, "values")[0] for i in self.cat_tree.selection()]

    def _start_generate(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        folder = self.folder_var.get().strip()
        if not folder or not Path(folder).is_dir():
            messagebox.showerror(__app_name__, "Select a valid project folder first.")
            return
        categories = self._selected_categories()
        if not categories:
            messagebox.showerror(__app_name__, "Select at least one drawing category.")
            return
        output = self.output_var.get().strip()
        if not output:
            messagebox.showerror(__app_name__, "Choose where to save the workbook.")
            return
        if self.track_var.get():
            if not self.stage_combo.get():
                messagebox.showerror(
                    __app_name__,
                    "Choose the stage before adding this issue to the tracker.\n\n"
                    "IFA - issued for approval\n"
                    "IFF - issued for fabrication\n\n"
                    "It is never guessed from the folder name: an approval issue "
                    "mistaken for a fabrication release would rewrite the approved "
                    "baseline and report the rest of the package as on hold.",
                )
                return
            if not self.tracker_var.get().strip():
                messagebox.showerror(__app_name__, "Choose where to save the tracker.")
                return

        # Save default output folder if checkbox is checked
        if self.save_default_var.get():
            out_path = Path(output)
            if out_path.parent != Path(folder):
                self.settings.default_output_folder = str(out_path.parent)
                try:
                    save_settings(self.settings)
                except OSError as exc:
                    self._log(f"Warning: could not save default output folder: {exc}")

        self._clear_log()
        self._log(f"Processing {folder}")
        self._log(f"Categories: {', '.join(categories)}")
        self.gen_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.open_btn.configure(state="disabled")
        self.progress.configure(value=0, maximum=100)
        self._cancel.clear()

        meta = self._current_meta()
        settings = self._settings_from_ui()

        def work() -> None:
            try:
                reg = build_register(
                    folder, settings=settings, selected_categories=categories,
                    meta_override=meta,
                    progress=lambda d, t, label: self._queue.put(("progress", d, t, label)),
                    should_cancel=self._cancel.is_set,
                )
                if self._cancel.is_set():
                    self._queue.put(("cancelled",))
                    return
                path = write_register(reg, output,
                                      include_source=settings.include_source_column)
                self._queue.put(("done", reg, path))
            except Exception:
                self._queue.put(("error", traceback.format_exc()))

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()

    def _request_cancel(self) -> None:
        self._cancel.set()
        self._set_status("Cancelling...")

    def _open_output(self) -> None:
        if self.register_path and self.register_path.exists():
            webbrowser.open(self.register_path.as_uri())

    def _drain_queue(self) -> None:
        try:
            while True:
                msg = self._queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, done, total, label = msg
                    self.progress.configure(maximum=max(total, 1), value=done)
                    self._set_status(f"[{done}/{total}] {label}")
                elif kind == "done":
                    _, reg, path = msg
                    self._finish_generate(reg, path)
                elif kind == "cancelled":
                    self._set_status("Cancelled.")
                    self._log("Cancelled by user.")
                    self.gen_btn.configure(state="normal")
                    self.cancel_btn.configure(state="disabled")
                elif kind == "cmp_progress":
                    _, done, total, label = msg
                    self.cmp_progress.configure(maximum=max(total, 1), value=done)
                    self._set_status(f"[{done}/{total}] {label}")
                elif kind == "cmp_done":
                    self._show_comparison(msg[1])
                elif kind == "cmp_error":
                    self._log(msg[1])
                    self.cmp_btn.configure(state="normal")
                    self._set_status("Comparison failed - see log.")
                    messagebox.showerror(__app_name__, "Comparison failed. See the log on tab 1.")
                elif kind == "error":
                    _, tb = msg
                    self._log(tb)
                    self._set_status("Failed - see log.")
                    self.gen_btn.configure(state="normal")
                    self.cancel_btn.configure(state="disabled")
                    messagebox.showerror(__app_name__, "Generation failed. See the log.")
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def _finish_generate(self, reg: Register, path: Path) -> None:
        self.register = reg
        self.register_path = path
        self.gen_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self.open_btn.configure(state="normal")

        self._log("")
        for cat in reg.categories:
            note = f"  ({cat.review_count} need review)" if cat.review_count else ""
            self._log(f"  {cat.name:<20} {cat.total:>6} row(s){note}")
        self._log(f"\nTotal {reg.total} drawing(s).")
        if reg.review_count:
            self._log(f"{reg.review_count} row(s) could not be read confidently and are "
                      f"highlighted in the workbook.")
        self._log(f"Saved: {path}")
        self._set_status(f"Register written: {path.name}")

        self.reg_status.configure(
            text=f"Using generated register: {reg.total} drawing(s) across "
                 f"{len(reg.categories)} categor{'y' if len(reg.categories) == 1 else 'ies'}."
        )
        self.val_cat_combo.configure(values=["(all)"] + [c.name for c in reg.categories])
        self.val_cat_combo.set("(all)")

        if reg.review_count:
            messagebox.showwarning(
                __app_name__,
                f"Register generated with {reg.total} drawing(s).\n\n"
                f"{reg.review_count} row(s) could not be read confidently and are "
                f"highlighted in the workbook. If that count is high, tune the patterns "
                f"in the Extraction Settings tab and run again.",
            )

        if self.track_var.get():
            # The register is already safely written. A tracker problem must be
            # reported as a message, not lost as a traceback on a console the
            # engineer never sees.
            try:
                self._track_issue(reg)
            except Exception:
                self._log(traceback.format_exc())
                self._set_status("Register written, but the tracker failed - see log.")
                messagebox.showerror(
                    __app_name__,
                    "The register was written, but the package tracker could not be "
                    "updated. See the log for details.",
                )

    # -- tracker ------------------------------------------------------------

    def _pick_tracker(self) -> None:
        current = self.tracker_var.get().strip()
        chosen = filedialog.asksaveasfilename(
            title="Package tracker workbook", defaultextension=".xlsx",
            filetypes=[("Excel workbook", "*.xlsx")],
            initialfile=Path(current).name if current else "Package Tracker.xlsx",
            initialdir=str(Path(current).parent) if current else None,
            confirmoverwrite=False,   # an existing tracker is appended to, not replaced
        )
        if chosen:
            self.tracker_var.set(chosen)

    def _open_tracker(self) -> None:
        path = self.tracker_path or (
            Path(self.tracker_var.get().strip()) if self.tracker_var.get().strip() else None
        )
        if path and path.exists():
            webbrowser.open(path.as_uri())
        else:
            messagebox.showinfo(__app_name__, "No tracker has been written yet.")

    def _track_issue(self, reg: Register) -> None:
        """Append the generated issue to its package chain and refresh the tracker.

        Runs on the UI thread: the chain is built from the register already in
        memory, so nothing here re-reads a PDF.
        """
        tracker = Path(self.tracker_var.get().strip())
        state_file = state_path_for(tracker)
        state = load_state(state_file)
        stage = self.stage_combo.get()

        if not state.project:
            state.project = project_name_from(reg.meta.title)

        folder = Path(self.folder_var.get().strip())
        state.add_issue(IssueEntry(
            label=folder.name,
            stage=stage,
            round_no=self.round_var.get().strip() or reg.meta.issue_no,
            date_text=reg.meta.date_display,
            folder=str(folder),
            members=snapshot_register(reg),
        ))

        settings = self._settings_from_ui()
        chain = build_chain(state, settings)
        step = chain.steps[-1] if chain.steps else None

        # A fabrication release that left approved members behind is the moment
        # to ask why - the answer is only in the engineer's head right now.
        if stage == STAGE_IFF and step and step.on_hold:
            dialog = HoldReasonDialog(
                self.master, list(step.on_hold),
                release_label=f"{folder.name}   [{stage}-{self.round_var.get().strip()}]",
                released=len(step.released),
            )
            self.master.wait_window(dialog)
            if dialog.result is None:
                self._log("Tracker not updated - hold reasons were cancelled.")
                self._set_status("Tracker not updated.")
                return
            apply_reasons(state, dialog.result, settings)
            chain = build_chain(state, settings)

        try:
            save_state(state, state_file)
            path = write_tracker(chain, tracker)
        except OSError as exc:
            messagebox.showerror(__app_name__, f"Could not write the tracker:\n{exc}")
            return

        self.tracker_path = path
        self._log("")
        self._log(f"Tracker: {chain.project or '(unnamed package)'}")
        for idx, entry in enumerate(chain.issues, start=1):
            steps = [None] + list(chain.steps)
            s = steps[idx - 1] if idx - 1 < len(steps) else None
            self._log(f"  {idx}. {entry.code:<8} {entry.label[:44]:<46} "
                      f"{entry.total:>4}   {s.verdict if s else 'first issue'}")
        outstanding = chain.outstanding
        if outstanding:
            self._log(f"{len(outstanding)} approved member(s) still on hold "
                      f"(not removed).")
        self._log(f"Tracker saved: {path}")
        self._set_status(f"Tracker updated: {path.name}")

    # -- tab 2 actions ------------------------------------------------------

    def _update_reg_source(self) -> None:
        use_file = self.reg_source_var.get() == "file"
        state = "normal" if use_file else "disabled"
        self.reg_file_entry.configure(state=state)
        self.reg_file_btn.configure(state=state)

    def _pick_register(self) -> None:
        chosen = filedialog.askopenfilename(
            title="Select a drawing register workbook",
            filetypes=[("Excel workbook", "*.xlsx *.xlsm")],
        )
        if chosen:
            self.reg_file_var.set(chosen)

    def _pick_members(self) -> None:
        chosen = filedialog.askopenfilename(
            title="Select the model member list",
            filetypes=[("Member lists", "*.xlsx *.xlsm *.csv *.tsv *.txt"),
                       ("All files", "*.*")],
        )
        if not chosen:
            return
        self.model_var.set(chosen)
        names = sheet_names(chosen)
        self.sheet_combo.configure(values=names)
        if names:
            self.sheet_combo.set(names[0])
        else:
            self.sheet_combo.set("")
        self._refresh_columns()

    def _refresh_columns(self) -> None:
        path = self.model_var.get().strip()
        if not path:
            return
        try:
            sheet = self.sheet_combo.get() or None
            headers = preview_columns(path, sheet=sheet)
            loaded = read_member_list(path, sheet=sheet)
        except (OSError, ValueError) as exc:
            messagebox.showerror(__app_name__, str(exc))
            return
        self.column_combo.configure(values=headers or [loaded.column_name])
        self.column_combo.set(loaded.column_name)
        self.member_list = loaded
        self.model_status.configure(
            text=f"{loaded.count} member(s) read from column '{loaded.column_name}'."
        )

    def _settings_from_ui(self) -> AppSettings:
        s = self.settings
        s.compare_case_insensitive = self.ci_var.get()
        s.compare_ignore_whitespace = self.ws_var.get()
        s.compare_strip_leading_zeros = self.lz_var.get()
        return s

    def _resolve_register(self) -> Register | None:
        if self.reg_source_var.get() == "file":
            path = self.reg_file_var.get().strip()
            if not path:
                messagebox.showerror(__app_name__, "Choose a register workbook.")
                return None
            try:
                reg = read_register(path)
            except (OSError, ValueError) as exc:
                messagebox.showerror(__app_name__, f"Could not read the register:\n{exc}")
                return None
            if not reg.categories:
                messagebox.showerror(
                    __app_name__,
                    "That workbook has no recognisable register sheets.\n"
                    "A sheet needs a 'Member Name' column header.",
                )
                return None
            self.val_cat_combo.configure(values=["(all)"] + [c.name for c in reg.categories])
            self.reg_status.configure(
                text=f"Loaded {Path(path).name}: {reg.total} drawing(s)."
            )
            return reg

        if self.register is None:
            messagebox.showerror(
                __app_name__,
                "No register has been generated yet.\n"
                "Generate one in tab 1, or load an existing workbook.",
            )
            return None
        return self.register

    def _run_compare(self) -> None:
        register = self._resolve_register()
        if register is None:
            return

        path = self.model_var.get().strip()
        if not path:
            messagebox.showerror(__app_name__, "Choose the model member list.")
            return
        try:
            column = self.column_combo.get() or None
            sheet = self.sheet_combo.get() or None
            members = read_member_list(path, column=column, sheet=sheet)
        except (OSError, ValueError) as exc:
            messagebox.showerror(__app_name__, f"Could not read the member list:\n{exc}")
            return
        if not members.members:
            messagebox.showerror(
                __app_name__,
                f"No members found in column '{column}'.\nPick a different column.",
            )
            return
        self.member_list = members

        chosen = self.val_cat_combo.get()
        categories = None if chosen in ("", "(all)") else [chosen]

        result = validate(register, members, settings=self._settings_from_ui(),
                          categories=categories)
        self.result = result
        self._show_result(result)

    def _show_result(self, result: ValidationResult) -> None:
        for tree in self.res_trees.values():
            for item in tree.get_children():
                tree.delete(item)

        for name in result.missing_in_drawings:
            self.res_trees["missing"].insert("", "end", values=(name,))
        for rec in result.extra_records:
            self.res_trees["extra"].insert(
                "", "end",
                values=(rec.member_name, rec.category, rec.seq_no, rec.revision, rec.source_file),
            )
        for rec in result.matched_records:
            self.res_trees["matched"].insert(
                "", "end",
                values=(rec.member_name, rec.category, rec.seq_no, rec.revision, rec.source_file),
            )
        for name, files in result.duplicates_in_drawings.items():
            self.res_trees["dupes"].insert(
                "", "end", values=(name, len(files), ", ".join(files))
            )

        for idx, (title, count) in enumerate([
            ("Missing in Drawings", len(result.missing_in_drawings)),
            ("Not in Model", len(result.extra_in_drawings)),
            ("Matched", len(result.matched)),
            ("Duplicates", len(result.duplicates_in_drawings)),
        ]):
            self.res_nb.tab(idx, text=f"  {title} ({count})  ")

        self.verdict.configure(
            text=result.verdict,
            style="Good.TLabel" if result.is_clean else "Bad.TLabel",
        )
        self.export_btn.configure(state="normal")
        self._set_status(
            f"Compared {result.drawing_count} drawing member(s) against "
            f"{result.model_count} model member(s)."
        )
        if result.unnamed_drawings:
            messagebox.showwarning(
                __app_name__,
                f"{result.unnamed_drawings} drawing(s) had no member name and were "
                f"excluded from the comparison. Check the highlighted rows in the register.",
            )

    def _export_report(self) -> None:
        if not self.result:
            return
        initial = "Member Validation Report.xlsx"
        chosen = filedialog.asksaveasfilename(
            title="Save validation report", defaultextension=".xlsx",
            filetypes=[("Excel workbook", "*.xlsx")], initialfile=initial,
            initialdir=str(self.register_path.parent) if self.register_path else None,
        )
        if not chosen:
            return
        try:
            path = write_validation_report(self.result, chosen)
        except OSError as exc:
            messagebox.showerror(__app_name__, f"Could not save the report:\n{exc}")
            return
        self._set_status(f"Report written: {Path(path).name}")
        if messagebox.askyesno(__app_name__, f"Report saved to:\n{path}\n\nOpen it now?"):
            webbrowser.open(Path(path).as_uri())

    # -- tab 3 actions ------------------------------------------------------

    def _load_profile_into_ui(self, profile: ExtractionProfile) -> None:
        for key, widget in self.pattern_boxes.items():
            widget.delete("1.0", "end")
            widget.insert("1.0", "\n".join(getattr(profile, key)))
        for idx, value in enumerate(profile.title_block_rect):
            self.rect_vars[idx].set(str(value))
        self.largest_var.set(profile.use_largest_text_fallback)

    def _profile_from_ui(self) -> ExtractionProfile | None:
        profile = ExtractionProfile()
        for key, widget in self.pattern_boxes.items():
            lines = [ln.strip() for ln in widget.get("1.0", "end").splitlines() if ln.strip()]
            setattr(profile, key, lines)
        try:
            rect = tuple(float(v.get()) for v in self.rect_vars)
        except ValueError:
            messagebox.showerror(__app_name__, "Title block region must be four numbers.")
            return None
        if not all(0.0 <= v <= 1.0 for v in rect) or rect[0] >= rect[2] or rect[1] >= rect[3]:
            messagebox.showerror(
                __app_name__,
                "Title block region must be fractions between 0 and 1, with "
                "left < right and top < bottom.",
            )
            return None
        profile.title_block_rect = rect  # type: ignore[assignment]
        profile.use_largest_text_fallback = self.largest_var.get()
        return profile

    def _pick_test_pdf(self) -> None:
        chosen = filedialog.askopenfilename(
            title="Select a representative drawing",
            filetypes=[("PDF drawing", "*.pdf")],
        )
        if chosen:
            self.test_pdf_var.set(chosen)
            self._run_test()

    def _run_test(self) -> None:
        path = self.test_pdf_var.get().strip()
        if not path:
            messagebox.showinfo(__app_name__, "Choose a drawing PDF to test against.")
            return
        profile = self._profile_from_ui()
        if profile is None:
            return

        lines: list[str] = []
        try:
            data = dump_text(path)
            lines.append(f"File: {data['file']}    pages: {data['pages']}")
            if data.get("page_size"):
                w, h = data["page_size"]  # type: ignore[misc]
                lines.append(f"Page size: {w:.0f} x {h:.0f} pt")
            lines.append("")
            lines.append("Largest text spans   size    x       y     text")
            spans = data["spans"]  # type: ignore[assignment]
            if not spans:
                lines.append("  (no text at all - this is a scanned image, and OCR "
                             "would be needed to read it)")
            for size, text, (x, y) in spans[:25]:  # type: ignore[misc]
                lines.append(f"  {size:>18.1f}  {x:<6.3f}  {y:<6.3f}  {text}")

            record = extract_drawing(path, profile=profile)
            lines.append("")
            lines.append("--- Extracted with the patterns above ---")
            lines.append(f"  S.No:        {record.seq_no or '(none)':<22} [{record.seq_source}]")
            lines.append(f"  Member Name: {record.member_name or '(none)':<22} [{record.member_source}]")
            lines.append(f"  Revision No: {record.revision or '(none)':<22} [{record.revision_source}]")
            if record.notes:
                lines.append(f"  Notes:       {record.note_text}")
            if record.error:
                lines.append(f"  Error:       {record.error}")
            lines.append("")
            lines.append("Tip: the x/y columns are page fractions. If the mark you want sits "
                         "outside the title block region, widen it above.")
        except Exception as exc:
            lines.append(f"Failed to read the PDF: {exc}")

        self.test_out.configure(state="normal")
        self.test_out.delete("1.0", "end")
        self.test_out.insert("1.0", "\n".join(lines))
        self.test_out.configure(state="disabled")

    def _save_settings(self) -> None:
        profile = self._profile_from_ui()
        if profile is None:
            return
        self.settings.profile = profile
        self._settings_from_ui()
        try:
            path = save_settings(self.settings)
        except OSError as exc:
            messagebox.showerror(__app_name__, f"Could not save settings:\n{exc}")
            return
        self.settings_status.configure(text=f"Saved to {path}")
        self._set_status("Extraction settings saved.")

    def _reset_settings(self) -> None:
        if not messagebox.askyesno(__app_name__, "Restore the default extraction patterns?"):
            return
        self.settings.profile = ExtractionProfile()
        self._load_profile_into_ui(self.settings.profile)
        self.settings_status.configure(text="Defaults restored (not yet saved).")


def main() -> int:
    root = tk.Tk()
    FabDocApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
