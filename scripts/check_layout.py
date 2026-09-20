#!/usr/bin/env python3
"""Validate DOCX-only page and surah layout JSON output."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", type=Path, default=Path("output"))
    args = ap.parse_args()
    pages_dir = args.output_dir / "pages"
    surahs_dir = args.output_dir / "surahs"

    page_files = sorted(pages_dir.glob("page_*.json"))
    surah_files = sorted(surahs_dir.glob("*.json"))
    if len(page_files) != 604:
        raise RuntimeError(f"Expected 604 page JSON files, found {len(page_files)}")
    if len(surah_files) != 114:
        raise RuntimeError(f"Expected 114 surah JSON files, found {len(surah_files)}")

    counts = {}
    total_lines = 0
    fonts = set()
    for f in page_files:
        data = json.loads(f.read_text(encoding="utf-8"))
        page = int(data["page"])
        if page != len(counts) + 1:
            raise RuntimeError(f"Unexpected page sequence at {f.name}")
        lines = data.get("lines", [])
        counts[page] = len(lines)
        total_lines += len(lines)
        fonts.add(data.get("font_family"))
        if len(lines) > 15:
            raise RuntimeError(f"Page {page} has {len(lines)} lines")
        for line in lines:
            for key in ("line", "text", "start", "end", "alignment", "runs"):
                if key not in line:
                    raise RuntimeError(f"{f.name}: missing {key}")
            for edge in (line["start"], line["end"]):
                if edge.get("type") not in ("word", "ayah_marker"):
                    raise RuntimeError(f"{f.name}: invalid endpoint type")
                if edge.get("surah") is None or edge.get("ayah") is None:
                    raise RuntimeError(f"{f.name}: endpoint missing surah/ayah")
            for run in line["runs"]:
                if run.get("font_family") not in fonts and run.get("font_family"):
                    pass
    if len(fonts) != 1:
        raise RuntimeError(f"Expected exactly one rendered font family, found {fonts}")
    if "KFGQPC HAFS Uthmanic Script" not in fonts and "KFGQPC QALOON Uthmanic Script" not in fonts:
        raise RuntimeError(f"Unexpected font family: {fonts}")

    print(f"Validation OK: 604 pages, 114 surahs, {total_lines} Quran lines, font={next(iter(fonts))}")
    for p in (1, 2, 440, 598, 604):
        print(f"  page {p}: {counts[p]} Quran lines")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
