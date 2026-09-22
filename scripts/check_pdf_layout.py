#!/usr/bin/env python3
"""Validate PDF-based schema-v3 layout output and optional strict review gate."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

EXPECTED_PAGES = 604
EXPECTED_SURAHS = 114


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def expected_page_tokens(db: Path, page: int) -> list[dict]:
    con = sqlite3.connect(db)
    try:
        rows = con.execute("SELECT surah, ayah, text FROM ayat WHERE page=? ORDER BY surah, ayah", (page,)).fetchall()
    finally:
        con.close()
    tokens = []
    for surah, ayah, text in rows:
        words = [w.replace("۞", "").strip() for w in re.split(r"\s+", (text or "").strip()) if w.replace("۞", "").strip()]
        for i, word in enumerate(words, 1):
            tokens.append((int(surah), int(ayah), i, word))
        tokens.append((int(surah), int(ayah), None, None))
    return tokens


def endpoint_key(ep: dict) -> tuple:
    if ep.get("type") == "ayah_marker":
        return (int(ep["surah"]), int(ep["ayah"]), None)
    return (int(ep["surah"]), int(ep["ayah"]), int(ep["wordIndex"]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", type=Path, default=Path("output"))
    ap.add_argument("--sqlite", type=Path, default=Path("input/quran_Al-Douri.sqlite"))
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    out = args.output_dir
    manifest = read_json(out / "manifest.json")
    if manifest.get("schema_version") != 3 or manifest.get("layout_source") != "pdf":
        raise RuntimeError("Manifest schema/layout_source mismatch")
    if int(manifest.get("pdf_alignment_version", 0)) < 4:
        raise RuntimeError("Manifest uses an older PDF alignment algorithm; rebuild with PDF alignment v4")
    expected_qiraah = manifest.get("source_qiraah_expected")
    detected_qiraah = manifest.get("source_qiraah_detected")
    if expected_qiraah and detected_qiraah and expected_qiraah != detected_qiraah:
        raise RuntimeError(
            f"Source qiraah mismatch in manifest: expected {expected_qiraah}, detected {detected_qiraah}"
        )
    if manifest.get("total_pages") != EXPECTED_PAGES or manifest.get("total_surahs") != EXPECTED_SURAHS:
        raise RuntimeError("Manifest page/surah counts are incorrect")

    pages = sorted((out / "pages").glob("page_*.json"))
    surahs = sorted((out / "surahs").glob("*.json"))
    if len(pages) != EXPECTED_PAGES:
        raise RuntimeError(f"Expected {EXPECTED_PAGES} page JSON files, found {len(pages)}")
    if len(surahs) != EXPECTED_SURAHS:
        raise RuntimeError(f"Expected {EXPECTED_SURAHS} surah JSON files, found {len(surahs)}")

    total_lines = 0
    for expected_page, path in enumerate(pages, 1):
        data = read_json(path)
        if data.get("page") != expected_page:
            raise RuntimeError(f"{path.name}: wrong page number")
        if data.get("schema_version") != 3 or data.get("layout_source") != "pdf":
            raise RuntimeError(f"{path.name}: wrong schema/layout_source")
        lines = data.get("lines")
        if not isinstance(lines, list) or not lines:
            raise RuntimeError(f"{path.name}: lines must be a non-empty list")
        if len(lines) > 20:
            raise RuntimeError(f"{path.name}: more than 20 Quran lines")
        tokens = expected_page_tokens(args.sqlite, expected_page)
        cursor = 0
        for idx, line in enumerate(lines, 1):
            if line.get("line") != idx:
                raise RuntimeError(f"{path.name}: line numbering is not contiguous")
            for key in ("first_word", "end_word"):
                ep = line.get(key)
                if not isinstance(ep, dict):
                    raise RuntimeError(f"{path.name} line {idx}: missing {key}")
            start = endpoint_key(line["first_word"])
            end = endpoint_key(line["end_word"])
            pos_start = next((i for i, t in enumerate(tokens[cursor:], cursor) if t[:3] == start), None)
            if pos_start is None:
                raise RuntimeError(f"{path.name} line {idx}: first endpoint not found in page token sequence")
            pos_end = next((i for i, t in enumerate(tokens[pos_start:], pos_start) if t[:3] == end), None)
            if pos_end is None or pos_end < pos_start:
                raise RuntimeError(f"{path.name} line {idx}: invalid end endpoint")
            cursor = pos_end + 1
            total_lines += 1
        if cursor != len(tokens):
            raise RuntimeError(f"{path.name}: endpoints do not consume the full SQLite token sequence ({cursor}/{len(tokens)})")

    review = read_json(out / "review.json").get("pages", []) if (out / "review.json").exists() else []
    if args.strict and review:
        raise RuntimeError(f"Strict validation failed: {len(review)} pages require review")

    print(f"Validation OK: {EXPECTED_PAGES} pages, {EXPECTED_SURAHS} surahs, {total_lines} Quran lines")
    print(f"Review pages: {len(review)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)
