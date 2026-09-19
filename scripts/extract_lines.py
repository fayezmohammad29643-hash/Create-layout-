#!/usr/bin/env python3
"""
Extract physical Quran-line geometry from a Quranpedia mushaf SVG.

Important design rule:
  - The SVG may contain Quran lines plus non-Quran rows such as a surah title
    or basmala.  We detect physical rows from the glyph artwork, then use
    ayahPolygon only to label rows. Rows with no Quran verse label are removed
    before they are returned as layout lines.

The DOCX is the source of truth for which Quran text belongs on each line.
This script is the source of truth for that line's actual SVG geometry.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import xml.etree.ElementTree as ET
from statistics import median
from pathlib import Path

SVG_NS = "http://www.w3.org/2000/svg"
IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def parse_transform(attr):
    if not attr:
        return IDENTITY
    attr = attr.strip()
    m = re.match(r"matrix\(([^)]+)\)", attr)
    if m:
        vals = [float(v) for v in re.split(r"[,\s]+", m.group(1).strip())]
        if len(vals) != 6:
            raise ValueError(f"Bad matrix(): {attr}")
        return tuple(vals)
    m = re.match(r"translate\(([^)]+)\)", attr)
    if m:
        vals = [float(v) for v in re.split(r"[,\s]+", m.group(1).strip())]
        tx = vals[0]
        ty = vals[1] if len(vals) > 1 else 0.0
        return (1.0, 0.0, 0.0, 1.0, tx, ty)
    m = re.match(r"scale\(([^)]+)\)", attr)
    if m:
        vals = [float(v) for v in re.split(r"[,\s]+", m.group(1).strip())]
        sx = vals[0]
        sy = vals[1] if len(vals) > 1 else sx
        return (sx, 0.0, 0.0, sy, 0.0, 0.0)
    raise ValueError(f"Unsupported transform: {attr}")


def compose(parent, child):
    a1, b1, c1, d1, e1, f1 = parent
    a2, b2, c2, d2, e2, f2 = child
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def apply_transform(m, x, y):
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


_TOKEN_RE = re.compile(
    r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?"
)
_ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}


def parse_path_d(d):
    tokens = _TOKEN_RE.findall(d)
    i = 0
    n = len(tokens)
    subpaths = []
    current = []
    cx = cy = 0.0
    sx = sy = 0.0
    cmd = None

    def read_floats(count):
        nonlocal i
        vals = [float(tokens[i + k]) for k in range(count)]
        i += count
        return vals

    while i < n:
        tok = tokens[i]
        if tok.isalpha():
            cmd = tok
            i += 1
        if cmd is None:
            break
        letter = cmd.upper()
        relative = cmd.islower()
        if letter == "Z":
            if current:
                current.append((sx, sy))
                subpaths.append(current)
            current = []
            cx, cy = sx, sy
            continue
        arity = _ARITY[letter]
        if i + arity > n:
            break
        vals = read_floats(arity)
        if letter == "M":
            if relative:
                cx, cy = cx + vals[0], cy + vals[1]
            else:
                cx, cy = vals[0], vals[1]
            if current:
                subpaths.append(current)
            current = [(cx, cy)]
            sx, sy = cx, cy
            cmd = "l" if relative else "L"
        elif letter == "L":
            cx, cy = (cx + vals[0], cy + vals[1]) if relative else (vals[0], vals[1])
            current.append((cx, cy))
        elif letter == "H":
            cx = cx + vals[0] if relative else vals[0]
            current.append((cx, cy))
        elif letter == "V":
            cy = cy + vals[0] if relative else vals[0]
            current.append((cx, cy))
        elif letter == "C":
            p = vals
            if relative:
                p1, p2, end = (cx + p[0], cy + p[1]), (cx + p[2], cy + p[3]), (cx + p[4], cy + p[5])
            else:
                p1, p2, end = (p[0], p[1]), (p[2], p[3]), (p[4], p[5])
            current.extend([p1, p2, end])
            cx, cy = end
        elif letter == "S":
            p = vals
            p2, end = ((cx + p[0], cy + p[1]), (cx + p[2], cy + p[3])) if relative else ((p[0], p[1]), (p[2], p[3]))
            current.extend([p2, end])
            cx, cy = end
        elif letter == "Q":
            p = vals
            p1, end = ((cx + p[0], cy + p[1]), (cx + p[2], cy + p[3])) if relative else ((p[0], p[1]), (p[2], p[3]))
            current.extend([p1, end])
            cx, cy = end
        elif letter == "T":
            cx, cy = (cx + vals[0], cy + vals[1]) if relative else (vals[0], vals[1])
            current.append((cx, cy))
        elif letter == "A":
            ex, ey = vals[5], vals[6]
            cx, cy = (cx + ex, cy + ey) if relative else (ex, ey)
            current.append((cx, cy))

    if current:
        subpaths.append(current)
    return subpaths


def parse_ayah_polygons(root):
    segments = []
    for path in root.iter(f"{{{SVG_NS}}}path"):
        if path.get("class") != "ayahPolygon":
            continue
        surah = path.get("surah")
        ayah = path.get("ayah")
        vid = path.get("id")
        d = path.get("d", "")
        for chunk in d.split("Z"):
            chunk = chunk.strip()
            if not chunk:
                continue
            nums = [float(v) for v in re.findall(r"-?\d+\.?\d*", chunk)]
            if len(nums) < 8:
                continue
            xs = nums[0::2]
            ys = nums[1::2]
            segments.append({
                "surah": surah,
                "ayah": ayah,
                "verse_id": vid,
                "x_min": min(xs),
                "x_max": max(xs),
                "y_min": min(ys),
                "y_max": max(ys),
            })
    return segments


def reference_line_height(segments):
    heights = sorted(s["y_max"] - s["y_min"] for s in segments)
    if not heights:
        return None
    base = median(heights)
    normal = [h for h in heights if h <= 1.5 * base] or heights
    return median(normal)


def find_glyph_paths_with_transforms(root):
    candidates = []

    def collect(node, acc):
        t = compose(acc, parse_transform(node.get("transform")))
        if node.tag == f"{{{SVG_NS}}}path":
            cls = node.get("class", "")
            d = node.get("d", "")
            if cls != "ayahPolygon" and d:
                candidates.append((node, t))
        for child in node:
            collect(child, t)

    def walk(node, acc):
        t = compose(acc, parse_transform(node.get("transform")))
        if node.tag == f"{{{SVG_NS}}}g" and node.get("id") == "content":
            for child in node:
                collect(child, t)
            return True
        for child in node:
            if walk(child, t):
                return True
        return False

    walk(root, IDENTITY)
    if not candidates:
        return []
    lengths = [len(p.get("d", "")) for p, _ in candidates]
    max_len = max(lengths)
    min_len = max(100, int(max_len * 0.05))
    selected = [item for item, ln in zip(candidates, lengths) if ln >= min_len]
    return selected or [candidates[0]]


def cluster_into_lines(subpaths_bbox, threshold):
    if not subpaths_bbox:
        return []
    ordered = sorted(subpaths_bbox, key=lambda b: b["center_y"])
    clusters = [[ordered[0]]]
    running_mean = ordered[0]["center_y"]
    for b in ordered[1:]:
        if b["center_y"] - running_mean > threshold:
            clusters.append([b])
            running_mean = b["center_y"]
        else:
            clusters[-1].append(b)
            running_mean = sum(x["center_y"] for x in clusters[-1]) / len(clusters[-1])
    return clusters


def overlap_fraction(a_min, a_max, b_min, b_max):
    inter = max(0.0, min(a_max, b_max) - max(a_min, b_min))
    span = max(1e-9, a_max - a_min)
    return inter / span


def label_clusters(clusters, ayah_segments):
    result = []
    for cluster in clusters:
        y_top = min(b["y_min"] for b in cluster)
        y_bottom = max(b["y_max"] for b in cluster)
        x_left = min(b["x_min"] for b in cluster)
        x_right = max(b["x_max"] for b in cluster)
        verses = []
        for seg in ayah_segments:
            if overlap_fraction(y_top, y_bottom, seg["y_min"], seg["y_max"]) > 0.4:
                label = f'{seg["surah"]}:{seg["ayah"]}'
                if label not in verses:
                    verses.append(label)
        result.append({
            "y_top": round(y_top, 2),
            "y_bottom": round(y_bottom, 2),
            "x_start": round(x_right, 2),
            "x_end": round(x_left, 2),
            "verses_on_line": verses,
        })
    return result


def choose_quran_lines(subpaths_bbox, ayah_segments, expected_line_count):
    ref_height = reference_line_height(ayah_segments)
    if ref_height is None:
        raise RuntimeError("No ayahPolygon data found — cannot derive line spacing.")

    candidates = []
    # The normal pass and progressively finer passes.  The first candidate
    # whose Quran-only line count matches the DOCX is preferred.
    for fraction in (0.55, 0.50, 0.45, 0.40, 0.35, 0.30, 0.25, 0.20, 0.15):
        clusters = cluster_into_lines(subpaths_bbox, fraction * ref_height)
        labeled = label_clusters(clusters, ayah_segments)
        quran = [x for x in labeled if x["verses_on_line"]]
        candidates.append((len(quran), labeled, fraction))
        if expected_line_count is not None and len(quran) == expected_line_count:
            return quran, labeled, ref_height, fraction

    # If the line centers are very close, use the largest vertical gaps, but
    # still choose based on Quran-vs-non-Quran labeling rather than blindly
    # forcing an arbitrary number of raw rows.
    ordered = sorted(subpaths_bbox, key=lambda b: b["center_y"])
    for target_raw in range(max(1, expected_line_count or 1), min(len(ordered), (expected_line_count or 1) + 5) + 1):
        if len(ordered) < target_raw:
            continue
        if target_raw == 1:
            cut_indices = set()
        else:
            gaps = [
                (ordered[i + 1]["center_y"] - ordered[i]["center_y"], i)
                for i in range(len(ordered) - 1)
            ]
            cut_indices = {i for _, i in sorted(gaps, key=lambda x: (-x[0], x[1]))[: target_raw - 1]}
        clusters, cur = [], []
        for i, item in enumerate(ordered):
            cur.append(item)
            if i in cut_indices:
                clusters.append(cur)
                cur = []
        if cur:
            clusters.append(cur)
        labeled = label_clusters(clusters, ayah_segments)
        quran = [x for x in labeled if x["verses_on_line"]]
        if expected_line_count is None or len(quran) == expected_line_count:
            return quran, labeled, ref_height, None

    best = max(candidates, key=lambda x: (x[0], -abs((expected_line_count or x[0]) - x[0])))
    quran = [x for x in best[1] if x["verses_on_line"]]
    return quran, best[1], ref_height, best[2]


def extract(svg_path, expected_line_count=None):
    svg_path = Path(svg_path)
    tree = ET.parse(svg_path)
    root = tree.getroot()
    vb = [float(v) for v in root.get("viewBox", "0 0 0 0").split()]
    view_box = {"min_x": vb[0], "min_y": vb[1], "width": vb[2], "height": vb[3]}

    ayah_segments = parse_ayah_polygons(root)
    glyph_paths = find_glyph_paths_with_transforms(root)
    if not glyph_paths:
        raise RuntimeError("Could not locate Quranic glyph paths under <g id=\"content\">.")

    subpaths_bbox = []
    for path_el, transform in glyph_paths:
        for sp in parse_path_d(path_el.get("d", "")):
            if not sp:
                continue
            pts = [apply_transform(transform, x, y) for x, y in sp]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            x_min, x_max = min(xs), max(xs)
            y_min, y_max = min(ys), max(ys)
            subpaths_bbox.append({
                "x_min": x_min,
                "x_max": x_max,
                "y_min": y_min,
                "y_max": y_max,
                "center_y": (y_min + y_max) / 2.0,
            })

    quran_lines, all_labeled, ref_height, method_fraction = choose_quran_lines(
        subpaths_bbox, ayah_segments, expected_line_count
    )

    if expected_line_count is not None and len(quran_lines) != int(expected_line_count):
        raise RuntimeError(
            f"SVG has {len(quran_lines)} Quran lines after removing non-Quran rows, "
            f"but DOCX needs {expected_line_count}. Raw physical rows: {len(all_labeled)}; "
            f"glyph_paths: {len(glyph_paths)}; glyph_subpaths: {len(subpaths_bbox)}; "
            f"reference_line_height: {ref_height:.2f}."
        )

    lines = []
    for idx, item in enumerate(quran_lines, start=1):
        lines.append({"line_number": idx, **item})

    return {
        "source_file": str(svg_path),
        "view_box": view_box,
        "reference_line_height": round(ref_height, 2),
        "raw_physical_line_count": len(all_labeled),
        "non_quran_row_count": len(all_labeled) - len(quran_lines),
        "line_detection_method_fraction": method_fraction,
        "line_count": len(lines),
        "lines": lines,
        "glyph_path_count": len(glyph_paths),
        "glyph_subpath_count": len(subpaths_bbox),
    }


def write_csv(result, csv_path):
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line_number", "y_top", "y_bottom", "x_start", "x_end", "verses_on_line"])
        for ln in result["lines"]:
            w.writerow([
                ln["line_number"], ln["y_top"], ln["y_bottom"],
                ln["x_start"], ln["x_end"], " | ".join(ln["verses_on_line"]),
            ])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("svg")
    ap.add_argument("-o", "--output")
    ap.add_argument("--expected-lines", type=int)
    ap.add_argument("--csv")
    args = ap.parse_args()
    result = extract(args.svg, expected_line_count=args.expected_lines)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    if args.csv:
        write_csv(result, args.csv)


if __name__ == "__main__":
    main()
