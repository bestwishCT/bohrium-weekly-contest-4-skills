#!/usr/bin/env python3
"""Align three versioned plans, recompute costs/schedules and gate selection."""
import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

SCHEMA = "aligned-plan-comparison-v1"
FIVE_FIELDS = {"objective", "risk", "cost", "feasibility", "evidence"}


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


def validate_ids(items, at, errors, key="id"):
    seen = {}
    for index, item in enumerate(items):
        value = item.get(key)
        location = f"{at}[{index}].{key}"
        if not valid_id(value):
            errors.append({"code": "ID_MISSING_OR_INVALID", "at": location, "value": value})
            continue
        if value in seen:
            errors.append({"code": "DUPLICATE_ID", "at": location, "id": value, "first_index": seen[value], "duplicate_index": index})
        else:
            seen[value] = index


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


def compute_cost(plan, ref, currency, errors):
    items, total, complete = [], Decimal(0), True
    raw_items = list_of_objects(plan.get("cost_items", []), f"{ref}.cost_items", errors)
    validate_ids(raw_items, f"{ref}.cost_items", errors)
    for item in raw_items:
        quantity = nonnegative_dec(item.get("quantity"), f"{ref}.{item.get('id')}.quantity", errors)
        price_data = item.get("unit_price", {})
        if not isinstance(price_data, dict):
            complete = False
            errors.append({"code": "UNIT_PRICE_OBJECT_REQUIRED", "version": ref, "item": item.get("id")})
            price_data = {}
        price = nonnegative_dec(price_data.get("value"), f"{ref}.{item.get('id')}.unit_price", errors)
        item_currency = price_data.get("currency")
        formula = None
        if quantity is not None and price is not None:
            amount = quantity * price
            formula = f"{quantity} {item.get('unit')} * {price} {item_currency}/{price_data.get('per_unit')} = {amount} {item_currency}"
            if item_currency == currency:
                total += amount
            else:
                complete = False
                errors.append({"code": "CURRENCY_MISMATCH", "version": ref, "item": item.get("id"), "expected": currency, "actual": item_currency})
        else:
            complete = False
        if item.get("unit") != price_data.get("per_unit"):
            complete = False
            errors.append({"code": "COST_UNIT_MISMATCH", "version": ref, "item": item.get("id"), "quantity_unit": item.get("unit"), "price_denominator": price_data.get("per_unit")})
        items.append({"id": item.get("id"), "formula": formula, "source": item.get("source"), "prospective": True})
        if not item.get("source"):
            errors.append({"code": "SOURCE_MISSING", "version": ref, "cost_item": item.get("id")})
    if not items:
        complete = False
        errors.append({"code": "COST_ITEMS_REQUIRED", "version": ref})
    declared = plan.get("declared_cost")
    if declared is not None and complete:
        stated = nonnegative_dec(declared, f"{ref}.declared_cost", errors)
        if stated is not None and stated != total:
            errors.append({"code": "DECLARED_COST_MISMATCH", "version": ref, "declared": str(stated), "computed": str(total)})
    return (total if complete else None), items


def compute_time(plan, ref, conversions, errors):
    paths = []
    raw_paths = list_of_objects(plan.get("paths", []), f"{ref}.paths", errors)
    validate_ids(raw_paths, f"{ref}.paths", errors)
    for path_index, path in enumerate(raw_paths):
        subtotal, complete = Decimal(0), True
        terms = []
        components = list_of_objects(path.get("components", []), f"{ref}.paths[{path_index}].components", errors)
        for component in components:
            value = nonnegative_dec(component.get("value"), f"{ref}.{path.get('id')}.duration", errors)
            unit = component.get("unit")
            if value is None:
                complete = False
                continue
            if unit == "day":
                days = value
            elif unit == "hour" and conversions.get("hours_per_day") is not None:
                factor = dec(conversions.get("hours_per_day"), "time_conversions.hours_per_day", errors)
                if factor is None or factor <= 0:
                    complete = False
                    errors.append({"code": "TIME_CONVERSION_INVALID", "version": ref})
                    continue
                days = value / factor
            else:
                complete = False
                errors.append({"code": "TIME_UNIT_OR_CONVERSION_MISMATCH", "version": ref, "path": path.get("id"), "unit": unit,
                               "required": "day or explicit hours_per_day"})
                continue
            subtotal += days
            terms.append(f"{value} {unit}={days} day")
        paths.append({"id": path.get("id"), "days": str(subtotal) if complete else None, "terms": terms})
    known = [(Decimal(p["days"]), p["id"]) for p in paths if p["days"] is not None]
    if not paths:
        errors.append({"code": "TIME_PATHS_REQUIRED", "version": ref})
    duration = max(known, key=lambda item: (item[0], str(item[1]))) if len(known) == len(paths) and known else None
    critical = {"days": str(duration[0]) if duration else None, "path": duration[1] if duration else None,
                "formula": "max(sum(component days) for each declared path); wait/procurement paths are not hidden"}
    return (duration[0] if duration else None), paths, critical


def audit_plan(plan, data, criteria, input_index):
    raw_ref = plan.get("ref")
    ref = raw_ref if isinstance(raw_ref, str) else ""
    errors = [{"code": "VERSION_REF_MISSING", "input_index": input_index}] if not valid_id(raw_ref) else []
    if plan.get("scenario") not in {"organizer_prospective", "synthetic_nonofficial"}:
        errors.append({"code": "SCENARIO_REQUIRED", "version": ref, "input_index": input_index})
    fields = plan.get("fields", {})
    if not isinstance(fields, dict):
        errors.append({"code": "FIVE_DIMENSIONS_OBJECT_REQUIRED", "version": ref, "input_index": input_index})
        fields = {}
    for field in sorted(FIVE_FIELDS - set(fields)):
        errors.append({"code": "FIVE_DIMENSION_MISSING", "version": ref, "field": field})
    for field, record in fields.items():
        if field in FIVE_FIELDS and (not isinstance(record, dict) or not record.get("source")):
            errors.append({"code": "SOURCE_MISSING", "version": ref, "field": field})
    fail_reasons, unknown_reasons = [], []
    hard_checks = list_of_objects(plan.get("hard_checks", []), f"{ref}.hard_checks", errors)
    validate_ids(hard_checks, f"{ref}.hard_checks", errors)
    for check in hard_checks:
        if check.get("status") not in {"pass", "fail", "unknown"} or not check.get("source"):
            errors.append({"code": "HARD_CHECK_INVALID", "version": ref, "check": check.get("id")})
        if check.get("status") == "fail":
            fail_reasons.append({"id": check.get("id"), "source": check.get("source"), "detail": check.get("detail")})
        if check.get("status") == "unknown":
            unknown_reasons.append({"id": check.get("id"), "source": check.get("source"), "missing": check.get("missing")})
    cost, cost_items = compute_cost(plan, ref, data.get("cost_currency"), errors)
    conversions = data.get("time_conversions", {})
    if not isinstance(conversions, dict):
        errors.append({"code": "TIME_CONVERSIONS_OBJECT_REQUIRED", "version": ref})
        conversions = {}
    duration, paths, critical = compute_time(plan, ref, conversions, errors)
    values = {"cost_total": cost, "duration_days": duration}
    metric_results = []
    metrics = plan.get("metrics", {})
    if not isinstance(metrics, dict):
        errors.append({"code": "METRICS_OBJECT_REQUIRED", "version": ref})
        metrics = {}
    for criterion in criteria:
        name = criterion.get("name")
        if name in values:
            if values[name] is None:
                errors.append({"code": "DERIVED_METRIC_INCOMPLETE", "version": ref, "criterion": name})
            metric_results.append({"name": name, "value": str(values[name]) if values[name] is not None else None, "unit": criterion.get("unit"), "direction": criterion.get("direction")})
            continue
        record = metrics.get(name)
        if record is None:
            errors.append({"code": "CRITERION_MISSING", "version": ref, "criterion": name})
            values[name] = None
            continue
        if not isinstance(record, dict):
            errors.append({"code": "CRITERION_RECORD_INVALID", "version": ref, "criterion": name})
            values[name] = None
            continue
        if record.get("unit") != criterion.get("unit"):
            errors.append({"code": "CRITERION_UNIT_MISMATCH", "version": ref, "criterion": name, "expected": criterion.get("unit"), "actual": record.get("unit")})
        value = dec(record.get("value"), f"{ref}.{name}", errors)
        values[name] = value
        if not record.get("source"):
            errors.append({"code": "SOURCE_MISSING", "version": ref, "criterion": name})
        metric_results.append({"name": name, "value": str(value), "unit": record.get("unit"), "direction": criterion.get("direction"), "source": record.get("source")})
    if fail_reasons:
        gate = "eliminated"
    elif unknown_reasons or errors:
        gate = "indeterminate"
    else:
        gate = "eligible"
    return {"ref": ref, "input_index": input_index, "scenario": plan.get("scenario"), "fields": plan.get("fields"), "hard_gate": gate,
            "hard_failures": fail_reasons, "uncertain_hard_checks": unknown_reasons, "errors": errors,
            "cost_total": str(cost) if cost is not None else None, "currency": data.get("cost_currency"), "cost_items": cost_items,
            "duration_days": str(duration) if duration is not None else None, "time_paths": paths, "critical_path": critical,
            "criteria_values": metric_results, "actual_values_for_comparison": {key: str(value) if value is not None else None for key, value in values.items()},
            "switch_trigger": plan.get("switch_trigger")}


def compare(data):
    errors = []
    if data.get("schema") != SCHEMA:
        errors.append({"code": "SCHEMA_MISMATCH", "expected": SCHEMA})
    if not valid_id(data.get("case_id")):
        errors.append({"code": "CASE_ID_REQUIRED", "field": "case_id", "value": data.get("case_id")})
    raw_plans = data.get("plans", [])
    if not isinstance(raw_plans, list):
        errors.append({"code": "OBJECT_LIST_REQUIRED", "at": "plans"})
        raw_plans = []
    if len(raw_plans) != 3:
        errors.append({"code": "THREE_VERSIONED_PLANS_REQUIRED", "actual": len(raw_plans)})
    plans = []
    for index, plan in enumerate(raw_plans):
        if not isinstance(plan, dict):
            errors.append({"code": "OBJECT_LIST_ITEM_INVALID", "at": "plans", "index": index})
            plan = {}
        plans.append(plan)
    validate_ids(plans, "plans", errors, key="ref")
    criteria = list_of_objects(data.get("criteria", []), "criteria", errors)
    validate_ids(criteria, "criteria", errors, key="name")
    if not criteria:
        errors.append({"code": "CRITERIA_REQUIRED"})
    for criterion in criteria:
        if criterion.get("direction") not in {"min", "max"}:
            errors.append({"code": "CRITERION_DIRECTION_INVALID", "criterion": criterion.get("name")})
    priority_requested = any("priority" in criterion or "priority_source" in criterion for criterion in criteria)
    priority_order = []
    if priority_requested:
        seen_priorities = {}
        for criterion in criteria:
            name = criterion.get("name")
            priority = criterion.get("priority")
            if isinstance(priority, bool) or not isinstance(priority, int) or priority < 1:
                errors.append({"code": "CRITERION_PRIORITY_INVALID", "criterion": name, "value": priority})
                continue
            if not valid_id(criterion.get("priority_source")):
                errors.append({"code": "CRITERION_PRIORITY_SOURCE_REQUIRED", "criterion": name})
            if priority in seen_priorities:
                errors.append({"code": "DUPLICATE_CRITERION_PRIORITY", "criterion": name,
                               "other_criterion": seen_priorities[priority], "priority": priority})
            else:
                seen_priorities[priority] = name
            priority_order.append(criterion)
        if len(priority_order) != len(criteria):
            errors.append({"code": "CRITERION_PRIORITIES_INCOMPLETE",
                           "reason": "either every declared criterion has a sourced unique priority or none does"})
        priority_order.sort(key=lambda criterion: criterion["priority"])
    conversions = data.get("time_conversions", {})
    if not isinstance(conversions, dict):
        errors.append({"code": "TIME_CONVERSIONS_OBJECT_REQUIRED"})
        conversions = {}
    conversion = conversions.get("hours_per_day")
    if conversion is not None:
        factor = dec(conversion, "time_conversions.hours_per_day", errors)
        if factor is not None and factor <= 0:
            errors.append({"code": "TIME_CONVERSION_INVALID", "at": "time_conversions.hours_per_day", "value": conversion})
    audits = [audit_plan(plan, data, criteria, index) for index, plan in enumerate(plans)]

    def dominates(left, right):
        any_strict = False
        for criterion in criteria:
            name, direction = criterion.get("name"), criterion.get("direction")
            lval = audits[left]["actual_values_for_comparison"].get(name)
            rval = audits[right]["actual_values_for_comparison"].get(name)
            if lval is None or rval is None:
                return False
            a, b = Decimal(lval), Decimal(rval)
            if direction == "min":
                if a > b:
                    return False
                any_strict |= a < b
            elif direction == "max":
                if a < b:
                    return False
                any_strict |= a > b
            else:
                return False
        return any_strict

    eligible_indices = [index for index, audit in enumerate(audits) if audit["hard_gate"] == "eligible"]
    for position, left in enumerate(eligible_indices):
        for right in eligible_indices[position + 1:]:
            try:
                dominates(left, right)
            except (InvalidOperation, ValueError, TypeError):
                errors.append({"code": "CRITERION_VALUE_INVALID", "left_index": left, "right_index": right})
    selected_index = None
    comparison_ready = eligible_indices and not errors and not any(
        audit["errors"] or audit["hard_gate"] == "indeterminate" for audit in audits)
    if comparison_ready and priority_order:
        def lexicographic_key(index):
            key = []
            for criterion in priority_order:
                value = Decimal(audits[index]["actual_values_for_comparison"][criterion["name"]])
                key.append(-value if criterion["direction"] == "max" else value)
            return tuple(key)
        try:
            ranked = sorted(eligible_indices, key=lexicographic_key)
            if len(ranked) == 1 or lexicographic_key(ranked[0]) != lexicographic_key(ranked[1]):
                selected_index = ranked[0]
        except (InvalidOperation, ValueError, TypeError, KeyError) as exc:
            errors.append({"code": "CRITERION_VALUE_INVALID", "message": str(exc)})
    elif comparison_ready:
        unique_dominant = [index for index in eligible_indices
                           if all(index == other or dominates(index, other) for other in eligible_indices)]
        if len(unique_dominant) == 1:
            selected_index = unique_dominant[0]
    selected = audits[selected_index]["ref"] if selected_index is not None else None
    not_selected = []
    for index, audit in enumerate(audits):
        if index == selected_index:
            continue
        if audit["hard_gate"] == "eliminated":
            reason = "hard gate failed; lower cost or other metric cannot compensate"
        elif audit["hard_gate"] == "indeterminate":
            reason = "hard evidence, cost, time, units or criteria are incomplete; not treated as zero or satisfied"
        elif selected_index is not None and priority_order:
            reason = "selected by sourced lexicographic criteria priority; no aggregate score or invented weight"
        elif selected_index is not None and dominates(selected_index, index):
            reason = "selected plan weakly dominates on every declared criterion and strictly on at least one"
        else:
            reason = "non-dominated or incomparable; task owner must supply an explicit preference or verified evidence"
        not_selected.append({"ref": audit["ref"], "input_index": index, "reason": reason, "switch_trigger": audit.get("switch_trigger")})
    for audit in audits:
        errors.extend(audit["errors"])
    if not selected:
        errors.append({"code": "NO_UNIQUE_DEFENSIBLE_SELECTION",
                       "reason": "no unique selection under sourced priorities or unique dominance, or unresolved comparison evidence"})
    sensitivity = []
    if selected_index is not None:
        base = audits[selected_index]["actual_values_for_comparison"]
        for index, audit in enumerate(audits):
            if index == selected_index:
                continue
            differences = {}
            for criterion in criteria:
                name = criterion.get("name")
                if base.get(name) is not None and audit["actual_values_for_comparison"].get(name) is not None:
                    differences[name] = str(Decimal(audit["actual_values_for_comparison"][name]) - Decimal(base[name]))
            sensitivity.append({"alternative": audit["ref"], "alternative_input_index": index, "alternative_minus_selected": differences,
                                "interpretation": "observed arithmetic gaps only; no unprovided critical threshold or confidence level"})
    valid = not errors
    return {"auxiliary_schema": SCHEMA, "case_id": data.get("case_id"), "status": "pass" if selected and valid else "no_recommendation",
            "selected_plan": selected, "version_audits": audits, "not_selected": not_selected,
            "eligible_plans": [audits[index]["ref"] for index in eligible_indices], "sensitivity": sensitivity, "global_errors": errors,
            "criteria_priority": [{"name": criterion.get("name"), "priority": criterion.get("priority"),
                                   "source": criterion.get("priority_source")} for criterion in priority_order],
            "selection_rule": ("Hard failures/unknowns precede sourced lexicographic criteria priority; no invented weights or aggregate score."
                               if priority_order else
                               "Hard failures/unknowns precede unique dominance on case-declared criteria; no invented weights or aggregate score."),
            "scope": "Prospective or synthetic comparison only; no real experiment, supplier, booking or approval claim."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    try:
        result = compare(json.loads(Path(args.input).read_text(encoding="utf-8")))
        code = 0 if result["status"] == "pass" else 2
    except (OSError, json.JSONDecodeError, TypeError, KeyError, AttributeError, ValueError) as exc:
        result = {"auxiliary_schema": SCHEMA, "status": "error", "global_errors": [{"code": "INPUT_OR_RUNTIME_ERROR", "message": str(exc)}]}
        code = 2
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{result['status']}: {output}; exit={code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
