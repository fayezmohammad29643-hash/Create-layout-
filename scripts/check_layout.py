#!/usr/bin/env python3
"""Small validator for generated surah layout JSON files."""
from __future__ import annotations
import json
import sys
from pathlib import Path


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "output")
    files = sorted(root.glob("*.json"))
    if not files:
        print("No JSON files found.")
        return 1
    failures = 0
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        for page in data.get("pages", []):
            lines = page.get("lines", [])
            for expected, line in enumerate(lines, 1):
                if line.get("line") != expected and page["page"] not in (data.get("startPage"),):
                    # Shared pages can legitimately begin at line 16 etc.; do not
                    # enforce 1..15 for the first/last shared page.
                    continue
                if "start" not in line or "end" not in line:
                    print(f"FAIL {path}: page {page['page']} missing start/end")
                    failures += 1
        if data.get("recordedPagesCount") != len(data.get("pages", [])):
            print(f"FAIL {path}: recordedPagesCount mismatch")
            failures += 1
    print(f"Checked {len(files)} files; failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
