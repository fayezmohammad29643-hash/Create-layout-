#!/usr/bin/env python3
"""Strict validator for the single runtime ayah_mapping.sqlite file."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

EXPECTED = {
    1: ("hafs-6236", 6236),
    2: ("warsh-6213", 6213),
    3: ("qalun-6214", 6214),
    4: ("douri-6217", 6217),
}


def fail(message: str) -> None:
    raise SystemExit(f"VALIDATION FAILED: {message}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=Path("output/ayah_mapping/ayah_mapping.sqlite"))
    args = ap.parse_args()
    db = args.db.resolve()
    if not db.is_file():
        fail(f"missing database: {db}")

    with sqlite3.connect(db) as con:
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            fail("SQLite integrity_check failed")

        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"metadata", "editions", "ayah_hafs_map"}.issubset(tables):
            fail("required tables are missing")

        editions = {
            int(row[0]): (row[1], int(row[3]))
            for row in con.execute("SELECT edition_id, edition_key, rawi, ayah_count, reference FROM editions")
        }
        if editions != {k: (v[0], v[1]) for k, v in EXPECTED.items()}:
            fail(f"unexpected editions: {editions}")

        # Every project ayah must have at least one Hafs reference.
        for edition_id, (key, expected_total) in EXPECTED.items():
            source_total = con.execute(
                "SELECT COUNT(DISTINCT surah || ':' || ayah) FROM ayah_hafs_map WHERE edition_id=?",
                (edition_id,),
            ).fetchone()[0]
            if int(source_total) != expected_total:
                fail(f"{key}: {source_total} source ayahs, expected {expected_total}")

            for surah in range(1, 115):
                rows = con.execute(
                    "SELECT ayah, hafs_ayah FROM ayah_hafs_map WHERE edition_id=? AND surah=? ORDER BY ayah, hafs_ayah",
                    (edition_id, surah),
                ).fetchall()
                if not rows:
                    fail(f"{key}: missing surah {surah}")
                by_ayah = {}
                for ayah, hafs_ayah in rows:
                    by_ayah.setdefault(int(ayah), []).append(int(hafs_ayah))
                last = 0
                for ayah, refs in by_ayah.items():
                    if refs != list(range(min(refs), max(refs) + 1)):
                        fail(f"{key} {surah}:{ayah}: non-contiguous Hafs refs {refs}")
                    if min(refs) < last:
                        fail(f"{key} {surah}:{ayah}: non-monotonic Hafs mapping")
                    last = max(refs)

                hafs_count = con.execute(
                    "SELECT COUNT(*) FROM ayah_hafs_map WHERE edition_id=1 AND surah=?", (surah,)
                ).fetchone()[0]
                expected_refs = set(range(2, 8)) if (edition_id != 1 and surah == 1) else set(range(1, hafs_count + 1))
                actual_refs = {int(h) for _, h in rows}
                if actual_refs != expected_refs:
                    missing = sorted(expected_refs - actual_refs)
                    extra = sorted(actual_refs - expected_refs)
                    fail(f"{key} surah {surah}: target coverage mismatch; missing={missing}, extra={extra}")

        # Hafs must be identity.
        bad = con.execute(
            "SELECT surah, ayah, hafs_ayah FROM ayah_hafs_map WHERE edition_id=1 AND (ayah != hafs_ayah) LIMIT 1"
        ).fetchone()
        if bad:
            fail(f"Hafs identity mapping broken: {bad}")

        # Critical anchors for intended semantics.
        def refs(edition_id: int, surah: int, ayah: int) -> list[int]:
            return [
                int(r[0])
                for r in con.execute(
                    "SELECT hafs_ayah FROM ayah_hafs_map WHERE edition_id=? AND surah=? AND ayah=? ORDER BY hafs_ayah",
                    (edition_id, surah, ayah),
                ).fetchall()
            ]

        # Edition IDs: Warsh=2, Qalun=3, Douri=4.
        expected_anchors = {
            (2, 1, 1): [2],      # Warsh Fatihah: الحمد... = Hafs 1:2.
            (3, 1, 1): [2],      # Qalun Fatihah.
            (4, 1, 1): [2],      # Douri Fatihah.
            (2, 1, 6): [7],      # Warsh split of Hafs Fatihah 1:7.
            (2, 1, 7): [7],
            (3, 1, 6): [7],
            (3, 1, 7): [7],
            (4, 1, 6): [7],
            (4, 1, 7): [7],
            (2, 2, 1): [1, 2],   # Al-Baqarah opening merged.
            (3, 2, 1): [1, 2],
            (4, 2, 1): [1, 2],
            (2, 16, 123): [123, 124],  # Project Warsh 6213 Nahl edition.
            (2, 11, 82): [82, 83],
            (2, 56, 52): [49, 50],
            (4, 2, 218): [219, 220],
            (4, 20, 88): [88, 89],
            (4, 40, 71): [71, 72],
            (4, 71, 24): [23, 24],
            (4, 67, 9): [9],    # Project Douri 6217 printed boundary.
        }
        for key, expected_refs in expected_anchors.items():
            actual = refs(*key)
            if actual != expected_refs:
                fail(f"anchor {key}: expected {expected_refs}, got {actual}")

    print(f"VALIDATION OK: {db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
