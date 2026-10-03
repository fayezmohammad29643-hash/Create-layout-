#!/usr/bin/env python3
"""Build the single runtime ayah-reference map consumed by the Miltazim app.

The generated file contains only forward mappings from the project's three
non-Hafs editions (Warsh, Qalun, Douri) to Hafs/Kufi. Source and reverse maps
remain available in the build output for provenance and validation, but are
not required at runtime by the app.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


EDITION_FILES = {
    "warsh-6213": "mappings/by-edition/warsh-6213-to-hafs.json",
    "qalun-6214": "mappings/by-edition/qalun-6214-to-hafs.json",
    "douri-6217": "mappings/by-edition/duri-6217-to-hafs.json",
}

EDITION_META = {
    "warsh-6213": {"rawi": "warsh", "ayah_count": 6213},
    "qalun-6214": {"rawi": "qalun", "ayah_count": 6214},
    "douri-6217": {"rawi": "douri", "ayah_count": 6217},
}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_entry(entry: dict) -> dict:
    status = entry.get("status", "mapped")
    if status == "covers_multiple":
        refs = entry.get("hafs_ayahs")
        if not isinstance(refs, list) or not refs:
            raise ValueError(f"covers_multiple entry has no hafs_ayahs: {entry}")
        return {"hafs_ayahs": [int(x) for x in refs], "status": status}

    if "hafs_ayah" not in entry:
        raise ValueError(f"Runtime mapping entry has no hafs_ayah: {entry}")
    return {"hafs_ayahs": [int(entry["hafs_ayah"])], "status": status}


def build_runtime_map(root: Path) -> dict:
    editions = {}
    for edition_id, rel in EDITION_FILES.items():
        path = root / rel
        if not path.is_file():
            raise SystemExit(f"Missing exact edition map: {path}")
        doc = load(path)
        surahs = {}
        for surah, surah_data in sorted(doc["surahs"].items(), key=lambda x: int(x[0])):
            ayahs = {}
            for ayah, entry in sorted(
                surah_data["ayahs"].items(), key=lambda x: int(x[0])
            ):
                ayahs[str(int(ayah))] = normalize_entry(entry)
            surahs[str(int(surah))] = ayahs
        editions[edition_id] = {
            **EDITION_META[edition_id],
            "reference": "hafs-kufi",
            "surahs": surahs,
        }

    return {
        "schema_version": 1,
        "format": "miltazim-runtime-ayah-to-hafs-map",
        "purpose": "Runtime reference map for tafsir and other Hafs-keyed attachments.",
        "reference": "hafs-kufi",
        "runtime_editions": list(EDITION_FILES),
        "never_use_numeric_offset": True,
        "sources": {
            "counting_system": "https://github.com/quran-ws/qiraat-ayah-map",
            "exact_edition": "https://github.com/quran-ws/quran-text/blob/main/data/ayah-map.json",
            "warsh_6213_override": "data/ayah_mapping/edition-overrides/warsh-6213.json",
        },
        "editions": editions,
    }


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", default=Path("output/ayah_mapping"), type=Path)
    args = ap.parse_args()

    root = args.output_dir.resolve()
    doc = build_runtime_map(root)
    out = root / "miltazim_ayah_mapping.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Built runtime mapping: {out} ({out.stat().st_size} bytes)")
    print(f"SHA-256: {sha256(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
