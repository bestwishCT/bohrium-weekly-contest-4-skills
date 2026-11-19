#!/usr/bin/env python3
"""Audit seven feasibility domains independently for each of three plan versions."""
import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

SCHEMA = "feasibility-audit-v1"
DOMAINS = ["假设与变量", "对照组", "设备材料", "时间成本", "参数范围", "风险与异常", "伦理与安全"]
FINDING_STATES = {"pass", "conditional", "block", "info_missing"}
RESOURCE_STATES = {"已具备", "可外协", "需采购", "不具备", "信息不足、待核验"}


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


def arithmetic(plan, ref, errors):
    sample = plan.get("sample_rule", {})
    groups = positive_int(sample.get("groups"), f"{ref}.sample.groups", errors)
    repeats = positive_int(sample.get("replicates"), f"{ref}.sample.replicates", errors)
    per_sample = nonnegative_dec(sample.get("per_sample"), f"{ref}.sample.per_sample", errors)
    loss = nonnegative_dec(sample.get("loss_factor"), f"{ref}.sample.loss_factor", errors)
    required = groups * repeats * per_sample * loss if None not in (groups, repeats, per_sample, loss) else None
    available = nonnegative_dec(sample.get("available"), f"{ref}.sample.available", errors)
    sample_formula = None
    if required is not None:
        sample_formula = f"{groups} groups * {repeats} replicates * {per_sample} {sample.get('unit')} * {loss} loss factor = {required} {sample.get('unit')}"
    blockers = []
    if required is not None and available is not None and available < required:
        blockers.append({"code": "SAMPLE_SHORTAGE", "required": str(required), "available": str(available), "unit": sample.get("unit")})
    costs, formulas = {}, []
    for item in plan.get("cost_items", []):
        quantity = nonnegative_dec(item.get("quantity"), f"{ref}.cost.{item.get('id')}.quantity", errors)
        price = nonnegative_dec(item.get("unit_price"), f"{ref}.cost.{item.get('id')}.unit_price", errors)
        if quantity is None or price is None:
            continue
        amount = quantity * price
        currency = item.get("currency")
        costs[currency] = costs.get(currency, Decimal(0)) + amount
        formulas.append({"id": item.get("id"), "formula": f"{quantity} {item.get('unit')} * {price} {currency}/{item.get('unit')} = {amount} {currency}", "amount": str(amount), "currency": currency})
    currency = plan.get("budget", {}).get("currency")
    subtotal = costs.get(currency)
    if any(key != currency for key in costs):
        errors.append({"code": "CURRENCY_MISMATCH", "version": ref, "expected": currency, "actual_currencies": sorted(str(k) for k in costs)})
    limit = nonnegative_dec(plan.get("budget", {}).get("amount"), f"{ref}.budget.amount", errors)
    if subtotal is not None and limit is not None and subtotal > limit:
        blockers.append({"code": "BUDGET_CONFLICT", "computed": str(subtotal), "limit": str(limit), "currency": currency})
    ready_days = [nonnegative_dec(plan.get("booking_available_day"), f"{ref}.booking_available_day", errors)]
    for item in plan.get("procurements", []):
        ready_days.append(nonnegative_dec(item.get("ready_day"), f"{ref}.procurement.{item.get('item')}.ready_day", errors))
    duration = nonnegative_dec(plan.get("duration_days"), f"{ref}.duration_days", errors)
    known_ready = [value for value in ready_days if value is not None]
    earliest = max(known_ready) if len(known_ready) == len(ready_days) else None
    finish = earliest + duration if earliest is not None and duration is not None else None
    window = nonnegative_dec(plan.get("window_end_day"), f"{ref}.window_end_day", errors)
    if finish is not None and window is not None and finish > window:
        blockers.append({"code": "TIME_WINDOW_CONFLICT", "finish_day": str(finish), "window_end_day": str(window)})
    return {"sample": {"required": str(required) if required is not None else None, "available": str(available) if available is not None else None, "unit": sample.get("unit"), "formula": sample_formula},
            "cost_totals_by_currency": {str(k): str(v) for k, v in sorted(costs.items(), key=lambda pair: str(pair[0]))},
            "budget_currency_subtotal": str(subtotal) if subtotal is not None else None, "cost_formulas": formulas,
            "earliest_start_day": str(earliest) if earliest is not None else None, "finish_day": str(finish) if finish is not None else None,
            "schedule_formula": f"max(booking, procurement ready days) + {duration} duration days = {finish} day" if finish is not None else None,
            "blocking_facts": blockers}


def audit_plan(plan, index):
    ref = str(plan.get("ref", f"position-{index}"))
    errors = []
    limitations = []
    blockers = []
    if not plan.get("ref"):
        errors.append({"code": "VERSION_REF_MISSING", "position": index})
    if plan.get("scenario") not in {"organizer_prospective", "synthetic_nonofficial"}:
        errors.append({"code": "SCENARIO_REQUIRED", "version": ref})
    findings, found_domains = [], set()
    for item in plan.get("findings", []):
        domain = item.get("domain")
        state = item.get("status")
        found_domains.add(domain)
        if domain not in DOMAINS or state not in FINDING_STATES:
            errors.append({"code": "FINDING_STATE_INVALID", "version": ref, "domain": domain, "status": state})
        for key in ("plan_ref", "source", "judgment"):
            if not item.get(key):
                errors.append({"code": "FINDING_EVIDENCE_INCOMPLETE", "version": ref, "domain": domain, "missing": key})
        if state == "conditional":
            conditions = item.get("conditions", [])
            if not conditions:
                errors.append({"code": "CONDITION_INCOMPLETE", "version": ref, "domain": domain, "missing": "conditions"})
            for condition in conditions:
                for key in ("action", "owner_role", "before", "verify", "review_point"):
                    if not condition.get(key):
                        errors.append({"code": "CONDITION_INCOMPLETE", "version": ref, "domain": domain, "missing": key})
        if state == "block":
            blockers.append({"code": "FINDING_BLOCK", "domain": domain, "plan_ref": item.get("plan_ref")})
        if state == "info_missing":
            limitations.append({"code": "CANNOT_JUDGE_FROM_ABSENCE", "domain": domain, "source": item.get("source")})
        findings.append(item)
    for domain in DOMAINS:
        if domain not in found_domains:
            errors.append({"code": "SEVEN_DOMAIN_INCOMPLETE", "version": ref, "missing_domain": domain})
    resources = []
    for resource in plan.get("resources", []):
        state = resource.get("state")
        if state not in RESOURCE_STATES:
            errors.append({"code": "RESOURCE_STATE_INVALID", "version": ref, "item": resource.get("item"), "state": state})
        if not resource.get("source"):
            errors.append({"code": "SOURCE_MISSING", "version": ref, "resource": resource.get("item")})
        if state in {"信息不足、待核验", "不具备"}:
            entry = {"code": "RESOURCE_UNAVAILABLE" if state == "不具备" else "RESOURCE_UNKNOWN", "item": resource.get("item"), "state": state}
            (blockers if state == "不具备" else limitations).append(entry)
        resources.append(resource)
    math = arithmetic(plan, ref, errors)
    blockers.extend(math["blocking_facts"])
    if blockers:
        conclusion = "不可行"
    elif errors or limitations or any(item.get("status") == "info_missing" for item in findings):
        conclusion = "无法判断"
    elif any(item.get("status") == "conditional" for item in findings):
        conclusion = "有条件可行"
    else:
        conclusion = "可行（仅本次文档审查口径）"
    changed = plan.get("changed_domains", [])
    invalid_changed = [domain for domain in changed if domain not in DOMAINS]
    if invalid_changed:
        errors.append({"code": "REAUDIT_DOMAIN_INVALID", "version": ref, "values": invalid_changed})
    return {"ref": ref, "conclusion": conclusion, "errors": errors, "uncertainties": limitations,
            "findings": findings, "resources": resources, "arithmetic": math, "blocking_facts": blockers,
            "conditions": [{"domain": item.get("domain"), "items": item.get("conditions", [])} for item in findings if item.get("status") == "conditional"],
            "reaudit_domains": changed,
            "note": "Technical conclusion only; not actual equipment, booking, procurement, safety or ethics approval."}


def process(data):
    errors = []
    if data.get("schema") != SCHEMA:
        errors.append({"code": "SCHEMA_MISMATCH", "expected": SCHEMA})
    versions = data.get("versions", [])
    if len(versions) != 3:
        errors.append({"code": "THREE_VERSIONS_REQUIRED", "actual": len(versions)})
    audits = [audit_plan(plan, index) for index, plan in enumerate(versions)]
    failed = bool(errors) or any(audit["errors"] for audit in audits)
    return {"auxiliary_schema": SCHEMA, "case_id": data.get("case_id"),
            "validation_status": "failed" if failed else "pass", "version_reviews": audits, "global_errors": errors,
            "seven_domains": DOMAINS,
            "scope": "document feasibility only; missing evidence is not satisfaction and scenarios are not real approvals/bookings"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    try:
        result = process(json.loads(Path(args.input).read_text(encoding="utf-8")))
        code = 2 if result["validation_status"] == "failed" else 0
    except (OSError, json.JSONDecodeError, TypeError, KeyError, AttributeError) as exc:
        result = {"auxiliary_schema": SCHEMA, "validation_status": "failed", "global_errors": [{"code": "INPUT_OR_RUNTIME_ERROR", "message": str(exc)}]}
        code = 2
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{result['validation_status']}: {output}; exit={code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
