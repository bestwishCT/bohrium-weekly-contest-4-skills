#!/usr/bin/env python3
"""Source-bound containment/sequence schematic to layered SVG (stdlib only)."""
import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

ID = re.compile(r"^[a-z][a-z0-9-]{0,39}$")


def evidence(model, item):
    source = item.get("source")
    quote = item.get("quote")
    records = {s.get("id"): s for s in model.get("sources", [])}
    if source not in records or not isinstance(quote, str) or not quote.strip():
        raise ValueError("Missing source/quote for " + str(item.get("id", item.get("label"))))
    if quote not in records[source].get("text", ""):
        raise ValueError("Quote is not verbatim in source " + source)


def check_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError("Unsafe or duplicate-prone id: " + str(value))


def structured_relation(model, relation, groups, entities):
    """Resolve a quote to one page-bound, ordered relation; never just keyword-match it."""
    source = next(s for s in model["sources"] if s["id"] == relation["source"])
    statements = [part.strip() for part in re.findall(r"[^.]+(?:\.|$)", source["text"]) if part.strip()]
    expected_type = {"contains": "contains", "sequence": "precedes", "parallel": "is parallel to"}[relation["type"]]
    matches = []
    for statement in statements:
        if relation["quote"] not in statement:
            continue
        parsed = re.fullmatch(r"(?P<from>.+?) (?P<predicate>contains|precedes|is parallel to) (?P<to>.+)", statement.rstrip("."))
        if not parsed or parsed["predicate"] != expected_type:
            continue
        from_label = groups[relation["from"]]["label"] if relation["from"] in groups else entities[relation["from"]]["label"]
        to_label = groups[relation["to"]]["label"] if relation["to"] in groups else entities[relation["to"]]["label"]
        parsed_to = [value.strip() for value in parsed["to"].split(" and ")] if relation["type"] == "contains" else [parsed["to"].strip()]
        if parsed["from"] == from_label and to_label in parsed_to:
            matches.append({"source": source["id"], "locator": source["locator"], "page": source["page"], "fields": {"from": parsed["from"], "predicate": parsed["predicate"], "to": parsed_to}})
    if len(matches) != 1:
        raise ValueError("Relation structured evidence mismatch or ambiguity: " + relation["id"])
    return matches[0]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--audit", type=Path, required=True)
    args = p.parse_args()
    try:
        m = json.loads(args.input.read_text(encoding="utf-8"))
        is_example = args.input.resolve() == (Path(__file__).resolve().parents[1] / "examples" / "minimal.json").resolve()
        if m.get("workorder") != "editable_relationship_schematic":
            raise ValueError("Wrong workorder")
        w, h = m.get("canvas", [1200, 760])
        if not isinstance(w, int) or not isinstance(h, int) or not (640 <= w <= 2400 and 480 <= h <= 1600):
            raise ValueError("Invalid canvas")
        sources = m.get("sources", [])
        if not sources:
            raise ValueError("No source ledger")
        seen = set()
        for s in sources:
            check_id(s.get("id"))
            if not s.get("locator") or not s.get("text"):
                raise ValueError("Source must have locator and verbatim text")
            if not isinstance(s.get("page"), int) or isinstance(s["page"], bool) or s["page"] < 1:
                raise ValueError("Source page must be a positive integer")
            if s["id"] in seen:
                raise ValueError("Duplicate source id")
            seen.add(s["id"])
        groups = m.get("groups", [])
        nodes = m.get("entities", [])
        edges = m.get("relations", [])
        if not groups or len(nodes) < 2 or not edges:
            raise ValueError("Need groups, >=2 entities and relations")
        for collection in (groups, nodes, edges):
            for item in collection:
                check_id(item.get("id"))
                evidence(m, item)
                if item["id"] in seen:
                    raise ValueError("Duplicate element id: " + item["id"])
                seen.add(item["id"])
                if "label" in item:
                    if not item["label"] or len(item["label"]) > 32 or item["label"] not in item["quote"]:
                        raise ValueError("Entity label must appear in its evidence quote")
        gm = {g["id"]: g for g in groups}
        nm = {n["id"]: n for n in nodes}
        all_ids = set(gm) | set(nm)
        for n in nodes:
            if n.get("group") not in gm or not isinstance(n.get("column"), int) or not isinstance(n.get("row"), int):
                raise ValueError("Entity group/column/row invalid: " + n["id"])
            if not (0 <= n["column"] < len(groups) and 0 <= n["row"] < 3):
                raise ValueError("Layout slot outside supported explicit grid")
        memberships = {(r["from"], r["to"]) for r in edges if r.get("type") == "contains"}
        for n in nodes:
            if (n["group"], n["id"]) not in memberships:
                raise ValueError("Entity lacks explicit containment relation: " + n["id"])
        structured_relations = {}
        for e in edges:
            if e.get("type") not in {"contains", "sequence", "parallel"} or e.get("from") not in all_ids or e.get("to") not in all_ids or e["from"] == e["to"]:
                raise ValueError("Unsupported relation or endpoint: " + e["id"])
            if e["type"] == "contains" and (e["from"] not in gm or e["to"] not in nm or nm[e["to"]]["group"] != e["from"]):
                raise ValueError("Containment/layout mismatch: " + e["id"])
            if e["type"] == "sequence" and (e["from"] not in nm or e["to"] not in nm):
                raise ValueError("Sequence endpoints must be entities")
            if e["type"] in {"sequence", "parallel"} and (e["from"] not in nm or e["to"] not in nm):
                raise ValueError("Sequence/parallel endpoints must be entities")
            structured_relations[e["id"]] = structured_relation(m, e, gm, nm)
        canonical = json.dumps(m, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        model_hash = hashlib.sha256(canonical).hexdigest()
        gwidth = (w - 160) / len(groups)
        positions = {}
        group_boxes = {}
        for i, g in enumerate(groups):
            gx = 80 + i * gwidth + 18
            group_boxes[g["id"]] = (gx, 150, gwidth - 36, h - 330)
        for n in nodes:
            gx, gy, gw, gh = group_boxes[n["group"]]
            nw = min(230, gw - 48)
            nh = 74
            nx = gx + (gw - nw) / 2
            ny = gy + 100 + n["row"] * 130
            if ny + nh > gy + gh - 20:
                raise ValueError("Too many rows for canvas")
            positions[n["id"]] = (nx, ny, nw, nh)
        out = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" width="%d" height="%d" viewBox="0 0 %d %d" data-model-sha256="%s">' % (w, h, w, h, model_hash),
            '<title>%s</title>' % escape(m.get("title", "Source-bound relationship schematic")),
            '<metadata>workorder=editable_relationship_schematic; model_sha256=%s; all labels are editable text</metadata>' % model_hash,
            '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L9,3 z" fill="#245b78"/></marker></defs>',
            '<g id="layer-background" inkscape:groupmode="layer" inkscape:label="background"><rect id="background" width="100%" height="100%" fill="#fbfcfe"/></g>',
            '<g id="layer-groups" inkscape:groupmode="layer" inkscape:label="containment boundaries">'
        ]
        for g in groups:
            x, y, gw, gh = group_boxes[g["id"]]
            out.append('<g id="group-%s" data-source="%s"><rect id="boundary-%s" x="%.2f" y="%.2f" width="%.2f" height="%.2f" rx="18" fill="#edf4f8" stroke="#6a8fa3" stroke-width="2"/><text id="group-label-%s" x="%.2f" y="184" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="20" font-weight="bold" fill="#183b4e">%s</text></g>' % (g["id"], g["source"], g["id"], x, y, gw, gh, g["id"], x + gw / 2, escape(g["label"])))
        out.append('</g><g id="layer-relations" inkscape:groupmode="layer" inkscape:label="sequence and parallel relations">')
        for e in edges:
            if e["type"] == "contains":
                continue
            x1, y1, w1, h1 = positions[e["from"]]
            x2, y2, w2, h2 = positions[e["to"]]
            if abs((x2 + w2 / 2) - (x1 + w1 / 2)) < 1:
                a = (x1 + w1 / 2, y1 + h1 if y2 >= y1 else y1)
                b = (x2 + w2 / 2, y2 - 10 if y2 >= y1 else y2 + h2 + 10)
            else:
                a = (x1 + w1, y1 + h1 / 2) if x2 > x1 else (x1, y1 + h1 / 2)
                b = (x2 - 10, y2 + h2 / 2) if x2 > x1 else (x2 + w2 + 10, y2 + h2 / 2)
            dash = ' stroke-dasharray="8 5"' if e["type"] == "parallel" else ""
            marker = ' marker-end="url(#arrow)"' if e["type"] == "sequence" else ""
            out.append('<line id="edge-%s" x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#245b78" stroke-width="3"%s data-source="%s" data-relation="%s"%s/>' % (e["id"], *a, *b, marker, e["source"], e["type"], dash))
        out.append('</g><g id="layer-entities" inkscape:groupmode="layer" inkscape:label="editable entity nodes">')
        for n in nodes:
            x, y, nw, nh = positions[n["id"]]
            out.append('<g id="node-%s" data-source="%s"><rect id="shape-%s" x="%.2f" y="%.2f" width="%.2f" height="%.2f" rx="12" fill="#ffffff" stroke="#277da1" stroke-width="2"/><text id="label-%s" x="%.2f" y="%.2f" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="17" fill="#102a38">%s</text></g>' % (n["id"], n["source"], n["id"], x, y, nw, nh, n["id"], x + nw / 2, y + nh / 2 + 6, escape(n["label"])))
        out.extend([
            '</g><g id="layer-annotation" inkscape:groupmode="layer" inkscape:label="title legend and notes">',
            '<text id="figure-title" x="80" y="62" font-family="Helvetica,Arial,sans-serif" font-size="30" font-weight="bold" fill="#102a38">%s</text>' % escape(m.get("title", "Relationship schematic"))])
        if is_example:
            out.append('<text id="demo-label" x="80" y="96" font-family="Helvetica,Arial,sans-serif" font-size="15" fill="#765500">SYNTHETIC DEMO — NOT OFFICIAL</text>')
        out.extend([
            '<line id="legend-line" x1="82" y1="%d" x2="142" y2="%d" stroke="#245b78" stroke-width="3" marker-end="url(#arrow)"/>' % (h - 100, h - 100),
            '<text id="legend-text" x="158" y="%d" font-family="Helvetica,Arial,sans-serif" font-size="16" fill="#334e5c">sequence; group boundary = contains; dashed = parallel</text>' % (h - 94),
            '<text id="unresolved" x="80" y="%d" font-family="Helvetica,Arial,sans-serif" font-size="14" fill="#667781">Unresolved items are excluded; see audit (%d).</text>' % (h - 50, len(m.get("unresolved", []))),
            '</g></svg>'
        ])
        svg = "\n".join(out) + "\n"
        root = ET.fromstring(svg)
        ns = {"s": "http://www.w3.org/2000/svg"}
        if root.findall(".//s:image", ns):
            raise AssertionError("Raster image found")
        for e in edges:
            if e["type"] == "contains":
                assert any(g["id"] == e["from"] and any(n["id"] == e["to"] and n["group"] == g["id"] for n in nodes) for g in groups)
            else:
                assert root.find(".//*[@id='edge-%s']" % e["id"]) is not None
        audit = {"status": "partial" if m.get("unresolved") else "passed", "workorder": m["workorder"], "model_sha256": model_hash, "canvas": [w, h], "source_count": len(sources), "entities": [{"id": n["id"], "source": n["source"], "quote": n["quote"], "group": n["group"]} for n in nodes], "relations": [{"id": e["id"], "type": e["type"], "from": e["from"], "to": e["to"], "source": e["source"], "quote": e["quote"], "evidence": structured_relations[e["id"]]} for e in edges], "unresolved": m.get("unresolved", []), "semantic_checks": {"labels_from_quotes": True, "containment_matches_membership": True, "sequence_direction_preserved": True, "structured_relation_fields_and_pages_checked": True, "demo_label_only_for_bundled_minimal_example": True, "no_raster_images": True, "editable_text_and_layers": True}}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.audit.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(svg, encoding="utf-8")
        args.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("PASS schematic: %d entities, %d sourced relations, layered SVG" % (len(nodes), len(edges)))
        return 0
    except Exception as exc:
        print("FAIL draw: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
