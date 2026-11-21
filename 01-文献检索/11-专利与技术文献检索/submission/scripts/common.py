#!/usr/bin/env python3
"""Shared I/O only; scientific algorithms live in each topic's run.py."""
import argparse
import hashlib
import json
import platform
import re
import sys
import unicodedata
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MISSING = object()


def read_json(path):
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def norm_text(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())


def norm_title(value):
    return "".join(c for c in norm_text(value).casefold() if c.isalnum())


def norm_doi(value):
    value = norm_text(value).casefold()
    value = re.sub(r"^(?:doi\s*:\s*|https?://(?:dx\.)?doi\.org/)", "", value)
    return value.rstrip(" .;,)") if value.startswith("10.") else ""


def first_author(record):
    authors = record.get("authors", record.get("author", []))
    if isinstance(authors, str):
        return norm_text(authors.split(";")[0].split(",")[0]).casefold()
    if isinstance(authors, list) and authors:
        author = authors[0]
        if isinstance(author, dict):
            author = author.get("family", author.get("name", ""))
        return norm_text(author).casefold()
    return ""


def record_key(record, index=0):
    doi = norm_doi(record.get("doi", ""))
    if doi:
        return "doi:" + doi
    explicit = norm_text(record.get("id", record.get("record_id", "")))
    if explicit:
        return "id:" + explicit
    basis = "|".join([norm_title(record.get("title", "")), first_author(record), str(record.get("year", ""))])
    return "record:" + hashlib.sha256((basis + "|" + str(index)).encode("utf-8")).hexdigest()[:16]


def unique_keys(records):
    keys, seen = [], {}
    for index, record in enumerate(records):
        base = record_key(record, index)
        seen[base] = seen.get(base, 0) + 1
        keys.append(base if seen[base] == 1 else "%s~%d" % (base, seen[base]))
    return keys


def dedupe(records):
    """Cluster exact DOI or corroborated metadata, never across conflicting DOIs."""
    keys = unique_keys(records)
    parents = list(range(len(records)))
    cluster_dois = {i: set() for i in range(len(records))}

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    def join(i, j):
        a, b = root(i), root(j)
        if a != b:
            low, high = min(a, b), max(a, b)
            parents[high] = low
            cluster_dois[low].update(cluster_dois[high])
            del cluster_dois[high]

    reasons = {}
    dois = {}
    triples = {}
    title_only = {}
    blocked = set()
    blocked_conflicts = []
    for i, record in enumerate(records):
        doi = norm_doi(record.get("doi", ""))
        title = norm_title(record.get("title", ""))
        author = first_author(record)
        year = norm_text(record.get("year", ""))
        if doi:
            if doi in dois:
                join(i, dois[doi])
                reasons[(dois[doi], i)] = "same normalized DOI"
            else:
                dois[doi] = i
            cluster_dois[root(i)].add(doi)
        if title and author and year:
            triple = (title, author, year)
            if triple in triples:
                previous = triples[triple]
                current_root, previous_root = root(i), root(previous)
                combined_dois = cluster_dois[current_root] | cluster_dois[previous_root]
                if current_root != previous_root and len(combined_dois) > 1:
                    pair = (min(previous, i), max(previous, i))
                    blocked.add(pair)
                    blocked_conflicts.append({"members": [keys[previous], keys[i]], "conflicting_fields": [{"field": "doi", "values": sorted(combined_dois)}], "resolution": "not_merged_pending_identity_review", "status": "needs_identity_review"})
                else:
                    join(i, previous)
                    reasons[(previous, i)] = "same normalized title, first author and year"
            else:
                triples[triple] = i
        if title:
            title_only.setdefault(title, []).append(i)
    groups = {}
    for i in range(len(records)):
        groups.setdefault(root(i), []).append(i)
    clusters, conflicts = [], []
    for members in sorted(groups.values(), key=lambda m: min(m)):
        master = min(members)
        member_keys = [keys[i] for i in members]
        evidence = ["%s -> %s: %s" % (keys[j], keys[i], why) for (i, j), why in sorted(reasons.items()) if i in members and j in members]
        fields = ["title", "year", "doi"]
        differences = []
        for field in fields:
            values = sorted({norm_text(records[i].get(field, "")) for i in members if norm_text(records[i].get(field, ""))})
            if len(values) > 1:
                differences.append({"field": field, "values": values})
        if differences:
            conflicts.append({"members": member_keys, "conflicting_fields": differences, "resolution": "source_conflict_retained"})
        clusters.append({"master": keys[master], "members": member_keys, "evidence": evidence, "merged": len(members) > 1})
    conflicts.extend(blocked_conflicts)
    possible = []
    for members in title_only.values():
        if len(members) > 1 and len({root(i) for i in members}) > 1:
            has_doi_conflict = any((min(i, j), max(i, j)) in blocked for position, i in enumerate(members) for j in members[position + 1:])
            reason = "same title and corroborating author/year but conflicting explicit DOIs; metadata merge blocked" if has_doi_conflict else "same title but insufficient corroborating author/year metadata"
            possible.append({"candidates": [keys[i] for i in members], "reason": reason, "status": "needs_identity_review"})
    return keys, clusters, possible, conflicts


def records_at(data, names):
    if isinstance(data, dict):
        for name in names:
            value = data.get(name)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                result = []
                for key, item in value.items():
                    if isinstance(item, dict):
                        item = dict(item)
                        item.setdefault("id", key)
                        result.append(item)
                return result
    return []


def inventory(root):
    root = Path(root)
    result = []
    if not root.exists():
        return [{"path": str(root), "status": "missing_input_root"}]
    paths = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink())
    for path in paths:
        try:
            raw = path.read_bytes()
            result.append({"path": str(path.relative_to(root)) if root.is_dir() else path.name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "status": "inventoried_not_interpreted"})
        except OSError as exc:
            result.append({"path": str(path), "status": "unreadable", "error": str(exc)})
    return result


def base_payload(case, task, inv):
    return {
        "case_id": case.get("case_id", ""),
        "status": "partial",
        "status_detail": {"completed": [], "incomplete": ["domain processing"]},
        "sources": inv,
        "uncertainties": [],
        "errors": [],
        "change_records": ["Initialized from actual case.json, task.md and input inventory; no live source queried."],
        "input_fingerprints": {"case": digest(case), "task_md": hashlib.sha256(task.encode("utf-8")).hexdigest()},
        "run_metadata": {"python": platform.python_version(), "network": "not_used"},
    }


COMMON_ALIASES = {
    "run_status": "status", "state": "status", "limitations": "uncertainties",
    "uncertainty": "uncertainties", "provenance": "sources", "evidence": "sources",
    "changes": "change_records", "change_log": "change_records", "main_results": "primary_results",
}


def properties_of(contract):
    if isinstance(contract, list):
        return {str(x): {} for x in contract}, []
    if not isinstance(contract, dict):
        return {}, []
    if isinstance(contract.get("properties"), dict):
        return contract["properties"], contract.get("required", [])
    if isinstance(contract.get("fields"), list):
        answer = {}
        for item in contract["fields"]:
            if isinstance(item, str):
                answer[item] = {}
            elif isinstance(item, dict) and item.get("name"):
                answer[item["name"]] = {k: v for k, v in item.items() if k != "name"}
        return answer, contract.get("required", [])
    structural = {"type", "description", "title", "$schema", "additionalProperties", "required"}
    return {k: v for k, v in contract.items() if k not in structural}, contract.get("required", [])


def schema_type(spec, value=MISSING):
    if isinstance(spec, str) and spec in {"string", "array", "object", "number", "integer", "boolean", "null"}:
        return spec
    if isinstance(spec, dict) and spec.get("type"):
        return spec["type"]
    if value is not MISSING:
        if isinstance(value, list):
            return "array"
        if isinstance(value, dict):
            return "object"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "number"
        if isinstance(value, str):
            return "string"
    return "any"


def lookup(container, name, aliases):
    if isinstance(container, dict):
        if name in container:
            return container[name]
        candidates = dict(COMMON_ALIASES)
        candidates.update(aliases)
        target = candidates.get(name)
        if target in container:
            return container[target]
    return MISSING


def type_ok(value, expected):
    if expected in ("any", None):
        return True
    choices = expected if isinstance(expected, list) else [expected]
    checks = {
        "string": lambda v: isinstance(v, str), "array": lambda v: isinstance(v, list),
        "object": lambda v: isinstance(v, dict), "boolean": lambda v: isinstance(v, bool),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "null": lambda v: v is None,
    }
    return any(checks.get(choice, lambda v: True)(value) for choice in choices)


def default_for(name, expected):
    if expected == "array":
        return []
    if expected == "object":
        return {}
    if expected == "boolean":
        return False
    if name in {"status", "run_status", "state"}:
        return "not_evaluated"
    if expected == "string":
        return ""
    if name not in {"count", "total", "input_count", "output_count"}:
        return None
    return 0


def bind_value(spec, value, name, warnings, path, required=False):
    expected = schema_type(spec, value)
    if value is MISSING:
        warnings.append(("missing_required:" if required else "unmapped:") + path)
        value = default_for(name, expected)
    if expected == "object" and isinstance(spec, dict):
        if not type_ok(value, expected):
            warnings.append("type_mismatch:%s" % path)
        local = value if isinstance(value, dict) else {}
        properties = spec.get("properties", {})
        result = local
        if properties:
            required_names = spec.get("required", [])
            required_names = required_names if isinstance(required_names, list) else []
            result = {key: bind_value(child, lookup(local, key, {}), key, warnings, path + "." + key, key in required_names) for key, child in properties.items()}
    elif expected == "array":
        values = value if isinstance(value, list) else []
        if not isinstance(value, list):
            warnings.append("type_mismatch:%s" % path)
        item_spec = spec.get("items", {}) if isinstance(spec, dict) else {}
        result = [bind_value(item_spec, item, name, warnings, "%s[%d]" % (path, i)) for i, item in enumerate(values)] if isinstance(item_spec, dict) and item_spec else values
    else:
        if not type_ok(value, expected):
            warnings.append("type_mismatch:%s" % path)
        result = value
    if isinstance(spec, dict) and "enum" in spec and isinstance(spec["enum"], list) and result not in spec["enum"]:
        warnings.append("enum_mismatch:%s" % path)
    return result


def render_result(case, payload):
    contract = case.get("output_contract", {})
    aliases = payload.get("_aliases", {})
    properties, required = properties_of(contract)
    required = required if isinstance(required, list) else []
    result = {"case_id": case.get("case_id", "")}
    warnings = []
    for name, spec in properties.items():
        value = result[name] if name == "case_id" else lookup(payload, name, aliases)
        result[name] = bind_value(spec, value, name, warnings, name, name in required)
    validation = {"mapped_fields": sorted(result), "warnings": warnings, "contract_detected": bool(properties)}
    return result, validation


def run(algorithm, handler):
    parser = argparse.ArgumentParser(description=algorithm)
    parser.add_argument("--case", default="/input/case.json")
    parser.add_argument("--task", default="/input/task.md")
    parser.add_argument("--data", default=None, help="Agent-extracted authorized JSON; defaults to /app/work/analysis_input.json when present")
    parser.add_argument("--input-root", default="/input")
    parser.add_argument("--resources", default="environment/resources.yaml")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    args = parser.parse_args()
    work, output = Path(args.work), Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    try:
        case = read_json(args.case)
        task = Path(args.task).read_text(encoding="utf-8-sig")
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "input_error", "error": str(exc)}, ensure_ascii=False))
        return 2
    inv = inventory(args.input_root)
    payload = base_payload(case, task, inv)
    handler_case = dict(case)
    handler_case["_runtime_work"] = str(work)
    handler_case["_runtime_output"] = str(output)
    initial, initial_validation = render_result(case, payload)
    write_json(output / "result.json", initial)
    resource_path = Path(args.resources)
    resources_status = "found" if resource_path.is_file() else "not_found_at_declared_path"
    payload["run_metadata"]["resources_file"] = resources_status
    try:
        data_path = Path(args.data) if args.data else work.parent / "work" / "analysis_input.json"
        if not args.data and data_path == Path("/app/work/analysis_input.json") and not data_path.exists():
            data = case.get("analysis_input", case)
            data_origin = "case_inline"
        elif data_path.exists():
            data = read_json(data_path)
            data_origin = str(data_path)
        else:
            data = case.get("analysis_input", case)
            data_origin = "case_inline"
        produced = handler(handler_case, data, task, inv)
        if not isinstance(produced, dict):
            raise ValueError("domain handler did not return an object")
        payload.update(produced)
        payload["run_metadata"]["data_origin"] = data_origin
    except Exception as exc:
        payload["status"] = "partial"
        payload["errors"].append({"type": type(exc).__name__, "message": str(exc)})
        payload["uncertainties"].append("Domain processing stopped; only the honest partial result is available.")
        payload["status_detail"] = {"completed": ["input inventory and partial result"], "incomplete": ["domain processing"]}
    result, validation = render_result(case, payload)
    write_json(output / "result.json", result)
    write_json(work / "analysis.json", {k: v for k, v in payload.items() if k != "_aliases"})
    write_json(work / "contract_validation.json", validation)
    write_json(work / "run_log.json", {"algorithm": algorithm, "initial_contract_check": initial_validation, "final_contract_check": validation, "python": platform.python_version(), "resources_file": resources_status})
    print(json.dumps({"status": payload.get("status", "partial"), "case_id": case.get("case_id", ""), "result": str(output / "result.json"), "analysis": str(work / "analysis.json"), "warnings": len(validation["warnings"]) + len(payload.get("errors", []))}, ensure_ascii=False))
    return 0 if not payload.get("errors") and not validation["warnings"] else 1
