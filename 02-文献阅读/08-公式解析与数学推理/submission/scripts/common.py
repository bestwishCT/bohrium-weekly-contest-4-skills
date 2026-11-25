"""Shared deterministic I/O helpers; Python 3.9+ standard library only."""
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import sys

VERSION = "1.1.0"
MISSING = object()


def norm(key):
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(str(temporary), str(path))


def digest(path):
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1048576), b""):
            hasher.update(block)
    return hasher.hexdigest()


def stable_id(prefix, *parts):
    raw = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return prefix + hashlib.sha256(raw).hexdigest()[:20]


def resolve(root, value):
    path = Path(value)
    return path if path.is_absolute() else Path(root) / path


def pick(payload, key, aliases):
    if not isinstance(payload, dict):
        return MISSING
    if key in payload:
        return payload[key]
    wanted = norm(key)
    for existing, value in payload.items():
        if norm(existing) == wanted:
            return value
    target = aliases.get(wanted)
    if target is not None:
        if target in payload:
            return payload[target]
        for existing, value in payload.items():
            if norm(existing) == norm(target):
                return value
    return MISSING


def unknown_value(template, key):
    lowered = str(key).lower()
    if "status" in lowered:
        return "partial"
    if isinstance(template, bool):
        return False
    if isinstance(template, (int, float)):
        return None
    if isinstance(template, list):
        return []
    if isinstance(template, dict):
        return {}
    return "not_reported"


def adapt_template(template, value, key, aliases):
    if isinstance(template, (dict, list)) and not template:
        if value is MISSING:
            return {} if isinstance(template, dict) else []
        return value
    if isinstance(template, dict) and ("properties" in template or template.get("type") in {"object", "array", "string", "number", "integer", "boolean"}):
        kind = template.get("type")
        if "properties" in template or kind == "object":
            source = value if isinstance(value, dict) else {}
            properties = template.get("properties", {})
            return {name: adapt_template(spec, pick(source, name, aliases), name, aliases)
                    for name, spec in properties.items()}
        if kind == "array":
            sequence = [] if value is MISSING or value is None else value
            if not isinstance(sequence, list):
                sequence = [sequence]
            return [adapt_template(template.get("items", {}), item, key, aliases) for item in sequence]
        if value is MISSING:
            if kind == "array":
                return []
            if kind == "object":
                return {}
            if kind in {"number", "integer"}:
                return None
            if kind == "boolean":
                return False
            allowed = template.get("enum")
            if isinstance(allowed, list):
                if "status" in str(key).lower():
                    for candidate in ("partial", "insufficient_evidence", "failed", "error"):
                        if candidate in allowed:
                            return candidate
                if "not_reported" in allowed:
                    return "not_reported"
                # Do not invent an arbitrary scientific enum value; validation will
                # keep the result partial until the Agent supplies a declared value.
                return ""
            return unknown_value("", key)
        return value
    if isinstance(template, dict):
        source = value if isinstance(value, dict) else {}
        return {name: adapt_template(spec, pick(source, name, aliases), name, aliases)
                for name, spec in template.items()}
    if isinstance(template, list):
        sequence = [] if value is MISSING or value is None else value
        if not isinstance(sequence, list):
            sequence = [sequence]
        if template:
            return [adapt_template(template[0], item, key, aliases) for item in sequence]
        return sequence
    return unknown_value(template, key) if value is MISSING else value


def bind_contract(case, payload, aliases=None):
    """Fit only keys actually declared by output_contract; retain case_id."""
    aliases = {norm(key): value for key, value in (aliases or {}).items()}
    contract = case.get("output_contract")
    if not contract:
        result = dict(payload)
    else:
        result = adapt_template(contract, payload, "result", aliases)
        if not isinstance(result, dict):
            result = {"result": result}
    if "case_id" in case:
        result["case_id"] = case["case_id"]
    return result


def matches_schema_type(value, kind):
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if kind == "null":
        return value is None
    return True


def contract_errors(contract, value, aliases=None, path="result", require_fields=True):
    """Validate nested required/type/enum/items without inventing missing evidence."""
    aliases = aliases or {}
    schema_kinds = {"object", "array", "string", "number", "integer", "boolean", "null"}
    if isinstance(contract, dict) and ("properties" in contract or contract.get("type") in schema_kinds):
        declared_kind = contract.get("type", "object" if "properties" in contract else None)
        kinds = declared_kind if isinstance(declared_kind, list) else [declared_kind]
        if declared_kind is not None and not any(matches_schema_type(value, kind) for kind in kinds):
            return [path + " has incorrect type for " + str(declared_kind)]
        errors = []
        if "enum" in contract and value not in contract["enum"]:
            errors.append(path + " is outside declared enum " + repr(contract["enum"]))
        if "object" in kinds:
            properties = contract.get("properties", {})
            if require_fields:
                required = contract.get("required", [])
                for name in required:
                    if pick(value, name, aliases) is MISSING:
                        errors.append(path + "." + name + " is required")
            for name, spec in properties.items():
                child = pick(value, name, aliases)
                if child is not MISSING:
                    errors.extend(contract_errors(spec, child, aliases, path + "." + name, require_fields))
        elif "array" in kinds and isinstance(contract.get("items"), dict):
            for index, item in enumerate(value):
                errors.extend(contract_errors(contract["items"], item, aliases,
                                              "%s[%d]" % (path, index), require_fields))
        return errors
    if isinstance(contract, dict):
        if not isinstance(value, dict):
            return [path + " has incorrect type for object"]
        errors = []
        for name, spec in contract.items():
            child = pick(value, name, aliases)
            child_path = path + "." + name
            if child is MISSING:
                if require_fields:
                    errors.append(child_path + " is missing")
            else:
                errors.extend(contract_errors(spec, child, aliases, child_path, require_fields))
        return errors
    if isinstance(contract, list):
        if not isinstance(value, list):
            return [path + " has incorrect type for array"]
        if contract:
            return [error for index, item in enumerate(value)
                    for error in contract_errors(contract[0], item, aliases, "%s[%d]" % (path, index), require_fields)]
        return []
    expected = type(contract)
    if value is None or not isinstance(value, expected) or (expected is not bool and isinstance(value, bool)):
        return [path + " has incorrect type for " + expected.__name__]
    return []


def paragraphs(value):
    """Accept common paragraph containers without inferring missing identifiers."""
    if isinstance(value, dict):
        for key in ("paragraphs", "material", "content", "sections"):
            if key in value and isinstance(value[key], (list, dict)):
                if key == "sections" and isinstance(value[key], list):
                    rows = []
                    for section in value[key]:
                        rows.extend(paragraphs(section))
                    return rows
                return paragraphs(value[key])
        if any(key in value for key in ("paragraph_id", "id")) and any(key in value for key in ("text", "quote", "content")):
            return [value]
        return []
    if isinstance(value, list):
        rows = []
        for item in value:
            if isinstance(item, dict) and any(key in item for key in ("paragraph_id", "id")):
                rows.append(item)
            elif isinstance(item, (dict, list)):
                rows.extend(paragraphs(item))
        return rows
    return []


def paragraph_map(rows):
    result = {}
    for row in rows:
        identifier = row.get("paragraph_id", row.get("id"))
        text = row.get("text", row.get("content", row.get("quote", "")))
        if identifier is not None:
            result[str(identifier)] = str(text)
    return result


def evidence_ok(index, evidence):
    if not isinstance(evidence, dict):
        return False
    identifier = str(evidence.get("paragraph_id", ""))
    quote = evidence.get("quote", "")
    return bool(identifier and isinstance(quote, str) and quote and quote in index.get(identifier, ""))


def mark_result_partial(result, contract):
    """Use a declared degraded status instead of introducing an invalid enum value."""
    if not isinstance(result, dict) or "status" not in result:
        return
    spec = None
    if isinstance(contract, dict):
        if isinstance(contract.get("properties"), dict):
            spec = contract["properties"].get("status")
        elif "type" not in contract:
            spec = contract.get("status")
    allowed = spec.get("enum") if isinstance(spec, dict) else None
    if isinstance(allowed, list):
        if result.get("status") in {"partial", "insufficient_evidence"} and result["status"] in allowed:
            return
        if "partial" in allowed:
            result["status"] = "partial"
        elif "insufficient_evidence" in allowed:
            result["status"] = "insufficient_evidence"
        return
    if spec is None or isinstance(spec, str) or (isinstance(spec, dict) and spec.get("type") == "string"):
        result["status"] = "partial"


class Run:
    def __init__(self, args):
        self.args = args
        self.root = Path(args.input_dir)
        self.work = Path(args.work)
        self.output = Path(args.output)
        self.work.mkdir(parents=True, exist_ok=True)
        self.output.mkdir(parents=True, exist_ok=True)
        self.case = read_json(args.case)
        self.case_id = self.case.get("case_id")
        self.task_path = Path(args.task)
        self.task = self.task_path.read_text(encoding="utf-8") if self.task_path.exists() else ""
        resource_path = self.root / "environment" / "resources.yaml"
        self.resources_text = resource_path.read_text(encoding="utf-8") if resource_path.exists() else ""
        self.changes = ["created deterministic partial-result checkpoint"]
        self.uncertainty = []

    def path(self, value):
        return resolve(self.root, value)

    def candidate(self, default_name):
        supplied = getattr(self.args, "candidate", None)
        path = Path(supplied) if supplied else self.work / default_name
        return read_json(path)

    def trace(self, extra=None):
        data = {
            "case_id": self.case_id,
            "skill_resource_version": VERSION,
            "python_tested_or_running": sys.version.split()[0],
            "platform": platform.platform(),
            "task_read": bool(self.task),
            "resources_yaml_read": bool(self.resources_text),
            "uncertainty": self.uncertainty,
            "changes": self.changes,
        }
        if extra:
            data.update(extra)
        write_json(self.work / "trace.json", data)

    def finish(self, payload, aliases=None, code=0, trace_extra=None):
        base = {
            "case_id": self.case_id,
            "status": "success" if code == 0 else "partial",
            "sources": [],
            "uncertainty": self.uncertainty,
            "changes": self.changes,
        }
        base.update(payload)
        if self.case_id is None:
            base.pop("case_id", None)
        aliases = {norm(key): value for key, value in (aliases or {}).items()}
        result = bind_contract(self.case, base, aliases)
        errors = []
        if self.case.get("output_contract"):
            errors = contract_errors(self.case["output_contract"], base, aliases, require_fields=False)
            errors += [error for error in contract_errors(self.case["output_contract"], result, aliases) if error not in errors]
        if errors:
            code = 2
            base["status"] = "partial"
            self.uncertainty.extend(error for error in errors if error not in self.uncertainty)
            mark_result_partial(result, self.case.get("output_contract"))
            if isinstance(result.get("failures"), list):
                result["failures"].extend(error for error in errors if error not in result["failures"])
            if isinstance(result.get("uncertainty"), list):
                result["uncertainty"] = self.uncertainty
        write_json(self.output / "result.json", result)
        self.trace(trace_extra)
        print(json.dumps({"status": base["status"], "exit_code": code,
                          "result": str(self.output / "result.json")}, ensure_ascii=False))
        raise SystemExit(code)


def add_arguments(parser):
    parser.add_argument("--case", default="/input/case.json")
    parser.add_argument("--task", default="/input/task.md")
    parser.add_argument("--input-dir", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--candidate")
    return parser
