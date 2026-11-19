#!/usr/bin/env python3
"""Content-addressed, non-destructive library intake. Python >=3.9 stdlib only."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile

VERSION = "1.0.0"


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path):
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def relative_target(value):
    target = PurePosixPath(value)
    if target.is_absolute() or not target.parts or any(part in (".", "..") for part in target.parts):
        raise ValueError("target must be a normalized relative path: " + value)
    return str(target)


def append_journal(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def journal_records(path):
    if not Path(path).exists():
        return []
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def contract_properties(contract):
    if not isinstance(contract, dict):
        return {}
    if isinstance(contract.get("properties"), dict):
        return contract["properties"]
    fields = contract.get("fields")
    if isinstance(fields, list):
        result = {}
        for field in fields:
            if isinstance(field, str):
                result[field] = {}
            elif isinstance(field, dict) and isinstance(field.get("name"), str):
                result[field["name"]] = field
        return result
    if isinstance(fields, dict):
        return fields
    reserved = {"title", "description", "type", "required", "additionalProperties", "examples"}
    return {key: value for key, value in contract.items()
            if key not in reserved and isinstance(value, (dict, str))}


def empty_value(spec):
    kind = spec.get("type") if isinstance(spec, dict) else spec
    if isinstance(kind, list):
        kind = next((item for item in kind if item != "null"), "null")
    return {"array": [], "object": {}, "string": "", "boolean": False,
            "number": 0, "integer": 0}.get(kind, None)


def write_contract_result(case_path, output, payload):
    if not case_path:
        return
    case = load(case_path)
    properties = contract_properties(case.get("output_contract"))
    result = {"case_id": case.get("case_id", case.get("id", "unknown"))}
    for name, spec in properties.items():
        if name == "case_id":
            result[name] = result["case_id"]
        elif name in payload:
            result[name] = payload[name]
        else:
            result[name] = empty_value(spec)
    dump(Path(output) / "result.json", result)


def base_payload(status, changes, uncertainty):
    return {
        "status": status,
        "sources": changes.get("sources", []),
        "uncertainty": uncertainty,
        "changes": changes.get("operations", changes if isinstance(changes, list) else []),
        "change_records": changes.get("operations", []) if isinstance(changes, dict) else changes,
    }


def inventory(source, denied):
    entries, failures = [], []
    source = Path(source)
    if not source.is_dir():
        raise ValueError("source is not a directory")
    for path in sorted(source.rglob("*"), key=lambda item: str(item.relative_to(source))):
        rel = path.relative_to(source).as_posix()
        if path.is_symlink():
            failures.append({"path": rel, "stage": "inventory", "reason": "symlink_not_followed"})
            continue
        if not path.is_file():
            continue
        stat = path.stat()
        entry = {"path": rel, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                 "mode": oct(stat.st_mode & 0o777), "source": rel}
        if rel in denied or not os.access(str(path), os.R_OK):
            entry["status"] = "permission_denied_simulated" if rel in denied else "permission_denied"
            failures.append({"path": rel, "stage": "inventory", "reason": entry["status"]})
        else:
            try:
                entry["sha256"] = sha256(path)
                entry["status"] = "readable"
            except OSError as error:
                entry["status"] = "read_failed"
                failures.append({"path": rel, "stage": "hash", "reason": type(error).__name__})
        entries.append(entry)
    return entries, failures


def duplicate_groups(entries):
    groups = {}
    for entry in entries:
        if entry.get("sha256"):
            groups.setdefault(entry["sha256"], []).append(entry["path"])
    return [{"sha256": digest, "aliases": sorted(paths), "relation": "byte_identical"}
            for digest, paths in sorted(groups.items()) if len(paths) > 1]


def version_candidates(entries, rules):
    by_path = {entry["path"]: entry for entry in entries}
    file_rules = {item["source"]: item for item in rules.get("files", [])}
    groups = {}
    for path, rule in file_rules.items():
        if path not in by_path or not by_path[path].get("sha256"):
            continue
        keys = []
        if rule.get("version_group"):
            keys.append("declared-group:" + str(rule["version_group"]))
        metadata = rule.get("metadata", {})
        if metadata.get("doi"):
            keys.append("doi:" + str(metadata["doi"]).casefold())
        for key in keys:
            groups.setdefault(key, []).append(path)
    candidates = []
    for key, paths in sorted(groups.items()):
        for index, left in enumerate(sorted(paths)):
            for right in sorted(paths)[index + 1:]:
                same = by_path[left].get("sha256") == by_path[right].get("sha256")
                if not same:
                    candidates.append({"from": left, "to": right, "status": "candidate",
                                       "evidence": key + "; hashes differ; no replacement assertion"})
    return candidates


def make_plan(args):
    source = Path(args.source)
    rules = load(args.rules) if args.rules else {}
    entries, failures = inventory(source, set(args.simulate_denied or []))
    file_rules = {item["source"]: item for item in rules.get("files", [])}
    target_claims = {}
    operations = []
    for entry in entries:
        if not entry.get("sha256"):
            continue
        rel = entry["path"]
        rule = file_rules.get(rel, {})
        if rules.get("only_configured") and rel not in file_rules:
            failures.append({"path": rel, "stage": "plan", "reason": "not_configured"})
            continue
        target = relative_target(rule.get("target", rel))
        claimed = target_claims.setdefault(target, [])
        claimed.append((rel, entry["sha256"]))
        operations.append({"op_id": hashlib.sha256((rel + "\0" + target).encode()).hexdigest()[:16],
                           "action": "copy", "source": rel, "target": target,
                           "source_sha256": entry["sha256"], "metadata": rule.get("metadata", {}),
                           "metadata_sources": rule.get("metadata_sources", {}),
                           "source_mode": entry["mode"]})
    conflicts = []
    for target, claims in sorted(target_claims.items()):
        if len({digest for _, digest in claims}) > 1:
            conflicts.append({"target": target, "claims": [{"source": p, "sha256": h} for p, h in claims],
                              "reason": "same_target_different_content"})
    conflict_targets = {item["target"] for item in conflicts}
    canonical_by_hash = {}
    for entry in sorted(entries, key=lambda item: item["path"]):
        if entry.get("sha256"):
            canonical_by_hash.setdefault(entry["sha256"], entry["path"])
    for operation in operations:
        canonical = canonical_by_hash[operation["source_sha256"]]
        if canonical != operation["source"]:
            operation["status"] = "duplicate_alias"
            operation["duplicate_of"] = canonical
            operation["requested_target"] = operation.pop("target")
        elif operation["target"] in conflict_targets:
            operation["status"] = "conflict"
        else:
            operation["status"] = "planned"
    relationships = version_candidates(entries, rules)
    for relation in rules.get("version_relations", []):
        status = relation.get("status", "candidate")
        relationships.append({"from": relation.get("from"), "to": relation.get("to"),
                              "status": "confirmed" if status == "confirmed" and relation.get("evidence") else "candidate",
                              "evidence": relation.get("evidence", "administrative evidence missing")})
    plan_material = json.dumps({"entries": entries, "operations": operations, "rules": rules,
                                "tool": VERSION}, ensure_ascii=False, sort_keys=True).encode()
    plan = {"tool_version": VERSION, "plan_id": hashlib.sha256(plan_material).hexdigest(),
            "source_root": str(source), "library_root": str(args.library), "dry_run": True,
            "inventory": entries, "operations": operations, "duplicates": duplicate_groups(entries),
            "version_candidates": relationships, "conflicts": conflicts, "failures": failures}
    dump(Path(args.work) / "plan.json", plan)
    report = {"status": "partial" if failures or conflicts else "planned", "plan_id": plan["plan_id"],
              "inventory_count": len(entries), "planned_count": sum(op["status"] == "planned" for op in operations),
              "conflicts": conflicts, "failures": failures, "duplicates": plan["duplicates"],
              "version_candidates": relationships, "operations": operations,
              "sources": [entry["path"] for entry in entries], "uncertainty": [
                  "Candidate versions are not replacements without explicit evidence.",
                  "Missing metadata remains absent; no value is inferred from a filename."],
              "change_records": []}
    dump(Path(args.output) / "intake_plan_report.json", report)
    write_contract_result(args.case, args.output, {**report, "library": operations, "metadata": operations,
                                                    "versions": relationships, "results": operations})
    print(json.dumps({"status": report["status"], "plan_id": plan["plan_id"],
                      "planned": report["planned_count"], "conflicts": len(conflicts)}, ensure_ascii=False))
    return 0


def apply_plan(args):
    plan = load(args.plan)
    journal = Path(args.journal)
    previous = journal_records(journal)
    committed = {(r.get("plan_id"), r.get("op_id")) for r in previous if r.get("event") == "committed"}
    source_root, library_root = Path(plan["source_root"]), Path(plan["library_root"])
    changes, failures = [], list(plan.get("failures", []))
    for op in plan["operations"]:
        record = {"plan_id": plan["plan_id"], "op_id": op["op_id"], "source": op["source"],
                  "target": op.get("target", op.get("requested_target")), "sha256": op["source_sha256"]}
        if op["status"] == "duplicate_alias":
            changes.append({**record, "event": "duplicate_alias", "canonical_source": op["duplicate_of"]})
            continue
        if op["status"] != "planned":
            changes.append({**record, "event": "conflict_skipped"})
            continue
        source = source_root / Path(op["source"])
        target = library_root / Path(op["target"])
        key = (plan["plan_id"], op["op_id"])
        try:
            if sha256(source) != op["source_sha256"]:
                raise ValueError("source_changed_after_plan")
            if target.exists():
                if target.is_symlink() or not target.is_file():
                    raise ValueError("unsafe_existing_target")
                if sha256(target) == op["source_sha256"]:
                    if key not in committed:
                        append_journal(journal, {**record, "event": "already_verified"})
                    changes.append({**record, "event": "already_verified"})
                    continue
                raise ValueError("target_hash_conflict")
            if key in committed:
                raise ValueError("journal_target_missing")
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".intake-", dir=str(target.parent))
            try:
                with os.fdopen(fd, "wb") as out, source.open("rb") as inp:
                    shutil.copyfileobj(inp, out, 1024 * 1024)
                    out.flush()
                    os.fsync(out.fileno())
                shutil.copystat(str(source), temporary)
                if sha256(temporary) != op["source_sha256"]:
                    raise ValueError("temporary_hash_mismatch")
                if target.exists():
                    raise ValueError("target_appeared_during_copy")
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            append_journal(journal, {**record, "event": "committed", "action": "copy",
                                     "source_mode": op["source_mode"], "target_path": str(target),
                                     "library_root": str(library_root)})
            changes.append({**record, "event": "committed"})
        except (OSError, ValueError) as error:
            reason = str(error) if isinstance(error, ValueError) else type(error).__name__
            failures.append({"stage": "apply", "path": op["source"], "target": op["target"], "reason": reason})
            append_journal(journal, {**record, "event": "failed", "reason": reason})
            changes.append({**record, "event": "failed", "reason": reason})
    accounted = len(changes) + len([f for f in failures if f.get("stage") == "inventory"])
    report = {"status": "partial" if failures or plan["conflicts"] else "completed", "plan_id": plan["plan_id"],
              "inventory_count": len(plan["inventory"]), "accounted_inputs": accounted,
              "accounting_conserved": accounted == len(plan["inventory"]),
              "change_records": changes, "changes": changes, "operations": changes,
              "failures": failures, "conflicts": plan["conflicts"], "duplicates": plan["duplicates"],
              "versions": plan["version_candidates"], "version_candidates": plan["version_candidates"],
              "library": changes, "sources": [entry["path"] for entry in plan["inventory"]],
              "uncertainty": ["Unreadable and conflicting inputs are not counted as completed.",
                              "Version candidates remain unconfirmed without evidence."]}
    dump(Path(args.output) / "intake_report.json", report)
    dump(Path(args.output) / "catalog.json", {"plan_id": plan["plan_id"], "inventory": plan["inventory"],
                                               "operations": plan["operations"]})
    write_contract_result(args.case, args.output, report)
    print(json.dumps({"status": report["status"], "changes": len(changes),
                      "failures": len(failures)}, ensure_ascii=False))
    return 2 if failures or plan["conflicts"] else 0


def rollback(args):
    records = journal_records(args.journal)
    selected = [r for r in records if r.get("event") == "committed" and
                (not args.plan_id or r.get("plan_id") == args.plan_id)]
    undone, failures = [], []
    for record in reversed(selected):
        target = Path(record.get("target_path", ""))
        try:
            if not target.exists():
                undone.append({**record, "event": "already_absent"})
            elif target.is_symlink() or not target.is_file() or sha256(target) != record["sha256"]:
                raise ValueError("target_changed_refuse_to_delete")
            else:
                target.unlink()
                undone.append({**record, "event": "rolled_back"})
            append_journal(args.journal, {**record, "event": undone[-1]["event"]})
        except (OSError, ValueError) as error:
            reason = str(error) if isinstance(error, ValueError) else type(error).__name__
            failures.append({"target": str(target), "stage": "rollback", "reason": reason})
            append_journal(args.journal, {**record, "event": "rollback_failed", "reason": reason})
    report = {"status": "partial" if failures else "rolled_back", "change_records": undone,
              "changes": undone, "failures": failures, "uncertainty": [
                  "Only unchanged, journal-created copies are removed; modified targets are preserved."]}
    dump(Path(args.output) / "rollback_report.json", report)
    write_contract_result(args.case, args.output, report)
    print(json.dumps({"status": report["status"], "rolled_back": len(undone), "failures": len(failures)}))
    return 2 if failures else 0


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--version", action="version", version=VERSION)
    commands = root.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="Dry-run only; never mutates the library")
    for name in ("source", "library", "work", "output"):
        plan.add_argument("--" + name, required=True)
    plan.add_argument("--rules")
    plan.add_argument("--case")
    plan.add_argument("--task", help="Read and hash the real task beside the plan")
    plan.add_argument("--simulate-denied", action="append", default=[])
    apply_cmd = commands.add_parser("apply")
    apply_cmd.add_argument("--plan", required=True)
    apply_cmd.add_argument("--journal", required=True)
    apply_cmd.add_argument("--output", required=True)
    apply_cmd.add_argument("--case")
    back = commands.add_parser("rollback")
    back.add_argument("--journal", required=True)
    back.add_argument("--output", required=True)
    back.add_argument("--plan-id")
    back.add_argument("--case")
    return root


def main():
    args = parser().parse_args()
    try:
        if getattr(args, "task", None):
            task = Path(args.task)
            with task.open(encoding="utf-8") as handle:
                handle.read()
        Path(args.work).mkdir(parents=True, exist_ok=True) if hasattr(args, "work") else None
        Path(args.output).mkdir(parents=True, exist_ok=True)
        return {"plan": make_plan, "apply": apply_plan, "rollback": rollback}[args.command](args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
