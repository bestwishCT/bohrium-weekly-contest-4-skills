#!/usr/bin/env python3
"""Trace table trends/anomalies to coordinates and audit proposed rechecks."""
import argparse
import json
import statistics
import sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

SCHEMA = "experiment-anomaly-audit-v1"
EVIDENCE_RANK = {"direct_observation": 0, "association": 1, "hypothesis": 2}


def dec(value, at, errors):
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise InvalidOperation
        return number
    except (InvalidOperation, ValueError, TypeError):
        errors.append({"code": "INVALID_NUMBER", "at": at, "value": value})
        return None


def nonnegative_dec(value, at, errors):
    number = dec(value, at, errors)
    if number is not None and number < 0:
        errors.append({"code": "NONNEGATIVE_NUMBER_REQUIRED", "at": at, "value": value})
        return None
    return number


def coordinates(record):
    return {"sample": record.get("sample"), "batch": record.get("batch"), "channel": record.get("channel"),
            "well": record.get("well"), "replicate": record.get("replicate"), "time_index": record.get("time_index"),
            "metric": record.get("metric"), "source": record.get("source")}


def prepare_records(records, errors, warnings):
    prepared = []
    seen = set()
    for index, record in enumerate(records):
        source = record.get("source", {})
        if not source.get("file") or source.get("row") is None:
            errors.append({"code": "SOURCE_COORDINATE_REQUIRED", "record_index": index})
        unit = record.get("unit")
        if not unit:
            errors.append({"code": "UNIT_REQUIRED", "record_index": index})
        quality = record.get("quality")
        if quality not in {"ok", "saturated", "out_of_range", "missing"}:
            errors.append({"code": "QUALITY_FLAG_REQUIRED", "record_index": index, "actual": quality})
        key = (record.get("sample"), record.get("batch"), record.get("channel"), record.get("well"), record.get("replicate"), record.get("time_index"), record.get("metric"))
        if key in seen:
            errors.append({"code": "DUPLICATE_RECORD_COORDINATE", "record_index": index, "coordinate": coordinates(record)})
        seen.add(key)
        if quality != "ok" or record.get("value") is None:
            warnings.append({"code": "MISSING_VALUE" if record.get("value") is None else quality.upper() + "_EXCLUDED",
                             "coordinate": coordinates(record), "original_value": record.get("value"), "unit": unit,
                             "action": "do not impute or treat a censored value as an exact number"})
            continue
        number = dec(record.get("value"), f"record[{index}].value", errors)
        if number is not None:
            enriched = dict(record)
            enriched["numeric_value"] = number
            prepared.append(enriched)
    return prepared


def trends(records):
    groups = defaultdict(list)
    for record in records:
        key = (record.get("metric"), record.get("sample"), record.get("batch"), record.get("channel"), record.get("unit"))
        groups[key].append(record)
    output = []
    for key in sorted(groups, key=lambda value: tuple(str(part) for part in value)):
        group = groups[key]
        by_time = defaultdict(list)
        for record in group:
            by_time[record.get("time_index")].append(record)
        time_summaries = []
        for time_index, values in sorted(by_time.items(), key=lambda pair: pair[0]):
            median = statistics.median(record["numeric_value"] for record in values)
            time_summaries.append({"time_index": time_index, "median": str(median), "n": len(values),
                                   "sources": [record.get("source") for record in values]})
        if len(time_summaries) >= 2:
            first, last = time_summaries[0], time_summaries[-1]
            delta = Decimal(last["median"]) - Decimal(first["median"])
            state = "first_last_median_delta_only_not_causality"
        else:
            delta, state = None, "single_timepoint_not_a_trend"
        output.append({"metric": key[0], "sample": key[1], "batch": key[2], "channel": key[3], "unit": key[4],
                       "time_summaries": time_summaries, "first_last_delta": str(delta) if delta is not None else None,
                       "trend_state": state})
    return output


def anomaly_results(data, records, errors, warnings):
    results = []
    for rule in data.get("anomaly_rules", []):
        threshold_type = rule.get("threshold_type")
        if threshold_type not in {"task_given", "reference_calculated", "exploratory"} or not rule.get("threshold_source"):
            errors.append({"code": "THRESHOLD_PROVENANCE_REQUIRED", "rule": rule.get("id")})
        threshold = nonnegative_dec(rule.get("deviation_gt"), f"rule[{rule.get('id')}].deviation_gt", errors)
        matching_all = [record for record in data.get("records", []) if record.get("metric") == rule.get("metric") and
                        record.get("batch") == rule.get("batch") and record.get("channel") == rule.get("channel") and
                        record.get("time_index") == rule.get("time_index")]
        wrong_units = [record for record in matching_all if record.get("unit") != rule.get("unit")]
        for record in wrong_units:
            errors.append({"code": "UNIT_MISMATCH", "rule": rule.get("id"), "expected": rule.get("unit"),
                           "actual": record.get("unit"), "coordinate": coordinates(record),
                           "action": "retain original values and obtain an explicit conversion before comparing"})
        matched = [record for record in records if record.get("metric") == rule.get("metric") and
                   record.get("batch") == rule.get("batch") and record.get("channel") == rule.get("channel") and
                   record.get("time_index") == rule.get("time_index") and record.get("unit") == rule.get("unit")]
        controls = [record for record in matched if record.get("sample") == rule.get("control_sample") and record.get("role") == "control"]
        reference = statistics.median(record["numeric_value"] for record in controls) if controls else None
        if not controls:
            errors.append({"code": "CONTROL_MISSING", "rule": rule.get("id")})
        elif len(controls) < 2:
            warnings.append({"code": "CONTROL_REPLICATES_INSUFFICIENT", "rule": rule.get("id"), "n": len(controls),
                             "action": "do not claim statistical significance"})
        anomalies = []
        if reference is not None and threshold is not None:
            for record in matched:
                if record.get("role") == "control":
                    continue
                deviation = record["numeric_value"] - reference
                if abs(deviation) > threshold:
                    anomalies.append({"coordinate": coordinates(record), "raw_value": str(record["numeric_value"]), "unit": record.get("unit"),
                                      "reference_median": str(reference), "deviation_signed": str(deviation),
                                      "deviation_absolute": str(abs(deviation)), "criterion": f"abs(value-median(control)) > {threshold} {rule.get('unit')}"})
        results.append({"rule_id": rule.get("id"), "dimensions": {"metric": rule.get("metric"), "batch": rule.get("batch"), "channel": rule.get("channel"), "time_index": rule.get("time_index")},
                        "threshold": str(threshold), "unit": rule.get("unit"), "threshold_type": threshold_type,
                        "threshold_source": rule.get("threshold_source"), "reference_count_usable": len(controls),
                        "reference_sources": [record.get("source") for record in controls],
                        "reference_median": str(reference) if reference is not None else None,
                        "anomalies": anomalies, "excluded_quality_flags": [coordinates(record) for record in matching_all if record.get("quality") != "ok"]})
    return results


def review_math(data, errors):
    currency = data.get("review_budget", {}).get("currency")
    total_minutes, total_cost = Decimal(0), Decimal(0)
    time_complete, cost_complete = bool(data.get("review_actions")), bool(data.get("review_actions"))
    audited_actions = []
    if not data.get("review_actions"):
        errors.append({"code": "REVIEW_ACTION_REQUIRED"})
    causes = {cause.get("id") for cause in data.get("causes", [])}
    for action in data.get("review_actions", []):
        for field in ("action", "linked_cause", "expected_observation", "supports_or_excludes", "failure_branch", "owner_role"):
            if not action.get(field):
                errors.append({"code": "REVIEW_ACTION_INCOMPLETE", "action": action.get("id"), "missing": field})
        if action.get("linked_cause") not in causes:
            errors.append({"code": "REVIEW_ACTION_CAUSE_UNKNOWN", "action": action.get("id")})
        if not action.get("source_refs"):
            errors.append({"code": "SOURCE_MISSING", "action": action.get("id")})
        minutes = nonnegative_dec(action.get("minutes"), f"action[{action.get('id')}].minutes", errors)
        if minutes is not None:
            total_minutes += minutes
        else:
            time_complete = False
        formulas = []
        if not action.get("cost_items"):
            cost_complete = False
            errors.append({"code": "REVIEW_COST_MISSING", "action": action.get("id"), "action_required": "supply a sourced price or retain an unknown subtotal, never zero"})
        for item in action.get("cost_items", []):
            quantity = nonnegative_dec(item.get("quantity"), f"action[{action.get('id')}].cost.quantity", errors)
            price = nonnegative_dec(item.get("unit_price"), f"action[{action.get('id')}].cost.unit_price", errors)
            if item.get("currency") != currency:
                cost_complete = False
                errors.append({"code": "CURRENCY_MISMATCH", "action": action.get("id"), "expected": currency, "actual": item.get("currency")})
                continue
            if quantity is not None and price is not None:
                amount = quantity * price
                total_cost += amount
                formulas.append(f"{quantity} {item.get('unit')} * {price} {currency}/{item.get('unit')} = {amount} {currency}")
            else:
                cost_complete = False
        audited_actions.append({**action, "cost_formulas": formulas, "execution_status": "proposed_not_executed"})
    limit_minutes = nonnegative_dec(data.get("review_budget", {}).get("time_limit_minutes"), "review_budget.time_limit_minutes", errors)
    limit_cost = nonnegative_dec(data.get("review_budget", {}).get("amount"), "review_budget.amount", errors)
    conflicts = []
    if time_complete and limit_minutes is not None and total_minutes > limit_minutes:
        errors.append({"code": "REVIEW_TIME_BUDGET_CONFLICT", "computed": str(total_minutes), "limit": str(limit_minutes), "unit": "minute"})
        conflicts.append("time")
    if cost_complete and limit_cost is not None and total_cost > limit_cost:
        errors.append({"code": "REVIEW_COST_BUDGET_CONFLICT", "computed": str(total_cost), "limit": str(limit_cost), "currency": currency})
        conflicts.append("cost")
    return {"actions": audited_actions, "total_minutes": str(total_minutes) if time_complete else None,
            "total_cost": str(total_cost) if cost_complete else None, "currency": currency,
            "totals_complete": time_complete and cost_complete,
            "cost_formula": "sum(action quantity * unit price); proposed resources only", "budget_conflicts": conflicts}


def process(data):
    errors, warnings = [], []
    if data.get("schema") != SCHEMA:
        errors.append({"code": "SCHEMA_MISMATCH", "expected": SCHEMA})
    if data.get("scenario") not in {"case_observed", "organizer_prospective", "synthetic_nonofficial"}:
        errors.append({"code": "SCENARIO_REQUIRED", "allowed": ["case_observed", "organizer_prospective", "synthetic_nonofficial"]})
    records = prepare_records(data.get("records", []), errors, warnings)
    anomalies = anomaly_results(data, records, errors, warnings)
    causes = []
    for cause in data.get("causes", []):
        evidence_class = cause.get("evidence_class")
        if evidence_class not in EVIDENCE_RANK:
            errors.append({"code": "CAUSE_EVIDENCE_CLASS_INVALID", "cause": cause.get("id")})
        if not cause.get("support_refs"):
            errors.append({"code": "CAUSE_SUPPORT_REQUIRED", "cause": cause.get("id")})
        causes.append({**cause, "causality_status": "candidate_only_unless_actually_controlled_evidence_is_supplied"})
    causes.sort(key=lambda cause: (EVIDENCE_RANK.get(cause.get("evidence_class"), 99), str(cause.get("id"))))
    reviews = review_math(data, errors)
    return {"auxiliary_schema": SCHEMA, "case_id": data.get("case_id"), "status": "failed" if errors else "pass",
            "five_part_analysis": {
                "trends": trends(records), "anomalies": anomalies,
                "possible_causes": causes,
                "recheck_steps": reviews["actions"],
                "next_experiments": data.get("next_experiments", [])},
            "recheck_resource_arithmetic": {key: value for key, value in reviews.items() if key != "actions"},
            "errors": errors, "uncertainties": warnings,
            "unexplained": data.get("unexplained", []),
            "status_scope": "Calculations on supplied data only; no wet recheck, cause confirmation or device-state inference.",
            "original_units_retained": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    try:
        raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
        result = process(raw.get("anomaly", raw))
        code = 2 if result["status"] == "failed" else 0
    except (OSError, json.JSONDecodeError, TypeError, KeyError, AttributeError, ValueError) as exc:
        result = {"auxiliary_schema": SCHEMA, "status": "failed", "errors": [{"code": "INPUT_OR_RUNTIME_ERROR", "message": str(exc)}]}
        code = 2
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{result['status']}: {output}; exit={code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
