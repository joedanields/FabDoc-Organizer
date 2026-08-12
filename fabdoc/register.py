"""Build a drawing register from a project folder."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from . import sequencing
from .categories import DrawingCategory, discover_categories, natural_key
from .config import AppSettings
from .extract import DrawingRecord, extract_drawing
from .folder_meta import ProjectMeta, parse_folder

ProgressFn = Callable[[int, int, str], None]
CancelFn = Callable[[], bool]


@dataclass
class CategoryRegister:
    """Extracted rows for one drawing category."""

    name: str
    folder: Path
    records: list[DrawingRecord] = field(default_factory=list)
    is_recognised: bool = True

    @property
    def total(self) -> int:
        return len(self.records)

    @property
    def review_count(self) -> int:
        return sum(1 for r in self.records if r.needs_review)

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.records if r.error)

    def member_names(self) -> list[str]:
        return [r.member_name for r in self.records if r.member_name]

    @property
    def zones(self) -> list[str]:
        """Distinct zones present, in register order."""
        out: list[str] = []
        for rec in self.records:
            if rec.zone and rec.zone not in out:
                out.append(rec.zone)
        return out

    def zone_groups(self) -> list[tuple[str, list[DrawingRecord]]]:
        """Records clustered by zone, in register order.

        Returns a single ("", records) group when no drawing carries a zone, so
        callers can use one code path whether or not zones are in play.
        """
        if not self.zones:
            return [("", list(self.records))]
        grouped: dict[str, list[DrawingRecord]] = {}
        for rec in self.records:
            grouped.setdefault(rec.zone, []).append(rec)
        ordered = sorted(grouped, key=lambda z: (not z.isdigit(), int(z) if z.isdigit() else 0, z))
        return [(z, grouped[z]) for z in ordered]

    def sequences_for(self, zone: str) -> list[str]:
        """Sequence numbers contributing to a zone, e.g. zone 1 -> 172, 173."""
        out: list[str] = []
        for rec in self.records:
            if rec.zone == zone and rec.seq_group and rec.seq_group not in out:
                out.append(rec.seq_group)
        return sorted(out, key=sequencing.sort_key)

    def band_groups(self, zone: str) -> list[tuple[tuple[str, str], list[DrawingRecord]]]:
        """One zone's records clustered into bands, in reading order.

        A band is a sequence for assemblies and a type for single parts (see
        ``fabdoc.sequencing``). Returns a single ("", "") band when nothing in
        the zone carries either, so the writer needs no special case for a
        package that encodes neither.
        """
        clustered: dict[tuple[str, str], list[DrawingRecord]] = {}
        for rec in self.records:
            if rec.zone != zone:
                continue
            clustered.setdefault(rec.band, []).append(rec)
        if not clustered:
            return []
        if list(clustered) == [("", "")]:
            return [(("", ""), clustered[("", "")])]
        ordered = sorted(clustered, key=sequencing.band_sort_key)
        return [(b, clustered[b]) for b in ordered]


@dataclass
class Register:
    """A complete drawing register for one issue folder."""

    meta: ProjectMeta
    project_folder: Path
    categories: list[CategoryRegister] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(c.total for c in self.categories)

    @property
    def review_count(self) -> int:
        return sum(c.review_count for c in self.categories)

    def all_records(self) -> list[DrawingRecord]:
        out: list[DrawingRecord] = []
        for cat in self.categories:
            out.extend(cat.records)
        return out

    def member_names(self) -> list[str]:
        return [r.member_name for r in self.all_records() if r.member_name]


def sort_records(records: Iterable[DrawingRecord]) -> list[DrawingRecord]:
    """Order rows by zone, then band, then member mark.

    Where a mark encodes zone and sequence ("17172C172"), that ordering is far
    more meaningful than the file order the S.No fell back to. Sequences run
    numerically, so 10 comes before 120 rather than after it, and single parts
    band by type after them. Rows without a zone sort after those with one, and
    nothing here can raise on odd data.
    """
    def key(rec: DrawingRecord):
        zone = (rec.zone or "").strip()
        zone_rank = (0, int(zone), "") if zone.isdigit() else ((1, 0, zone) if zone else (2, 0, ""))
        return (zone_rank, sequencing.band_sort_key(rec.band),
                natural_key(rec.member_name or rec.source_file))

    return sorted(records, key=key)


def renumber_by_zone(records: Iterable[DrawingRecord]) -> None:
    """Assign S.No 1..N restarting within each zone and band, in place.

    The drawings carry no printed sequence number, so S.No is a position in the
    register. It restarts at every band heading, which is what makes it read as
    "the fourth drawing of sequence 172" rather than a running total nobody can
    use. The band headings and the summary carry the counts.
    """
    counters: dict[tuple[str, tuple[str, str]], int] = {}
    for rec in records:
        key = (rec.zone or "", rec.band)
        counters[key] = counters.get(key, 0) + 1
        rec.seq_no = str(counters[key])


def build_register(
    project_folder: str | Path,
    settings: AppSettings | None = None,
    selected_categories: list[str] | None = None,
    meta_override: ProjectMeta | None = None,
    progress: ProgressFn | None = None,
    should_cancel: CancelFn | None = None,
) -> Register:
    """Scan a project folder and extract every drawing into a register.

    ``selected_categories`` limits processing to named categories; ``None``
    processes everything found. ``progress`` is called as
    ``(done, total, label)`` and ``should_cancel`` is polled between files.
    """
    cfg = settings or AppSettings()
    root = Path(project_folder)

    meta = meta_override or parse_folder(root, day_first=cfg.day_first_dates)

    cats: list[DrawingCategory] = discover_categories(
        root, aliases=cfg.category_aliases, order=cfg.category_order
    )
    if selected_categories is not None:
        wanted = {c.lower() for c in selected_categories}
        cats = [c for c in cats if c.name.lower() in wanted]

    total = sum(len(c.pdfs) for c in cats)
    done = 0
    register = Register(meta=meta, project_folder=root)

    for cat in cats:
        cat_reg = CategoryRegister(name=cat.name, folder=cat.folder,
                                   is_recognised=cat.is_recognised)
        for index, pdf in enumerate(cat.pdfs, start=1):
            if should_cancel and should_cancel():
                register.categories.append(cat_reg)
                return register
            record = extract_drawing(
                pdf, profile=cfg.profile, category=cat.name, fallback_seq=index
            )
            cat_reg.records.append(record)
            done += 1
            if progress:
                progress(done, total, f"{cat.name}: {pdf.name}")
        cat_reg.records = sort_records(cat_reg.records)
        if cfg.group_by_zone:
            renumber_by_zone(cat_reg.records)
        register.categories.append(cat_reg)

    # Zones found in the drawings are more reliable than zones guessed from the
    # folder name, so let them win when both are available.
    drawing_zones: list[str] = []
    for cat in register.categories:
        for zone in cat.zones:
            if zone not in drawing_zones:
                drawing_zones.append(zone)
    if drawing_zones:
        meta.zones = sorted(
            drawing_zones, key=lambda z: (not z.isdigit(), int(z) if z.isdigit() else 0, z)
        )

    return register
