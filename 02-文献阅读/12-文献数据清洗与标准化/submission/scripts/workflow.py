#!/usr/bin/env python3
"""Identity-preserving tabular normalization with row-level rule provenance."""
import argparse
import csv
from decimal import Decimal, InvalidOperation
import json
import re
import sys

from common import Run, add_arguments, digest, read_json, write_json


def convert_number(text, factor, offset):
    return (Decimal(text) * factor + offset)


def convert_value(raw, rule, digits=None):
    number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
    clean = raw.strip().replace(",", "")
    interval = re.fullmatch(r"(" + number + r")\s*[-–]\s*(" + number + r")", clean)
    factor = Decimal(rule["factor"])
    offset = Decimal(rule.get("offset", "0"))
    if interval:
        values = [convert_number(interval.group(1), factor, offset), convert_number(interval.group(2), factor, offset)]
        if values[0] > values[1]:
            raise ValueError("interval reversed after conversion")
        converted = [values[0], values[1]]
    else:
        converted = [convert_number(clean, factor, offset)]
    if digits is not None:
        quantum = Decimal(1).scaleb(-int(digits))
        converted = [value.quantize(quantum) for value in converted]
    return "–".join(format(value, "f") for value in converted)


def write_csv(path, rows, fields):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames, list(reader)


def main():
    args = add_arguments(argparse.ArgumentParser(description=__doc__)).parse_args()
    try:
        run = Run(args)
    except Exception as error:
        print(json.dumps({"status": "input_error", "error": str(error)}), file=sys.stderr)
        return 2
    try:
        input_path = run.path(run.case.get("records", "records.csv"))
        rules_path = run.path(run.case.get("rules", "normalization-rules.json"))
        rules = read_json(rules_path)
        fields, records = read_csv(input_path)
        if not fields or not records:
            raise ValueError("empty_table_or_missing_header")
        required_identity = rules.get("identity_columns", [])
        if any(column not in fields for column in required_identity + [rules.get("row_id", "row_id"), "field", "raw_value", "raw_unit"]):
            raise ValueError("input lacks declared row identity or value columns")
        normalized = []
        changes = []
        pending = []
        seen = {}
        row_ids = set()
        for position, record in enumerate(records, 1):
            row_id = record[rules.get("row_id", "row_id")]
            if row_id in row_ids:
                raise ValueError("duplicate input row_id: " + row_id)
            row_ids.add(row_id)
            original = dict(record)
            field_map = rules.get("field_map", {})
            mapping_rule = field_map.get(record.get("field", ""))
            if isinstance(mapping_rule, dict):
                field = mapping_rule.get("target", record.get("field", ""))
                mapping_rule_id = mapping_rule.get("rule_id", "F001")
                mapping_priority = int(mapping_rule.get("priority", 10))
            else:
                field = mapping_rule if mapping_rule is not None else record.get("field", "")
                mapping_rule_id = "F001"
                mapping_priority = 10
            raw_value = record.get("raw_value", "")
            raw_unit = record.get("raw_unit", "")
            standard_value = ""
            target_unit = ""
            actions = []
            rule_trail = []
            conversion_formula = None
            if field != record.get("field"):
                actions.append("field_mapped")
                rule_trail.append({"rule_id": mapping_rule_id, "kind": "field_mapping", "priority": mapping_priority})
            if raw_value.strip() == "":
                actions.append("missing_preserved_empty")
                pending.append({"row_id": row_id, "kind": "missing_value", "original": original,
                                "resolution": "left empty; never converted to zero"})
            else:
                unit_rule = rules.get("units", {}).get(raw_unit)
                if not unit_rule:
                    actions.append("unknown_unit_preserved")
                    pending.append({"row_id": row_id, "kind": "unknown_unit", "original": original,
                                    "resolution": "standard_value left empty; no guessed conversion"})
                else:
                    try:
                        standard_value = convert_value(raw_value, unit_rule, unit_rule.get("decimal_places"))
                        target_unit = unit_rule["target"]
                        actions.append("unit_standardized" if standard_value != raw_value or target_unit != raw_unit else "value_retained")
                        rule_trail.append({"rule_id": unit_rule["rule_id"], "kind": "unit_standardization", "priority": int(unit_rule.get("priority", 20))})
                        conversion_formula = "standard=(raw*factor)+offset"
                    except (InvalidOperation, ValueError) as error:
                        actions.append("numeric_parse_failed")
                        pending.append({"row_id": row_id, "kind": "numeric_parse_failed", "original": original,
                                        "resolution": str(error)})
            range_rule = rules.get("numeric_ranges", {}).get(field)
            if range_rule and standard_value and "–" not in standard_value:
                value = Decimal(standard_value)
                if value < Decimal(str(range_rule["min"])) or value > Decimal(str(range_rule["max"])):
                    pending.append({"row_id": row_id, "kind": "out_of_declared_range", "original": original,
                                    "standard_value": standard_value, "rule": range_rule})
                    rule_trail.append({"rule_id": range_rule.get("rule_id", "R001"), "kind": "range_check", "priority": int(range_rule.get("priority", 30))})
            identity = tuple(record.get(column, "") for column in required_identity)
            duplicate_key = (identity, field, standard_value or raw_value, target_unit or raw_unit, record.get("context", ""))
            action = "candidate_new"
            if duplicate_key in seen:
                action = "mapped_duplicate_extraction"
                canonical_id = seen[duplicate_key]
            else:
                canonical_id = row_id
                seen[duplicate_key] = row_id
            duplicate_rule = rules.get("duplicate_rule", {"rule_id": "D001", "priority": 40})
            rule_trail.append({"rule_id": duplicate_rule.get("rule_id", "D001"), "kind": "duplicate_detection", "priority": int(duplicate_rule.get("priority", 40))})
            if [item["kind"] for item in sorted(rule_trail, key=lambda item: item["priority"])] != [item["kind"] for item in rule_trail]:
                raise ValueError("configured rule priorities conflict with deterministic execution order at row " + row_id)
            output_row = {"row_id": row_id, "canonical_row_id": canonical_id,
                          "paper_id": record.get("paper_id", ""), "experiment_id": record.get("experiment_id", ""),
                          "extraction_event_id": record.get("extraction_event_id", ""),
                          "field": field, "raw_value": raw_value, "raw_unit": raw_unit,
                          "standard_value": standard_value, "standard_unit": target_unit,
                          "context": record.get("context", ""), "status": action}
            normalized.append(output_row)
            actions.append(action)
            changes.append({"input_row_id": row_id, "canonical_row_id": canonical_id,
                            "original_record": original, "field_after_mapping": field,
                            "rule_ids": [item["rule_id"] for item in rule_trail], "rule_trail": rule_trail, "actions": actions,
                            "output_value": standard_value, "output_unit": target_unit,
                            "formula": conversion_formula,
                            "source_identity": {column: record.get(column, "") for column in required_identity}})
        clean_rows = [row for row in normalized if row["status"] != "mapped_duplicate_extraction"]
        clean_path = run.output / "clean.csv"
        changes_path = run.output / "changes.json"
        pending_path = run.output / "pending.json"
        write_csv(clean_path, clean_rows, ["row_id", "canonical_row_id", "paper_id", "experiment_id", "extraction_event_id", "field", "raw_value", "raw_unit", "standard_value", "standard_unit", "context", "status"])
        write_json(changes_path, changes)
        write_json(pending_path, pending)
        _, clean_reread = read_csv(clean_path)
        changes_reread = read_json(changes_path)
        if len(clean_reread) != len(clean_rows) or len(changes_reread) != len(records):
            raise ValueError("export readback row accounting mismatch")
        status = "partial" if pending else "success"
        run.changes.append("mapped only identity-matching duplicate extractions; distinct experiments were not merged")
        run.finish({"status": status, "clean_rows": clean_rows, "changes": changes, "pending": pending,
                    "summary": {"input_rows": len(records), "clean_rows": len(clean_rows),
                                "duplicate_extractions_mapped": len(records) - len(clean_rows),
                                "pending_rows": len(pending)},
                    "source": {"path": str(input_path), "sha256": digest(input_path),
                               "records_scope": "organizer-derived extraction records, not original author experimental data"},
                    "sources": [{"path": str(input_path), "sha256": digest(input_path)},
                                {"path": str(rules_path), "sha256": digest(rules_path)}]},
                   aliases={"data": "clean_rows", "standardizeddata": "clean_rows", "result": "clean_rows"},
                   code=2 if pending else 0,
                   trace_extra={"csv_and_json_readback": True, "deterministic_decimal_conversion": True})
    except Exception as error:
        run.uncertainty.append(str(error))
        run.finish({"status": "input_error", "clean_rows": [], "changes": [], "pending": [],
                    "failure": str(error)}, code=2)


if __name__ == "__main__":
    sys.exit(main())
