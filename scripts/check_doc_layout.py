#!/usr/bin/env python3
"""Validate compact schema-v3 DOC+SQLite Quran layout output."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
import sys

# Reuse exactly the same source normalization/token handling used by the builder.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_doc_layout import canonical, split_token  # noqa: E402


def load_words(db: Path):
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("select surah,ayah,text from ayat order by surah,ayah").fetchall()
    finally:
        conn.close()

    by_ayah: dict[tuple[int, int], list[dict]] = {}
    global_words: list[dict] = []
    for s, a, text in rows:
        arr: list[dict] = []
        for raw in text.split():
            for token, _marker in split_token(raw):
                token = token.replace("۞", "").strip()
                if not token:
                    continue
                item = {
                    "surah": int(s),
                    "ayah": int(a),
                    "word": token,
                    "wordIndex": len(arr) + 1,
                }
                arr.append(item)
                global_words.append(item)
        by_ayah[(int(s), int(a))] = arr
    if not global_words:
        raise RuntimeError(f"SQLite contains no Quran words: {db}")
    return by_ayah, global_words


def validate_endpoint(
    ep: dict,
    where: str,
    words_by_ayah: dict[tuple[int, int], list[dict]],
) -> tuple[int | None, tuple[int, int, int] | None]:
    if not isinstance(ep, dict):
        raise RuntimeError(f"{where}: endpoint must be object")
    if ep.get("type") not in ("word", "ayah_marker"):
        raise RuntimeError(f"{where}: invalid endpoint type")
    for k in ("surah", "ayah"):
        if k not in ep:
            raise RuntimeError(f"{where}: missing {k}")
    key = (int(ep["surah"]), int(ep["ayah"]))
    arr = words_by_ayah.get(key)
    if arr is None:
        raise RuntimeError(f"{where}: ayah is absent from SQLite")

    if ep["type"] == "ayah_marker":
        if ep.get("number") != ep.get("ayah"):
            raise RuntimeError(f"{where}: marker number/ayah mismatch")
        if not arr:
            raise RuntimeError(f"{where}: marker ayah contains no SQLite words")
        last = arr[-1]
        return len(arr) - 1, (last["surah"], last["ayah"], last["wordIndex"])

    if "word" not in ep or "wordIndex" not in ep:
        raise RuntimeError(f"{where}: missing word/wordIndex")
    index = int(ep["wordIndex"])
    if not (1 <= index <= len(arr)):
        raise RuntimeError(f"{where}: wordIndex outside SQLite ayah")
    expected = arr[index - 1]
    if canonical(expected["word"]) != canonical(ep["word"]):
        raise RuntimeError(f"{where}: endpoint word does not match SQLite")
    return index - 1, (expected["surah"], expected["ayah"], expected["wordIndex"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--sqlite", type=Path, required=True)
    args = ap.parse_args()

    out = args.output_dir
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf8"))
    if manifest.get("schema_version") != 3 or manifest.get("layout_source") != "doc+sqlite":
        raise RuntimeError("Manifest schema/layout mismatch")
    if manifest.get("total_pages") != 604 or manifest.get("total_surahs") != 114:
        raise RuntimeError("Wrong page/surah counts")

    files = sorted((out / "pages").glob("page_*.json"))
    surah_files = sorted((out / "surahs").glob("*.json"))
    if len(files) != 604:
        raise RuntimeError(f"Expected 604 page files, found {len(files)}")
    if len(surah_files) != 114:
        raise RuntimeError(f"Expected 114 surah files, found {len(surah_files)}")

    words_by_ayah, global_words = load_words(args.sqlite)
    position = {
        (w["surah"], w["ayah"], w["wordIndex"]): i
        for i, w in enumerate(global_words)
    }
    total_lines = 0
    previous_end_pos: int | None = None

    for page_no, path in enumerate(files, 1):
        data = json.loads(path.read_text(encoding="utf8"))
        lines = data.get("lines")
        if data.get("page") != page_no or not isinstance(lines, list) or not lines or len(lines) > 15:
            raise RuntimeError(f"{path.name}: invalid page/lines")

        for idx, line in enumerate(lines, 1):
            if line.get("line") != idx:
                raise RuntimeError(f"{path.name} line numbering mismatch")
            if set(line) != {"line", "first_word", "end_word"}:
                raise RuntimeError(f"{path.name}: noncompact line fields")

            _, first_key = validate_endpoint(
                line["first_word"], f"{path.name} line {idx} first_word", words_by_ayah
            )
            _, end_key = validate_endpoint(
                line["end_word"], f"{path.name} line {idx} end_word", words_by_ayah
            )
            assert first_key is not None and end_key is not None
            first_pos = position[first_key]
            end_pos = position[end_key]
            if first_pos > end_pos:
                raise RuntimeError(f"{path.name} line {idx}: first_word comes after end_word")
            if previous_end_pos is not None and first_pos != previous_end_pos + 1:
                raise RuntimeError(
                    f"{path.name} line {idx}: word-stream discontinuity; expected global word {previous_end_pos + 2}, got {first_pos + 1}"
                )
            previous_end_pos = end_pos
            total_lines += 1

    if previous_end_pos != len(global_words) - 1:
        raise RuntimeError(
            f"Layout does not consume all SQLite words: ended at {previous_end_pos + 1 if previous_end_pos is not None else 0} of {len(global_words)}"
        )

    print(
        f"Validation OK: 604 pages, 114 surahs, {total_lines} Quran lines; "
        f"endpoints match the complete SQLite word stream"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}")
        raise SystemExit(1)
