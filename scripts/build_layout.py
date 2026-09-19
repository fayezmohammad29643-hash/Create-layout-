#!/usr/bin/env python3
"""
Build one JSON layout file per surah from the supplied DOCX, then merge the
real line coordinates from Quranpedia's Qalun/KFQC SVG pages.

Sources of truth
----------------
DOCX:
  * surah order / numbering
  * rendered page transitions
  * physical line breaks
  * first/last word of each physical line
  * wordIndex and ayah-marker positions

Quranpedia SVG + extract_lines.py:
  * y_top / y_bottom / x_start / x_end for each physical line

The SVG files are downloaded only into a temporary directory during the run.
Nothing from the SVG repository is copied into this project's Git repository.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from zipfile import ZipFile
import xml.etree.ElementTree as ET

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"
ARABIC_DIGITS_RE = re.compile(r"^[٠-٩۰-۹0-9]+$")

META_SINGLE = "مصحف رواية قالون عن نافع"
META_VARIANT = "بوجه صلة ميم الجمع وتوسط المنفصل"


def arabic_digits_to_int(value: str) -> int:
    table = str.maketrans(
        "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
        "01234567890123456789",
    )
    return int(value.translate(table))


def clean_surah_name(heading: str) -> str:
    value = heading.strip()
    if value.startswith("سُورَةُ "):
        value = value[len("سُورَةُ "):]

    # Titles are normalized only for filenames/title display.
    # Quranic words themselves are never normalized this way.
    value = "".join(
        ch
        for ch in unicodedata.normalize("NFD", value)
        if unicodedata.category(ch) != "Mn"
    )
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
    value = value.replace(META_SINGLE + META_VARIANT, "")
    value = value.replace(META_VARIANT + META_SINGLE, "")
    return value


def iter_docx_events(paragraph: ET.Element) -> Iterable[Tuple[str, Optional[str]]]:
    """Read text and structural page/line breaks in document order."""
    LINE = "\ue000"
    PAGE = "\ue001"
    LRB = "\ue002"

    combined: List[str] = []
    for node in paragraph.iter():
        if node.tag == f"{W}t":
            combined.append(node.text or "")
        elif node.tag == f"{W}br":
            combined.append(PAGE if node.get(f"{W}type") == "page" else LINE)
        elif node.tag == f"{W}lastRenderedPageBreak":
            combined.append(LRB)

    cleaned = strip_metadata("".join(combined))
    token_re = re.compile(
        f"({re.escape(LINE)}|{re.escape(PAGE)}|{re.escape(LRB)})"
    )
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

    # Handles the common case where the ayah number is attached to the word.
    m = re.match(r"^(.*?)([٠-٩۰-۹0-9]+)$", token)
    if m and m.group(1):
        return [m.group(1), m.group(2)]
    return [token]


def build_line(
    surah_id: int,
    state_ayah: int,
    state_word_index: int,
    line_text: str,
) -> Tuple[Optional[dict], int, int]:
    tokens = line_text.replace("\xa0", " ").split()

    first_word: Optional[dict] = None
    last_word: Optional[dict] = None
    last_item: Optional[Tuple[str, object]] = None
    ayahs: List[int] = []

    ayah = state_ayah
    word_index = state_word_index

    for raw_token in tokens:
        token = raw_token.strip().replace("۞", "")
        if not token:
            continue

        for part in split_word_and_marker(token):
            if not part:
                continue

            if ARABIC_DIGITS_RE.fullmatch(part):
                marker_number = arabic_digits_to_int(part)
                last_item = ("marker", marker_number)
                ayah = marker_number + 1
                word_index = 1
                continue

            word_obj = {
                "type": "word",
                "surah": surah_id,
                "ayah": ayah,
                "word": part,
                "wordIndex": word_index,
            }
            if first_word is None:
                first_word = dict(word_obj)
            last_word = dict(word_obj)
            last_item = ("word", dict(word_obj))
            if ayah not in ayahs:
                ayahs.append(ayah)
            word_index += 1

    if first_word is None:
        return None, ayah, word_index

    if last_item and last_item[0] == "marker":
        number = int(last_item[1])
        end_obj = {
            "type": "ayah_marker",
            "surah": surah_id,
            "ayah": number,
            "number": number,
        }
    else:
        end_obj = last_word

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
    surahs: List[dict] = []
    current: Optional[dict] = None
    current_ayah = 1
    current_word_index = 1
    page = 1
    line_in_page = 0

    # In this supplied DOCX, lastRenderedPageBreak tracks rendered page
    # transitions. Explicit w:br type="page" is treated as a line terminator.
    def ensure_page(surah: dict, page_number: int) -> dict:
        for page_obj in surah["pages"]:
            if page_obj["page"] == page_number:
                return page_obj
        page_obj = {"page": page_number, "lines": []}
        surah["pages"].append(page_obj)
        return page_obj

    def emit_line(line_text: str) -> None:
        nonlocal current_ayah, current_word_index, line_in_page
        if current is None or not line_text.strip():
            return
        line_obj, new_ayah, new_word_index = build_line(
            current["surahId"], current_ayah, current_word_index, line_text
        )
        if line_obj is None:
            return
        line_in_page += 1
        page_obj = ensure_page(current, page)
        page_obj["lines"].append(
            {
                "line": line_in_page,
                "start": line_obj["start"],
                "end": line_obj["end"],
                "y_top": line_obj["y_top"],
                "y_bottom": line_obj["y_bottom"],
                "x_start": line_obj["x_start"],
                "x_end": line_obj["x_end"],
                "verses_on_line": line_obj["verses_on_line"],
            }
        )
        current_ayah = new_ayah
        current_word_index = new_word_index

    for paragraph in paragraphs:
        raw_text = "".join(t.text or "" for t in paragraph.findall(f".//{W}t"))

        if is_surah_heading(raw_text):
            heading_lrb_count = sum(
                1
                for event_type, _ in iter_docx_events(paragraph)
                if event_type == "lrb"
            )
            if heading_lrb_count:
                page += heading_lrb_count
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
            current_ayah = 1
            current_word_index = 1
            continue

        is_bismillah = raw_text.strip().startswith("بِسۡمِ ٱللَّهِ")
        content_text = strip_metadata(raw_text).strip()
        is_content = bool(content_text) and not is_bismillah
        cur_line = ""

        for event_type, event_value in iter_docx_events(paragraph):
            if event_type == "lrb":
                if cur_line.strip() and is_content and current is not None:
                    emit_line(cur_line)
                    cur_line = ""
                page += 1
                line_in_page = 0
                continue

            if event_type == "text":
                if is_content and current is not None:
                    cur_line += event_value or ""
                continue

            if event_type == "br":
                if cur_line.strip() and is_content and current is not None:
                    emit_line(cur_line)
                cur_line = ""

        if cur_line.strip() and is_content and current is not None:
            emit_line(cur_line)

    for surah in surahs:
        surah["pages"].sort(key=lambda p: p["page"])
        if surah["pages"]:
            surah["startPage"] = surah["pages"][0]["page"]
            surah["endPage"] = surah["pages"][-1]["page"]
        max_ayah = 0
        for page_obj in surah["pages"]:
            for line_obj in page_obj["lines"]:
                for edge in (line_obj["start"], line_obj["end"]):
                    if edge.get("type") == "ayah_marker":
                        max_ayah = max(max_ayah, int(edge["number"]))
                    elif edge.get("type") == "word":
                        max_ayah = max(max_ayah, int(edge["ayah"]))
        surah["ayaCount"] = max_ayah
        surah["totalPagesInSurah"] = len(surah["pages"])
        surah["recordedPagesCount"] = len(surah["pages"])
        surah["totalCompletedLines"] = sum(
            len(p["lines"]) for p in surah["pages"]
        )

    return surahs


def fetch_bytes(url: str, retries: int = 4) -> bytes:
    headers = {"User-Agent": "quran-layout-builder/1.0"}
    last_error: Optional[Exception] = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            last_error = exc
            # Retry transient throttling/server errors; fail fast on other 4xx.
            if exc.code not in {408, 429} and not (500 <= exc.code <= 599):
                raise
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
        import time
        time.sleep(2 ** attempt)
    raise RuntimeError(f"Unable to download {url}: {last_error}")


def download_svg_page(
    page: int,
    base_url: str,
    fallback_url: Optional[str],
    destination_dir: Path,
) -> Path:
    target = destination_dir / f"{page:03d}.svg"
    url = base_url.format(page=page)
    try:
        target.write_bytes(fetch_bytes(url))
        return target
    except urllib.error.HTTPError as exc:
        if exc.code != 404 or not fallback_url:
            raise RuntimeError(f"Page {page}: failed to download {url}: HTTP {exc.code}") from exc

    # Optional compressed fallback.
    fallback = fallback_url.format(page=page)
    data = fetch_bytes(fallback)
    if fallback.lower().endswith(".br"):
        try:
            import brotli  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "Fallback .svg.br was returned, but Brotli is not installed."
            ) from exc
        data = brotli.decompress(data)
    target.write_bytes(data)
    return target


def fetch_required_svgs(
    pages: Iterable[int],
    temp_dir: Path,
    base_url: str,
    fallback_url: Optional[str],
    jobs: int,
) -> Dict[int, Path]:
    pages = sorted(set(int(p) for p in pages))
    results: Dict[int, Path] = {}
    failures: List[Tuple[int, str]] = []

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        future_map = {
            pool.submit(
                download_svg_page,
                page,
                base_url,
                fallback_url,
                temp_dir,
            ): page
            for page in pages
        }
        for future in as_completed(future_map):
            page = future_map[future]
            try:
                results[page] = future.result()
            except Exception as exc:  # noqa: BLE001
                failures.append((page, str(exc)))

    if failures:
        details = "\n".join(f"  page {p}: {e}" for p, e in sorted(failures)[:30])
        raise RuntimeError("SVG download failed:\n" + details)
    return results


def extract_svg_coordinates(svg_paths: Dict[int, Path]) -> Dict[int, dict]:
    scripts_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(scripts_dir))
    import extract_lines  # type: ignore

    results: Dict[int, dict] = {}
    failures: List[Tuple[int, str]] = []
    for page, svg_path in sorted(svg_paths.items()):
        try:
            results[page] = extract_lines.extract(svg_path)
        except Exception as exc:  # noqa: BLE001
            failures.append((page, str(exc)))
    if failures:
        details = "\n".join(f"  page {p}: {e}" for p, e in failures[:30])
        raise RuntimeError("SVG extraction failed:\n" + details)
    return results


def merge_coordinates(surahs: List[dict], svg_results: Dict[int, dict]) -> None:
    for surah in surahs:
        for page_obj in surah["pages"]:
            page = int(page_obj["page"])
            extracted = svg_results.get(page)
            if not extracted:
                raise RuntimeError(f"No extracted SVG coordinates for page {page}")

            coord_lines = extracted.get("lines", [])
            by_line = {int(x["line_number"]): x for x in coord_lines}
            expected_lines = page_obj["lines"]

            missing = [
                int(line["line"])
                for line in expected_lines
                if int(line["line"]) not in by_line
            ]
            if missing:
                raise RuntimeError(
                    f"Page {page}: SVG has {len(coord_lines)} extracted lines, "
                    f"but DOCX needs line(s) {missing[:10]} for surah {surah['surahId']}"
                )

            for line_obj in expected_lines:
                info = by_line[int(line_obj["line"])]
                line_obj["y_top"] = info.get("y_top")
                line_obj["y_bottom"] = info.get("y_bottom")
                line_obj["x_start"] = info.get("x_start")
                line_obj["x_end"] = info.get("x_end")


def output_filename(surah: dict) -> str:
    title = re.sub(r"[\\/:*?\"<>|]", "_", surah["surahName"])
    return f"{surah['surahId']:03d}_{title}.json"


def write_outputs(surahs: List[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for old in output_dir.glob("*.json"):
        old.unlink()
    for surah in surahs:
        path = output_dir / output_filename(surah)
        path.write_text(
            json.dumps(surah, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def load_config(config_path: Path) -> dict:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if "quranpedia_svg_base_url" not in data:
        raise ValueError("config.json is missing quranpedia_svg_base_url")
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--docx", required=True, type=Path)
    ap.add_argument("--config", type=Path, default=Path("config.json"))
    ap.add_argument("--output-dir", type=Path, default=Path("output"))
    ap.add_argument("--jobs", type=int, default=6)
    args = ap.parse_args()

    if not args.docx.exists():
        raise FileNotFoundError(f"DOCX not found: {args.docx}")
    if not args.config.exists():
        raise FileNotFoundError(f"Config not found: {args.config}")

    config = load_config(args.config)
    surahs = extract_docx_layout(args.docx)
    all_pages = sorted({p["page"] for s in surahs for p in s["pages"]})

    with tempfile.TemporaryDirectory(prefix="quranpedia_svg_") as temp_name:
        temp_dir = Path(temp_name)
        svg_paths = fetch_required_svgs(
            all_pages,
            temp_dir,
            config["quranpedia_svg_base_url"],
            config.get("quranpedia_svg_fallback_url"),
            args.jobs,
        )
        svg_results = extract_svg_coordinates(svg_paths)

    merge_coordinates(surahs, svg_results)
    write_outputs(surahs, args.output_dir)

    print(f"Generated {len(surahs)} surah JSON files in {args.output_dir}")
    print(f"DOCX pages: {all_pages[0]}..{all_pages[-1]} ({len(all_pages)} pages)")
    print(f"Quranpedia SVG pages downloaded/extracted: {len(svg_results)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
