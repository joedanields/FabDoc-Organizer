"""Track one package across its whole issue chain.

A package is not a single delivery. It goes out for approval (IFA), comes back,
goes out again revised, and repeats until the client signs it off. Only then does
it move to fabrication (IFF) - and fabrication is released in shipments, a slice
of the approved scope at a time.

Two rules make this different from the pairwise register-against-register diff in
``compare.py``, and both come from how the work actually flows:

**Absent does not mean removed once fabrication starts.** Within IFA, a drawing
that disappears really was dropped. But an approved member missing from an IFF
release has simply not shipped yet. Reporting it as "removed" would be a false
alarm on the one report a fabricator acts on, so it is reported as *on hold* and
carries a reason the engineer supplies.

**Releases accumulate.** IFF-2 contains the next slice, not the previous one, so
diffing it against IFF-1 would mark the whole first shipment as removed. The last
IFA issue is the approved *baseline*, each release adds to a running *released*
set, and what is still outstanding is ``baseline - released``. That set shrinks
with every shipment, which is why the engineer is asked about a smaller balance
each time rather than the same list forever.

This module holds no UI. Reasons arrive as data in ``ChainState.hold_reasons``,
so the GUI can populate them from a dialog and the CLI from a flag.
"""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import AppSettings
from .extract import band_for_mark
from .register import Register
from .validate import normalise

STAGE_IFA = "IFA"   # Issued For Approval
STAGE_IFF = "IFF"   # Issued For Fabrication

STAGES = (STAGE_IFA, STAGE_IFF)

# "Stairs at Zone 1 and Zone 2 for Re Approval" -> "Stairs at Zone 1 and Zone 2".
_PURPOSE_CLAUSE = re.compile(
    r"\s+for\s+(?:re[\s-]*)?(?:approval|fabrication|construction|review|comment)\b.*$",
    re.IGNORECASE,
)

# "IFF-3 Stairs at Zone 1" -> "Stairs at Zone 1". A numeric "25." prefix is
# already lifted into ProjectMeta.issue_no by folder_meta, but a stage-coded
# prefix is not, and it survives into the title.
_LEADING_STAGE = re.compile(r"^(?:IFA|IFF)\b[\s\-_:#]*\d*[\s.\-_:)]*", re.IGNORECASE)


def project_name_from(title: str) -> str:
    """Reduce an issue title to the package it belongs to.

    Folder titles carry the identity of that one delivery: a leading stage code
    ("IFF-3"), and a trailing purpose clause ("for Approval", then "for Re
    Approval", then "for Fabrication"). Both change every issue while the package
    does not. Left in, each issue looks like a different project, lands in its own
    tracker, and nothing ever chains - which also silently disables the on-hold
    rule, because a release with no prior approval issue becomes its own baseline.
    """
    text = _LEADING_STAGE.sub("", title or "")
    text = _PURPOSE_CLAUSE.sub("", text)
    return text.strip(" -_") or (title or "").strip()


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


MEMBER_SEP = "::"


def member_id(category: str, name: str) -> str:
    """Identity of one tracked item: its category and its mark.

    An assembly drawing and a single-part drawing can carry the same mark and
    are different deliverables - the assembly is fabricated, the part is cut.
    Keyed by mark alone the two collapse into one entry, so whichever was read
    second silently overwrote the first and the release counts were wrong.
    """
    category = (category or "").strip()
    return f"{category}{MEMBER_SEP}{name}" if category else name


def split_member_id(ident: str) -> tuple[str, str]:
    """``("Assembly", "17130B103")``. Category is empty for an untagged id."""
    if MEMBER_SEP in ident:
        category, _, name = ident.partition(MEMBER_SEP)
        return category, name
    return "", ident


def is_tracked(category: str, cfg: AppSettings) -> bool:
    """Does the tracker follow this drawing category at all?"""
    skip = {c.strip().lower() for c in cfg.untracked_categories if c.strip()}
    return (category or "").strip().lower() not in skip


def snapshot_register(register: Register,
                      settings: AppSettings | None = None) -> "OrderedDict[str, dict[str, str]]":
    """Reduce a register to the member data the chain needs.

    Storing this rather than the register itself is what lets the tracker rebuild
    the whole history without re-scanning thousands of PDFs.
    """
    cfg = settings or AppSettings()
    out: "OrderedDict[str, dict[str, str]]" = OrderedDict()
    for rec in register.all_records():
        if not rec.member_name or not is_tracked(rec.category, cfg):
            continue
        ident = member_id(rec.category, rec.member_name)
        if ident in out:
            continue
        out[ident] = {
            "name": rec.member_name,
            "rev": rec.revision,
            "zone": rec.zone,
            "category": rec.category,
        }
    return out


@dataclass
class IssueEntry:
    """One issue folder in the chain, reduced to what the tracker needs."""

    label: str = ""                 # folder name, as the engineer knows it
    stage: str = STAGE_IFA
    round_no: str = ""              # the leading "15." / "25." of the folder
    date_text: str = ""
    folder: str = ""
    members: "OrderedDict[str, dict[str, str]]" = field(default_factory=OrderedDict)

    def __post_init__(self) -> None:
        """Re-key members to their canonical id.

        The category recorded against a member is the authority, not whatever
        the caller happened to key the dict by. Without this an entry built in
        memory and the same entry read back from disk hash differently, so a
        chain compared before saving and after reloading gave different answers.
        """
        canonical: "OrderedDict[str, dict[str, str]]" = OrderedDict()
        for ident, info in self.members.items():
            category = info.get("category") or split_member_id(ident)[0]
            name = info.get("name") or split_member_id(ident)[1]
            info = dict(info)
            info["name"] = name
            info["category"] = category
            canonical[member_id(category, name)] = info
        self.members = canonical

    @property
    def total(self) -> int:
        return len(self.members)

    @property
    def code(self) -> str:
        """Short stage code for the tracking table: "IFA-25", "IFF-2"."""
        return f"{self.stage}-{self.round_no}" if self.round_no else self.stage

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label, "stage": self.stage, "round_no": self.round_no,
            "date_text": self.date_text, "folder": self.folder,
            "members": {k: dict(v) for k, v in self.members.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IssueEntry":
        entry = cls(
            label=data.get("label", ""),
            stage=data.get("stage", STAGE_IFA),
            round_no=str(data.get("round_no", "")),
            date_text=data.get("date_text", ""),
            folder=data.get("folder", ""),
        )
        # Chains written before members were scoped by category are keyed by the
        # bare mark. The category was already stored alongside, so __post_init__
        # rebuilds the identity exactly rather than guessing it.
        for key, info in (data.get("members") or {}).items():
            info = dict(info)
            info.setdefault("name", split_member_id(key)[1])
            entry.members[key] = info
        entry.__post_init__()
        return entry


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class HoldRecord:
    """An approved member that has not shipped yet."""

    member_name: str
    category: str = ""              # Assembly, Part - the same mark can be both
    ident: str = ""                 # "Assembly::17130B103", as the history keys it
    member_key: str = ""            # comparison key, for recording the reason
    zone: str = ""
    revision: str = ""              # revision at the approved baseline
    reason: str = ""
    held_since: str = ""            # issue label where it first went outstanding
    released_in: str = ""           # issue label where it finally shipped

    @property
    def needs_reason(self) -> bool:
        return not self.reason.strip()


@dataclass
class ChainStep:
    """What changed between one issue and the next."""

    old_label: str = ""
    new_label: str = ""
    stage: str = STAGE_IFA          # stage of the NEW issue
    code: str = ""

    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    revised: list[tuple[str, str, str]] = field(default_factory=list)  # name, old, new
    unchanged: list[str] = field(default_factory=list)

    released: list[str] = field(default_factory=list)   # shipped in this IFF release
    on_hold: list[HoldRecord] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.stage == STAGE_IFF:
            bits = [f"{len(self.released)} released"]
            if self.added:
                bits.append(f"{len(self.added)} new")
            if self.on_hold:
                bits.append(f"{len(self.on_hold)} on hold")
            return ", ".join(bits)
        if not (self.added or self.removed or self.revised):
            return "no change"
        bits = []
        if self.added:
            bits.append(f"{len(self.added)} added")
        if self.removed:
            bits.append(f"{len(self.removed)} removed")
        if self.revised:
            bits.append(f"{len(self.revised)} revised")
        return ", ".join(bits)


@dataclass
class PackageChain:
    """The whole history of one package."""

    project: str = ""
    issues: list[IssueEntry] = field(default_factory=list)
    steps: list[ChainStep] = field(default_factory=list)
    holds: "OrderedDict[str, HoldRecord]" = field(default_factory=OrderedDict)
    # issue label -> number of tracked members, which is not entry.total once a
    # category is excluded from tracking.
    totals: dict[str, int] = field(default_factory=dict)
    # issue label -> category -> count. An assembly and a single part are
    # different deliverables, so a release of 324 drawings says nothing useful
    # until it says how much of it was assemblies.
    totals_by_category: dict[str, "OrderedDict[str, int]"] = field(default_factory=dict)
    # comparison key -> {issue label: revision}. Keyed by the comparison key
    # rather than the mark as written, so a member the detailer spelled
    # "17CH104" in one issue and "17ch104" in the next is one row, not two.
    history: "OrderedDict[str, dict[str, str]]" = field(default_factory=OrderedDict)
    # comparison key -> the member as most recently drawn: name, zone, category,
    # and the band it sits in ("seq"/"172", "type"/"CH").
    member_info: "OrderedDict[str, dict[str, str]]" = field(default_factory=OrderedDict)
    # Every key that has appeared in some IFF release, for the by-sequence view.
    released_keys: set = field(default_factory=set)

    @property
    def baseline_label(self) -> str:
        """Label of the last IFA issue - the approved scope."""
        for entry in reversed(self.issues):
            if entry.stage == STAGE_IFA:
                return entry.label
        return ""

    @property
    def outstanding(self) -> list[HoldRecord]:
        """Approved members still not released, newest state first."""
        return [h for h in self.holds.values() if not h.released_in]

    def missing_reasons(self) -> list[HoldRecord]:
        return [h for h in self.outstanding if h.needs_reason]


# ---------------------------------------------------------------------------
# Chain state (persisted)
# ---------------------------------------------------------------------------


@dataclass
class ChainState:
    """Everything needed to rebuild a package's tracking workbook.

    Persisted next to the tracker so appending a new issue and rebuilding the
    whole chain are the same operation over the same data.
    """

    project: str = ""
    issues: list[IssueEntry] = field(default_factory=list)
    hold_reasons: dict[str, str] = field(default_factory=dict)

    def index_of(self, label: str) -> int:
        for idx, entry in enumerate(self.issues):
            if entry.label == label:
                return idx
        return -1

    def add_issue(self, entry: IssueEntry) -> None:
        """Add an issue, replacing an earlier run of the same folder.

        Re-processing a folder is routine - patterns get tuned and the register
        regenerated - and must update that issue in place rather than appending a
        duplicate that would read as a new revision round.
        """
        existing = self.index_of(entry.label)
        if existing >= 0:
            self.issues[existing] = entry
        else:
            self.issues.append(entry)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "issues": [e.to_dict() for e in self.issues],
            "hold_reasons": self.hold_reasons,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChainState":
        state = cls(project=data.get("project", ""))
        for raw in data.get("issues") or []:
            state.issues.append(IssueEntry.from_dict(raw))
        state.hold_reasons = dict(data.get("hold_reasons") or {})
        return state


def state_path_for(tracker_path: str | Path) -> Path:
    """Sidecar JSON beside the tracking workbook."""
    p = Path(tracker_path)
    return p.with_suffix(".chain.json")


def load_state(path: str | Path) -> ChainState:
    """Load chain state, returning an empty chain when absent or unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return ChainState.from_dict(json.load(fh))
    except (OSError, ValueError, TypeError):
        return ChainState()


def save_state(state: ChainState, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(state.to_dict(), fh, indent=2)
    return p


# ---------------------------------------------------------------------------
# Chain construction
# ---------------------------------------------------------------------------


def _keys(entry: IssueEntry, cfg: AppSettings) -> "OrderedDict[str, str]":
    """Comparison key -> member id, for one issue.

    The key carries the category, so a part and an assembly of the same mark are
    compared as the two separate deliverables they are.
    """
    out: "OrderedDict[str, str]" = OrderedDict()
    for ident, info in entry.members.items():
        category = info.get("category") or split_member_id(ident)[0]
        if not is_tracked(category, cfg):
            continue
        key = normalise(info.get("name") or split_member_id(ident)[1], cfg)
        if not key:
            continue
        key = member_id(category, key)
        if key not in out:
            out[key] = ident
    return out


def build_chain(state: ChainState, settings: AppSettings | None = None) -> PackageChain:
    """Replay the issue list into a full package history.

    IFA issues are compared against the issue before them. IFF issues are
    compared against the approved baseline and the running released set, so a
    partial shipment never reports the unshipped balance as removed.
    """
    cfg = settings or AppSettings()
    chain = PackageChain(project=state.project, issues=list(state.issues))

    baseline: "OrderedDict[str, str]" = OrderedDict()   # key -> mark
    baseline_entry: IssueEntry | None = None
    released: set[str] = set()
    previous: IssueEntry | None = None

    for entry in state.issues:
        current = _keys(entry, cfg)

        chain.totals[entry.label] = len(current)
        per_category: "OrderedDict[str, int]" = OrderedDict()
        for ident in current.values():
            cat = split_member_id(ident)[0] or "-"
            per_category[cat] = per_category.get(cat, 0) + 1
        chain.totals_by_category[entry.label] = per_category
        for key, ident in current.items():
            info = entry.members.get(ident, {})
            chain.history.setdefault(key, {})[entry.label] = info.get("rev", "")
            # The latest issue is the current spelling of the mark.
            name = info.get("name") or split_member_id(ident)[1]
            kind, band = band_for_mark(name, cfg.profile)
            chain.member_info[key] = {
                "name": name,
                "zone": info.get("zone", "") or chain.member_info.get(key, {}).get("zone", ""),
                "category": info.get("category", ""),
                "band_kind": kind,
                "band": band,
            }

        step = ChainStep(
            old_label=previous.label if previous else "",
            new_label=entry.label,
            stage=entry.stage,
            code=entry.code,
        )

        if entry.stage == STAGE_IFF:
            if baseline_entry is None:
                # Fabrication without a prior approval issue: treat this release
                # as its own baseline rather than inventing an empty one.
                baseline, baseline_entry = current, entry

            newly = [k for k in current if k not in released]
            released |= set(current)
            chain.released_keys |= set(current)

            for key in newly:
                mark = current[key]
                if key in baseline:
                    step.released.append(mark)
                else:
                    step.added.append(mark)

            for key, mark in current.items():
                if key in baseline:
                    old_rev = entry.members.get(mark, {}).get("rev", "")
                    base_rev = baseline_entry.members.get(baseline[key], {}).get("rev", "")
                    if base_rev and old_rev and base_rev != old_rev:
                        step.revised.append((mark, base_rev, old_rev))

            for key, mark in baseline.items():
                info = baseline_entry.members.get(mark, {})
                hold = chain.holds.get(key)
                if key in released:
                    if hold and not hold.released_in:
                        hold.released_in = entry.label
                    continue
                if hold is None:
                    hold = HoldRecord(
                        member_name=info.get("name") or split_member_id(mark)[1],
                        category=info.get("category", ""),
                        ident=mark,
                        member_key=key,
                        zone=info.get("zone", ""),
                        revision=info.get("rev", ""),
                        held_since=entry.label,
                    )
                    chain.holds[key] = hold
                hold.reason = state.hold_reasons.get(key, hold.reason)
                step.on_hold.append(hold)

        elif previous is not None:
            prior = _keys(previous, cfg)
            for key, mark in current.items():
                if key not in prior:
                    step.added.append(mark)
                    continue
                new_rev = entry.members.get(mark, {}).get("rev", "")
                old_rev = previous.members.get(prior[key], {}).get("rev", "")
                if old_rev and new_rev and old_rev != new_rev:
                    step.revised.append((mark, old_rev, new_rev))
                else:
                    step.unchanged.append(mark)
            for key, mark in prior.items():
                if key not in current:
                    step.removed.append(mark)

        if entry.stage == STAGE_IFA:
            baseline, baseline_entry = current, entry

        if previous is not None or entry.stage == STAGE_IFF:
            chain.steps.append(step)
        previous = entry

    return chain


def apply_reasons(state: ChainState, reasons: dict[str, str],
                  settings: AppSettings | None = None) -> None:
    """Record hold reasons against their comparison keys.

    A key may arrive as the member mark, as a category-scoped id
    ("Assembly::17130B103"), or already normalised, so a caller can pass
    straight back whatever a dialog collected.
    """
    cfg = settings or AppSettings()
    for raw, reason in reasons.items():
        if not reason or not reason.strip():
            continue
        category, name = split_member_id(raw)
        key = normalise(name, cfg)
        if not key:
            continue
        state.hold_reasons[member_id(category, key)] = reason.strip()
