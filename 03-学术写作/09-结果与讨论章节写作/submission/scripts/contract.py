#!/usr/bin/env python3
"""Initialize and validate honest partial delivery against the original case contract.

Only Python's standard library is used.  The original output_contract is kept
verbatim in the work snapshot; public result fields are never replaced by the
internal audit schema.
"""

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path


PARTIAL_REASON = "仅完成真实case与材料编目；正文、证据映射和语义复核尚未执行。"


def read_json(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON top level must be an object: %s" % path)
    return value


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def material_entries(case, extras):
    """Read only explicitly declared paths; do not guess case-specific layouts."""
    raw = case.get("materials", [])
    if isinstance(raw, dict):
        raw = list(raw.values())
    entries = []
    if isinstance(raw, list):
        for index, item in enumerate(raw):
            if isinstance(item, str):
                entries.append(("material-%d" % (index + 1), item))
            elif isinstance(item, dict):
                path = next((item[k] for k in ("path", "file_path", "source_path") if isinstance(item.get(k), str)), None)
                if path is not None:
                    entries.append((str(item.get("material_id") or item.get("id") or "material-%d" % (index + 1)), path))
    entries.extend(("cli-material-%d" % (i + 1), path) for i, path in enumerate(extras))
    return entries


def catalog_materials(case, case_path, extras):
    catalog = []
    for material_id, declared in material_entries(case, extras):
        path = Path(declared)
        resolved = (path if path.is_absolute() else case_path.parent / path).resolve()
        record = {"material_id": material_id, "declared_path": declared, "resolved_path": str(resolved)}
        try:
            if not resolved.is_file():
                record["status"] = "missing"
            else:
                record.update(status="readable", size_bytes=resolved.stat().st_size, sha256=sha256(resolved))
        except OSError as exc:
            record.update(status="unreadable", error=str(exc))
        catalog.append(record)
    return catalog


def public_properties(contract):
    if contract.get("type") == "object" and isinstance(contract.get("properties"), dict):
        return contract["properties"]
    return contract


def initial_value(spec, field="", gaps=None):
    """Produce typed placeholders, not scientific values or arbitrary enum claims."""
    if not isinstance(spec, dict):
        return [] if isinstance(spec, list) else None
    value_type = spec.get("type")
    if isinstance(value_type, list):
        value_type = next((item for item in value_type if item != "null"), "null")
    if "status" in field.lower():
        enum = spec.get("enum")
        if isinstance(enum, list):
            if "partial" in enum:
                return "partial"
            if gaps is not None:
                gaps.append("状态字段%s的原enum不含partial；未擅自选择其他完成态。" % field)
            return None
        if value_type in (None, "string"):
            return "partial"
    enum = spec.get("enum")
    if isinstance(enum, list):
        # An explicit pending value is an honest incomplete state; never pick
        # an arbitrary enum member merely to satisfy the schema.
        return "pending" if "pending" in enum else None
    if value_type == "object" or isinstance(spec.get("properties"), dict):
        return {key: initial_value(child, key, gaps) for key, child in spec.get("properties", {}).items()}
    if value_type == "array":
        return []
    if value_type == "string":
        if any(token in field.lower() for token in ("uncert", "gap", "limit")) and gaps:
            return "；".join(gaps)
        return ""
    # null/None is intentional: zero, false, or the first enum member could invent facts.
    return None


def json_equal(left, right):
    return json.dumps(left, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == json.dumps(right, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def type_matches(value, expected):
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def validate_value(value, spec, path, checks):
    if not isinstance(spec, dict):
        return
    expected = spec.get("type")
    expected_types = expected if isinstance(expected, list) else ([expected] if expected else [])
    if expected_types and not any(type_matches(value, item) for item in expected_types):
        checks.append({"code": "contract_type:" + path, "ok": False, "expected": expected, "actual": type(value).__name__})
        return
    enum = spec.get("enum")
    if isinstance(enum, list) and not any(json_equal(value, candidate) for candidate in enum):
        checks.append({"code": "contract_enum:" + path, "ok": False, "allowed": enum, "actual": value})
    if isinstance(value, dict):
        properties = spec.get("properties", {})
        if isinstance(properties, dict):
            required = spec.get("required", [])
            if isinstance(required, list):
                for key in required:
                    checks.append({"code": "contract_required:%s.%s" % (path, key), "ok": isinstance(key, str) and key in value})
            for key, child in properties.items():
                if key in value:
                    validate_value(value[key], child, "%s.%s" % (path, key), checks)
    elif isinstance(value, list) and isinstance(spec.get("items"), (dict, list)):
        items = spec["items"]
        for index, item in enumerate(value):
            item_spec = items[index] if isinstance(items, list) and index < len(items) else ({} if isinstance(items, list) else items)
            validate_value(item, item_spec, "%s[%d]" % (path, index), checks)


def validate_result(case, result):
    checks = [{"code": "case_id_match", "ok": result.get("case_id") == case.get("case_id")}]
    contract = case.get("output_contract")
    if not isinstance(contract, dict):
        return checks + [{"code": "output_contract_present", "ok": False}]
    properties = public_properties(contract)
    for field, spec in properties.items():
        # All original public top-level fields are retained, even optional ones.
        checks.append({"code": "contract_field:" + field, "ok": field in result})
        if field in result:
            validate_value(result[field], spec, field, checks)
    if contract.get("type") == "object":
        required = contract.get("required", [])
        if isinstance(required, list):
            for field in required:
                checks.append({"code": "contract_required:" + str(field), "ok": field in result})
    return checks


def has_partial_status(value):
    if isinstance(value, dict):
        return any((key == "status" and item == "partial") or has_partial_status(item) for key, item in value.items())
    if isinstance(value, list):
        return any(has_partial_status(item) for item in value)
    return False


def init_command(args):
    case_path = Path(args.case).resolve()
    task_path = Path(args.task).resolve()
    case = read_json(case_path)
    case_id = case.get("case_id")
    if not isinstance(case_id, (str, int)) or isinstance(case_id, bool) or (isinstance(case_id, str) and not case_id.strip()):
        raise ValueError("case.json requires a real non-empty string or integer case_id")
    contract = case.get("output_contract")
    if not isinstance(contract, dict):
        raise ValueError("case.json requires the original output_contract object")
    task_text = task_path.read_text(encoding="utf-8")
    if not task_text.strip():
        raise ValueError("task.md must not be empty")
    work_dir, output_dir = Path(args.work_dir), Path(args.output_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    materials = catalog_materials(case, case_path, args.material)
    gaps = [PARTIAL_REASON]
    if not materials:
        gaps.append("未提供已支持的materials路径或--material；材料范围待实读确认。")
    missing = [item["material_id"] for item in materials if item["status"] != "readable"]
    if missing:
        gaps.append("以下声明材料不可读：" + "、".join(missing) + "。")
    result = {"case_id": case_id}
    for field, spec in public_properties(contract).items():
        if field != "case_id":
            result[field] = initial_value(spec, field, gaps)
    snapshot = {
        "synthetic": bool(args.synthetic), "case_id": case_id,
        "case_path": str(case_path), "task_path": str(task_path),
        "task_sha256": hashlib.sha256(task_text.encode("utf-8")).hexdigest(),
        "original_output_contract": copy.deepcopy(contract), "materials": materials,
    }
    write_json(work_dir / "input_snapshot.json", snapshot)
    write_json(work_dir / "run_state.json", {
        "case_id": case_id, "status": "partial", "gaps": gaps,
        "public_result_path": str(output_dir / "result.json"),
        "note": "公开result只含case_id和原合同字段；内部审计字段不自动复制。",
    })
    write_json(output_dir / "result.json", result)
    print(json.dumps({"case_id": case_id, "status": "partial", "gaps": gaps, "result": str(output_dir / "result.json")}, ensure_ascii=False, indent=2))
    return 0


def check_command(args):
    case = read_json(args.case)
    result = read_json(args.result)
    checks = validate_result(case, result)
    if args.snapshot:
        snapshot = read_json(args.snapshot)
        checks.append({"code": "snapshot_case_id", "ok": snapshot.get("case_id") == case.get("case_id")})
        checks.append({"code": "original_output_contract_preserved", "ok": snapshot.get("original_output_contract") == case.get("output_contract")})
    failures = [item for item in checks if not item["ok"]]
    partial = has_partial_status(result)
    status = "fail" if failures else ("partial" if partial else "pass")
    print(json.dumps({"case_id": case.get("case_id"), "status": status, "checks": checks}, ensure_ascii=False, indent=2))
    return 1 if status != "pass" else 0


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Catalog real inputs and write a typed partial result")
    init.add_argument("--case", default="/input/case.json")
    init.add_argument("--task", default="/input/task.md")
    init.add_argument("--work-dir", default="/app/work")
    init.add_argument("--output-dir", default="/app/output")
    init.add_argument("--material", action="append", default=[])
    init.add_argument("--synthetic", action="store_true", help="Explicitly label a non-official test run")
    init.set_defaults(function=init_command)
    check = commands.add_parser("check", help="Recursively validate the delivered result")
    check.add_argument("--case", default="/input/case.json")
    check.add_argument("--result", default="/app/output/result.json")
    check.add_argument("--snapshot", default="/app/work/input_snapshot.json")
    check.set_defaults(function=check_command)
    return parser.parse_args()


def main():
    args = parse_args()
    return args.function(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        sys.exit(2)
