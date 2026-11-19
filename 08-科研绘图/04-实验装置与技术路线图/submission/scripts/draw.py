#!/usr/bin/env python3
"""Port-bound apparatus and numbered technical-route SVG generator (stdlib only)."""
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


def statements(source, quote):
    return [part.strip() for part in re.findall(r"[^.]+(?:\.|$)", source["text"]) if part.strip() and quote in part]


def structured_connection(source, link, ports, components):
    matches = []
    for statement in statements(source, link["quote"]):
        parsed = re.fullmatch(r"(?:A|An) (?P<type>material|information|energy) connection runs from (?P<from>.+) to (?P<to>.+)", statement.rstrip("."))
        if not parsed:
            continue
        left, right = ports[link["from"]], ports[link["to"]]
        expected = (link["type"], components[left["component"]]["label"] + " " + left["label"], components[right["component"]]["label"] + " " + right["label"])
        if (parsed["type"], parsed["from"], parsed["to"]) == expected:
            matches.append({"source": source["id"], "locator": source["locator"], "page": source["page"], "fields": parsed.groupdict()})
    if len(matches) != 1:
        raise ValueError("Connection ordered endpoint evidence mismatch or ambiguity: " + link["id"])
    return matches[0]


def structured_step(source, step):
    matches = []
    for statement in statements(source, step["quote"]):
        parsed = re.fullmatch(r"Step (?P<order>[1-9]\d*) is (?P<label>.+)", statement.rstrip("."))
        if parsed and int(parsed["order"]) == step["order"] and parsed["label"] == step["label"]:
            matches.append({"source": source["id"], "locator": source["locator"], "page": source["page"], "fields": {"order": int(parsed["order"]), "label": parsed["label"]}})
    if len(matches) != 1:
        raise ValueError("Step structured evidence mismatch or ambiguity: " + step["id"])
    return matches[0]


def structured_dependency(source, dependency, steps):
    if dependency["type"] != "dependency":
        raise ValueError("Feedback dependencies need a supported route layout: " + dependency["id"])
    matches = []
    for statement in statements(source, dependency["quote"]):
        parsed = re.fullmatch(r"Step (?P<from>[1-9]\d*) precedes Step (?P<to>[1-9]\d*)", statement.rstrip("."))
        if parsed and (int(parsed["from"]), int(parsed["to"])) == (steps[dependency["from"]]["order"], steps[dependency["to"]]["order"]):
            matches.append({"source": source["id"], "locator": source["locator"], "page": source["page"], "fields": {"from_order": int(parsed["from"]), "predicate": "precedes", "to_order": int(parsed["to"])}})
    if len(matches) != 1:
        raise ValueError("Dependency ordered evidence mismatch or ambiguity: " + dependency["id"])
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
        if m.get("workorder") != "apparatus_and_technical_route" or not m.get("sources"):
            raise ValueError("Expected apparatus_and_technical_route and sources")
        sources = {}
        for s in m["sources"]:
            if not SAFE.fullmatch(str(s.get("id"))) or not s.get("locator") or not s.get("text") or not isinstance(s.get("page"), int) or isinstance(s["page"], bool) or s["page"] < 1 or s["id"] in sources:
                raise ValueError("Invalid or duplicate source/page")
            sources[s["id"]] = s

        def prove(item):
            if item.get("source") not in sources or not isinstance(item.get("quote"), str) or item["quote"] not in sources[item["source"]]["text"]:
                raise ValueError("Evidence missing/non-verbatim: " + str(item.get("id")))

        components, ports, connections, steps, dependencies = (m.get(k) for k in ("components", "ports", "connections", "steps", "dependencies"))
        if not all(isinstance(v, list) for v in (components, ports, connections, steps, dependencies)) or not components or not connections or not steps:
            raise ValueError("Components, ports, connections and steps are required")
        used = set(sources)
        cm = {}
        for c in components:
            prove(c)
            if not SAFE.fullmatch(str(c.get("id"))) or c["id"] in used or c.get("kind") not in {"pump", "reactor", "collector", "sensor", "valve", "controller", "other"}:
                raise ValueError("Invalid component")
            if not c.get("label") or len(c["label"]) > 22 or c["label"] not in c["quote"] or c["kind"] not in c["quote"].lower():
                raise ValueError("Component name/type not in source")
            if not isinstance(c.get("center"), list) or len(c["center"]) != 2 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in c["center"]):
                raise ValueError("Invalid component center")
            x, y = c["center"]
            if not (105 <= x <= 1095 and 190 <= y <= 505):
                raise ValueError("Component outside apparatus safe area")
            used.add(c["id"])
            cm[c["id"]] = c
        pm = {}
        for p in ports:
            prove(p)
            if not SAFE.fullmatch(str(p.get("id"))) or p["id"] in used or p.get("component") not in cm or p.get("side") not in {"left", "right", "top", "bottom"}:
                raise ValueError("Invalid/duplicate port")
            if not p.get("label") or p["label"] not in p["quote"] or cm[p["component"]]["label"] not in p["quote"]:
                raise ValueError("Port label/component not in source")
            x, y = cm[p["component"]]["center"]
            offsets = {"left": (-85, 0), "right": (85, 0), "top": (0, -46), "bottom": (0, 46)}
            dx, dy = offsets[p["side"]]
            p["coordinates"] = [x + dx, y + dy]
            used.add(p["id"])
            pm[p["id"]] = p
        structured_connections = {}
        for link in connections:
            prove(link)
            if not SAFE.fullmatch(str(link.get("id"))) or link["id"] in used or link.get("type") not in {"material", "information", "energy"} or link.get("from") not in pm or link.get("to") not in pm or link["from"] == link["to"]:
                raise ValueError("Invalid directed connection")
            structured_connections[link["id"]] = structured_connection(sources[link["source"]], link, pm, cm)
            used.add(link["id"])
        sm = {}
        structured_steps = {}
        for s in steps:
            prove(s)
            if not SAFE.fullmatch(str(s.get("id"))) or s["id"] in used or not isinstance(s.get("order"), int) or isinstance(s["order"], bool) or s["order"] < 1:
                raise ValueError("Invalid step/order")
            if not s.get("label") or len(s["label"]) > 20 or s["label"] not in s["quote"] or ("Step %d" % s["order"]) not in s["quote"] or s["order"] in {v["order"] for v in sm.values()}:
                raise ValueError("Step label/number not in source")
            structured_steps[s["id"]] = structured_step(sources[s["source"]], s)
            used.add(s["id"])
            sm[s["id"]] = s
        structured_dependencies = {}
        for d in dependencies:
            prove(d)
            if not SAFE.fullmatch(str(d.get("id"))) or d["id"] in used or d.get("from") not in sm or d.get("to") not in sm or d["from"] == d["to"]:
                raise ValueError("Invalid step dependency")
            if d.get("type", "dependency") == "dependency" and sm[d["from"]]["order"] >= sm[d["to"]]["order"]:
                raise ValueError("Forward dependency order mismatch")
            if d.get("type") not in {"dependency", "feedback"}:
                raise ValueError("Invalid dependency type")
            structured_dependencies[d["id"]] = structured_dependency(sources[d["source"]], d, sm)
            used.add(d["id"])
        model_hash = hashlib.sha256(json.dumps(m, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        out = ['<?xml version="1.0" encoding="UTF-8"?>',
               '<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" width="1200" height="800" viewBox="0 0 1200 800" data-model-sha256="%s">' % model_hash,
               '<title>%s</title><metadata>apparatus_and_technical_route; model_sha256=%s</metadata>' % (escape(m.get("title", "Apparatus and technical route")), model_hash),
               '<defs><marker id="material-arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#287996"/></marker><marker id="information-arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#7564a5"/></marker><marker id="energy-arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#b77820"/></marker><marker id="step-arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#657f8c"/></marker></defs>',
               '<g id="layer-background" inkscape:groupmode="layer" inkscape:label="background"><rect id="background" width="1200" height="800" fill="#fcfdfd"/></g>',
               '<g id="layer-route" inkscape:groupmode="layer" inkscape:label="numbered technical route">',
               '<text id="route-heading" x="70" y="567" font-family="Helvetica,Arial,sans-serif" font-size="18" font-weight="bold" fill="#3d555f">TECHNICAL ROUTE — source order</text>']
        ordered_steps = sorted(steps, key=lambda s: s["order"])
        step_pos = {}
        sw = min(220, 900 / max(1, len(ordered_steps)) - 20)
        for i, s in enumerate(ordered_steps):
            if len(ordered_steps) > 4:
                raise ValueError("Deterministic summary supports at most four steps; use an extended layout")
            x = 150 + i * (900 / max(1, len(ordered_steps) - 1)) if len(ordered_steps) > 1 else 600
            step_pos[s["id"]] = (x, 650)
            out.append('<g id="route-%s" data-source="%s"><rect id="route-shape-%s" x="%.2f" y="613" width="%.2f" height="76" rx="12" fill="#f4f7f8" stroke="#6d838c"/><text id="route-number-%s" x="%.2f" y="638" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="13" fill="#6b7d85">Step %d</text><text id="route-label-%s" x="%.2f" y="670" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="18" fill="#243e49">%s</text></g>' % (s["id"], s["source"], s["id"], x - sw / 2, sw, s["id"], x, s["order"], s["id"], x, escape(s["label"])))
        for d in dependencies:
            x1, y1 = step_pos[d["from"]]
            x2, y2 = step_pos[d["to"]]
            if d["type"] == "feedback":
                raise ValueError("Feedback route needs a layout-specific arc, not a straight overlap")
            out.append('<line id="dependency-%s" x1="%.2f" y1="651" x2="%.2f" y2="651" stroke="#657f8c" stroke-width="2" marker-end="url(#step-arrow)" data-source="%s"/>' % (d["id"], x1 + sw / 2 + 4, x2 - sw / 2 - 10, d["source"]))
        out.append('</g><g id="layer-connections" inkscape:groupmode="layer" inkscape:label="typed directed port connections">')
        for link in connections:
            x1, y1 = pm[link["from"]]["coordinates"]
            x2, y2 = pm[link["to"]]["coordinates"]
            dx, dy = x2 - x1, y2 - y1
            length = math.hypot(dx, dy)
            if length < 15:
                raise ValueError("Connection too short or reversed geometry")
            tx, ty = x2 - dx * 10 / length, y2 - dy * 10 / length
            dash = ' stroke-dasharray="8 5"' if link["type"] == "information" else ' stroke-dasharray="3 4"' if link["type"] == "energy" else ""
            out.append('<line id="connection-%s" x1="%g" y1="%g" x2="%.2f" y2="%.2f" stroke="%s" stroke-width="3" marker-end="url(#%s-arrow)" data-source="%s" data-flow="%s"%s/>' % (link["id"], x1, y1, tx, ty, {"material": "#287996", "information": "#7564a5", "energy": "#b77820"}[link["type"]], link["type"], link["source"], link["type"], dash))
        out.append('</g><g id="layer-components" inkscape:groupmode="layer" inkscape:label="apparatus components and ports">')
        for c in components:
            x, y = c["center"]
            out.append('<g id="component-%s" data-kind="%s" data-source="%s"><rect id="shape-%s" x="%g" y="%g" width="170" height="92" rx="%s" fill="#ffffff" stroke="#31778b" stroke-width="2"/><text id="label-%s" x="%g" y="%g" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="17" fill="#193e4b">%s</text><text id="kind-%s" x="%g" y="%g" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="12" fill="#657d88">%s</text></g>' % (c["id"], c["kind"], c["source"], c["id"], x - 85, y - 46, "45" if c["kind"] == "reactor" else "10", c["id"], x, y - 1, escape(c["label"]), c["id"], x, y + 24, escape(c["kind"])))
        for p in ports:
            x, y = p["coordinates"]
            out.append('<circle id="port-%s" cx="%g" cy="%g" r="6" fill="#ffffff" stroke="#254f60" stroke-width="2" data-component="%s" data-side="%s"><title>%s: %s</title></circle>' % (p["id"], x, y, p["component"], p["side"], escape(cm[p["component"]]["label"]), escape(p["label"])))
        out.extend(['</g><g id="layer-annotation" inkscape:groupmode="layer" inkscape:label="title and flow legend">',
                    '<text id="figure-title" x="65" y="55" font-family="Helvetica,Arial,sans-serif" font-size="28" font-weight="bold" fill="#173844">%s</text>' % escape(m.get("title", "Apparatus and technical route"))])
        if is_example:
            out.append('<text id="demo-label" x="65" y="85" font-family="Helvetica,Arial,sans-serif" font-size="14" fill="#765500">SYNTHETIC DEMO — NOT OFFICIAL</text>')
        out.extend([
                    '<text id="apparatus-heading" x="65" y="128" font-family="Helvetica,Arial,sans-serif" font-size="18" font-weight="bold" fill="#3d555f">APPARATUS — explicit ports only</text>',
                    '<text id="legend" x="65" y="735" font-family="Helvetica,Arial,sans-serif" font-size="14" fill="#425c67">Solid = material; dashed = information; dotted = energy. Arrow direction follows from-port to to-port.</text>',
                    '<text id="unresolved" x="65" y="768" font-family="Helvetica,Arial,sans-serif" font-size="12" fill="#6c7d84">Unresolved interfaces or steps excluded: %d; see audit.</text>' % len(m.get("unresolved", [])),
                    '</g></svg>'])
        svg = "\n".join(out) + "\n"
        root = ET.fromstring(svg)
        assert not root.findall(".//{http://www.w3.org/2000/svg}image")
        for link in connections:
            node = root.find(".//*[@id='connection-%s']" % link["id"])
            assert node is not None and node.attrib["data-flow"] == link["type"]
        audit = {"status": "partial" if m.get("unresolved") else "passed", "workorder": m["workorder"], "model_sha256": model_hash, "components": [{"id": c["id"], "label": c["label"], "kind": c["kind"], "source": c["source"]} for c in components], "ports": [{"id": p["id"], "component": p["component"], "side": p["side"], "coordinates": p["coordinates"], "source": p["source"]} for p in ports], "connections": [{"id": l["id"], "from": l["from"], "to": l["to"], "flow": l["type"], "source": l["source"], "quote": l["quote"], "evidence": structured_connections[l["id"]]} for l in connections], "steps": [{"id": s["id"], "order": s["order"], "label": s["label"], "source": s["source"], "quote": s["quote"], "evidence": structured_steps[s["id"]]} for s in ordered_steps], "dependencies": [dict(d, evidence=structured_dependencies[d["id"]]) for d in dependencies], "unresolved": m.get("unresolved", []), "semantic_checks": {"components_and_ports_quoted": True, "connection_direction_and_type_quoted": True, "ordered_connection_and_dependency_fields_checked": True, "step_numbers_parsed_exactly": True, "source_pages_recorded": True, "demo_label_only_for_bundled_minimal_example": True, "no_implicit_connections": True, "step_order_quoted": True, "editable_layers_text_ports": True}}
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.audit.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(svg, encoding="utf-8")
        a.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("PASS apparatus/route: %d components, %d port connections, %d steps" % (len(components), len(connections), len(steps)))
        return 0
    except Exception as exc:
        print("FAIL draw: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
