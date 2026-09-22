#!/usr/bin/env python3
"""Extract Quran page geometry from custom-font PDF files.

The source PDFs use custom Quran fonts (HQPB*/Hamd*) whose decoded Unicode is
not the Quran text.  The PDF text layer nevertheless preserves a very useful
structural fact: PyMuPDF's rawdict ``line`` objects are positioned text objects
that, for these mushaf PDFs, usually correspond to one printed word or one
ayah-marker object.  This module promotes that structure to the primary visual
word evidence and leaves SQLite as the canonical Quran token sequence.

Important design rules:

* Never clip Quran content with a fixed top/bottom margin.
* Never use ``page.get_text("words")`` as the Quran word source.
* Detect bismillah and ayah markers structurally from their rawdict objects.
* Group raw visual units into physical Quran lines by Y geometry.
* Record enough diagnostics for the page-level aligner to distinguish exact
  visual counts from pages that still need a constrained correction.
* Fail early when the visible PDF qiraah does not match the requested build.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import unicodedata
from typing import Any, Iterable

import fitz


QURAN_FONT_PREFIXES = ("HQPB", "Hamd")
SPACE_FONTS = ("TimesNewRomanPSMT", "Times New Roman")
MARKER_FONT_HINTS = ("MSH-Quraan", "HQPB")
MARKER_EDGE_CHARS = ("∩", "∪")


@dataclass
class PdfLine:
    line: int
    y_center: float
    char_count: int
    extracted_word_count: int
    char_split_upper: int
    detected_marker_count: int
    marker_texts: list[str]
    excluded_reason: str | None = None
    ink_width: float = 0.0
    explicit_space_count: int = 0
    gap_scores: list[float] | None = None
    boundary_count_hint: int = 0
    bbox: tuple[float, float, float, float] | None = None
    marker_xs: list[float] | None = None
    marker_positions: list[str] | None = None
    visual_unit_count: int = 0
    visual_word_count: int = 0
    visual_marker_unit_count: int = 0
    bismillah_unit: bool = False
    visual_unit_xs: list[float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _font_is_quran(font: str | None) -> bool:
    value = str(font or "")
    return any(prefix in value for prefix in QURAN_FONT_PREFIXES)


def _font_is_space(font: str | None) -> bool:
    value = str(font or "")
    return any(prefix in value for prefix in SPACE_FONTS)


def _raw_lines(page: fitz.Page) -> Iterable[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Yield every text-line object together with its spans."""
    raw = page.get_text("rawdict")
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = [s for s in (line.get("spans") or []) if isinstance(s, dict)]
            yield line, spans


def _span_chars(span: dict[str, Any]) -> list[dict[str, Any]]:
    chars = span.get("chars") or []
    return [c for c in chars if isinstance(c, dict) and "bbox" in c]


def _text_for_span(span: dict[str, Any]) -> str:
    return "".join(str(c.get("c", "")) for c in _span_chars(span))


def _bbox(chars: list[dict[str, Any]]) -> tuple[float, float, float, float] | None:
    if not chars:
        return None
    x0 = min(float(c["bbox"][0]) for c in chars)
    y0 = min(float(c["bbox"][1]) for c in chars)
    x1 = max(float(c["bbox"][2]) for c in chars)
    y1 = max(float(c["bbox"][3]) for c in chars)
    return (x0, y0, x1, y1)


def _char_count(chars: list[dict[str, Any]], min_width: float) -> int:
    count = 0
    for char in chars:
        x0, _y0, x1, _y1 = char["bbox"]
        if float(x1) - float(x0) > min_width and str(char.get("c", "")).strip():
            count += 1
    return count


def _is_marker_text(text: str) -> bool:
    """Recognise the 3-glyph ayah-marker encoding used by these PDFs."""
    compact = "".join(ch for ch in text if not ch.isspace())
    return (
        3 <= len(compact) <= 7
        and ((compact.startswith("∩") and compact.endswith("∪"))
             or (compact.startswith("∪") and compact.endswith("∩")))
    )


def _is_bismillah_text(span: dict[str, Any]) -> bool:
    chars = _span_chars(span)
    text = _text_for_span(span).strip()
    if not chars or text != "ijk" or len(chars) != 3:
        return False
    font = str(span.get("font", ""))
    box = _bbox(chars)
    if box is None:
        return False
    return "HQPB3" in font and (box[2] - box[0]) >= 80.0


def _visual_units_from_raw_lines(
    page: fitz.Page,
    *,
    min_char_width: float,
) -> list[dict[str, Any]]:
    """Extract positioned visual word/marker units from rawdict parent lines.

    In the target mushaf PDFs, one parent rawdict line object maps to one
    printed word or one ayah marker, except that a word may share the same
    parent line object with its terminal marker.  We preserve that as one unit
    with ``word_like=True`` and ``marker_count=1``.
    """
    units: list[dict[str, Any]] = []
    for raw_line, spans in _raw_lines(page):
        q_spans: list[dict[str, Any]] = []
        marker_spans: list[dict[str, Any]] = []
        regular_chars: list[dict[str, Any]] = []

        for span in spans:
            chars = _span_chars(span)
            if not chars:
                continue
            font = str(span.get("font", ""))
            text = _text_for_span(span)

            if _font_is_quran(font):
                q_spans.append(span)
                if _is_marker_text(text):
                    marker_spans.append(span)
                else:
                    regular_chars.extend(
                        c for c in chars
                        if str(c.get("c", "")).strip()
                        and float(c["bbox"][2]) - float(c["bbox"][0]) > min_char_width
                    )

            # Markers can occasionally be exposed with MSH-Quraan rather than
            # the HQPB span family.  Keep their text for the same structural test.
            if any(hint in font for hint in MARKER_FONT_HINTS) and _is_marker_text(text):
                if span not in marker_spans:
                    marker_spans.append(span)

        if not q_spans and not marker_spans:
            continue

        all_chars: list[dict[str, Any]] = []
        for span in q_spans:
            all_chars.extend(_span_chars(span))
        if not all_chars:
            for span in marker_spans:
                all_chars.extend(_span_chars(span))
        box = _bbox(all_chars)
        if box is None:
            continue

        marker_count = len(marker_spans)
        word_like = bool(regular_chars)
        bismillah = len(q_spans) == 1 and _is_bismillah_text(q_spans[0]) and not marker_spans

        # Bismillah is a visual object but not a SQLite Quran word.  It must be
        # retained long enough for physical ordering and then explicitly skipped.
        if bismillah:
            word_like = False

        units.append({
            "y_center": (box[1] + box[3]) / 2.0,
            "x0": box[0],
            "x1": box[2],
            "bbox": box,
            "char_count": _char_count(all_chars, min_char_width),
            "word_like": bool(word_like),
            "marker_count": marker_count,
            "marker_texts": [_text_for_span(s) for s in marker_spans],
            "bismillah": bismillah,
            "font_names": sorted({str(s.get("font", "")) for s in q_spans}),
            "raw_line_bbox": raw_line.get("bbox"),
        })

    units.sort(key=lambda u: (u["y_center"], -u["x0"]))
    return units


def _cluster_visual_units(units: list[dict[str, Any]], y_tolerance: float) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    group_y: list[float] = []
    for unit in units:
        if not groups or abs(float(unit["y_center"]) - group_y[-1]) > y_tolerance:
            groups.append([unit])
            group_y.append(float(unit["y_center"]))
        else:
            groups[-1].append(unit)
            group_y[-1] = sum(float(u["y_center"]) for u in groups[-1]) / len(groups[-1])
    return groups


def _group_bbox(group: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    return (
        min(float(u["bbox"][0]) for u in group),
        min(float(u["bbox"][1]) for u in group),
        max(float(u["bbox"][2]) for u in group),
        max(float(u["bbox"][3]) for u in group),
    )


def _group_is_bismillah(group: list[dict[str, Any]], page_width: float) -> bool:
    """Detect the centred bismillah even when its glyphs are split across spans.

    Some PDFs expose the same printed bismillah as two rawdict spans (for
    example HQPB3 ``ij`` plus a Hamd2 glyph), so a single-span ``ijk`` test is
    not sufficient.  The structural signature is: no marker, exactly three
    usable Quran glyphs, a wide centred box, and no normal word-sized extent.
    """
    if not group or any(int(u["marker_count"]) for u in group):
        return False
    box = _group_bbox(group)
    total_chars = sum(int(u["char_count"]) for u in group)
    width = box[2] - box[0]
    center = (box[0] + box[2]) / 2.0
    centered = abs(center - (page_width / 2.0)) <= 28.0
    if total_chars == 3 and width >= 95.0 and centered:
        return True
    return bool(group) and all(bool(u["bismillah"]) for u in group)


def _normalize_text(text: str) -> str:
    """Normalize Arabic presentation forms before qiraah-header matching."""
    text = unicodedata.normalize("NFKC", text or "")
    return " ".join(text.split())


def detect_pdf_qiraah(pdf_path: str | bytes) -> dict[str, Any]:
    """Detect the qiraah printed in the PDF header.

    The current sources expose an ordinary text header (outside the custom
    Quran font) that survives Unicode normalization.  We inspect several pages
    rather than trusting page 1 alone.
    """
    doc = fitz.open(pdf_path)
    try:
        sample_indices = sorted({0, min(1, len(doc) - 1), len(doc) // 2, len(doc) - 1})
        samples: list[str] = []
        for idx in sample_indices:
            text = _normalize_text(doc[idx].get_text("text"))
            samples.append(text)

        haystack = "\n".join(samples)
        if "رواية قالون عن نافع" in haystack or "رواية قالون" in haystack:
            detected = "qalun"
        elif "رواية ورش عن نافع" in haystack or "رواية ورش" in haystack:
            detected = "warsh"
        elif "رواية الدوري" in haystack or "رواية الدوري عن أبي عمرو" in haystack:
            detected = "douri"
        else:
            detected = None

        return {
            "detected_qiraah": detected,
            "sample_pages": [i + 1 for i in sample_indices],
            "sample_text": samples,
        }
    finally:
        doc.close()


def validate_pdf_source(pdf_path: str, expected_qiraah: str | None) -> dict[str, Any]:
    """Validate that the PDF visibly belongs to the requested qiraah."""
    result = detect_pdf_qiraah(pdf_path)
    expected = (expected_qiraah or "").strip().lower() or None
    result["expected_qiraah"] = expected
    result["compatible"] = bool(expected and result["detected_qiraah"] == expected)
    if expected and not result["compatible"]:
        detected = result["detected_qiraah"] or "unknown"
        raise RuntimeError(
            "Source PDF/qiraah mismatch: "
            f"config expects '{expected}', but the PDF header identifies '{detected}'. "
            "Do not align a different qiraah SQLite database to this PDF."
        )
    return result


def extract_page_lines(
    page: fitz.Page,
    *,
    is_surah_start: bool,
    first_surah: int | None = None,
    top_margin: float = 0,
    bottom_margin: float = 0,
    y_tolerance: float = 5.5,
    min_char_width: float = 0.1,
    lower_word_width: float = 0.5,
    upper_gap_threshold: float = 0.05,
    gap_merge_tolerance: float = 0.75,
    explicit_space_bonus: float = 9.0,
) -> list[PdfLine]:
    del top_margin, bottom_margin, lower_word_width, upper_gap_threshold, gap_merge_tolerance, explicit_space_bonus

    units = _visual_units_from_raw_lines(page, min_char_width=min_char_width)
    groups = _cluster_visual_units(units, y_tolerance=y_tolerance)

    # Page 1 is unusual: the Fatiha bismillah is encoded as ordinary HQPB glyphs
    # rather than the dedicated ``ijk`` object used on most later surah starts.
    # On a page that starts with ayah 1 (except At-Tawbah), the first visual group
    # is therefore structural bismillah and is not a Quran word-bearing line.
    skip_first_group = bool(is_surah_start and first_surah not in (None, 9))

    output: list[PdfLine] = []
    visible_line_index = 0
    page_width = float(page.rect.width)
    for group_index, group in enumerate(groups):
        is_dedicated_bismillah = _group_is_bismillah(group, page_width)
        if is_dedicated_bismillah or (skip_first_group and group_index == 0):
            continue

        box = _group_bbox(group)
        y_center = sum(float(u["y_center"]) for u in group) / len(group)
        word_count = sum(1 for u in group if u["word_like"])
        marker_count = sum(int(u["marker_count"]) for u in group)
        char_count = sum(int(u["char_count"]) for u in group)
        word_units = [u for u in group if u["word_like"]]
        marker_xs = [
            round((float(u["x0"]) + float(u["x1"])) / 2.0, 4)
            for u in group if u["marker_count"]
        ]
        marker_positions: list[str] = []
        left_edge, _top, right_edge, _bottom = box
        width = max(1.0, right_edge - left_edge)
        left_threshold = max(16.0, width * 0.08)
        for x in marker_xs:
            from_left = float(x) - left_edge
            from_right = right_edge - float(x)
            if from_left <= left_threshold:
                marker_positions.append("end")
            elif from_right <= left_threshold:
                marker_positions.append("start")
            else:
                marker_positions.append("middle")
        visible_line_index += 1

        # The rawdict unit count is primary evidence.  Legacy gap fields are kept
        # populated for downstream compatibility, but the builder no longer treats
        # them as the source of truth.
        boundary_hint = max(0, word_count - 1)
        gap_scores = [1.0] * boundary_hint
        explicit_space_count = max(0, word_count - 1)

        output.append(
            PdfLine(
                line=visible_line_index,
                y_center=round(y_center, 3),
                char_count=char_count,
                extracted_word_count=word_count,
                char_split_upper=word_count,
                detected_marker_count=marker_count,
                marker_texts=[t for u in group for t in u["marker_texts"]],
                ink_width=round(box[2] - box[0], 3),
                explicit_space_count=explicit_space_count,
                gap_scores=gap_scores,
                boundary_count_hint=boundary_hint,
                bbox=tuple(round(v, 3) for v in box),
                marker_xs=marker_xs,
                marker_positions=marker_positions,
                visual_unit_count=len(group),
                visual_word_count=word_count,
                visual_marker_unit_count=sum(1 for u in group if u["marker_count"]),
                bismillah_unit=False,
                visual_unit_xs=[round((u["x0"] + u["x1"]) / 2.0, 4) for u in word_units],
            )
        )

    return output


def extract_document(pdf_path: str, page_meta: dict[int, dict[str, Any]], config: dict[str, Any]) -> dict[int, list[PdfLine]]:
    validate_pdf_source(pdf_path, config.get("qiraah"))

    pdf_cfg = config.get("pdf_region", {})
    tok_cfg = config.get("tokenization", {})
    doc = fitz.open(pdf_path)
    try:
        result: dict[int, list[PdfLine]] = {}
        for page_no in range(1, len(doc) + 1):
            meta = page_meta.get(page_no, {})
            result[page_no] = extract_page_lines(
                doc[page_no - 1],
                is_surah_start=bool(meta.get("is_surah_start")),
                first_surah=(int(meta["first_surah"]) if meta.get("first_surah") is not None else None),
                y_tolerance=float(tok_cfg.get("visual_unit_y_tolerance_pt", pdf_cfg.get("line_y_tolerance_pt", 5.5))),
                min_char_width=float(tok_cfg.get("min_char_width_pt", 0.1)),
            )
        return result
    finally:
        doc.close()
