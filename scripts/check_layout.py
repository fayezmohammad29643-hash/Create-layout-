#!/usr/bin/env python3
"""Validate DOCX-only page and surah layout JSON output."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ALLOWED_FONTS = {
    "KFGQPC HAFS Uthmanic Script",
    "KFGQPC QALOON Uthmanic Script",
}
EXPECTED_PAGES = 604
EXPECTED_SURAHS = 114


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Invalid JSON: {path}: {exc}") from exc


def validate_endpoint(endpoint: dict, location: str) -> None:
    if endpoint.get("type") not in ("word", "ayah_marker"):
        raise RuntimeError(f"{location}: invalid endpoint type")
    if endpoint.get("surah") is None or endpoint.get("ayah") is None:
        raise RuntimeError(f"{location}: endpoint missing surah/ayah")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", type=Path, default=Path("output"))
    args = ap.parse_args()

    output_dir = args.output_dir
    manifest_path = output_dir / "manifest.json"
    pages_dir = output_dir / "pages"
    surahs_dir = output_dir / "surahs"

    if not manifest_path.is_file():
        raise RuntimeError("Missing output/manifest.json")
    if not pages_dir.is_dir():
        raise RuntimeError("Missing output/pages/")
    if not surahs_dir.is_dir():
        raise RuntimeError("Missing output/surahs/")

    manifest = read_json(manifest_path)
    if manifest.get("schema_version") != 3:
        raise RuntimeError(f"Manifest schema_version={manifest.get('schema_version')}, expected 3")
    if manifest.get("total_pages") != EXPECTED_PAGES:
        raise RuntimeError(f"Manifest total_pages={manifest.get('total_pages')}, expected {EXPECTED_PAGES}")
    if manifest.get("total_surahs") != EXPECTED_SURAHS:
        raise RuntimeError(f"Manifest total_surahs={manifest.get('total_surahs')}, expected {EXPECTED_SURAHS}")
    manifest_font = manifest.get("font_family")
    if manifest_font not in ALLOWED_FONTS:
        raise RuntimeError(f"Unexpected manifest font: {manifest_font}")

    page_files = sorted(pages_dir.glob("page_*.json"))
    surah_files = sorted(surahs_dir.glob("*.json"))
    if len(page_files) != EXPECTED_PAGES:
        raise RuntimeError(f"Expected {EXPECTED_PAGES} page JSON files, found {len(page_files)}")
    if len(surah_files) != EXPECTED_SURAHS:
        raise RuntimeError(f"Expected {EXPECTED_SURAHS} surah JSON files, found {len(surah_files)}")

    counts: dict[int, int] = {}
    total_lines = 0

    for expected_page, path in enumerate(page_files, start=1):
        data = read_json(path)
        page = data.get("page")
        if page != expected_page:
            raise RuntimeError(f"Unexpected page sequence: {path.name} has page={page}, expected {expected_page}")
        if data.get("layout_source") != "docx":
            raise RuntimeError(f"{path.name}: layout_source must be docx")
        if data.get("font_family") != manifest_font:
            raise RuntimeError(f"{path.name}: font_family differs from manifest")

        lines = data.get("lines")
        if not isinstance(lines, list):
            raise RuntimeError(f"{path.name}: lines must be a list")
        if len(lines) > 15:
            raise RuntimeError(f"{path.name}: has {len(lines)} Quran lines (maximum 15)")

        counts[page] = len(lines)
        total_lines += len(lines)

        for idx, line in enumerate(lines, start=1):
            location = f"{path.name} line {idx}"
            required = ("line", "first_word", "end_word")
            for key in required:
                if key not in line:
                    raise RuntimeError(f"{location}: missing {key}")

            forbidden = ("text", "start", "end", "alignment", "bidi", "spacing", "runs", "verses_on_line")
            present_forbidden = [key for key in forbidden if key in line]
            if present_forbidden:
                raise RuntimeError(f"{location}: schema-v3 line contains removed fields: {present_forbidden}")

            if not isinstance(line["line"], int) or line["line"] < 1:
                raise RuntimeError(f"{location}: line must be a positive integer")

            validate_endpoint(line["first_word"], f"{location} first_word")
            validate_endpoint(line["end_word"], f"{location} end_word")

    for expected_sid in range(1, EXPECTED_SURAHS + 1):
        path = surahs_dir / f"{expected_sid:03d}.json"
        if not path.is_file():
            raise RuntimeError(f"Missing surah file: {path.name}")
        data = read_json(path)
        if data.get("surahId") != expected_sid:
            raise RuntimeError(f"{path.name}: surahId={data.get('surahId')}, expected {expected_sid}")
        if data.get("layout_source") != "docx":
            raise RuntimeError(f"{path.name}: layout_source must be docx")
        if data.get("font_family") != manifest_font:
            raise RuntimeError(f"{path.name}: font_family differs from manifest")
        if not isinstance(data.get("pages"), list):
            raise RuntimeError(f"{path.name}: pages must be a list")

    print(
        f"Validation OK: {EXPECTED_PAGES} pages, {EXPECTED_SURAHS} surahs, "
        f"{total_lines} Quran lines, font={manifest_font}"
    )
    for p in (1, 2, 440, 598, 604):
        print(f"  page {p}: {counts[p]} Quran lines")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
