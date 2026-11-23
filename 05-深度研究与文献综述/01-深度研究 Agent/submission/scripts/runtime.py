"""Local, standard-library runtime checks; never performs a literature search."""
import copy
import hashlib
import json
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

TOOL_OK = {"ok", "empty"}
TOOL_BAD = {"error", "timeout", "permission_denied", "parse_failed"}
TOOL_STATES = TOOL_OK | TOOL_BAD | {"not_executed"}


class InputError(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise InputError("cannot read UTF-8 text %s: %s" % (path, exc))


def load_json(path):
    try:
        return json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        raise InputError("invalid JSON %s: %s" % (path, exc))


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_json(path, value):
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def require_list(value, label):
    if not isinstance(value, list):
        raise InputError(label + " must be an array")
    return value


def unique_ids(items, label, errors):
    seen = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            errors.append(label + " entry lacks a non-empty string id")
            continue
        if item["id"] in seen:
            errors.append("duplicate %s id %s" % (label, item["id"]))
        seen[item["id"]] = item
    return seen


def case_receipt(args):
    case_path, task_path = Path(args.case), Path(args.task)
    case = load_json(case_path)
    if not isinstance(case, dict):
        raise InputError("case.json must be an object")
    case_id = case.get("case_id", case.get("id"))
    if not isinstance(case_id, str) or not case_id:
        raise InputError("actual case_id is missing")
    if "output_contract" not in case:
        raise InputError("actual case.output_contract is missing")
    task = read_text(task_path)
    work, output = Path(args.work), Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    write_text(work / "task_snapshot.md", task)
    write_json(work / "output_contract.json", case["output_contract"])
    resource = {"provided": bool(args.resources), "read": False}
    if args.resources:
        text = read_text(args.resources)
        write_text(work / "resources_snapshot.yaml", text)
        resource = {"provided": True, "read": True, "path": str(Path(args.resources).resolve()),
                    "sha256": sha256(text), "snapshot": str(work / "resources_snapshot.yaml"),
                    "note": "verbatim snapshot; the Agent must inspect actual capabilities and permissions"}
    receipt = {"case_path": str(case_path.resolve()), "task_path": str(task_path.resolve()),
               "case_id": case_id, "case_sha256": sha256(read_text(case_path)),
               "task_sha256": sha256(task), "task_chars": len(task),
               "case_keys": sorted(case), "resources": resource,
               "dependency": {"language": "Python", "running_version": platform.python_version(),
                              "minimum_syntax": "3.9", "third_party_packages": []},
               "received_at": now()}
    write_json(work / "input_receipt.json", receipt)
    return case, case_id, receipt


def source_text(source, base):
    if isinstance(source.get("text"), str):
        return source["text"]
    if source.get("text_path"):
        path = Path(source["text_path"])
        return read_text(path if path.is_absolute() else base / path)
    return None


def public_source(source):
    return {k: copy.deepcopy(v) for k, v in source.items() if k not in {"text", "content", "artifact_payload"}}


def public_tool(tool):
    return {k: copy.deepcopy(v) for k, v in tool.items() if k != "artifact_payload"}


def load_tool_artifact(tool, base):
    """Load a declared JSON object or explicitly mapped JSONL records."""
    artifact_path = Path(tool["artifact"])
    path = artifact_path if artifact_path.is_absolute() else base / artifact_path
    artifact_format = tool.get("artifact_format", "json")
    if artifact_format == "json":
        artifact = load_json(path)
        if not isinstance(artifact, dict):
            raise InputError("JSON tool artifact must be an object")
        return artifact
    if artifact_format != "jsonl":
        raise InputError("unknown tool artifact format: %s" % artifact_format)
    record_field = tool.get("record_id_field")
    source_field = tool.get("source_id_field")
    if not isinstance(record_field, str) or not record_field or not isinstance(source_field, str) or not source_field:
        raise InputError("JSONL artifact requires explicit record_id_field and source_id_field mappings")
    records = []
    for line_number, line in enumerate(read_text(path).splitlines(), 1):
        if not line.strip():
            raise InputError("JSONL artifact line %d is empty" % line_number)
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise InputError("invalid JSONL artifact line %d: %s" % (line_number, exc))
        if not isinstance(item, dict):
            raise InputError("JSONL artifact line %d must be an object" % line_number)
        record_id, source_id = item.get(record_field), item.get(source_field)
        if not isinstance(record_id, str) or not record_id or not isinstance(source_id, str) or not source_id:
            raise InputError("JSONL artifact line %d lacks its declared record/source mapping" % line_number)
        records.append({"record_id": record_id, "source_id": source_id})
    return records


def check_common(data, base):
    errors = []
    sources_list = require_list(data.get("sources", []), "sources")
    tools_list = require_list(data.get("tool_runs", []), "tool_runs")
    evidence = require_list(data.get("evidence", []), "evidence")
    sources = unique_ids(sources_list, "source", errors)
    tools = unique_ids(tools_list, "tool_run", errors)
    evidence_by_id = unique_ids(evidence, "evidence", errors)
    for item in sources_list:
        if not item.get("authorization"):
            errors.append("source %s lacks authorization/provenance" % item.get("id"))
        if not (item.get("locator") or item.get("path") or item.get("url")):
            errors.append("source %s lacks a real locator" % item.get("id"))
    for tool in tools_list:
        tid = tool.get("id", "<unknown>")
        if tool.get("status") not in TOOL_STATES:
            errors.append("tool %s has unknown status" % tid)
        if not isinstance(tool.get("parameters"), dict) or not tool.get("kind"):
            errors.append("tool %s lacks kind or parameters" % tid)
        if not tool.get("return_summary"):
            errors.append("tool %s lacks a truthful return summary" % tid)
        if tool.get("status") == "not_executed" and not (tool.get("not_executed_reason") or tool.get("error")):
            errors.append("pending tool %s lacks an explicit not_executed reason" % tid)
        artifact = tool.get("artifact")
        if not artifact and tool.get("status") != "not_executed":
            errors.append("tool %s lacks an artifact path, including for failures" % tid)
        elif artifact:
            path = Path(artifact)
            if not (path if path.is_absolute() else base / path).is_file():
                errors.append("tool %s artifact is not retained: %s" % (tid, artifact))
        if tool.get("status") == "ok" and tool.get("artifact_format", "json") not in {"json", "jsonl"}:
            errors.append("tool %s has an unknown artifact format" % tid)
        if tool.get("status") == "ok" and tool.get("artifact_format") == "jsonl":
            if not tool.get("record_id_field") or not tool.get("source_id_field"):
                errors.append("tool %s JSONL artifact lacks record/source mappings" % tid)
        if tool.get("status") == "empty" and tool.get("returned_count") != 0:
            errors.append("empty tool %s must report returned_count=0" % tid)
        if tool.get("status") in TOOL_BAD and not tool.get("error"):
            errors.append("failed tool %s lacks its actual error" % tid)
        if tool.get("retry_of") and tool["retry_of"] not in tools:
            errors.append("tool %s names missing retry parent" % tid)
    for row in evidence:
        eid = row.get("id", "<unknown>")
        source = sources.get(row.get("source_id"))
        if source is None:
            errors.append("evidence %s references an unknown source" % eid)
            continue
        if not row.get("quote") or not row.get("locator"):
            errors.append("evidence %s lacks quote or exact locator" % eid)
            continue
        text = source_text(source, base)
        if text is None:
            errors.append("evidence %s has no retrieved text to verify; parse the real source first" % eid)
            continue
        if source.get("sha256") and source["sha256"] != sha256(text):
            errors.append("source %s content hash mismatch" % source["id"])
        quote = row["quote"]
        if quote not in text:
            errors.append("evidence %s quote is absent from its source" % eid)
        locator = row["locator"]
        if isinstance(locator, dict):
            if "page" in locator:
                page = locator["page"]
                if not isinstance(page, int) or isinstance(page, bool) or page < 1:
                    errors.append("evidence %s page must be a positive integer" % eid)
                else:
                    pages = text.split("\f")
                    if page > len(pages) or quote not in pages[page - 1]:
                        errors.append("evidence %s quote is not on declared page" % eid)
            if isinstance(locator.get("paragraph"), int):
                paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
                if not 1 <= locator["paragraph"] <= len(paragraphs) or quote not in paragraphs[locator["paragraph"] - 1]:
                    errors.append("evidence %s quote is not in declared paragraph" % eid)
        tid = row.get("tool_run_id")
        tool = tools.get(tid)
        if not isinstance(tid, str) or not tid or tool is None or tool.get("status") != "ok":
            errors.append("evidence %s must come from a successful tool run" % eid)
            continue
        try:
            artifact = load_tool_artifact(tool, base)
        except (InputError, KeyError) as exc:
            errors.append("evidence %s tool artifact cannot be checked: %s" % (eid, exc))
            continue
        record_id = row.get("record_id")
        if isinstance(artifact, list):
            if record_id is not None:
                if not any(item["record_id"] == record_id and item["source_id"] == row["source_id"] for item in artifact):
                    errors.append("evidence %s record/source pair is absent from its JSONL tool artifact" % eid)
            elif not any(item["source_id"] == row["source_id"] for item in artifact):
                errors.append("evidence %s source_id is absent from its JSONL tool artifact" % eid)
            continue
        record_ids = artifact.get("record_ids", [])
        source_ids = artifact.get("source_ids", [])
        record_ids = list(record_ids) if isinstance(record_ids, list) else []
        source_ids = list(source_ids) if isinstance(source_ids, list) else []
        if artifact.get("record_id") is not None:
            record_ids.append(artifact["record_id"])
        if artifact.get("source_id") is not None:
            source_ids.append(artifact["source_id"])
        if record_id is not None:
            if record_id not in record_ids:
                errors.append("evidence %s record_id is absent from its tool artifact" % eid)
            if artifact.get("source_id") is not None and row["source_id"] != artifact["source_id"]:
                errors.append("evidence %s source_id differs from its tool artifact" % eid)
            elif source_ids and row["source_id"] not in source_ids:
                errors.append("evidence %s source_id is absent from its tool artifact" % eid)
        elif row["source_id"] not in record_ids + source_ids:
            errors.append("evidence %s source_id is absent from its tool artifact" % eid)
    return sources, tools, evidence_by_id, errors


def materialize_tools(data, work):
    """Used by standalone synthetic smoke drivers; production passes real artifacts."""
    for tool in data.get("tool_runs", []):
        if "artifact_payload" in tool:
            path = Path(tool["artifact"])
            path = path if path.is_absolute() else Path(work) / path
            write_json(path, tool["artifact_payload"])


def outcome(evidence, tools, conflicts):
    failed = [t for t in tools.values() if t.get("status") in TOOL_BAD or t.get("status") == "not_executed"]
    if any(t.get("status") == "not_executed" for t in failed):
        return "partial", 5, failed
    if not evidence:
        return "blocked", 4, failed
    if failed:
        return "partial", 5, failed
    if conflicts:
        return "needs_review", 0, failed
    return "completed", 0, failed


def expand(value, context):
    if isinstance(value, dict):
        return {k: expand(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v, context) for v in value]
    if isinstance(value, str) and value.startswith("$"):
        target = context
        for part in value[1:].split("."):
            if not isinstance(target, dict) or part not in target:
                raise InputError("unknown result reference " + value)
            target = target[part]
        return copy.deepcopy(target)
    return value


def normalize_contract(contract):
    if isinstance(contract, str):
        try:
            contract = json.loads(contract)
        except json.JSONDecodeError:
            return None, ["output_contract is prose; full manual validation is required"]
    if not isinstance(contract, dict):
        return None, ["unsupported output_contract; full manual validation is required"]
    if isinstance(contract.get("schema"), dict):
        contract = contract["schema"]
    fields = contract.get("fields")
    if isinstance(fields, list):
        props, required = {}, []
        for field in fields:
            if not isinstance(field, dict) or not isinstance(field.get("name"), str):
                return None, ["fields entries need name/type; full manual validation is required"]
            spec = {k: copy.deepcopy(v) for k, v in field.items() if k not in {"name", "required"}}
            props[field["name"]] = spec
            if field.get("required"):
                required.append(field["name"])
        normalized = {"type": "object", "properties": props, "required": required,
                      "additionalProperties": contract.get("additionalProperties", True)}
        for key in ("$defs", "definitions"):
            if key in contract:
                normalized[key] = copy.deepcopy(contract[key])
        return normalized, []
    if isinstance(fields, dict):
        return dict(contract, type="object", properties=fields,
                    required=contract.get("required", [])), []
    if "properties" not in contract:
        reserved = {"type", "required", "additionalProperties", "description", "$schema", "$defs",
                    "definitions", "$ref", "items", "enum", "anyOf", "oneOf", "allOf", "pattern", "format"}
        direct = {k: v for k, v in contract.items() if k not in reserved and isinstance(v, dict)}
        if direct:
            normalized = {"type": "object", "properties": direct,
                          "required": contract.get("required", []),
                          "additionalProperties": contract.get("additionalProperties", True)}
            for key in ("$defs", "definitions"):
                if key in contract:
                    normalized[key] = copy.deepcopy(contract[key])
            return normalized, []
    return contract, []


def check_type(value, expected):
    types = expected if isinstance(expected, list) else [expected]
    for kind in types:
        if kind == "object" and isinstance(value, dict): return True
        if kind == "array" and isinstance(value, list): return True
        if kind == "string" and isinstance(value, str): return True
        if kind == "boolean" and isinstance(value, bool): return True
        if kind == "number" and isinstance(value, (int, float)) and not isinstance(value, bool): return True
        if kind == "integer" and isinstance(value, int) and not isinstance(value, bool): return True
        if kind == "null" and value is None: return True
    return False


def resolve_local_ref(root, reference):
    if not isinstance(reference, str) or not reference.startswith("#"):
        raise ValueError("only local $ref values are supported")
    if reference == "#":
        return root
    if not reference.startswith("#/"):
        raise ValueError("local $ref must be a JSON pointer")
    target = root
    for encoded in reference[2:].split("/"):
        part = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(target, dict) and part in target:
            target = target[part]
        elif isinstance(target, list) and part.isdigit() and int(part) < len(target):
            target = target[int(part)]
        else:
            raise ValueError("unresolved local $ref " + reference)
    if not isinstance(target, dict):
        raise ValueError("local $ref must point to a schema object")
    return target


def validate_schema(value, schema, path, errors, warnings, root=None, ref_stack=(), definition_errors=None):
    if not isinstance(schema, dict):
        return
    if root is None:
        root = schema
    if definition_errors is None:
        definition_errors = errors
    if "$ref" in schema:
        reference = schema["$ref"]
        if reference in ref_stack:
            definition_errors.append(path + " has a cyclic local $ref " + str(reference))
            return
        try:
            target = resolve_local_ref(root, reference)
        except ValueError as exc:
            definition_errors.append(path + ": " + str(exc))
            return
        validate_schema(value, target, path, errors, warnings, root, ref_stack + (reference,), definition_errors)
        schema = {k: v for k, v in schema.items() if k != "$ref"}
    if any(k in schema for k in ("oneOf", "allOf", "format")):
        warnings.append(path + ": unsupported advanced schema constraints require manual review")
    if "anyOf" in schema:
        alternatives = schema["anyOf"]
        matched = False
        if not isinstance(alternatives, list) or not alternatives:
            definition_errors.append(path + " anyOf must be a non-empty schema array")
        else:
            for alternative in alternatives:
                if not isinstance(alternative, dict):
                    definition_errors.append(path + " anyOf entries must be schema objects")
                    continue
                branch_errors, branch_warnings, branch_definition_errors = [], [], []
                validate_schema(value, alternative, path, branch_errors, branch_warnings, root, ref_stack, branch_definition_errors)
                for item in branch_warnings:
                    if item not in warnings:
                        warnings.append(item)
                for item in branch_definition_errors:
                    if item not in definition_errors:
                        definition_errors.append(item)
                if not branch_errors and not branch_warnings and not branch_definition_errors:
                    matched = True
            if not matched:
                errors.append(path + " does not satisfy anyOf")
    if "type" in schema and not check_type(value, schema["type"]):
        errors.append(path + " violates declared type " + str(schema["type"]))
        return
    if "enum" in schema and value not in schema["enum"]:
        errors.append(path + " is outside declared enum")
    if "pattern" in schema:
        try:
            pattern = re.compile(schema["pattern"])
        except (re.error, TypeError) as exc:
            definition_errors.append(path + " has an invalid declared pattern: " + str(exc))
        else:
            if isinstance(value, str) and pattern.search(value) is None:
                errors.append(path + " does not match declared pattern")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(path + " lacks required field " + key)
        props = schema.get("properties", {})
        for key, item in value.items():
            if key in props:
                validate_schema(item, props[key], path + "." + key, errors, warnings, root, ref_stack, definition_errors)
            elif schema.get("additionalProperties") is False:
                errors.append(path + " has undeclared field " + key)
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(item, schema["additionalProperties"], path + "." + key, errors, warnings, root, ref_stack, definition_errors)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(path + " has too few items")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                validate_schema(item, schema["items"], "%s[%d]" % (path, index), errors, warnings, root, ref_stack, definition_errors)
    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        errors.append(path + " is shorter than minLength")


def build(args, domain_validate):
    case, case_id, receipt = case_receipt(args)
    work, output, base = Path(args.work), Path(args.output), Path(args.input).resolve().parent
    data = load_json(args.input)
    if not isinstance(data, dict):
        raise InputError("pipeline input must be an object")
    write_json(work / "recovery_point.json", {"state": "inputs_frozen", "at": now(),
               "resume": "retain real tool artifacts, repair canonical input, rerun the same command"})
    sources, tools, evidence, common_errors = check_common(data, base)
    derived, domain_audit, domain_errors = domain_validate(data, sources, tools, evidence, base)
    conflicts = data.get("conflicts", [])
    state, exit_code, failed = outcome(evidence, tools, conflicts)
    if domain_audit.get("partial") and state not in {"blocked", "partial"}:
        state, exit_code = "partial", 5
    errors = common_errors + domain_errors
    if errors:
        state, exit_code = "invalid", 2
    uncertainty = copy.deepcopy(data.get("uncertainty", {}))
    for key in ("conflicts", "biases", "gaps", "unknowns"):
        uncertainty.setdefault(key, [])
    uncertainty["conflicts"] = copy.deepcopy(conflicts)
    if domain_audit.get("gaps") is not None:
        uncertainty["gaps"] = copy.deepcopy(domain_audit["gaps"])
    operational = [{"state": t["status"], "tool_run_id": t["id"], "error": t.get("error"),
                    "recovery": "keep prior artifacts; repair access/tool and rerun only affected descendants"} for t in failed]
    if not evidence:
        operational.append({"state": "no_admissible_evidence",
                            "boundary": "declared executed scope only; absence is not a global research gap"})
    if operational:
        uncertainty["operational"] = operational
    changes = copy.deepcopy(data.get("changes", []))
    changes.append({"type": "runtime_build", "at": now(), "input_sha256": sha256(read_text(args.input)),
                    "contract_sha256": sha256(read_text(work / "output_contract.json"))})
    public_sources = [public_source(s) for s in data.get("sources", [])]
    public_tools = [public_tool(t) for t in data.get("tool_runs", [])]
    context = {"data": data.get("data", {}), "derived": derived, "sources": public_sources,
               "tool_runs": public_tools, "uncertainty": uncertainty, "changes": changes,
               "status": state, "evidence_matrix": list(evidence.values()), "case_id": case_id}
    result = expand(data.get("result", {}), context)
    if not isinstance(result, dict):
        raise InputError("result template must expand to an object")
    if "case_id" in result and result["case_id"] != case_id:
        raise InputError("result case_id differs from actual input")
    result["case_id"] = case_id
    contract, contract_warnings = normalize_contract(case["output_contract"])
    contract_errors = []
    if contract is not None:
        validate_schema(result, contract, "$result", contract_errors, contract_warnings)
    if contract_warnings:
        contract_errors.extend(contract_warnings)
    if contract_errors:
        errors.extend(contract_errors)
        state, exit_code = "invalid", 2
    audit = {"case_id": case_id, "status": state, "generated_at": now(), "input_receipt": receipt,
             "source_locators": public_sources, "tool_runs": public_tools, "evidence_matrix": list(evidence.values()),
             "uncertainty": uncertainty, "changes": changes, "domain": domain_audit, "derived": derived,
             "checks": [{"id": "actual_case_and_task_frozen", "passed": True},
                        {"id": "quotes_and_source_permissions", "passed": not common_errors},
                        {"id": "domain_integrity", "passed": not domain_errors},
                        {"id": "actual_output_contract", "passed": not contract_errors and not contract_warnings}],
             "validation_errors": errors, "contract_warnings": contract_warnings,
             "boundary": "Only the supplied, authorized and successfully retrieved materials support this result; no global coverage or experiment is claimed."}
    write_json(work / "integrity_audit.json", audit)
    write_json(output / "result.json", result)
    write_json(output / "research_audit.json", audit)
    write_json(work / "recovery_point.json", {"state": state, "at": now(),
               "completed_tool_ids": [t["id"] for t in tools.values() if t.get("status") in TOOL_OK],
               "failed_tool_ids": [t["id"] for t in failed],
               "resume": "retain outputs; rerun failed tools and their descendants, then rerun build"})
    print(json.dumps({"status": state, "exit_code": exit_code, "result": str(output / "result.json"),
                      "audit": str(output / "research_audit.json"), "errors": errors}, ensure_ascii=False))
    return exit_code


def cli(parser, domain_validate):
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "build"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--case", required=True)
        cmd.add_argument("--task", required=True)
        cmd.add_argument("--work", required=True)
        cmd.add_argument("--output", required=True)
        cmd.add_argument("--resources")
        if name == "build":
            cmd.add_argument("--input", required=True)
    args = parser.parse_args()
    try:
        if args.command == "inspect":
            _, case_id, receipt = case_receipt(args)
            write_json(Path(args.output) / "run_status.json",
                       {"case_id": case_id, "status": "inputs_frozen", "receipt": receipt,
                        "next": "read actual materials and create the canonical work input before build"})
            print(json.dumps({"status": "inputs_frozen", "case_id": case_id, "exit_code": 0}, ensure_ascii=False))
            return 0
        return build(args, domain_validate)
    except (InputError, OSError, KeyError, TypeError, ValueError) as exc:
        if getattr(args, "work", None):
            write_json(Path(args.work) / "run_error.json", {"status": "invalid", "error": str(exc), "at": now(),
                       "recovery": "do not discard evidence or failure artifacts; correct the stated input and rerun"})
        print(json.dumps({"status": "invalid", "error": str(exc), "exit_code": 2}, ensure_ascii=False))
        return 2
