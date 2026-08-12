"""The erection sequence a drawing belongs to.

A member mark carries the sequence in it: "17172C172" is job 17, sequence 172,
member C172. The register is segregated by that sequence, inside each zone.

Sequences run in plain ascending order - 10, 11, 12, 120 ... 139, 172 - and the
band says only which sequence it is. Sorting is numeric rather than
lexicographic so that "10" comes before "120" rather than after it, and one
package routinely mixes both widths.
"""

from __future__ import annotations

# Sequences that could not be read sort after every real one, so a package that
# does not encode a sequence still lands in a predictable place.
NO_SEQUENCE = 10 ** 9


def is_sequence(seq: str) -> bool:
    return (seq or "").strip().isdigit()


def sort_key(seq: str) -> tuple:
    """Order sequences numerically, unreadable ones last."""
    text = (seq or "").strip()
    if text.isdigit():
        return (0, int(text), "")
    return (1, NO_SEQUENCE, text)


def label_for(seq: str) -> str:
    """The band heading for a sequence."""
    return f"SEQ {seq}" if (seq or "").strip() else "NO SEQUENCE"
