#!/usr/bin/env python3
"""Import Qiraat Ayah Map artefacts and establish project-edition compatibility."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sqlite_ayah_count(path: Path) -> int:
    with sqlite3.connect(path) as con:
        return int(con.execute("SELECT COUNT(*) FROM ayat").fetchone()[0])


def apply_warsh_6213_override(
    out: Path, project_root: Path, config: dict, manifest_files: list[dict]
) -> dict:
    override_rel = config.get("edition_overrides", {}).get("warsh_6213")
    if not override_rel:
        raise RuntimeError("warsh_6213 edition override is not configured")
    override_path = project_root / override_rel
    override = load_json(override_path)

    base_w2h = load_json(out / "mappings/by-rawi/warsh-to-hafs.json")
    base_h2w = load_json(out / "mappings/by-rawi/hafs-to-warsh.json")

    def clone(doc: dict) -> dict:
        return json.loads(json.dumps(doc))

    w2h = clone(base_w2h)
    h2w = clone(base_h2w)
    surah = "16"

    wa = w2h["surahs"][surah]["ayahs"]
    for k in list(wa):
        if int(k) >= 123:
            wa.pop(k)
    for key, val in list(override["overrides"]["warsh_to_hafs"].items()):
        wa[key.split(":", 1)[1]] = val
    w2h["surahs"][surah]["target_ayah_count"] = 127
    w2h["_edition_id"] = override["edition_id"]
    w2h["_description"] = (
        "Edition-exact Warsh (project 6213) → Hafs/Kufi mapping; upstream generic "
        "mapping is used everywhere except the explicit al-Nahl override."
    )

    ha = h2w["surahs"][surah]["ayahs"]
    for k in list(ha):
        if int(k) >= 123:
            ha.pop(k)
    for key, val in list(override["overrides"]["hafs_to_warsh"].items()):
        ha[key.split(":", 1)[1]] = val
    h2w["surahs"][surah]["source_ayah_count"] = 128
    h2w["_edition_id"] = override["edition_id"]
    h2w["_description"] = (
        "Edition-exact Hafs/Kufi → Warsh (project 6213) mapping; upstream generic "
        "mapping is used everywhere except the explicit al-Nahl override."
    )

    out_w2h = out / "mappings/by-edition/warsh-6213-to-hafs.json"
    out_h2w = out / "mappings/by-edition/hafs-to-warsh-6213.json"
    out_w2h.parent.mkdir(parents=True, exist_ok=True)
    out_w2h.write_text(json.dumps(w2h, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_h2w.write_text(json.dumps(h2w, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for rel in (
        "mappings/by-edition/warsh-6213-to-hafs.json",
        "mappings/by-edition/hafs-to-warsh-6213.json",
    ):
        fp = out / rel
        manifest_files.append({"path": rel, "sha256": sha256_file(fp), "size": fp.stat().st_size})

    counts = load_json(out / "surah-counts/madani-last.json")
    counts["_edition_id"] = override["edition_id"]
    counts["_description"] = (
        "Project Warsh edition ayah counts (6213), derived from the pinned SQLite; "
        "differs from upstream Last Madinan only at al-Nahl."
    )
    counts["surahs"]["16"] = 127
    counts["_total_ayahs"] = 6213
    count_rel = "surah-counts/warsh-6213.json"
    count_path = out / count_rel
    count_path.write_text(json.dumps(counts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest_files.append({"path": count_rel, "sha256": sha256_file(count_path), "size": count_path.stat().st_size})

    return override


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--upstream-dir", required=True, type=Path)
    ap.add_argument("--project-root", default=Path("."), type=Path)
    ap.add_argument("--config", default="config-ayah-mapping.json", type=Path)
    ap.add_argument("--output-dir", default=Path("output/ayah_mapping"), type=Path)
    args = ap.parse_args()

    project_root = args.project_root.resolve()
    upstream = args.upstream_dir.resolve()
    config = load_json(args.config.resolve())

    if not (upstream / "dist").is_dir():
        raise SystemExit(f"Upstream dist directory not found: {upstream / 'dist'}")

    out = args.output_dir.resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    manifest_files: list[dict] = []
    for rel in config["required_outputs"]:
        src = upstream / "dist" / rel
        if not src.is_file():
            raise SystemExit(f"Required Qiraat Ayah Map artefact is missing: {src}")
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        manifest_files.append({"path": rel, "sha256": sha256_file(dest), "size": dest.stat().st_size})

    edition_override = apply_warsh_6213_override(out, project_root, config, manifest_files)

    compatibility = {}
    for rawi, resource in config["resources"].items():
        sqlite_rel = resource.get("project_sqlite_path")
        if not sqlite_rel:
            continue
        sqlite_path = project_root / sqlite_rel
        if not sqlite_path.is_file():
            compatibility[rawi] = {"status": "missing_sqlite", "path": sqlite_rel}
            continue
        actual = sqlite_ayah_count(sqlite_path)
        expected = resource.get("project_sqlite_ayah_count")
        if expected is None:
            expected = resource.get("current_measured_printing_ayah_count")
        count_match = expected is not None and actual == expected
        has_edition_map = bool(resource.get("exact_edition_source"))
        exact_override = rawi == "warsh" and actual == 6213 and bool(config.get("edition_overrides", {}).get("warsh_6213"))
        exact_ready = exact_override or (has_edition_map and count_match)
        status = (
            "edition_exact_override" if exact_override else
            "exact_edition_source" if exact_ready else
            "exact_count_match" if count_match and resource.get("mapping_exact_for_current_layout") else
            resource.get("status", "count_mismatch")
        )
        compatibility[rawi] = {
            "status": status,
            "sqlite_path": sqlite_rel,
            "sqlite_ayah_count": actual,
            "expected_edition_ayah_count": expected,
            "mapping_exact_for_current_layout": exact_ready,
            "mapping_file": (
                "mappings/by-edition/warsh-6213-to-hafs.json" if exact_override else resource.get("mapping_file")
            ),
            "reverse_mapping_file": (
                "mappings/by-edition/hafs-to-warsh-6213.json" if exact_override else resource.get("reverse_mapping_file")
            ),
            "exact_edition_source": resource.get("exact_edition_source"),
            "exact_edition_key": resource.get("exact_edition_key"),
        }

    try:
        upstream_commit = subprocess.check_output(
            ["git", "-c", f"safe.directory={upstream}", "rev-parse", "HEAD"], cwd=upstream, text=True
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        upstream_commit = "local-source-unresolved"

    manifest = {
        "schema_version": 3,
        "source": config["upstream"],
        "resolved_commit": upstream_commit,
        "reference_system": config["reference_system"],
        "resources": config["resources"],
        "source_compatibility": compatibility,
        "files": manifest_files,
        "edition_overrides": {"warsh_6213": edition_override},
        "exact_edition_source": config["exact_edition_source"],
        "consumer_rule": (
            "Persist references in Hafs/Kufan numbering for attachments; translate at display time. "
            "Use an edition-specific map whenever the displayed mushaf edition has one. "
            "Never infer mappings by a numeric offset."
        ),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Imported {len(manifest_files)} Qiraat Ayah Map artefacts into {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
