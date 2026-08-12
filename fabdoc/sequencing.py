"""How a register is split into bands inside each zone.

An assembly drawing carries the erection sequence in its mark - "17172C172" is
job 17, sequence 172 - and assemblies are grouped by that sequence, ascending.

A single part carries no sequence, because a part is not erected: it is cut for
an assembly. Its mark carries a type instead - "17ch104" is job 17, type CH,
piece 104 - and parts are grouped by that type. In a real package 92 of 124
single-part drawings are named this way, so leaving them ungrouped would put
three quarters of the sheet in one nameless heap.

A band is therefore ``(kind, value)``: ``("seq", "172")`` or ``("type", "CH")``.
Sequences come first and run numerically, so 10 sorts before 120 rather than
after it; type bands follow, alphabetically.
"""

from __future__ import annotations

# Rows with neither a sequence nor a type sort after everything that has one.
NO_BAND = 10 ** 9

SEQ = "seq"
TYPE = "type"


def sort_key(seq: str) -> tuple:
    """Order sequence numbers numerically, unreadable ones last."""
    text = (seq or "").strip()
    if text.isdigit():
        return (0, int(text), "")
    return (1, NO_BAND, text)


def band_sort_key(band: tuple[str, str]) -> tuple:
    """Sequences first in numeric order, then types alphabetically."""
    kind, value = band
    if kind == SEQ:
        return (0,) + sort_key(value)
    if kind == TYPE:
        return (1, 0, 0, (value or "").upper())
    return (2, 0, NO_BAND, "")


def band_label(band: tuple[str, str]) -> str:
    """The band heading: "SEQ 172", "TYPE CH"."""
    kind, value = band
    if kind == SEQ and value:
        return f"SEQ {value}"
    if kind == TYPE and value:
        return f"TYPE {value.upper()}"
    return "NO SEQUENCE"


def label_for(seq: str) -> str:
    """Band heading for a bare sequence number."""
    return band_label((SEQ, seq)) if (seq or "").strip() else "NO SEQUENCE"
