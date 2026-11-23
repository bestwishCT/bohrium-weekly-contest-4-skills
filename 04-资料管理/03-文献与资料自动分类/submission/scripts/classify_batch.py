#!/usr/bin/env python3
"""Evidence-weighted multi-label classification and transactional file organization."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

VERSION = "1.0.0"
META_FIELDS = ("title", "authors", "year", "doi", "journal", "keywords", "abstract")


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def native_text(path):
    if path.suffix.casefold() in {".txt", ".md", ".html", ".htm", ".xml", ".json"}:
        return path.read_text(encoding="utf-8"), "stdlib-utf8-or-markup"
    if path.suffix.casefold() == ".docx":
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
            return "\n".join(t.strip() for t in root.itertext() if t.strip()), "stdlib-docx-xml"
    raise ValueError("unsupported_without_declared_extraction:" + path.suffix)


def normalize(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return str(value).strip() if value is not None else None


def compare_value(value):
    if isinstance(value, list):
        return [str(item).casefold() for item in value]
    return str(value).casefold() if value is not None else None


def extract_metadata(text):
    values, sources = {}, {}
    lines = text.splitlines()
    for number, line in enumerate(lines, 1):
        match = re.match(r"^\s*(Title|Authors?|Year|DOI|Journal|Keywords?|Abstract)\s*:\s*(.+?)\s*$", line, re.I)
        if not match:
            continue
        field = match.group(1).casefold().rstrip("s") if match.group(1).casefold() != "keywords" else "keywords"
        field = {"author": "authors", "keyword": "keywords"}.get(field, field)
        value = match.group(2).strip()
        if field in {"authors", "keywords"}:
            value = [item.strip() for item in re.split(r";|,(?!\d)", value) if item.strip()]
        values[field] = value
        sources[field] = "body#line=%d" % number
    return values, sources


def region_texts(text, metadata):
    result = {"title": str(metadata.get("title") or ""),
              "keywords": " ".join(metadata.get("keywords") or []) if isinstance(metadata.get("keywords"), list) else str(metadata.get("keywords") or ""),
              "abstract": str(metadata.get("abstract") or ""), "body": text}
    return result


def term_pattern(term):
    """ASCII terms are whole words; CJK aliases use their literal declared form."""
    if re.search(r"[A-Za-z0-9_]", term):
        return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])", re.I)
    return re.compile(re.escape(term), re.I)


def locate(text, phrase):
    pattern = term_pattern(phrase)
    for number, line in enumerate(text.splitlines(), 1):
        if pattern.search(line):
            return "body#line=%d" % number, line.strip()
    return "body#offset=unknown", None


def category_evidence(category, regions, full_text, synonyms):
    evidence = []
    weights = {"title": 5, "keywords": 4, "abstract": 3, "body": 1}
    for declared_term in category.get("terms", []):
        forms = [declared_term]
        for alias, canonical in synonyms.items():
            if canonical.casefold() == declared_term.casefold() and alias.casefold() != declared_term.casefold():
                forms.append(alias)
        best = None
        for form in forms:
            for region, text in regions.items():
                if term_pattern(form).search(text):
                    locator, quote = locate(full_text, form)
                    candidate = {"term": declared_term, "matched_form": form, "region": region,
                                 "weight": weights[region], "locator": locator, "quote": quote}
                    if best is None or candidate["weight"] > best["weight"]:
                        best = candidate
        if best:
            evidence.append(best)
    return evidence


def safe_component(value, max_bytes):
    value = unicodedata.normalize("NFC", str(value))
    value = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .") or "unknown"
    raw = value.encode("utf-8")
    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
        value = raw.decode("utf-8", "ignore").rstrip(" .")
    return value


def target_for(primary, metadata, template, digest_value, suffix=".txt", max_bytes=180):
    missing = []

    def replace(match):
        field = match.group(1)
        value = metadata.get(field)
        if isinstance(value, list):
            value = value[0] if value else None
        if value is None or str(value).strip() == "":
            missing.append(field)
            return "unknown"
        return str(value)

    stem = re.sub(r"\{([a-zA-Z_]+)\}", replace, template)
    if missing and any("{%s}" % field in template for field in missing):
        stem += "--" + digest_value[:8]
    stem = safe_component(stem, max_bytes)
    relative = PurePosixPath(primary) / (stem + suffix)
    if relative.is_absolute() or any(part in {".", ".."} for part in relative.parts):
        raise ValueError("unsafe target path")
    return relative.as_posix(), missing


def contract_properties(contract):
    if not isinstance(contract, dict):
        return {}
    if isinstance(contract.get("properties"), dict):
        return contract["properties"]
    fields = contract.get("fields")
    if isinstance(fields, list):
        return {field if isinstance(field, str) else field.get("name"): field if isinstance(field, dict) else {}
                for field in fields if isinstance(field, str) or isinstance(field, dict) and field.get("name")}
    if isinstance(fields, dict):
        return fields
    reserved = {"type", "required", "title", "description", "additionalProperties", "examples"}
    return {key: value for key, value in contract.items() if key not in reserved and isinstance(value, (dict, str))}


def validate_contract(value, schema, path="result"):
    """Validate the supported JSON-schema contract recursively, including nested items."""
    if not isinstance(schema, dict):
        return
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(path + ": value is not in enum")
    kind = schema.get("type")
    types = kind if isinstance(kind, list) else [kind] if kind else []
    checks = {"object": dict, "array": list, "string": str, "boolean": bool}
    valid = not types
    for item in types:
        if item == "null":
            valid |= value is None
        elif item == "integer":
            valid |= isinstance(value, int) and not isinstance(value, bool)
        elif item == "number":
            valid |= isinstance(value, (int, float)) and not isinstance(value, bool)
        elif item in checks:
            valid |= isinstance(value, checks[item]) and (item != "string" or not isinstance(value, bool))
    if not valid:
        raise ValueError(path + ": expected " + str(kind))
    if isinstance(value, dict):
        required = schema.get("required", [])
        if isinstance(schema.get("fields"), list):
            required = list(required) + [f["name"] for f in schema["fields"]
                                         if isinstance(f, dict) and f.get("required") and f.get("name")]
        for name in required:
            if name not in value:
                raise ValueError(path + "." + name + ": required field missing")
        for name, child in contract_properties(schema).items():
            if name in value:
                validate_contract(value[name], child if isinstance(child, dict) else {"type": child},
                                  path + "." + name)
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            validate_contract(item, schema["items"], "%s[%d]" % (path, index))


def write_result(case_path, output, payload):
    if not case_path:
        return
    case = load(case_path)
    contract = case.get("output_contract", {})
    path = Path(output) / "result.json"
    result = load(path) if path.exists() else {}
    result["case_id"] = case.get("case_id", case.get("id", "unknown"))
    # Merge only supplied fields: apply/rollback must not erase earlier canonical results.
    for name, spec in contract_properties(contract).items():
        if name in result:
            continue
        if name in payload:
            result[name] = payload[name]
        else:
            kind = spec.get("type") if isinstance(spec, dict) else spec
            result[name] = [] if kind == "array" else {} if kind == "object" else "" if kind == "string" else None
    for name in contract_properties(contract):
        if name in payload:
            result[name] = payload[name]
    validate_contract(result, contract if isinstance(contract, dict) else {})
    dump(path, result)


def version_relations(records, relations, source=None):
    output = []
    for record in records:
        for other in records:
            if record["path"] >= other["path"]:
                continue
            same_hash = record["sha256"] == other["sha256"]
            doi = record["metadata"].get("doi")
            if same_hash:
                relation, evidence = "byte_duplicate", "identical SHA-256"
            elif doi and doi == other["metadata"].get("doi"):
                relation, evidence = "version_candidate", "same declared DOI but different SHA-256"
            elif record["metadata"].get("title") and all(record["metadata"].get(key) == other["metadata"].get(key)
                                                         for key in ("title", "authors", "year")):
                relation, evidence = "version_candidate", "same title/authors/year but different SHA-256"
            else:
                continue
            output.append({"from": record["path"], "to": other["path"], "status": relation, "evidence": evidence})
    known = {record["path"] for record in records}
    for relation in relations:
        evidence = relation.get("evidence")
        locator = relation.get("locator")
        if isinstance(evidence, dict):
            locator = locator or evidence.get("locator")
        elif isinstance(evidence, str):
            match = re.search(r"body#line=\d+", evidence)
            locator = locator or (match.group(0) if match else None)
        if relation.get("from") not in known or relation.get("to") not in known or not locator:
            continue
        match = re.fullmatch(r"body#line=(\d+)", locator)
        try:
            text, _ = native_text(Path(source) / relation["from"])
        except (OSError, ValueError, UnicodeError):
            continue
        if not match or not (1 <= int(match.group(1)) <= len(text.splitlines())):
            continue
        output.append({"from": relation["from"], "to": relation["to"],
                       "status": relation.get("status", "version_candidate"),
                       "evidence": evidence, "locator": locator})
    return output


def plan(args):
    source = Path(args.library)
    if not source.is_dir():
        raise ValueError("library is not a directory")
    rules = load(args.rules)
    declarations = {item["path"]: item for item in rules.get("documents", [])}
    denied = set(args.simulate_denied or [])
    inventory, failures, records, operations = [], [], [], []
    synonyms = rules.get("synonyms", {})
    template = rules.get("naming", {}).get("template", "{year}-{title}")
    max_tags = int(rules.get("naming", {}).get("max_tags", 8))
    for path in sorted(source.rglob("*"), key=lambda p: p.relative_to(source).as_posix()):
        rel = path.relative_to(source).as_posix()
        if path.is_symlink():
            failures.append({"path": rel, "stage": "inventory", "reason": "symlink_not_followed"})
            continue
        if not path.is_file():
            continue
        item = {"path": rel, "size": path.stat().st_size, "mode": oct(path.stat().st_mode & 0o777)}
        inventory.append(item)
        if rel in denied or not os.access(path, os.R_OK):
            failures.append({"path": rel, "stage": "read", "reason": "permission_denied_simulated" if rel in denied else "permission_denied"})
            continue
        try:
            file_hash = sha256(path)
            item["sha256"] = file_hash
            declaration = declarations.get(rel, {})
            if declaration.get("extracted_text"):
                text = Path(declaration["extracted_text"]).read_text(encoding="utf-8")
                parser_name = declaration.get("parser", "declared-external-unverified")
            else:
                text, parser_name = native_text(path)
            parsed, parsed_sources = extract_metadata(text)
            declared_metadata = declaration.get("metadata", {})
            declared_sources = declaration.get("metadata_sources", {})
            metadata, sources, conflicts = {}, {}, []
            for field in set(META_FIELDS) | set(declared_metadata) | set(parsed):
                left, right = parsed.get(field), declared_metadata.get(field)
                if left is not None and right is not None and compare_value(left) != compare_value(right):
                    conflicts.append({"field": field, "candidates": [left, normalize(right)],
                                      "sources": [parsed_sources.get(field), declared_sources.get(field)]})
                    metadata[field] = None
                else:
                    metadata[field] = normalize(left if left is not None else right)
                sources[field] = [value for value in (parsed_sources.get(field), declared_sources.get(field)) if value]
            regions = region_texts(text, metadata)
            labels = []
            for category in rules.get("categories", []):
                evidence = category_evidence(category, regions, text, synonyms)
                minimum = int(category.get("minimum_score", 2))
                score = sum(item["weight"] for item in evidence)
                if evidence and score >= minimum:
                    labels.append({"tag": category["name"], "directory": category.get("directory", category["name"]),
                                   "parent": category.get("parent"), "score_not_confidence": score, "evidence": evidence})
            labels.sort(key=lambda item: (-item["score_not_confidence"], item["tag"]))
            distinct, seen = [], set()
            for label in labels:
                if label["tag"] not in seen:
                    distinct.append(label)
                    seen.add(label["tag"])
            labels = distinct[:max_tags]
            best_score = labels[0]["score_not_confidence"] if labels else 0
            leaders = [label for label in labels if label["score_not_confidence"] == best_score]
            primary = leaders[0]["directory"] if len(leaders) == 1 else None
            record = {"path": rel, "sha256": file_hash, "parser": parser_name, "metadata": metadata,
                      "metadata_sources": sources, "metadata_conflicts": conflicts, "labels": labels,
                      "primary_directory": primary, "status": "review_required" if conflicts or not primary else "classified",
                      "parent_tags": [{"tag": label["parent"], "derived_from": label["tag"],
                                       "basis": "declared taxonomy parent, not a new content match"}
                                      for label in labels if label["parent"]],
                      "review_reason": "metadata_or_classification_conflict" if conflicts else
                                       None if primary else "ambiguous_primary" if len(leaders) > 1 else "insufficient_evidence"}
            if primary and not conflicts:
                target, missing = target_for(primary, metadata, template, file_hash, path.suffix.casefold() or ".bin")
                record["target"] = target
                record["missing_naming_fields"] = missing
                operations.append({"source": rel, "target": target, "source_sha256": file_hash, "status": "planned",
                                   "metadata": metadata, "metadata_sources": sources, "labels": [l["tag"] for l in labels]})
            records.append(record)
        except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile, UnicodeError) as error:
            failures.append({"path": rel, "stage": "parse_or_classify", "reason": str(error)})
    duplicates = {}
    for record in records:
        duplicates.setdefault(record["sha256"], []).append(record["path"])
    duplicate_groups = [{"sha256": key, "aliases": sorted(paths)} for key, paths in duplicates.items() if len(paths) > 1]
    canonical = {key: sorted(paths)[0] for key, paths in duplicates.items()}
    claims = {}
    for operation in operations:
        claims.setdefault(operation["target"], []).append(operation)
    conflicts = []
    for target, items in claims.items():
        if len({item["source_sha256"] for item in items}) > 1:
            conflicts.append({"target": target, "sources": [item["source"] for item in items],
                              "reason": "normalized_name_collision"})
    conflict_targets = {item["target"] for item in conflicts}
    for operation in operations:
        representative = canonical[operation["source_sha256"]]
        if operation["source"] != representative:
            operation["status"] = "duplicate_alias"
            operation["duplicate_of"] = representative
            operation["requested_target"] = operation.pop("target")
        elif operation["target"] in conflict_targets:
            operation["status"] = "conflict"
    relationships = version_relations(records, rules.get("version_relations", []), source)
    plan_data = {"tool_version": VERSION, "source_root": str(source), "library_root": str(args.destination),
                 "inventory": inventory, "records": records, "operations": operations,
                 "duplicates": duplicate_groups, "version_relations": relationships,
                 "conflicts": conflicts, "failures": failures, "dry_run": True, "rules_sha256": sha256(args.rules)}
    plan_data["plan_id"] = sha256(args.rules)[:12] + "-" + hashlib.sha256(json.dumps(
        {"inventory": inventory, "operations": operations}, sort_keys=True).encode()).hexdigest()[:16]
    dump(Path(args.work) / "classification-plan.json", plan_data)
    review_queue = [record["path"] for record in records if record["status"] == "review_required"]
    report = {"status": "blocked" if not inventory else "partial" if failures or conflicts or review_queue else "planned",
              "reason": "empty_inventory" if not inventory else None,
              "plan_id": plan_data["plan_id"],
              "inventory": inventory, "classification": records, "records": records,
              "tags": [{"path": r["path"], "tags": [l["tag"] for l in r["labels"]]} for r in records],
              "operations": operations, "duplicates": duplicate_groups, "versions": relationships,
              "conflicts": conflicts, "failures": failures, "review_queue": review_queue,
              "sources": [i["path"] for i in inventory],
              "uncertainty": ["Scores are rule evidence weights, not probabilities.",
                              "Tied primary classes, conflicts and missing fields require review.",
                              "Same DOI/title with different hashes creates a candidate, not a replacement claim."],
              "change_records": []}
    dump(Path(args.output) / "classification_plan_report.json", report)
    write_result(args.case, args.output, report)
    print(json.dumps({"status": report["status"], "classified": sum(r["status"] == "classified" for r in records),
                      "conflicts": len(conflicts), "dry_run": True}, ensure_ascii=False))
    return 2 if not inventory else 0


def append_journal(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def apply(args):
    data = load(args.plan)
    source_root, destination = Path(data["source_root"]), Path(data["library_root"])
    changes, failures = [], list(data["failures"])
    for operation in data["operations"]:
        base = {"plan_id": data["plan_id"], "source": operation["source"],
                "target": operation.get("target", operation.get("requested_target")), "sha256": operation["source_sha256"]}
        if operation["status"] == "duplicate_alias":
            changes.append({**base, "event": "duplicate_alias", "canonical_source": operation["duplicate_of"]})
            continue
        if operation["status"] != "planned":
            changes.append({**base, "event": "conflict_skipped"})
            continue
        source, target = source_root / operation["source"], destination / operation["target"]
        try:
            if sha256(source) != operation["source_sha256"]:
                raise ValueError("source_changed_after_plan")
            if target.exists():
                if target.is_symlink() or not target.is_file() or sha256(target) != operation["source_sha256"]:
                    raise ValueError("target_conflict_no_overwrite")
                changes.append({**base, "event": "already_verified"})
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".classify-", dir=str(target.parent))
            try:
                with os.fdopen(fd, "wb") as out, source.open("rb") as inp:
                    shutil.copyfileobj(inp, out)
                    out.flush()
                    os.fsync(out.fileno())
                shutil.copystat(str(source), temporary)
                if sha256(temporary) != operation["source_sha256"]:
                    raise ValueError("hash_mismatch")
                if target.exists():
                    raise ValueError("target_appeared")
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            event = {**base, "event": "committed", "action": "copy", "target_path": str(target),
                     "library_root": str(destination)}
            append_journal(args.journal, event)
            changes.append(event)
        except (OSError, ValueError) as error:
            reason = str(error) if isinstance(error, ValueError) else type(error).__name__
            append_journal(args.journal, {**base, "event": "failed", "reason": reason})
            changes.append({**base, "event": "failed", "reason": reason})
            failures.append({"path": operation["source"], "stage": "apply", "reason": reason})
    review_queue = [record["path"] for record in data["records"] if record["status"] == "review_required"]
    operation_paths = {operation["source"] for operation in data["operations"]}
    for record in data["records"]:
        if record["status"] == "classified" and record["path"] not in operation_paths:
            failures.append({"path": record["path"], "stage": "apply", "reason": "missing_operation"})
    accounted_paths = set(operation_paths)
    accounted_paths.update(failure["path"] for failure in failures if failure.get("path"))
    accounted_paths.update(review_queue)
    inventory_paths = {item["path"] for item in data["inventory"]}
    conserved = accounted_paths == inventory_paths
    dispositions = []
    for item in data["inventory"]:
        path = item["path"]
        operation = next((op for op in data["operations"] if op["source"] == path), None)
        failure = next((failure for failure in failures if failure.get("path") == path), None)
        disposition = operation["status"] if operation else "review_required" if path in review_queue else "failed" if failure else "unaccounted"
        dispositions.append({"path": path, "disposition": disposition})
    case_id = load(args.case).get("case_id", load(args.case).get("id", "unknown")) if args.case else "unknown"
    report = {"status": "blocked" if not inventory_paths else "partial" if failures or data["conflicts"] or review_queue or not conserved else "completed",
              "reason": "empty_inventory" if not inventory_paths else None,
              "case_id": case_id, "input_count": len(inventory_paths),
              "accounting_conserved": conserved,
              "unaccounted_inputs": sorted(inventory_paths - accounted_paths),
              "dispositions": dispositions,
              "review_queue": review_queue, "plan_id": data["plan_id"], "classification": data["records"],
              "tags": [{"path": r["path"], "tags": [l["tag"] for l in r["labels"]]} for r in data["records"]],
              "operations": data["operations"], "change_records": changes, "changes": changes,
              "duplicates": data["duplicates"], "versions": data["version_relations"],
              "conflicts": data["conflicts"], "failures": failures,
              "sources": [item["path"] for item in data["inventory"]],
              "uncertainty": ["Classification review items remain uncopied.",
                              "Version candidates do not permit deletion or selection of a canonical publication."]}
    dump(Path(args.output) / "classification_report.json", report)
    dump(Path(args.output) / "catalog.json", {"records": data["records"], "operations": data["operations"]})
    write_result(args.case, args.output, report)
    print(json.dumps({"status": report["status"], "committed": sum(c["event"] == "committed" for c in changes),
                      "failures": len(failures)}, ensure_ascii=False))
    return 2 if not inventory_paths or failures or data["conflicts"] or review_queue or not conserved else 0


def rollback(args):
    records = []
    if Path(args.journal).exists():
        records = [json.loads(line) for line in Path(args.journal).read_text(encoding="utf-8").splitlines() if line.strip()]
    changes, failures = [], []
    for record in reversed([r for r in records if r.get("event") == "committed" and
                            (not args.plan_id or r.get("plan_id") == args.plan_id)]):
        target = Path(record["target_path"])
        try:
            if target.exists() and (target.is_symlink() or sha256(target) != record["sha256"]):
                raise ValueError("modified_target_refused")
            if target.exists():
                target.unlink()
            event = {**record, "event": "rolled_back"}
            changes.append(event)
            append_journal(args.journal, event)
        except (OSError, ValueError) as error:
            failures.append({"target": str(target), "reason": str(error)})
    report = {"status": "partial" if failures else "rolled_back", "changes": changes,
              "change_records": changes, "failures": failures,
              "uncertainty": ["Only unchanged committed copies are removed."]}
    dump(Path(args.output) / "rollback_report.json", report)
    write_result(args.case, args.output, report)
    return 2 if failures else 0


def main():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--version", action="version", version=VERSION)
    commands = root.add_subparsers(dest="command", required=True)
    classify = commands.add_parser("classify")
    classify.add_argument("--library", required=True)
    classify.add_argument("--destination", required=True)
    classify.add_argument("--rules", required=True)
    classify.add_argument("--work", required=True)
    classify.add_argument("--output", required=True)
    classify.add_argument("--case")
    classify.add_argument("--task")
    classify.add_argument("--simulate-denied", action="append", default=[])
    apply_cmd = commands.add_parser("apply")
    apply_cmd.add_argument("--plan", required=True)
    apply_cmd.add_argument("--journal", required=True)
    apply_cmd.add_argument("--output", required=True)
    apply_cmd.add_argument("--case")
    back = commands.add_parser("rollback")
    back.add_argument("--journal", required=True)
    back.add_argument("--output", required=True)
    back.add_argument("--case")
    back.add_argument("--plan-id")
    args = root.parse_args()
    try:
        if getattr(args, "task", None):
            Path(args.task).read_text(encoding="utf-8")
        if hasattr(args, "work"):
            Path(args.work).mkdir(parents=True, exist_ok=True)
        Path(args.output).mkdir(parents=True, exist_ok=True)
        return {"classify": plan, "apply": apply, "rollback": rollback}[args.command](args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
