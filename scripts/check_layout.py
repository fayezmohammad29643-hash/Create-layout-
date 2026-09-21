#!/usr/bin/env python3
"""Validate the compact schema-v3 Qalun DOCX layout output."""
from __future__ import annotations
import argparse, json
from pathlib import Path

EXPECTED_PAGES = 604
EXPECTED_SURAHS = 114
EXPECTED_LINES = 8817
EXPECTED_FONT = "KFGQPC QALOON Uthmanic Script"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_endpoint(ep: dict, where: str) -> None:
    if not isinstance(ep, dict):
        raise RuntimeError(f"{where}: endpoint must be an object")
    if ep.get("type") not in ("word", "ayah_marker"):
        raise RuntimeError(f"{where}: invalid endpoint type")
    for key in ("surah", "ayah"):
        if key not in ep:
            raise RuntimeError(f"{where}: missing {key}")
    if ep["type"] == "word" and ("word" not in ep or "wordIndex" not in ep):
        raise RuntimeError(f"{where}: word endpoint missing word/wordIndex")
    if ep["type"] == "ayah_marker" and "number" not in ep:
        raise RuntimeError(f"{where}: marker endpoint missing number")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", type=Path, default=Path("output"))
    args = ap.parse_args()
    out = args.output_dir
    manifest = read_json(out / "manifest.json")
    if manifest.get("schema_version") != 3 or manifest.get("layout_source") != "docx":
        raise RuntimeError("Manifest schema/layout_source mismatch")
    if manifest.get("font_family") != EXPECTED_FONT:
        raise RuntimeError(f"Unexpected font family: {manifest.get('font_family')!r}")
    if manifest.get("total_pages") != EXPECTED_PAGES or manifest.get("total_surahs") != EXPECTED_SURAHS:
        raise RuntimeError("Manifest page/surah counts are incorrect")

    pages = sorted((out / "pages").glob("page_*.json"))
    surahs = sorted((out / "surahs").glob("*.json"))
    if len(pages) != EXPECTED_PAGES:
        raise RuntimeError(f"Expected {EXPECTED_PAGES} page files, found {len(pages)}")
    if len(surahs) != EXPECTED_SURAHS:
        raise RuntimeError(f"Expected {EXPECTED_SURAHS} surah files, found {len(surahs)}")

    total_lines = 0
    for expected_page, path in enumerate(pages, 1):
        data = read_json(path)
        if data.get("schema_version") != 3 or data.get("layout_source") != "docx":
            raise RuntimeError(f"{path.name}: wrong schema/layout_source")
        if data.get("page") != expected_page:
            raise RuntimeError(f"{path.name}: wrong page number")
        if data.get("font_family") != EXPECTED_FONT:
            raise RuntimeError(f"{path.name}: wrong font")
        lines = data.get("lines")
        if not isinstance(lines, list) or not lines:
            raise RuntimeError(f"{path.name}: lines must be non-empty")
        if len(lines) > 15:
            raise RuntimeError(f"{path.name}: more than 15 Quran lines")
        for idx, line in enumerate(lines, 1):
            if set(line.keys()) != {"line", "first_word", "end_word"}:
                raise RuntimeError(f"{path.name} line {idx}: non-compact fields detected: {sorted(line)}")
            if line["line"] != idx:
                raise RuntimeError(f"{path.name} line {idx}: wrong line number")
            validate_endpoint(line["first_word"], f"{path.name} line {idx} first_word")
            validate_endpoint(line["end_word"], f"{path.name} line {idx} end_word")
        total_lines += len(lines)

    for sid in range(1, EXPECTED_SURAHS + 1):
        path = out / "surahs" / f"{sid:03d}.json"
        if not path.is_file():
            raise RuntimeError(f"Missing surah file {path.name}")
        data = read_json(path)
        if data.get("schema_version") != 3 or data.get("layout_source") != "docx":
            raise RuntimeError(f"{path.name}: wrong schema/layout_source")
        if data.get("surahId") != sid:
            raise RuntimeError(f"{path.name}: wrong surahId")
        if data.get("font_family") != EXPECTED_FONT:
            raise RuntimeError(f"{path.name}: wrong font")

    if total_lines != EXPECTED_LINES:
        raise RuntimeError(f"Total Quran lines={total_lines}, expected {EXPECTED_LINES}")

    print(f"Validation OK: {EXPECTED_PAGES} pages, {EXPECTED_SURAHS} surahs, {total_lines} Quran lines, schema=3, font={EXPECTED_FONT}")
    for p in (1, 2, 440, 598, 604):
        d = read_json(out / "pages" / f"page_{p:03d}.json")
        print(f"  page {p}: {len(d['lines'])} Quran lines")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)
