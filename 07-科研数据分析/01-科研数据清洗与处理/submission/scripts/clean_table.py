#!/usr/bin/env python3
"""Deterministic, dictionary-led cleaning for aggregate table transcriptions."""
import argparse
import csv
import hashlib
import json
import platform
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

VERSION = "1.0.0"
MISSING_FIELDS = ["row", "field", "original", "repaired", "issue", "action", "result", "rule", "review"]
DUP_FIELDS = ["group", "rows", "kind", "action", "normalized_key"]
UTC = lambda: datetime.now(timezone.utc).isoformat()


class InputError(Exception):
    pass


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def dump_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def dump_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def decimal_text(value):
    if value == 0:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def norm_key(value):
    return re.sub(r"\s+", " ", value.strip()).casefold()


def read_required(path):
    if not path or not path.is_file():
        raise InputError("required file missing: %s" % path)
    return path


def source_record(path):
    return {"name": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)}


def parse_number(text, rule):
    value = text.strip()
    if rule.get("decimal_comma"):
        if value.count(",") == 1 and "." not in value:
            value = value.replace(",", ".")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise InputError("not a finite decimal")
    if not number.is_finite():
        raise InputError("not a finite decimal")
    return number


def build_result(case_path, values_path, output):
    case = json.loads(read_required(case_path).read_text(encoding="utf-8"))
    values = json.loads(read_required(values_path).read_text(encoding="utf-8"))
    contract = case.get("output_contract")
    if not isinstance(contract, dict) or not isinstance(values, dict):
        raise InputError("this adapter requires object output_contract and object contract values")
    if set(contract) != set(values):
        raise InputError("contract values keys differ from output_contract: expected=%s actual=%s" % (sorted(contract), sorted(values)))
    if "case_id" not in case:
        raise InputError("case_id missing")
    if "case_id" in values:
        raise InputError("contract values must not replace case_id")
    result = {"case_id": case["case_id"]}
    result.update(values)
    dump_json(output / "result.json", result)


def main():
    parser = argparse.ArgumentParser(description="Clean CSV using explicit dictionary and workorder rules; never imputes.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--rules", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--case", type=Path)
    parser.add_argument("--task", type=Path)
    parser.add_argument("--resources", type=Path)
    parser.add_argument("--contract-values", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    args.work.mkdir(parents=True, exist_ok=True)
    started = UTC()
    try:
        source = read_required(args.input).resolve()
        rules_path = read_required(args.rules)
        protected = {source, rules_path.resolve()}
        protected.update(path.resolve() for path in (args.case, args.task, args.resources, args.contract_values) if path is not None)
        for output_name in ("cleaned.csv", "quarantined.csv", "issues.csv", "duplicates.csv", "quality_report.json", "run_manifest.json", "run_failure.json", "result.json"):
            if (args.output / output_name).resolve() in protected:
                raise InputError("an output would overwrite an input or evidence file: %s" % output_name)
        rules = json.loads(rules_path.read_text(encoding="utf-8"))
        if rules.get("schema_version") != "1.0":
            raise InputError("unsupported rules schema_version")
        fields = rules.get("fields")
        if not isinstance(fields, list) or not fields:
            raise InputError("rules.fields must be a non-empty list")
        by_name = {}
        for rule in fields:
            name = rule.get("name")
            if not name or name in by_name:
                raise InputError("field names must be non-empty and unique")
            if rule.get("kind") not in {"key", "measure", "attribute", "time", "unit"}:
                raise InputError("unsupported field kind: %s" % name)
            unit_rule = rule.get("unit")
            if unit_rule:
                if not unit_rule.get("target") or not isinstance(unit_rule.get("factors"), dict) or not unit_rule["factors"]:
                    raise InputError("unit target and nonempty factors required: %s" % name)
                for factor_name, factor_text in unit_rule["factors"].items():
                    try:
                        factor = Decimal(str(factor_text))
                    except InvalidOperation:
                        raise InputError("invalid unit factor: %s/%s" % (name, factor_name))
                    if not factor.is_finite() or factor == 0:
                        raise InputError("unit factors must be finite and nonzero: %s/%s" % (name, factor_name))
            for bound in ("min", "max"):
                if bound in rule.get("qc", {}):
                    try:
                        bound_value = Decimal(str(rule["qc"][bound]))
                    except InvalidOperation:
                        raise InputError("invalid QC bound: %s/%s" % (name, bound))
                    if not bound_value.is_finite():
                        raise InputError("QC bounds must be finite: %s/%s" % (name, bound))
            by_name[name] = rule
        keys = rules.get("key_fields", [])
        if not keys or any(k not in by_name for k in keys):
            raise InputError("key_fields must name declared columns")
        duplicates = rules.get("duplicates", {})
        if duplicates.get("exact") not in {"drop_first", "keep_review"}:
            raise InputError("duplicates.exact must be drop_first or keep_review")
        if duplicates.get("conflict") not in {"quarantine_group", "keep_review"}:
            raise InputError("duplicates.conflict must be quarantine_group or keep_review")
        encoding = rules.get("input", {}).get("encoding", "utf-8")
        delimiter = rules.get("input", {}).get("delimiter", ",")
        if delimiter not in {",", ";", "\t", "|"}:
            raise InputError("delimiter must be explicitly one of comma, semicolon, tab, pipe")
        with source.open("r", encoding=encoding, newline="") as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            if reader.fieldnames is None:
                raise InputError("CSV header missing")
            if any(x is None or x == "" for x in reader.fieldnames) or len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise InputError("CSV header has blank or duplicate columns")
            missing_columns = [n for n in by_name if n not in reader.fieldnames]
            if missing_columns:
                raise InputError("required columns missing: %s" % missing_columns)
            raw_rows = list(reader)
        if not raw_rows:
            raise InputError("CSV contains no data rows")
        if any(None in row or any(value is None for value in row.values()) for row in raw_rows):
            raise InputError("row has fewer or more cells than the header")
        snapshots = args.work / "snapshots"
        snapshots.mkdir(parents=True, exist_ok=True)
        snapshot = snapshots / (sha256(source)[:16] + "-" + source.name)
        if not snapshot.exists():
            shutil.copyfile(source, snapshot)
        sources = {"input": source_record(source), "rules": source_record(rules_path)}
        for label, path in (("case", args.case), ("task", args.task), ("resources", args.resources)):
            if path is not None:
                sources[label] = source_record(read_required(path))
        rows, issues = [], []
        for index, raw in enumerate(raw_rows, 2):
            clean = dict(raw)
            flags, bad_actions = [], []
            for name, rule in by_name.items():
                original = raw.get(name, "")
                value = original.strip() if rule.get("strip", True) else original
                repaired = value
                for old, new in rule.get("literal_map", {}).items():
                    value = value.replace(old, new)
                after_literal = value
                if after_literal != repaired:
                    issues.append(dict(row=index, field=name, original=original, repaired=after_literal, issue="explicit_storage_repair", action="map_literal", result=after_literal, rule="literal_map", review="no"))
                if rule.get("normalize") == "casefold_strip":
                    value = norm_key(value)
                    if value != after_literal:
                        issues.append(dict(row=index, field=name, original=original, repaired=value, issue="key_format", action="normalize_key", result=value, rule="normalize", review="no"))
                semantics = rule.get("missing", {})
                if value in semantics:
                    state = semantics[value]
                    clean[name] = ""
                    issues.append(dict(row=index, field=name, original=original, repaired=value, issue=state, action="preserve_unresolved_empty", result="", rule="missing", review="yes"))
                    flags.append(state)
                    continue
                if rule.get("required") and value == "":
                    clean[name] = ""
                    issues.append(dict(row=index, field=name, original=original, repaired=value, issue="entry_missing", action="preserve_unresolved_empty", result="", rule="required", review="yes"))
                    flags.append("entry_missing")
                    bad_actions.append(rule.get("invalid_action", "retain_unresolved"))
                    continue
                if rule.get("type") == "decimal" and value != "":
                    try:
                        number = parse_number(value, rule)
                        if rule.get("decimal_comma") and "," in value:
                            issues.append(dict(row=index, field=name, original=original, repaired=value, issue="decimal_format", action="decimal_comma_to_point", result=decimal_text(number), rule="decimal_comma", review="no"))
                        unit_rule = rule.get("unit")
                        if unit_rule:
                            if unit_rule.get("unit_column"):
                                unit_col = unit_rule["unit_column"]
                                if unit_col not in raw:
                                    raise InputError("unit_column missing: %s" % unit_col)
                                from_unit = raw[unit_col].strip()
                            else:
                                from_unit = unit_rule["from"]
                            factors = unit_rule.get("factors", {})
                            if from_unit not in factors:
                                clean[name] = ""
                                issues.append(dict(row=index, field=name, original=original, repaired=value, issue="unknown_unit:" + from_unit, action="preserve_unresolved_empty", result="", rule="unit.factors", review="yes"))
                                flags.append("unknown_unit")
                                bad_actions.append(unit_rule.get("unknown_action", "retain_unresolved"))
                                continue
                            with localcontext() as ctx:
                                ctx.prec = max(50, len(value) + 30)
                                converted = number * Decimal(factors[from_unit])
                                back = converted / Decimal(factors[from_unit])
                            if back != number:
                                raise InputError("unit conversion failed reversibility check at row %d field %s" % (index, name))
                            converted_text = decimal_text(converted)
                            clean[name] = converted_text
                            if unit_rule.get("unit_column"):
                                clean[unit_col] = unit_rule["target"]
                            if from_unit != unit_rule["target"] or converted_text != value:
                                issues.append(dict(row=index, field=name, original=original, repaired=value, issue="unit_or_numeric_format", action="convert:%s->%s" % (from_unit, unit_rule["target"]), result=converted_text, rule="unit.factors=" + factors[from_unit], review="no"))
                            number = converted
                        else:
                            clean[name] = decimal_text(number)
                        qc = rule.get("qc", {})
                        if ("min" in qc and number < Decimal(qc["min"])) or ("max" in qc and number > Decimal(qc["max"])):
                            action = qc.get("action", "retain_unresolved")
                            issues.append(dict(row=index, field=name, original=original, repaired=value, issue="qc_out_of_range", action=action, result=clean[name], rule=json.dumps(qc, sort_keys=True), review="yes"))
                            flags.append("qc_out_of_range")
                            bad_actions.append(action)
                    except InputError as exc:
                        if "unit_column missing" in str(exc):
                            raise
                        clean[name] = ""
                        action = rule.get("invalid_action", "retain_unresolved")
                        issues.append(dict(row=index, field=name, original=original, repaired=value, issue="invalid_decimal", action=action, result="", rule="type=decimal", review="yes"))
                        flags.append("invalid_decimal")
                        bad_actions.append(action)
                else:
                    clean[name] = value
            rows.append({"source_row": index, "raw": raw, "clean": clean, "flags": flags, "actions": bad_actions, "disposition": "retain", "reason": ""})
        for row in rows:
            if "quarantine" in row["actions"]:
                row["disposition"], row["reason"] = "quarantine", "invalid_or_qc_workorder"
        normalized = [{j: norm_key(row["clean"].get(j, "")) for j in keys} for row in rows]
        signatures = [json.dumps(row["clean"], ensure_ascii=False, sort_keys=True) for row in rows]
        groups = defaultdict(list)
        for pos, key in enumerate(normalized):
            groups[json.dumps(key, ensure_ascii=False, sort_keys=True)].append(pos)
        dup_rows, dup_group = [], 0
        for group_key, positions in sorted(groups.items()):
            if len(positions) < 2 or "" in [normalized[p][k] for p in positions for k in keys]:
                continue
            dup_group += 1
            unique_sigs = {signatures[p] for p in positions}
            if len(unique_sigs) == 1:
                kind = "exact_duplicate"
                action = duplicates["exact"]
                if action == "drop_first":
                    for p in positions[1:]:
                        rows[p]["disposition"], rows[p]["reason"] = "duplicate_removed", "exact_duplicate_drop_first"
            elif rules.get("keys_expected_unique", True):
                kind = "conflicting_key_duplicate"
                action = duplicates["conflict"]
                if action == "quarantine_group":
                    for p in positions:
                        if rows[p]["disposition"] == "retain":
                            rows[p]["disposition"], rows[p]["reason"] = "quarantine", "conflicting_key_duplicate"
            else:
                kind, action = "legitimate_repeated_measure", "keep_all"
            dup_rows.append({"group": dup_group, "rows": ";".join(str(rows[p]["source_row"]) for p in positions), "kind": kind, "action": action, "normalized_key": group_key})
        kept = [r for r in rows if r["disposition"] == "retain"]
        rejected = [r for r in rows if r["disposition"] != "retain"]
        output_fields = list(raw_rows[0].keys())
        dump_csv(args.output / "cleaned.csv", ["source_row"] + output_fields, [dict(r["clean"], source_row=r["source_row"]) for r in kept])
        dump_csv(args.output / "quarantined.csv", ["source_row", "disposition", "reason"] + output_fields, [dict(r["clean"], source_row=r["source_row"], disposition=r["disposition"], reason=r["reason"]) for r in rejected])
        dump_csv(args.output / "issues.csv", MISSING_FIELDS, issues)
        dump_csv(args.output / "duplicates.csv", DUP_FIELDS, dup_rows)
        with (args.output / "cleaned.csv").open(encoding="utf-8", newline="") as f:
            final_rows_for_quality = list(csv.DictReader(f))
        final_missing_in_memory = {name: sum(1 for r in kept if r["clean"].get(name, "") == "") for name in by_name}
        final_missing = {name: sum(1 for r in final_rows_for_quality if r.get(name, "") == "") for name in by_name}
        issue_counts = Counter(i["issue"].split(":", 1)[0] for i in issues)
        denominator = len(kept) * len(by_name)
        conversion_names = {"unit_or_numeric_format", "explicit_storage_repair", "decimal_format", "key_format"}
        report = {
            "schema_version": "1.0", "status": "complete_with_unresolved" if any(i["review"] == "yes" for i in issues) else "complete",
            "raw_rows": len(rows), "retained_rows": len(kept), "not_retained_rows": len(rejected),
            "duplicate_removed_rows": sum(r["disposition"] == "duplicate_removed" for r in rows),
            "quarantined_rows": sum(r["disposition"] == "quarantine" for r in rows),
            "duplicate_groups": len(dup_rows), "conflicting_duplicate_groups": sum(d["kind"] == "conflicting_key_duplicate" for d in dup_rows),
            "issues_by_type": dict(sorted(issue_counts.items())), "conversion_or_mapping_records": sum(i["issue"] in conversion_names for i in issues),
            "final_missing_by_field": final_missing,
            "final_missing_rate_by_field": {n: (float(Decimal(v) / Decimal(len(kept))) if kept else None) for n, v in final_missing.items()},
            "overall_final_missing_cells": sum(final_missing.values()),
            "overall_final_missing_rate": float(Decimal(sum(final_missing.values())) / Decimal(denominator)) if denominator else None,
            "imputation_performed": False,
            "population_statistics_inferred": False,
            "reconciliation": {}
        }
        report["reconciliation"] = {
            "raw_equals_retained_plus_not_retained": len(rows) == len(kept) + len(rejected),
            "not_retained_equals_duplicate_removed_plus_quarantined": len(rejected) == report["duplicate_removed_rows"] + report["quarantined_rows"],
            "cleaned_file_rows_match": sum(1 for _ in (args.output / "cleaned.csv").open(encoding="utf-8")) - 1 == len(kept),
            "quarantined_file_rows_match": sum(1 for _ in (args.output / "quarantined.csv").open(encoding="utf-8")) - 1 == len(rejected),
            "duplicate_file_groups_match": sum(1 for _ in (args.output / "duplicates.csv").open(encoding="utf-8")) - 1 == len(dup_rows),
            "issue_file_rows_match": sum(1 for _ in (args.output / "issues.csv").open(encoding="utf-8")) - 1 == len(issues),
            "final_missing_recomputed_from_cleaned": final_missing == final_missing_in_memory
        }
        if not all(report["reconciliation"].values()):
            raise InputError("quality reconciliation failed")
        dump_json(args.output / "quality_report.json", report)
        manifest = {
            "skill_version": VERSION, "status": "success", "partial": report["status"] != "complete", "started_at": started, "finished_at": UTC(),
            "exit_code": 0, "sources": sources, "snapshot": os_path(args.output, snapshot),
            "input_unchanged": sha256(source) == sources["input"]["sha256"], "no_randomness": True,
            "environment": {"python": platform.python_version(), "implementation": platform.python_implementation(), "stdlib_only": True},
            "outputs": ["cleaned.csv", "quarantined.csv", "issues.csv", "duplicates.csv", "quality_report.json"],
            "uncertainties": [i for i in issues if i["review"] == "yes"], "changes": issues,
            "notes": "Aggregate-transcription cleaning only. No imputation or population inference. quality-gold.md is not used."
        }
        dump_json(args.output / "run_manifest.json", manifest)
        if args.contract_values:
            build_result(args.case, args.contract_values, args.output)
        print("OK rows=%d retained=%d not_retained=%d issues=%d duplicate_groups=%d" % (len(rows), len(kept), len(rejected), len(issues), len(dup_rows)))
        return 0
    except (InputError, OSError, UnicodeError, LookupError, csv.Error, json.JSONDecodeError) as exc:
        failure = {"schema_version": "1.0", "status": "failed", "started_at": started, "finished_at": UTC(), "exit_code": 2, "error_type": type(exc).__name__, "message": str(exc), "partial_outputs_retained": True}
        dump_json(args.output / "run_failure.json", failure)
        print("ERROR %s" % exc, file=sys.stderr)
        return 2


def os_path(output, target):
    import os
    return os.path.relpath(str(target), str(output))


if __name__ == "__main__":
    sys.exit(main())
