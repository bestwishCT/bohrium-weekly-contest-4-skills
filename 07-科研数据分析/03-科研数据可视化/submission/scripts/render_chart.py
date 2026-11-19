#!/usr/bin/env python3
"""Source-audited grouped bars, scatter and line charts; SVG + real stdlib PNG."""
import argparse
import csv
import hashlib
import json
import math
import platform
import struct
import sys
import zlib
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

VERSION = "1.0.0"
UTC = lambda: datetime.now(timezone.utc).isoformat()
PALETTE = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9"]
W, H = 850, 550
LEFT, RIGHT, TOP, BOTTOM = 100, 190, 55, 90
FONT = {
    "A":["01110","10001","10001","11111","10001","10001","10001"], "B":["11110","10001","10001","11110","10001","10001","11110"],
    "C":["01111","10000","10000","10000","10000","10000","01111"], "D":["11110","10001","10001","10001","10001","10001","11110"],
    "E":["11111","10000","10000","11110","10000","10000","11111"], "F":["11111","10000","10000","11110","10000","10000","10000"],
    "G":["01111","10000","10000","10111","10001","10001","01111"], "H":["10001","10001","10001","11111","10001","10001","10001"],
    "I":["111","010","010","010","010","010","111"], "J":["00111","00010","00010","00010","10010","10010","01100"],
    "K":["10001","10010","10100","11000","10100","10010","10001"], "L":["10000","10000","10000","10000","10000","10000","11111"],
    "M":["10001","11011","10101","10101","10001","10001","10001"], "N":["10001","11001","10101","10011","10001","10001","10001"],
    "O":["01110","10001","10001","10001","10001","10001","01110"], "P":["11110","10001","10001","11110","10000","10000","10000"],
    "Q":["01110","10001","10001","10001","10101","10010","01101"], "R":["11110","10001","10001","11110","10100","10010","10001"],
    "S":["01111","10000","10000","01110","00001","00001","11110"], "T":["11111","00100","00100","00100","00100","00100","00100"],
    "U":["10001","10001","10001","10001","10001","10001","01110"], "V":["10001","10001","10001","10001","10001","01010","00100"],
    "W":["10001","10001","10001","10101","10101","10101","01010"], "X":["10001","10001","01010","00100","01010","10001","10001"],
    "Y":["10001","10001","01010","00100","00100","00100","00100"], "Z":["11111","00001","00010","00100","01000","10000","11111"],
    "0":["01110","10001","10011","10101","11001","10001","01110"], "1":["010","110","010","010","010","010","111"],
    "2":["01110","10001","00001","00010","00100","01000","11111"], "3":["11110","00001","00001","01110","00001","00001","11110"],
    "4":["00010","00110","01010","10010","11111","00010","00010"], "5":["11111","10000","10000","11110","00001","00001","11110"],
    "6":["01110","10000","10000","11110","10001","10001","01110"], "7":["11111","00001","00010","00100","01000","01000","01000"],
    "8":["01110","10001","10001","01110","10001","10001","01110"], "9":["01110","10001","10001","01111","00001","00001","01110"],
    ".":["0","0","0","0","0","0","1"], "-":["000","000","000","111","000","000","000"], "+":["000","010","010","111","010","010","000"],
    "/":["00001","00010","00100","01000","10000","00000","00000"], "_":["00000","00000","00000","00000","00000","00000","11111"],
    "(":["01","10","10","10","10","10","01"], ")":["10","01","01","01","01","01","10"], ":":["0","0","1","0","0","1","0"],
    ",":["0","0","0","0","0","1","1"], "%":["10001","00010","00100","01000","10001","00000","00000"], "=":["0000","0000","1111","0000","1111","0000","0000"],
    "#":["01010","11111","01010","01010","11111","01010","00000"], "<":["001","010","100","100","100","010","001"], ">":["100","010","001","001","001","010","100"],
    " ":["000"] * 7
}


class PlotError(Exception):
    pass


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def dump_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def fmt(value):
    if abs(value) >= 10000 or (value and abs(value) < 0.001):
        return "%.3e" % value
    return ("%.6f" % value).rstrip("0").rstrip(".") or "0"


def hexrgb(value):
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


class Canvas:
    def __init__(self):
        self.items = []

    def line(self, x1, y1, x2, y2, color="#333333", width=1, dash=None):
        self.items.append(("line", x1, y1, x2, y2, color, width, dash))

    def rect(self, x, y, w, h, fill="none", stroke="#333333", width=1, pattern=0):
        self.items.append(("rect", x, y, w, h, fill, stroke, width, pattern))

    def circle(self, cx, cy, r, fill="#0072B2", stroke="#333333", width=1):
        self.items.append(("circle", cx, cy, r, fill, stroke, width))

    def text(self, x, y, text, size=12, anchor="start", color="#222222"):
        self.items.append(("text", x, y, str(text), size, anchor, color))

    def svg(self):
        parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<svg xmlns="http://www.w3.org/2000/svg" width="85mm" height="55mm" viewBox="0 0 850 550">', '<rect width="850" height="550" fill="white"/>']
        for item in self.items:
            kind = item[0]
            if kind == "line":
                _, x1, y1, x2, y2, color, width, dash = item
                extra = ' stroke-dasharray="5,3"' if dash else ""
                parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="%s"%s/>' % (fmt(x1), fmt(y1), fmt(x2), fmt(y2), color, width, extra))
            elif kind == "rect":
                _, x, y, w, h, fill, stroke, width, pattern = item
                parts.append('<rect x="%s" y="%s" width="%s" height="%s" fill="%s" stroke="%s" stroke-width="%s"/>' % (fmt(x), fmt(y), fmt(max(0, w)), fmt(max(0, h)), fill, stroke, width))
                if fill != "none" and pattern:
                    for dx in range(4, int(w), 7):
                        parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#333333" stroke-opacity="0.35" stroke-width="0.6"/>' % (fmt(x + dx), fmt(y), fmt(max(x, x + dx - h)), fmt(y + h)))
            elif kind == "circle":
                _, cx, cy, r, fill, stroke, width = item
                parts.append('<circle cx="%s" cy="%s" r="%s" fill="%s" stroke="%s" stroke-width="%s"/>' % (fmt(cx), fmt(cy), r, fill, stroke, width))
            else:
                _, x, y, text, size, anchor, color = item
                parts.append('<text x="%s" y="%s" font-family="DejaVu Sans,Arial,sans-serif" font-size="%s" text-anchor="%s" fill="%s">%s</text>' % (fmt(x), fmt(y), size, anchor, color, escape(text)))
        parts.append("</svg>")
        return "\n".join(parts) + "\n"

    def png(self, width, height, dpi):
        pixels = bytearray([255]) * (width * height * 3)
        sx, sy = width / W, height / H

        def pixel(x, y, color):
            if 0 <= x < width and 0 <= y < height:
                pos = (y * width + x) * 3
                pixels[pos:pos + 3] = bytes(color)

        def line(x1, y1, x2, y2, color, thickness=1):
            x1, y1, x2, y2 = map(round, (x1 * sx, y1 * sy, x2 * sx, y2 * sy))
            dx, dy = abs(x2 - x1), -abs(y2 - y1)
            step_x, step_y = (1 if x1 < x2 else -1), (1 if y1 < y2 else -1)
            err, rgb = dx + dy, hexrgb(color)
            radius = max(0, round(thickness * min(sx, sy) / 2) - 1)
            while True:
                for ox in range(-radius, radius + 1):
                    for oy in range(-radius, radius + 1):
                        pixel(x1 + ox, y1 + oy, rgb)
                if x1 == x2 and y1 == y2:
                    break
                e2 = 2 * err
                if e2 >= dy:
                    err += dy
                    x1 += step_x
                if e2 <= dx:
                    err += dx
                    y1 += step_y

        def rect(x, y, w, h, fill, stroke, thickness, pattern):
            x0, y0, x1, y1 = round(x * sx), round(y * sy), round((x + w) * sx), round((y + h) * sy)
            if fill != "none":
                rgb = hexrgb(fill)
                for yy in range(max(0, y0), min(height, y1 + 1)):
                    start = (yy * width + max(0, x0)) * 3
                    count = max(0, min(width, x1 + 1) - max(0, x0))
                    pixels[start:start + count * 3] = bytes(rgb) * count
                if pattern:
                    for xx in range(x0 + 5, x1, max(5, round(7 * sx))):
                        line((xx / sx), y, max(x, (xx - (y1 - y0)) / sx), y + h, "#555555", 0.6)
            line(x, y, x + w, y, stroke, thickness)
            line(x + w, y, x + w, y + h, stroke, thickness)
            line(x + w, y + h, x, y + h, stroke, thickness)
            line(x, y + h, x, y, stroke, thickness)

        def text(x, y, value, size, anchor, color):
            scale = max(1, round(size * min(sx, sy) / 8))
            glyphs = [FONT.get(char.upper()) for char in value]
            if any(glyph is None for glyph in glyphs):
                raise PlotError("PNG fallback supports ASCII labels only; use a verified font-capable renderer for non-ASCII")
            lengths = [len(g[0]) + 1 for g in glyphs]
            total = max(0, sum(lengths) - 1) * scale
            xpix = round(x * sx - (total if anchor == "end" else total / 2 if anchor == "middle" else 0))
            ypix = round(y * sy - 7 * scale)
            rgb = hexrgb(color)
            for glyph, length in zip(glyphs, lengths):
                for gy, row in enumerate(glyph):
                    for gx, cell in enumerate(row):
                        if cell == "1":
                            for ox in range(scale):
                                for oy in range(scale):
                                    pixel(xpix + gx * scale + ox, ypix + gy * scale + oy, rgb)
                xpix += length * scale

        for item in self.items:
            if item[0] == "line":
                _, x1, y1, x2, y2, color, thickness, dash = item
                line(x1, y1, x2, y2, color, thickness)
            elif item[0] == "rect":
                rect(*item[1:])
            elif item[0] == "circle":
                _, cx, cy, radius, fill, stroke, thickness = item
                cxp, cyp, rp = round(cx * sx), round(cy * sy), max(1, round(radius * min(sx, sy)))
                frgb = None if fill == "none" else hexrgb(fill)
                srgb = hexrgb(stroke)
                for yy in range(-rp, rp + 1):
                    for xx in range(-rp, rp + 1):
                        dist = xx * xx + yy * yy
                        if dist <= rp * rp:
                            pixel(cxp + xx, cyp + yy, srgb if dist >= (rp - 1) ** 2 or fill == "none" else frgb)
            else:
                text(*item[1:])
        raw = b"".join(b"\x00" + bytes(pixels[y * width * 3:(y + 1) * width * 3]) for y in range(height))

        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)

        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"pHYs", struct.pack(">IIB", round(dpi / 0.0254), round(dpi / 0.0254), 1)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def axis_ticks(low, high, count=5):
    if high == low:
        high = low + 1
    return [low + (high - low) * i / count for i in range(count + 1)]


def aggregated_bar(rows, spec):
    group, value = spec["category"], spec["value"]
    series_col = spec.get("series")
    missing_tokens = set(spec.get("missing_tokens", ["", "NA"]))
    grouped, missing, invalid, duplicates = defaultdict(list), 0, 0, 0
    seen = set()
    for row in rows:
        signature = json.dumps(row, sort_keys=True)
        if signature in seen:
            duplicates += 1
        seen.add(signature)
        text = row.get(value, "").strip()
        if text in missing_tokens:
            missing += 1
            continue
        try:
            number = float(text)
            if not math.isfinite(number):
                raise ValueError("nonfinite")
        except (TypeError, ValueError):
            invalid += 1
            continue
        qc = spec.get("qc", {})
        if ("min" in qc and number < float(qc["min"])) or ("max" in qc and number > float(qc["max"])):
            if not qc.get("exclude_authorized"):
                raise PlotError("QC exclusion requires explicit exclude_authorized")
            invalid += 1
            continue
        category, series = row.get(group, "").strip(), row.get(series_col, "").strip() if series_col else "all"
        if not category or not series:
            missing += 1
            continue
        grouped[(category, series)].append(number)
    categories = spec.get("category_order") or sorted({x[0] for x in grouped})
    series_levels = spec.get("series_order") or sorted({x[1] for x in grouped})
    if not grouped:
        raise PlotError("no valid numeric values; refusing empty chart")
    if len(categories) > 12 or len(series_levels) > 6 or len(categories) * len(series_levels) > 72:
        raise PlotError("category/series density exceeds readable template; use another design or facets")
    if any(len(x) > 16 for x in categories + series_levels):
        raise PlotError("labels exceed 16 characters; use approved labels or another layout")
    aggregation = spec.get("aggregation", "mean")
    data = []
    for category in categories:
        for series in series_levels:
            values = grouped.get((category, series), [])
            if not values:
                continue
            n = len(values)
            if aggregation == "mean":
                center = math.fsum(values) / n
            elif aggregation == "median":
                ordered = sorted(values)
                center = ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2
            elif aggregation == "sum":
                center = math.fsum(values)
            elif aggregation == "count":
                center = n
            else:
                raise PlotError("unsupported aggregation")
            sd = math.sqrt(math.fsum((x - center) ** 2 for x in values) / (n - 1)) if n > 1 and aggregation == "mean" else None
            error_kind = spec.get("error")
            error = sd if error_kind == "sd" else sd / math.sqrt(n) if error_kind == "se" and sd is not None else None
            if error_kind not in {None, "sd", "se"}:
                raise PlotError("error must be sd or se")
            data.append({"category": category, "series": series, "n": n, "center": center, "sd": sd, "error": error, "error_kind": error_kind if error is not None else None, "source_values": values})
    return data, {"missing_pairs": missing, "invalid_or_authorized_qc_exclusions": invalid, "exact_duplicate_rows_flagged_not_removed": duplicates}, categories, series_levels


def point_data(rows, spec, chart):
    missing_tokens = set(spec.get("missing_tokens", ["", "NA"]))
    xcol, ycol, series_col = spec["x"], spec["y"], spec.get("series")
    trajectory_col = spec.get("trajectory")
    points, missing, seen = [], 0, set()
    duplicates = 0
    for source_row, row in enumerate(rows, 2):
        signature = json.dumps(row, sort_keys=True)
        if signature in seen:
            duplicates += 1
        seen.add(signature)
        try:
            if row[xcol].strip() in missing_tokens or row[ycol].strip() in missing_tokens:
                raise ValueError("missing")
            x, y = float(row[xcol]), float(row[ycol])
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError("nonfinite")
            series = row.get(series_col, "").strip() if series_col else "all"
            if not series:
                raise ValueError("missing series")
            trajectory = row.get(trajectory_col, "").strip() if trajectory_col else series
            if not trajectory:
                raise ValueError("missing trajectory")
            points.append({"source_row": source_row, "x": x, "y": y, "series": series, "trajectory": trajectory})
        except (ValueError, TypeError, KeyError):
            missing += 1
    if not points:
        raise PlotError("no complete finite xy pairs")
    if len(points) > 20000:
        raise PlotError("point count exceeds template limit; predefine readable aggregation")
    series_levels = spec.get("series_order") or sorted({p["series"] for p in points})
    if len(series_levels) > 6:
        raise PlotError("series count exceeds template limit")
    return points, {"incomplete_or_invalid_pairs": missing, "exact_duplicate_rows_flagged_not_removed": duplicates}, series_levels


def build_canvas(spec, data, levels, audit):
    canvas, chart = Canvas(), spec["chart"]
    canvas.text(W / 2, 24, spec["title"], 17, "middle")
    y_label, x_label = spec["y_label"] if chart != "grouped_bar" else spec["value_label"], spec["x_label"] if chart != "grouped_bar" else spec["category_label"]
    canvas.text(LEFT + (W - LEFT - RIGHT) / 2, H - 20, x_label, 13, "middle")
    canvas.text(18, TOP - 22, y_label, 13, "start")
    plot_rights = W - RIGHT
    plot_bottom = H - BOTTOM
    color_map = {level: PALETTE[i % len(PALETTE)] for i, level in enumerate(levels)}
    if chart == "grouped_bar":
        values = []
        for row in data:
            error = row["error"] or 0
            values.extend([0, row["center"] - error, row["center"] + error])
        low, high = min(values), max(values)
        if spec.get("baseline", 0) != 0:
            raise PlotError("bar chart requires zero baseline")
        padding = (high - low) * 0.08 or 1
        low, high = low - padding if low < 0 else 0, high + padding
        ticks = axis_ticks(low, high)
        def ymap(value): return TOP + (high - value) / (high - low) * (plot_bottom - TOP)
        cats = list(dict.fromkeys(row["category"] for row in data))
        band = (plot_rights - LEFT) / len(cats)
        bar_width = band * 0.7 / max(1, len(levels))
        for index, row in enumerate(data):
            center_x = LEFT + (cats.index(row["category"]) + 0.5) * band + (levels.index(row["series"]) - (len(levels) - 1) / 2) * bar_width
            x0 = center_x - bar_width * 0.42
            y0, y1 = ymap(row["center"]), ymap(0)
            canvas.rect(x0, min(y0, y1), bar_width * 0.84, abs(y1 - y0), fill=color_map[row["series"]], pattern=levels.index(row["series"]) % 2, width=1)
            if row["error"] is not None:
                canvas.line(center_x, ymap(row["center"] - row["error"]), center_x, ymap(row["center"] + row["error"]), "#222222", 1.2)
                canvas.line(center_x - 4, ymap(row["center"] + row["error"]), center_x + 4, ymap(row["center"] + row["error"]), "#222222", 1.2)
                canvas.line(center_x - 4, ymap(row["center"] - row["error"]), center_x + 4, ymap(row["center"] - row["error"]), "#222222", 1.2)
        for i, category in enumerate(cats):
            canvas.text(LEFT + (i + 0.5) * band, plot_bottom + 24, category, 11, "middle")
        element_count = len(data)
    else:
        xs, ys = [p["x"] for p in data], [p["y"] for p in data]
        xpad, ypad = (max(xs) - min(xs)) * 0.06 or 1, (max(ys) - min(ys)) * 0.06 or 1
        xlow, xhigh, low, high = min(xs) - xpad, max(xs) + xpad, min(ys) - ypad, max(ys) + ypad
        xticks = axis_ticks(xlow, xhigh)
        ticks = axis_ticks(low, high)
        def xmap(value): return LEFT + (value - xlow) / (xhigh - xlow) * (plot_rights - LEFT)
        def ymap(value): return TOP + (high - value) / (high - low) * (plot_bottom - TOP)
        for tick in xticks:
            x = xmap(tick)
            canvas.line(x, TOP, x, plot_bottom, "#DDDDDD", 0.6)
            canvas.text(x, plot_bottom + 24, fmt(tick), 10, "middle")
        if chart == "line":
            trajectory_values = [p.get("trajectory", p["series"]) for p in data]
            if len(set(zip(trajectory_values, [p["x"] for p in data]))) != len(data):
                raise PlotError("line chart requires unique x per trajectory; declare trajectory or use scatter/repeated-measure summaries")
            for level in levels:
                level_points = [p for p in data if p["series"] == level]
                trajectories = sorted({p.get("trajectory", level) for p in level_points})
                for trajectory in trajectories:
                    points = sorted([p for p in level_points if p.get("trajectory", level) == trajectory], key=lambda p: (p["x"], p["source_row"]))
                    for a, b in zip(points, points[1:]):
                        canvas.line(xmap(a["x"]), ymap(a["y"]), xmap(b["x"]), ymap(b["y"]), color_map[level], 1.5)
        for point in data:
            idx = levels.index(point["series"])
            canvas.circle(xmap(point["x"]), ymap(point["y"]), 3, fill="none" if idx % 2 else color_map[point["series"]], stroke=color_map[point["series"]], width=1.2)
        element_count = len(data)
    for tick in ticks:
        y = ymap(tick)
        canvas.line(LEFT, y, plot_rights, y, "#DDDDDD", 0.6)
        canvas.text(LEFT - 8, y + 3, fmt(tick), 10, "end")
    canvas.line(LEFT, TOP, LEFT, plot_bottom, "#222222", 1.2)
    canvas.line(LEFT, plot_bottom, plot_rights, plot_bottom, "#222222", 1.2)
    canvas.text(W - RIGHT + 18, TOP, spec.get("legend_title", "series"), 11, "start")
    for i, level in enumerate(levels):
        y = TOP + 24 + i * 25
        canvas.rect(W - RIGHT + 18, y - 10, 13, 13, fill=color_map[level], pattern=i % 2, width=1)
        canvas.text(W - RIGHT + 38, y, level, 11, "start")
    return canvas, element_count


def inspect_png(path, expected_width, expected_height, dpi):
    blob = path.read_bytes()
    if not blob.startswith(b"\x89PNG\r\n\x1a\n"):
        raise PlotError("PNG signature invalid")
    pos, chunks, idat = 8, [], bytearray()
    while pos < len(blob):
        length = struct.unpack(">I", blob[pos:pos + 4])[0]
        kind = blob[pos + 4:pos + 8]
        data = blob[pos + 8:pos + 8 + length]
        crc = struct.unpack(">I", blob[pos + 8 + length:pos + 12 + length])[0]
        if zlib.crc32(kind + data) & 0xffffffff != crc:
            raise PlotError("PNG CRC failed")
        chunks.append((kind, data))
        if kind == b"IDAT":
            idat.extend(data)
        pos += 12 + length
    width, height, depth, color = struct.unpack(">IIBB", chunks[0][1][:10])
    dpi_x, dpi_y, unit = struct.unpack(">IIB", next(data for kind, data in chunks if kind == b"pHYs"))
    raw = zlib.decompress(bytes(idat))
    rgb = bytearray()
    stride = width * 3
    for row in range(height):
        offset = row * (stride + 1)
        if raw[offset] != 0:
            raise PlotError("unexpected PNG row filter")
        rgb.extend(raw[offset + 1:offset + 1 + stride])
    nonwhite = sum(1 for i in range(0, len(rgb), 3) if rgb[i:i + 3] != b"\xff\xff\xff")
    unique = len({bytes(rgb[i:i + 3]) for i in range(0, len(rgb), 3)})
    checks = {"signature_crc_idat_readable": True, "width": width, "height": height, "expected_width": expected_width, "expected_height": expected_height, "bit_depth": depth, "color_type": color, "dpi_x": round(dpi_x * 0.0254), "dpi_y": round(dpi_y * 0.0254), "minimum_dpi": dpi, "nonwhite_pixels": nonwhite, "unique_rgb_colors": unique}
    checks["passed"] = width == expected_width and height == expected_height and depth == 8 and color == 2 and unit == 1 and min(dpi_x, dpi_y) >= round(dpi / 0.0254) and nonwhite > 100 and unique > 3
    if not checks["passed"]:
        raise PlotError("raster render inspection failed")
    return checks


def build_result(case_path, values_path, output):
    case = json.loads(case_path.read_text(encoding="utf-8"))
    values = json.loads(values_path.read_text(encoding="utf-8"))
    if not isinstance(case.get("output_contract"), dict) or not isinstance(values, dict) or set(case["output_contract"]) != set(values) or "case_id" not in case or "case_id" in values:
        raise PlotError("contract adapter requires exact object keys and case_id")
    result = {"case_id": case["case_id"]}
    result.update(values)
    dump_json(output / "result.json", result)


def main():
    parser = argparse.ArgumentParser(description="Render audited grouped bars, scatter or line charts with SVG and 300+dpi PNG.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--case", type=Path)
    parser.add_argument("--task", type=Path)
    parser.add_argument("--resources", type=Path)
    parser.add_argument("--contract-values", type=Path)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--width-mm", type=int, default=85)
    parser.add_argument("--height-mm", type=int, default=55)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    args.work.mkdir(parents=True, exist_ok=True)
    started = UTC()
    try:
        if args.dpi < 300 or args.width_mm <= 0 or args.height_mm <= 0:
            raise PlotError("dpi must be >=300 and dimensions positive")
        if not args.input.is_file() or not args.spec.is_file():
            raise PlotError("input or spec missing")
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
        if spec.get("schema_version") != "1.0":
            raise PlotError("unsupported spec schema_version")
        chart = spec.get("chart")
        if chart not in {"grouped_bar", "scatter", "line"}:
            raise PlotError("chart must be grouped_bar, scatter or line")
        required = {"title", "category", "value", "category_label", "value_label", "purpose"} if chart == "grouped_bar" else {"title", "x", "y", "x_label", "y_label", "purpose"}
        if not required <= set(spec):
            raise PlotError("missing spec fields: %s" % sorted(required - set(spec)))
        compatibility = {"grouped_bar": "comparison", "scatter": "relationship", "line": "trend"}
        if spec["purpose"] != compatibility[chart]:
            raise PlotError("chart/purpose mismatch; choose by scientific question")
        if args.width_mm != 85 or args.height_mm != 55:
            raise PlotError("stdlib fixed-layout template only supports 85x55mm; use verified publishing tool for other sizes")
        with args.input.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise PlotError("input header absent or duplicated")
            rows = list(reader)
        needed = set(spec.get(key, "") for key in ("category", "value", "series", "x", "y")) - {""}
        if not needed <= set(reader.fieldnames):
            raise PlotError("required data columns missing: %s" % sorted(needed - set(reader.fieldnames)))
        if not rows:
            raise PlotError("empty input")
        if chart == "grouped_bar":
            data, audit, categories, levels = aggregated_bar(rows, spec)
            plot_rows = [{"category": d["category"], "series": d["series"], "n": d["n"], "center": d["center"], "sd": d["sd"], "error": d["error"], "error_kind": d["error_kind"]} for d in data]
        else:
            data, audit, levels = point_data(rows, spec, chart)
            plot_rows = data
        canvas, elements = build_canvas(spec, data, levels, audit)
        png_width, png_height = round(args.width_mm / 25.4 * args.dpi), round(args.height_mm / 25.4 * args.dpi)
        (args.output / "figure.svg").write_text(canvas.svg(), encoding="utf-8")
        (args.output / "figure.png").write_bytes(canvas.png(png_width, png_height, args.dpi))
        fields = sorted({field for row in plot_rows for field in row})
        with (args.output / "plot_data.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
            writer.writeheader()
            for row in plot_rows:
                writer.writerow({key: json.dumps(row.get(key), ensure_ascii=False) if isinstance(row.get(key), (dict, list)) else row.get(key) for key in fields})
        mapping = {"purpose": spec["purpose"], "chart": chart, "encoding": {"grouped_bar": "x=category, bar length=aggregate value from zero, color/ordered hatch=series, vertical interval=SD/SE only when supplied", "scatter": "x/y=row-level numeric fields, color/fill=series", "line": "x sorted numeric x, y numeric y, color=series, markers=source observations"}[chart], "aggregation": spec.get("aggregation") if chart == "grouped_bar" else None, "legend": levels, "rejected_design_note": "No pie/dual-axis/area encoding or invented significance markers."}
        dump_json(args.output / "encoding_audit.json", {"mapping": mapping, "audit": audit, "element_count": elements, "source_rows": len(rows), "plot_rows": len(plot_rows), "units": spec.get("units", {})})
        svg_root = ET.parse(args.output / "figure.svg").getroot()
        png_checks = inspect_png(args.output / "figure.png", png_width, png_height, args.dpi)
        render = {"svg_well_formed": svg_root.tag.endswith("svg"), "svg_bytes": (args.output / "figure.svg").stat().st_size, "png_bytes": (args.output / "figure.png").stat().st_size, "png": png_checks, "source_visual_reconciliation": {"element_count_matches": elements == (len(data) if chart != "line" else len(data)), "series_match": set(levels) == {row["series"] for row in data}, "expected_category_series_nonempty": all(row["n"] > 0 for row in plot_rows) if chart == "grouped_bar" else True}, "visual_review_required": "Open final-size PNG and inspect semantic readability after structural checks"}
        render["passed"] = render["svg_well_formed"] and png_checks["passed"] and all(render["source_visual_reconciliation"].values())
        dump_json(args.output / "render_check.json", render)
        if not render["passed"]:
            raise PlotError("render inspection failed")
        caption_lines = ["# 图注（自动同源生成）", "", "%s：%s；分析目的为 %s。" % (spec["title"], chart, spec["purpose"])]
        if chart == "grouped_bar":
            caption_lines.append("柱高为 %s，基线为 0；各组有效 n：%s。" % (spec.get("aggregation", "mean"), "; ".join("%s/%s=%d" % (d["category"], d["series"], d["n"]) for d in data)))
            caption_lines.append("误差线为 %s；n=1 时 SD/SE 不可估，不用零误差线伪装。" % (spec.get("error") or "无"))
        else:
            caption_lines.append("共有 %d 个完整有限观测点，来源行号保存在 plot_data.csv。" % len(data))
        caption_lines += ["排除/诊断：%s。" % json.dumps(audit, ensure_ascii=False), "缺失未插补，完全重复仅标记；无显著性或因果标记。单位见轴标签与 encoding_audit.json。", ""]
        (args.output / "caption.md").write_text("\n".join(caption_lines), encoding="utf-8")
        sources = {}
        for label, path in (("input", args.input), ("spec", args.spec), ("case", args.case), ("task", args.task), ("resources", args.resources)):
            if path is not None:
                if not path.is_file():
                    raise PlotError("evidence file missing: %s" % path)
                sources[label] = {"name": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)}
        dump_json(args.output / "run_manifest.json", {"skill_version": VERSION, "status": "success_with_structural_checks_visual_review_pending", "started_at": started, "finished_at": UTC(), "exit_code": 0, "sources": sources, "environment": {"python": platform.python_version(), "stdlib_only": True}, "randomness": None, "uncertainties": ["automatic checks do not replace human final-size visual review", "independence/statistical validity not inferred by plotting"], "changes": ["excluded only missing/invalid or explicitly authorized QC rows; details in encoding_audit.json"]})
        if args.contract_values:
            if not args.case or not args.case.is_file():
                raise PlotError("--case required for result contract")
            build_result(args.case, args.contract_values, args.output)
        print("OK chart=%s elements=%d png=%dx%d dpi=%d" % (chart, elements, png_width, png_height, args.dpi))
        return 0
    except (PlotError, OSError, ValueError, KeyError, csv.Error, json.JSONDecodeError, ET.ParseError, zlib.error) as exc:
        dump_json(args.output / "run_failure.json", {"status": "failed", "started_at": started, "finished_at": UTC(), "exit_code": 2, "error_type": type(exc).__name__, "message": str(exc), "partial_outputs_retained": True})
        print("ERROR %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
