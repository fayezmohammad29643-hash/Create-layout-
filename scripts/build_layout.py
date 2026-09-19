#!/usr/bin/env python3
"""Build one final layout JSON per surah from the supplied DOCX and the
Quranpedia Qalun/KFQC SVG source.

DOCX = source of truth for page/line text boundaries.
Quranpedia SVG = source of truth for physical line coordinates.
SVG is downloaded into a temporary working directory/repository clone only.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from zipfile import ZipFile

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"
ARABIC_DIGITS_RE = re.compile(r"^[٠-٩۰-۹0-9]+$")
META_SINGLE = "مصحف رواية قالون عن نافع"
META_VARIANT = "بوجه صلة ميم الجمع وتوسط المنفصل"


def arabic_digits_to_int(value: str) -> int:
    table = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
    return int(value.translate(table))


def clean_surah_name(heading: str) -> str:
    value = heading.strip()
    if value.startswith("سُورَةُ "):
        value = value[len("سُورَةُ "):]
    value = "".join(ch for ch in unicodedata.normalize("NFD", value) if unicodedata.category(ch) != "Mn")
    value = value.replace("ٱ", "ا")
    for ch in "ۖۗۘۙۚۛۜ۝":
        value = value.replace(ch, "")
    return " ".join(value.split())


def is_surah_heading(text: str) -> bool:
    return text.strip().startswith("سُورَةُ ")


def strip_metadata(text: str) -> str:
    value = text
    value = value.replace(META_SINGLE, "")
    value = value.replace(META_VARIANT, "")
    return value


def iter_docx_events(paragraph: ET.Element):
    LINE, PAGE, LRB = "\ue000", "\ue001", "\ue002"
    combined = []
    for node in paragraph.iter():
        if node.tag == f"{W}t":
            combined.append(node.text or "")
        elif node.tag == f"{W}br":
            combined.append(PAGE if node.get(f"{W}type") == "page" else LINE)
        elif node.tag == f"{W}lastRenderedPageBreak":
            combined.append(LRB)
    cleaned = strip_metadata("".join(combined))
    token_re = re.compile(f"({re.escape(LINE)}|{re.escape(PAGE)}|{re.escape(LRB)})")
    for piece in token_re.split(cleaned):
        if not piece:
            continue
        if piece == LINE:
            yield "br", None
        elif piece == PAGE:
            yield "br", "page"
        elif piece == LRB:
            yield "lrb", None
        else:
            yield "text", piece


def split_word_and_marker(token: str) -> List[str]:
    token = token.strip()
    if not token:
        return []
    if ARABIC_DIGITS_RE.fullmatch(token):
        return [token]
    m = re.match(r"^(.*?)([٠-٩۰-۹0-9]+)$", token)
    if m and m.group(1):
        return [m.group(1), m.group(2)]
    return [token]


def build_line(surah_id: int, state_ayah: int, state_word_index: int, line_text: str):
    first_word = last_word = None
    last_item = None
    ayahs = []
    ayah, word_index = state_ayah, state_word_index
    for raw_token in line_text.replace("\xa0", " ").split():
        token = raw_token.strip().replace("۞", "")
        if not token:
            continue
        for part in split_word_and_marker(token):
            if ARABIC_DIGITS_RE.fullmatch(part):
                marker_number = arabic_digits_to_int(part)
                last_item = ("marker", marker_number)
                ayah, word_index = marker_number + 1, 1
                continue
            word_obj = {"type": "word", "surah": surah_id, "ayah": ayah, "word": part, "wordIndex": word_index}
            if first_word is None:
                first_word = dict(word_obj)
            last_word = dict(word_obj)
            last_item = ("word", dict(word_obj))
            if ayah not in ayahs:
                ayahs.append(ayah)
            word_index += 1
    if first_word is None:
        return None, ayah, word_index
    end_obj = (
        {"type": "ayah_marker", "surah": surah_id, "ayah": int(last_item[1]), "number": int(last_item[1])}
        if last_item and last_item[0] == "marker"
        else last_word
    )
    return {
        "start": first_word,
        "end": end_obj,
        "y_top": None,
        "y_bottom": None,
        "x_start": None,
        "x_end": None,
        "verses_on_line": [f"{surah_id}:{a}" for a in ayahs],
    }, ayah, word_index


def extract_docx_layout(docx_path: Path) -> List[dict]:
    with ZipFile(docx_path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    paragraphs = root.findall(f".//{W}body/{W}p")
    surahs = []
    current = None
    current_ayah = 1
    current_word_index = 1
    page = 1
    line_in_page = 0

    def ensure_page(surah, page_number):
        for p in surah["pages"]:
            if p["page"] == page_number:
                return p
        p = {"page": page_number, "lines": []}
        surah["pages"].append(p)
        return p

    def emit_line(line_text):
        nonlocal current_ayah, current_word_index, line_in_page
        if current is None or not line_text.strip():
            return
        line_obj, new_ayah, new_word_index = build_line(current["surahId"], current_ayah, current_word_index, line_text)
        if line_obj is None:
            return
        line_in_page += 1
        p = ensure_page(current, page)
        p["lines"].append({
            "line": line_in_page,
            "start": line_obj["start"],
            "end": line_obj["end"],
            "y_top": None,
            "y_bottom": None,
            "x_start": None,
            "x_end": None,
            "verses_on_line": line_obj["verses_on_line"],
        })
        current_ayah, current_word_index = new_ayah, new_word_index

    for paragraph in paragraphs:
        raw_text = "".join(t.text or "" for t in paragraph.findall(f".//{W}t"))
        if is_surah_heading(raw_text):
            if any(event_type == "lrb" for event_type, _ in iter_docx_events(paragraph)):
                page += 1
                line_in_page = 0
            current = {
                "surahId": len(surahs) + 1,
                "surahName": clean_surah_name(raw_text),
                "englishName": None,
                "ayaCount": 0,
                "type": None,
                "startPage": None,
                "endPage": None,
                "totalPagesInSurah": 0,
                "recordedPagesCount": 0,
                "totalCompletedLines": 0,
                "pages": [],
            }
            surahs.append(current)
            current_ayah, current_word_index = 1, 1
            continue

        is_bismillah = raw_text.strip().startswith("بِسۡمِ ٱللَّهِ")
        is_content = bool(strip_metadata(raw_text).strip()) and not is_bismillah
        cur_line = ""
        for event_type, event_value in iter_docx_events(paragraph):
            if event_type == "lrb":
                if cur_line.strip() and is_content:
                    emit_line(cur_line)
                    cur_line = ""
                page += 1
                line_in_page = 0
            elif event_type == "text":
                if is_content:
                    cur_line += event_value or ""
            elif event_type == "br":
                if cur_line.strip() and is_content:
                    emit_line(cur_line)
                cur_line = ""
        if cur_line.strip() and is_content:
            emit_line(cur_line)

    for surah in surahs:
        surah["pages"].sort(key=lambda p: p["page"])
        if surah["pages"]:
            surah["startPage"] = surah["pages"][0]["page"]
            surah["endPage"] = surah["pages"][-1]["page"]
        max_ayah = 0
        for p in surah["pages"]:
            for line in p["lines"]:
                for edge in (line["start"], line["end"]):
                    if edge["type"] == "ayah_marker":
                        max_ayah = max(max_ayah, int(edge["number"]))
                    else:
                        max_ayah = max(max_ayah, int(edge["ayah"]))
        surah["ayaCount"] = max_ayah
        surah["totalPagesInSurah"] = len(surah["pages"])
        surah["recordedPagesCount"] = len(surah["pages"])
        surah["totalCompletedLines"] = sum(len(p["lines"]) for p in surah["pages"])
    return surahs


def run_git(args: List[str], cwd: Optional[Path] = None):
    proc = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(args)}\n{proc.stderr.strip()}")
    return proc.stdout


def prepare_quranpedia_source(config: dict, temp_dir: Path) -> Path:
    repo = temp_dir / "quranpedia-quran-svg"
    run_git(["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", "--branch", config.get("quranpedia_ref", "main"), config["quranpedia_repository"], str(repo)])
    subdir = config["svg_subdir"]
    run_git(["git", "sparse-checkout", "set", subdir], cwd=repo)
    svg_root = repo / subdir
    if not svg_root.exists():
        raise RuntimeError(f"Quranpedia SVG directory not found after sparse checkout: {subdir}")
    return svg_root


def find_page_svg(svg_root: Path, page: int) -> Path:
    plain = svg_root / f"{page:03d}.svg"
    if plain.exists():
        return plain
    compressed = svg_root / f"{page:03d}.svg.br"
    if compressed.exists():
        try:
            import brotli
        except ImportError as exc:
            raise RuntimeError("Found .svg.br but Brotli is not installed.") from exc
        data = brotli.decompress(compressed.read_bytes())
        target = svg_root / f"{page:03d}.svg.__decompressed"
        target.write_bytes(data)
        return target
    raise FileNotFoundError(f"Quranpedia SVG page not found: {page:03d}")


def extract_svg_coordinates(svg_root: Path, expected_lines_by_page: Dict[int, int]):
    scripts_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(scripts_dir))
    import extract_lines
    results = {}
    failures = []
    for page, expected in sorted(expected_lines_by_page.items()):
        try:
            svg_path = find_page_svg(svg_root, page)
            results[page] = extract_lines.extract(svg_path, expected_line_count=expected)
        except Exception as exc:
            failures.append((page, str(exc)))
    if failures:
        details = "\n".join(f"  page {p}: {e}" for p, e in failures[:30])
        raise RuntimeError("SVG extraction failed:\n" + details)
    return results


def merge_coordinates(surahs: List[dict], svg_results: Dict[int, dict]):
    # Build the complete Quran-line sequence for each physical page across all
    # surahs. This matters on pages like 440 where one page contains the end of
    # one surah and the beginning of another.
    by_page: Dict[int, List[dict]] = {}
    for surah in surahs:
        for page_obj in surah["pages"]:
            by_page.setdefault(int(page_obj["page"]), []).extend(page_obj["lines"])

    for page, page_lines in by_page.items():
        page_lines.sort(key=lambda line: int(line["line"]))
        extracted = svg_results[page]
        coord_lines = extracted["lines"]
        if len(coord_lines) != len(page_lines):
            raise RuntimeError(
                f"Page {page}: coordinate lines={len(coord_lines)} but DOCX lines={len(page_lines)}."
            )
        for doc_line, coord in zip(page_lines, coord_lines):
            doc_line["y_top"] = coord["y_top"]
            doc_line["y_bottom"] = coord["y_bottom"]
            doc_line["x_start"] = coord["x_start"]
            doc_line["x_end"] = coord["x_end"]


def output_filename(surah: dict):
    title = re.sub(r"[\\/:*?\"<>|]", "_", surah["surahName"])
    return f"{surah['surahId']:03d}_{title}.json"


def write_outputs(surahs: List[dict], output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    for old in output_dir.glob("*.json"):
        old.unlink()
    for surah in surahs:
        (output_dir / output_filename(surah)).write_text(json.dumps(surah, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_config(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("quranpedia_repository", "svg_subdir"):
        if key not in data:
            raise ValueError(f"config.json is missing {key}")
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docx", required=True, type=Path)
    ap.add_argument("--config", default=Path("config.json"), type=Path)
    ap.add_argument("--output-dir", default=Path("output"), type=Path)
    args = ap.parse_args()
    if not args.docx.exists():
        raise FileNotFoundError(args.docx)
    config = load_config(args.config)
    surahs = extract_docx_layout(args.docx)
    all_pages = sorted({int(p["page"]) for s in surahs for p in s["pages"]})
    expected_lines_by_page = {}
    for surah in surahs:
        for page_obj in surah["pages"]:
            p = int(page_obj["page"])
            expected_lines_by_page[p] = expected_lines_by_page.get(p, 0) + len(page_obj["lines"])

    with tempfile.TemporaryDirectory(prefix="quranpedia-layout-") as temp_name:
        temp_dir = Path(temp_name)
        svg_root = prepare_quranpedia_source(config, temp_dir)
        svg_results = extract_svg_coordinates(svg_root, expected_lines_by_page)

    merge_coordinates(surahs, svg_results)
    write_outputs(surahs, args.output_dir)
    print(f"Generated {len(surahs)} surah JSON files in {args.output_dir}")
    print(f"Pages: {all_pages[0]}..{all_pages[-1]} ({len(all_pages)})")
    print(f"SVG pages processed: {len(svg_results)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
