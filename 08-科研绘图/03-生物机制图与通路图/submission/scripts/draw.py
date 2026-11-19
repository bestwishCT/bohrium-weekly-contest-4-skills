#!/usr/bin/env python3
"""Evidence-bound biological mechanism/pathway SVG with compartment and edge checks."""
import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

SAFE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
REL_WORD = {"activation": "activates", "inhibition": "inhibits", "binding": "binds", "phosphorylation": "phosphorylates", "transport": "transports"}


def structured_interaction(source, interaction, entities):
    """Parse subject, predicate and object in order from the quote's source-page statement."""
    matches = []
    for statement in [part.strip() for part in re.findall(r"[^.]+(?:\.|$)", source["text"]) if part.strip()]:
        if interaction["quote"] not in statement:
            continue
        body = re.sub(r" as a feedback branch$", "", statement.rstrip("."))
        parsed = re.fullmatch(r"(?P<from>.+?) (?P<predicate>activates|inhibits|binds|phosphorylates|transports) (?P<to>.+)", body)
        if not parsed:
            continue
        if (parsed["from"], parsed["predicate"], parsed["to"]) == (entities[interaction["from"]]["label"], REL_WORD[interaction["type"]], entities[interaction["to"]]["label"]):
            matches.append({"source": source["id"], "locator": source["locator"], "page": source["page"], "fields": parsed.groupdict()})
    if len(matches) != 1:
        raise ValueError("Interaction ordered evidence mismatch or ambiguity: " + interaction["id"])
    return matches[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--audit", type=Path, required=True)
    a = ap.parse_args()
    try:
        m = json.loads(a.input.read_text(encoding="utf-8"))
        is_example = a.input.resolve() == (Path(__file__).resolve().parents[1] / "examples" / "minimal.json").resolve()
        if m.get("workorder") != "biological_mechanism_pathway" or not m.get("sources"):
            raise ValueError("Expected biological_mechanism_pathway and sources")
        sources = {}
        for s in m["sources"]:
            if not SAFE.fullmatch(str(s.get("id"))) or not s.get("locator") or not s.get("text") or not isinstance(s.get("page"), int) or isinstance(s["page"], bool) or s["page"] < 1 or s["id"] in sources:
                raise ValueError("Invalid or duplicate source/page")
            sources[s["id"]] = s

        def prove(item):
            if item.get("source") not in sources or not isinstance(item.get("quote"), str) or item["quote"] not in sources[item["source"]]["text"]:
                raise ValueError("Evidence missing/non-verbatim: " + str(item.get("id")))

        comps, nodes, edges = m.get("compartments"), m.get("entities"), m.get("interactions")
        if not comps or not nodes or not edges:
            raise ValueError("Compartments, entities and interactions are required")
        identifiers = set(sources)
        cm = {}
        for c in comps:
            prove(c)
            if not SAFE.fullmatch(str(c.get("id"))) or c["id"] in identifiers or c.get("role") not in {"extracellular", "membrane", "cytosol", "nucleus", "organelle"}:
                raise ValueError("Invalid compartment")
            if not c.get("label") or c["label"] not in c["quote"]:
                raise ValueError("Compartment label absent from source")
            coords = c.get("box")
            if not isinstance(coords, list) or len(coords) != 4 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in coords):
                raise ValueError("Compartment box invalid")
            x, y, w, h = coords
            if x < 20 or y < 120 or w < 100 or h < 55 or x + w > 1180 or y + h > 720:
                raise ValueError("Compartment outside safe canvas")
            identifiers.add(c["id"])
            cm[c["id"]] = c
        nm = {}
        for n in nodes:
            prove(n)
            if not SAFE.fullmatch(str(n.get("id"))) or n["id"] in identifiers or n.get("kind") not in {"ligand", "protein", "receptor", "gene", "complex", "rna", "cell", "other"}:
                raise ValueError("Invalid biological entity/type")
            if not n.get("label") or len(n["label"]) > 25 or n["label"] not in n["quote"] or n["kind"] not in n["quote"].lower():
                raise ValueError("Entity name/type not present in source quote")
            if n.get("compartment") not in cm or not isinstance(n.get("center"), list) or len(n["center"]) != 2:
                raise ValueError("Entity compartment/center invalid")
            x, y = n["center"]
            cx, cy, cw, ch = cm[n["compartment"]]["box"]
            if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (x, y)) or not (cx + 82 <= x <= cx + cw - 82 and cy + 36 <= y <= cy + ch - 36):
                raise ValueError("Entity exceeds its source-declared compartment")
            if n["kind"] == "receptor" and cm[n["compartment"]]["role"] != "membrane":
                raise ValueError("Receptor must be placed in a membrane compartment")
            identifiers.add(n["id"])
            nm[n["id"]] = n
        structured_edges = {}
        for e in edges:
            prove(e)
            if not SAFE.fullmatch(str(e.get("id"))) or e["id"] in identifiers or e.get("type") not in REL_WORD:
                raise ValueError("Unsupported interaction")
            if e.get("from") not in nm or e.get("to") not in nm or e["from"] == e["to"]:
                raise ValueError("Interaction endpoints unknown")
            structured_edges[e["id"]] = structured_interaction(sources[e["source"]], e, nm)
            if e["type"] == "transport" and nm[e["from"]]["compartment"] == nm[e["to"]]["compartment"]:
                raise ValueError("Transport must cross compartments")
            if e.get("route") not in {None, "straight", "left-feedback"}:
                raise ValueError("Unknown route")
            identifiers.add(e["id"])
        model_hash = hashlib.sha256(json.dumps(m, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        out = ['<?xml version="1.0" encoding="UTF-8"?>',
               '<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" width="1200" height="760" viewBox="0 0 1200 760" data-model-sha256="%s">' % model_hash,
               '<title>%s</title><metadata>biological_mechanism_pathway; model_sha256=%s</metadata>' % (escape(m.get("title", "Mechanism pathway")), model_hash),
               '<defs><marker id="activate" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#226f8c"/></marker><marker id="transport" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#7461a8"/></marker><marker id="inhibit" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto"><path d="M10,0 L10,12" stroke="#b44b50" stroke-width="2.5"/></marker></defs>',
               '<g id="layer-background" inkscape:groupmode="layer" inkscape:label="background"><rect id="background" width="1200" height="760" fill="#fcfdfd"/></g>',
               '<g id="layer-compartments" inkscape:groupmode="layer" inkscape:label="biological compartments">']
        fills = {"extracellular": "#f5f8fa", "membrane": "#f7e3b2", "cytosol": "#edf6f4", "nucleus": "#f1edf7", "organelle": "#f2f2f2"}
        for c in comps:
            x, y, w, h = c["box"]
            out.append('<g id="compartment-%s" data-source="%s"><rect id="boundary-%s" x="%g" y="%g" width="%g" height="%g" rx="%s" fill="%s" stroke="#7b929c" stroke-width="2"/><text id="compartment-label-%s" x="%g" y="%g" font-family="Helvetica,Arial,sans-serif" font-size="15" font-weight="bold" fill="#435d68">%s</text></g>' % (c["id"], c["source"], c["id"], x, y, w, h, "12" if c["role"] != "membrane" else "28", fills[c["role"]], c["id"], x + 14, y + 22, escape(c["label"])))
        out.append('</g><g id="layer-interactions" inkscape:groupmode="layer" inkscape:label="directed interaction edges">')
        edge_audit = []
        for e in edges:
            x1, y1 = nm[e["from"]]["center"]
            x2, y2 = nm[e["to"]]["center"]
            dx, dy = x2 - x1, y2 - y1
            if not dx and not dy:
                raise ValueError("Coincident nodes")
            scale = min((82 / abs(dx)) if dx else 1e9, (34 / abs(dy)) if dy else 1e9)
            sx, sy, tx, ty = x1 + dx * scale, y1 + dy * scale, x2 - dx * scale, y2 - dy * scale
            if e.get("route") == "left-feedback":
                sx, sy, tx, ty = x1 - 82, y1, x2 - 82, y2
                d = "M%.2f %.2f C55 %.2f 55 %.2f %.2f %.2f" % (sx, sy, sy, ty, tx, ty)
                label_pos = (78, (sy + ty) / 2)
            else:
                d = "M%.2f %.2f L%.2f %.2f" % (sx, sy, tx, ty)
                length = math.hypot(tx - sx, ty - sy)
                if e["type"] == "binding":
                    label_pos = ((sx + tx) / 2 + 110, (sy + ty) / 2 + 4)
                elif e["type"] == "phosphorylation":
                    label_pos = ((sx + tx) / 2 + 95, (sy + ty) / 2 - 18)
                else:
                    label_pos = ((sx + tx) / 2 - (ty - sy) * 16 / length, (sy + ty) / 2 + (tx - sx) * 16 / length)
            marker = "inhibit" if e["type"] == "inhibition" else "transport" if e["type"] == "transport" else "activate"
            marker_attr = "" if e["type"] == "binding" else ' marker-end="url(#%s)"' % marker
            color = "#b44b50" if e["type"] == "inhibition" else "#7461a8" if e["type"] == "transport" else "#226f8c"
            dash = ' stroke-dasharray="8 5"' if e["type"] in {"binding", "transport"} or e.get("certainty") == "uncertain" else ""
            out.append('<path id="edge-%s" d="%s" fill="none" stroke="%s" stroke-width="2.5" data-source="%s" data-relation="%s"%s%s/>' % (e["id"], d, color, e["source"], e["type"], marker_attr, dash))
            out.append('<text id="edge-label-%s" x="%.2f" y="%.2f" font-family="Helvetica,Arial,sans-serif" font-size="12" fill="%s">%s</text>' % (e["id"], *label_pos, color, escape(e["type"])))
            edge_audit.append({"id": e["id"], "from": e["from"], "to": e["to"], "type": e["type"], "source": e["source"], "quote": e["quote"], "evidence": structured_edges[e["id"]], "route": e.get("route", "straight"), "svg_id": "edge-" + e["id"]})
        out.append('</g><g id="layer-entities" inkscape:groupmode="layer" inkscape:label="editable biological entities">')
        for n in nodes:
            x, y = n["center"]
            shape = '<ellipse id="shape-%s" cx="%g" cy="%g" rx="82" ry="34" fill="#ffffff" stroke="#307a88" stroke-width="2"/>' % (n["id"], x, y) if n["kind"] in {"protein", "complex", "other"} else '<rect id="shape-%s" x="%g" y="%g" width="164" height="68" rx="%s" fill="#ffffff" stroke="#307a88" stroke-width="2"/>' % (n["id"], x - 82, y - 34, "32" if n["kind"] == "receptor" else "8")
            out.append('<g id="node-%s" data-kind="%s" data-source="%s" data-compartment="%s">%s<text id="label-%s" x="%g" y="%g" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="16" fill="#163943">%s</text></g>' % (n["id"], n["kind"], n["source"], n["compartment"], shape, n["id"], x, y + 6, escape(n["label"])))
        out.extend(['</g><g id="layer-annotation" inkscape:groupmode="layer" inkscape:label="title and interaction legend">',
                    '<text id="figure-title" x="65" y="54" font-family="Helvetica,Arial,sans-serif" font-size="28" font-weight="bold" fill="#173844">%s</text>' % escape(m.get("title", "Source-bound mechanism"))])
        if is_example:
            out.append('<text id="demo-label" x="65" y="84" font-family="Helvetica,Arial,sans-serif" font-size="14" fill="#765500">SYNTHETIC DEMO — NOT OFFICIAL</text>')
        out.extend([
                    '<text id="legend" x="65" y="723" font-family="Helvetica,Arial,sans-serif" font-size="14" fill="#425c67">Solid arrow = activation/modification; T-end = inhibition; dashed = binding/transport/uncertain. Compartments are source-declared.</text>',
                    '<text id="unresolved" x="65" y="746" font-family="Helvetica,Arial,sans-serif" font-size="12" fill="#6c7d84">Unresolved claims excluded: %d. No expression or significance encoding is inferred.</text>' % len(m.get("unresolved", [])),
                    '</g></svg>'])
        svg = "\n".join(out) + "\n"
        root = ET.fromstring(svg)
        assert not root.findall(".//{http://www.w3.org/2000/svg}image")
        for e in edges:
            node = root.find(".//*[@id='edge-%s']" % e["id"])
            assert node is not None and node.attrib["data-relation"] == e["type"] and node.attrib["data-source"] == e["source"]
        audit = {"status": "partial" if m.get("unresolved") else "passed", "workorder": m["workorder"], "model_sha256": model_hash, "entities": [{"id": n["id"], "label": n["label"], "kind": n["kind"], "compartment": n["compartment"], "source": n["source"], "quote": n["quote"]} for n in nodes], "interactions": edge_audit, "compartments": [{"id": c["id"], "role": c["role"], "box": c["box"], "source": c["source"]} for c in comps], "legend_data_mapping": {"node_color": "uniform; no data encoding", "edge_style": REL_WORD}, "unresolved": m.get("unresolved", []), "semantic_checks": {"entity_names_types_quoted": True, "directed_edges_quoted": True, "ordered_interaction_fields_and_pages_checked": True, "demo_label_only_for_bundled_minimal_example": True, "nodes_inside_compartments": True, "receptors_on_membrane": True, "no_invented_data_encoding": True, "editable_layers_text_edges": True}}
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.audit.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(svg, encoding="utf-8")
        a.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("PASS pathway: %d typed entities, %d sourced directed interactions" % (len(nodes), len(edges)))
        return 0
    except Exception as exc:
        print("FAIL draw: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
