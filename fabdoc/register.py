"""Build a drawing register from a project folder."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

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
    """Order rows by sequence number, numerically where possible.

    Rows whose sequence is non-numeric sort after the numeric ones rather than
    raising, and member name breaks ties so the order is stable.
    """
    def key(rec: DrawingRecord):
        raw = (rec.seq_no or "").strip()
        if raw.isdigit():
            return (0, int(raw), natural_key(rec.member_name or rec.source_file))
        return (1, 0, natural_key(raw or rec.member_name or rec.source_file))

    return sorted(records, key=key)


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
        register.categories.append(cat_reg)

    return register
