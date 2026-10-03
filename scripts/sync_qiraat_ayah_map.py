#!/usr/bin/env python3
"""Build the attachment-reference layer from QAM + quran-text exact editions."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(str(x) for x in cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=PROJECT_ROOT / "config-ayah-mapping.json", type=Path)
    ap.add_argument("--work-dir", default=PROJECT_ROOT / ".cache/qiraat-ayah-map", type=Path)
    ap.add_argument("--output-dir", default=PROJECT_ROOT / "output/ayah_mapping", type=Path)
    args = ap.parse_args()

    config_path = args.config.resolve()
    work_dir = args.work_dir.resolve()
    output_dir = args.output_dir.resolve()
    project_root = PROJECT_ROOT.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))

    upstream = config["upstream"]
    repo = work_dir
    if (repo / ".git").is_dir():
        run(["git", "fetch", "--depth", "1", "origin", upstream["ref"]], cwd=repo)
    else:
        if repo.exists():
            shutil.rmtree(repo)
        repo.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--no-checkout", "--filter=blob:none", upstream["repository"], str(repo)])
        run(["git", "fetch", "--depth", "1", "origin", upstream["ref"]], cwd=repo)

    run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=repo)
    run(["npm", "install", "--ignore-scripts"], cwd=repo)
    run(["npm", "run", "generate"], cwd=repo)

    # Import Qiraat Ayah Map's committed/generated counting-system layer first.
    prepare = SCRIPT_DIR / "prepare_ayah_mapping.py"
    checker = SCRIPT_DIR / "check_ayah_mapping.py"
    exact_sync = SCRIPT_DIR / "sync_quran_text_ayah_map.py"
    exact_builder = SCRIPT_DIR / "build_exact_edition_mapping.py"

    run([
        "python", str(prepare),
        "--upstream-dir", str(repo),
        "--project-root", str(project_root),
        "--config", str(config_path),
        "--output-dir", str(output_dir),
    ], cwd=project_root)

    # Fetch the edition-level map after prepare() because prepare() intentionally resets output/.
    run([
        "python", str(exact_sync),
        "--config", str(config_path),
        "--output-dir", str(output_dir),
    ], cwd=project_root)

    exact_map = output_dir / "sources/quran-text/ayah-map.json"
    for edition in ("qalun", "duri"):
        run([
            "python", str(exact_builder),
            "--map", str(exact_map),
            "--project-root", str(project_root),
            "--edition", edition,
            "--output-dir", str(output_dir),
        ], cwd=project_root)

    # Build one stable runtime file for the Flutter app. All detailed source,
    # reverse, counting-system, and provenance artefacts remain in output/ for
    # validation/documentation; the app consumes only this forward map.
    app_builder = SCRIPT_DIR / "build_app_ayah_mapping.py"
    run([
        "python", str(app_builder),
        "--output-dir", str(output_dir),
    ], cwd=project_root)

    # Add generated source/map hashes to the existing manifest.
    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_manifest = json.loads((output_dir / "sources/quran-text/manifest.json").read_text(encoding="utf-8"))
    manifest["quran_text_source"] = source_manifest

    files = {item["path"]: item for item in manifest.get("files", [])}
    for path in [
        "sources/quran-text/ayah-map.json",
        "sources/quran-text/manifest.json",
        "mappings/by-edition/qalun-6214-to-hafs.json",
        "mappings/by-edition/hafs-to-qalun-6214.json",
        "mappings/by-edition/duri-6217-to-hafs.json",
        "mappings/by-edition/hafs-to-duri-6217.json",
        "miltazim_ayah_mapping.json",
    ]:
        fp = output_dir / path
        if fp.is_file():
            import hashlib
            digest = hashlib.sha256(fp.read_bytes()).hexdigest()
            files[path] = {"path": path, "sha256": digest, "size": fp.stat().st_size}
    manifest["files"] = list(files.values())
    manifest["source_compatibility"]["qalun"].update({
        "mapping_exact_for_current_layout": True,
        "status": "exact_edition_source",
        "mapping_file": "mappings/by-edition/qalun-6214-to-hafs.json",
        "reverse_mapping_file": "mappings/by-edition/hafs-to-qalun-6214.json",
    })
    manifest["source_compatibility"]["douri"].update({
        "mapping_exact_for_current_layout": True,
        "status": "exact_edition_source",
        "mapping_file": "mappings/by-edition/duri-6217-to-hafs.json",
        "reverse_mapping_file": "mappings/by-edition/hafs-to-duri-6217.json",
    })
    manifest["consumer_rule"] = (
        "The Miltazim app consumes only miltazim_ayah_mapping.json for edition-to-Hafs attachment lookup. "
        "The detailed counting-system, rawi, edition, reverse, and provenance artefacts remain build-time outputs only. "
        "Qiraat Ayah Map remains the counting-system/provenance baseline. Never use numeric offsets."
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    run([
        "python", str(checker),
        "--project-root", str(project_root),
        "--config", str(config_path),
        "--output-dir", str(output_dir),
    ], cwd=project_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
