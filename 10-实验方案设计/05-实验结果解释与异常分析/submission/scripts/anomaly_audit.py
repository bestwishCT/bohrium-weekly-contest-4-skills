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


def valid_id(value):
    return isinstance(value, str) and bool(value.strip())


def list_of_objects(value, at, errors):
    if not isinstance(value, list):
        errors.append({"code": "OBJECT_LIST_REQUIRED", "at": at})
        return []
    output = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append({"code": "OBJECT_LIST_ITEM_INVALID", "at": at, "index": index})
        else:
            output.append(item)
    return output


def validate_ids(items, at, errors):
    seen = {}
    for index, item in enumerate(items):
        value = item.get("id")
        location = f"{at}[{index}].id"
        if not valid_id(value):
            errors.append({"code": "ID_MISSING_OR_INVALID", "at": location, "value": value})
            continue
        if value in seen:
            errors.append({"code": "DUPLICATE_ID", "at": location, "id": value, "first_index": seen[value], "duplicate_index": index})
        else:
            seen[value] = index


def source_key(reference):
    if not isinstance(reference, dict) or not valid_id(reference.get("file")):
        return None
    row = reference.get("row")
    if isinstance(row, bool) or not isinstance(row, (int, str)) or (isinstance(row, str) and not row.strip()):
        return None
    sheet = reference.get("sheet")
    if sheet is not None and not isinstance(sheet, str):
        return None
    return reference["file"], sheet, row


def source_index(records):
    indexed = defaultdict(list)
    for index, record in enumerate(records):
        key = source_key(record.get("source"))
        if key is not None:
            indexed[key].append(index)
    return indexed


def resolve_refs(refs, at, indexed, errors, allowed=None):
    parsed = list_of_objects(refs, at, errors)
    if not parsed:
        errors.append({"code": "SOURCE_REFS_REQUIRED", "at": at})
    resolved = set()
    for ref_index, reference in enumerate(parsed):
        key = source_key(reference)
        if key is None:
            errors.append({"code": "SOURCE_COORDINATE_INVALID", "at": f"{at}[{ref_index}]"})
            continue
        matches = indexed.get(key, [])
        if len(matches) != 1:
            errors.append({"code": "EVIDENCE_REF_UNRESOLVED" if not matches else "EVIDENCE_REF_AMBIGUOUS",
                           "at": f"{at}[{ref_index}]", "matches": len(matches)})
            continue
        if allowed is not None and key not in allowed:
            errors.append({"code": "EVIDENCE_REF_NOT_LINKED_ANOMALY", "at": f"{at}[{ref_index}]"})
            continue
        resolved.add(key)
    return resolved


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
    time_types = defaultdict(set)
    for index, record in enumerate(records):
        source = record.get("source", {})
        if source_key(source) is None:
            errors.append({"code": "SOURCE_COORDINATE_REQUIRED", "record_index": index})
        time_index = record.get("time_index")
        if isinstance(time_index, bool) or not isinstance(time_index, (int, float, str)):
            errors.append({"code": "TIME_INDEX_INVALID", "record_index": index, "value": time_index})
        elif isinstance(time_index, (int, float)):
            dec(time_index, f"record[{index}].time_index", errors)
        group_key = (record.get("metric"), record.get("sample"), record.get("batch"), record.get("channel"), record.get("unit"))
        time_types[group_key].add(type(time_index).__name__)
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
    for key, types in time_types.items():
        if len(types) > 1:
            errors.append({"code": "MIXED_TIME_INDEX_TYPES", "metric": key[0], "sample": key[1], "batch": key[2],
                           "channel": key[3], "unit": key[4], "types": sorted(types)})
    return prepared


def time_sort_key(value):
    if not isinstance(value, bool) and isinstance(value, (int, float)):
        try:
            number = Decimal(str(value))
            if number.is_finite():
                return 0, number
        except (InvalidOperation, ValueError, TypeError):
            pass
    return 1, str(value)


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
        for time_index, values in sorted(by_time.items(), key=lambda pair: time_sort_key(pair[0])):
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


def review_math(data, errors, indexed, anomaly_by_cause, warnings):
    budget = data.get("review_budget", {})
    budget_currency = budget.get("currency") if valid_id(budget.get("currency")) else None
    declared_currencies = set()
    for action in data.get("review_actions", []):
        raw_items = action.get("cost_items")
        if isinstance(raw_items, list):
            declared_currencies.update(item.get("currency") for item in raw_items
                                       if isinstance(item, dict) and valid_id(item.get("currency")))
    currency = budget_currency
    if currency is None and len(declared_currencies) == 1:
        currency = next(iter(declared_currencies))
    if budget_currency is None and len(declared_currencies) > 1:
        errors.append({"code": "CURRENCY_MISMATCH", "expected": "one explicit common currency",
                       "actual": sorted(declared_currencies)})
    total_minutes, total_cost = Decimal(0), Decimal(0)
    time_complete, cost_complete = bool(data.get("review_actions")), bool(data.get("review_actions"))
    if currency is None:
        cost_complete = False
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
        linked_cause = action.get("linked_cause")
        allowed = anomaly_by_cause.get(linked_cause) if linked_cause in causes else None
        resolve_refs(action.get("source_refs"), f"review_actions[{action.get('id')}].source_refs", indexed, errors, allowed)
        raw_minutes = action.get("minutes")
        minutes = nonnegative_dec(raw_minutes, f"action[{action.get('id')}].minutes", errors) if raw_minutes is not None else None
        if minutes is not None:
            total_minutes += minutes
        else:
            time_complete = False
            if raw_minutes is None:
                warnings.append({"code": "REVIEW_TIME_UNKNOWN", "action": action.get("id"),
                                 "uncertainty": "minutes were not supplied; the action remains proposed and the time total is unknown"})
        formulas = []
        raw_items = action.get("cost_items")
        if not raw_items:
            cost_complete = False
            warnings.append({"code": "REVIEW_COST_UNKNOWN", "action": action.get("id"),
                             "uncertainty": "no quotation/cost item was supplied; cost is unknown, not zero"})
        for item in list_of_objects(raw_items or [], f"review_actions[{action.get('id')}].cost_items", errors):
            raw_quantity, raw_price = item.get("quantity"), item.get("unit_price")
            quantity = nonnegative_dec(raw_quantity, f"action[{action.get('id')}].cost.quantity", errors) if raw_quantity is not None else None
            price = nonnegative_dec(raw_price, f"action[{action.get('id')}].cost.unit_price", errors) if raw_price is not None else None
            item_currency = item.get("currency") if valid_id(item.get("currency")) else None
            if raw_quantity is None or raw_price is None or item_currency is None:
                cost_complete = False
                warnings.append({"code": "REVIEW_COST_UNKNOWN", "action": action.get("id"),
                                 "uncertainty": "quantity, unit price or currency is missing; unknown cost is not zero"})
            if item_currency is not None and currency is not None and item_currency != currency:
                cost_complete = False
                errors.append({"code": "CURRENCY_MISMATCH", "action": action.get("id"), "expected": currency, "actual": item_currency})
                continue
            if quantity is not None and price is not None and item_currency is not None and item_currency == currency:
                amount = quantity * price
                total_cost += amount
                formulas.append(f"{quantity} {item.get('unit')} * {price} {currency}/{item.get('unit')} = {amount} {currency}")
            else:
                cost_complete = False
        audited_actions.append({**action, "cost_formulas": formulas, "execution_status": "proposed_not_executed"})
    raw_limit_minutes = budget.get("time_limit_minutes")
    raw_limit_cost = budget.get("amount")
    limit_minutes = nonnegative_dec(raw_limit_minutes, "review_budget.time_limit_minutes", errors) if raw_limit_minutes is not None else None
    limit_cost = nonnegative_dec(raw_limit_cost, "review_budget.amount", errors) if raw_limit_cost is not None else None
    if limit_minutes is None:
        warnings.append({"code": "REVIEW_TIME_BUDGET_UNKNOWN", "uncertainty": "no known time limit; no budget conflict can be concluded"})
    if limit_cost is None:
        warnings.append({"code": "REVIEW_COST_BUDGET_UNKNOWN", "uncertainty": "no known cost limit; no budget conflict can be concluded"})
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
            "budget_known": {"time_limit_minutes": limit_minutes is not None, "amount": limit_cost is not None},
            "cost_formula": "sum(known same-currency action quantity * unit price); null total if any component is unknown",
            "budget_conflicts": conflicts}


def process(data):
    errors, warnings = [], []
    if data.get("schema") != SCHEMA:
        errors.append({"code": "SCHEMA_MISMATCH", "expected": SCHEMA})
    if not valid_id(data.get("case_id")):
        errors.append({"code": "CASE_ID_REQUIRED", "field": "case_id", "value": data.get("case_id")})
    if data.get("scenario") not in {"case_observed", "organizer_prospective", "synthetic_nonofficial"}:
        errors.append({"code": "SCENARIO_REQUIRED", "allowed": ["case_observed", "organizer_prospective", "synthetic_nonofficial"]})
    raw_records = list_of_objects(data.get("records", []), "records", errors)
    records = prepare_records(raw_records, errors, warnings)
    indexed = source_index(raw_records)
    raw_rules = list_of_objects(data.get("anomaly_rules", []), "anomaly_rules", errors)
    validate_ids(raw_rules, "anomaly_rules", errors)
    anomaly_data = dict(data)
    anomaly_data["anomaly_rules"] = raw_rules
    anomalies = anomaly_results(anomaly_data, records, errors, warnings)
    anomaly_keys = {source_key(anomaly["coordinate"].get("source")) for result in anomalies for anomaly in result["anomalies"]}
    anomaly_keys.discard(None)
    raw_causes = list_of_objects(data.get("causes", []), "causes", errors)
    validate_ids(raw_causes, "causes", errors)
    causes = []
    anomaly_by_cause = {}
    for cause_index, cause in enumerate(raw_causes):
        cause_id = cause.get("id")
        evidence_class = cause.get("evidence_class")
        if evidence_class not in EVIDENCE_RANK:
            errors.append({"code": "CAUSE_EVIDENCE_CLASS_INVALID", "cause": cause_id})
        resolved = resolve_refs(cause.get("support_refs"), f"causes[{cause_index}].support_refs", indexed, errors, anomaly_keys)
        anomaly_by_cause[cause_id] = resolved
        counterevidence = cause.get("counterevidence", [])
        if not isinstance(counterevidence, list):
            errors.append({"code": "OBJECT_LIST_REQUIRED", "at": f"causes[{cause_index}].counterevidence"})
            counterevidence = []
        for counter_index, counter in enumerate(counterevidence):
            if isinstance(counter, str):
                continue
            at = f"causes[{cause_index}].counterevidence[{counter_index}]"
            if not isinstance(counter, dict):
                errors.append({"code": "COUNTEREVIDENCE_INVALID", "at": at})
                continue
            if counter.get("linked_cause") != cause_id:
                errors.append({"code": "COUNTEREVIDENCE_CAUSE_MISMATCH", "at": at, "expected": cause_id,
                               "actual": counter.get("linked_cause")})
            resolve_refs(counter.get("source_refs"), f"{at}.source_refs", indexed, errors)
        causes.append({**cause, "causality_status": "candidate_only_unless_actually_controlled_evidence_is_supplied"})
    causes.sort(key=lambda cause: (EVIDENCE_RANK.get(cause.get("evidence_class"), 99), str(cause.get("id"))))
    review_actions = list_of_objects(data.get("review_actions", []), "review_actions", errors)
    validate_ids(review_actions, "review_actions", errors)
    review_data = dict(data)
    review_data["review_actions"] = review_actions
    if review_data.get("review_budget") is None:
        review_data["review_budget"] = {}
        warnings.append({"code": "REVIEW_BUDGET_UNKNOWN",
                         "uncertainty": "review_budget was not supplied; no budget limit is assumed"})
    elif not isinstance(review_data.get("review_budget"), dict):
        errors.append({"code": "REVIEW_BUDGET_OBJECT_REQUIRED"})
        review_data["review_budget"] = {}
    reviews = review_math(review_data, errors, indexed, anomaly_by_cause, warnings)
    raw_next = list_of_objects(data.get("next_experiments", []), "next_experiments", errors)
    next_experiments = []
    cause_ids = {cause.get("id") for cause in raw_causes}
    for index, experiment in enumerate(raw_next):
        at = f"next_experiments[{index}]"
        linked_cause = experiment.get("linked_cause")
        if linked_cause not in cause_ids:
            errors.append({"code": "NEXT_EXPERIMENT_CAUSE_UNKNOWN", "at": at, "linked_cause": linked_cause})
        if not valid_id(experiment.get("changed_variable")):
            errors.append({"code": "NEXT_EXPERIMENT_VARIABLE_REQUIRED", "at": f"{at}.changed_variable"})
        controls = experiment.get("controlled_variables")
        if not isinstance(controls, list) or not controls or not all(valid_id(control) for control in controls):
            errors.append({"code": "NEXT_EXPERIMENT_CONTROLS_REQUIRED", "at": f"{at}.controlled_variables"})
        if not valid_id(experiment.get("judgment")):
            errors.append({"code": "NEXT_EXPERIMENT_JUDGMENT_REQUIRED", "at": f"{at}.judgment"})
        allowed = anomaly_by_cause.get(linked_cause) if linked_cause in cause_ids else None
        resolve_refs(experiment.get("source_refs"), f"{at}.source_refs", indexed, errors, allowed)
        next_experiments.append({**experiment, "execution_status": "proposed_not_executed"})
    resource_unknown = any(warning.get("code", "").startswith("REVIEW_") and
                           (warning.get("code", "").endswith("_UNKNOWN")) for warning in warnings)
    status = "failed" if errors else ("partial" if resource_unknown else "pass")
    return {"auxiliary_schema": SCHEMA, "case_id": data.get("case_id"), "status": status,
            "five_part_analysis": {
                "trends": trends(records), "anomalies": anomalies,
                "possible_causes": causes,
                "recheck_steps": reviews["actions"],
                "next_experiments": next_experiments},
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
