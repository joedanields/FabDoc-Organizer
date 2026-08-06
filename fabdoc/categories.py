"""Discover drawing categories inside a project folder.

The requirement is that the tool adapts to whatever is present: one category
folder yields one worksheet, three yield three. Nothing is mandatory, and a
folder whose name matches none of the known categories is still processed under
its own name rather than being silently dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import DEFAULT_CATEGORY_ALIASES, DEFAULT_CATEGORY_ORDER

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Excel worksheet names cannot contain these, and cap at 31 characters.
_INVALID_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


def _normalise(name: str) -> str:
    return _NON_ALNUM.sub("", name.lower())


@dataclass
class DrawingCategory:
    """One drawing category and the PDFs belonging to it."""

    name: str                       # canonical name, used as the worksheet name
    folder: Path                    # directory the PDFs came from
    pdfs: list[Path] = field(default_factory=list)
    matched_alias: str = ""         # which alias matched, "" for unrecognised
    is_recognised: bool = True

    @property
    def count(self) -> int:
        return len(self.pdfs)


def classify_folder_name(
    folder_name: str,
    aliases: dict[str, list[str]] | None = None,
) -> tuple[str | None, str]:
    """Map a folder name to a canonical category.

    Returns ``(canonical_name, matched_alias)``, or ``(None, "")`` when the
    folder matches no known category. The longest alias wins so that "singlepart"
    beats "part" when both would match.
    """
    table = aliases or DEFAULT_CATEGORY_ALIASES
    key = _normalise(folder_name)
    best: tuple[str, str] | None = None
    for canonical, alias_list in table.items():
        for alias in alias_list:
            token = _normalise(alias)
            if token and token in key:
                if best is None or len(token) > len(best[1]):
                    best = (canonical, alias)
    return best if best else (None, "")


def list_pdfs(folder: Path, recursive: bool = True) -> list[Path]:
    """All PDFs in a folder, sorted naturally by name."""
    pattern = "**/*.pdf" if recursive else "*.pdf"
    files = [p for p in folder.glob(pattern) if p.is_file()]
    return sorted(files, key=lambda p: natural_key(p.name))


def natural_key(text: str) -> tuple:
    """Sort key that orders embedded numbers numerically ("A10" after "A9")."""
    parts = re.split(r"(\d+)", text.lower())
    return tuple(int(p) if p.isdigit() else p for p in parts)


def safe_sheet_name(name: str, used: set[str] | None = None) -> str:
    """Make a worksheet name Excel will accept, keeping it unique."""
    clean = _INVALID_SHEET_CHARS.sub("-", name).strip() or "Sheet"
    clean = clean[:31]
    if used is None:
        return clean
    candidate, n = clean, 2
    while candidate.lower() in {u.lower() for u in used}:
        suffix = f" ({n})"
        candidate = clean[: 31 - len(suffix)] + suffix
        n += 1
    used.add(candidate)
    return candidate


def discover_categories(
    project_folder: str | Path,
    aliases: dict[str, list[str]] | None = None,
    order: list[str] | None = None,
    recursive: bool = True,
    include_unrecognised: bool = True,
) -> list[DrawingCategory]:
    """Find every drawing category under ``project_folder``.

    Subfolders containing PDFs become categories. If PDFs sit loose in the
    project folder itself they are collected into a "Drawings" category, so a
    flat package still produces a register.
    """
    root = Path(project_folder)
    if not root.is_dir():
        raise NotADirectoryError(f"Not a folder: {root}")

    found: list[DrawingCategory] = []

    for sub in sorted(root.iterdir(), key=lambda p: natural_key(p.name)):
        if not sub.is_dir() or sub.name.startswith((".", "~$")):
            continue
        pdfs = list_pdfs(sub, recursive=recursive)
        if not pdfs:
            continue
        canonical, alias = classify_folder_name(sub.name, aliases)
        if canonical is None:
            if not include_unrecognised:
                continue
            found.append(
                DrawingCategory(name=sub.name.strip(), folder=sub, pdfs=pdfs,
                                matched_alias="", is_recognised=False)
            )
        else:
            found.append(
                DrawingCategory(name=canonical, folder=sub, pdfs=pdfs,
                                matched_alias=alias, is_recognised=True)
            )

    loose = list_pdfs(root, recursive=False)
    if loose:
        found.append(
            DrawingCategory(name="Drawings", folder=root, pdfs=loose,
                            matched_alias="", is_recognised=False)
        )

    # Merge duplicates: two folders can map to the same canonical category
    # (for example "Erection" and "Assembly Drawings").
    merged: dict[str, DrawingCategory] = {}
    for cat in found:
        existing = merged.get(cat.name)
        if existing:
            existing.pdfs.extend(cat.pdfs)
            existing.pdfs.sort(key=lambda p: natural_key(p.name))
        else:
            merged[cat.name] = cat

    ranking = order or DEFAULT_CATEGORY_ORDER
    def sort_key(cat: DrawingCategory) -> tuple[int, str]:
        try:
            return (ranking.index(cat.name), "")
        except ValueError:
            return (len(ranking), cat.name.lower())

    return sorted(merged.values(), key=sort_key)
