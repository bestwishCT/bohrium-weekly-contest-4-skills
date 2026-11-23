#!/usr/bin/env python3
"""Self-contained real-case preflight and output-contract finalizer."""
import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

REQUIRE_PLAN_CATALOG = False
PATH_KEYS = {"path", "file", "file_path", "material_path", "source_path"}
MATERIAL_KEYS = {"materials", "material_files", "input_files", "attachments"}


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def valid_id(value):
    return isinstance(value, str) and bool(value.strip())


def contract_schema(case):
    contract = case.get("output_contract")
    if not isinstance(contract, dict):
        raise ValueError("case.output_contract must be a JSON-Schema-shaped object")
    if "properties" in contract or "type" in contract:
        return contract
    for key in ("json_schema", "schema"):
        candidate = contract.get(key)
        if isinstance(candidate, dict) and ("properties" in candidate or "type" in candidate):
            return candidate
    raise ValueError("output_contract has no supported type/properties schema")


def set_partial_status(result, schema):
    properties = schema.get("properties", {})
    status_schema = properties.get("status") if isinstance(properties, dict) else None
    if not isinstance(status_schema, dict):
        return
    allowed = status_schema.get("enum")
    if isinstance(allowed, list):
        for candidate in ("partial", "failed", "error"):
            if candidate in allowed:
                result["status"] = candidate
                return
    elif status_schema.get("type") == "string" or (isinstance(status_schema.get("type"), list) and "string" in status_schema["type"]):
        result["status"] = "partial"


def partial_value(schema, case_id, root_key=None):
    if root_key == "case_id":
        return case_id
    schema_type = schema.get("type")
    if schema_type == "object" or isinstance(schema.get("properties"), dict):
        return {key: partial_value(child, case_id) for key, child in schema.get("properties", {}).items()}
    if schema_type == "array":
        return []
    return None


def write_partial(result_path, case, schema=None):
    case_id = case.get("case_id") if isinstance(case, dict) else None
    if schema is None:
        result = {"case_id": case_id, "status": "partial"}
    else:
        result = partial_value(schema, case_id)
        if not isinstance(result, dict):
            result = {"case_id": case_id}
        result["case_id"] = case_id
        set_partial_status(result, schema)
    save_json(result_path, result)
    return result


def digest(path):
    data = Path(path).read_bytes()
    return {"resolved_path": str(Path(path).resolve()), "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "read_status": "read"}


def declared_paths(node, base, found, location="case"):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "output_contract":
                continue
            child_location = f"{location}.{key}"
            if key in PATH_KEYS and isinstance(value, str):
                found.append((child_location, value))
            elif key in MATERIAL_KEYS and isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, str):
                        found.append((f"{child_location}[{index}]", item))
                    else:
                        declared_paths(item, base, found, f"{child_location}[{index}]")
            else:
                declared_paths(value, base, found, child_location)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            declared_paths(item, base, found, f"{location}[{index}]")


def read_materials(case, case_path, errors):
    declarations = []
    declared_paths(case, case_path.parent, declarations)
    catalog, seen = [], set()
    for location, raw in declarations:
        if not raw.strip():
            errors.append({"code": "MATERIAL_PATH_INVALID", "at": location})
            continue
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", raw) and not raw.startswith("file://"):
            errors.append({"code": "MATERIAL_UNAVAILABLE_OFFLINE", "at": location, "declared_path": raw})
            catalog.append({"at": location, "declared_path": raw, "read_status": "unavailable_offline"})
            continue
        raw_path = raw[7:] if raw.startswith("file://") else raw
        path = Path(raw_path)
        if not path.is_absolute():
            path = case_path.parent / path
        path = path.resolve()
        files = [path]
        if path.is_dir():
            files = sorted(item for item in path.rglob("*") if item.is_file() and
                           not any(part.startswith(".") for part in item.relative_to(path).parts))
        if not path.exists():
            errors.append({"code": "MATERIAL_MISSING", "at": location, "declared_path": raw})
            catalog.append({"at": location, "declared_path": raw, "read_status": "missing"})
            continue
        for file_path in files:
            key = str(file_path)
            if key in seen:
                continue
            seen.add(key)
            try:
                record = {"at": location, "declared_path": raw, **digest(file_path)}
                catalog.append(record)
            except OSError as exc:
                errors.append({"code": "MATERIAL_READ_FAILED", "at": location,
                               "declared_path": raw, "message": str(exc)})
                catalog.append({"at": location, "declared_path": raw, "read_status": "read_failed"})
    return catalog


def plan_catalog(case, errors):
    plans, seen_lists = [], set()
    def visit(node, location="case"):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "output_contract":
                    continue
                if key in {"plans", "candidate_plans", "candidates"} and isinstance(value, list) and id(value) not in seen_lists:
                    seen_lists.add(id(value))
                    for index, plan in enumerate(value):
                        at = f"{location}.{key}[{index}]"
                        if not isinstance(plan, dict):
                            errors.append({"code": "PLAN_RECORD_INVALID", "at": at})
                            continue
                        original_id = plan.get("id", plan.get("plan_id", plan.get("ref")))
                        version = plan.get("version", plan.get("version_id"))
                        source = plan.get("source", plan.get("source_ref", plan.get("origin")))
                        plans.append({"at": at, "original_id": original_id, "version": version, "source": source})
                        if original_id is None or not valid_id(str(original_id)):
                            errors.append({"code": "PLAN_ORIGINAL_ID_MISSING", "at": at})
                        if version is None or (isinstance(version, str) and not version.strip()):
                            errors.append({"code": "PLAN_VERSION_MISSING", "at": at})
                        if source is None or source == "":
                            errors.append({"code": "PLAN_SOURCE_MISSING", "at": at})
                else:
                    visit(value, f"{location}.{key}")
        elif isinstance(node, list):
            for index, item in enumerate(node):
                visit(item, f"{location}[{index}]")
    visit(case)
    if not plans:
        errors.append({"code": "VERSIONED_PLANS_NOT_FOUND"})
    return plans


def preflight(args):
    errors, case, schema = [], None, None
    case_path = Path(args.case).resolve()
    try:
        case = load_json(case_path)
        if not isinstance(case, dict):
            raise ValueError("case.json root must be an object")
        if not valid_id(case.get("case_id")):
            errors.append({"code": "CASE_ID_REQUIRED"})
        schema = contract_schema(case)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        errors.append({"code": "CASE_OR_CONTRACT_INVALID", "message": str(exc)})
    write_partial(args.result, case or {}, schema)
    task_record = None
    try:
        task_record = {"declared_path": str(Path(args.task).resolve()), **digest(args.task)}
    except OSError as exc:
        errors.append({"code": "TASK_MISSING_OR_UNREADABLE", "message": str(exc)})
        task_record = {"declared_path": str(args.task), "read_status": "missing_or_unreadable"}
    materials = read_materials(case, case_path, errors) if isinstance(case, dict) else []
    if args.auxiliary:
        try:
            auxiliary = load_json(args.auxiliary)
            auxiliary_case_id = auxiliary.get("case_id") if isinstance(auxiliary, dict) else None
            if not isinstance(case, dict) or auxiliary_case_id != case.get("case_id"):
                errors.append({"code": "AUXILIARY_CASE_ID_MISMATCH", "actual": auxiliary_case_id,
                               "expected": case.get("case_id") if isinstance(case, dict) else None})
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            errors.append({"code": "AUXILIARY_INVALID", "message": str(exc)})
    plans = plan_catalog(case, errors) if REQUIRE_PLAN_CATALOG and isinstance(case, dict) else []
    manifest = {"status": "pass" if not errors else "partial",
                "case_id": case.get("case_id") if isinstance(case, dict) else None,
                "case_path": str(case_path), "task": task_record, "materials": materials,
                "plan_catalog": plans, "errors": errors,
                "scope": "declared local materials only; bytes hashed, scientific extraction remains to be done"}
    save_json(args.manifest, manifest)
    print(f"{manifest['status']}: {args.result}; preflight_errors={len(errors)}")
    return 0 if not errors else 2


def matches_type(value, expected):
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return (isinstance(value, int) and not isinstance(value, bool)) or (isinstance(value, float) and math.isfinite(value))
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def validate(value, schema, at, errors, root=False):
    if not isinstance(schema, dict):
        errors.append({"code": "CONTRACT_SCHEMA_INVALID", "at": at})
        return
    expected = schema.get("type")
    expected_types = expected if isinstance(expected, list) else ([expected] if expected else [])
    if expected_types and not any(matches_type(value, item) for item in expected_types):
        errors.append({"code": "CONTRACT_TYPE_MISMATCH", "at": at, "expected": expected,
                       "actual_type": type(value).__name__})
        return
    if "enum" in schema and value not in schema["enum"]:
        errors.append({"code": "CONTRACT_ENUM_MISMATCH", "at": at, "allowed": schema["enum"], "actual": value})
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append({"code": "CONTRACT_REQUIRED_MISSING", "at": f"{at}.{key}"})
        if isinstance(properties, dict):
            for key, child in properties.items():
                if key in value:
                    validate(value[key], child, f"{at}.{key}", errors)
            for key in value:
                if key not in properties and (root or schema.get("additionalProperties") is False):
                    errors.append({"code": "UNDECLARED_TOP_LEVEL_FIELD" if root else "CONTRACT_ADDITIONAL_FIELD",
                                   "at": f"{at}.{key}"})
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            validate(item, schema["items"], f"{at}[{index}]", errors)


def finalize(args):
    errors = []
    try:
        case = load_json(args.case)
        schema = contract_schema(case)
        case_id = case.get("case_id")
        if not valid_id(case_id):
            errors.append({"code": "CASE_ID_REQUIRED"})
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        save_json(args.report, {"status": "partial", "errors": [{"code": "CASE_OR_CONTRACT_INVALID", "message": str(exc)}]})
        return 2
    result = None
    try:
        result = load_json(args.result)
        if not isinstance(result, dict):
            errors.append({"code": "RESULT_OBJECT_REQUIRED"})
        elif result.get("case_id") != case_id:
            errors.append({"code": "RESULT_CASE_ID_MISMATCH", "expected": case_id, "actual": result.get("case_id")})
        validate(result, schema, "$", errors, root=True)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        errors.append({"code": "RESULT_INVALID", "message": str(exc)})
    try:
        manifest = load_json(args.manifest)
        if manifest.get("status") != "pass" or manifest.get("case_id") != case_id:
            errors.append({"code": "PREFLIGHT_INCOMPLETE", "manifest_status": manifest.get("status")})
    except (OSError, ValueError, TypeError, json.JSONDecodeError, AttributeError) as exc:
        errors.append({"code": "PREFLIGHT_MANIFEST_INVALID", "message": str(exc)})
    if errors:
        if isinstance(result, dict):
            set_partial_status(result, schema)
            save_json(args.result, result)
        else:
            write_partial(args.result, case, schema)
    report = {"status": "pass" if not errors else "partial", "case_id": case_id, "errors": errors,
              "result_path": str(Path(args.result).resolve()),
              "validation": "recursive required/type/enum/items/nested-object validation; top-level auxiliary keys rejected"}
    save_json(args.report, report)
    print(f"{report['status']}: {args.result}; finalization_errors={len(errors)}")
    return 0 if not errors else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    pre = subparsers.add_parser("preflight")
    pre.add_argument("--case", default="/input/case.json")
    pre.add_argument("--task", default="/input/task.md")
    pre.add_argument("--result", default="/app/output/result.json")
    pre.add_argument("--manifest", default="/app/work/preflight.json")
    pre.add_argument("--auxiliary", help="optional work JSON whose case_id must match the real case")
    pre.set_defaults(function=preflight)
    final = subparsers.add_parser("finalize")
    final.add_argument("--case", default="/input/case.json")
    final.add_argument("--task", default="/input/task.md")
    final.add_argument("--result", default="/app/output/result.json")
    final.add_argument("--manifest", default="/app/work/preflight.json")
    final.add_argument("--report", default="/app/work/finalization.json")
    final.set_defaults(function=finalize)
    args = parser.parse_args()
    return args.function(args)


if __name__ == "__main__":
    sys.exit(main())
