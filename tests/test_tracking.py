"""Tests for the IFA/IFF package chain.

The rules under test are the ones that differ from a pairwise register diff:
absent members are on hold rather than removed once fabrication starts, and
releases accumulate against the approved baseline instead of against each other.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

import pytest
from openpyxl import load_workbook

from conftest import make_drawing

from fabdoc.register import build_register
from fabdoc.tracking import (STAGE_IFA, STAGE_IFF, ChainState, IssueEntry,
                             apply_reasons, build_chain, load_state,
                             project_name_from, save_state, snapshot_register,
                             state_path_for)
from fabdoc.tracking_out import write_tracker


def key(mark: str, category: str = "Assembly") -> str:
    """The id the chain tracks a mark under, now that category is part of it."""
    return f"{category}::{mark}"


def issue(label: str, stage: str, round_no: str, marks: dict[str, str]) -> IssueEntry:
    members: "OrderedDict[str, dict[str, str]]" = OrderedDict()
    for mark, rev in marks.items():
        members[mark] = {"rev": rev, "zone": mark[2] if len(mark) > 2 else "", "category": "Assembly"}
    return IssueEntry(label=label, stage=stage, round_no=round_no, members=members)


def approved_then_released(released: list[dict[str, str]]) -> ChainState:
    """An approved scope of five members, then the given IFF releases."""
    state = ChainState(project="Stairs")
    state.add_issue(issue("IFA-1", STAGE_IFA, "1",
                          {"A1": "A", "A2": "A", "A3": "A", "A4": "A", "A5": "A"}))
    for idx, marks in enumerate(released, start=1):
        state.add_issue(issue(f"IFF-{idx}", STAGE_IFF, str(idx), marks))
    return state


# ------------------------------------------------------------------ IFA rounds


def test_ifa_round_reports_added_revised_and_removed():
    state = ChainState(project="Stairs")
    state.add_issue(issue("IFA-15", STAGE_IFA, "15", {"A1": "A", "A2": "A", "A3": "A"}))
    state.add_issue(issue("IFA-25", STAGE_IFA, "25", {"A1": "A", "A2": "B", "A9": "A"}))
    step = build_chain(state).steps[-1]
    assert step.added == [key("A9")]
    assert step.removed == [key("A3")]
    assert [r[0] for r in step.revised] == [key("A2")]
    assert step.unchanged == [key("A1")]


def test_within_approval_a_dropped_drawing_is_still_removed():
    """The on-hold rule must not leak backwards into the IFA rounds."""
    state = ChainState()
    state.add_issue(issue("IFA-1", STAGE_IFA, "1", {"A1": "A", "A2": "A"}))
    state.add_issue(issue("IFA-2", STAGE_IFA, "2", {"A1": "B"}))
    step = build_chain(state).steps[-1]
    assert step.removed == [key("A2")]
    assert not step.on_hold


# ------------------------------------------------------- IFF partial releases


def test_unshipped_members_are_on_hold_not_removed():
    chain = build_chain(approved_then_released([{"A1": "A", "A2": "A"}]))
    step = chain.steps[-1]
    assert step.removed == []
    assert sorted(h.member_name for h in step.on_hold) == ["A3", "A4", "A5"]
    assert sorted(step.released) == [key("A1"), key("A2")]


def test_second_release_does_not_mark_the_first_as_removed():
    """The trap a pairwise diff falls into: IFF-2 holds the next slice, not the previous."""
    chain = build_chain(approved_then_released([{"A1": "A", "A2": "A"}, {"A3": "A"}]))
    step = chain.steps[-1]
    assert step.removed == []
    assert step.released == [key("A3")]
    assert sorted(h.member_name for h in step.on_hold) == ["A4", "A5"]


def test_outstanding_shrinks_with_every_release():
    counts = []
    for releases in ([{"A1": "A"}],
                     [{"A1": "A"}, {"A2": "A", "A3": "A"}],
                     [{"A1": "A"}, {"A2": "A", "A3": "A"}, {"A4": "A", "A5": "A"}]):
        counts.append(len(build_chain(approved_then_released(releases)).outstanding))
    assert counts == [4, 2, 0]


def test_a_held_member_records_where_it_finally_shipped():
    chain = build_chain(approved_then_released([{"A1": "A"}, {"A3": "A"}]))
    hold = chain.holds[key("A3")]
    assert hold.held_since == "IFF-1"
    assert hold.released_in == "IFF-2"
    assert hold not in chain.outstanding


def test_a_release_may_add_scope_the_baseline_never_had():
    chain = build_chain(approved_then_released([{"A1": "A", "NEW9": "A"}]))
    step = chain.steps[-1]
    assert step.added == [key("NEW9")]
    assert step.released == [key("A1")]


def test_fabrication_without_a_prior_approval_uses_itself_as_baseline():
    state = ChainState()
    state.add_issue(issue("IFF-1", STAGE_IFF, "1", {"A1": "A"}))
    chain = build_chain(state)
    assert chain.outstanding == []
    assert chain.steps[-1].released == [key("A1")]


# -------------------------------------------------------------- hold reasons


def test_outstanding_members_start_without_a_reason():
    chain = build_chain(approved_then_released([{"A1": "A"}]))
    assert len(chain.missing_reasons()) == 4


def test_a_batch_reason_can_be_overridden_per_member():
    state = approved_then_released([{"A1": "A"}])
    chain = build_chain(state)
    # Keyed by identity, not by the displayed mark: that is what a dialog
    # hands back, and what keeps an assembly and a part apart.
    reasons = {h.ident: "Client hold" for h in chain.outstanding}
    reasons[key("A5")] = "Material shortage"
    apply_reasons(state, reasons)
    chain = build_chain(state)
    assert chain.missing_reasons() == []
    assert chain.holds[key("A5")].reason == "Material shortage"
    assert chain.holds[key("A4")].reason == "Client hold"


def test_a_reason_survives_into_later_releases():
    state = approved_then_released([{"A1": "A"}])
    apply_reasons(state, {key("A5"): "Client hold"})
    state.add_issue(issue("IFF-2", STAGE_IFF, "2", {"A2": "A"}))
    chain = build_chain(state)
    assert chain.holds[key("A5")].reason == "Client hold"


def test_blank_reasons_are_ignored():
    state = approved_then_released([{"A1": "A"}])
    apply_reasons(state, {key("A5"): "   "})
    assert build_chain(state).holds[key("A5")].needs_reason


# ------------------------------------------------------------- state and names


def test_reprocessing_a_folder_updates_it_rather_than_chaining_a_duplicate():
    state = ChainState()
    state.add_issue(issue("IFA-1", STAGE_IFA, "1", {"A1": "A"}))
    state.add_issue(issue("IFA-1", STAGE_IFA, "1", {"A1": "A", "A2": "A"}))
    assert len(state.issues) == 1
    assert state.issues[0].total == 2


def test_chain_state_round_trips_through_json(tmp_path: Path):
    state = approved_then_released([{"A1": "A"}])
    apply_reasons(state, {key("A5"): "Client hold"})
    path = state_path_for(tmp_path / "Package Tracker.xlsx")
    save_state(state, path)
    assert path.name.endswith(".chain.json")

    reloaded = load_state(path)
    assert [e.label for e in reloaded.issues] == ["IFA-1", "IFF-1"]
    assert build_chain(reloaded).holds[key("A5")].reason == "Client hold"


def test_missing_state_file_yields_an_empty_chain(tmp_path: Path):
    assert load_state(tmp_path / "nope.chain.json").issues == []


@pytest.mark.parametrize("title", [
    "Stairs at Zone 1 and Zone 2 for Approval",
    "Stairs at Zone 1 and Zone 2 for Re Approval",
    "Stairs at Zone 1 and Zone 2 for Re-Approval",
    "Stairs at Zone 1 and Zone 2 for Fabrication",
    # A stage-coded folder prefix survives folder_meta, which only lifts a
    # numeric "25." into issue_no. Left in, the fabrication issues chained into
    # their own tracker and the on-hold rule silently switched itself off.
    "IFF-0    Stairs at Zone 1 and Zone 2 for Fabrication",
    "IFF-3 Stairs at Zone 1 and Zone 2 for Fabrication",
    "IFA-2 Stairs at Zone 1 and Zone 2 for Re Approval",
    "IFF 1 - Stairs at Zone 1 and Zone 2",
])
def test_every_issue_purpose_maps_to_one_project(title: str):
    assert project_name_from(title) == "Stairs at Zone 1 and Zone 2"


def test_a_title_without_a_purpose_clause_is_left_alone():
    assert project_name_from("Skyline Tower") == "Skyline Tower"


# ------------------------------------------------------------------- workbook


def test_tracker_workbook_has_the_expected_sheets(tmp_path: Path):
    state = approved_then_released([{"A1": "A"}])
    apply_reasons(state, {key("A5"): "Client hold"})
    out = write_tracker(build_chain(state), tmp_path / "tracker.xlsx")
    wb = load_workbook(out)
    assert wb.sheetnames == ["Tracker", "History - Assembly", "Change Log", "On Hold"]
    wb.close()


def test_member_history_shows_a_revision_per_issue(tmp_path: Path):
    state = ChainState()
    state.add_issue(issue("IFA-1", STAGE_IFA, "1", {"A1": "A"}))
    state.add_issue(issue("IFA-2", STAGE_IFA, "2", {"A1": "B"}))
    out = write_tracker(build_chain(state), tmp_path / "tracker.xlsx")
    wb = load_workbook(out)
    ws = wb["History - Assembly"]
    assert [c.value for c in ws[3]] == ["Member Name", "Zone", "IFA-1", "IFA-2"]
    assert [c.value for c in ws[4]][:1] + [c.value for c in ws[4]][2:] == ["A1", "A", "B"]
    wb.close()


def test_on_hold_sheet_carries_the_reason(tmp_path: Path):
    state = approved_then_released([{"A1": "A"}])
    apply_reasons(state, {key(h): "Client hold" for h in ("A2", "A3", "A4", "A5")})
    out = write_tracker(build_chain(state), tmp_path / "tracker.xlsx")
    wb = load_workbook(out)
    rows = list(wb["On Hold"].iter_rows(min_row=4, values_only=True))
    assert {r[0] for r in rows if r[0]} == {"A2", "A3", "A4", "A5"}
    assert all(r[5] == "Client hold" for r in rows if r[0])
    wb.close()


# ------------------------------------------------------- against real drawings


def test_snapshot_keeps_one_row_per_member(tmp_path: Path):
    root = tmp_path / "15. Stairs at Zone 1 for Approval - 2026-06-26"
    for mark in ("17172C1", "17172C2", "17271X1"):
        make_drawing(root / "Assembly" / f"{mark}  - Rev A.pdf", mark, revision="A")
    snap = snapshot_register(build_register(root))
    # Keyed by category and mark, because a part drawing may carry the same one.
    assert set(snap) == {key("17172C1"), key("17172C2"), key("17271X1")}
    assert snap[key("17271X1")]["rev"] == "A"
    assert snap[key("17271X1")]["zone"] == "2"
    assert snap[key("17271X1")]["name"] == "17271X1"


# ------------------------------------------------- categories in one tracker


def part(label: str, stage: str, round_no: str,
         marks: dict[str, str], category: str) -> IssueEntry:
    members: "OrderedDict[str, dict[str, str]]" = OrderedDict()
    for mark, rev in marks.items():
        members[mark] = {"rev": rev, "zone": "1", "category": category}
    return IssueEntry(label=label, stage=stage, round_no=round_no, members=members)


def test_a_part_and_an_assembly_sharing_a_mark_are_two_items():
    """Keyed by mark alone, whichever was read second overwrote the first.

    An assembly drawing is fabricated and a single-part drawing is cut; they
    are different deliverables that routinely carry the same mark.
    """
    members: "OrderedDict[str, dict[str, str]]" = OrderedDict()
    members["17172C172"] = {"rev": "A", "category": "Assembly"}
    members["Part::17172C172"] = {"rev": "0", "category": "Part"}
    entry = IssueEntry(label="IFA-1", stage=STAGE_IFA, round_no="1", members=members)

    assert entry.total == 2
    assert set(entry.members) == {"Assembly::17172C172", "Part::17172C172"}
    assert entry.members["Part::17172C172"]["name"] == "17172C172"


def test_a_part_is_not_released_by_shipping_the_assembly():
    state = ChainState(project="Zone 1")
    state.add_issue(part("IFA-1", STAGE_IFA, "1", {"C172": "A"}, "Assembly"))
    state.issues[-1].members["Part::C172"] = {
        "name": "C172", "rev": "0", "zone": "1", "category": "Part"}
    state.issues[-1].__post_init__()
    state.add_issue(part("IFF-2", STAGE_IFF, "2", {"C172": "A"}, "Assembly"))

    chain = build_chain(state)
    step = chain.steps[-1]
    assert step.released == ["Assembly::C172"]
    # The part of the same mark has not shipped and is still owed.
    assert [h.ident for h in step.on_hold] == ["Part::C172"]
    assert [h.category for h in chain.outstanding] == ["Part"]


def test_every_category_shares_one_tracker_file(tmp_path: Path):
    """Parts get their own sheet, not their own workbook.

    A package that issues assemblies and single parts is one package with one
    chain; a second file would break the chaining the tracker exists for.
    """
    state = ChainState(project="Zone 1")
    entry = part("IFA-1", STAGE_IFA, "1", {"C1": "A", "C2": "A"}, "Assembly")
    entry.members["Part::P9"] = {"name": "P9", "rev": "0", "zone": "1",
                                 "category": "Part"}
    entry.__post_init__()
    state.add_issue(entry)

    out = write_tracker(build_chain(state), tmp_path / "tracker.xlsx")
    assert list(tmp_path.glob("*.xlsx")) == [out], "a second workbook was written"

    wb = load_workbook(out)
    try:
        assert wb.sheetnames == ["Tracker", "History - Assembly", "History - Part",
                                 "Change Log"]
        assert [r[0] for r in wb["History - Part"].iter_rows(min_row=4,
                                                             values_only=True)] == ["P9"]
        assert sorted(r[0] for r in wb["History - Assembly"].iter_rows(
            min_row=4, values_only=True)) == ["C1", "C2"]
    finally:
        wb.close()


def test_reasons_can_be_keyed_by_identity_or_by_mark():
    """A dialog hands back whatever it used as a row id."""
    state = ChainState(project="Zone 1")
    state.add_issue(part("IFA-1", STAGE_IFA, "1", {"A1": "A", "A2": "A"}, "Assembly"))
    state.add_issue(part("IFF-2", STAGE_IFF, "2", {"A1": "A"}, "Assembly"))

    apply_reasons(state, {"Assembly::A2": "Client hold"})
    assert build_chain(state).holds["Assembly::A2"].reason == "Client hold"


# --------------------------------------------------------------- mark casing


def _members(names, category="Part", rev="0"):
    from collections import OrderedDict
    return OrderedDict(
        (n, {"name": n, "rev": rev, "zone": "", "category": category}) for n in names
    )


def test_a_case_change_between_issues_is_the_same_member():
    """Marks are now recorded as drawn, and existing trackers hold them upper.

    An issue added after that change must chain onto the ones before it, not
    read as the old member removed and a new one added.
    """
    state = ChainState(project="P")
    state.add_issue(IssueEntry(label="approval", stage=STAGE_IFA, round_no="1",
                               members=_members(["17CH104", "17HSP1"])))
    state.add_issue(IssueEntry(label="release", stage=STAGE_IFF, round_no="1",
                               members=_members(["17ch104"])))
    chain = build_chain(state)
    step = chain.steps[-1]

    assert len(step.released) == 1
    assert not step.added, "a case change read as a different member"
    assert len(step.on_hold) == 1


def test_a_case_change_does_not_duplicate_the_history_row():
    """Member History is keyed by comparison key, so one member is one row."""
    state = ChainState(project="P")
    state.add_issue(IssueEntry(label="approval", stage=STAGE_IFA, round_no="1",
                               members=_members(["17CH104"])))
    state.add_issue(IssueEntry(label="release", stage=STAGE_IFF, round_no="1",
                               members=_members(["17ch104"])))
    chain = build_chain(state)

    assert len(chain.history) == 1
    # The most recent spelling is what the sheet shows.
    assert list(chain.member_info.values())[0]["name"] == "17ch104"


def test_a_hold_reason_survives_a_case_change():
    state = ChainState(project="P")
    state.add_issue(IssueEntry(label="approval", stage=STAGE_IFA, round_no="1",
                               members=_members(["17ch104", "17hsp1"])))
    state.add_issue(IssueEntry(label="release", stage=STAGE_IFF, round_no="1",
                               members=_members(["17ch104"])))
    chain = build_chain(state)
    hold = chain.outstanding[0]
    assert hold.member_name == "17hsp1"

    apply_reasons(state, {hold.ident: "awaiting material"})
    assert build_chain(state).outstanding[0].reason == "awaiting material"


def test_the_history_sheet_lists_each_member_once(tmp_path: Path):
    state = ChainState(project="P")
    state.add_issue(IssueEntry(label="approval", stage=STAGE_IFA, round_no="1",
                               members=_members(["17CH104", "17HSP1"])))
    state.add_issue(IssueEntry(label="release", stage=STAGE_IFF, round_no="1",
                               members=_members(["17ch104"])))
    out = write_tracker(build_chain(state), tmp_path / "T.xlsx")

    wb = load_workbook(out)
    try:
        sheet = next(n for n in wb.sheetnames if "History" in n)
        names = [r[0] for r in wb[sheet].iter_rows(min_row=4, values_only=True) if r and r[0]]
    finally:
        wb.close()
    assert sorted(names) == ["17HSP1", "17ch104"]
