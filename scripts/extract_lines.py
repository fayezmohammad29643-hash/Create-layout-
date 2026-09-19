#!/usr/bin/env python3
"""
extract_lines.py
=================
Extracts the REAL per-line coordinates of a mushaf page rendered in the
Quranpedia SVG format (the kind of file that has <path class="ayahPolygon">
verse-highlight boxes plus a single giant pre-traced glyph <path>).

WHY THIS IS NEEDED
------------------
The ayahPolygon boxes cannot be used directly as "one rectangle = one line":
when a verse fills two (or more) full-width lines back-to-back with no
verse boundary in between, the generator merges them into ONE tall
rectangle to save space. So counting/using those boxes naively gives the
wrong line boundaries and the wrong line count.

This script instead reconstructs line geometry from the ACTUAL glyph
artwork:
  1. It replays the SVG transform stack (matrix + translate) that sits
     above the glyph <path>, so every point is converted into the same
     coordinate space used by the ayahPolygon boxes (the page/viewBox
     space).
  2. It parses the glyph path's "d" attribute (a full mini SVG path
     interpreter: M/L/H/V/C/S/Q/T/A/Z, relative and absolute).
  3. It groups the resulting sub-paths (=strokes/letter pieces) into rows
     using 1D clustering, with the clustering threshold derived
     automatically from the *un-merged* ayahPolygon boxes (so it adapts to
     each page's own line spacing instead of a hard-coded constant).
  4. For every detected line it reports the true ink bounding box
     (y_top, y_bottom, x_start = right edge/RTL start, x_end = left
     edge/RTL end) and cross-references it against the ayahPolygon layer
     to list which verse(s) sit on that line.

USAGE
-----
    python3 extract_lines.py input.svg [-o output.json] [--csv output.csv]

OUTPUT
------
A JSON document:
{
  "source_file": "...",
  "view_box": {"min_x":..,"min_y":..,"width":..,"height":..},
  "reference_line_height": 35.1,
  "line_count": 15,
  "lines": [
      {
        "line_number": 1,
        "y_top": 1.5, "y_bottom": 43.9,
        "x_start": 338.7, "x_end": -5.8,
        "verses_on_line": ["2:16"]
      },
      ...
  ]
}
"""

import argparse
import csv
import json
import re
import sys
import xml.etree.ElementTree as ET
from statistics import median

SVG_NS = "http://www.w3.org/2000/svg"


# --------------------------------------------------------------------------- #
# 1. Affine transform helpers  (a,b,c,d,e,f)  ->  x' = a*x + c*y + e
#                                                  y' = b*x + d*y + f
# --------------------------------------------------------------------------- #
IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def parse_transform(attr):
    """Parse a single SVG transform attribute (matrix(...) or translate(...))."""
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
    """Return parent ∘ child, i.e. apply child first, then parent."""
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


# --------------------------------------------------------------------------- #
# 2. Minimal SVG path ("d" attribute) interpreter
#    Returns a list of subpaths; each subpath is a list of (x, y) points
#    (anchors AND control points, all in the path's own local coordinates).
# --------------------------------------------------------------------------- #
_TOKEN_RE = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?")

_ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}


def parse_path_d(d):
    tokens = _TOKEN_RE.findall(d)
    i = 0
    n = len(tokens)
    subpaths = []
    current = []
    cx = cy = 0.0          # current point
    sx = sy = 0.0          # current subpath's start point
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
        # else: reuse previous cmd (implicit repeat), 'i' stays put

        letter = cmd.upper()
        relative = cmd.islower()

        if letter == "Z":
            if current:
                current.append((sx, sy))
                subpaths.append(current)
            current = []
            cx, cy = sx, sy
            # after Z, an implicit repeat would also mean Z; just continue loop
            continue

        arity = _ARITY[letter]
        if i + arity > n:
            break  # malformed / truncated tail — stop safely
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
            cmd = "l" if relative else "L"   # subsequent pairs are implicit lineto

        elif letter == "L":
            if relative:
                cx, cy = cx + vals[0], cy + vals[1]
            else:
                cx, cy = vals[0], vals[1]
            current.append((cx, cy))

        elif letter == "H":
            cx = cx + vals[0] if relative else vals[0]
            current.append((cx, cy))

        elif letter == "V":
            cy = cy + vals[0] if relative else vals[0]
            current.append((cx, cy))

        elif letter == "C":
            pts = vals
            if relative:
                p1 = (cx + pts[0], cy + pts[1])
                p2 = (cx + pts[2], cy + pts[3])
                end = (cx + pts[4], cy + pts[5])
            else:
                p1, p2, end = (pts[0], pts[1]), (pts[2], pts[3]), (pts[4], pts[5])
            current.extend([p1, p2, end])
            cx, cy = end

        elif letter == "S":
            pts = vals
            if relative:
                p2 = (cx + pts[0], cy + pts[1])
                end = (cx + pts[2], cy + pts[3])
            else:
                p2, end = (pts[0], pts[1]), (pts[2], pts[3])
            current.extend([p2, end])
            cx, cy = end

        elif letter == "Q":
            pts = vals
            if relative:
                p1 = (cx + pts[0], cy + pts[1])
                end = (cx + pts[2], cy + pts[3])
            else:
                p1, end = (pts[0], pts[1]), (pts[2], pts[3])
            current.extend([p1, end])
            cx, cy = end

        elif letter == "T":
            pts = vals
            end = (cx + pts[0], cy + pts[1]) if relative else (pts[0], pts[1])
            current.append(end)
            cx, cy = end

        elif letter == "A":
            # Arc: only the endpoint matters for bounding-box/clustering purposes.
            ex, ey = vals[5], vals[6]
            end = (cx + ex, cy + ey) if relative else (ex, ey)
            current.append(end)
            cx, cy = end

    if current:
        subpaths.append(current)
    return subpaths


# --------------------------------------------------------------------------- #
# 3. ayahPolygon parsing (used only to (a) derive a reference line height and
#    (b) label each detected line with the verse(s) that occupy it)
# --------------------------------------------------------------------------- #
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
                "surah": surah, "ayah": ayah, "verse_id": vid,
                "x_min": min(xs), "x_max": max(xs),
                "y_min": min(ys), "y_max": max(ys),
            })
    return segments


def reference_line_height(segments):
    heights = sorted(s["y_max"] - s["y_min"] for s in segments)
    if not heights:
        return None
    base = median(heights)
    # Drop merged multi-line boxes (roughly >= 1.5x the typical height),
    # then re-take the median for a clean reference value.
    normal = [h for h in heights if h <= 1.5 * base] or heights
    return median(normal)


# --------------------------------------------------------------------------- #
# 4. Locate + transform the glyph outline path
# --------------------------------------------------------------------------- #
def find_glyph_path_with_transform(root):
    """
    Walk the tree to find <g id="content"> and return (path_element,
    cumulative_transform) where cumulative_transform maps the path's local
    coordinates into the page/viewBox coordinate system.
    """
    def walk(node, acc):
        t = compose(acc, parse_transform(node.get("transform")))
        if node.tag == f"{{{SVG_NS}}}g" and node.get("id") == "content":
            paths = list(node.iter(f"{{{SVG_NS}}}path"))
            gs = [c for c in node if c.tag == f"{{{SVG_NS}}}g"]
            if paths:
                # transform for path = t plus any transform on the inner <g>
                inner_t = t
                if gs:
                    inner_t = compose(t, parse_transform(gs[0].get("transform")))
                return paths[0], inner_t
        for child in node:
            result = walk(child, t)
            if result:
                return result
        return None

    return walk(root, IDENTITY)


# --------------------------------------------------------------------------- #
# 5. Clustering subpaths into physical lines
# --------------------------------------------------------------------------- #
def cluster_into_lines(subpaths_bbox, threshold):
    """subpaths_bbox: list of dicts with x_min,x_max,y_min,y_max,center_y."""
    if not subpaths_bbox:
        return []
    ordered = sorted(subpaths_bbox, key=lambda b: b["center_y"])
    clusters = []
    current = [ordered[0]]
    running_mean = ordered[0]["center_y"]
    for b in ordered[1:]:
        if b["center_y"] - running_mean > threshold:
            clusters.append(current)
            current = [b]
            running_mean = b["center_y"]
        else:
            current.append(b)
            running_mean = sum(x["center_y"] for x in current) / len(current)
    clusters.append(current)
    return clusters


def cluster_into_expected_lines(subpaths_bbox, expected_count):
    """
    Deterministically partition glyph subpaths into exactly ``expected_count``
    physical lines using the largest vertical gaps between consecutive glyph
    centers.  This is a recovery path for pages where the normal threshold
    clustering merges two adjacent printed lines.
    """
    if not subpaths_bbox or expected_count <= 0:
        return []
    if len(subpaths_bbox) < expected_count:
        return []
    ordered = sorted(subpaths_bbox, key=lambda b: b["center_y"])
    if expected_count == 1:
        return [ordered]

    gaps = []
    for i in range(len(ordered) - 1):
        gap = ordered[i + 1]["center_y"] - ordered[i]["center_y"]
        gaps.append((gap, i))

    # The largest vertical gaps are the most likely boundaries between
    # physical lines.  Keep the original order deterministic for ties.
    cut_indices = sorted(i for _, i in sorted(gaps, key=lambda x: (-x[0], x[1]))[:expected_count - 1])
    cut_set = set(cut_indices)

    clusters = []
    current = []
    for i, item in enumerate(ordered):
        current.append(item)
        if i in cut_set:
            clusters.append(current)
            current = []
    if current:
        clusters.append(current)

    return clusters if len(clusters) == expected_count else []


def overlap_fraction(a_min, a_max, b_min, b_max):
    inter = max(0.0, min(a_max, b_max) - max(a_min, b_min))
    span = max(1e-9, a_max - a_min)
    return inter / span


# --------------------------------------------------------------------------- #
# 6. Main pipeline
# --------------------------------------------------------------------------- #
def extract(svg_path, expected_line_count=None):
    ET.register_namespace("", SVG_NS)
    tree = ET.parse(svg_path)
    root = tree.getroot()

    vb = [float(v) for v in root.get("viewBox", "0 0 0 0").split()]
    view_box = {"min_x": vb[0], "min_y": vb[1], "width": vb[2], "height": vb[3]}

    ayah_segments = parse_ayah_polygons(root)
    ref_height = reference_line_height(ayah_segments)
    if ref_height is None:
        raise RuntimeError("No ayahPolygon data found — cannot derive a reference line height.")

    path_el, transform = find_glyph_path_with_transform(root)
    if path_el is None:
        raise RuntimeError('Could not locate the glyph path under <g id="content">.')

    subpaths_local = parse_path_d(path_el.get("d", ""))

    subpaths_bbox = []
    for sp in subpaths_local:
        pts = [apply_transform(transform, x, y) for x, y in sp]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        subpaths_bbox.append({
            "x_min": x_min, "x_max": x_max,
            "y_min": y_min, "y_max": y_max,
            "center_y": (y_min + y_max) / 2.0,
        })

    threshold = 0.55 * ref_height
    clusters = cluster_into_lines(subpaths_bbox, threshold)

    # The DOCX is the source of truth for physical line breaks.  Usually the
    # SVG glyph clustering already finds the same count.  When two adjacent
    # lines are close enough that the default threshold merges them, try a
    # progressively finer threshold before falling back to an exact-count
    # partition based on the strongest vertical gaps.
    if expected_line_count is not None and expected_line_count > 0:
        expected_line_count = int(expected_line_count)
        if len(clusters) != expected_line_count:
            for fraction in (0.50, 0.45, 0.40, 0.35, 0.30, 0.25, 0.20):
                candidate = cluster_into_lines(subpaths_bbox, fraction * ref_height)
                if len(candidate) == expected_line_count:
                    clusters = candidate
                    break
        if len(clusters) != expected_line_count:
            candidate = cluster_into_expected_lines(subpaths_bbox, expected_line_count)
            if candidate:
                clusters = candidate

    clusters.sort(key=lambda c: min(b["y_min"] for b in c))

    lines = []
    for idx, cluster in enumerate(clusters, start=1):
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

        lines.append({
            "line_number": idx,
            "y_top": round(y_top, 2),
            "y_bottom": round(y_bottom, 2),
            "x_start": round(x_right, 2),   # right edge = RTL start
            "x_end": round(x_left, 2),      # left edge  = RTL end
            "verses_on_line": verses,
        })

    return {
        "source_file": str(svg_path),
        "view_box": view_box,
        "reference_line_height": round(ref_height, 2),
        "line_count": len(lines),
        "lines": lines,
    }


def write_csv(result, csv_path):
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line_number", "y_top", "y_bottom", "x_start", "x_end", "verses_on_line"])
        for ln in result["lines"]:
            w.writerow([ln["line_number"], ln["y_top"], ln["y_bottom"],
                        ln["x_start"], ln["x_end"], " | ".join(ln["verses_on_line"])])


def main():
    ap = argparse.ArgumentParser(description="Extract accurate per-line coordinates from a mushaf SVG page.")
    ap.add_argument("svg", help="Path to the input .svg file")
    ap.add_argument("-o", "--output", help="Output JSON path (default: stdout)")
    ap.add_argument("--csv", help="Also write a CSV summary to this path")
    args = ap.parse_args()

    result = extract(args.svg)
    text = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text)

    if args.csv:
        write_csv(result, args.csv)


if __name__ == "__main__":
    main()
