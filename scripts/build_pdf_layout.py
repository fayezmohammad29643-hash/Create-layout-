#!/usr/bin/env python3
"""Build schema-v3 Quran layout from PDF geometry + SQLite text.

The PDF supplies the physical line structure and word-boundary evidence. SQLite
supplies the canonical Quran token sequence. A page-level constrained DP selects
how many *words* belong to every physical PDF line while preserving exact page
word order and automatically carrying ayah-marker tokens to the line containing
 their terminal word.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

from pdf_layout import PdfLine, extract_document, validate_pdf_source

SURAH_NAMES = [
    "الفاتحة","البقرة","آل عمران","النساء","المائدة","الأنعام","الأعراف","الأنفال","التوبة","يونس",
    "هود","يوسف","الرعد","إبراهيم","الحجر","النحل","الإسراء","الكهف","مريم","طه",
    "الأنبياء","الحج","المؤمنون","النور","الفرقان","الشعراء","النمل","القصص","العنكبوت","الروم",
    "لقمان","السجدة","الأحزاب","سبأ","فاطر","يس","الصافات","ص","الزمر","غافر",
    "فصلت","الشورى","الزخرف","الدخان","الجاثية","الأحقاف","محمد","الفتح","الحجرات","ق",
    "الذاريات","الطور","النجم","القمر","الرحمن","الواقعة","الحديد","المجادلة","الحشر","الممتحنة",
    "الصف","الجمعة","المنافقون","التغابن","الطلاق","التحريم","الملك","القلم","الحاقة","المعارج",
    "نوح","الجن","المزمل","المدثر","القيامة","الإنسان","المرسلات","النبأ","النازعات","عبس",
    "التكوير","الانفطار","المطففين","الانشقاق","البروج","الطارق","الأعلى","الغاشية","الفجر","البلد",
    "الشمس","الليل","الضحى","الشرح","التين","العلق","القدر","البينة","الزلزلة","العاديات",
    "القارعة","التكاثر","العصر","الهمزة","الفيل","قريش","الماعون","الكوثر","الكافرون","النصر",
    "المسد","الإخلاص","الفلق","الناس",
]


def words_in_ayah(text: str) -> list[str]:
    words: list[str] = []
    for word in re.split(r"\s+", (text or "").replace("\xa0", " ").strip()):
        if not word:
            continue
        word = word.replace("۞", "").strip()
        if word:
            words.append(word)
    return words


def word_feature(word: str) -> int:
    return max(1, sum(1 for ch in word if unicodedata.category(ch)[0] in ("L", "N")))


def load_sqlite(db_path: Path) -> dict[int, list[dict[str, Any]]]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT surah, ayah, text, page FROM ayat ORDER BY page, surah, ayah"
        ).fetchall()
    finally:
        con.close()

    by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for surah, ayah, text, page in rows:
        by_page[int(page)].append({"surah": int(surah), "ayah": int(ayah), "text": text or ""})
    return dict(by_page)


def build_page_tokens(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    for row in rows:
        words = words_in_ayah(row["text"])
        for idx, word in enumerate(words, start=1):
            tokens.append({
                "type": "word",
                "surah": row["surah"],
                "ayah": row["ayah"],
                "word": word,
                "wordIndex": idx,
                "feature": word_feature(word),
            })
        tokens.append({
            "type": "ayah_marker",
            "surah": row["surah"],
            "ayah": row["ayah"],
            "number": row["ayah"],
            "feature": 1,
        })
    return tokens


def _word_positions(tokens: list[dict[str, Any]]) -> list[int]:
    return [i for i, t in enumerate(tokens) if t["type"] == "word"]


def _span_for_word_ordinal(
    tokens: list[dict[str, Any]],
    word_positions: list[int],
    start_word_ordinal: int,
    word_count: int,
) -> tuple[int, int, int]:
    """Return token [start,end), and marker count, consuming trailing markers.

    A physical line is a sequence of Quran words. Any ayah-marker token directly
    following its final word belongs to that same printed line and is consumed as
    well. Markers between words are naturally inside the interval.
    """
    start_token = word_positions[start_word_ordinal]
    final_word_position = word_positions[start_word_ordinal + word_count - 1]
    end_token = final_word_position + 1
    while end_token < len(tokens) and tokens[end_token]["type"] == "ayah_marker":
        end_token += 1
    marker_count = sum(1 for t in tokens[start_token:end_token] if t["type"] == "ayah_marker")
    return start_token, end_token, marker_count


def _marker_terminal_geometry(line: PdfLine) -> bool | None:
    """Infer whether the left-most marker sits at the visual line terminus."""
    if not line.marker_xs or not line.bbox:
        return None
    left_marker = min(float(x) for x in line.marker_xs)
    left_edge = float(line.bbox[0])
    width = max(1.0, float(line.bbox[2]) - left_edge)
    return (left_marker - left_edge) <= max(16.0, width * 0.08)


def _marker_position_counts(lines: list[PdfLine]) -> list[dict[str, int]]:
    """Convert visual marker positions into semantic per-line marker targets.

    A marker at the right edge of an Arabic line is printed before that line's
    first word and visually marks the ayah that ended on the previous line.  A
    marker at the left edge follows the last word of the same line.  Middle
    markers belong to the current line.  This keeps marker evidence useful
    without falsely demanding that every marker be physically inside the same
    raw line object as its semantic endpoint.
    """
    own: list[dict[str, int]] = []
    for line in lines:
        positions = list(line.marker_positions or [])
        own.append({
            "start": sum(p == "start" for p in positions),
            "middle": sum(p == "middle" for p in positions),
            "end": sum(p == "end" for p in positions),
        })

    targets: list[dict[str, int]] = []
    for i, counts in enumerate(own):
        semantic = counts["middle"] + counts["end"]
        # A start-position marker on the *next* physical line semantically marks
        # the ayah ending on this line, so carry next-line starts backwards.
        if i + 1 < len(own):
            semantic += own[i + 1]["start"]
        targets.append({"target": semantic, **counts})
    return targets


def _raw_visual_cost(
    line: PdfLine,
    count: int,
    marker_count: int,
    ends_with_marker: bool,
    marker_target: int,
) -> float:
    """Cost a candidate against visual word counts and semantic marker targets."""
    target = int(line.visual_word_count or line.extracted_word_count)
    delta = count - target
    cost = 45.0 * (delta ** 2)

    # Very weak width tie-breaker: rawdict word objects remain the primary source.
    if line.ink_width > 0 and count > 0 and target > 0:
        width_per_word = line.ink_width / target
        predicted_width = count * width_per_word
        cost += 0.0015 * ((predicted_width - line.ink_width) ** 2)

    marker_delta = abs(marker_count - marker_target)
    cost += 260.0 * (marker_delta ** 2)

    # When the visual marker is at the left terminus, it agrees with a marker
    # consumed by this line.  For a right-edge/start marker the target is carried
    # to the preceding line by _marker_position_counts, so no direct terminal
    # penalty is applied here.
    terminal = _marker_terminal_geometry(line)
    has_end_marker = bool(line.marker_positions and "end" in line.marker_positions)
    if terminal is not None and has_end_marker and bool(terminal) != ends_with_marker:
        cost += 180.0
    return cost

def _span_for_candidate(
    tokens: list[dict[str, Any]],
    word_positions: list[int],
    start_word_ordinal: int,
    word_count: int,
) -> tuple[int, int, int]:
    return _span_for_word_ordinal(tokens, word_positions, start_word_ordinal, word_count)


def solve_page(
    lines: list[PdfLine],
    tokens: list[dict[str, Any]],
    max_words_per_line: int,
    candidate_extra: int,
    max_visual_count_delta: int = 8,
    max_visual_line_delta: int = 1,
    max_corrected_lines: int = 8,
) -> tuple[list[int], dict[str, Any]]:
    """Align SQLite words to the physical PDF lines with rawdict-first evidence."""
    if not lines or not tokens:
        raise RuntimeError("Page has no usable PDF lines or SQLite tokens")

    word_positions = _word_positions(tokens)
    n_words = len(word_positions)
    m = len(lines)
    if n_words < m:
        raise RuntimeError(f"PDF has {m} lines but SQLite only has {n_words} words")

    visual_counts = [int(line.visual_word_count or line.extracted_word_count) for line in lines]
    visual_total = sum(visual_counts)
    total_delta = visual_total - n_words
    marker_targets = _marker_position_counts(lines)

    def evaluate_counts(counts: list[int]) -> tuple[int, int, int, list[dict[str, Any]]]:
        marker_error = 0
        visual_count_error = 0
        terminal_marker_error = 0
        details: list[dict[str, Any]] = []
        cursor = 0
        for idx, (line, count) in enumerate(zip(lines, counts)):
            _ts, token_end, marker_count = _span_for_word_ordinal(tokens, word_positions, cursor, count)
            ends_with_marker = bool(tokens[token_end - 1]["type"] == "ayah_marker")
            marker_delta = abs(marker_count - marker_targets[idx]["target"])
            count_delta = abs(count - int(line.visual_word_count or line.extracted_word_count))
            terminal = _marker_terminal_geometry(line)
            has_end_marker = bool(line.marker_positions and "end" in line.marker_positions)
            terminal_error = int(has_end_marker and terminal is not None and bool(terminal) != ends_with_marker)
            marker_error += marker_delta
            visual_count_error += count_delta
            terminal_marker_error += terminal_error
            details.append({
                "line": line.line,
                "words": count,
                "visual_word_count": int(line.visual_word_count or line.extracted_word_count),
                "visual_count_delta": count_delta,
                "visual_unit_count": int(line.visual_unit_count),
                "detected_markers": int(line.detected_marker_count),
                "semantic_marker_target": int(marker_targets[idx]["target"]),
                "token_markers": marker_count,
                "marker_delta": marker_delta,
                "visual_marker_unit_count": int(line.visual_marker_unit_count),
                "marker_positions": list(line.marker_positions or []),
                "ends_with_marker": ends_with_marker,
                "visual_terminal_marker": terminal,
                "terminal_marker_error": terminal_error,
                "bbox": line.bbox,
            })
            cursor += count
        return marker_error, visual_count_error, terminal_marker_error, details

    if visual_total == n_words and all(c >= 1 for c in visual_counts):
        direct_marker_error, direct_visual_error, direct_terminal_error, direct_details = evaluate_counts(visual_counts)
    else:
        direct_marker_error = direct_visual_error = direct_terminal_error = 0
        direct_details = []
    direct_safe = (
        visual_total == n_words
        and all(c >= 1 for c in visual_counts)
        and direct_marker_error == 0
        and direct_terminal_error == 0
    )

    if direct_safe:
        counts = visual_counts
        method = "rawdict_visual_line_units_exact"
    else:
        # A page-level delta by itself is not a reliable safety measure. Four
        # visual extras spread across four independent lines are much safer than
        # four extras concentrated in one line. The old V3 gate rejected the
        # former case before the DP even had a chance to resolve it.
        max_abs_page_delta = max(1, int(max_visual_count_delta))
        if abs(total_delta) > max_abs_page_delta:
            raise RuntimeError(
                "PDF/SQLite visual word-count mismatch exceeds the page correction budget: "
                f"PDF visual words={visual_total}, SQLite words={n_words}, delta={total_delta}; "
                f"allowed_abs_page_delta={max_abs_page_delta}. "
                f"visual_line_word_counts={visual_counts}"
            )

        prev: dict[int, tuple[float, tuple[int, int] | None]] = {0: (0.0, None)}
        backs: list[dict[int, tuple[int, int]]] = []
        for line_index, line in enumerate(lines):
            remaining_lines = m - line_index - 1
            current: dict[int, tuple[float, tuple[int, int] | None]] = {}
            back: dict[int, tuple[int, int]] = {}
            target = max(1, int(line.visual_word_count or line.extracted_word_count))
            for start_word_ordinal, (base_cost, _meta) in prev.items():
                remaining_words = n_words - start_word_ordinal
                if remaining_words < remaining_lines + 1:
                    continue
                lo = max(1, target - candidate_extra)
                hi = min(max_words_per_line, target + candidate_extra, remaining_words - remaining_lines)
                for count in range(lo, hi + 1):
                    _ts, token_end, marker_count = _span_for_candidate(tokens, word_positions, start_word_ordinal, count)
                    ends_with_marker = bool(tokens[token_end - 1]["type"] == "ayah_marker")
                    marker_target = int(marker_targets[line_index]["target"])
                    cost = _raw_visual_cost(line, count, marker_count, ends_with_marker, marker_target)
                    total = base_cost + cost
                    end_ordinal = start_word_ordinal + count
                    old = current.get(end_ordinal)
                    if old is None or total < old[0]:
                        current[end_ordinal] = (total, (start_word_ordinal, count))
                        back[end_ordinal] = (start_word_ordinal, count)
            if not current:
                raise RuntimeError(f"No safe visual alignment at PDF line {line.line}")
            prev = current
            backs.append(back)

        if n_words not in prev:
            raise RuntimeError("Visual alignment could not consume all SQLite page words")

        chosen: list[tuple[int, int]] = []
        cursor = n_words
        for back in reversed(backs):
            start, count = back[cursor]
            chosen.append((start, count))
            cursor = start
        chosen.reverse()
        counts = [count for _start, count in chosen]
        method = "rawdict_visual_line_units_constrained_dp"

    correction_deltas = [int(c) - int(v) for c, v in zip(counts, visual_counts)]
    corrected_line_count = sum(1 for d in correction_deltas if d)
    max_abs_line_delta = max((abs(d) for d in correction_deltas), default=0)
    total_abs_correction = sum(abs(d) for d in correction_deltas)
    if method != "rawdict_visual_line_units_exact":
        if max_abs_line_delta > max(1, int(max_visual_line_delta)):
            raise RuntimeError(
                "Visual correction is not local enough for safe alignment: "
                f"max_line_delta={max_abs_line_delta}, "
                f"allowed_max_line_delta={max_visual_line_delta}, "
                f"page_delta={total_delta}, "
                f"line_deltas={correction_deltas}, "
                f"visual_line_word_counts={visual_counts}, "
                f"aligned_line_word_counts={counts}"
            )
        if corrected_line_count > max(1, int(max_corrected_lines)):
            raise RuntimeError(
                "Visual correction touches too many physical lines for safe alignment: "
                f"corrected_lines={corrected_line_count}, "
                f"allowed_corrected_lines={max_corrected_lines}, "
                f"page_delta={total_delta}, "
                f"line_deltas={correction_deltas}"
            )

    marker_error, visual_count_error, terminal_marker_error, line_details = evaluate_counts(counts)
    avg_visual_error = visual_count_error / max(1, m)
    confidence = math.exp(
        -(1.15 * avg_visual_error + 1.3 * marker_error / max(1, m) + 1.0 * terminal_marker_error / max(1, m))
    )
    if method == "rawdict_visual_line_units_exact" and marker_error == 0 and terminal_marker_error == 0:
        confidence = 1.0

    diagnostics = {
        "token_count": len(tokens),
        "word_token_count": n_words,
        "pdf_line_count": m,
        "line_word_counts": counts,
        "visual_line_word_counts": visual_counts,
        "visual_total_word_count": visual_total,
        "visual_sqlite_word_delta": total_delta,
        "alignment_method": method,
        "exact_visual_alignment": bool(visual_total == n_words),
        "correction_applied": method != "rawdict_visual_line_units_exact",
        "correction_total_abs": total_abs_correction,
        "correction_corrected_lines": corrected_line_count,
        "correction_max_abs_line_delta": max_abs_line_delta,
        "correction_line_deltas": correction_deltas,
        "direct_visual_marker_error": direct_marker_error,
        "direct_visual_count_error": direct_visual_error,
        "direct_terminal_marker_error": direct_terminal_error,
        "marker_error": marker_error,
        "terminal_marker_error": terminal_marker_error,
        "average_visual_count_error": round(avg_visual_error, 6),
        "confidence": round(confidence, 6),
        "line_details": line_details,
    }
    return counts, diagnostics

def make_endpoint(token: dict[str, Any]) -> dict[str, Any]:
    if token["type"] == "ayah_marker":
        return {
            "type": "ayah_marker",
            "surah": token["surah"],
            "ayah": token["ayah"],
            "number": token["number"],
        }
    return {
        "type": "word",
        "surah": token["surah"],
        "ayah": token["ayah"],
        "word": token["word"],
        "wordIndex": token["wordIndex"],
    }


def page_lines_from_counts(tokens: list[dict[str, Any]], lines: list[PdfLine], counts: list[int]) -> list[dict[str, Any]]:
    word_positions = _word_positions(tokens)
    output: list[dict[str, Any]] = []
    start_ordinal = 0
    for line, count in zip(lines, counts):
        start_token, end_token, _markers = _span_for_word_ordinal(tokens, word_positions, start_ordinal, count)
        if end_token <= start_token:
            raise RuntimeError("Internal token span is empty")
        output.append({
            "line": line.line,
            "first_word": make_endpoint(tokens[start_token]),
            "end_word": make_endpoint(tokens[end_token - 1]),
        })
        start_ordinal += count
    if start_ordinal != len(word_positions):
        raise RuntimeError("Internal word cursor did not consume page words")
    return output


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", type=Path, required=True)
    ap.add_argument("--sqlite", type=Path, required=True)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    source_info = validate_pdf_source(str(args.pdf), config.get("qiraah"))
    by_page = load_sqlite(args.sqlite)
    expected_pages = int(config.get("expected_pages", 604))
    if len(by_page) < expected_pages:
        raise RuntimeError(f"SQLite only covers {len(by_page)} pages")

    page_meta = {
        p: {
            "is_surah_start": bool(rows and rows[0]["ayah"] == 1),
            "first_surah": (int(rows[0]["surah"]) if rows else None),
        }
        for p, rows in by_page.items()
    }
    pdf_pages = extract_document(str(args.pdf), page_meta, config)
    if len(pdf_pages) != expected_pages:
        raise RuntimeError(f"PDF has {len(pdf_pages)} pages; expected {expected_pages}")

    tok_cfg = config.get("tokenization", {})
    max_words_per_line = int(tok_cfg.get("max_tokens_per_line", 30))
    candidate_extra = int(tok_cfg.get("candidate_extra_tokens", 4))
    max_visual_count_delta = int(tok_cfg.get("max_visual_count_delta_for_correction", 8))
    max_visual_line_delta = int(tok_cfg.get("max_visual_line_delta_for_correction", 1))
    max_corrected_lines = int(tok_cfg.get("max_corrected_lines_for_correction", 8))
    review_cfg = config.get("validation", {})
    confidence_threshold = float(review_cfg.get("review_confidence_threshold", 0.70))

    output_dir = args.output_dir
    pages_dir = output_dir / "pages"
    surahs_dir = output_dir / "surahs"
    output_dir.mkdir(parents=True, exist_ok=True)
    pages_dir.mkdir(parents=True, exist_ok=True)
    surahs_dir.mkdir(parents=True, exist_ok=True)

    all_page_lines: dict[int, list[dict[str, Any]]] = {}
    page_surahs: dict[int, list[int]] = {}
    review: list[dict[str, Any]] = []

    for page_no in range(1, expected_pages + 1):
        rows = by_page.get(page_no, [])
        tokens = build_page_tokens(rows)
        pdf_lines = pdf_pages.get(page_no, [])
        if not tokens:
            raise RuntimeError(f"Page {page_no}: SQLite has no Quran tokens")
        if not pdf_lines:
            raise RuntimeError(f"Page {page_no}: PDF has no usable Quran lines")
        if len(pdf_lines) > 20:
            raise RuntimeError(f"Page {page_no}: PDF produced {len(pdf_lines)} Quran lines (>20)")

        try:
            counts, diagnostics = solve_page(
                pdf_lines,
                tokens,
                max_words_per_line,
                candidate_extra,
                max_visual_count_delta,
                max_visual_line_delta,
                max_corrected_lines,
            )
        except RuntimeError as exc:
            visual_counts = [int(line.visual_word_count or line.extracted_word_count) for line in pdf_lines]
            visual_total = sum(visual_counts)
            sqlite_words = len(_word_positions(tokens))
            raise RuntimeError(
                f"Page {page_no}: {exc} "
                f"[pdf_line_count={len(pdf_lines)}, pdf_visual_words={visual_total}, "
                f"sqlite_words={sqlite_words}, visual_sqlite_delta={visual_total - sqlite_words}]"
            ) from exc
        lines_json = page_lines_from_counts(tokens, pdf_lines, counts)
        all_page_lines[page_no] = lines_json

        surah_ids = sorted({int(t["surah"]) for t in tokens})
        page_surahs[page_no] = surah_ids

        reasons: list[str] = []
        if abs(int(diagnostics["visual_sqlite_word_delta"])) > 1:
            reasons.append("large_visual_word_count_correction")
        if diagnostics["terminal_marker_error"] > 0:
            reasons.append("terminal_marker_mismatch")
        if diagnostics["confidence"] < confidence_threshold:
            reasons.append("low_geometry_confidence")
        if reasons:
            review.append({"page": page_no, "status": "review_required", "reasons": reasons, **diagnostics})

    font_family = config.get("font_family")
    for page_no in range(1, expected_pages + 1):
        payload = {
            "schema_version": 3,
            "page": page_no,
            "surah_ids": page_surahs[page_no],
            "font_family": font_family,
            "layout_source": "pdf",
            "pdf_alignment_version": 4,
            "lines": all_page_lines[page_no],
        }
        (pages_dir / f"page_{page_no:03d}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    surah_pages: dict[int, list[int]] = defaultdict(list)
    for page_no, sid_list in page_surahs.items():
        for sid in sid_list:
            surah_pages[sid].append(page_no)

    for sid in range(1, 115):
        pages = []
        for page_no in sorted(surah_pages.get(sid, [])):
            lines = []
            for line in all_page_lines[page_no]:
                endpoints = [line["first_word"], line["end_word"]]
                if any(int(ep["surah"]) == sid for ep in endpoints):
                    lines.append(line)
            if lines:
                pages.append({"page": page_no, "lines": lines})
        payload = {
            "schema_version": 3,
            "surahId": sid,
            "surahName": SURAH_NAMES[sid - 1],
            "startPage": pages[0]["page"] if pages else None,
            "endPage": pages[-1]["page"] if pages else None,
            "totalPagesInSurah": len(pages),
            "totalCompletedLines": sum(len(p["lines"]) for p in pages),
            "ayaCount": 0,
            "font_family": font_family,
            "layout_source": "pdf",
            "pdf_alignment_version": 4,
            "pages": pages,
        }
        max_ayah = 0
        for p in pages:
            for line in p["lines"]:
                for ep in (line["first_word"], line["end_word"]):
                    if int(ep["surah"]) == sid:
                        max_ayah = max(max_ayah, int(ep["ayah"]))
        payload["ayaCount"] = max_ayah
        (surahs_dir / f"{sid:03d}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    manifest = {
        "schema_version": 3,
        "layout_source": "pdf",
        "pdf_alignment_version": 4,
        "word_boundary_method": "rawdict_visual_line_units_exact_or_bounded_constrained_dp",
        "qiraah": config.get("qiraah"),
        "font_family": font_family,
        "font_required_for_builder": False,
        "source_pdf": str(args.pdf.as_posix()),
        "source_sqlite": str(args.sqlite.as_posix()),
        "source_qiraah_expected": source_info.get("expected_qiraah"),
        "source_qiraah_detected": source_info.get("detected_qiraah"),
        "source_compatibility": "matched",
        "total_pages": expected_pages,
        "total_surahs": 114,
        "review_required_pages": len(review),
        "review_pages": [item["page"] for item in review],
        "review_policy": "Only pages whose rawdict visual word structure matches SQLite (or receives a bounded correction) and whose marker geometry agrees are treated as verified.",
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "review.json").write_text(
        json.dumps({"schema_version": 2, "pages": review}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    total_lines = sum(len(v) for v in all_page_lines.values())
    print(f"OK: generated {expected_pages} pages, 114 surahs, {total_lines} Quran lines")
    print(f"Review required: {len(review)} pages")
    for p in (1, 2, 34, 107, 440, 598, 604):
        page_review = next((r for r in review if r["page"] == p), None)
        status = page_review["status"] if page_review else "verified_by_constraints"
        print(f"  page {p}: {len(all_page_lines[p])} lines [{status}]")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)
