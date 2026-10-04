#!/usr/bin/env python3
"""Build edition-exact Hafs<->riwayah mappings from quran-text ayah-map.json."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def parse_ref(value: str) -> tuple[int, int]:
    surah, ayah = value.split(":", 1)
    return int(surah), int(ayah)


def target_range(entry: dict) -> list[int]:
    start = int(entry["ayah"])
    end = int(entry.get("ayah_last", start))
    if end < start:
        raise ValueError(f"Invalid target range: {entry}")
    return list(range(start, end + 1))


def quran_text_to_qam_forward(doc: dict, edition: str) -> dict:
    by_surah: dict[str, dict] = {}
    for row in doc["ayahs"]:
        s = int(row["surah"])
        h = int(row["ayah"])
        entry = row[edition]
        relation = entry["relation"]
        targets = target_range(entry)
        status = {
            "same": "mapped",
            "merged": "merged",
            "split": "split",
            "shifted": "mapped",
            "unnumbered": "unnumbered",
            "missing": "missing",
        }.get(relation)
        if status is None:
            raise ValueError(f"Unknown relation {relation} for {edition} {s}:{h}")
        out = {"target_ayah": targets[0], "status": status, "source_relation": relation}
        if relation == "split":
            out["splits_into"] = targets
        if relation == "merged":
            out["merges_with_next"] = True
        if relation == "unnumbered":
            out["target_ayah"] = 0
        by_surah.setdefault(str(s), {"source_ayah_count": 0, "ayahs": {}})
        by_surah[str(s)]["source_ayah_count"] += 1
        by_surah[str(s)]["ayahs"][str(h)] = out
    return {
        "schema_version": 1,
        "format": "quran-ayah-edition-map",
        "reference": "hafs",
        "edition": edition,
        "surahs": by_surah,
        "_source_relation_semantics": "Copied from quran-text ayah-map.json; no numeric offset is used.",
    }


def invert_forward(forward: dict, edition: str) -> dict:
    grouped: dict[tuple[int, int], list[tuple[int, int, str]]] = defaultdict(list)
    unnumbered: dict[int, list[int]] = defaultdict(list)
    for s, sdata in forward["surahs"].items():
        si = int(s)
        for h, entry in sdata["ayahs"].items():
            hi = int(h)
            if entry["status"] == "unnumbered":
                unnumbered[si].append(hi)
                continue
            start = int(entry["target_ayah"])
            end = int(entry.get("splits_into", [start])[-1]) if entry.get("status") == "split" else start
            for ta in range(start, end + 1):
                grouped[(si, ta)].append((hi, entry["status"], entry.get("source_relation", entry["status"])))

    surahs: dict[str, dict] = {}
    for (s, ta), refs in sorted(grouped.items()):
        refs_sorted = sorted({h for h, _, _ in refs})
        rels = sorted({r for _, _, r in refs})
        if len(refs_sorted) == 1:
            entry = {
                "hafs_ayah": refs_sorted[0],
                "status": "mapped",
            }
        else:
            entry = {
                "hafs_ayah": refs_sorted[0],
                "hafs_ayahs": refs_sorted,
                "status": "covers_multiple",
            }
        if rels:
            entry["source_relations"] = rels
        surahs.setdefault(str(s), {"target_ayah_count": 0, "ayahs": {}})
        surahs[str(s)]["target_ayah_count"] = max(surahs[str(s)]["target_ayah_count"], ta)
        surahs[str(s)]["ayahs"][str(ta)] = entry

    for s in range(1, 115):
        surahs.setdefault(str(s), {"target_ayah_count": 0, "ayahs": {}})

    # Numbered counts are inferred from the actual targets present, not a delta.
    for s, sdata in surahs.items():
        sdata["target_ayah_count"] = len(sdata["ayahs"])
        if unnumbered.get(int(s)):
            sdata["unnumbered_hafs_ayahs"] = sorted(unnumbered[int(s)])

    return {
        "schema_version": 1,
        "format": "quran-ayah-edition-reverse-map",
        "edition": edition,
        "reference": "hafs",
        "surahs": surahs,
        "_source_relation_semantics": "Inverted from quran-text ayah-map.json; covers_multiple is explicit.",
    }


def compare_counts(reverse: dict, sqlite_counts: dict[str, int], edition: str) -> None:
    generated_total = sum(d["target_ayah_count"] for d in reverse["surahs"].values())
    actual_total = sum(sqlite_counts.values())
    if generated_total != actual_total:
        raise SystemExit(
            f"{edition}: generated edition count {generated_total} != project SQLite {actual_total}"
        )
    for s in range(1, 115):
        a = reverse["surahs"][str(s)]["target_ayah_count"]
        b = sqlite_counts[str(s)]
        if a != b:
            raise SystemExit(f"{edition}: surah {s} count {a} != project SQLite {b}")


def sqlite_counts(path: Path) -> dict[str, int]:
    import sqlite3
    with sqlite3.connect(path) as con:
        rows = con.execute("SELECT surah, COUNT(*) FROM ayat GROUP BY surah ORDER BY surah").fetchall()
    counts = {str(int(s)): int(c) for s, c in rows}
    if len(counts) != 114:
        raise SystemExit(f"Expected 114 surahs, found {len(counts)} in {path}")
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", required=True, type=Path)
    ap.add_argument("--project-root", default=Path("."), type=Path)
    ap.add_argument("--edition", required=True, choices=["qalun", "duri"])
    ap.add_argument("--sqlite-path", required=True, type=Path, help="Project SQLite path for the exact printed edition")
    ap.add_argument("--output-dir", default=Path("output/ayah_mapping"), type=Path)
    args = ap.parse_args()

    doc = json.loads(args.map.resolve().read_text(encoding="utf-8"))
    if doc.get("format") != "quran-ayah-map":
        raise SystemExit("Unexpected quran-text ayah-map format")
    forward = quran_text_to_qam_forward(doc, args.edition)
    reverse = invert_forward(forward, args.edition)
    generated = doc.get("generated")
    forward["source"] = {
        "repository": "https://github.com/quran-ws/quran-text",
        "file": "data/ayah-map.json",
        "generated": generated,
    }
    reverse["source"] = forward["source"]

    sqlite_path = args.sqlite_path
    if not sqlite_path.is_absolute():
        sqlite_path = args.project_root.resolve() / sqlite_path
    sqlite_path = sqlite_path.resolve()
    if not sqlite_path.is_file():
        raise SystemExit(f"Project SQLite not found: {sqlite_path}")
    counts = sqlite_counts(sqlite_path)
    compare_counts(reverse, counts, args.edition)

    edition_count = sum(counts.values())
    forward["edition_ayah_count"] = edition_count
    reverse["edition_ayah_count"] = edition_count
    out = args.output_dir.resolve() / "mappings/by-edition"
    out.mkdir(parents=True, exist_ok=True)
    fwd = out / f"hafs-to-{args.edition}-{edition_count}.json"
    rev = out / f"{args.edition}-{edition_count}-to-hafs.json"
    fwd.write_text(json.dumps(forward, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rev.write_text(json.dumps(reverse, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Built exact {args.edition} mapping ({edition_count} ayahs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
