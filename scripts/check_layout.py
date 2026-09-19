#!/usr/bin/env python3
"""Validate generated per-surah Quran layout JSON files."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", type=Path, default=Path("output"))
    args = ap.parse_args()
    files = sorted(args.output_dir.glob("*.json"))
    if len(files) != 114:
        raise RuntimeError(f"Expected 114 surah JSON files, found {len(files)}")
    pages = {}
    total_lines = 0
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(data.get("surahId"), int) or not data.get("surahName"):
            raise RuntimeError(f"Invalid surah metadata: {f.name}")
        for p in data.get("pages", []):
            page_no = int(p["page"])
            line_count = len(p.get("lines", []))
            pages[page_no] = pages.get(page_no, 0) + line_count
            total_lines += line_count
            for line in p.get("lines", []):
                for key in ("line", "start", "end", "y_top", "y_bottom", "x_start", "x_end"):
                    if key not in line:
                        raise RuntimeError(f"{f.name}: page {page_no} missing line field {key}")
                if line["y_top"] is None or line["x_start"] is None:
                    raise RuntimeError(f"{f.name}: page {page_no} line {line['line']} has empty coordinates")
    if set(pages) != set(range(1, 605)):
        missing = sorted(set(range(1, 605)) - set(pages))
        extra = sorted(set(pages) - set(range(1, 605)))
        raise RuntimeError(f"Pages mismatch. Missing={missing[:20]} Extra={extra[:20]}")
    bad_range = {p: c for p, c in pages.items() if c < 1 or c > 15}
    if bad_range:
        sample = list(bad_range.items())[:20]
        raise RuntimeError(f"Invalid Quran-line count on page(s): {sample}")
    print(f"Validation OK: 114 files, 604 pages, {total_lines} Quran lines")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
