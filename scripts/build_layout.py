#!/usr/bin/env python3
"""
Build per-surah Quran layout JSON from the supplied DOCX, then optionally merge
per-page line coordinates extracted from Quranpedia SVG files via extract_lines.py.

DOCX is the source of truth for:
  - surah boundaries and numbering (heading order)
  - physical line breaks
  - physical page breaks
  - first/last word of every line
  - ayah marker positions and wordIndex

SVG/extract_lines.py is used only for:
  - y_top / y_bottom
  - x_start / x_end

The resulting surah JSON follows the structure of the provided reference file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from zipfile import ZipFile
import xml.etree.ElementTree as ET

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"
SVG_NUMBER_RE = re.compile(r"(?:^|[^0-9])([0-9]{1,4})(?:\D|$)")
ARABIC_DIGITS_RE = re.compile(r"^[٠-٩۰-۹0-9]+$")

META_SINGLE = "مصحف رواية قالون عن نافع"
META_VARIANTS = (
    "بوجه صلة ميم الجمع وتوسط المنفصل",
    "بوجه صلة ميم الجمع وتوسط المنفصل",
)


def arabic_digits_to_int(value: str) -> int:
    table = str.maketrans(
        "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
        "01234567890123456789",
    )
    return int(value.translate(table))


def clean_surah_name(heading: str) -> str:
    """Turn 'سُورَةُ البَقَرَةِ' into a compact title like 'البقرة'."""
    value = heading.strip()
    if value.startswith("سُورَةُ "):
        value = value[len("سُورَةُ "):]

    # Remove Arabic combining marks for the title only. Quran text is preserved.
    value = "".join(
        ch for ch in unicodedata.normalize("NFD", value)
        if unicodedata.category(ch) != "Mn"
    )
    value = value.replace("ٱ", "ا")
    for ch in "ۖۗۘۙۚۛۜ۝":
        value = value.replace(ch, "")
    return " ".join(value.split())


def is_surah_heading(text: str) -> bool:
    return text.strip().startswith("سُورَةُ ")


def iter_docx_events(paragraph: ET.Element) -> Iterable[Tuple[str, Optional[str]]]:
    """Yield text / line-break / rendered-page-break events in document order.

    The DOCX sometimes splits the printed metadata string across many separate
    <w:t> nodes. We therefore concatenate text and structural markers first,
    remove metadata once, then restore the event boundaries.
    """
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

def strip_metadata(text: str) -> str:
    # The supplied DOCX contains a repeated printed-header string in a few
    # paragraphs. It is not Quran text and must not become a layout line.
    value = text
    value = value.replace(META_SINGLE, "")
    for variant in META_VARIANTS:
        value = value.replace(variant, "")
    value = value.replace("مصحف رواية قالون عن نافعبوجه صلة ميم الجمع وتوسط المنفصل", "")
    return value


def split_word_and_marker(token: str) -> List[str]:
    """Split a token such as 'الكتاب ١' or 'الكلمة١' into word + marker."""
    token = token.strip()
    if not token:
        return []
    if ARABIC_DIGITS_RE.fullmatch(token):
        return [token]

    # Verse numbers in this DOCX normally have whitespace/NBSP before them,
    # but this also handles the attached form defensively.
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
    """Parse one physical DOCX line and return (line object, new ayah, new wordIndex)."""
    tokens = line_text.replace("\xa0", " ").split()

    first_word = None
    last_word = None
    last_item = None
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

            word = part
            word_obj = {
                "type": "word",
                "surah": surah_id,
                "ayah": ayah,
                "word": word,
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

    line_obj = {
        "start": first_word,
        "end": end_obj,
        "y_top": None,
        "y_bottom": None,
        "x_start": None,
        "x_end": None,
        "verses_on_line": [f"{surah_id}:{a}" for a in ayahs],
    }
    return line_obj, ayah, word_index


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
    # Page numbering in this DOCX is represented reliably by the Word
    # `lastRenderedPageBreak` markers: the document contains one marker per
    # rendered page transition (603 for a 604-page mushaf). Explicit `w:br
    # type="page"` entries are formatting instructions and are treated only
    # as line delimiters; otherwise they would double-count page transitions.

    def ensure_page(surah: dict, page_number: int) -> dict:
        for page_obj in surah["pages"]:
            if page_obj["page"] == page_number:
                return page_obj
        page_obj = {"page": page_number, "lines": []}
        surah["pages"].append(page_obj)
        surah["pages"].sort(key=lambda x: x["page"])
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
        line_obj["line"] = line_in_page
        page_obj = ensure_page(current, page)
        # Keep the canonical field order used by the supplied reference JSON.
        page_obj["lines"].append({
            "line": line_obj.pop("line"),
            "start": line_obj["start"],
            "end": line_obj["end"],
            "y_top": line_obj["y_top"],
            "y_bottom": line_obj["y_bottom"],
            "x_start": line_obj["x_start"],
            "x_end": line_obj["x_end"],
            "verses_on_line": line_obj["verses_on_line"],
        })
        current_ayah = new_ayah
        current_word_index = new_word_index

    for paragraph in paragraphs:
        raw_text = "".join(
            t.text or "" for t in paragraph.findall(f".//{W}t")
        )
        if is_surah_heading(raw_text):
            # lastRenderedPageBreak is the authoritative rendered-page marker.
            heading_lrb_count = sum(1 for event_type, _ in iter_docx_events(paragraph) if event_type == "lrb")
            if heading_lrb_count:
                page += heading_lrb_count
                line_in_page = 0

            current = {
                "surahId": len(surahs) + 1,
                "surahName": clean_surah_name(raw_text),
                "englishName": None,
                "type": None,
                "ayaCount": 0,
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
                # This is the authoritative rendered-page transition. It can
                # appear at the start of a heading or between line fragments.
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

            # w:br type="page" is only used here to terminate the current
            # physical line. The page number itself changes at lastRenderedPageBreak.
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
        else:
            surah["startPage"] = None
            surah["endPage"] = None

        # Last completed marker in this surah. The DOCX is the source for this.
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
        surah["totalCompletedLines"] = sum(len(p["lines"]) for p in surah["pages"])

        # Match the key order of the supplied reference JSON.
        ordered = {
            "surahId": surah["surahId"],
            "surahName": surah["surahName"],
            "englishName": surah["englishName"],
            "ayaCount": surah["ayaCount"],
            "type": surah["type"],
            "startPage": surah["startPage"],
            "endPage": surah["endPage"],
            "totalPagesInSurah": surah["totalPagesInSurah"],
            "recordedPagesCount": surah["recordedPagesCount"],
            "totalCompletedLines": surah["totalCompletedLines"],
            "pages": surah["pages"],
        }
        surah.clear()
        surah.update(ordered)

    return surahs


def page_number_from_filename(path: Path) -> Optional[int]:
    m = re.search(r"(?<!\d)(\d{1,4})(?!\d)", path.name)
    if not m:
        return None
    return int(m.group(1))


def prepare_svg(svg_path: Path, temp_dir: Path) -> Path:
    """Return a normal SVG path. Transparently decompress .svg.br when needed."""
    if svg_path.suffix.lower() != ".br":
        return svg_path

    try:
        import brotli  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Found .svg.br files but Python package 'brotli' is not installed. "
            "Run: python -m pip install Brotli"
        ) from exc

    out = temp_dir / svg_path.with_suffix("").name
    out.write_bytes(brotli.decompress(svg_path.read_bytes()))
    return out


def collect_svg_files(svg_dir: Path) -> Dict[int, Path]:
    candidates: Dict[int, Path] = {}
    for path in sorted(svg_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".svg", ".br"}:
            continue
        if path.name.lower().endswith(".svg.br") or path.suffix.lower() == ".svg":
            page = page_number_from_filename(path)
            if page is None:
                continue
            # Prefer an uncompressed SVG over its .br counterpart.
            if page not in candidates or candidates[page].suffix.lower() == ".br":
                candidates[page] = path
    return candidates


def extract_svg_coordinates(
    svg_dir: Optional[Path],
    required_pages: Iterable[int],
    temp_dir: Path,
    fail_on_missing: bool,
) -> Dict[int, dict]:
    if svg_dir is None:
        return {}
    if not svg_dir.exists():
        if fail_on_missing:
            raise FileNotFoundError(f"SVG directory not found: {svg_dir}")
        print(f"[WARN] SVG directory not found: {svg_dir}", file=sys.stderr)
        return {}

    # Import the user's proven geometry extractor rather than duplicating it.
    scripts_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(scripts_dir))
    import extract_lines  # type: ignore

    files = collect_svg_files(svg_dir)
    required = sorted(set(required_pages))
    missing = [p for p in required if p not in files]
    if missing and fail_on_missing:
        raise FileNotFoundError(
            f"Missing SVG files for pages: {', '.join(map(str, missing[:30]))}"
            + (" ..." if len(missing) > 30 else "")
        )

    results: Dict[int, dict] = {}
    failures = []
    for page, svg_path in sorted(files.items()):
        try:
            real_svg = prepare_svg(svg_path, temp_dir)
            results[page] = extract_lines.extract(real_svg)
        except Exception as exc:  # noqa: BLE001
            failures.append((page, svg_path.name, str(exc)))

    if failures:
        details = "\n".join(f"  page {p}: {name}: {err}" for p, name, err in failures[:20])
        raise RuntimeError(f"SVG extraction failed:\n{details}")

    if missing:
        print(
            f"[WARN] {len(missing)} pages have no SVG; their coordinates remain null.",
            file=sys.stderr,
        )
    return results


def merge_coordinates(surahs: List[dict], svg_results: Dict[int, dict]) -> None:
    for surah in surahs:
        for page_obj in surah["pages"]:
            page = page_obj["page"]
            extracted = svg_results.get(page)
            if not extracted:
                continue

            coord_lines = extracted.get("lines", [])
            by_line = {int(x["line_number"]): x for x in coord_lines}

            for line_obj in page_obj["lines"]:
                info = by_line.get(int(line_obj["line"]))
                if not info:
                    continue
                line_obj["y_top"] = info.get("y_top")
                line_obj["y_bottom"] = info.get("y_bottom")
                line_obj["x_start"] = info.get("x_start")
                line_obj["x_end"] = info.get("x_end")


def output_filename(surah: dict) -> str:
    title = re.sub(r"[\\/:*?\"<>|]", "_", surah["surahName"])
    return f"{surah['surahId']:03d}_{title}.json"


def write_outputs(surahs: List[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    # Remove generated JSONs from previous runs so deleted/renamed surahs don't linger.
    for old in output_dir.glob("*.json"):
        old.unlink()

    for surah in surahs:
        path = output_dir / output_filename(surah)
        path.write_text(
            json.dumps(surah, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--docx", required=True, type=Path, help="Input DOCX")
    ap.add_argument("--svg-dir", type=Path, default=None, help="Directory containing page SVG/ SVG.BR files")
    ap.add_argument("--output-dir", type=Path, default=Path("output"), help="Per-surah JSON output directory")
    ap.add_argument(
        "--fail-on-missing-svg",
        action="store_true",
        help="Fail when any page referenced by the DOCX has no SVG file",
    )
    args = ap.parse_args()

    if not args.docx.exists():
        raise FileNotFoundError(f"DOCX not found: {args.docx}")

    surahs = extract_docx_layout(args.docx)
    all_pages = [p["page"] for s in surahs for p in s["pages"]]

    with tempfile.TemporaryDirectory(prefix="quran_layout_") as temp:
        svg_results = extract_svg_coordinates(
            args.svg_dir,
            all_pages,
            Path(temp),
            args.fail_on_missing_svg,
        )

    merge_coordinates(surahs, svg_results)
    write_outputs(surahs, args.output_dir)

    print(f"Generated {len(surahs)} surah files in {args.output_dir}")
    print(
        "Pages from DOCX: "
        f"{min(all_pages) if all_pages else '-'}..{max(all_pages) if all_pages else '-'}; "
        f"SVG pages loaded: {len(svg_results)}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
