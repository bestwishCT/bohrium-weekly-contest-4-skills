#!/usr/bin/env python3
"""Validate a real chart image/tables and digitize explicitly calibrated points."""
import argparse
import csv
import json
import math
import struct
import sys
import zlib
from pathlib import Path

from common import Run, add_arguments, digest, write_json


def inspect_png(path):
    raw = Path(path).read_bytes()
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("only real PNG bytes are accepted by the deterministic image inspector")
    offset = 8
    chunks = []
    dimensions = None
    while offset + 12 <= len(raw):
        length = struct.unpack(">I", raw[offset:offset + 4])[0]
        kind = raw[offset + 4:offset + 8]
        payload = raw[offset + 8:offset + 8 + length]
        expected = struct.unpack(">I", raw[offset + 8 + length:offset + 12 + length])[0]
        actual = zlib.crc32(kind + payload) & 0xffffffff
        if expected != actual:
            raise ValueError("PNG chunk CRC mismatch: " + kind.decode("latin-1"))
        chunks.append(kind.decode("latin-1"))
        if kind == b"IHDR":
            dimensions = list(struct.unpack(">IIBBBBB", payload)[:2])
        offset += length + 12
        if kind == b"IEND":
            break
    if not dimensions or b"IEND" not in raw:
        raise ValueError("PNG lacks IHDR or IEND")
    return {"width": dimensions[0], "height": dimensions[1], "bytes": len(raw),
            "sha256": digest(path), "validated_chunks": chunks}


def transform(axis):
    anchors = axis.get("anchors", [])
    if len(anchors) < 2:
        raise ValueError("two explicit tick anchors are required per axis")
    first, second = anchors[0], anchors[1]
    p1, p2 = float(first["pixel"]), float(second["pixel"])
    if p1 == p2:
        raise ValueError("axis pixel anchors must differ")
    scale = axis.get("scale", "linear")
    if scale not in {"linear", "log10"}:
        raise ValueError("axis scale must be linear or log10")
    v1, v2 = float(first["value"]), float(second["value"])
    if scale == "log10":
        if v1 <= 0 or v2 <= 0:
            raise ValueError("log10 tick values must be positive")
        v1, v2 = math.log10(v1), math.log10(v2)
    slope = (v2 - v1) / (p2 - p1)
    intercept = v1 - slope * p1

    def apply(pixel):
        base = intercept + slope * float(pixel)
        return 10 ** base if scale == "log10" else base

    residuals = []
    for anchor in anchors:
        expected = float(anchor["value"])
        residuals.append(abs(apply(anchor["pixel"]) - expected))
    return apply, {"scale": scale, "unit": axis.get("unit", ""),
                   "anchors": anchors, "max_absolute_residual": max(residuals),
                   "transform": "value=10^(intercept+slope*pixel)" if scale == "log10" else "value=intercept+slope*pixel"}


def main():
    args = add_arguments(argparse.ArgumentParser(description=__doc__)).parse_args()
    try:
        run = Run(args)
    except Exception as error:
        print(json.dumps({"status": "input_error", "error": str(error)}), file=sys.stderr)
        return 2
    try:
        image_value = run.case.get("image_path") or run.case.get("image")
        table_values = run.case.get("table_paths", run.case.get("tables", []))
        if not image_value:
            raise ValueError("revision v2 requires the one actual image")
        if not isinstance(table_values, list) or not table_values:
            raise ValueError("revision v2 requires all supplied source tables")
        image_path = run.path(image_value)
        image = inspect_png(image_path)
        tables = []
        for value in table_values:
            table_value = value.get("path") if isinstance(value, dict) else value
            table_path = run.path(table_value)
            with open(table_path, "r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
                fields = list(rows[0].keys()) if rows else []
            tables.append({"path": str(table_path), "sha256": digest(table_path),
                           "fields": fields, "rows": rows, "row_count": len(rows)})
        calibration = run.case.get("calibration", {})
        x_transform, x_axis = transform(calibration["x"])
        y_transform, y_axis = transform(calibration["y"])
        x_axis["transform"] = "x_intercept + x_slope * pixel_x"
        y_axis["transform"] = "y_intercept + y_slope * pixel_y"
        points = []
        for item in calibration.get("points", []):
            px, py = item["pixel"]
            points.append({
                "point_id": item.get("point_id") or item.get("id"),
                "figure": item.get("figure", "Figure 1"),
                "subfigure": item.get("subfigure", ""),
                "series": item.get("series", ""),
                "pixel": [float(px), float(py)],
                "x": x_transform(px), "x_unit": x_axis["unit"],
                "y": y_transform(py), "y_unit": y_axis["unit"],
                "read_method": "marker center read from actual image, then affine/log10 tick calibration",
                "certainty": "inferred_from_calibrated_pixels",
                "precision_note": "numeric output is not a claim about instrument precision; inspect residual and image resolution",
                "evidence": {"image": str(image_path), "subfigure": item.get("subfigure", ""),
                             "pixel": [float(px), float(py)]},
            })
        if not points:
            raise ValueError("no visually identified points were supplied for the actual image")
        export = run.output / "digitized.csv"
        with open(export, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["point_id", "series", "subfigure", "x", "x_unit", "y", "y_unit", "pixel"])
            writer.writeheader()
            for point in points:
                writer.writerow({key: point[key] for key in writer.fieldnames})
        with open(export, "r", encoding="utf-8", newline="") as handle:
            reread = list(csv.DictReader(handle))
        if len(reread) != len(points):
            raise ValueError("CSV readback count mismatch")
        sources = [{"kind": "image", "path": str(image_path), "sha256": image["sha256"]}]
        sources.extend({"kind": "table", "path": table["path"], "sha256": table["sha256"]} for table in tables)
        run.changes.append("validated every declared PNG chunk and reread digitized CSV")
        run.finish({"status": "success", "image": image, "axes": {"x": x_axis, "y": y_axis},
                    "points": points, "tables": tables, "conflicts": run.case.get("conflicts", []),
                    "sources": sources, "exports": [{"path": str(export), "rows": len(reread)}]},
                   aliases={"digitizeddata": "points", "chartdata": "points", "result": "points"})
    except Exception as error:
        run.uncertainty.append(str(error))
        run.finish({"status": "partial", "image": {}, "axes": {}, "points": [], "tables": [],
                    "sources": [], "failure": str(error)}, code=2,
                   trace_extra={"failed_stage": "real image/table/calibration validation"})


if __name__ == "__main__":
    sys.exit(main())
