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

    # Derived from the member mark: "17172C172" -> job 17, sequence 172, zone 1.
    job_no: str = ""
    seq_group: str = ""
    zone: str = ""

    seq_source: str = SOURCE_NONE
    member_source: str = SOURCE_NONE
    revision_source: str = SOURCE_NONE

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


def _first_match(text: str, patterns: list[re.Pattern[str]]) -> str:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            value = (match.group(1) if match.groups() else match.group(0)).strip()
            if value:
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
                    if shape.match(token.upper()):
                        best_text, best_size = token.upper(), size
                        break
    return best_text


def _reject_stopword(value: str, stopwords: set[str]) -> str:
    return "" if value.upper() in stopwords else value


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
    try:
        shape = re.compile(prof.member_shape_pattern)
    except re.error:
        shape = re.compile(r"^(?=.*\d)[A-Z0-9][A-Z0-9._/\-]{1,19}$")

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
                    value = _first_match(block_text, pats)
                    source = SOURCE_TITLEBLOCK
                    if not value:
                        value = _first_match(page_text, pats)
                        source = SOURCE_PAGE
                    if label == "member":
                        value = _reject_stopword(value, stopwords)
                    if value:
                        clean = _tidy_seq(value) if label == "seq" else value.strip().upper()
                        setattr(record, attr, clean)
                        setattr(record, src_attr, source)

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
        candidate = _reject_stopword(from_name["member"].strip().upper(), stopwords)
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

    record.job_no, record.seq_group, record.zone = parse_member_mark(
        record.member_name, prof
    )

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
