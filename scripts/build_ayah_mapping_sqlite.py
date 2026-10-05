#!/usr/bin/env python3
"""Build the single Miltazim ayah-reference SQLite database.

Method:
- Hafs/Kufi is the reference numbering.
- For Warsh, Qalun, and Douri, match the actual project SQLite ayah texts
  against Hafs by character-level alignment after conservative normalization.
- A source ayah may produce multiple rows when it spans multiple Hafs ayahs.
- No fixed numeric offset and no external generated mapping is used.

Runtime database:
  ayah_mapping.sqlite

Schema:
  metadata
  editions
  ayah_hafs_map
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import unicodedata
from pathlib import Path

from rapidfuzz.distance import Levenshtein

EDITIONS = (
    (1, "hafs-6236", "hafs", "input/hafs/quran.sqlite", 6236),
    (2, "warsh-6213", "warsh", "input/warsh/quran.sqlite", 6213),
    (3, "qalun-6214", "qalun", "input/qalun/quran.sqlite", 6214),
    (4, "douri-6217", "douri", "input/douri/quran.sqlite", 6217),
)

MARKS_RE = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
NON_ARABIC_RE = re.compile(r"[^\u0621-\u063A\u0641-\u064A]")
TRANSLATION = str.maketrans(
    {
        "ٱ": "ا",
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٲ": "ا",
        "ٳ": "ا",
        "ٵ": "ا",
        "ى": "ي",
        "ئ": "ي",
        "ؤ": "و",
        "ء": "ا",
    }
)

NORMALIZATION_VERSION = "arabic-nfkc-marks-v1"
MIN_TARGET_COVERAGE = 0.50
MIN_SPAN_COVERAGE = 0.00


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = MARKS_RE.sub("", text).replace("ـ", "")
    text = text.translate(TRANSLATION)
    return NON_ARABIC_RE.sub("", text)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_ayahs(db_path: Path, surah: int) -> list[tuple[int, str]]:
    with sqlite3.connect(db_path) as con:
        rows = con.execute(
            "SELECT ayah, text FROM ayat WHERE surah = ? ORDER BY ayah", (surah,)
        ).fetchall()
    return [(int(ayah), normalize_text(str(text))) for ayah, text in rows]


def load_counts(db_path: Path) -> dict[int, int]:
    with sqlite3.connect(db_path) as con:
        rows = con.execute(
            "SELECT surah, COUNT(*) FROM ayat GROUP BY surah ORDER BY surah"
        ).fetchall()
    counts = {int(s): int(c) for s, c in rows}
    if set(counts) != set(range(1, 115)):
        raise ValueError(f"{db_path}: expected exactly 114 surahs")
    return counts


def align_source_to_hafs(
    source_rows: list[tuple[int, str]],
    hafs_rows: list[tuple[int, str]],
    *,
    skip_hafs_first_ayah: bool,
) -> list[tuple[int, list[int]]]:
    """Return (source_ayah, [hafs_ayah...]) using text-span alignment."""
    target_rows = hafs_rows[1:] if skip_hafs_first_ayah else hafs_rows
    if not source_rows or not target_rows:
        raise ValueError("Cannot align empty ayah sets")

    source = "".join(text for _, text in source_rows)
    target = "".join(text for _, text in target_rows)
    opcodes = Levenshtein.opcodes(source, target)

    source_spans: list[tuple[int, int, int, int]] = []
    cursor = 0
    for ayah, text in source_rows:
        start = cursor
        cursor += len(text)
        source_spans.append((ayah, start, cursor, len(text)))

    target_spans: list[tuple[int, int, int, int]] = []
    cursor = 0
    for ayah, text in target_rows:
        start = cursor
        cursor += len(text)
        target_spans.append((ayah, start, cursor, len(text)))

    def map_boundary(pos: int, *, end: bool) -> int:
        for op in opcodes:
            if op.src_start <= pos <= op.src_end:
                if op.tag == "equal":
                    return op.dest_start + (pos - op.src_start)
                if op.tag == "replace":
                    src_len = max(1, op.src_end - op.src_start)
                    fraction = (pos - op.src_start) / src_len
                    return op.dest_start + round(fraction * (op.dest_end - op.dest_start))
                if op.tag == "delete":
                    return op.dest_start
                if op.tag == "insert":
                    return op.dest_end if end else op.dest_start
        return len(target)

    results: list[tuple[int, list[int]]] = []
    for source_ayah, start, end, _ in source_spans:
        dest_start = map_boundary(start, end=False)
        dest_end = map_boundary(end, end=True)
        if dest_end < dest_start:
            dest_start, dest_end = dest_end, dest_start
        mapped_span = max(1, dest_end - dest_start)

        candidates: list[tuple[int, int, float, float]] = []
        for hafs_ayah, ts, te, target_len in target_spans:
            overlap = max(0, min(dest_end, te) - max(dest_start, ts))
            if overlap <= 0:
                continue
            target_coverage = overlap / max(1, target_len)
            span_coverage = overlap / mapped_span
            candidates.append((hafs_ayah, overlap, target_coverage, span_coverage))

        selected = []
        for hafs_ayah, overlap, target_coverage, span_coverage in candidates:
            # Keep a target ayah when its text substantially lies inside the
            # mapped span OR when the source ayah starts/ends inside that target.
            # The boundary rule is essential for split cases such as a source
            # ayah beginning with the tail of one Hafs ayah and continuing into the next.
            for target_no, ts, te, _target_len in target_spans:
                if target_no == hafs_ayah:
                    start_inside = ts <= dest_start < te
                    end_inside = ts < dest_end <= te
                    break
            else:
                start_inside = end_inside = False
            if target_coverage >= MIN_TARGET_COVERAGE or start_inside or end_inside:
                selected.append(hafs_ayah)

        if not selected and candidates:
            selected = [max(candidates, key=lambda row: row[1])[0]]

        if not selected:
            raise ValueError(f"No Hafs correspondence for source {source_ayah}")

        results.append((source_ayah, sorted(set(selected))))

    return results


def build(project_root: Path, output_path: Path, report_path: Path | None) -> dict:
    source_paths = {}
    source_counts: dict[str, dict[int, int]] = {}
    for _, key, _, rel, expected in EDITIONS:
        path = project_root / rel
        counts = load_counts(path)
        total = sum(counts.values())
        if total != expected:
            raise ValueError(f"{key}: expected {expected} ayahs, found {total}")
        source_paths[key] = path
        source_counts[key] = counts

    rows: list[tuple[int, int, int, int]] = []
    multi_refs: list[dict] = []

    # Hafs identity mapping.
    for surah in range(1, 115):
        for ayah in range(1, source_counts["hafs-6236"][surah] + 1):
            rows.append((1, surah, ayah, ayah))

    for edition_id, key, rawi, _, expected in EDITIONS[1:]:
        source_path = source_paths[key]
        for surah in range(1, 115):
            source_rows = load_ayahs(source_path, surah)
            hafs_rows = load_ayahs(source_paths["hafs-6236"], surah)
            mapping = align_source_to_hafs(
                source_rows,
                hafs_rows,
                skip_hafs_first_ayah=(surah == 1),
            )
            if len(mapping) != source_counts[key][surah]:
                raise ValueError(f"{key} {surah}: mapping count mismatch")
            for source_ayah, hafs_refs in mapping:
                for hafs_ayah in hafs_refs:
                    rows.append((edition_id, surah, source_ayah, hafs_ayah))
                if len(hafs_refs) > 1:
                    multi_refs.append(
                        {"edition": key, "surah": surah, "ayah": source_ayah, "hafs_ayahs": hafs_refs}
                    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    # Set page size before creating tables. No secondary index is needed because
    # the WITHOUT ROWID primary key starts with (edition_id, surah, ayah).
    con = sqlite3.connect(output_path)
    try:
        con.execute("PRAGMA page_size=1024")
        con.execute("PRAGMA auto_vacuum=NONE")
        con.executescript(
            """
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            ) WITHOUT ROWID;

            CREATE TABLE editions (
                edition_id INTEGER PRIMARY KEY,
                edition_key TEXT NOT NULL UNIQUE,
                rawi TEXT NOT NULL,
                ayah_count INTEGER NOT NULL,
                reference TEXT NOT NULL
            ) WITHOUT ROWID;

            CREATE TABLE ayah_hafs_map (
                edition_id INTEGER NOT NULL,
                surah INTEGER NOT NULL,
                ayah INTEGER NOT NULL,
                hafs_ayah INTEGER NOT NULL,
                PRIMARY KEY (edition_id, surah, ayah, hafs_ayah),
                FOREIGN KEY (edition_id) REFERENCES editions(edition_id)
            ) WITHOUT ROWID;
            """
        )

        metadata = {
            "schema_version": 1,
            "format": "miltazim-ayah-hafs-map-sqlite",
            "purpose": "Runtime lookup from all four project Quran editions to Hafs/Kufi-keyed tafsir and attachments.",
            "reference": "hafs-kufi",
            "algorithm": "character-span-alignment",
            "normalization": NORMALIZATION_VERSION,
            "source_method": "Match project SQLite ayah texts to Hafs; no numeric offsets and no external mapping tables.",
            "runtime_file": "ayah_mapping.sqlite",
            "multi_reference_rule": "Multiple rows with the same edition/surah/ayah represent multiple Hafs references.",
        }
        for k, v in metadata.items():
            con.execute("INSERT INTO metadata(key, value) VALUES (?, ?)", (k, str(v)))

        con.executemany(
            "INSERT INTO editions(edition_id, edition_key, rawi, ayah_count, reference) VALUES (?, ?, ?, ?, ?)",
            [
                (1, "hafs-6236", "hafs", 6236, "hafs-kufi"),
                (2, "warsh-6213", "warsh", 6213, "hafs-kufi"),
                (3, "qalun-6214", "qalun", 6214, "hafs-kufi"),
                (4, "douri-6217", "douri", 6217, "hafs-kufi"),
            ],
        )
        con.executemany(
            "INSERT INTO ayah_hafs_map(edition_id, surah, ayah, hafs_ayah) VALUES (?, ?, ?, ?)",
            rows,
        )
        con.commit()
        con.execute("VACUUM")
    finally:
        con.close()

    report = {
        "format": "miltazim-ayah-hafs-map-build-report",
        "runtime_file": "ayah_mapping.sqlite",
        "runtime_sha256": sha256_file(output_path),
        "runtime_bytes": output_path.stat().st_size,
        "row_count": len(rows),
        "editions": [
            {
                "edition_id": edition_id,
                "edition_key": key,
                "rawi": rawi,
                "ayah_count": expected,
                "source": rel,
                "sha256": sha256_file(source_paths[key]),
            }
            for edition_id, key, rawi, rel, expected in EDITIONS
        ],
        "multi_reference_ayah_count": len(multi_refs),
        "multi_reference_examples": multi_refs[:50],
        "algorithm": {
            "method": "character-span-alignment",
            "normalization": NORMALIZATION_VERSION,
            "min_target_coverage": MIN_TARGET_COVERAGE,
            "min_span_coverage": MIN_SPAN_COVERAGE,
            "numeric_offset": False,
        },
    }
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "multi_reference_examples"}, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, default=Path("output/ayah_mapping/ayah_mapping.sqlite"))
    parser.add_argument("--report", type=Path, default=Path("output/ayah_mapping/ayah_mapping-build-report.json"))
    args = parser.parse_args()
    root = args.project_root.resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    report = args.report if args.report.is_absolute() else root / args.report
    build(root, output, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
