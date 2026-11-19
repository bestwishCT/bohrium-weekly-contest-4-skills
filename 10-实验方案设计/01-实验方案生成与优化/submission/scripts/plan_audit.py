#!/usr/bin/env python3
"""Validate three versioned plans and recompute finite domains, cost and schedule."""
import argparse
import itertools
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

SCHEMA = "plan-audit-v1"
SCENARIOS = {"organizer_prospective", "synthetic_nonofficial"}


def dec(value, label, errors):
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise InvalidOperation
        return number
    except (InvalidOperation, ValueError, TypeError):
        errors.append({"code": "INVALID_NUMBER", "at": label, "value": value})
        return None


def nonnegative_dec(value, label, errors):
    number = dec(value, label, errors)
    if number is not None and number < 0:
        errors.append({"code": "NONNEGATIVE_NUMBER_REQUIRED", "at": label, "value": value})
        return None
    return number


def critical_path(steps, ref):
    errors = []
    by_id = {}
    for position, step in enumerate(steps):
        sid = str(step.get("id", ""))
        if not sid or sid in by_id:
            errors.append({"code": "STEP_ID_INVALID", "version": ref, "position": position})
            continue
        if not step.get("source"):
            errors.append({"code": "SOURCE_MISSING", "version": ref, "step": sid})
        by_id[sid] = step
    duration = {}
    for sid, step in by_id.items():
        duration[sid] = nonnegative_dec(step.get("duration_minutes"), f"{ref}.{sid}.duration_minutes", errors)
        for parent in step.get("predecessors", []):
            if parent not in by_id:
                errors.append({"code": "UNKNOWN_PREDECESSOR", "version": ref, "step": sid, "predecessor": parent})
    if any(value is None for value in duration.values()):
        return None, [], errors
    memo, active = {}, set()

    def visit(sid):
        if sid in memo:
            return memo[sid]
        if sid in active:
            raise ValueError("cycle")
        active.add(sid)
        parents = [p for p in by_id[sid].get("predecessors", []) if p in by_id]
        alternatives = [(visit(p)[0], visit(p)[1]) for p in parents]
        best = max(alternatives, key=lambda item: (item[0], item[1])) if alternatives else (Decimal(0), [])
        result = (best[0] + duration[sid], best[1] + [sid])
        active.remove(sid)
        memo[sid] = result
        return result

    try:
        paths = [visit(sid) for sid in sorted(by_id)]
        total, path = max(paths, key=lambda item: (item[0], item[1])) if paths else (Decimal(0), [])
    except ValueError:
        errors.append({"code": "STEP_CYCLE", "version": ref})
        total, path = None, []
    return total, path, errors


def audit_version(version, budget):
    ref = str(version.get("ref", ""))
    errors = [{"code": "VERSION_REF_MISSING"}] if not ref else []
    parameter_results = []
    for parameter in version.get("parameters", []):
        name = str(parameter.get("name", ""))
        source = parameter.get("source")
        if not source:
            errors.append({"code": "SOURCE_MISSING", "version": ref, "parameter": name})
        unit = parameter.get("unit")
        value = dec(parameter.get("value"), f"{ref}.{name}.value", errors)
        allowed = parameter.get("allowed", [])
        matches = []
        seen = set()
        for alternative in allowed:
            alt_value = dec(alternative.get("value"), f"{ref}.{name}.allowed", errors)
            alt_unit = alternative.get("unit")
            key = (str(alt_value), alt_unit)
            if key in seen:
                errors.append({"code": "DUPLICATE_DOMAIN_VALUE", "version": ref, "parameter": name, "value": key[0]})
            seen.add(key)
            if value == alt_value and unit == alt_unit:
                matches.append(alternative)
        if not allowed:
            errors.append({"code": "FINITE_DOMAIN_REQUIRED", "version": ref, "parameter": name})
        elif not matches:
            errors.append({"code": "OUT_OF_FINITE_DOMAIN", "version": ref, "parameter": name,
                           "actual": {"value": str(value), "unit": unit},
                           "allowed": [{"value": str(dec(a.get("value"), f"{ref}.{name}", [])), "unit": a.get("unit")} for a in allowed]})
        parameter_results.append({"name": name, "value": str(value), "unit": unit, "source": source,
                                  "finite_domain_member": bool(matches),
                                  "repair_domain": allowed if not matches else []})
    totals = {}
    formulas = []
    for item in version.get("cost_items", []):
        currency = item.get("currency")
        quantity = nonnegative_dec(item.get("quantity"), f"{ref}.{item.get('id')}.quantity", errors)
        price = nonnegative_dec(item.get("unit_price"), f"{ref}.{item.get('id')}.unit_price", errors)
        if not item.get("source"):
            errors.append({"code": "SOURCE_MISSING", "version": ref, "cost_item": item.get("id")})
            continue
        if quantity is None or price is None:
            continue
        amount = quantity * price
        totals[currency] = totals.get(currency, Decimal(0)) + amount
        formulas.append({"id": item.get("id"), "quantity": str(quantity), "unit": item.get("unit"),
                         "unit_price": str(price), "currency": currency, "amount": str(amount),
                         "formula": f"{quantity} {item.get('unit')} * {price} {currency}/{item.get('unit')} = {amount} {currency}"})
    if not formulas:
        errors.append({"code": "COST_ITEMS_REQUIRED", "version": ref})
    expected_currency = budget.get("currency")
    known_subtotal = totals.get(expected_currency)
    foreign = sorted(str(currency) for currency in totals if currency != expected_currency)
    if foreign:
        errors.append({"code": "CURRENCY_MISMATCH", "version": ref, "expected": expected_currency,
                       "unexpected": foreign, "action": "obtain an explicit sourced conversion; do not add currencies"})
    total_minutes, path, step_errors = critical_path(version.get("steps", []), ref)
    errors.extend(step_errors)
    budget_amount = nonnegative_dec(budget.get("amount"), "budget.amount", errors)
    time_limit = nonnegative_dec(budget.get("time_limit_minutes"), "budget.time_limit_minutes", errors) if budget.get("time_limit_minutes") is not None else None
    declared = version.get("declared_total")
    if declared is not None and known_subtotal is not None:
        declared_value = nonnegative_dec(declared, f"{ref}.declared_total", errors)
        if declared_value is not None and declared_value != known_subtotal:
            errors.append({"code": "DECLARED_COST_MISMATCH", "version": ref,
                           "declared": str(declared_value), "computed": str(known_subtotal)})
    if known_subtotal is not None and budget_amount is not None:
        if known_subtotal > budget_amount:
            errors.append({"code": "BUDGET_CONFLICT", "version": ref,
                           "computed": str(known_subtotal), "limit": str(budget_amount), "currency": expected_currency})
    if total_minutes is not None and time_limit is not None:
        if total_minutes > time_limit:
            errors.append({"code": "TIME_WINDOW_CONFLICT", "version": ref,
                           "critical_path_minutes": str(total_minutes), "limit_minutes": str(time_limit)})
    return {"ref": ref, "errors": errors, "parameters": parameter_results, "cost_totals_by_currency": {str(k): str(v) for k, v in sorted(totals.items(), key=lambda pair: str(pair[0]))},
            "known_budget_currency_subtotal": str(known_subtotal) if known_subtotal is not None else None,
            "cost_formulas": formulas, "critical_path": {"minutes": str(total_minutes) if total_minutes is not None else None, "steps": path}}


def process(data):
    errors = []
    if data.get("schema") != SCHEMA:
        errors.append({"code": "SCHEMA_MISMATCH", "expected": SCHEMA})
    if data.get("scenario") not in SCENARIOS:
        errors.append({"code": "SCENARIO_REQUIRED", "allowed": sorted(SCENARIOS)})
    versions = data.get("versions", [])
    if len(versions) != 3:
        errors.append({"code": "THREE_VERSIONS_REQUIRED", "actual": len(versions)})
    audits = [audit_version(version, data.get("budget", {})) for version in versions]
    baseline_ref = data.get("baseline_ref")
    selected = next((a for a in audits if a["ref"] == baseline_ref and not a["errors"]), None)
    if baseline_ref not in {a["ref"] for a in audits}:
        errors.append({"code": "BASELINE_NOT_FOUND", "baseline_ref": baseline_ref})
    elif selected is None:
        errors.append({"code": "BASELINE_BLOCKED", "baseline_ref": baseline_ref})
    return {"auxiliary_schema": SCHEMA, "case_id": data.get("case_id"), "scenario": data.get("scenario"),
            "status": "pass" if not errors and all(not a["errors"] for a in audits) else "blocked",
            "status_scope": "deterministic checks only; not wet-lab, booking, quotation or approval validation",
            "selected_baseline": baseline_ref if selected else None,
            "version_audits": audits, "global_errors": errors,
            "change_record_rule": "Record old/new ref, exact changed fields, source, constraint impact and rerun; never merge versions."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    try:
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
        result = process(data)
        code = 0 if result["status"] == "pass" else 2
    except (OSError, json.JSONDecodeError, TypeError, KeyError, AttributeError) as exc:
        result = {"auxiliary_schema": SCHEMA, "status": "error", "errors": [{"code": "INPUT_OR_RUNTIME_ERROR", "message": str(exc)}]}
        code = 2
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{result['status']}: {output}; exit={code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
