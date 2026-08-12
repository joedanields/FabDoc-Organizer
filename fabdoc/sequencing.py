"""Where a drawing sits in the erection sequence.

A member mark carries the sequence in it: "17172C172" is job 17, sequence 172.
The sequence is itself two fields - the leading digit is the zone, and the last
two digits say what kind of steel it is:

    172  ->  zone 1,  code 72
    270  ->  zone 2,  code 70

The code groups by its tens digit, which is how a detailer reads it: everything
in the 10s is perimeter steel, the 30s are roof, the 70s are miscellaneous and
stair steel. That decade rule is what lets an unlisted code land somewhere
sensible - 72 and 73 are not written down anywhere, but they are plainly 70s
work and belong beside 70 and 71.

The order those groups appear in is **not** numeric. Mezzanine (20s) is erected
after roof (30s), so it is listed after it, and sorting the sequence numbers
would put the register in the wrong order. Hence a table rather than a sort.
"""

from __future__ import annotations

# Priority order of the steel types, by the tens digit of the sequence code.
# Position in this list is the order they appear in the register; the number is
# the decade it covers, so 3 matches codes 30-39.
DEFAULT_SEQUENCE_GROUPS: list[list] = [
    [1, "Perimeter Steel"],       # 10, 11 - anchor bolts, slab embeds
    [3, "Roof Steel"],            # 30, 31
    [2, "Mezzanine Steel"],       # 21, 22
    [5, "Elevator Steel"],        # 50
    [7, "Misc. / Stair Steel"],   # 70, 71 - interior and exterior misc.
]

# A decade with no entry above still has to go somewhere. It sorts after every
# named group, in numeric order, and is left unnamed rather than guessed at.
UNKNOWN_RANK = 10_000


def sequence_code(seq: str) -> str:
    """The content code inside a sequence number.

    The last two digits, because the leading digit is the zone: 172 -> "72".
    A two-digit sequence has no zone prefix to strip, so it is the code
    already - one package mixes both widths, and "10" is perimeter steel in
    the same register where "130" is roof steel.
    """
    text = (seq or "").strip()
    if not text.isdigit():
        return ""
    return text[-2:] if len(text) > 2 else text


def _decade(code: str) -> int:
    return int(code) // 10 if code.isdigit() else -1


def group_for(seq: str, groups: list[list] | None = None) -> tuple[int, str]:
    """The steel type a sequence belongs to, as ``(rank, name)``.

    ``rank`` orders the groups in the register. An unrecognised decade gets
    ``UNKNOWN_RANK`` and an empty name, so it lands after everything named
    without being labelled as something it might not be.
    """
    table = groups if groups is not None else DEFAULT_SEQUENCE_GROUPS
    code = sequence_code(seq)
    if not code:
        return UNKNOWN_RANK, ""
    decade = _decade(code)
    for rank, entry in enumerate(table):
        try:
            if int(entry[0]) == decade:
                return rank, str(entry[1])
        except (TypeError, ValueError, IndexError):
            continue          # a malformed row in a hand-edited settings file
    return UNKNOWN_RANK, ""


def sort_key(seq: str, groups: list[list] | None = None) -> tuple:
    """Order sequences by steel type, then by the code inside that type.

    Within a group the codes run in their own order - 70 before 71 before 72 -
    which is both numeric and the order they are erected in.
    """
    rank, _ = group_for(seq, groups)
    code = sequence_code(seq)
    return (rank, int(code) if code.isdigit() else 0, seq or "")


def label_for(seq: str, groups: list[list] | None = None) -> str:
    """The band heading for a sequence: "SEQ 172 - Misc. / Stair Steel"."""
    _, name = group_for(seq, groups)
    head = f"SEQ {seq}" if seq else "NO SEQUENCE"
    return f"{head}   -   {name}" if name else head
