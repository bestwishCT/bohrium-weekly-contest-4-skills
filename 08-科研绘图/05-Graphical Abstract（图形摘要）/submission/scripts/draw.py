#!/usr/bin/env python3
"""Four-part source-bound graphical abstract with linked result encoding; stdlib only."""
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
ROLES = ["question", "method", "result", "conclusion"]
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"


def exact_number(text, value):
    try:
        parsed = Decimal(text)
        return parsed.is_finite() and parsed == Decimal(str(value))
    except (InvalidOperation, ValueError):
        return False


def structured_result(source, record, objects):
    parsed = re.fullmatch(r"(?P<object>.+?) value=(?P<value>" + NUMBER + r") (?P<unit>.+)", record["quote"].strip())
    if not parsed:
        raise ValueError("Result does not match structured object/value/unit grammar: " + record["id"])
    if parsed["object"] != objects[record["object"]]["label"]:
        raise ValueError("Result object mismatch: " + record["id"])
    if not exact_number(parsed["value"], record["value"]):
        raise ValueError("Result value mismatch: " + record["id"])
    if not record.get("unit") or parsed["unit"] != record["unit"]:
        raise ValueError("Result unit mismatch: " + record["id"])
    return {"source": source["id"], "locator": source["locator"], "page": source["page"], "fields": parsed.groupdict()}


def structured_method(source, step, objects):
    parsed = re.fullmatch(r"(?P<label>.+?) uses (?P<object>.+)", step["quote"].strip().rstrip("."))
    if not parsed or (parsed["label"], parsed["object"]) != (step["label"], objects[step["object"]]["label"]):
        raise ValueError("Method label/object ordered evidence mismatch: " + step["id"])
    return {"source": source["id"], "locator": source["locator"], "page": source["page"], "fields": parsed.groupdict()}


def structured_comparison(source, quote, expected_higher, expected_lower, claim_id):
    parsed = re.fullmatch(r"(?P<higher>.+?) has greater measured signal than (?P<lower>.+)", quote.strip().rstrip("."))
    if not parsed or (parsed["higher"], parsed["lower"]) != (expected_higher, expected_lower):
        raise ValueError("Comparison ordered direction evidence mismatch: " + claim_id)
    return {"source": source["id"], "locator": source["locator"], "page": source["page"], "fields": parsed.groupdict()}


def wrap(text, width=29):
    words, lines, line = text.split(), [], ""
    for word in words:
        if len(word) > width:
            raise ValueError("Unbreakable word exceeds panel width")
        candidate = (line + " " + word).strip()
        if len(candidate) > width:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    if len(lines) > 4:
        raise ValueError("Panel text needs more than four lines")
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--audit", type=Path, required=True)
    a = ap.parse_args()
    try:
        m = json.loads(a.input.read_text(encoding="utf-8"))
        is_example = a.input.resolve() == (Path(__file__).resolve().parents[1] / "examples" / "minimal.json").resolve()
        if m.get("workorder") != "graphical_abstract" or not m.get("sources"):
            raise ValueError("Expected graphical_abstract and sources")
        sources = {}
        for s in m["sources"]:
            if not SAFE.fullmatch(str(s.get("id"))) or not s.get("locator") or not s.get("text") or not isinstance(s.get("page"), int) or isinstance(s["page"], bool) or s["page"] < 1 or s["id"] in sources:
                raise ValueError("Invalid or duplicate source/page")
            sources[s["id"]] = s

        def prove(item):
            if item.get("source") not in sources or not isinstance(item.get("quote"), str) or item["quote"] not in sources[item["source"]]["text"]:
                raise ValueError("Evidence missing/non-verbatim: " + str(item.get("id")))

        panels, objects, steps, records = (m.get(k) for k in ("panels", "objects", "method_steps", "result_records"))
        if not all(isinstance(v, list) for v in (panels, objects, steps, records)) or len(panels) != 4 or len(objects) != 2 or len(records) != 2:
            raise ValueError("Need four panels, two objects and two result records")
        used = set(sources)
        pm = {}
        for p in panels:
            prove(p)
            if p.get("role") not in ROLES or p["role"] in pm or not SAFE.fullmatch(str(p.get("id"))) or p["id"] in used:
                raise ValueError("Panel roles/ids must be unique")
            if not p.get("title") or len(p["title"]) > 30 or len(p.get("body", "")) > 130 or p["title"] not in p["quote"] or p["body"] not in p["quote"]:
                raise ValueError("Panel title/body not supported or too long")
            wrap(p["body"])
            used.add(p["id"])
            pm[p["role"]] = p
        if set(pm) != set(ROLES):
            raise ValueError("Question, method, result, conclusion are all mandatory")
        om = {}
        for o in objects:
            prove(o)
            if not SAFE.fullmatch(str(o.get("id"))) or o["id"] in used or not o.get("label") or len(o["label"]) > 16 or o["label"] not in o["quote"] or not HEX.fullmatch(o.get("color", "")):
                raise ValueError("Invalid research object/color")
            used.add(o["id"])
            om[o["id"]] = o
        structured_methods = {}
        for s in steps:
            prove(s)
            if not SAFE.fullmatch(str(s.get("id"))) or s["id"] in used or s.get("object") not in om or not s.get("label") or len(s["label"]) > 20 or s["label"] not in s["quote"] or om[s["object"]]["label"] not in s["quote"]:
                raise ValueError("Invalid method step")
            structured_methods[s["id"]] = structured_method(sources[s["source"]], s, om)
            used.add(s["id"])
        values = {}
        structured_results = {}
        for r in records:
            prove(r)
            if not SAFE.fullmatch(str(r.get("id"))) or r["id"] in used or r.get("object") not in om or r["object"] in values or not isinstance(r.get("value"), (int, float)) or isinstance(r["value"], bool) or not math.isfinite(r["value"]) or r["value"] < 0:
                raise ValueError("Invalid/duplicate result record")
            structured_results[r["id"]] = structured_result(sources[r["source"]], r, om)
            if len({r["unit"] for r in records}) != 1:
                raise ValueError("Mixed units need a different layout")
            used.add(r["id"])
            values[r["object"]] = r["value"]
        if set(values) != set(om) or max(values.values()) <= 0:
            raise ValueError("Both objects need results and positive scale")
        compare = m.get("comparison")
        prove(compare)
        if compare.get("higher") not in om or compare.get("lower") not in om or compare["higher"] == compare["lower"] or not values[compare["higher"]] > values[compare["lower"]]:
            raise ValueError("Conclusion comparison contradicts result records")
        higher, lower = om[compare["higher"]]["label"], om[compare["lower"]]["label"]
        comparison_evidence = structured_comparison(sources[compare["source"]], compare["quote"], higher, lower, "comparison")
        structured_comparison(sources[pm["conclusion"]["source"]], pm["conclusion"]["body"], higher, lower, "conclusion-panel")
        if "significant" in compare["quote"].lower() or "p=" in compare["quote"].lower():
            raise ValueError("This minimal chart does not support significance claims")
        model_hash = hashlib.sha256(json.dumps(m, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        out = ['<?xml version="1.0" encoding="UTF-8"?>',
               '<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" width="1200" height="760" viewBox="0 0 1200 760" data-model-sha256="%s">' % model_hash,
               '<title>%s</title><metadata>graphical_abstract; single page; deterministic reading order question-method-result-conclusion; model_sha256=%s</metadata>' % (escape(m.get("title", "Graphical abstract")), model_hash),
               '<defs><marker id="main-arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#547380"/></marker></defs>',
               '<g id="layer-background" inkscape:groupmode="layer" inkscape:label="background"><rect id="background" width="1200" height="760" fill="#fcfdfd"/></g>',
               '<g id="layer-reading-path" inkscape:groupmode="layer" inkscape:label="question method result conclusion path">']
        xs = {role: 50 + i * 290 for i, role in enumerate(ROLES)}
        for i in range(3):
            x1 = xs[ROLES[i]] + 260
            out.append('<line id="path-%d" x1="%d" y1="135" x2="%d" y2="135" stroke="#547380" stroke-width="3" marker-end="url(#main-arrow)"/>' % (i + 1, x1 + 2, x1 + 26))
        out.append('</g><g id="layer-panels" inkscape:groupmode="layer" inkscape:label="four sourced panels">')
        accents = {"question": "#4e7f95", "method": "#578f83", "result": "#b67a3c", "conclusion": "#74649c"}
        for role in ROLES:
            p, x = pm[role], xs[role]
            out.append('<g id="panel-%s" data-source="%s"><rect id="panel-boundary-%s" x="%d" y="155" width="260" height="510" rx="18" fill="#ffffff" stroke="#c4d2d8" stroke-width="2"/><rect id="panel-accent-%s" x="%d" y="155" width="260" height="8" rx="4" fill="%s"/><text id="panel-role-%s" x="%d" y="190" font-family="Helvetica,Arial,sans-serif" font-size="13" font-weight="bold" fill="%s">0%d  %s</text><text id="panel-title-%s" x="%d" y="225" font-family="Helvetica,Arial,sans-serif" font-size="20" font-weight="bold" fill="#1e3c48">%s</text></g>' % (role, p["source"], role, x, role, x, accents[role], role, x + 20, accents[role], ROLES.index(role) + 1, role.upper(), role, x + 20, escape(p["title"])))
            if role != "conclusion":
                for j, line in enumerate(wrap(p["body"])):
                    out.append('<text id="panel-body-%s-%d" x="%d" y="%d" font-family="Helvetica,Arial,sans-serif" font-size="15" fill="#405c68">%s</text>' % (role, j, x + 20, 610 + j * 21, escape(line)))
        out.append('</g><g id="layer-question" inkscape:groupmode="layer" inkscape:label="research objects">')
        qx = xs["question"]
        for i, o in enumerate(objects):
            cx = qx + 78 + i * 105
            out.append('<g id="object-%s" data-source="%s"><circle id="object-shape-%s" cx="%d" cy="355" r="42" fill="%s" stroke="#284957" stroke-width="2"/><text id="object-label-%s" x="%d" y="425" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="14" fill="#29444f">%s</text></g>' % (o["id"], o["source"], o["id"], cx, o["color"], o["id"], cx, escape(o["label"])))
        out.append('<text id="question-mark" x="%d" y="490" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="42" fill="#4e7f95">?</text></g>' % (qx + 130))
        out.append('<g id="layer-method" inkscape:groupmode="layer" inkscape:label="actual method steps">')
        mx = xs["method"]
        for i, s in enumerate(steps[:3]):
            y = 282 + i * 90
            color = om[s["object"]]["color"]
            out.append('<g id="method-%s" data-source="%s" data-object="%s"><rect id="method-shape-%s" x="%d" y="%d" width="220" height="62" rx="12" fill="%s" stroke="#416856"/><text id="method-label-%s" x="%d" y="%d" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="16" fill="#183d36">%s</text></g>' % (s["id"], s["source"], s["object"], s["id"], mx + 20, y, color, s["id"], mx + 130, y + 38, escape(s["label"])))
        if not steps or len(steps) > 3:
            raise ValueError("One to three method steps required")
        out.append('</g><g id="layer-result" inkscape:groupmode="layer" inkscape:label="linked source-record bars">')
        rx = xs["result"]
        vmax = max(values.values()) * 1.15
        for i, r in enumerate(records):
            x = rx + 40 + i * 110
            bh = 210 * r["value"] / vmax
            y = 490 - bh
            o = om[r["object"]]
            out.append('<rect id="result-bar-%s" x="%d" y="%.2f" width="70" height="%.2f" fill="%s" stroke="#654f33" data-record-id="%s" data-source="%s" data-value="%s" data-unit="%s"/><text id="result-value-%s" x="%d" y="%.2f" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="15" fill="#3c4e53">%s</text><text id="result-label-%s" x="%d" y="520" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="13" fill="#3c4e53">%s</text>' % (r["id"], x, y, bh, o["color"], r["id"], r["source"], "%.6g" % r["value"], r["unit"], r["id"], x + 35, y - 12, "%.6g" % r["value"], r["id"], x + 35, escape(o["label"])))
        out.append('<line id="result-baseline" x1="%d" y1="490" x2="%d" y2="490" stroke="#596b70" stroke-width="2"/><text id="result-unit" x="%d" y="553" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="13" fill="#67787e">Unit: %s; no significance inferred</text></g>' % (rx + 25, rx + 235, rx + 130, escape(records[0]["unit"])))
        out.append('<g id="layer-conclusion" inkscape:groupmode="layer" inkscape:label="source-supported conclusion">')
        cx = xs["conclusion"]
        out.append('<rect id="conclusion-callout" x="%d" y="285" width="220" height="190" rx="18" fill="#f1eef8" stroke="#74649c" stroke-width="2"/>' % (cx + 20))
        for j, line in enumerate(wrap(pm["conclusion"]["body"], 25)):
            out.append('<text id="conclusion-text-%d" x="%d" y="%d" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="17" fill="#40365f">%s</text>' % (j, cx + 130, 335 + j * 32, escape(line)))
        out.append('<text id="comparison-note" x="%d" y="520" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="13" fill="#675d7d">Source-linked direction</text></g>' % (cx + 130))
        out.extend(['<g id="layer-annotation" inkscape:groupmode="layer" inkscape:label="title and limitations">',
                    '<text id="figure-title" x="50" y="55" font-family="Helvetica,Arial,sans-serif" font-size="29" font-weight="bold" fill="#183844">%s</text>' % escape(m.get("title", "Graphical abstract"))])
        if is_example:
            out.append('<text id="demo-label" x="50" y="88" font-family="Helvetica,Arial,sans-serif" font-size="14" fill="#765500">SYNTHETIC DEMO — NOT OFFICIAL</text>')
        out.extend([
                    '<text id="mainline" x="50" y="120" font-family="Helvetica,Arial,sans-serif" font-size="15" fill="#506872">Question  →  Method  →  Core result  →  Conclusion</text>',
                    '<text id="unresolved" x="50" y="708" font-family="Helvetica,Arial,sans-serif" font-size="13" fill="#657981">Unresolved items excluded: %d. Object colors are consistent across question, method and result.</text>' % len(m.get("unresolved", [])),
                    '</g></svg>'])
        svg = "\n".join(out) + "\n"
        root = ET.fromstring(svg)
        assert not root.findall(".//{http://www.w3.org/2000/svg}image")
        for r in records:
            node = root.find(".//*[@id='result-bar-%s']" % r["id"])
            assert node is not None and node.attrib["data-value"] == "%.6g" % r["value"] and node.attrib["data-source"] == r["source"]
        audit = {"status": "partial" if m.get("unresolved") else "passed", "workorder": "graphical_abstract", "model_sha256": model_hash, "reading_order": ROLES, "panel_sources": [{"role": p["role"], "panel_id": p["id"], "source": p["source"], "quote": p["quote"]} for p in panels], "objects": [{"id": o["id"], "label": o["label"], "color": o["color"], "source": o["source"]} for o in objects], "method_steps": [dict(s, evidence=structured_methods[s["id"]]) for s in steps], "result_mapping": [{"record": r["id"], "object": r["object"], "value": r["value"], "unit": r["unit"], "evidence": structured_results[r["id"]], "svg_id": "result-bar-" + r["id"], "source": r["source"], "quote": r["quote"]} for r in records], "comparison": dict(compare, evidence=comparison_evidence), "unresolved": m.get("unresolved", []), "semantic_checks": {"four_parts_present": True, "single_page": True, "deterministic_reading_path": True, "comparison_matches_records": True, "ordered_comparison_evidence_checked": True, "result_fields_exact_and_decimal_compared": True, "result_units_checked": True, "source_pages_recorded": True, "demo_label_only_for_bundled_minimal_example": True, "object_color_consistent": True, "no_significance_added": True, "editable_text_and_layers": True}}
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.audit.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(svg, encoding="utf-8")
        a.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("PASS graphical abstract: four sourced panels, linked result and conclusion")
        return 0
    except Exception as exc:
        print("FAIL draw: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
