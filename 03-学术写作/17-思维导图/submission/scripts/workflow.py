#!/usr/bin/env python3
"""Case-driven evidence mindmap audit, editable SVG and contract-bound result writer."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from common import Run, add_arguments, digest, read_json, write_json
from audit import audit_layout, build_layout, render_svg, validate


def material_records(graph, run):
    materials = graph.get("materials")
    if not isinstance(materials, dict) or not materials:
        raise ValueError("graph materials must be a nonempty source_id-to-material mapping")
    records = []
    for material_id, specification in sorted(materials.items()):
        path_value = specification.get("path") if isinstance(specification, dict) else specification
        embedded_text = specification.get("text") if isinstance(specification, dict) else None
        record = {"material_id": material_id, "declared": specification,
                  "embedded_text_available": isinstance(embedded_text, str) and bool(embedded_text)}
        if isinstance(path_value, str) and path_value:
            path = run.path(path_value)
            record["path"] = str(path)
            if path.is_file():
                data = path.read_bytes()
                data.decode("utf-8")
                record.update({"readable": True, "bytes": len(data),
                               "sha256": hashlib.sha256(data).hexdigest()})
            else:
                record.update({"readable": False, "missing": embedded_text is None})
        elif embedded_text is None:
            record.update({"readable": False, "missing": True})
        else:
            record["readable"] = True
        record["read_method"] = "file_bytes_sha256" if "bytes" in record else "embedded_text" if embedded_text is not None else "unavailable"
        records.append(record)
    return records


def graph_snapshot(graph):
    return {"nodes": len(graph.get("nodes", [])),
            "edges": len(graph.get("edges", [])),
            "materials": len(graph.get("materials", {})),
            "sha256": hashlib.sha256(json.dumps(graph, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()}


def main():
    parser = add_arguments(argparse.ArgumentParser(description=__doc__))
    parser.add_argument("--svg-name", default="mindmap.svg")
    args = parser.parse_args()
    try:
        run = Run(args)
    except Exception as error:
        print(json.dumps({"status": "input_error", "error": str(error)}), file=sys.stderr)
        return 2
    try:
        graph = run.candidate("mindmap.json")
        if not isinstance(graph, dict):
            raise ValueError("candidate mindmap must be a top-level JSON object")
        materials = material_records(graph, run)
        report = validate(graph)
        failures = [{"code": item["code"], "message": item["message"]} for item in report.failures]
        for record in materials:
            if record.get("missing"):
                failures.append({"code": "material_unavailable", "message": "material has neither readable file nor embedded text",
                                 "material_id": record["material_id"]})
        svg = run.output / args.svg_name
        artifact = {"path": str(svg), "written": False, "format": "editable_vector_svg"}
        layout_audit = {"status": "not_executed", "coverage_status": "unknown"}
        if report.failures:
            run.uncertainty.append("graph structure invalidated; SVG rendering was not executed")
        else:
            layout = build_layout(graph)
            layout_audit = audit_layout(layout)
            render_svg(layout, svg)
            artifact.update({
                "written": True, "bytes": svg.stat().st_size, "sha256": digest(svg),
                "canvas": layout["canvas"],
                "node_count": len(layout["nodes"]), "edge_path_count": len(layout["paths"]),
                "overlap_count": layout_audit["overlap_count"],
                "clipping_count": layout_audit["clipping_count"],
                "text_and_edges_are_vector": True,
            })
            for category, check in layout_audit["checks"].items():
                if check["status"] != "pass":
                    failures.append({"code": "layout:" + category, "message": "real SVG geometry audit failed",
                                     "count": check["count"]})
        run.uncertainty.append("PNG preview and direct visual inspection were not executed by this deterministic step")
        run.changes.append("audited actual candidate graph and preserved contract-bound editable SVG without deleting partial artifacts")
        graph_file = run.work / "mindmap-readback.json"
        write_json(graph_file, graph)
        reread = read_json(graph_file)
        snapshot = graph_snapshot(reread)
        preview = {
            "status": "required_not_executed",
            "reason": "open the current SVG with an available real renderer, read the PNG and only then replace the contract status if allowed",
            "no_pixel_review_claim": True,
            "source_svg_sha256": artifact.get("sha256"),
        }
        run.finish({"status": "partial", "graph_checks": "pass" if not failures else "fail",
                    "graph": graph, "graph_readback": snapshot,
                    "artifact": artifact, "layout_audit": layout_audit,
                    "materials": materials, "failures": failures, "preview": preview},
                   aliases={"mindmap": "graph", "result": "graph", "layoutaudit": "layout_audit",
                            "svgartifact": "artifact", "source": "materials", "pngpreview": "preview"},
                   code=2,
                   trace_extra={"candidate_snapshot": snapshot, "geometry_computed": layout_audit.get("status") != "not_executed",
                                "preview_pending": True})
        return 2
    except Exception as error:
        run.uncertainty.append(str(error))
        run.finish({"status": "partial", "graph": {"nodes": [], "edges": [], "materials": {}},
                    "layout_audit": {"status": "not_executed"},
                    "artifact": {"written": False}, "failures": [{"code": "execution_error", "message": str(error)}]}, code=2)
    return 2


if __name__ == "__main__":
    sys.exit(main())
