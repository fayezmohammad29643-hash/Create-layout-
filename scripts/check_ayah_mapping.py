#!/usr/bin/env python3
"""Validate counting-system baselines and exact printed-edition mappings."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def forward_entry(doc: dict, surah: int, ayah: int) -> dict:
    return doc["surahs"][str(surah)]["ayahs"][str(ayah)]


def validate_qam_doc(doc: dict, expected_total: int | None = None) -> None:
    assert set(doc) >= {"surahs"}
    total = 0
    for surah in range(1, 115):
        data = doc["surahs"][str(surah)]
        count = int(data["target_ayah_count"])
        total += count
        assert len(data["ayahs"]) == int(data["hafs_ayah_count"])
    if expected_total is not None:
        assert total == expected_total, (total, expected_total)


def sqlite_counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(path) as con:
        rows = con.execute("SELECT surah, COUNT(*) FROM ayat GROUP BY surah ORDER BY surah").fetchall()
    counts = {str(int(s)): int(c) for s, c in rows}
    if len(counts) != 114:
        raise SystemExit(f"Expected 114 surahs, found {len(counts)} in {path}")
    return counts


def validate_exact_edition(path: Path, edition: str, expected_total: int, counts: dict[str, int]) -> None:
    doc = load_json(path)
    if doc.get("format") != "quran-ayah-edition-reverse-map":
        raise SystemExit(f"Unexpected exact reverse-map format in {path}")
    if doc.get("edition") != edition:
        raise SystemExit(f"Unexpected edition in {path}: {doc.get('edition')}")
    if int(doc.get("edition_ayah_count", -1)) != expected_total:
        raise SystemExit(f"Unexpected edition count in {path}")
    total = 0
    for s in range(1, 115):
        sd = doc["surahs"][str(s)]
        target_count = int(sd["target_ayah_count"])
        if target_count != counts[str(s)]:
            raise SystemExit(
                f"{edition}: surah {s} exact-map count {target_count} != project SQLite {counts[str(s)]}"
            )
        if len(sd["ayahs"]) != target_count:
            raise SystemExit(f"{edition}: surah {s} target_ayah_count does not equal ayah table size")
        total += target_count
    if total != expected_total:
        raise SystemExit(f"{edition}: exact mapping total {total} != expected {expected_total}")


def validate_runtime_map(path: Path, expected_editions: dict[str, int]) -> None:
    doc = load_json(path)
    if doc.get("format") != "miltazim-runtime-ayah-to-hafs-map":
        raise SystemExit("Unexpected runtime mapping format")
    if doc.get("reference") != "hafs-kufi":
        raise SystemExit("Runtime mapping must target Hafs/Kufi")
    if doc.get("never_use_numeric_offset") is not True:
        raise SystemExit("Runtime mapping must explicitly disallow numeric offsets")
    editions = doc.get("editions", {})
    if set(editions) != set(expected_editions):
        raise SystemExit(f"Runtime mapping editions mismatch: {sorted(editions)}")
    for edition, total in expected_editions.items():
        ed = editions[edition]
        if int(ed.get("ayah_count", -1)) != total:
            raise SystemExit(f"Runtime mapping {edition}: unexpected ayah count")
        numbered = sum(len(sd["surahs"][str(s)]) for sd in []) if False else 0
        count = sum(len(ayahs) for ayahs in ed["surahs"].values())
        if count != total:
            raise SystemExit(f"Runtime mapping {edition}: {count} entries != {total}")
        # Every runtime entry must resolve to one or more Hafs ayahs.
        for surah, ayahs in ed["surahs"].items():
            for ayah, entry in ayahs.items():
                refs = entry.get("hafs_ayahs")
                if not isinstance(refs, list) or not refs or any(int(x) < 1 for x in refs):
                    raise SystemExit(f"Invalid runtime ref at {edition} {surah}:{ayah}: {entry}")


def validate_exact_forward(path: Path, edition: str, expected_total: int, examples: list[tuple[int, int, dict]]) -> None:
    doc = load_json(path)
    if doc.get("format") != "quran-ayah-edition-map":
        raise SystemExit(f"Unexpected exact forward-map format in {path}: {doc.get('format')!r}")
    if doc.get("edition") != edition:
        raise SystemExit(f"Unexpected edition in {path}: {doc.get('edition')}")
    for s, a, expected in examples:
        actual = forward_entry(doc, s, a)
        for k, v in expected.items():
            if actual.get(k) != v:
                raise SystemExit(f"{edition} {s}:{a}: expected {k}={v!r}, got {actual}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", default=Path("."), type=Path)
    ap.add_argument("--config", default=Path("config-ayah-mapping.json"), type=Path)
    ap.add_argument("--output-dir", default=Path("output/ayah_mapping"), type=Path)
    args = ap.parse_args()

    root = args.output_dir.resolve()
    project_root = args.project_root.resolve()
    config = load_json(args.config.resolve())
    manifest = load_json(root / "manifest.json")

    expected = {item["path"] for item in manifest["files"]}
    for rel in expected:
        if not (root / rel).is_file():
            raise SystemExit(f"Manifest file missing: {rel}")

    quran_text_manifest = load_json(root / "sources/quran-text/manifest.json")
    if quran_text_manifest["resolved_generated"] != config["exact_edition_source"]["expected_generated"]:
        raise SystemExit("quran-text source generation date does not match config pin")
    exact_map = load_json(root / "sources/quran-text/ayah-map.json")
    if exact_map.get("format") != "quran-ayah-map":
        raise SystemExit("Unexpected quran-text ayah-map format")
    if set(exact_map.get("editions", [])) != {"hafs", "shubah", "bazzi", "qalun", "warsh", "duri", "susi"}:
        raise SystemExit("quran-text ayah-map does not expose the expected seven editions")

    ml = load_json(root / "mappings/by-counting-system/kufi-to-madani-last.json")
    mf = load_json(root / "mappings/by-counting-system/kufi-to-madani-first.json")
    validate_qam_doc(ml, 6214)
    validate_qam_doc(mf, 6214)

    e = forward_entry(ml, 2, 255)
    if e.get("status") != "split" or e.get("target_ayah") != 253 or e.get("splits_into") != [253, 254]:
        raise SystemExit(f"Unexpected Last Madinan mapping for 2:255: {e}")
    e = forward_entry(ml, 1, 1)
    if e.get("status") != "merged" or e.get("target_ayah") != 1:
        raise SystemExit(f"Unexpected Last Madinan mapping for 1:1: {e}")
    e = forward_entry(ml, 1, 7)
    if e.get("status") != "split" or e.get("splits_into") != [6, 7]:
        raise SystemExit(f"Unexpected Last Madinan mapping for 1:7: {e}")
    e = forward_entry(mf, 1, 1)
    if e.get("status") != "merged" or e.get("target_ayah") != 1:
        raise SystemExit(f"Unexpected First Madinan mapping for 1:1: {e}")

    # QAM rawi metadata stays useful as provenance/baseline.
    for rawi, expected_system in [("warsh", "madani-last"), ("qalun", "madani-last"), ("duri", "madani-first")]:
        rawi_doc = load_json(root / f"rawis/{rawi}.json")
        if rawi_doc.get("_counting_system_printed") != expected_system:
            raise SystemExit(f"Unexpected printed system for {rawi}: {rawi_doc.get('_counting_system_printed')}")

    compatibility = manifest.get("source_compatibility", {})
    for rawi in ("warsh", "qalun", "douri"):
        if rawi not in compatibility:
            raise SystemExit(f"Missing source compatibility for {rawi}")

    # Source integrity for the four project editions.
    edition_expectations = {"hafs": 6236, "warsh": 6213, "qalun": 6214, "douri": 6217}
    for edition, total in edition_expectations.items():
        db = project_root / f"input/{edition}/quran.sqlite"
        actual = int(sqlite_counts(db)["1"]) if False else None
        with sqlite3.connect(db) as con:
            c = int(con.execute("SELECT COUNT(*) FROM ayat").fetchone()[0])
        if c != total:
            raise SystemExit(f"{edition}: project SQLite contains {c} ayahs, expected {total}")

    # Exact edition maps from quran-text.
    qalun_counts = sqlite_counts(project_root / "input/qalun/quran.sqlite")
    duri_counts = sqlite_counts(project_root / "input/douri/quran.sqlite")
    validate_exact_edition(root / "mappings/by-edition/qalun-6214-to-hafs.json", "qalun", 6214, qalun_counts)
    validate_exact_edition(root / "mappings/by-edition/duri-6217-to-hafs.json", "duri", 6217, duri_counts)

    validate_exact_forward(
        root / "mappings/by-edition/hafs-to-duri-6217.json",
        "duri",
        6217,
        [
            (2, 255, {"target_ayah": 253, "status": "mapped"}),
            (2, 257, {"target_ayah": 255, "status": "split", "splits_into": [255, 256]}),
            (67, 9, {"target_ayah": 9, "status": "mapped"}),
        ],
    )
    validate_exact_forward(
        root / "mappings/by-edition/hafs-to-qalun-6214.json",
        "qalun",
        6214,
        [
            (1, 2, {"target_ayah": 1, "status": "mapped"}),
            (1, 7, {"target_ayah": 6, "status": "split", "splits_into": [6, 7]}),
            (2, 255, {"target_ayah": 253, "status": "split", "splits_into": [253, 254]}),
        ],
    )

    # Warsh 6213 remains a project-specific exact edition override.
    if compatibility["warsh"].get("mapping_exact_for_current_layout") is not True:
        raise SystemExit("Warsh 6213 must be exact after the local Nahl override")
    warsh_exact = load_json(root / "mappings/by-edition/warsh-6213-to-hafs.json")
    e = warsh_exact["surahs"]["16"]["ayahs"]["123"]
    if e != {"hafs_ayahs": [123, 124], "status": "covers_multiple"}:
        raise SystemExit(f"Unexpected exact Warsh 6213 Nahl 16:123 mapping: {e}")
    expected_shift = {"124": 125, "125": 126, "126": 127, "127": 128}
    for src, target in expected_shift.items():
        e = warsh_exact["surahs"]["16"]["ayahs"][src]
        if e.get("hafs_ayah") != target or e.get("status") != "mapped":
            raise SystemExit(f"Unexpected exact Warsh 6213 Nahl {src}: {e}")

    # Critical Douri edition anchor: the Kufi internal boundary after the first "نذير"
    # inside Kufi 67:9 is not a boundary in the Douri printed edition. Therefore
    # the whole Kufi 67:9 remains Douri 67:9.
    douri_text = project_root / "input/douri/quran.sqlite"
    with sqlite3.connect(douri_text) as con:
        rows = [
            r[0]
            for r in con.execute(
                "SELECT text FROM ayat WHERE surah=67 AND ayah IN (8,9) ORDER BY ayah"
            )
        ]
    if len(rows) != 2:
        raise SystemExit("Douri 67:8/67:9 anchor rows missing")
    if not rows[0].endswith("نَذِيرٞ"):
        raise SystemExit("Unexpected Douri 67:8 ending")
    if not rows[1].endswith("كَبِيرٖ"):
        raise SystemExit("Unexpected Douri 67:9 ending")

    if compatibility["qalun"].get("mapping_exact_for_current_layout") is not True:
        raise SystemExit("Qalun exact edition mapping must be enabled")
    if compatibility["douri"].get("mapping_exact_for_current_layout") is not True:
        raise SystemExit("Douri exact edition mapping must be enabled")

    validate_runtime_map(
        root / "miltazim_ayah_mapping.json",
        {"warsh-6213": 6213, "qalun-6214": 6214, "douri-6217": 6217},
    )

    print("Ayah mapping validation OK")
    print("Runtime file ready: miltazim_ayah_mapping.json")
    print("Exact attachment mappings ready: Hafs, Warsh 6213, Qalun 6214, Douri 6217")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
