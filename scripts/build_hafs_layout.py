#!/usr/bin/env python3
"""Build Hafs Quran layout from the supplied legacy MS-DOC + SQLite pair.

The DOC is authoritative for physical page/line boundaries. For this verified
Hafs source pair, SQLite's `page` field is used only as a secondary page-range
constraint: it bounds the word stream available to each already-established DOC
page; it does not define line boundaries or line breaks.

Because the Hafs DOC uses Uthmanic orthographic forms that are not always
character-identical to the SQLite text, each DOC page is aligned to its SQLite
page word range with constrained fuzzy matching. The result remains compact
schema-v3 with the same first_word/end_word endpoint contract as the other
DOC+SQLite workflows.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz.distance import Levenshtein

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_doc_layout import (  # noqa: E402
    QURAN_SYMBOLS,
    canonical as legacy_canonical,
    endpoint_from_word,
    extract_doc_pages,
    marker_endpoint,
    sha256_file,
    split_token,
    write_outputs,
)

SURAH_HEADING_RE = re.compile(r"^\s*سُورَةُ\s+(.+?)\s*$")
BISMILLAH_SOURCE = "بِسۡمِ ٱللَّهِ ٱلرَّحۡمَٰنِ ٱلرَّحِيمِ"


def is_heading(text: str) -> bool:
    return SURAH_HEADING_RE.match(text.strip()) is not None


def is_standalone_bismillah(text: str) -> bool:
    return legacy_canonical(text).strip() == legacy_canonical(BISMILLAH_SOURCE)


def is_fatihah_bismillah_ayah1(text: str) -> bool:
    """Hafs encodes Al-Fatihah's opening Bismillah as numbered ayah 1."""
    parts: list[str] = []
    marker: int | None = None
    for raw in re.split(r"\s+", text.strip()):
        for token, maybe_marker in split_token(raw):
            if token:
                parts.append(token)
            if maybe_marker is not None:
                marker = maybe_marker
    return (
        marker == 1
        and legacy_canonical("".join(parts)).replace(" ", "")
        == legacy_canonical(BISMILLAH_SOURCE).replace(" ", "")
    )


def hafs_canonical(text: str) -> str:
    """Normalize Hafs Uthmanic/SQLite strings for fuzzy alignment only."""
    # Do this before NFD: otherwise carrier-hamza forms such as ئ/ؤ can turn
    # into combining marks which the cleanup below deliberately removes.
    text = (text or "").replace("ئ", "ء").replace("ؤ", "ء").replace("ٰ", "ا")
    text = unicodedata.normalize("NFD", text)
    out: list[str] = []
    for ch in text:
        if ch == "ـ" or ch in QURAN_SYMBOLS:
            continue
        cat = unicodedata.category(ch)
        if cat in ("Mn", "Cf"):
            continue
        if ch in "ٱأإآ":
            ch = "ا"
        elif ch == "ى":
            ch = "ي"
        out.append(ch)
    return "".join(out).replace(" ", "")


def validate_source_integrity(doc_path: Path, sqlite_path: Path, config: dict) -> dict:
    expected = config.get("source_integrity") or {}
    actual_doc = sha256_file(doc_path)
    actual_sqlite = sha256_file(sqlite_path)
    expected_doc = (expected.get("doc") or {}).get("sha256")
    expected_sqlite = (expected.get("sqlite") or {}).get("sha256")
    if expected_doc and actual_doc != expected_doc:
        raise RuntimeError(
            f"Source DOC integrity mismatch: expected SHA256={expected_doc}, actual SHA256={actual_doc}"
        )
    if expected_sqlite and actual_sqlite != expected_sqlite:
        raise RuntimeError(
            f"Source SQLite integrity mismatch: expected SHA256={expected_sqlite}, actual SHA256={actual_sqlite}"
        )
    return {
        "doc_sha256": actual_doc,
        "sqlite_sha256": actual_sqlite,
        "integrity_policy": "sha256_exact_source_pair",
    }


def load_sqlite_pages(sqlite_path: Path) -> tuple[dict[int, list[dict]], int]:
    conn = sqlite3.connect(sqlite_path)
    try:
        rows = conn.execute(
            "SELECT surah, ayah, text, page FROM ayat ORDER BY surah, ayah"
        ).fetchall()
    finally:
        conn.close()

    pages: dict[int, list[dict]] = {page: [] for page in range(1, 605)}
    total_words = 0
    for surah, ayah, text, page in rows:
        page_no = int(page)
        if not 1 <= page_no <= 604:
            raise RuntimeError(f"SQLite contains out-of-range Hafs page value: {page_no}")
        word_index = 0
        for raw in re.split(r"\s+", (text or "").strip()):
            if not raw:
                continue
            for token, _marker in split_token(raw):
                if not token:
                    continue
                word_index += 1
                pages[page_no].append(
                    {
                        "surah": int(surah),
                        "ayah": int(ayah),
                        "word": token,
                        "wordIndex": word_index,
                        "f": hafs_canonical(token),
                    }
                )
                total_words += 1

    missing = [p for p, arr in pages.items() if not arr]
    if missing:
        raise RuntimeError(f"SQLite is missing words for Hafs pages: {missing[:10]}")
    return pages, total_words


def segment_distance_limit(target_len: int, config: dict) -> int:
    align = config.get("alignment") or {}
    absolute = int(align.get("max_edit_distance", 6))
    ratio = float(align.get("max_edit_distance_ratio", 0.25))
    return max(2, min(absolute, max(2, int(target_len * ratio))))


@dataclass(frozen=True)
class Segment:
    line: int
    surah: int
    text: str
    marker: int | None


def parse_page_structure(
    page_lines: list[str], current_surah: int, page_no: int
) -> tuple[list[Segment], int, list[int]]:
    segments: list[Segment] = []
    page_surahs: list[int] = []
    line_no = 0
    for raw_line in page_lines:
        line = raw_line.strip()
        if not line:
            continue
        if is_heading(line):
            current_surah += 1
            if current_surah > 114:
                raise RuntimeError(f"Page {page_no}: detected more than 114 surah headings")
            if current_surah not in page_surahs:
                page_surahs.append(current_surah)
            continue
        # Every standalone Bismillah after Al-Fatihah is structural. The numbered
        # Bismillah in Al-Fatihah is intentionally NOT caught by this condition.
        if current_surah > 1 and is_standalone_bismillah(line):
            if current_surah not in page_surahs:
                page_surahs.append(current_surah)
            continue
        if current_surah == 0:
            raise RuntimeError(f"Page {page_no}: Quran line before first surah heading")

        line_no += 1
        if current_surah not in page_surahs:
            page_surahs.append(current_surah)
        line_tokens: list[tuple[str, int | None]] = []
        for raw_token in re.split(r"\s+", line):
            line_tokens.extend(split_token(raw_token))
        parts: list[str] = []
        for token, marker in line_tokens:
            if token:
                parts.append(token)
            if marker is not None:
                if not parts:
                    raise RuntimeError(
                        f"Page {page_no} line {line_no}: marker without preceding text"
                    )
                segments.append(Segment(line_no, current_surah, "".join(parts), marker))
                parts = []
        if parts:
            segments.append(Segment(line_no, current_surah, "".join(parts), None))
    return segments, current_surah, page_surahs


def align_page(
    segments: list[Segment],
    page_words: list[dict],
    config: dict,
) -> tuple[list[tuple[Segment, int, int, int]], int]:
    """Align all segments of one DOC page to the exact SQLite page word range."""
    align = config.get("alignment") or {}
    max_words = int(align.get("max_candidate_words", 24))

    # Cursor within this page -> (cost, previous cursor, distance, word_count).
    states: dict[int, tuple[int, int | None, int, int]] = {0: (0, None, 0, 0)}
    layers: list[dict[int, tuple[int, int | None, int, int]]] = []

    for seg in segments:
        new: dict[int, tuple[int, int | None, int, int]] = {}
        target = hafs_canonical(seg.text)
        distance_limit = segment_distance_limit(len(target), config)
        for start, (base_cost, _prev, _d, _n) in states.items():
            if start >= len(page_words):
                continue
            acc = ""
            for n in range(1, min(max_words, len(page_words) - start) + 1):
                item = page_words[start + n - 1]
                if item["surah"] != seg.surah:
                    break
                acc += item["f"]
                if seg.marker is not None and item["ayah"] != seg.marker:
                    continue
                distance = Levenshtein.distance(target, acc)
                if distance > distance_limit:
                    continue
                end = start + n
                candidate = base_cost + distance
                previous = new.get(end)
                if previous is None or candidate < previous[0]:
                    new[end] = (candidate, start, distance, n)
        if not new:
            raise RuntimeError(
                f"Unable to align Hafs page segment at line {seg.line}, surah {seg.surah}: {seg.text!r}"
            )
        states = new
        layers.append(states)

    final = len(page_words)
    if final not in states:
        best = sorted((cost, pos) for pos, (cost, *_rest) in states.items())[:5]
        raise RuntimeError(
            f"Hafs page alignment ended at the wrong word count: expected {final}, best={best}"
        )

    path_rev: list[tuple[Segment, int, int, int]] = []
    cursor = final
    for layer_index in range(len(segments) - 1, -1, -1):
        cost, previous, distance, word_count = layers[layer_index][cursor]
        if previous is None:
            raise RuntimeError("Hafs alignment backtracking reached an invalid root")
        seg = segments[layer_index]
        path_rev.append((seg, previous, cursor, distance))
        cursor = previous
    path_rev.reverse()
    total_cost = states[final][0]
    return path_rev, total_cost


def build_from_sources(doc_path: Path, sqlite_path: Path, config: dict) -> dict:
    integrity = validate_source_integrity(doc_path, sqlite_path, config)
    doc_pages, source_meta = extract_doc_pages(doc_path)
    sqlite_pages, total_sqlite_words = load_sqlite_pages(sqlite_path)
    if len(doc_pages) != 604:
        raise RuntimeError(f"DOC reconstruction produced {len(doc_pages)} pages; expected 604")

    out_pages: dict[int, dict] = {}
    surahs: dict[int, dict] = {}
    chosen_paths: dict[int, list[tuple[Segment, int, int, int]]] = {}
    total_edit_distance = 0
    fuzzy_segments = 0
    current_surah = 0

    for page_no, page_lines in enumerate(doc_pages, 1):
        segments, current_surah, page_surahs = parse_page_structure(
            page_lines, current_surah, page_no
        )
        page_words = sqlite_pages[page_no]
        path, page_cost = align_page(segments, page_words, config)
        chosen_paths[page_no] = path
        total_edit_distance += page_cost
        fuzzy_segments += sum(1 for _seg, _start, _end, distance in path if distance)
        out_pages[page_no] = {"lines": [], "surah_ids": page_surahs}

    # Rebuild compact line endpoints from the winning segment paths.
    for page_no, path in chosen_paths.items():
        by_line: dict[int, list[tuple[Segment, int, int, int]]] = {}
        for record in path:
            by_line.setdefault(record[0].line, []).append(record)
        for line_no in sorted(by_line):
            segments = by_line[line_no]
            first = sqlite_pages[page_no][segments[0][1]]
            last = sqlite_pages[page_no][segments[-1][2] - 1]
            terminal_marker = segments[-1][0].marker
            end_ep = (
                marker_endpoint(segments[-1][0].surah, terminal_marker)
                if terminal_marker is not None
                else endpoint_from_word(last)
            )
            out_pages[page_no]["lines"].append(
                {
                    "line": line_no,
                    "start": endpoint_from_word(first),
                    "end": end_ep,
                    "first_word": endpoint_from_word(first),
                    "end_word": end_ep,
                }
            )

    # Build surah records from the reconstructed pages, mirroring the compact
    # output contract of the existing DOC+SQLite engine.
    current_surah = 0
    for page_no, page_lines in enumerate(doc_pages, 1):
        for raw_line in page_lines:
            line = raw_line.strip()
            if not line:
                continue
            if is_heading(line):
                current_surah += 1
                surahs[current_surah] = {
                    "surahId": current_surah,
                    "surahName": SURAH_HEADING_RE.match(line).group(1).strip(),
                    "pages": [],
                }

    if current_surah != 114:
        raise RuntimeError(f"Detected {current_surah} surahs; expected 114")

    for page_no, page_obj in out_pages.items():
        for sid in page_obj["surah_ids"]:
            if sid in surahs:
                surahs[sid]["pages"].append(page_no)

    for sid, surah in surahs.items():
        bucket = []
        for page_no in surah["pages"]:
            filtered = [
                line for line in out_pages[page_no]["lines"]
                if line["first_word"]["surah"] == sid or line["end_word"]["surah"] == sid
            ]
            if filtered:
                bucket.append({"page": page_no, "lines": filtered})
        surah["pages"] = bucket
        surah["startPage"] = bucket[0]["page"] if bucket else None
        surah["endPage"] = bucket[-1]["page"] if bucket else None
        surah["totalPagesInSurah"] = len(bucket)
        surah["totalCompletedLines"] = sum(len(p["lines"]) for p in bucket)
        max_ayah = 0
        for p in bucket:
            for line in p["lines"]:
                for ep in (line["first_word"], line["end_word"]):
                    if ep["type"] == "ayah_marker":
                        max_ayah = max(max_ayah, int(ep["number"]))
                    elif ep.get("surah") == sid:
                        max_ayah = max(max_ayah, int(ep["ayah"]))
        surah["ayaCount"] = max_ayah

    return {
        "schema_version": 3,
        "layout_source": "doc+sqlite",
        "qiraah": "hafs",
        "font_family": config.get("font_family"),
        "source": {
            "doc": doc_path.name,
            "sqlite": sqlite_path.name,
            "sqlite_page_field_used": True,
            "sqlite_page_field_role": "secondary_page_range_constraint_only",
            "alignment_method": "page_bounded_fuzzy_dp",
            "beam_width": None,
            "max_candidate_words": int((config.get("alignment") or {}).get("max_candidate_words", 30)),
            "fuzzy_segments": fuzzy_segments,
            "total_edit_distance": total_edit_distance,
            "total_sqlite_words": total_sqlite_words,
            **integrity,
            **source_meta,
        },
        "total_pages": len(out_pages),
        "total_surahs": len(surahs),
        "pages": out_pages,
        "surahs": surahs,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", type=Path, required=True)
    ap.add_argument("--sqlite", type=Path, required=True)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()

    if not args.doc.is_file() or not args.sqlite.is_file():
        raise RuntimeError("DOC and SQLite inputs must exist")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    result = build_from_sources(args.doc, args.sqlite, config)
    if result["total_pages"] != 604 or result["total_surahs"] != 114:
        raise RuntimeError("Hafs reconstruction did not produce 604 pages and 114 surahs")
    for page_no, page in result["pages"].items():
        if not page["lines"] or len(page["lines"]) > 15:
            raise RuntimeError(f"Page {page_no}: invalid Quran line count")
    write_outputs(result, args.output_dir)
    print(
        f"OK: generated Hafs DOC+SQLite layout: {result['total_pages']} pages, "
        f"{result['total_surahs']} surahs; fuzzy_segments={result['source']['fuzzy_segments']}, "
        f"total_edit_distance={result['source']['total_edit_distance']}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
