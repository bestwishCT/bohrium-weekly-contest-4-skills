#!/usr/bin/env python3
"""Materialize only the case's original output_contract with hash-bound pathway evidence."""
import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def deep_merge(old, new):
    merged = copy.deepcopy(old)
    for key, value in new.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def put_typed(result, key, value):
    """Update an existing contract field without adding keys or changing its declared type."""
    if key not in result:
        return
    previous = result[key]
    if previous is not None and not isinstance(value, type(previous)):
        raise TypeError(f"contract field {key} changed type")
    if isinstance(previous, dict) and isinstance(value, dict):
        result[key] = deep_merge(previous, value)
    else:
        result[key] = value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="mechanism model containing case_id and output_contract")
    parser.add_argument("--audit", required=True)
    parser.add_argument("--export", required=True, help="export report for this exact final SVG")
    parser.add_argument("--svg", required=True)
    parser.add_argument("--png", required=True)
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--output", required=True, help="final result.json")
    args = parser.parse_args()
    output = Path(args.output)
    try:
        model = json.loads(Path(args.input).read_text(encoding="utf-8"))
        contract = model.get("output_contract")
        if not isinstance(model.get("case_id"), str) or not model["case_id"]:
            raise ValueError("actual case_id is required")
        if not isinstance(contract, dict) or contract.get("case_id") != model["case_id"]:
            raise ValueError("output_contract must preserve the actual case_id")
        result = copy.deepcopy(contract)
        limitations, errors = [], []
        paths = {key: Path(getattr(args, key)) for key in ("audit", "export", "svg", "png", "pdf")}
        missing = [key for key, path in paths.items() if not path.is_file()]
        if missing:
            errors.append("Missing required evidence/artifacts: " + ", ".join(missing))
        audit = json.loads(paths["audit"].read_text(encoding="utf-8")) if paths["audit"].is_file() else {}
        export = json.loads(paths["export"].read_text(encoding="utf-8")) if paths["export"].is_file() else {}
        svg_hash = sha(paths["svg"]) if paths["svg"].is_file() else None
        model_hash = hashlib.sha256(json.dumps(model, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if audit.get("model_sha256") != model_hash:
            errors.append("Audit/model identity is unresolved")
        if paths["svg"].is_file() and "data-model-sha256=\"" + str(audit.get("model_sha256")) not in paths["svg"].read_text(encoding="utf-8"):
            errors.append("Audit is not bound to the final SVG model hash")
        if export.get("status") != "passed" or export.get("source_svg_sha256") != svg_hash:
            errors.append("Final SVG has no passing, hash-matched PNG/PDF export report")
        for kind in ("png", "pdf"):
            entry = export.get(kind)
            path = paths[kind]
            if path.is_file() and (not isinstance(entry, dict) or entry.get("status") != "rendered_and_verified" or entry.get("sha256") != sha(path)):
                errors.append(f"{kind.upper()} hash/render evidence does not match the current artifact")
        unresolved = model.get("unresolved", [])
        if unresolved:
            limitations.append("Unresolved mechanism claims remain excluded: " + json.dumps(unresolved, ensure_ascii=False))
        if audit.get("status") not in ("passed", "partial"):
            errors.append("Mechanism semantic audit did not pass")
        if audit.get("status") == "partial" and not unresolved:
            errors.append("Audit reports partial evidence without an explicit unresolved list")
        limitations = errors + limitations
        status = "partial" if limitations else "completed"
        evidence = {
            "status": status,
            "model_sha256": audit.get("model_sha256"),
            "source_svg_sha256": svg_hash,
            "semantic_audit": str(paths["audit"].resolve()),
            "export_report": str(paths["export"].resolve()),
            "pdf_method": (export.get("pdf") or {}).get("method"),
            "limitations": limitations,
            "visual_and_scientific_review": "not certified by this structural command",
        }
        artifacts = [str(paths[key].resolve()) for key in ("svg", "png", "pdf", "audit", "export") if paths[key].is_file()]
        put_typed(result, "status", status)
        put_typed(result, "artifacts", artifacts)
        put_typed(result, "primary_result", str(paths["svg"].resolve()))
        put_typed(result, "output", str(paths["svg"].resolve()))
        put_typed(result, "sources", copy.deepcopy(model.get("sources", [])))
        put_typed(result, "mechanism_audit", evidence)
        put_typed(result, "validation", evidence)
        put_typed(result, "render", export)
        if "uncertainty" in result:
            old = result["uncertainty"]
            if not isinstance(old, list):
                raise TypeError("contract field uncertainty changed type")
            put_typed(result, "uncertainty", old + [item for item in limitations if item not in old])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": status, "case_id": model["case_id"], "limitations": limitations}, ensure_ascii=False))
        return 2 if errors else 0
    except Exception as exc:
        print("FAIL write_result: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
