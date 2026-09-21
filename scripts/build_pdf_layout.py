#!/usr/bin/env python3
"""Build compact schema-v3 Quran layout JSON from PDF geometry + SQLite text.

Important: this script never attempts to decode the PDF's custom-font Unicode.
SQLite supplies the Quran token sequence; the PDF supplies physical line evidence.
A constrained DP chooses how many SQLite tokens belong to each physical line.
Any page with weak evidence is recorded in output/review.json instead of being
silently presented as verified.
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

from pdf_layout import PdfLine, extract_document

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
    # Positive-width glyph count correlates much better with letter count than
    # raw Unicode length, so ignore combining marks and tatweel here.
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


def _prefix(values: list[int]) -> list[int]:
    out = [0]
    for value in values:
        out.append(out[-1] + value)
    return out


def solve_page(lines: list[PdfLine], tokens: list[dict[str, Any]], max_tokens_per_line: int, candidate_extra: int) -> tuple[list[int], dict[str, Any]]:
    if not lines or not tokens:
        raise RuntimeError("Page has no usable PDF lines or SQLite tokens")

    n = len(tokens)
    m = len(lines)
    features = _prefix([int(t["feature"]) for t in tokens])
    markers = _prefix([1 if t["type"] == "ayah_marker" else 0 for t in tokens])

    total_feature = features[-1]
    total_chars = sum(line.char_count for line in lines)
    scale = total_chars / max(total_feature, 1)

    # DP state: best total cost ending after token index j.
    prev: dict[int, tuple[float, tuple[int, int, float, int] | None]] = {0: (0.0, None)}
    back_layers: list[dict[int, int]] = []

    for i, line in enumerate(lines):
        remaining_lines = m - i - 1
        current: dict[int, tuple[float, tuple[int, int, float, int] | None]] = {}
        back: dict[int, int] = {}

        for start, (base_cost, _) in prev.items():
            min_end = start + 1
            max_end = min(n - remaining_lines, start + max_tokens_per_line)
            if min_end > max_end:
                continue

            for end in range(min_end, max_end + 1):
                count = end - start
                if n - end < remaining_lines:
                    continue

                expected_feature = features[end] - features[start]
                expected_markers = markers[end] - markers[start]
                extracted_lower = max(1, line.extracted_word_count)
                extracted_upper = max(extracted_lower, line.char_split_upper + candidate_extra)

                # Strong evidence: line glyph count vs expected Quran-letter mass.
                char_target = scale * expected_feature
                char_cost = ((line.char_count - char_target) ** 2) / (line.char_count + 4.0)

                # Use extracted PDF marker count as a soft anchor. It can miss
                # split/custom-font marker glyphs, so it is intentionally not hard.
                marker_delta = abs(expected_markers - line.detected_marker_count)
                marker_cost = 7.0 * (marker_delta ** 2)

                # Keep the solution close to what the PDF extractor can support,
                # but allow a small amount of splitting/merging beyond it.
                bound_penalty = 0.0
                if count < extracted_lower:
                    bound_penalty += 6.0 * (extracted_lower - count) ** 2
                elif count > extracted_upper:
                    bound_penalty += 2.0 * (count - extracted_upper) ** 2

                # Avoid bizarre line lengths when several partitions have almost
                # identical geometry costs.
                prior = 0.08 * ((count - 9.0) ** 2)
                total = base_cost + char_cost + marker_cost + bound_penalty + prior

                old = current.get(end)
                if old is None or total < old[0]:
                    current[end] = (total, (start, count, char_target, expected_markers))
                    back[end] = start

        if not current:
            raise RuntimeError(f"No valid token partition at PDF line {line.line}")
        prev = current
        back_layers.append(back)

    if n not in prev:
        raise RuntimeError("DP could not consume all SQLite page tokens")

    ends: list[tuple[int, int]] = []
    cursor = n
    for layer in reversed(back_layers):
        start = layer[cursor]
        ends.append((start, cursor))
        cursor = start
    ends.reverse()

    counts = [end - start for start, end in ends]
    final_cost = prev[n][0]

    marker_error = sum(abs((markers[end] - markers[start]) - line.detected_marker_count) for (start, end), line in zip(ends, lines))
    normalized_cost = final_cost / max(1, m)
    confidence = math.exp(-normalized_cost / 18.0)

    diagnostics = {
        "token_count": n,
        "pdf_line_count": m,
        "line_token_counts": counts,
        "pdf_char_count": total_chars,
        "sqlite_feature_count": total_feature,
        "feature_scale": round(scale, 6),
        "marker_error": marker_error,
        "normalized_cost": round(normalized_cost, 6),
        "confidence": round(confidence, 6),
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
    output: list[dict[str, Any]] = []
    cursor = 0
    for line, count in zip(lines, counts):
        start = cursor
        end = cursor + count
        if end > len(tokens):
            raise RuntimeError("Internal token cursor exceeded page token list")
        output.append({
            "line": line.line,
            "first_word": make_endpoint(tokens[start]),
            "end_word": make_endpoint(tokens[end - 1]),
        })
        cursor = end
    if cursor != len(tokens):
        raise RuntimeError("Internal token cursor did not consume page tokens")
    return output


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", type=Path, required=True)
    ap.add_argument("--sqlite", type=Path, required=True)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    by_page = load_sqlite(args.sqlite)
    if len(by_page) < int(config.get("expected_pages", 604)):
        raise RuntimeError(f"SQLite only covers {len(by_page)} pages")

    page_meta = {
        p: {
            "is_surah_start": bool(rows and rows[0]["ayah"] == 1),
        }
        for p, rows in by_page.items()
    }
    pdf_pages = extract_document(str(args.pdf), page_meta, config)
    expected_pages = int(config.get("expected_pages", 604))
    if len(pdf_pages) != expected_pages:
        raise RuntimeError(f"PDF has {len(pdf_pages)} pages; expected {expected_pages}")

    tok_cfg = config.get("tokenization", {})
    max_tokens_per_line = int(tok_cfg.get("max_tokens_per_line", 30))
    candidate_extra = int(tok_cfg.get("candidate_extra_tokens", 3))
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

        counts, diagnostics = solve_page(pdf_lines, tokens, max_tokens_per_line, candidate_extra)
        lines_json = page_lines_from_counts(tokens, pdf_lines, counts)
        all_page_lines[page_no] = lines_json

        surah_ids = sorted({int(t["surah"]) for t in tokens})
        page_surahs[page_no] = surah_ids

        status = "verified_by_constraints"
        reasons: list[str] = []
        if diagnostics["marker_error"] > 0:
            reasons.append("marker_evidence_mismatch")
        if diagnostics["confidence"] < confidence_threshold:
            reasons.append("low_geometry_confidence")
        if reasons:
            status = "review_required"
            review.append({"page": page_no, "status": status, "reasons": reasons, **diagnostics})

    # Build page JSON.
    font_family = config.get("font_family")
    for page_no in range(1, expected_pages + 1):
        payload = {
            "schema_version": 3,
            "page": page_no,
            "surah_ids": page_surahs[page_no],
            "font_family": font_family,
            "layout_source": "pdf",
            "lines": all_page_lines[page_no],
        }
        (pages_dir / f"page_{page_no:03d}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    # Build surah JSON by assigning a line to a surah if either endpoint points to it.
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
            "pages": pages,
        }
        # Derive highest ayah seen for this surah from endpoints.
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
        "qiraah": config.get("qiraah"),
        "font_family": font_family,
        "font_required_for_builder": False,
        "source_pdf": str(args.pdf.as_posix()),
        "source_sqlite": str(args.sqlite.as_posix()),
        "total_pages": expected_pages,
        "total_surahs": 114,
        "review_required_pages": len(review),
        "review_pages": [item["page"] for item in review],
        "review_policy": "Pages with mismatched marker evidence or low geometric confidence are not treated as verified.",
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "review.json").write_text(
        json.dumps({"schema_version": 1, "pages": review}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    total_lines = sum(len(v) for v in all_page_lines.values())
    print(f"OK: generated {expected_pages} pages, 114 surahs, {total_lines} Quran lines")
    print(f"Review required: {len(review)} pages")
    for p in (1, 2, 34, 107, 440, 598, 604):
        page_review = next((r for r in review if r["page"] == p), None)
        print(f"  page {p}: {len(all_page_lines[p])} lines" + (f" [{page_review['status']}]" if page_review else " [verified_by_constraints]"))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)
