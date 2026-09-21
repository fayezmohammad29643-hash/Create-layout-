#!/usr/bin/env python3
"""Extract page/line geometry from the Douri PDF without decoding its custom font text.

The PDF's textual Unicode is intentionally treated as untrusted.  We use only:
- page/line Y geometry;
- positive-width glyph counts;
- rough word-object counts as a lower bound;
- visible ayah-marker patterns when extraction exposes them.

The actual Quran words come from SQLite in build_layout.py.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import fitz


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_quran_char(char: dict[str, Any], page_height: float, top_margin: float, bottom_margin: float, min_width: float) -> bool:
    text = char.get("c", "")
    if not text or not text.strip():
        return False
    x0, y0, x1, y1 = char["bbox"]
    if y0 <= top_margin or y1 >= page_height - bottom_margin:
        return False
    if (x1 - x0) <= min_width:
        return False
    return True


def _group_chars(chars: list[dict[str, Any]], y_tolerance: float) -> list[tuple[float, list[dict[str, Any]]]]:
    ordered = sorted(chars, key=lambda c: ((c["bbox"][1] + c["bbox"][3]) / 2, c["bbox"][0]))
    groups: list[tuple[float, list[dict[str, Any]]]] = []
    for char in ordered:
        x0, y0, x1, y1 = char["bbox"]
        y_center = (y0 + y1) / 2
        if not groups or y_center - groups[-1][0] > y_tolerance:
            groups.append((y_center, [char]))
        else:
            groups[-1][1].append(char)
    return groups


def _extract_marker_words(page: fitz.Page, top_margin: float, bottom_margin: float) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    for item in page.get_text("words", sort=False):
        x0, y0, x1, y1, text, *_ = item
        if y0 <= top_margin or y1 >= page.rect.height - bottom_margin:
            continue
        if "∩" in text and "∪" in text:
            markers.append({"bbox": (x0, y0, x1, y1), "text": text})
    return markers


def extract_page_lines(
    page: fitz.Page,
    *,
    is_surah_start: bool,
    top_margin: float = 115,
    bottom_margin: float = 115,
    y_tolerance: float = 4,
    min_char_width: float = 0.1,
    lower_word_width: float = 0.5,
    upper_gap_threshold: float = 0.05,
) -> list[PdfLine]:
    raw = page.get_text("rawdict")
    chars: list[dict[str, Any]] = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for char in span.get("chars", []):
                    if _is_quran_char(char, page.rect.height, top_margin, bottom_margin, min_char_width):
                        chars.append(char)

    groups = _group_chars(chars, y_tolerance)
    all_words = [
        w for w in page.get_text("words", sort=False)
        if w[1] > top_margin
        and w[3] < page.rect.height - bottom_margin
    ]
    marker_words = [
        {"bbox": (w[0], w[1], w[2], w[3]), "text": w[4]}
        for w in all_words
        if "∩" in w[4] and "∪" in w[4]
    ]

    # The first Quran-area line is Bismillah on a surah-start page in this PDF.
    # Later surah starts are commonly represented by the 3-glyph centered "ijk"
    # object from the PDF's custom-font encoding; HQPB3 is another reliable cue.
    output: list[PdfLine] = []
    visible_line_index = 0

    for group_index, (y_center, group_chars) in enumerate(groups):
        sorted_chars = sorted(group_chars, key=lambda c: c["bbox"][0])
        extracted_words = [
            w for w in all_words
            if (w[2] - w[0]) > lower_word_width
            and abs(((w[1] + w[3]) / 2) - y_center) <= 3
        ]

        word_texts = [w[4] for w in extracted_words]
        all_same_hqpb3 = bool(group_chars) and len(group_chars) == 3 and all("HQPB3" in str(c.get("font", "")) for c in group_chars)
        first_is_bismillah = is_surah_start and group_index == 0
        explicit_bismillah = len(word_texts) == 1 and word_texts[0].strip() == "ijk"

        if first_is_bismillah or explicit_bismillah or all_same_hqpb3:
            continue

        visible_line_index += 1
        gaps = []
        for left, right in zip(sorted_chars, sorted_chars[1:]):
            lx0, ly0, lx1, ly1 = left["bbox"]
            rx0, ry0, rx1, ry1 = right["bbox"]
            gaps.append(max(0.0, rx0 - lx1))
        char_split_upper = 1 + sum(gap > upper_gap_threshold for gap in gaps)

        detected_markers = [
            m for m in marker_words if abs(((m["bbox"][1] + m["bbox"][3]) / 2) - y_center) <= 3
        ]

        output.append(
            PdfLine(
                line=visible_line_index,
                y_center=round(y_center, 3),
                char_count=len(sorted_chars),
                extracted_word_count=len(extracted_words),
                char_split_upper=char_split_upper,
                detected_marker_count=len(detected_markers),
                marker_texts=[m["text"] for m in detected_markers],
            )
        )

    return output


def extract_document(pdf_path: str, page_meta: dict[int, dict[str, Any]], config: dict[str, Any]) -> dict[int, list[PdfLine]]:
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
                top_margin=float(pdf_cfg.get("top_margin_pt", 115)),
                bottom_margin=float(pdf_cfg.get("bottom_margin_pt", 115)),
                y_tolerance=float(pdf_cfg.get("line_y_tolerance_pt", 4)),
                min_char_width=float(tok_cfg.get("min_char_width_pt", 0.1)),
                lower_word_width=float(tok_cfg.get("lower_word_width_pt", 0.5)),
                upper_gap_threshold=float(tok_cfg.get("upper_gap_threshold_pt", 0.05)),
            )
        return result
    finally:
        doc.close()
