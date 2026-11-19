#!/usr/bin/env python3
import hashlib
import html
import json
import math
import re
import sys
import unicodedata
from pathlib import Path

NODE_W = 172.0
NODE_H = 62.0
FONT_SIZE = 15.0
TEXT_ASCENT = 12.0
TEXT_DESCENT = 4.0
LABEL_H = 21.0


class Report:
    def __init__(self):
        self.checks = []

    def need(self, ok, code, message, details=None):
        check = {"code": code, "ok": bool(ok), "message": message}
        if details is not None:
            check["details"] = details
        self.checks.append(check)

    @property
    def failures(self):
        return [c for c in self.checks if not c["ok"]]


def numbers(text):
    return re.findall(r"(?<![A-Za-z])[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?%?", text or '')


def rect(x, y, width, height):
    # SVG attributes use four decimal places; quantize the shared model first so
    # the audit never measures coordinates more precise than those emitted.
    return {"x": round(float(x), 4), "y": round(float(y), 4),
            "width": round(float(width), 4), "height": round(float(height), 4)}


def right(r):
    return r["x"] + r["width"]


def bottom(r):
    return r["y"] + r["height"]


def area(r):
    return max(0.0, r["width"]) * max(0.0, r["height"])


def intersection(a, b):
    w = min(right(a), right(b)) - max(a["x"], b["x"])
    h = min(bottom(a), bottom(b)) - max(a["y"], b["y"])
    if w <= 1e-9 or h <= 1e-9:
        return None
    return rect(max(a["x"], b["x"]), max(a["y"], b["y"]), w, h)


def contains(outer, inner):
    return (outer["x"] <= inner["x"] + 1e-9 and outer["y"] <= inner["y"] + 1e-9
            and right(outer) + 1e-9 >= right(inner)
            and bottom(outer) + 1e-9 >= bottom(inner))


def fmt(value):
    value = round(float(value), 4)
    return str(int(value)) if value.is_integer() else str(value)


def text_width(text):
    # textLength in the generated SVG uses this same deterministic advance.
    return sum(FONT_SIZE if unicodedata.east_asian_width(ch) in ("W", "F") else 8.5
               for ch in text)


def wrap_text(text, max_width):
    lines = []
    current = ""
    current_width = 0.0
    for ch in text:
        advance = FONT_SIZE if unicodedata.east_asian_width(ch) in ("W", "F") else 8.5
        if current and current_width + advance > max_width + 1e-9:
            lines.append(current)
            current = ""
            current_width = 0.0
        current += ch
        current_width += advance
    if current or not lines:
        lines.append(current)
    return [line for line in lines if line]


def text_geometry(text, x, baseline):
    width = text_width(text)
    return {
        "text": text,
        "x": round(float(x), 4),
        "baseline": round(float(baseline), 4),
        "text_length": round(float(width), 4),
        "rect": rect(x, baseline - TEXT_ASCENT, width, TEXT_ASCENT + TEXT_DESCENT),
    }


def segment_intersects_rect(path, r):
    # Liang-Barsky clipping: touching a rectangle boundary also counts for edges.
    x1, y1, x2, y2 = path["x1"], path["y1"], path["x2"], path["y2"]
    dx, dy = x2 - x1, y2 - y1
    p = (-dx, dx, -dy, dy)
    q = (x1 - r["x"], right(r) - x1, y1 - r["y"], bottom(r) - y1)
    low, high = 0.0, 1.0
    for pi, qi in zip(p, q):
        if abs(pi) < 1e-12:
            if qi < 0:
                return False
            continue
        t = qi / pi
        if pi < 0:
            low = max(low, t)
        else:
            high = min(high, t)
        if low > high:
            return False
    return True


def label_candidate(origin, label_w, canvas, nodes, paths, labels, own_edge_id):
    origin_x, origin_y, along_x, along_y, perp_x, perp_y, length = origin
    offsets = [0.0]
    for distance in (0.25, 0.5, 0.75):
        offsets.extend((length * distance, -length * distance))
    perpendicular = [0.0, 28.0, -28.0, 48.0, -48.0, 72.0, -72.0, 104.0, -104.0, 144.0, -144.0]
    candidates = [(origin_x, origin_y)]
    for along in offsets[1:]:
        for across in perpendicular:
            candidates.append((origin_x + along_x * along + perp_x * across,
                               origin_y + along_y * along + perp_y * across))
    # Also fan away from the original midpoint when the edge itself is crowded.
    for across in perpendicular[1:]:
        candidates.append((origin_x + perp_x * across, origin_y + perp_y * across))
    candidates = sorted(set(candidates), key=lambda p: ((p[0] - origin_x) ** 2 + (p[1] - origin_y) ** 2,
                                                         abs((p[0] - origin_x) * perp_x + (p[1] - origin_y) * perp_y)))
    for cx, cy in candidates:
        candidate = rect(cx - label_w / 2.0, cy - LABEL_H / 2.0, label_w, LABEL_H)
        if not contains(canvas, candidate):
            continue
        if any(intersection(candidate, node["rect"]) for node in nodes):
            continue
        if any(intersection(candidate, label["rect"]) for label in labels):
            continue
        if any(segment_intersects_rect(path, candidate) for path in paths if path["id"] != own_edge_id):
            continue
        return candidate
    return rect(origin_x - label_w / 2.0, origin_y - LABEL_H / 2.0, label_w, LABEL_H)


def build_layout(d):
    nodes = d["nodes"]
    levels = {}
    for n in nodes:
        levels.setdefault(n.get("depth", 0), []).append(n)
    margin, col_w, row_h = 35.0, 240.0, 92.0
    width = max(1000.0, margin * 2 + max(levels) * col_w + NODE_W) if levels else 1000.0
    height = max(420.0, max(len(v) for v in levels.values()) * row_h + 70.0) if levels else 420.0
    layout_options = d.get("layout", {})
    canvas_option = layout_options.get("canvas", {})
    width = float(canvas_option.get("width", width))
    height = float(canvas_option.get("height", height))
    if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
        raise ValueError("layout.canvas width/height must be finite positive numbers")
    canvas = rect(0, 0, width, height)
    node_options = layout_options.get("nodes", {})
    positions = {}
    for depth, arr in levels.items():
        for i, n in enumerate(arr):
            positions[n["id"]] = (margin + depth * col_w,
                                  45.0 + i * row_h + (height - 70.0 - len(arr) * row_h) / 2.0)
    node_geometries = []
    for n in nodes:
        x, y = positions[n["id"]]
        option = node_options.get(n["id"], {})
        x, y = float(option.get("x", x)), float(option.get("y", y))
        w, h = float(option.get("width", NODE_W)), float(option.get("height", NODE_H))
        if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
            raise ValueError("layout.nodes entries must have finite positions and positive sizes")
        node_rect = rect(x, y, w, h)
        baselines = [y + 21.0 + j * 17.0 for j in range(len(wrap_text(n["label"], w - 20.0)))]
        lines = wrap_text(n["label"], w - 20.0)
        texts = [text_geometry(line, x + 10.0, baseline) for line, baseline in zip(lines, baselines)]
        node_geometries.append({"id": n["id"], "parent": n.get("parent"),
                                "container": n.get("container") is True,
                                "rect": node_rect, "texts": texts})
    by_id = {n["id"]: n for n in node_geometries}

    def endpoints(source, target):
        return (right(source["rect"]), source["rect"]["y"] + source["rect"]["height"] / 2.0,
                target["rect"]["x"], target["rect"]["y"] + target["rect"]["height"] / 2.0)

    paths = []
    for n in nodes:
        if n.get("parent"):
            x1, y1, x2, y2 = endpoints(by_id[n["parent"]], by_id[n["id"]])
            paths.append({"id": "parent:" + n["id"], "kind": "parent",
                          "from": n["parent"], "to": n["id"],
                          "x1": x1, "y1": y1, "x2": x2, "y2": y2})
    for e in d.get("edges", []):
        x1, y1, x2, y2 = endpoints(by_id[e["from"]], by_id[e["to"]])
        paths.append({"id": e["id"], "kind": "relationship",
                      "from": e["from"], "to": e["to"],
                      "x1": x1, "y1": y1, "x2": x2, "y2": y2})

    label_options = layout_options.get("edge_labels", {})
    labels = []
    for e in d.get("edges", []):
        path = next(p for p in paths if p["id"] == e["id"] and p["kind"] == "relationship")
        content = text_geometry(e["type"], 0, 0)
        label_w = max(54.0, content["text_length"] + 10.0)
        cx = (path["x1"] + path["x2"]) / 2.0
        # Preserve the original renderer's ten-pixel baseline lift above an edge.
        cy = (path["y1"] + path["y2"]) / 2.0 - 14.5
        dx, dy = path["x2"] - path["x1"], path["y2"] - path["y1"]
        length = max(math.hypot(dx, dy), 1.0)
        origin = (cx, cy, dx / length, dy / length, -dy / length, dx / length, length)
        option = label_options.get(e["id"], {})
        if "x" in option or "y" in option:
            label_rect = rect(option.get("x", cx - label_w / 2.0),
                              option.get("y", cy - LABEL_H / 2.0), label_w, LABEL_H)
        else:
            label_rect = label_candidate(origin, label_w, canvas, node_geometries, paths, labels, e["id"])
        label_text = text_geometry(e["type"], label_rect["x"] + 5.0, label_rect["y"] + 15.0)
        labels.append({"id": e["id"], "rect": label_rect, "text": label_text})
    return {"canvas": canvas, "nodes": node_geometries, "paths": paths, "edge_labels": labels}


def audit_layout(layout):
    categories = (
        "canvas_clipping", "node_text_clipping", "node_overlap", "text_overlap",
        "edge_label_node_overlap", "edge_label_overlap", "edge_node_crossing",
        "edge_label_path_overlap",
    )
    checks = {name: {"status": "pass", "count": 0} for name in categories}
    issues = []

    def add(category, kind, objects, **details):
        issue = {"category": category, "kind": kind, "objects": objects}
        issue.update(details)
        issues.append(issue)
        checks[category]["count"] += 1

    canvas = layout["canvas"]
    nodes, labels, paths = layout["nodes"], layout["edge_labels"], layout["paths"]
    bounded = []
    for n in nodes:
        bounded.append(("node", n["id"], n["rect"]))
        for i, text in enumerate(n["texts"]):
            bounded.append(("node_text", "%s:%d" % (n["id"], i + 1), text["rect"]))
            if not contains(n["rect"], text["rect"]):
                add("node_text_clipping", "text_outside_own_node", [n["id"]],
                    line=i + 1, node_rect=n["rect"], text_rect=text["rect"])
    for label in labels:
        bounded.append(("edge_label", label["id"], label["rect"]))
        bounded.append(("edge_label_text", label["id"], label["text"]["rect"]))
    for kind, object_id, item_rect in bounded:
        if not contains(canvas, item_rect):
            outside = {"left": max(0.0, -item_rect["x"]), "top": max(0.0, -item_rect["y"]),
                       "right": max(0.0, right(item_rect) - right(canvas)),
                       "bottom": max(0.0, bottom(item_rect) - bottom(canvas))}
            add("canvas_clipping", kind, [object_id], rect=item_rect, outside=outside)
    for i, a in enumerate(nodes):
        for b in nodes[i + 1:]:
            hit = intersection(a["rect"], b["rect"])
            if not hit:
                continue
            intentional = False
            if a["container"] and b["parent"] == a["id"] and contains(a["rect"], b["rect"]):
                intentional = True
            if b["container"] and a["parent"] == b["id"] and contains(b["rect"], a["rect"]):
                intentional = True
            if not intentional:
                add("node_overlap", "node_node", [a["id"], b["id"]],
                    intersection=hit, overlap_area=area(hit))
        for other in nodes:
            if a["id"] == other["id"]:
                continue
            # Descendant text on an explicit container background is intentional;
            # text-text and ancestor-text/descendant-node collisions remain errors.
            if other["container"] and a["parent"] == other["id"] and contains(other["rect"], a["rect"]):
                continue
            for i, text in enumerate(a["texts"]):
                hit = intersection(text["rect"], other["rect"])
                if hit:
                    add("text_overlap", "node_text_other_node", [a["id"] + ":" + str(i + 1), other["id"]],
                        intersection=hit, overlap_area=area(hit))
    texts = [(n["id"], i + 1, t) for n in nodes for i, t in enumerate(n["texts"])]
    for i, (a_id, a_line, a_text) in enumerate(texts):
        for b_id, b_line, b_text in texts[i + 1:]:
            if a_id == b_id:
                continue
            hit = intersection(a_text["rect"], b_text["rect"])
            if hit:
                add("text_overlap", "node_text_node_text",
                    [a_id + ":" + str(a_line), b_id + ":" + str(b_line)],
                    intersection=hit, overlap_area=area(hit))
    for i, label in enumerate(labels):
        for n in nodes:
            hit = intersection(label["rect"], n["rect"])
            if hit:
                add("edge_label_node_overlap", "edge_label_node", [label["id"], n["id"]],
                    intersection=hit, overlap_area=area(hit))
        for other in labels[i + 1:]:
            hit = intersection(label["rect"], other["rect"])
            if hit:
                add("edge_label_overlap", "edge_label_edge_label", [label["id"], other["id"]],
                    intersection=hit, overlap_area=area(hit))
    for path in paths:
        for n in nodes:
            if n["id"] in (path["from"], path["to"]):
                continue
            if segment_intersects_rect(path, n["rect"]):
                add("edge_node_crossing", "edge_non_endpoint_node", [path["id"], n["id"]],
                    path={k: path[k] for k in ("x1", "y1", "x2", "y2")}, node_rect=n["rect"])
        for label in labels:
            if path["id"] == label["id"]:
                continue  # White edge-label cards intentionally mask their own line.
            if segment_intersects_rect(path, label["rect"]):
                add("edge_label_path_overlap", "edge_other_edge_label", [path["id"], label["id"]],
                    path={k: path[k] for k in ("x1", "y1", "x2", "y2")}, label_rect=label["rect"])
    for check in checks.values():
        if check["count"]:
            check["status"] = "fail"
    clipping_count = checks["canvas_clipping"]["count"] + checks["node_text_clipping"]["count"]
    overlap_count = sum(check["count"] for name, check in checks.items()
                        if name not in ("canvas_clipping", "node_text_clipping"))
    return {
        "status": "fail" if any(c["status"] == "fail" for c in checks.values()) else "pass",
        "coverage_status": "partial",
        "checks": checks,
        "overlap_count": overlap_count,
        "clipping_count": clipping_count,
        "issues": issues,
        "undetected_or_approximate": [
            {"code": "font_raster_bounds", "status": "partial",
             "detail": "SVG horizontal textLength and baseline boxes are deterministic; actual glyph outlines, fallback fonts and raster antialiasing require PNG inspection."},
            {"code": "marker_and_stroke_pixels", "status": "partial",
             "detail": "Path segments and label/node rectangles are checked; arrowhead raster pixels, stroke joins and renderer-specific antialiasing are not fully measured."},
            {"code": "edge_edge_crossings_and_semantic_readability", "status": "unknown", "count": None,
             "detail": "Edge-edge crossings, route ambiguity and human readability/font availability are not decided by this geometry audit."},
        ],
        "intentional_exceptions": [
            "A node text box may intersect its own node background.",
            "An edge-label card may mask its own edge path, but not other paths.",
            "Only an explicit container:true parent fully containing its direct child exempts that child's rectangle/text from the container background; ordinary parent-child rectangles are not exempt, and text-text collisions still fail.",
        ],
    }


def render_svg(layout, out):
    canvas = layout["canvas"]
    width, height = canvas["width"], canvas["height"]
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%s" height="%s" viewBox="0 0 %s %s">' % (
            fmt(width), fmt(height), fmt(width), fmt(height)),
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#456"/></marker></defs>',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<style>text{font-family:Arial,"PingFang SC",sans-serif;font-size:15px;fill:#172033}.edge{stroke:#678;stroke-width:2;fill:none;marker-end:url(#arrow)}.rel{stroke:#b06;stroke-dasharray:5 4;fill:none;marker-end:url(#arrow)}</style>',
    ]
    for path in paths_sorted_for_render(layout["paths"]):
        css = "edge" if path["kind"] == "parent" else "rel"
        parts.append('<path class="%s" data-path-id="%s" d="M%s,%s L%s,%s"/>' % (
            css, html.escape(path["id"], quote=True), fmt(path["x1"]), fmt(path["y1"]),
            fmt(path["x2"]), fmt(path["y2"])))
    for n in layout["nodes"]:
        r = n["rect"]
        color = "#dbeafe" if not n["parent"] else "#e7f6ed"
        parts.append('<rect data-node-id="%s" x="%s" y="%s" width="%s" height="%s" rx="10" fill="%s" stroke="#345"/>' % (
            html.escape(n["id"], quote=True), fmt(r["x"]), fmt(r["y"]), fmt(r["width"]), fmt(r["height"]), color))
        for i, text in enumerate(n["texts"]):
            parts.append('<text data-node-text-id="%s:%d" x="%s" y="%s" textLength="%s" lengthAdjust="spacingAndGlyphs">%s</text>' % (
                html.escape(n["id"], quote=True), i + 1, fmt(text["x"]), fmt(text["baseline"]),
                fmt(text["text_length"]), html.escape(text["text"])))
    for label in layout["edge_labels"]:
        r, text = label["rect"], label["text"]
        parts.append('<rect data-edge-label-id="%s" x="%s" y="%s" width="%s" height="%s" rx="3" fill="#fff" opacity=".94"/>' % (
            html.escape(label["id"], quote=True), fmt(r["x"]), fmt(r["y"]), fmt(r["width"]), fmt(r["height"])))
        parts.append('<text data-edge-label-text-id="%s" x="%s" y="%s" textLength="%s" lengthAdjust="spacingAndGlyphs" fill="#b06">%s</text>' % (
            html.escape(label["id"], quote=True), fmt(text["x"]), fmt(text["baseline"]),
            fmt(text["text_length"]), html.escape(text["text"])))
    parts.append('</svg>')
    Path(out).write_text('\n'.join(parts), encoding='utf-8')


def paths_sorted_for_render(paths):
    # Relationship paths precede parent paths, as in the original SVG.
    return sorted(paths, key=lambda p: 0 if p["kind"] == "relationship" else 1)


def geometry_manifest(layout):
    return {
        "canvas": layout["canvas"],
        "node_rects": {n["id"]: n["rect"] for n in layout["nodes"]},
        "node_text_rects": {n["id"]: [t["rect"] for t in n["texts"]] for n in layout["nodes"]},
        "edge_label_rects": {label["id"]: label["rect"] for label in layout["edge_labels"]},
        "edge_label_text_rects": {label["id"]: label["text"]["rect"] for label in layout["edge_labels"]},
        "paths": [{k: p[k] for k in ("id", "kind", "from", "to", "x1", "y1", "x2", "y2")}
                  for p in layout["paths"]],
    }


def finish(data, path, report, render=None, layout_audit=None):
    out = {"artifact": str(path), "status": "pass" if not report.failures else "fail",
           "checks": report.checks}
    if render is not None:
        out["render"] = render
    if layout_audit is not None:
        out["layout_audit"] = layout_audit
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if not report.failures else 1


def load():
    if len(sys.argv) < 2:
        raise ValueError("usage: audit.py INPUT.json [render OUTPUT.svg]")
    p = Path(sys.argv[1])
    data = json.loads(p.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('top-level JSON must be object')
    return data, p


def validate(d):
    r = Report()
    nodes = {n['id']: n for n in d['nodes']}
    roots = [n for n in d['nodes'] if not n.get('parent')]
    r.need(len(roots) == 1, 'single_root', '导图有且仅有一个根')
    r.need(len(nodes) == len(d['nodes']), 'unique_nodes', '节点ID唯一')
    for n in d['nodes']:
        tag = n['id']
        r.need(bool(n['label'].strip()) and len(n['label']) <= 30, 'label:' + tag, '节点文字精简且非空')
        if n.get('parent'):
            r.need(n['parent'] in nodes, 'parent:' + tag, '父节点存在')
            if n['parent'] in nodes:
                r.need(n.get('depth') == nodes[n['parent']].get('depth', 0) + 1, 'depth:' + tag, '深度与父子层级一致')
        for ev in n['evidence']:
            r.need(ev['source_id'] in d['materials'] and bool(ev['locator']), 'node_evidence:' + tag, '节点证据可追溯')
            r.need(ev['kind'] in ('fact', 'summary', 'inference'), 'node_kind:' + tag, '节点事实/概括/推断明示')
    for e in d['edges']:
        tag = e['id']
        r.need(e['from'] in nodes and e['to'] in nodes, 'edge_nodes:' + tag, '关系端点存在')
        r.need(e['type'] in ('dependency', 'support', 'conflict', 'contrast', 'time', 'citation', 'cause'), 'edge_type:' + tag, '关系类型明确')
        r.need(bool(e['evidence']) and all(x['source_id'] in d['materials'] and bool(x['locator']) for x in e['evidence']), 'edge_evidence:' + tag, '关系级证据可追溯')
    for n in d['nodes']:
        seen, cur = set(), n
        while cur.get('parent'):
            if cur['id'] in seen:
                r.need(False, 'cycle:' + n['id'], '层级无环')
                break
            seen.add(cur['id'])
            if cur['parent'] not in nodes:
                break
            cur = nodes[cur['parent']]
    return r


def main():
    render_mode = len(sys.argv) > 3 and sys.argv[1] == 'render'
    if render_mode:
        sys.argv[1:] = [sys.argv[2], sys.argv[3]]
    d, p = load()
    r = validate(d)
    if r.failures:
        return finish(d, p, r)
    layout = build_layout(d)
    layout_audit = audit_layout(layout)
    messages = {
        "canvas_clipping": "节点、文字与边标签在画布边界内",
        "node_text_clipping": "节点文字未裁切出自身节点",
        "node_overlap": "非容器节点矩形无遮叠",
        "text_overlap": "节点文字不压其他节点或文字",
        "edge_label_node_overlap": "边标签不压节点",
        "edge_label_overlap": "边标签互不重叠",
        "edge_node_crossing": "边不穿过非端点节点",
        "edge_label_path_overlap": "边标签只遮罩自身边",
    }
    for name, check in layout_audit["checks"].items():
        r.need(check["status"] == "pass", "layout:" + name, messages[name],
               {"status": check["status"], "count": check["count"]})
    if render_mode:
        out_path = Path(sys.argv[2])
        render_svg(layout, out_path)
        geometry = geometry_manifest(layout)
        info = {"svg": str(out_path), "nodes": len(d['nodes']), "edges": len(d.get('edges', [])),
                "width": layout["canvas"]["width"], "height": layout["canvas"]["height"],
                "overlap_count": layout_audit["overlap_count"],
                "clipping_count": layout_audit["clipping_count"],
                "layout_audit": layout_audit, "geometry": geometry,
                "geometry_sha256": hashlib.sha256(json.dumps(geometry, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()}
        return finish(d, p, r, render=info)
    return finish(d, p, r, layout_audit=layout_audit)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        sys.exit(2)
