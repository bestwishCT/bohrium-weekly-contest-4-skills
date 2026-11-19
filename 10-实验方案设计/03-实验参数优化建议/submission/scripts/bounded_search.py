#!/usr/bin/env python3
"""Enumerate bounded candidates per plan version; audit cost and Pareto tradeoffs."""
import argparse
import itertools
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

SCHEMA = "bounded-search-v1"


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


def positive_int(value, at, errors):
    number = dec(value, at, errors)
    if number is not None and (number <= 0 or number != number.to_integral_value()):
        errors.append({"code": "POSITIVE_INTEGER_REQUIRED", "at": at, "value": value})
        return None
    return number


def nonnegative_int(value, at, errors):
    number = dec(value, at, errors)
    if number is not None and (number < 0 or number != number.to_integral_value()):
        errors.append({"code": "NONNEGATIVE_INTEGER_REQUIRED", "at": at, "value": value})
        return None
    return number


def point_key(point):
    return ";".join(f"{name}={point[name]['value']} {point[name]['unit']}" for name in sorted(point))


def plain_point(point):
    return {name: {"value": str(value["value"]), "unit": value["unit"]} for name, value in sorted(point.items())}


def prepare_domains(parameters, ref, errors):
    domains = []
    for parameter in parameters:
        name = parameter.get("name")
        allowed, seen = [], set()
        for entry in parameter.get("allowed_values", []):
            value = dec(entry.get("value"), f"{ref}.{name}.allowed", errors)
            unit = entry.get("unit")
            key = (str(value), unit)
            if key in seen:
                errors.append({"code": "DUPLICATE_DOMAIN_VALUE", "version": ref, "parameter": name})
            seen.add(key)
            allowed.append({"value": value, "unit": unit})
        if not allowed:
            errors.append({"code": "EMPTY_FINITE_DOMAIN", "version": ref, "parameter": name})
        domains.append((name, sorted(allowed, key=lambda entry: (entry["value"] if entry["value"] is not None else Decimal(0), str(entry["unit"])))))
    return domains


def point_cost(point, version, resources):
    currency = version.get("currency")
    fixed = resources["fixed_cost"]
    per_replicate = resources["per_replicate_cost"]
    replicates = resources["replicates"]
    setup = resources["setup_minutes"]
    per_minutes = resources["minutes_per_replicate"]
    terms, component_cost = [], Decimal(0)
    for name, coefficient_value in sorted(resources["parameter_costs"].items()):
        if name not in point or coefficient_value is None:
            continue
        amount = coefficient_value * point[name]["value"]
        component_cost += amount
        terms.append(f"{coefficient_value} * {point[name]['value']}={amount}")
    cost = fixed + per_replicate * replicates + component_cost
    minutes = setup + per_minutes * replicates
    formulas = [f"fixed={fixed}", f"replicate cost={per_replicate}*{replicates}={per_replicate * replicates}"] + terms + [f"total={cost} {currency}"]
    time_formula = [f"minutes={setup}+{per_minutes}*{replicates}={minutes}"]
    return cost, minutes, formulas, time_formula


def evaluate(version, global_data):
    ref = str(version.get("ref", ""))
    errors = [{"code": "VERSION_REF_MISSING"}] if not ref else []
    for key in ("domain", "baseline", "cost", "schedule"):
        if not version.get("source_refs", {}).get(key):
            errors.append({"code": "SOURCE_MISSING", "version": ref, "aspect": key})
    if version.get("scenario") not in {"organizer_prospective", "synthetic_nonofficial"}:
        errors.append({"code": "SCENARIO_REQUIRED", "version": ref})
    domains = prepare_domains(version.get("parameters", []), ref, errors)
    names = [name for name, values in domains]
    baseline = {}
    for item in version.get("baseline", []):
        name = item.get("name")
        value = dec(item.get("value"), f"{ref}.baseline.{name}", errors)
        if name not in names:
            errors.append({"code": "BASELINE_PARAMETER_UNKNOWN", "version": ref, "parameter": name})
            continue
        allowed = next(values for n, values in domains if n == name)
        if not any(value == entry["value"] and item.get("unit") == entry["unit"] for entry in allowed):
            errors.append({"code": "BASELINE_UNIT_OR_DOMAIN_MISMATCH", "version": ref, "parameter": name,
                           "actual": {"value": str(value), "unit": item.get("unit")},
                           "allowed": [{"value": str(entry["value"]), "unit": entry["unit"]} for entry in allowed]})
        baseline[name] = {"value": value, "unit": item.get("unit")}
    def blocked_search():
        return {"ref": ref, "errors": errors, "baseline": plain_point(baseline), "candidates": [], "pareto": [], "next_batch": [], "stop_state": "search_blocked"}

    domains_valid = all(entry["value"] is not None for name, values in domains for entry in values)
    constraints = []
    constraints_valid = True
    for constraint in version.get("constraints", []):
        terms = {}
        for name, coefficient in constraint.get("terms", {}).items():
            if name not in names:
                errors.append({"code": "CONSTRAINT_PARAMETER_UNKNOWN", "version": ref, "parameter": name})
                constraints_valid = False
            value = dec(coefficient, f"{ref}.constraint.{name}", errors)
            terms[name] = value
            constraints_valid &= value is not None
        if constraint.get("op") not in {"<=", ">=", "=="}:
            errors.append({"code": "CONSTRAINT_OPERATOR_INVALID", "version": ref, "operator": constraint.get("op")})
            constraints_valid = False
        limit_value = dec(constraint.get("limit"), f"{ref}.constraint.limit", errors)
        constraints_valid &= limit_value is not None
        constraints.append({"terms": terms, "op": constraint.get("op"), "limit": limit_value, "source": constraint.get("source")})

    resources = {
        "fixed_cost": nonnegative_dec(version.get("fixed_cost"), f"{ref}.fixed_cost", errors),
        "per_replicate_cost": nonnegative_dec(version.get("per_replicate_cost"), f"{ref}.per_replicate_cost", errors),
        "replicates": positive_int(version.get("replicates"), f"{ref}.replicates", errors),
        "setup_minutes": nonnegative_dec(version.get("setup_minutes"), f"{ref}.setup_minutes", errors),
        "minutes_per_replicate": nonnegative_dec(version.get("minutes_per_replicate"), f"{ref}.minutes_per_replicate", errors),
        "parameter_costs": {}
    }
    cost_parameters_known = True
    for name, coefficient in version.get("parameter_costs", {}).items():
        if name not in names:
            errors.append({"code": "COST_PARAMETER_UNKNOWN", "version": ref, "parameter": name})
            cost_parameters_known = False
        resources["parameter_costs"][name] = nonnegative_dec(coefficient, f"{ref}.parameter_costs.{name}", errors)
    if version.get("currency") != global_data.get("budget", {}).get("currency"):
        errors.append({"code": "CURRENCY_MISMATCH", "version": ref, "expected": global_data.get("budget", {}).get("currency"), "actual": version.get("currency")})

    limit_count = positive_int(global_data.get("enumeration_limit", 4096), "enumeration_limit", errors)
    batch_size = nonnegative_int(global_data.get("max_batch_size", 1), "max_batch_size", errors)
    batch_limit = nonnegative_dec(global_data.get("batch_cost_limit"), "batch_cost_limit", errors) if global_data.get("batch_cost_limit") is not None else None
    batch_limit_valid = global_data.get("batch_cost_limit") is None or batch_limit is not None
    budget = global_data.get("budget", {})
    budget_amount = nonnegative_dec(budget.get("amount"), "budget.amount", errors)
    budget_minutes = nonnegative_dec(budget.get("time_limit_minutes"), "budget.time_limit_minutes", errors)
    expected_count = 1
    for name, values in domains:
        expected_count *= len(values)
    if limit_count is not None and (expected_count > limit_count or limit_count > 100000):
        errors.append({"code": "ENUMERATION_LIMIT_EXCEEDED", "version": ref, "points": expected_count, "limit": str(limit_count)})
        return blocked_search()
    resources_complete = all(resources[key] is not None for key in ("fixed_cost", "per_replicate_cost", "replicates", "setup_minutes", "minutes_per_replicate"))
    resources_complete &= all(value is not None for value in resources["parameter_costs"].values())
    if not all((domains_valid, constraints_valid, cost_parameters_known, resources_complete, batch_limit_valid)) or None in (limit_count, batch_size, budget_amount, budget_minutes):
        return blocked_search()

    responses = global_data.get("responses", [])
    observations = version.get("observations", [])
    candidates = []
    for values in itertools.product(*(allowed for name, allowed in domains)):
        point = {name: value for (name, allowed), value in zip(domains, values)}
        reasons = []
        for constraint in constraints:
            lhs = Decimal(0)
            for name, coefficient_value in constraint["terms"].items():
                lhs += coefficient_value * point[name]["value"]
            rhs = constraint["limit"]
            operator = constraint["op"]
            passed = (operator == "<=" and lhs <= rhs) or (operator == ">=" and lhs >= rhs) or (operator == "==" and lhs == rhs)
            if not passed:
                reasons.append({"code": "COMBINATION_CONSTRAINT", "expression": f"{lhs} {operator} {rhs} is false", "source": constraint["source"]})
        cost, minutes, cost_formula, time_formula = point_cost(point, version, resources)
        if cost < 0:
            errors.append({"code": "NEGATIVE_COMPUTED_COST", "version": ref, "point": point_key(point), "cost": str(cost)})
            reasons.append({"code": "NEGATIVE_COMPUTED_COST", "cost": str(cost)})
        if cost > budget_amount:
            reasons.append({"code": "BUDGET_EXCEEDED", "cost": str(cost), "limit": str(budget_amount), "currency": budget.get("currency")})
        if minutes > budget_minutes:
            reasons.append({"code": "TIME_LIMIT_EXCEEDED", "minutes": str(minutes), "limit": str(budget_minutes)})
        matched = []
        for obs in observations:
            obs_values = obs.get("values", {})
            if set(obs_values) != set(names):
                continue
            comparable = True
            for name in names:
                obs_value = dec(obs_values[name].get("value"), f"{ref}.observation.{name}", [])
                if point[name]["value"] != obs_value or point[name]["unit"] != obs_values[name].get("unit"):
                    comparable = False
                    break
            if comparable:
                matched.append(obs)
        if len(matched) > 1:
            reasons.append({"code": "DUPLICATE_OBSERVATION", "point": point_key(point)})
        observation = matched[0] if matched else None
        candidates.append({"values": plain_point(point), "key": point_key(point), "rejection_reasons": reasons,
                           "feasible": not reasons, "cost": str(cost) if cost is not None else None, "currency": version.get("currency"),
                           "minutes": str(minutes) if minutes is not None else None, "cost_formula": cost_formula, "time_formula": time_formula,
                           "provided_response": observation.get("responses") if observation else None,
                           "response_source": observation.get("source") if observation else None,
                           "response_status": "provided_observation_not_prediction" if observation else "no_response_yet"})
    feasible = [candidate for candidate in candidates if candidate["feasible"]]
    if not feasible:
        errors.append({"code": "NO_FEASIBLE_CANDIDATE", "version": ref})
    evaluated = [candidate for candidate in feasible if candidate["provided_response"] is not None]
    for candidate in evaluated:
        for response in responses:
            value = candidate["provided_response"].get(response.get("name"))
            if dec(value, f"{ref}.{candidate['key']}.response.{response.get('name')}", []) is None:
                errors.append({"code": "RESPONSE_MISSING_OR_INVALID", "version": ref, "point": candidate["key"], "response": response.get("name")})

    def dominates(left, right):
        dimensions = [("cost", "min"), ("minutes", "min")] + [(r.get("name"), r.get("direction")) for r in responses]
        better_or_equal, strictly = True, False
        for name, direction in dimensions:
            source = left if name in {"cost", "minutes"} else left["provided_response"]
            target = right if name in {"cost", "minutes"} else right["provided_response"]
            a, b = Decimal(str(source[name])), Decimal(str(target[name]))
            if direction == "min":
                better_or_equal &= a <= b
                strictly |= a < b
            elif direction == "max":
                better_or_equal &= a >= b
                strictly |= a > b
            else:
                raise ValueError("response direction must be min or max")
        return better_or_equal and strictly

    try:
        pareto = [candidate for candidate in evaluated if not any(dominates(other, candidate) for other in evaluated if other is not candidate)]
    except (InvalidOperation, ValueError, TypeError, KeyError) as exc:
        errors.append({"code": "PARETO_EVALUATION_ERROR", "version": ref, "message": str(exc)})
        pareto = []
    pending = [candidate for candidate in feasible if candidate["provided_response"] is None]
    pending.sort(key=lambda candidate: (Decimal(candidate["cost"]) if candidate["cost"] is not None else Decimal("Infinity"), candidate["key"]))
    batch, batch_cost = [], Decimal(0)
    deferred = []
    for candidate in pending:
        if len(batch) >= batch_size:
            deferred.append({"key": candidate["key"], "reason": "BATCH_SIZE_LIMIT"})
            continue
        cost = Decimal(candidate["cost"]) if candidate["cost"] is not None else None
        if cost is None or (batch_limit is not None and batch_cost + cost > batch_limit):
            deferred.append({"key": candidate["key"], "reason": "BATCH_BUDGET_OR_COST_MISSING"})
            continue
        batch.append(candidate)
        batch_cost += cost
    if not feasible:
        stop = "no_feasible_candidate"
    elif len(evaluated) == len(feasible):
        stop = "provided_evaluations_complete_not_convergence_claim"
    else:
        stop = "next_batch_proposed_no_new_response_no_convergence_claim"
    edges = []
    for left, right in itertools.permutations(pareto, 2):
        edges.append({"from": left["key"], "to": right["key"], "cost_delta": str(Decimal(right["cost"]) - Decimal(left["cost"])),
                      "response_delta": {response["name"]: str(Decimal(str(right["provided_response"][response["name"]])) - Decimal(str(left["provided_response"][response["name"]]))) for response in responses}})
    return {"ref": ref, "errors": errors, "baseline": plain_point(baseline), "finite_domain_size": expected_count,
            "candidates": candidates, "pareto": [{"key": c["key"], "cost": c["cost"], "minutes": c["minutes"], "responses": c["provided_response"], "source": c["response_source"]} for c in pareto],
            "tradeoff_edges": edges,
            "next_batch": [{"key": c["key"], "cost": c["cost"], "response_status": c["response_status"]} for c in batch],
            "proposed_batch_total_cost": str(batch_cost), "deferred": deferred, "stop_state": stop,
            "version_isolation": "No parameter, response or cost is merged across versions."}


def process(data):
    errors = []
    if data.get("schema") != SCHEMA:
        errors.append({"code": "SCHEMA_MISMATCH", "expected": SCHEMA})
    versions = data.get("versions", [])
    if len(versions) != 3:
        errors.append({"code": "THREE_VERSIONS_REQUIRED", "actual": len(versions)})
    audits = [evaluate(version, data) for version in versions]
    failed = bool(errors) or any(audit["errors"] for audit in audits)
    return {"auxiliary_schema": SCHEMA, "case_id": data.get("case_id"), "status": "failed" if failed else "pass",
            "global_errors": errors, "version_searches": audits,
            "selection_rule": "Hard feasibility precedes Pareto comparison; no unprovided weights, modeled response or convergence.",
            "scope": "Deterministic proposal/checking only; no experiment was executed and no safety approval was granted."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    try:
        result = process(json.loads(Path(args.input).read_text(encoding="utf-8")))
        code = 2 if result["status"] == "failed" else 0
    except (OSError, json.JSONDecodeError, TypeError, KeyError, AttributeError, ValueError) as exc:
        result = {"auxiliary_schema": SCHEMA, "status": "failed", "global_errors": [{"code": "INPUT_OR_RUNTIME_ERROR", "message": str(exc)}]}
        code = 2
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{result['status']}: {output}; exit={code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
