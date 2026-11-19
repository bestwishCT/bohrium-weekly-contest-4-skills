#!/usr/bin/env python3
"""Align three versioned plans, recompute costs/schedules and gate selection."""
import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

SCHEMA = "aligned-plan-comparison-v1"
FIVE_FIELDS = {"objective", "risk", "cost", "feasibility", "evidence"}


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
    for item in plan.get("cost_items", []):
        quantity = nonnegative_dec(item.get("quantity"), f"{ref}.{item.get('id')}.quantity", errors)
        price_data = item.get("unit_price", {})
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
    for path in plan.get("paths", []):
        subtotal, complete = Decimal(0), True
        terms = []
        for component in path.get("components", []):
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
    duration = max(known, key=lambda item: (item[0], item[1])) if len(known) == len(paths) and known else None
    critical = {"days": str(duration[0]) if duration else None, "path": duration[1] if duration else None,
                "formula": "max(sum(component days) for each declared path); wait/procurement paths are not hidden"}
    return (duration[0] if duration else None), paths, critical


def audit_plan(plan, data, criteria):
    ref = str(plan.get("ref", ""))
    errors = [{"code": "VERSION_REF_MISSING"}] if not ref else []
    if plan.get("scenario") not in {"organizer_prospective", "synthetic_nonofficial"}:
        errors.append({"code": "SCENARIO_REQUIRED", "version": ref})
    for field in sorted(FIVE_FIELDS - set(plan.get("fields", {}))):
        errors.append({"code": "FIVE_DIMENSION_MISSING", "version": ref, "field": field})
    for field, record in plan.get("fields", {}).items():
        if field in FIVE_FIELDS and not record.get("source"):
            errors.append({"code": "SOURCE_MISSING", "version": ref, "field": field})
    fail_reasons, unknown_reasons = [], []
    for check in plan.get("hard_checks", []):
        if check.get("status") not in {"pass", "fail", "unknown"} or not check.get("source"):
            errors.append({"code": "HARD_CHECK_INVALID", "version": ref, "check": check.get("id")})
        if check.get("status") == "fail":
            fail_reasons.append({"id": check.get("id"), "source": check.get("source"), "detail": check.get("detail")})
        if check.get("status") == "unknown":
            unknown_reasons.append({"id": check.get("id"), "source": check.get("source"), "missing": check.get("missing")})
    cost, cost_items = compute_cost(plan, ref, data.get("cost_currency"), errors)
    duration, paths, critical = compute_time(plan, ref, data.get("time_conversions", {}), errors)
    values = {"cost_total": cost, "duration_days": duration}
    metric_results = []
    for criterion in criteria:
        name = criterion.get("name")
        if name in values:
            if values[name] is None:
                errors.append({"code": "DERIVED_METRIC_INCOMPLETE", "version": ref, "criterion": name})
            metric_results.append({"name": name, "value": str(values[name]) if values[name] is not None else None, "unit": criterion.get("unit"), "direction": criterion.get("direction")})
            continue
        record = plan.get("metrics", {}).get(name)
        if record is None:
            errors.append({"code": "CRITERION_MISSING", "version": ref, "criterion": name})
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
    return {"ref": ref, "scenario": plan.get("scenario"), "fields": plan.get("fields"), "hard_gate": gate,
            "hard_failures": fail_reasons, "uncertain_hard_checks": unknown_reasons, "errors": errors,
            "cost_total": str(cost) if cost is not None else None, "currency": data.get("cost_currency"), "cost_items": cost_items,
            "duration_days": str(duration) if duration is not None else None, "time_paths": paths, "critical_path": critical,
            "criteria_values": metric_results, "actual_values_for_comparison": {key: str(value) if value is not None else None for key, value in values.items()},
            "switch_trigger": plan.get("switch_trigger")}


def compare(data):
    errors = []
    if data.get("schema") != SCHEMA:
        errors.append({"code": "SCHEMA_MISMATCH", "expected": SCHEMA})
    plans = data.get("plans", [])
    if len(plans) != 3:
        errors.append({"code": "THREE_VERSIONED_PLANS_REQUIRED", "actual": len(plans)})
    criteria = data.get("criteria", [])
    if not criteria:
        errors.append({"code": "CRITERIA_REQUIRED"})
    for criterion in criteria:
        if criterion.get("direction") not in {"min", "max"}:
            errors.append({"code": "CRITERION_DIRECTION_INVALID", "criterion": criterion.get("name")})
    conversion = data.get("time_conversions", {}).get("hours_per_day")
    if conversion is not None:
        factor = dec(conversion, "time_conversions.hours_per_day", errors)
        if factor is not None and factor <= 0:
            errors.append({"code": "TIME_CONVERSION_INVALID", "at": "time_conversions.hours_per_day", "value": conversion})
    audits = [audit_plan(plan, data, criteria) for plan in plans]
    lookup = {audit["ref"]: audit["actual_values_for_comparison"] for audit in audits}

    def dominates(left, right):
        any_strict = False
        for criterion in criteria:
            name, direction = criterion.get("name"), criterion.get("direction")
            lval, rval = lookup[left][name], lookup[right][name]
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

    eligible = [audit["ref"] for audit in audits if audit["hard_gate"] == "eligible"]
    for index, left in enumerate(eligible):
        for right in eligible[index + 1:]:
            try:
                dominates(left, right)
            except (InvalidOperation, ValueError, TypeError):
                errors.append({"code": "CRITERION_VALUE_INVALID", "left": left, "right": right})
    selected = None
    if eligible and not errors and not any(audit["hard_gate"] == "indeterminate" for audit in audits):
        unique_dominant = [ref for ref in eligible if all(ref == other or dominates(ref, other) for other in eligible)]
        if len(unique_dominant) == 1:
            selected = unique_dominant[0]
    not_selected = []
    for audit in audits:
        if audit["ref"] == selected:
            continue
        if audit["hard_gate"] == "eliminated":
            reason = "hard gate failed; lower cost or other metric cannot compensate"
        elif audit["hard_gate"] == "indeterminate":
            reason = "hard evidence, cost, time, units or criteria are incomplete; not treated as zero or satisfied"
        elif selected and dominates(selected, audit["ref"]):
            reason = "selected plan weakly dominates on every declared criterion and strictly on at least one"
        else:
            reason = "non-dominated or incomparable; task owner must supply an explicit preference or verified evidence"
        not_selected.append({"ref": audit["ref"], "reason": reason, "switch_trigger": audit.get("switch_trigger")})
    for audit in audits:
        errors.extend(audit["errors"])
    if not selected:
        errors.append({"code": "NO_UNIQUE_DEFENSIBLE_SELECTION", "reason": "no eligible unique dominant plan or unresolved comparison evidence"})
    sensitivity = []
    if selected:
        base = lookup[selected]
        for audit in audits:
            if audit["ref"] == selected:
                continue
            differences = {}
            for criterion in criteria:
                name = criterion.get("name")
                if base.get(name) is not None and audit["actual_values_for_comparison"].get(name) is not None:
                    differences[name] = str(Decimal(audit["actual_values_for_comparison"][name]) - Decimal(base[name]))
            sensitivity.append({"alternative": audit["ref"], "alternative_minus_selected": differences,
                                "interpretation": "observed arithmetic gaps only; no unprovided critical threshold or confidence level"})
    valid = not errors
    return {"auxiliary_schema": SCHEMA, "case_id": data.get("case_id"), "status": "pass" if selected and valid else "no_recommendation",
            "selected_plan": selected, "version_audits": audits, "not_selected": not_selected,
            "eligible_plans": eligible, "sensitivity": sensitivity, "global_errors": errors,
            "selection_rule": "Hard failures/unknowns precede unique dominance on case-declared criteria; no invented weights or aggregate score.",
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
