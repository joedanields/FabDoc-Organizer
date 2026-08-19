"""Pull sequence number, member name and revision out of a drawing PDF.

Extraction runs as a cascade, most trustworthy source first:

1. Labelled fields inside the title block region (bottom-right of the sheet).
2. Labelled fields anywhere on the page.
3. The largest mark-shaped text in the title block, unlabelled - this is how
   most detailing templates present the assembly mark.
4. The filename.

Every record records which tier produced each field, so a register can be
audited at a glance instead of trusted blindly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

try:
    import fitz  # PyMuPDF
except ImportError as exc:  # pragma: no cover - environment guard
    raise ImportError(
        "PyMuPDF is required. Install it with:  pip install PyMuPDF"
    ) from exc

from .config import ExtractionProfile

# Confidence tiers, ordered worst to best for reporting.
SOURCE_NONE = "none"
SOURCE_FILENAME = "filename"
SOURCE_LARGEST = "title-block text"
SOURCE_PAGE = "page label"
SOURCE_TITLEBLOCK = "title-block label"


@dataclass
class DrawingRecord:
    """One row of the drawing register."""

    source_file: str = ""
    source_path: str = ""
    category: str = ""

    seq_no: str = ""
    member_name: str = ""
    revision: str = ""

    # What the shop is told to make: how many, and how long to cut them.
    # Length is kept as the sheet draws it (8'-7 15/16") and again in inches,
    # because a string cannot answer "by how much did it change".
    quantity: str = ""
    length: str = ""
    length_inches: float | None = None
    # The rest of that row: what the piece is cut from, and what it comes to.
    profile: str = ""
    material: str = ""
    weight: str = ""
    weight_value: float | None = None

    # Derived from the member mark: "17172C172" -> job 17, sequence 172, zone 1.
    job_no: str = ""
    seq_group: str = ""
    zone: str = ""
    # A single part carries a type instead of a sequence: "17ch104" -> CH.
    type_code: str = ""

    seq_source: str = SOURCE_NONE
    member_source: str = SOURCE_NONE
    revision_source: str = SOURCE_NONE
    quantity_source: str = SOURCE_NONE
    length_source: str = SOURCE_NONE
    profile_source: str = SOURCE_NONE
    material_source: str = SOURCE_NONE
    weight_source: str = SOURCE_NONE

    page_count: int = 0
    notes: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        """True when the row carries a member name and no hard error."""
        return bool(self.member_name) and not self.error

    @property
    def needs_review(self) -> bool:
        """True when a human should look at this row before trusting it."""
        return (
            bool(self.error)
            or not self.member_name
            or self.member_source in (SOURCE_NONE, SOURCE_FILENAME)
        )

    @property
    def band(self) -> tuple[str, str]:
        """Which band this row belongs under: ``("seq", "172")``, ``("type", "CH")``.

        An assembly is grouped by the sequence it is erected in. A single part
        has no sequence - it is cut for an assembly - so it is grouped by its
        type instead. Rows with neither fall in a band of their own.
        """
        if self.seq_group:
            return ("seq", self.seq_group)
        if self.type_code:
            return ("type", self.type_code)
        return ("", "")

    @property
    def note_text(self) -> str:
        return "; ".join(self.notes)


# ---------------------------------------------------------------------------
# Low-level text helpers
# ---------------------------------------------------------------------------


def _compile(patterns: list[str]) -> list[re.Pattern[str]]:
    out: list[re.Pattern[str]] = []
    for p in patterns:
        try:
            out.append(re.compile(p, re.IGNORECASE))
        except re.error:
            # A malformed user-supplied pattern must not sink the whole run.
            continue
    return out


def _first_match(text: str, patterns: list[re.Pattern[str]],
                 accept: "Callable[[str], bool] | None" = None) -> str:
    """The first captured value, skipping any the caller will not accept.

    Without ``accept`` the first match wins outright, which is what let a label
    pattern matching a table heading claim the row. Skipping a rejected capture
    and trying the next pattern is what lets a better tier answer instead.
    """
    for pattern in patterns:
        for match in pattern.finditer(text):
            value = (match.group(1) if match.groups() else match.group(0)).strip()
            if value and (accept is None or accept(value)):
                return value
    return ""


def _clip_rect(page: "fitz.Page", frac: tuple[float, float, float, float]) -> "fitz.Rect":
    r = page.rect
    x0 = r.x0 + r.width * frac[0]
    y0 = r.y0 + r.height * frac[1]
    x1 = r.x0 + r.width * frac[2]
    y1 = r.y0 + r.height * frac[3]
    return fitz.Rect(x0, y0, x1, y1)


def _largest_mark(
    page: "fitz.Page",
    clip: "fitz.Rect",
    shape: re.Pattern[str],
    stopwords: set[str],
) -> str:
    """Biggest mark-shaped span inside ``clip``, or "" if there is none."""
    try:
        data = page.get_text("dict", clip=clip)
    except Exception:
        return ""

    best_text, best_size = "", 0.0
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                raw = (span.get("text") or "").strip()
                size = float(span.get("size") or 0.0)
                if not raw or size <= best_size:
                    continue
                # A span can hold several words; test each candidate token.
                for token in re.split(r"\s+", raw):
                    token = token.strip(" .,:;()[]")
                    if not token or token.upper() in stopwords:
                        continue
                    # Tested upper, stored as drawn: single-part marks are
                    # lower case on the sheet ("17ch104") and the register has
                    # to read back the way the detailer wrote them.
                    if shape.match(token.upper()):
                        best_text, best_size = token, size
                        break
    return best_text


def _reject_stopword(value: str, stopwords: set[str]) -> str:
    return "" if value.upper() in stopwords else value


def _is_mark(value: str, shape: re.Pattern[str], stopwords: set[str],
             rejects: list[re.Pattern[str]]) -> bool:
    """Could this token be a member mark at all?

    The largest-text tier has always applied the shape test; the labelled tiers
    did not, so a capture like "HSS4X4X1/2" off a material column, or the word
    "CENTERLINE" off a dimension note, was taken as the mark and no later tier
    ever ran.
    """
    text = value.strip().upper()
    if not text or text in stopwords or not shape.match(text):
        return False
    return not any(r.search(text) for r in rejects)


# ---------------------------------------------------------------------------
# Quantity and cut length
# ---------------------------------------------------------------------------

# 8'-7 15/16"  /  3'-11 5/8"  /  4'-0"
_FEET_INCHES = re.compile(
    r"^(\d+)\s*'\s*-?\s*(\d+)?(?:\s+(\d+)\s*/\s*(\d+))?\s*\"?$"
)
# A bare length in inches, with or without a fraction: 29.00, 12 3/4"
_INCHES_ONLY = re.compile(r"^(\d+(?:\.\d+)?)?(?:\s*(\d+)\s*/\s*(\d+))?\s*\"?$")

_QTY_SHAPE = re.compile(r"^\d{1,5}$")
# "177.41 lbs", "1.66 lbs", "80.5 kg", "14.72", and "1085.70***" - the marker a
# detailing package puts on an estimated weight, which is still a weight.
_WEIGHT_SHAPE = re.compile(
    r"^(\d+(?:\.\d+)?)\s*(?:lbs?|kgs?|pounds?)?\s*[*~+]*$", re.IGNORECASE)


def parse_weight(text: str) -> float | None:
    """The number out of a weight cell, or None when it is not one.

    The unit stays on the string as the sheet writes it and is dropped here:
    two issues of the same drawing state the same unit, and what is being asked
    is whether the number moved.
    """
    match = _WEIGHT_SHAPE.match((text or "").strip())
    return float(match.group(1)) if match else None


def _is_weight(value: str) -> bool:
    return parse_weight(value) is not None


def _spec_headings(profile: ExtractionProfile) -> set[str]:
    """Every heading in the title block table, lower-cased.

    A value is read as the cell under a heading, so a heading drawn under
    another heading must not be taken for one - that is the whole of how a
    column with an empty cell would otherwise borrow the row below it.
    """
    return {(label or "").strip().lower().rstrip(".")
            for labels in (profile.quantity_labels, profile.length_labels,
                           profile.profile_labels, profile.material_labels,
                           profile.weight_labels)
            for label in labels if label.strip()}


def _is_spec_text(value: str, headings: set[str]) -> bool:
    """Could this be a profile or a material?

    Neither has a shape worth testing - "L3X3X3/16", "PIPE1-1/4SCH40", "A36"
    and "A500-GR.C" have nothing in common - so the column position does the
    work and this only rejects what is obviously not a value.
    """
    text = (value or "").strip()
    return bool(text) and text.lower().rstrip(".") not in headings


def reads_spec(category: str, profile: ExtractionProfile) -> bool:
    """Does a drawing in this category state a part's five values?

    A part is one piece cut to one length from one profile in one material at
    one weight, and its title block says so. An assembly's says something else
    under the same headings, so assemblies are skipped by name - see
    ExtractionProfile.non_spec_categories, which is a list of what to skip
    rather than what to read, so a folder of part drawings under any name at
    all is still read as what it plainly is.
    """
    skip = {c.strip().lower() for c in profile.non_spec_categories if c.strip()}
    return (category or "").strip().lower() not in skip


def parse_length_inches(text: str) -> float | None:
    """A cut length in inches, or None when it is not a length at all.

    The sheet writes it the way the shop reads it - 8'-7 15/16" - which sorts
    and subtracts like a string, which is to say not at all. Inches are what
    makes "did this get longer, and by how much" answerable.
    """
    value = (text or "").strip().replace("’", "'").replace("”", '"')
    if not value:
        return None
    match = _FEET_INCHES.match(value)
    if match:
        feet, inches, num, den = match.groups()
        total = int(feet) * 12.0 + float(inches or 0)
        if num and den and int(den):
            total += int(num) / int(den)
        return total
    match = _INCHES_ONLY.match(value)
    if match and any(match.groups()):
        whole, num, den = match.groups()
        total = float(whole or 0)
        if num and den and int(den):
            total += int(num) / int(den)
        return total
    return None


def _is_length(value: str) -> bool:
    return parse_length_inches(value) is not None


def _spans_in(page: "fitz.Page", band: "fitz.Rect") -> list[dict]:
    """Every non-empty text span inside ``band``, in reading order."""
    try:
        data = page.get_text("dict", clip=band)
    except Exception:
        return []
    out: list[dict] = []
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = (span.get("text") or "").strip()
                if not text:
                    continue
                x0, y0, x1, y1 = span.get("bbox", (0, 0, 0, 0))
                out.append({"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1})
    out.sort(key=lambda s: (round(s["y0"], 1), s["x0"]))
    return out


def _cell_under(spans: list[dict], labels: list[str],
                accept: "Callable[[str], bool]") -> str:
    """The value written in the cell below a heading.

    The title block of a fabrication drawing is a table: Qty, Profile,
    Material, Length, Weight as headings, and the values on the row beneath.
    Read as flowing text the two rows interleave by column, so "Qty" is
    followed by "Profile" and no same-line pattern can reach the number. The
    cell is found by position instead - directly below the heading, left edges
    aligned - which is also what stops the value of the next column over being
    picked up.

    Deliberately below only, never above. The same sheet carries a second Qty
    heading with its value above it, in the "Qty / In Assembly" table, and that
    is how many of the part go into one assembly - not how many to make.
    """
    wanted = {(l or "").strip().lower().rstrip(".") for l in labels if l.strip()}
    for label in spans:
        if label["text"].strip().lower().rstrip(".") not in wanted:
            continue
        # One table row down: far enough for the row beneath, not so far that
        # the next band of the title block is in range.
        drop = max(6.0, (label["y1"] - label["y0"]) * 3.0)
        below = [s for s in spans
                 if label["y1"] - 1.0 <= s["y0"] <= label["y1"] + drop
                 and abs(s["x0"] - label["x0"]) <= _COLUMN_TOLERANCE]
        for candidate in sorted(below, key=lambda s: s["y0"]):
            if accept(candidate["text"]):
                return candidate["text"]
    return ""


# How far a value may sit from its heading's left edge and still be the same
# column. The templates seen align them to a third of a point; the slack is for
# a value drawn centred in a narrow cell.
_COLUMN_TOLERANCE = 8.0


def parse_member_mark(mark: str, profile: ExtractionProfile | None = None
                      ) -> tuple[str, str, str]:
    """Split a member mark into ``(job, sequence, zone)``.

    Detailers commonly encode the erection sequence into the mark itself, so
    "17172C172" is job 17, sequence 172, and - taking the leading digit of the
    sequence - zone 1. Returns empty strings when the mark does not fit the
    configured shape, which is not an error: plenty of projects do not do this.
    """
    prof = profile or ExtractionProfile()
    if not mark or not prof.member_seq_pattern:
        return "", "", ""
    try:
        match = re.match(prof.member_seq_pattern, mark.strip(), re.IGNORECASE)
    except re.error:
        return "", "", ""
    if not match:
        return "", "", ""

    groups = match.groupdict()
    job = (groups.get("job") or "").strip()
    seq = (groups.get("seq") or "").strip()

    zone = ""
    if seq and prof.derive_zone_from_seq:
        digits = max(1, prof.zone_seq_digits)
        zone = seq[:digits].lstrip("0") or seq[:digits]
    return job, seq, zone


def parse_part_type(mark: str, profile: ExtractionProfile | None = None) -> str:
    """The type code of a single-part mark: "17ch104" -> "CH".

    Empty when the mark does not fit the shape, which includes every mark that
    encodes a sequence instead - the two are mutually exclusive by construction.
    """
    prof = profile or ExtractionProfile()
    if not mark or not prof.part_type_pattern:
        return ""
    try:
        match = re.match(prof.part_type_pattern, mark.strip(), re.IGNORECASE)
    except re.error:
        return ""
    return (match.group("type").upper() if match else "")


def band_for_mark(mark: str, profile: ExtractionProfile | None = None
                  ) -> tuple[str, str]:
    """The band a mark belongs to: ``("seq", "172")`` or ``("type", "CH")``.

    The same rule DrawingRecord.band applies, reached from the mark alone so the
    tracker can band a chain saved before bands existed. Nothing is stored in
    the chain state that the mark does not already carry.
    """
    prof = profile or ExtractionProfile()
    _job, seq, _zone = parse_member_mark(mark, prof)
    if seq:
        return ("seq", seq)
    type_code = parse_part_type(mark, prof)
    if type_code:
        return ("type", type_code)
    return ("", "")


def _tidy_seq(value: str) -> str:
    """Normalise a sequence number so "012" and "12" sort and compare alike."""
    text = value.strip()
    return str(int(text)) if text.isdigit() else text


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------


def parse_filename(stem: str, profile: ExtractionProfile) -> dict[str, str]:
    """Best-effort field recovery from a file stem."""
    for pattern in _compile(profile.filename_patterns):
        match = pattern.match(stem.strip())
        if not match:
            continue
        groups = match.groupdict()
        found = {k: (v or "").strip() for k, v in groups.items() if v}
        if found:
            return found
    return {}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def extract_drawing(
    pdf_path: str | Path,
    profile: ExtractionProfile | None = None,
    category: str = "",
    fallback_seq: int | None = None,
) -> DrawingRecord:
    """Extract register fields from a single drawing PDF.

    ``fallback_seq`` is used as the sequence number when the drawing carries no
    recoverable one, so that rows still order deterministically.
    """
    prof = profile or ExtractionProfile()
    path = Path(pdf_path)
    record = DrawingRecord(
        source_file=path.name,
        source_path=str(path),
        category=category,
    )

    member_pats = _compile(prof.member_patterns)
    rev_pats = _compile(prof.revision_patterns)
    seq_pats = _compile(prof.sequence_patterns)
    stopwords = {w.upper() for w in prof.member_stopwords}
    rejects = _compile(prof.member_reject_patterns)
    try:
        shape = re.compile(prof.member_shape_pattern)
    except re.error:
        shape = re.compile(r"^(?=.*\d)[A-Z0-9][A-Z0-9._/\-]{1,19}$")

    def acceptable(value: str) -> bool:
        return _is_mark(value, shape, stopwords, rejects)

    page_text, block_text = "", ""
    try:
        with fitz.open(path) as doc:
            record.page_count = doc.page_count
            if doc.page_count == 0:
                record.error = "PDF has no pages"
            else:
                index = prof.title_block_page
                if index < 0:
                    index = max(0, doc.page_count + index)
                index = min(index, doc.page_count - 1)
                page = doc[index]
                page_text = page.get_text("text") or ""
                clip = _clip_rect(page, prof.title_block_rect)
                block_text = page.get_text("text", clip=clip) or ""

                if not page_text.strip():
                    record.notes.append("no extractable text (scanned image?)")

                # Tier 1 + 2: labelled fields, title block before whole page.
                for label, pats, attr, src_attr in (
                    ("member", member_pats, "member_name", "member_source"),
                    ("revision", rev_pats, "revision", "revision_source"),
                    ("seq", seq_pats, "seq_no", "seq_source"),
                ):
                    accept = acceptable if label == "member" else None
                    value = _first_match(block_text, pats, accept)
                    source = SOURCE_TITLEBLOCK
                    if not value:
                        value = _first_match(page_text, pats, accept)
                        source = SOURCE_PAGE
                    if value:
                        if label == "seq":
                            clean = _tidy_seq(value)
                        elif label == "member":
                            # The mark is an identifier, not a heading - it is
                            # recorded exactly as the drawing carries it.
                            clean = value.strip()
                        else:
                            clean = value.strip().upper()
                        setattr(record, attr, clean)
                        setattr(record, src_attr, source)

                # The five values a part's title block states, off anything
                # that is not an assembly - see reads_spec. They are written as
                # a table, so the cell under each heading is read first and the
                # inline patterns are only the fallback.
                # The band is the full width of the sheet at the title block's
                # height: the Qty column sits left of the block's own left
                # edge on a landscape sheet, outside the clip everything else
                # is read from.
                band = fitz.Rect(page.rect.x0, clip.y0, page.rect.x1, page.rect.y1)
                spans = _spans_in(page, band) if reads_spec(category, prof) else []

                headings = _spec_headings(prof)
                text_value = lambda v: _is_spec_text(v, headings)   # noqa: E731
                for labels, attr, src_attr, accept in (
                    (prof.quantity_labels, "quantity", "quantity_source",
                     lambda v: bool(_QTY_SHAPE.match(v.strip()))),
                    (prof.length_labels, "length", "length_source", _is_length),
                    (prof.profile_labels, "profile", "profile_source", text_value),
                    (prof.material_labels, "material", "material_source", text_value),
                    (prof.weight_labels, "weight", "weight_source", _is_weight),
                ):
                    found = _cell_under(spans, labels, accept)
                    if found:
                        setattr(record, attr, found.strip())
                        setattr(record, src_attr, SOURCE_TITLEBLOCK)

                # Templates that write them inline ("QTY: 3") instead of as a
                # table. Only the two that have patterns: a profile or a
                # material found by a same-line pattern would as likely be the
                # cell beside it, and a wrong one is worse than none.
                if reads_spec(category, prof):
                    for patterns, attr, src_attr, accept in (
                        (prof.quantity_patterns, "quantity", "quantity_source", None),
                        (prof.length_patterns, "length", "length_source", _is_length),
                    ):
                        if getattr(record, attr):
                            continue
                        for text, source in ((block_text, SOURCE_TITLEBLOCK),
                                             (page_text, SOURCE_PAGE)):
                            found = _first_match(text, _compile(patterns), accept)
                            if found:
                                setattr(record, attr, found.strip())
                                setattr(record, src_attr, source)
                                break

                # Tier 3: unlabelled mark, largest text in the title block.
                if not record.member_name and prof.use_largest_text_fallback:
                    candidate = _largest_mark(page, clip, shape, stopwords)
                    if candidate:
                        record.member_name = candidate
                        record.member_source = SOURCE_LARGEST
    except Exception as exc:  # a corrupt file must not stop the batch
        record.error = f"{type(exc).__name__}: {exc}"

    # Tier 4: the filename.
    from_name = parse_filename(path.stem, prof)
    if not record.member_name and from_name.get("member"):
        candidate = _reject_stopword(from_name["member"].strip(), stopwords)
        if candidate:
            record.member_name = candidate
            record.member_source = SOURCE_FILENAME
    if not record.revision and from_name.get("rev"):
        record.revision = from_name["rev"].strip().upper()
        record.revision_source = SOURCE_FILENAME
    if not record.seq_no and from_name.get("seq"):
        record.seq_no = _tidy_seq(from_name["seq"])
        record.seq_source = SOURCE_FILENAME

    if not record.seq_no and fallback_seq is not None:
        record.seq_no = str(fallback_seq)
        record.seq_source = SOURCE_NONE
        record.notes.append("sequence assigned by file order")

    if not record.revision:
        record.revision = prof.default_revision
        record.revision_source = SOURCE_NONE
        record.notes.append("revision defaulted")

    if not record.member_name and not record.error:
        record.notes.append("member name not found")

    # The file name is the authority on case. A mark is an identifier, and the
    # detailer types it into the file name the way it is meant to read -
    # "17HSP134" and "17hsp134" are the same piece, but the register is worked
    # from on a shop floor and it has to say what the file says. Only the
    # spelling is taken, and only when the two are the same mark: a filename
    # that disagrees about which mark this is stays a fallback, not an override.
    for candidate in (from_name.get("member"), path.stem.strip()):
        candidate = (candidate or "").strip()
        if candidate and candidate.lower() == record.member_name.lower():
            record.member_name = candidate
            break

    record.length_inches = parse_length_inches(record.length)
    record.weight_value = parse_weight(record.weight)

    record.job_no, record.seq_group, record.zone = parse_member_mark(
        record.member_name, prof
    )
    # Only when the mark carries no sequence: an assembly is grouped by the
    # sequence it is erected in, a single part by the type it is cut as.
    if not record.seq_group:
        record.type_code = parse_part_type(record.member_name, prof)

    return record


def dump_text(pdf_path: str | Path, page_index: int = 0) -> dict[str, object]:
    """Return a PDF's text for calibrating patterns against a real drawing.

    Used by the Extraction Settings tab and ``--calibrate`` to show exactly what
    the parser sees, so patterns can be fitted without guesswork.
    """
    path = Path(pdf_path)
    out: dict[str, object] = {"file": path.name, "pages": 0, "text": "", "spans": []}
    with fitz.open(path) as doc:
        out["pages"] = doc.page_count
        if doc.page_count == 0:
            return out
        index = max(0, min(page_index if page_index >= 0 else doc.page_count + page_index,
                           doc.page_count - 1))
        page = doc[index]
        out["text"] = page.get_text("text") or ""
        out["page_size"] = (page.rect.width, page.rect.height)
        spans: list[tuple[float, str, tuple[float, float]]] = []
        data = page.get_text("dict")
        for block in data.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = (span.get("text") or "").strip()
                    if text:
                        bbox = span.get("bbox", (0, 0, 0, 0))
                        spans.append((
                            round(float(span.get("size") or 0), 1),
                            text,
                            (round(bbox[0] / page.rect.width, 3),
                             round(bbox[1] / page.rect.height, 3)),
                        ))
        spans.sort(key=lambda s: -s[0])
        out["spans"] = spans[:60]
    return out
