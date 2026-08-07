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


def project_name_from(title: str) -> str:
    """Strip the issue-purpose clause so every issue maps to one project.

    Folder titles carry the purpose of that particular issue - "for Approval",
    then "for Re Approval", then "for Fabrication". The purpose changes every
    time; the package does not. Left in, each issue would look like a different
    project and never chain together.
    """
    return _PURPOSE_CLAUSE.sub("", title or "").strip(" -_") or (title or "").strip()


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def snapshot_register(register: Register) -> "OrderedDict[str, dict[str, str]]":
    """Reduce a register to the member data the chain needs, keyed by mark.

    Storing this rather than the register itself is what lets the tracker rebuild
    the whole history without re-scanning thousands of PDFs.
    """
    out: "OrderedDict[str, dict[str, str]]" = OrderedDict()
    for rec in register.all_records():
        if not rec.member_name or rec.member_name in out:
            continue
        out[rec.member_name] = {
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
        for name, info in (data.get("members") or {}).items():
            entry.members[name] = dict(info)
        return entry


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class HoldRecord:
    """An approved member that has not shipped yet."""

    member_name: str
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
    history: "OrderedDict[str, dict[str, str]]" = field(default_factory=OrderedDict)

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
    """Comparison key -> original mark, for one issue."""
    out: "OrderedDict[str, str]" = OrderedDict()
    for name in entry.members:
        key = normalise(name, cfg)
        if key and key not in out:
            out[key] = name
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

        for name, info in entry.members.items():
            chain.history.setdefault(name, {})[entry.label] = info.get("rev", "")

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
                        member_name=mark,
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

    Keys may be given as either the member mark or an already-normalised key, so
    a caller can pass straight back what a dialog collected.
    """
    cfg = settings or AppSettings()
    for name, reason in reasons.items():
        if not reason or not reason.strip():
            continue
        key = normalise(name, cfg)
        if key:
            state.hold_reasons[key] = reason.strip()
