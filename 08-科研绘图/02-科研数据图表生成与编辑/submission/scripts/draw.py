#!/usr/bin/env python3
"""Source-record grouped bar chart to editable SVG; no aggregation or inference."""
import argparse
import hashlib
import json
import math
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

SAFE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def source_quote(model, item):
    sources = {s.get("id"): s for s in model["sources"]}
    if item.get("source") not in sources or not isinstance(item.get("quote"), str) or item["quote"] not in sources[item["source"]].get("text", ""):
        raise ValueError("Missing or non-verbatim evidence for " + str(item.get("id", "chart")))


def fmt(value):
    return ("%.6g" % value)


NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"


def exact_number(text, value):
    try:
        parsed = Decimal(text)
        return parsed.is_finite() and parsed == Decimal(str(value))
    except (InvalidOperation, ValueError):
        return False


def structured_record(model, record, categories, series):
    """Parse category/series/value/error/unit fields from the selected page's quote."""
    parsed = re.fullmatch(r"(?P<prefix>.+?) value=(?P<value>" + NUMBER + r") error=(?P<error>" + NUMBER + r") (?P<unit>.+)", record["quote"].strip())
    if not parsed:
        raise ValueError("Record does not match structured value/error/unit grammar: " + record["id"])
    mappings = [(category, parsed["prefix"][len(category) + 1:]) for category in categories if parsed["prefix"].startswith(category + " ")]
    expected_label = next(s["label"] for s in series if s["id"] == record["series"])
    if len(mappings) != 1 or mappings[0] != (record["category"], expected_label):
        raise ValueError("Record category/series mismatch: " + record["id"])
    if not exact_number(parsed["value"], record["value"]):
        raise ValueError("Record value mismatch: " + record["id"])
    if not exact_number(parsed["error"], record.get("error", 0)):
        raise ValueError("Record error mismatch: " + record["id"])
    if not record.get("unit") or parsed["unit"] != record["unit"]:
        raise ValueError("Record unit mismatch: " + record["id"])
    source = next(s for s in model["sources"] if s["id"] == record["source"])
    return {"source": source["id"], "locator": source["locator"], "page": source["page"], "fields": {"category": mappings[0][0], "series": mappings[0][1], "value": parsed["value"], "error": parsed["error"], "unit": parsed["unit"]}}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--audit", type=Path, required=True)
    a = ap.parse_args()
    try:
        m = json.loads(a.input.read_text(encoding="utf-8"))
        is_example = a.input.resolve() == (Path(__file__).resolve().parents[1] / "examples" / "minimal.json").resolve()
        if m.get("workorder") != "source_value_chart" or m.get("chart", {}).get("type") != "grouped-bar":
            raise ValueError("Expected source_value_chart/grouped-bar model")
        if not m.get("sources"):
            raise ValueError("Source ledger is required")
        ids = set()
        for s in m["sources"]:
            if not SAFE.fullmatch(str(s.get("id"))) or not s.get("locator") or not s.get("text") or not isinstance(s.get("page"), int) or isinstance(s["page"], bool) or s["page"] < 1:
                raise ValueError("Invalid source record or page")
            if s["id"] in ids:
                raise ValueError("Duplicate source id")
            ids.add(s["id"])
        c = m["chart"]
        source_quote(m, c)
        if not c.get("x_label") or not c.get("y_label") or c.get("error_label") not in {"SD", "SE", "CI", "none"}:
            raise ValueError("Axis labels and declared error semantics are required")
        if c["x_label"] not in c["quote"] or c["y_label"] not in c["quote"] or c["error_label"] not in c["quote"]:
            raise ValueError("Chart labels/error semantics must be in source quote")
        if not c.get("unit") or c["unit"] not in c["quote"] or "(" + c["unit"] + ")" not in c["y_label"]:
            raise ValueError("Chart unit must agree with axis label and source quote")
        categories, series, records = c.get("categories"), c.get("series"), c.get("records")
        if not isinstance(categories, list) or not (1 <= len(categories) <= 8) or len(set(categories)) != len(categories):
            raise ValueError("One to eight unique categories required")
        if not isinstance(series, list) or not (1 <= len(series) <= 4) or not isinstance(records, list) or not records:
            raise ValueError("One to four series and records required")
        for s in series:
            source_quote(m, s)
            if not SAFE.fullmatch(str(s.get("id"))) or not s.get("label") or s["label"] not in s["quote"] or not HEX.fullmatch(s.get("color", "")) or s["id"] in ids:
                raise ValueError("Invalid/duplicate series or unsafe color")
            ids.add(s["id"])
        ymax_value = 0.0
        pairs = set()
        structured_records = {}
        for r in records:
            source_quote(m, r)
            if not SAFE.fullmatch(str(r.get("id"))) or r["id"] in ids or r.get("category") not in categories or r.get("series") not in {s["id"] for s in series}:
                raise ValueError("Invalid/duplicate record mapping")
            ids.add(r["id"])
            pair = (r["category"], r["series"])
            if pair in pairs:
                raise ValueError("Duplicate category/series record")
            pairs.add(pair)
            if not isinstance(r.get("value"), (int, float)) or isinstance(r["value"], bool) or not math.isfinite(r["value"]) or r["value"] < 0:
                raise ValueError("Bar values must be finite and non-negative")
            err = r.get("error", 0)
            if not isinstance(err, (int, float)) or isinstance(err, bool) or not math.isfinite(err) or err < 0 or c["error_label"] == "none" and err:
                raise ValueError("Invalid error value")
            structured_records[r["id"]] = structured_record(m, r, categories, series)
            if r["unit"] != c["unit"]:
                raise ValueError("Record unit differs from chart unit: " + r["id"])
            ymax_value = max(ymax_value, r["value"] + err)
        if len(pairs) != len(categories) * len(series) or ymax_value <= 0:
            raise ValueError("Complete category/series matrix and positive range required")
        w, h = c.get("canvas", [1200, 760])
        if [w, h] != [1200, 760]:
            raise ValueError("This deterministic template requires canvas [1200,760]; reflow for other contracts")
        top, left, pw, ph = 155.0, 135.0, 770.0, 450.0
        ymax = ymax_value * 1.18
        baseline = top + ph
        band = pw / len(categories)
        barw = min(90.0, band * 0.65 / len(series))
        series_index = {s["id"]: i for i, s in enumerate(series)}
        cat_index = {v: i for i, v in enumerate(categories)}
        colors = {s["id"]: s["color"] for s in series}
        model_hash = hashlib.sha256(json.dumps(m, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        out = ['<?xml version="1.0" encoding="UTF-8"?>',
               '<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" width="1200" height="760" viewBox="0 0 1200 760" data-model-sha256="%s">' % model_hash,
               '<title>%s</title><metadata>source_value_chart; no aggregation; model_sha256=%s</metadata>' % (escape(c["title"]), model_hash),
               '<defs><marker id="axis-arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 z" fill="#334e5c"/></marker></defs>',
               '<g id="layer-background" inkscape:groupmode="layer" inkscape:label="background"><rect id="background" width="1200" height="760" fill="#ffffff"/></g>',
               '<g id="layer-grid" inkscape:groupmode="layer" inkscape:label="value grid">']
        for i in range(6):
            y = baseline - ph * i / 5
            value = ymax * i / 5
            out.append('<line id="grid-%d" x1="135" y1="%.2f" x2="905" y2="%.2f" stroke="#dce4e8" stroke-width="1"/><text id="tick-%d" x="120" y="%.2f" text-anchor="end" font-family="Helvetica,Arial,sans-serif" font-size="14" fill="#425563">%s</text>' % (i, y, y, i, y + 5, fmt(value)))
        out.extend(['</g><g id="layer-axes" inkscape:groupmode="layer" inkscape:label="axes and units">',
                    '<line id="axis-x" x1="135" y1="605" x2="918" y2="605" stroke="#334e5c" stroke-width="2" marker-end="url(#axis-arrow)"/><line id="axis-y" x1="135" y1="605" x2="135" y2="140" stroke="#334e5c" stroke-width="2" marker-end="url(#axis-arrow)"/>',
                    '<text id="axis-title-x" x="520" y="675" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="18" fill="#253b47">%s</text>' % escape(c["x_label"]),
                    '<text id="axis-title-y" x="42" y="380" text-anchor="middle" transform="rotate(-90 42 380)" font-family="Helvetica,Arial,sans-serif" font-size="18" fill="#253b47">%s</text>' % escape(c["y_label"]),
                    '</g><g id="layer-bars" inkscape:groupmode="layer" inkscape:label="source-record bars">'])
        audit_records = []
        for r in records:
            ci, si = cat_index[r["category"]], series_index[r["series"]]
            center = left + band * (ci + 0.5) + (si - (len(series) - 1) / 2) * (barw + 12)
            bh = ph * r["value"] / ymax
            x, y = center - barw / 2, baseline - bh
            out.append('<rect id="bar-%s" x="%.3f" y="%.3f" width="%.3f" height="%.3f" fill="%s" stroke="#1c313d" stroke-width="1" data-record-id="%s" data-source="%s" data-value="%s" data-unit="%s" data-series="%s" data-field="value"/>' % (r["id"], x, y, barw, bh, colors[r["series"]], r["id"], r["source"], fmt(r["value"]), escape(r["unit"]), r["series"]))
            err = r.get("error", 0)
            if err:
                eh = ph * err / ymax
                out.append('<g id="error-%s" data-semantics="%s" data-source="%s"><line id="error-line-%s" x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#1c313d" stroke-width="2"/><line id="error-cap-top-%s" x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#1c313d"/><line id="error-cap-bottom-%s" x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#1c313d"/></g>' % (r["id"], c["error_label"], r["source"], r["id"], center, y-eh, center, y+eh, r["id"], center-barw*.25, y-eh, center+barw*.25, y-eh, r["id"], center-barw*.25, y+eh, center+barw*.25, y+eh))
            out.append('<text id="value-%s" x="%.2f" y="%.2f" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="13" fill="#1c313d">%s</text>' % (r["id"], center, y-eh-8, fmt(r["value"])))
            audit_records.append({"record_id": r["id"], "source": r["source"], "quote": r["quote"], "category": r["category"], "series": r["series"], "value": r["value"], "error": err, "unit": r["unit"], "error_semantics": c["error_label"], "evidence": structured_records[r["id"]], "svg_id": "bar-" + r["id"], "geometry": {"x": round(x, 3), "y": round(y, 3), "width": round(barw, 3), "height": round(bh, 3)}, "scale": "height=value*450/%.8g; no aggregation" % ymax})
        out.append('</g><g id="layer-labels" inkscape:groupmode="layer" inkscape:label="category labels and heading">')
        for i, label in enumerate(categories):
            if len(label) > 22:
                raise ValueError("Category label too long for deterministic template")
            out.append('<text id="category-%d" x="%.2f" y="636" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="16" fill="#263f4d">%s</text>' % (i, left + band * (i + .5), escape(label)))
        out.extend([
            '<text id="figure-title" x="70" y="58" font-family="Helvetica,Arial,sans-serif" font-size="29" font-weight="bold" fill="#173141">%s</text>' % escape(c["title"])])
        if is_example:
            out.append('<text id="demo-label" x="70" y="88" font-family="Helvetica,Arial,sans-serif" font-size="14" fill="#765500">SYNTHETIC DEMO — NOT OFFICIAL</text>')
        out.extend([
            '<text id="method-label" x="70" y="116" font-family="Helvetica,Arial,sans-serif" font-size="14" fill="#526976">One record per bar; zero baseline; error bars = %s; no significance inferred</text>' % escape(c["error_label"]),
            '</g><g id="layer-legend" inkscape:groupmode="layer" inkscape:label="series and error legend">'])
        for i, s in enumerate(series):
            yy = 195 + i * 75
            out.append('<rect id="legend-swatch-%s" x="955" y="%d" width="30" height="30" fill="%s" stroke="#1c313d"/><text id="legend-label-%s" x="1000" y="%d" font-family="Helvetica,Arial,sans-serif" font-size="17" fill="#233d4c">%s</text>' % (s["id"], yy, s["color"], s["id"], yy + 22, escape(s["label"])))
        out.extend(['<text id="legend-title" x="955" y="160" font-family="Helvetica,Arial,sans-serif" font-size="18" font-weight="bold" fill="#233d4c">Series</text>',
                    '<text id="error-legend" x="955" y="390" font-family="Helvetica,Arial,sans-serif" font-size="15" fill="#526976">Error: %s</text>' % escape(c["error_label"]),
                    '<text id="unresolved" x="70" y="724" font-family="Helvetica,Arial,sans-serif" font-size="13" fill="#667781">Unresolved rows excluded: %d; see mapping audit.</text>' % len(m.get("unresolved", [])),
                    '</g></svg>'])
        svg = "\n".join(out) + "\n"
        root = ET.fromstring(svg)
        ns = {"s": "http://www.w3.org/2000/svg"}
        assert not root.findall(".//s:image", ns)
        for r, ar in zip(records, audit_records):
            node = root.find(".//*[@id='bar-%s']" % r["id"])
            assert node is not None and math.isclose(float(node.attrib["height"]), ar["geometry"]["height"], abs_tol=.002)
            assert node.attrib["data-source"] == r["source"] and node.attrib["data-value"] == fmt(r["value"])
        audit = {"status": "partial" if m.get("unresolved") else "passed", "workorder": "source_value_chart", "model_sha256": model_hash, "aggregation": "none", "y_scale": {"minimum": 0, "maximum": ymax, "axis_unit_label": c["y_label"]}, "record_to_glyph": audit_records, "legend_mapping": [{"series": s["id"], "label": s["label"], "color": s["color"], "source": s["source"]} for s in series], "unresolved": m.get("unresolved", []), "semantic_checks": {"one_record_per_bar": True, "quoted_values": True, "structured_record_fields_exact": True, "numeric_comparison_uses_parsed_decimal_fields": True, "record_series_and_units_checked": True, "demo_label_only_for_bundled_minimal_example": True, "error_semantics_explicit": True, "no_significance_added": True, "editable_vector_and_text": True}}
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.audit.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(svg, encoding="utf-8")
        a.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("PASS chart: %d source-record bars, %d series, mapping verified" % (len(records), len(series)))
        return 0
    except Exception as exc:
        print("FAIL draw: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
