#!/usr/bin/env python3
"""Build Quran page layout directly from the supplied DOCX.

The DOCX is the only layout source.  SVG/Quranpedia is not used.

The builder preserves:
- page boundaries from w:lastRenderedPageBreak;
- explicit line breaks from w:br (non-page breaks);
- paragraph alignment and spacing;
- the exact line text from the DOCX;
- run-level font sizes;

All font-family choices are overridden to exactly one approved KFGQPC
Uthmanic font according to config.json (Hafs or Qalun). Any other font family
embedded in the DOCX is ignored for the generated layout.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from zipfile import ZipFile

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"

META_PHRASES = (
    "مصحف رواية قالون عن نافع",
    "بوجه صلة ميم الجمع وتوسط المنفصل",
)
META_CHUNKS = {
    "مصحف رواية قالون عن نافع",
    "بوجه",
    "صلة",
    "ميم الجمع وتوسط المنفصل",
}
ARABIC_DIGITS_RE = re.compile(r"^[٠-٩۰-۹0-9]+$")
TRAILING_DIGITS_RE = re.compile(r"^(.*?)([٠-٩۰-۹0-9]+)$")


def arabic_digits_to_int(value: str) -> int:
    table = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
    return int(value.translate(table))


def strip_meta(text: str) -> str:
    value = text or ""
    # In the DOCX the metadata is sometimes concatenated directly at run
    # boundaries (for example: "نافعبوجه"). Allow arbitrary spacing so the
    # metadata cannot leak into Quran lines.
    value = re.sub(r"مصحف\s*رواية\s*قالون\s*عن\s*نافع", "", value)
    value = re.sub(r"بوجه\s*صلة\s*ميم\s*الجمع\s*وتوسط\s*المنفصل", "", value)
    return value


def clean_inline_chunk(text: str) -> str:
    value = strip_meta(text)
    if normalize_structural_arabic(value) in META_CHUNKS:
        return ""
    return value


def normalize_structural_arabic(text: str) -> str:
    value = unicodedata.normalize("NFD", text or "")
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("ـ", "").replace("ٱ", "ا")
    return " ".join(value.replace("\xa0", " ").split())


def is_surah_heading(text: str) -> bool:
    return strip_meta(text).strip().startswith("سُورَةُ ")


def is_bismillah(text: str) -> bool:
    normalized = normalize_structural_arabic(strip_meta(text))
    return normalized.startswith("بسم الله الرحمن الرحيم")


def clean_surah_name(text: str) -> str:
    value = strip_meta(text).strip()
    value = re.sub(r"^سُورَةُ\s*", "", value)
    return value


def parse_int_attr(element: ET.Element | None, local_name: str) -> int | None:
    if element is None:
        return None
    value = element.get(f"{W}{local_name}")
    return int(value) if value and value.isdigit() else None


def extract_page_geometry(root: ET.Element) -> dict:
    body = root.find(f".//{W}body")
    sect = body.find(f"./{W}sectPr") if body is not None else None
    if sect is None:
        return {}
    pg_sz = sect.find(f"./{W}pgSz")
    pg_mar = sect.find(f"./{W}pgMar")
    doc_grid = sect.find(f"./{W}docGrid")
    return {
        "page_size_twips": {
            "width": parse_int_attr(pg_sz, "w"),
            "height": parse_int_attr(pg_sz, "h"),
        },
        "margins_twips": {
            "top": parse_int_attr(pg_mar, "top"),
            "right": parse_int_attr(pg_mar, "right"),
            "bottom": parse_int_attr(pg_mar, "bottom"),
            "left": parse_int_attr(pg_mar, "left"),
            "header": parse_int_attr(pg_mar, "header"),
            "footer": parse_int_attr(pg_mar, "footer"),
            "gutter": parse_int_attr(pg_mar, "gutter"),
        },
        "doc_grid": {
            "line_pitch_twips": parse_int_attr(doc_grid, "linePitch") if doc_grid is not None else None,
            "line_rule": doc_grid.get(f"{W}lineRule") if doc_grid is not None else None,
        },
    }


def paragraph_properties(paragraph: ET.Element) -> dict:
    ppr = paragraph.find(f"./{W}pPr")
    result = {
        "alignment": "left",
        "bidi": False,
        "spacing": {},
        "indent": {},
        "widow_control": None,
        "auto_space_de": None,
        "auto_space_dn": None,
        "adjust_right_ind": None,
    }
    if ppr is None:
        return result
    jc = ppr.find(f"./{W}jc")
    if jc is not None and jc.get(f"{W}val"):
        result["alignment"] = jc.get(f"{W}val")
    result["bidi"] = ppr.find(f"./{W}bidi") is not None
    spacing = ppr.find(f"./{W}spacing")
    if spacing is not None:
        for key in ("before", "after", "line", "lineRule"):
            val = spacing.get(f"{W}{key}")
            if val is not None:
                result["spacing"][key] = int(val) if key != "lineRule" and val.isdigit() else val
    ind = ppr.find(f"./{W}ind")
    if ind is not None:
        for key in ("left", "right", "firstLine", "hanging"):
            val = ind.get(f"{W}{key}")
            if val is not None:
                result["indent"][key] = int(val) if val.isdigit() else val
    for tag, key in (
        ("widowControl", "widow_control"),
        ("autoSpaceDE", "auto_space_de"),
        ("autoSpaceDN", "auto_space_dn"),
        ("adjustRightInd", "adjust_right_ind"),
    ):
        el = ppr.find(f"./{W}{tag}")
        if el is not None:
            val = el.get(f"{W}val")
            result[key] = True if val is None else val not in ("0", "false", "off")
    return result


def run_style(run: ET.Element, forced_font: str) -> dict:
    rpr = run.find(f"./{W}rPr")
    style = {"font_family": forced_font, "font_size_half_points": None, "rtl": True}
    if rpr is None:
        return style
    sz = rpr.find(f"./{W}sz")
    szcs = rpr.find(f"./{W}szCs")
    if sz is not None and sz.get(f"{W}val"):
        style["font_size_half_points"] = int(sz.get(f"{W}val"))
    elif szcs is not None and szcs.get(f"{W}val"):
        style["font_size_half_points"] = int(szcs.get(f"{W}val"))
    style["rtl"] = rpr.find(f"./{W}rtl") is not None or style["rtl"]
    # Preserve only shaping-relevant/visible run flags that can materially affect rendering.
    for tag, key in (
        ("b", "bold"),
        ("i", "italic"),
        ("vanish", "hidden"),
    ):
        style[key] = rpr.find(f"./{W}{tag}") is not None
    return style


def iter_inline_events(paragraph: ET.Element, forced_font: str):
    """Yield inline text/break events together with the exact run style."""
    for node in paragraph.iter():
        if node.tag != f"{W}r":
            continue
        style = run_style(node, forced_font)
        for child in node.iter():
            if child is node or child.tag == f"{W}rPr":
                continue
            if child.tag == f"{W}t":
                yield ("text", child.text or "", style)
            elif child.tag == f"{W}br":
                if child.get(f"{W}type") == "page":
                    yield ("page_break", None, style)
                else:
                    yield ("line_break", None, style)
            elif child.tag == f"{W}lastRenderedPageBreak":
                yield ("rendered_page_break", None, style)


def iter_paragraph_events(paragraph: ET.Element, forced_font: str):
    """Compatibility wrapper for callers that only need event values."""
    yield from iter_inline_events(paragraph, forced_font)


def paragraph_runs(paragraph: ET.Element, forced_font: str) -> list[dict]:
    runs = []
    for typ, text, style in iter_inline_events(paragraph, forced_font):
        if typ != "text" or not text:
            continue
        text = clean_inline_chunk(text)
        if text:
            runs.append({"text": text, **style})
    return merge_adjacent_runs(runs)


def merge_adjacent_runs(runs: list[dict]) -> list[dict]:
    merged = []
    for item in runs:
        if merged:
            same = all(merged[-1].get(k) == item.get(k) for k in item.keys() if k != "text")
            if same:
                merged[-1]["text"] += item["text"]
                continue
        merged.append(dict(item))
    return merged

def split_word_and_marker(token: str) -> list[str]:
    token = token.strip()
    if not token:
        return []
    token = token.replace("۞", "")
    if not token:
        return []
    if ARABIC_DIGITS_RE.fullmatch(token):
        return [token]
    match = TRAILING_DIGITS_RE.match(token)
    if match and match.group(1):
        return [match.group(1), match.group(2)]
    return [token]


def build_endpoints(surah_id: int, ayah: int, word_index: int, line_text: str) -> tuple[dict | None, int, int, list[str]]:
    first = None
    last_word = None
    last_item = None
    verses = []
    for raw in re.split(r"\s+", line_text.replace("\xa0", " ").strip()):
        for part in split_word_and_marker(raw):
            if not part:
                continue
            if ARABIC_DIGITS_RE.fullmatch(part):
                number = arabic_digits_to_int(part)
                marker = {
                    "type": "ayah_marker",
                    "surah": surah_id,
                    "ayah": number,
                    "number": number,
                }
                if first is None:
                    first = marker
                last_item = marker
                ayah = number + 1
                word_index = 1
                continue
            word = {
                "type": "word",
                "surah": surah_id,
                "ayah": ayah,
                "word": part,
                "wordIndex": word_index,
            }
            if first is None:
                first = dict(word)
            last_word = dict(word)
            last_item = word
            label = f"{surah_id}:{ayah}"
            if label not in verses:
                verses.append(label)
            word_index += 1
    if first is None:
        return None, ayah, word_index, verses
    end = last_item
    return ({"start": first, "end": end, "verses_on_line": verses}), ayah, word_index, verses


def make_line(surah_id: int, line_number: int, line_text: str, paragraph_props: dict, runs: list[dict], ayah: int, word_index: int) -> tuple[dict | None, int, int]:
    line_text = strip_meta(line_text)
    if not line_text.strip():
        return None, ayah, word_index
    endpoints, new_ayah, new_word_index, verses = build_endpoints(surah_id, ayah, word_index, line_text)
    if endpoints is None:
        return None, new_ayah, new_word_index
    return {
        "line": line_number,
        "text": line_text,
        "start": endpoints["start"],
        "end": endpoints["end"],
        # Explicit aliases for the mushaf-line boundary fields. These make
        # the JSON convenient for Flutter while keeping the richer start/end
        # objects (including ayah-marker type) intact.
        "first_word": endpoints["start"],
        "end_word": endpoints["end"],
        "verses_on_line": verses,
        "alignment": paragraph_props["alignment"],
        "bidi": paragraph_props["bidi"],
        "spacing": paragraph_props["spacing"],
        "runs": runs,
    }, new_ayah, new_word_index


def extract_docx(docx_path: Path, forced_font: str) -> dict:
    with ZipFile(docx_path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))

    page_geometry = extract_page_geometry(root)
    body = root.find(f".//{W}body")
    paragraphs = body.findall(f"./{W}p") if body is not None else []

    pages = defaultdict(lambda: {"elements": [], "lines": [], "surah_ids": []})
    surahs: dict[int, dict] = {}
    current_surah: dict | None = None
    current_ayah = 1
    current_word_index = 1
    page = 1
    line_in_page = 0

    def page_boundary() -> None:
        nonlocal page, line_in_page
        page += 1
        line_in_page = 0

    for paragraph in paragraphs:
        props = paragraph_properties(paragraph)
        events = list(iter_paragraph_events(paragraph, forced_font))
        raw_text = "".join(text for typ, text, _ in events if typ == "text")
        clean_raw = strip_meta(raw_text)
        # Count rendered page boundaries first and in exact document order.
        if is_surah_heading(clean_raw):
            for typ, _, _ in events:
                if typ == "rendered_page_break":
                    page_boundary()
            sid = len(surahs) + 1
            current_surah = {
                "surahId": sid,
                "surahName": clean_surah_name(clean_raw),
                "pages": [],
            }
            surahs[sid] = current_surah
            current_ayah = 1
            current_word_index = 1
            pages[page]["elements"].append({
                "type": "surah_heading",
                "surah": sid,
                "text": clean_raw.strip(),
                "alignment": props["alignment"],
                "bidi": props["bidi"],
                "spacing": props["spacing"],
                "font_family": forced_font,
            })
            if sid not in pages[page]["surah_ids"]:
                pages[page]["surah_ids"].append(sid)
            continue

        if not clean_raw.strip():
            for typ, _, _ in events:
                if typ == "rendered_page_break":
                    page_boundary()
            continue

        if is_bismillah(clean_raw):
            for typ, _, _ in events:
                if typ == "rendered_page_break":
                    page_boundary()
            pages[page]["elements"].append({
                "type": "bismillah",
                "surah": current_surah["surahId"] if current_surah else None,
                "text": clean_raw.strip(),
                "alignment": props["alignment"],
                "bidi": props["bidi"],
                "spacing": props["spacing"],
                "font_family": forced_font,
                "font_size_half_points": next((r["font_size_half_points"] for r in paragraph_runs(paragraph, forced_font) if r["font_size_half_points"]), None),
            })
            continue

        if current_surah is None:
            continue

        # Quran paragraph: preserve run sizes, force only the family.
        cur_text = ""
        cur_runs: list[dict] = []

        def flush_line() -> None:
            nonlocal cur_text, cur_runs, line_in_page, current_ayah, current_word_index
            line_text = strip_meta(cur_text)
            line_runs = merge_adjacent_runs(cur_runs)
            if not line_text.strip():
                cur_text = ""
                cur_runs = []
                return
            line_in_page += 1
            line_obj, current_ayah, current_word_index = make_line(
                current_surah["surahId"], line_in_page, line_text, props, line_runs, current_ayah, current_word_index
            )
            if line_obj:
                pages[page]["lines"].append(line_obj)
                pages[page]["elements"].append({"type": "quran_line_ref", "line": line_in_page, "surah": current_surah["surahId"]})
            cur_text = ""
            cur_runs = []

        for typ, value, style in events:
            if typ == "text":
                text = clean_inline_chunk(value or "")
                if text:
                    cur_text += text
                    cur_runs.append({"text": text, **style})
            elif typ == "line_break":
                flush_line()
            elif typ == "rendered_page_break":
                flush_line()
                page_boundary()
            elif typ == "page_break":
                # Explicit page breaks duplicate the rendered-page-break model in this DOCX.
                # They are intentionally ignored for numbering to avoid double counting.
                pass
        flush_line()

    # Post-process references and surah page lists.
    for page_no in sorted(pages):
        page_obj = pages[page_no]
        for line in page_obj["lines"]:
            if line["surah"] if "surah" in line else False:
                pass
        for line in page_obj["lines"]:
            sid = line["start"].get("surah")
            if sid and sid not in page_obj["surah_ids"]:
                page_obj["surah_ids"].append(sid)
        page_obj["surah_ids"].sort()
        for sid in page_obj["surah_ids"]:
            surahs[sid]["pages"].append(page_no)

    # Rebuild per-surah line buckets from pages.
    for sid, surah in surahs.items():
        bucket = []
        for page_no in sorted(surah["pages"]):
            lines = [
                line for line in pages[page_no]["lines"]
                if line["start"].get("surah") == sid or line["end"].get("surah") == sid or f"{sid}:" in "|".join(line["verses_on_line"])
            ]
            if lines:
                bucket.append({"page": page_no, "lines": lines})
        surah["pages"] = bucket
        surah["startPage"] = bucket[0]["page"] if bucket else None
        surah["endPage"] = bucket[-1]["page"] if bucket else None
        surah["totalPagesInSurah"] = len(bucket)
        surah["recordedPagesCount"] = len(bucket)
        surah["totalCompletedLines"] = sum(len(p["lines"]) for p in bucket)
        max_ayah = 0
        for p in bucket:
            for line in p["lines"]:
                for endpoint in (line["start"], line["end"]):
                    if endpoint.get("surah") == sid:
                        max_ayah = max(max_ayah, int(endpoint.get("ayah", 0)))
        surah["ayaCount"] = max_ayah

    # Remove internal-only line references and make each page line list the canonical line data.
    for page_obj in pages.values():
        page_obj["elements"] = [e for e in page_obj["elements"] if e["type"] != "quran_line_ref"]
        # Reconstruct exact reading order using line indexes between headings/bismillah.
        # The canonical renderer can use `lines` and the ordered non-line elements.
        page_obj["elements_order"] = [e for e in page_obj["elements"]]

    return {
        "schema_version": 3,
        "layout_source": "docx",
        "font_family": forced_font,
        "font_policy": {
            "mode": "forced_single_mushaf_font",
            "forced_font_family": forced_font,
            "other_docx_font_families_ignored": True,
        },
        "document_page_geometry": page_geometry,
        "total_pages": max(pages) if pages else 0,
        "total_surahs": len(surahs),
        "pages": pages,
        "surahs": surahs,
    }


def write_outputs(result: dict, output_dir: Path) -> None:
    pages_dir = output_dir / "pages"
    surahs_dir = output_dir / "surahs"
    pages_dir.mkdir(parents=True, exist_ok=True)
    surahs_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema_version": result["schema_version"],
        "layout_source": result["layout_source"],
        "font_family": result["font_family"],
        "font_policy": result["font_policy"],
        "document_page_geometry": result["document_page_geometry"],
        "total_pages": result["total_pages"],
        "total_surahs": result["total_surahs"],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    for page_no in range(1, result["total_pages"] + 1):
        page_data = result["pages"].get(page_no, {"lines": [], "surah_ids": []})
        compact_lines = []
        for line in page_data["lines"]:
            compact_lines.append({
                "line": line["line"],
                "first_word": line["first_word"],
                "end_word": line["end_word"],
            })
        payload = {
            "schema_version": 3,
            "page": page_no,
            "surah_ids": page_data["surah_ids"],
            "font_family": result["font_family"],
            "layout_source": "docx",
            "lines": compact_lines,
        }
        (pages_dir / f"page_{page_no:03d}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    for sid in range(1, result["total_surahs"] + 1):
        surah = result["surahs"][sid]
        compact_pages = []
        for p in surah["pages"]:
            compact_pages.append({
                "page": p["page"],
                "lines": [
                    {
                        "line": line["line"],
                        "first_word": line["first_word"],
                        "end_word": line["end_word"],
                    }
                    for line in p["lines"]
                ],
            })
        payload = {
            "schema_version": 3,
            "surahId": sid,
            "surahName": surah.get("surahName"),
            "startPage": surah.get("startPage"),
            "endPage": surah.get("endPage"),
            "totalPagesInSurah": surah.get("totalPagesInSurah", 0),
            "totalCompletedLines": surah.get("totalCompletedLines", 0),
            "ayaCount": surah.get("ayaCount", 0),
            "font_family": result["font_family"],
            "layout_source": "docx",
            "pages": compact_pages,
        }
        (surahs_dir / f"{sid:03d}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def load_config(config_path: Path) -> dict:
    return json.loads(config_path.read_text(encoding="utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docx", type=Path, required=True)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()

    cfg = load_config(args.config)
    qiraah = cfg.get("qiraah", "qalun").lower()
    fonts = cfg.get("font_families", {})
    forced_font = fonts.get(qiraah)
    if not forced_font:
        raise RuntimeError(f"No configured font for qiraah={qiraah}")
    allowed = cfg.get("allowed_mushaf_fonts", [])
    if forced_font not in allowed:
        raise RuntimeError(f"Configured font is not in allowed_mushaf_fonts: {forced_font}")

    result = extract_docx(args.docx, forced_font)
    if result["total_pages"] != 604:
        raise RuntimeError(f"DOCX parser produced {result['total_pages']} pages; expected 604")
    if result["total_surahs"] != 114:
        raise RuntimeError(f"DOCX parser produced {result['total_surahs']} surahs; expected 114")
    for page_no, page_obj in result["pages"].items():
        if len(page_obj["lines"]) > 15:
            raise RuntimeError(f"Page {page_no}: DOCX produced {len(page_obj['lines'])} Quran lines; maximum is 15")
    write_outputs(result, args.output_dir)
    print(f"OK: generated DOCX-only layout for {result['total_surahs']} surahs and {result['total_pages']} pages")
    for pno in (1, 2, 440, 598, 604):
        pdata = result["pages"].get(pno)
        if pdata:
            print(f"  page {pno}: {len(pdata['lines'])} Quran lines; surahs={pdata['surah_ids']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
