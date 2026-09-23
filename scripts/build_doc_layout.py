#!/usr/bin/env python3
"""Build Quran layout from the supplied legacy MS-DOC plus its SQLite text.

The DOC is the authority for physical page/line boundaries. SQLite is used only
as the canonical ordered word stream; its `page` column is intentionally ignored.
This lets us handle verses that cross a printed page boundary.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

from build_layout import arabic_digits_to_int

SURAH_HEADING_RE = re.compile(r"^\s*سُورَةُ\s+(.+?)\s*$")
TRAILING_DIGITS_RE = re.compile(r"^(.*?)([٠-٩۰-۹0-9]+)$")
DIGITS_RE = re.compile(r"^[٠-٩۰-۹0-9]+$")

# Quranic annotation/symbol characters which are not part of a lexical word.
QURAN_SYMBOLS = {
    "۞", "۩", "۝", "۟", "۠", "ۡ", "ۢ", "ۣ", "ۤ", "ۥ", "ۦ", "ۧ", "ۨ", "۪", "۫", "۬", "ۭ",
}


def canonical(s: str) -> str:
    """Normalize DOC/SQLite word strings for boundary matching only."""
    s = unicodedata.normalize("NFD", s or "")
    out = []
    for ch in s:
        cat = unicodedata.category(ch)
        if cat in ("Mn", "Cf"):
            continue
        if ch in QURAN_SYMBOLS or ch == "ـ":
            continue
        if ch in "ٱأإآ":
            ch = "ا"
        elif ch == "ى":
            ch = "ي"
        out.append(ch)
    return "".join(out)




def canonical_loose(s: str) -> str:
    """Small fallback normalization for legacy DOC/SQLite orthographic glyph differences.

    The source DOC can encode a standalone hamza in a visually equivalent way that
    is absent from the SQLite word, or vice versa. We use this fallback only after
    exact canonical matching fails, and never to skip/reorder words.
    """
    return canonical(s).replace("ء", "")

def is_heading(text: str) -> bool:
    # Keep the exact U+0651/etc. pattern here: a Quran verse beginning with
    # "سُورَةٌ" must NOT be mistaken for a surah heading "سُورَةُ".
    return SURAH_HEADING_RE.match(text.strip()) is not None


def is_bismillah(text: str) -> bool:
    # Only a standalone Bismillah line is structural. In Surah An-Naml,
    # "بسم الله الرحمن الرحيم" occurs inside an actual Quran verse and must
    # therefore remain part of the SQLite word stream.
    return canonical(text).strip() == canonical("بسم الله الرحمن الرحيم")


def split_token(token: str) -> list[tuple[str, int | None]]:
    token = token.strip()
    if not token:
        return []
    token = token.replace("۞", "")
    if not token:
        return []
    if DIGITS_RE.fullmatch(token):
        return [("", arabic_digits_to_int(token))]

    # Arabic RTL extraction from legacy DOC can attach a verse number to the
    # following word instead of the preceding word (e.g. "٢٦ءَٰا۬نتُمۡ").
    # Recognize both leading and trailing number forms.
    leading = re.match(r"^([٠-٩۰-۹0-9]+)(.+)$", token)
    if leading:
        return [("", arabic_digits_to_int(leading.group(1))), (leading.group(2), None)]

    trailing = TRAILING_DIGITS_RE.match(token)
    if trailing and trailing.group(1):
        return [(trailing.group(1), None), ("", arabic_digits_to_int(trailing.group(2)))]
    return [(token, None)]


def run_antiword(doc_path: Path, mode: str) -> str:
    if mode == "text":
        cmd = ["antiword", "-f", "-m", "UTF-8.txt", str(doc_path)]
    elif mode == "xml":
        cmd = ["antiword", "-x", "db", str(doc_path)]
    else:
        raise ValueError(mode)
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:
        raise RuntimeError("antiword is required; install it with apt-get install antiword") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"antiword failed for {doc_path.name}: {exc.stderr.strip()}") from exc
    return proc.stdout


def text_without_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


def no_ws(s: str) -> str:
    return "".join(ch for ch in s if not ch.isspace())


def extract_doc_pages(doc_path: Path) -> tuple[list[list[str]], dict]:
    formatted = run_antiword(doc_path, "text")
    formatted_lines = [line.rstrip() for line in formatted.splitlines() if line.strip()]
    xml = run_antiword(doc_path, "xml")
    raw_segments = xml.split("<beginpage/>")

    page_segments: list[list[str]] = []
    cursor = 0
    for segment_index, segment in enumerate(raw_segments, 1):
        target = no_ws(text_without_tags(segment))
        if segment_index == 1:
            m = re.search("سُورَةُ", target)
            if m:
                target = target[m.start():]
        acc = ""
        start = cursor
        while cursor < len(formatted_lines) and len(acc) < len(target):
            acc += no_ws(formatted_lines[cursor])
            cursor += 1
            if acc == target:
                break
        if acc != target:
            raise RuntimeError(
                f"{doc_path.name}: DOC text/page boundary alignment failed at raw page segment {segment_index}"
            )
        page_segments.append(formatted_lines[start:cursor])

    if cursor != len(formatted_lines):
        raise RuntimeError(
            f"{doc_path.name}: formatter left {len(formatted_lines) - cursor} unconsumed text lines"
        )

    # These legacy DOCs contain a small number of missing page-break markers.
    # In the observed source, every affected segment is exactly 30 printed lines,
    # i.e. two normal 15-line pages. Split only exact multiples of 15 > 15.
    pages: list[list[str]] = []
    split_segments = 0
    for segment in page_segments:
        n = len(segment)
        if n <= 15:
            pages.append(segment)
            continue
        if n % 15 != 0:
            raise RuntimeError(
                f"{doc_path.name}: unexpected DOC page segment with {n} formatted lines; refusing to guess page boundaries"
            )
        split_segments += n // 15 - 1
        for start in range(0, n, 15):
            pages.append(segment[start:start + 15])

    if len(pages) != 604:
        raise RuntimeError(f"{doc_path.name}: reconstructed {len(pages)} pages; expected 604")
    if max(len(page) for page in pages) > 15:
        raise RuntimeError(f"{doc_path.name}: reconstructed page exceeds 15 formatted lines")

    return pages, {
        "raw_beginpage_count": len(raw_segments) - 1,
        "raw_page_segments": len(raw_segments),
        "split_missing_page_breaks": split_segments,
        "formatted_nonempty_lines": len(formatted_lines),
    }


def load_sqlite_words(sqlite_path: Path) -> tuple[list[dict], dict[int, list[dict]]]:
    conn = sqlite3.connect(sqlite_path)
    try:
        rows = conn.execute("SELECT surah, ayah, text FROM ayat ORDER BY surah, ayah").fetchall()
    finally:
        conn.close()

    global_words: list[dict] = []
    by_surah: dict[int, list[dict]] = defaultdict(list)
    for surah, ayah, text in rows:
        word_index = 0
        for raw in re.split(r"\s+", text.strip()):
            raw = raw.strip()
            if not raw:
                continue
            # SQLite exports sometimes attach Arabic verse numbers to the last
            # word of an ayah (e.g. "المشركين١٢٣"). Treat those numbers exactly
            # like DOC markers: they are metadata, not lexical words.
            for token, _marker in split_token(raw):
                token = token.replace("۞", "").strip()
                if not token:
                    continue
                word_index += 1
                item = {
                    "surah": int(surah),
                    "ayah": int(ayah),
                    "word": token,
                    "wordIndex": word_index,
                    "canonical": canonical(token),
                }
                global_words.append(item)
                by_surah[int(surah)].append(item)

    if not global_words:
        raise RuntimeError(f"SQLite contains no Quran words: {sqlite_path}")
    return global_words, by_surah


def endpoint_from_word(item: dict) -> dict:
    return {
        "type": "word",
        "surah": item["surah"],
        "ayah": item["ayah"],
        "word": item["word"],
        "wordIndex": item["wordIndex"],
    }


def marker_endpoint(surah: int, ayah: int) -> dict:
    return {
        "type": "ayah_marker",
        "surah": surah,
        "ayah": ayah,
        "number": ayah,
    }


def match_token_to_words(token: str, words: list[dict], cursor: int) -> tuple[list[dict], int]:
    wanted = canonical(token)
    if not wanted:
        return [], cursor
    wanted_loose = canonical_loose(token)
    acc = ""
    acc_loose = ""
    matches: list[dict] = []
    i = cursor
    while i < len(words) and len(acc_loose) <= len(wanted_loose):
        item = words[i]
        acc += item["canonical"]
        acc_loose += canonical_loose(item["word"])
        matches.append(item)
        i += 1
        if acc == wanted:
            return matches, i
        if wanted_loose and acc_loose == wanted_loose:
            return matches, i
        if not wanted_loose.startswith(acc_loose):
            break
    context = " ".join(x["word"] for x in words[cursor:min(cursor + 6, len(words))])
    raise RuntimeError(
        f"Unable to align DOC token {token!r} with SQLite sequence at {context!r}"
    )


def build_from_sources(doc_path: Path, sqlite_path: Path, config: dict) -> dict:
    pages, source_meta = extract_doc_pages(doc_path)
    global_words, _ = load_sqlite_words(sqlite_path)

    qiraah = config.get("qiraah", "unknown")
    source_font = config.get("font_family")

    out_pages: dict[int, dict] = {}
    surahs: dict[int, dict] = {}
    current_surah = 0
    cursor = 0

    for page_no, page_lines in enumerate(pages, 1):
        page_obj = {"lines": [], "surah_ids": []}
        line_no = 0

        for raw_line in page_lines:
            line = raw_line.strip()
            if not line:
                continue

            if is_heading(line):
                current_surah += 1
                if current_surah > 114:
                    raise RuntimeError(f"{doc_path.name}: detected more than 114 surah headings")
                surahs[current_surah] = {
                    "surahId": current_surah,
                    "surahName": is_heading(line) and SURAH_HEADING_RE.match(line).group(1).strip(),
                    "pages": [],
                }
                page_obj["surah_ids"].append(current_surah)
                continue

            if is_bismillah(line):
                if current_surah and current_surah not in page_obj["surah_ids"]:
                    page_obj["surah_ids"].append(current_surah)
                continue

            if current_surah == 0:
                raise RuntimeError(f"{doc_path.name}: Quran line encountered before the first surah heading")

            # DOC whitespace is not a reliable word boundary: some sources
            # split one SQLite word into several DOC tokens (e.g. "وَمَا لِيَ"),
            # while other places join several SQLite words into one DOC token.
            # Align each contiguous lexical segment between verse markers as one
            # character stream against consecutive SQLite words.
            line_tokens: list[tuple[str, int | None]] = []
            for raw_token in re.split(r"\s+", line):
                line_tokens.extend(split_token(raw_token))

            line_matches: list[dict] = []
            marker_events: list[tuple[int, int, bool, dict | None]] = []
            segment_parts: list[str] = []

            def consume_segment(parts: list[str]) -> None:
                nonlocal cursor
                if not parts:
                    return
                segment_text = "".join(parts)
                if not segment_text:
                    return
                matched, new_cursor = match_token_to_words(segment_text, global_words, cursor)
                for item in matched:
                    if item["surah"] != current_surah:
                        raise RuntimeError(
                            f"Page {page_no}: DOC surah {current_surah} diverges from SQLite at {item['surah']}:{item['ayah']}"
                        )
                line_matches.extend(matched)
                cursor = new_cursor

            for token_idx, (token, maybe_marker) in enumerate(line_tokens):
                if token:
                    segment_parts.append(token)
                if maybe_marker is not None:
                    before_count = len(line_matches)
                    consume_segment(segment_parts)
                    segment_parts = []
                    if len(line_matches) > before_count:
                        marker_source = line_matches[-1]
                    elif cursor > 0:
                        marker_source = global_words[cursor - 1]
                    else:
                        marker_source = None
                    marker_events.append(
                        (maybe_marker, token_idx, token_idx == len(line_tokens) - 1, marker_source)
                    )

            consume_segment(segment_parts)

            if not line_matches:
                raise RuntimeError(f"Page {page_no}: line contains no SQLite words: {line!r}")

            # The legacy DOC's Arabic verse-number glyphs are not a reliable
            # semantic source: in RTL extraction their numeric token can move
            # relative to the surrounding text. Use the SQLite word sequence as
            # the authority for the actual ayah boundary. A terminal DOC marker
            # therefore becomes a marker for the ayah of the final matched word.
            first_item = line_matches[0]
            last_item = line_matches[-1]
            end_ep = endpoint_from_word(last_item)
            if marker_events and marker_events[-1][2]:
                end_ep = marker_endpoint(current_surah, last_item["ayah"])

            line_no += 1
            line_obj = {
                "line": line_no,
                "start": endpoint_from_word(first_item),
                "end": end_ep,
                "first_word": endpoint_from_word(first_item),
                "end_word": end_ep,
            }
            page_obj["lines"].append(line_obj)
            if current_surah not in page_obj["surah_ids"]:
                page_obj["surah_ids"].append(current_surah)

        out_pages[page_no] = page_obj
        for sid in page_obj["surah_ids"]:
            surahs[sid]["pages"].append(page_no)

    if current_surah != 114:
        raise RuntimeError(f"{doc_path.name}: detected {current_surah} surahs; expected 114")
    if cursor != len(global_words):
        remaining = global_words[cursor:min(cursor + 5, len(global_words))]
        raise RuntimeError(
            f"{doc_path.name}: DOC ended before consuming all SQLite words; remaining={len(global_words)-cursor}, next={remaining}"
        )

    for sid, surah in surahs.items():
        bucket = []
        for page_no in surah["pages"]:
            lines = out_pages[page_no]["lines"]
            # Keep only lines whose endpoints belong to this surah.
            filtered = [
                line for line in lines
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
        "qiraah": qiraah,
        "font_family": source_font,
        "source": {
            "doc": doc_path.name,
            "sqlite": sqlite_path.name,
            "sqlite_page_field_used": False,
            **source_meta,
        },
        "total_pages": len(out_pages),
        "total_surahs": len(surahs),
        "pages": out_pages,
        "surahs": surahs,
    }


def write_outputs(result: dict, output_dir: Path) -> None:
    pages_dir = output_dir / "pages"
    surahs_dir = output_dir / "surahs"
    pages_dir.mkdir(parents=True, exist_ok=True)
    surahs_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema_version": result["schema_version"],
        "layout_source": result["layout_source"],
        "qiraah": result["qiraah"],
        "font_family": result["font_family"],
        "source": result["source"],
        "total_pages": result["total_pages"],
        "total_surahs": result["total_surahs"],
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for page_no in range(1, result["total_pages"] + 1):
        page_data = result["pages"][page_no]
        payload = {
            "schema_version": 3,
            "page": page_no,
            "surah_ids": page_data["surah_ids"],
            "font_family": result["font_family"],
            "layout_source": result["layout_source"],
            "lines": [
                {
                    "line": line["line"],
                    "first_word": line["first_word"],
                    "end_word": line["end_word"],
                }
                for line in page_data["lines"]
            ],
        }
        (pages_dir / f"page_{page_no:03d}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for sid in range(1, result["total_surahs"] + 1):
        surah = result["surahs"][sid]
        payload = {
            "schema_version": 3,
            "surahId": sid,
            "surahName": surah.get("surahName"),
            "startPage": surah.get("startPage"),
            "endPage": surah.get("endPage"),
            "totalPagesInSurah": surah.get("totalPagesInSurah", 0),
            "totalCompletedLines": surah.get("totalCompletedLines", 0),
            "ayaCount": surah.get("ayaCount", 0),
            "font_family": result["font_family"],
            "layout_source": result["layout_source"],
            "pages": [
                {
                    "page": p["page"],
                    "lines": [
                        {
                            "line": line["line"],
                            "first_word": line["first_word"],
                            "end_word": line["end_word"],
                        }
                        for line in p["lines"]
                    ],
                }
                for p in surah["pages"]
            ],
        }
        (surahs_dir / f"{sid:03d}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
        raise RuntimeError("Source reconstruction did not produce 604 pages and 114 surahs")
    for page_no, page in result["pages"].items():
        if len(page["lines"]) > 15:
            raise RuntimeError(f"Page {page_no}: more than 15 Quran lines")
        if not page["lines"]:
            raise RuntimeError(f"Page {page_no}: no Quran lines")

    write_outputs(result, args.output_dir)
    print(
        f"OK: generated DOC+SQLite layout for {result['qiraah']}: "
        f"{result['total_pages']} pages, {result['total_surahs']} surahs; "
        f"raw_beginpages={result['source']['raw_beginpage_count']}, "
        f"split_missing_page_breaks={result['source']['split_missing_page_breaks']}"
    )
    for pno in (1, 2, 34, 35, 598, 604):
        print(f"  page {pno}: {len(result['pages'][pno]['lines'])} Quran lines; surahs={result['pages'][pno]['surah_ids']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
