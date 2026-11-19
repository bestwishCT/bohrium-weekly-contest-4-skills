#!/usr/bin/env python3
"""Check methods review ledgers and bounded two-group Wald calculations."""
import argparse
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
import sys

FACETS = ("research_design", "variable_definition", "statistical_method",
          "sample_size", "result_reporting")
RELATED = ("fact_verification", "citation_support", "methods_design",
           "statistics_report", "argumentation", "scholarly_norms")
STATES = {"passed", "issues_found", "review_required", "not_checked"}
SEVERITIES = {"critical", "major", "moderate", "minor", "advisory"}
Z95 = 1.959963984540054


class AuditError(ValueError):
    pass


def text(obj, key, where):
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AuditError("%s requires %s" % (where, key))
    return value


def decimal(obj, key, where):
    try:
        value = Decimal(text(obj, key, where))
    except InvalidOperation:
        raise AuditError(where + " has invalid decimal " + key)
    if not value.is_finite():
        raise AuditError(where + " requires finite " + key)
    return value


def anchor(body, quote, where):
    count = body.count(quote)
    if count != 1:
        raise AuditError("%s quote count is %d, expected 1" % (where, count))
    offset = body.index(quote)
    return {"line": body.count("\n", 0, offset) + 1,
            "column": offset - body.rfind("\n", 0, offset)}


def locate(obj, where):
    if not isinstance(obj.get("location"), dict) or not obj["location"]:
        raise AuditError(where + " requires location")


def check_matrix(matrix, required, complete, finding_ids, prefix):
    if not isinstance(matrix, dict) or set(matrix) != set(required):
        raise AuditError(prefix + " has incorrect facets")
    linked = []
    for facet, row in matrix.items():
        where = prefix + "." + facet
        if not isinstance(row, dict) or row.get("status") not in STATES:
            raise AuditError(where + " has invalid status")
        ids = row.get("finding_ids", [])
        if not isinstance(ids, list) or not set(ids).issubset(finding_ids):
            raise AuditError(where + " has invalid finding_ids")
        if row["status"] == "issues_found" and not ids:
            raise AuditError(where + " needs findings")
        if row["status"] in {"not_checked", "review_required"}:
            text(row, "reason", where)
        if not complete and row["status"] in {"passed", "issues_found"}:
            text(row, "scope", where)
            if row["scope"] == "full":
                raise AuditError(where + " cannot have full scope for truncated text")
        linked.extend(ids)
    return linked


def calculate(record, body):
    where = "calculation " + text(record, "id", "calculation")
    locate(record, where)
    anchor(body, text(record, "quote", where), where)
    if record.get("kind") == "not_applicable":
        return {"calculation_id": record["id"], "status": "not_checked",
                "reason": text(record, "reason", where)}
    if record.get("kind") != "two_group_wald" or record.get("design") != "independent_two_group":
        raise AuditError(where + " only supports explicitly independent two-group Wald inputs")
    effect = float(decimal(record, "effect", where))
    se = float(decimal(record, "se", where))
    if not math.isfinite(effect) or not math.isfinite(se) or se <= 0:
        raise AuditError(where + " requires finite effect and positive finite SE")
    z = effect / se
    p = math.erfc(abs(z) / math.sqrt(2.0))
    low, high = effect - Z95 * se, effect + Z95 * se
    expected = {"z": z, "two_sided_normal_p": p, "wald_ci95": [low, high]}
    mismatches = []
    reported = record.get("reported", {})
    tolerance = record.get("tolerance", {})
    ci_tol = float(tolerance.get("ci", "0.01"))
    p_tol = float(tolerance.get("p", "0.001"))
    z_tol = float(tolerance.get("z", "0.01"))
    if not all(math.isfinite(v) for v in (ci_tol, p_tol, z_tol)) or min(ci_tol, p_tol, z_tol) < 0:
        raise AuditError(where + " tolerances must be finite and nonnegative")
    if "z" in reported and abs(float(decimal(reported, "z", where)) - z) > z_tol:
        mismatches.append("z")
    if "p" in reported:
        observed = float(decimal(reported, "p", where))
        if not math.isfinite(observed) or observed < 0 or observed > 1:
            raise AuditError(where + " reported p or bound must be finite and in [0,1]")
        relation = reported.get("p_relation", "exact")
        if relation == "exact" and abs(observed - p) > p_tol:
            mismatches.append("p")
        elif relation == "<" and not (p <= observed):
            mismatches.append("p_bound")
        elif relation == ">" and not (p >= observed):
            mismatches.append("p_bound")
        elif relation not in {"exact", "<", ">"}:
            raise AuditError(where + " has invalid p_relation")
    if "ci95" in reported:
        interval = reported["ci95"]
        if not isinstance(interval, list) or len(interval) != 2:
            raise AuditError(where + " ci95 requires two endpoints")
        observed_interval = [float(decimal({"v": str(v)}, "v", where)) for v in interval]
        if not all(math.isfinite(v) for v in observed_interval):
            raise AuditError(where + " ci95 endpoints must be finite")
        if (abs(observed_interval[0] - low) > ci_tol or
                abs(observed_interval[1] - high) > ci_tol):
            mismatches.append("ci95")
    return {
        "calculation_id": record["id"],
        "status": "review_candidate" if mismatches else "consistent_within_tolerance",
        "formula": "z=effect/SE; two-sided p=erfc(abs(z)/sqrt(2)); CI=effect+/-1.959963984540054*SE",
        "computed": {"z": float("%.12g" % z), "two_sided_normal_p": float("%.12g" % p),
                     "wald_ci95": [float("%.12g" % low), float("%.12g" % high)]},
        "tolerances": {"z": z_tol, "p": p_tol, "ci95": ci_tol},
        "difference_fields": mismatches,
        "disposition": "calculation_candidate_only_verify_model_rounding_and_estimand"
    }


def validate(data):
    if not isinstance(data, dict):
        raise AuditError("root must be an object")
    manuscript = data.get("manuscript", {})
    body = text(manuscript, "text", "manuscript")
    complete = manuscript.get("complete")
    if not isinstance(complete, bool):
        raise AuditError("manuscript.complete must be boolean")
    if not isinstance(manuscript.get("requested_sections"), list) or not isinstance(manuscript.get("readable_sections"), list):
        raise AuditError("required section arrays missing")
    if complete and set(manuscript["requested_sections"]) != set(manuscript["readable_sections"]):
        raise AuditError("complete manuscript has unread sections")
    variables = data.get("variables", [])
    if not isinstance(variables, list):
        raise AuditError("variables must be an array")
    for i, variable in enumerate(variables):
        where = "variables[%d]" % i
        text(variable, "id", where)
        text(variable, "declared_type", where)
        text(variable, "model_id", where)
        anchor(body, text(variable, "definition_quote", where), where)
        locate(variable, where)
    calculations = data.get("calculations", [])
    if not isinstance(calculations, list):
        raise AuditError("calculations must be an array")
    numeric_results = [calculate(row, body) for row in calculations if row.get("kind") == "two_group_wald"]
    skipped = [calculate(row, body) for row in calculations if row.get("kind") == "not_applicable"]
    if len(numeric_results) + len(skipped) != len(calculations):
        raise AuditError("calculations must declare supported or explicitly not_applicable kinds")
    review = data.get("review", {})
    findings = review.get("findings", [])
    if not isinstance(findings, list):
        raise AuditError("findings must be an array")
    finding_ids = set()
    for i, finding in enumerate(findings):
        where = "findings[%d]" % i
        fid = text(finding, "id", where)
        if fid in finding_ids:
            raise AuditError("duplicate finding id")
        finding_ids.add(fid)
        if text(finding, "facet", where) not in FACETS:
            raise AuditError(where + " has unknown facet")
        anchor(body, text(finding, "quote", where), where)
        locate(finding, where)
        for key in ("type", "severity", "severity_reason", "basis", "recommendation", "certainty"):
            text(finding, key, where)
        if finding["severity"] not in SEVERITIES:
            raise AuditError(where + " has invalid severity")
        if finding["type"] == "reporting_omission":
            text(finding, "absence_scope", where)
        if finding["type"] == "design_or_method_error" and finding.get("positive_evidence") is not True:
            raise AuditError(where + " method error requires positive_evidence=true")
        if "calculation_id" in finding and finding["calculation_id"] not in {row["calculation_id"] for row in numeric_results}:
            raise AuditError(where + " references absent calculation")
    linked = check_matrix(review.get("coverage"), FACETS, complete, finding_ids, "coverage")
    related = check_matrix(review.get("related_facets"), RELATED, complete, set(), "related_facets")
    if related:
        raise AuditError("related facet matrix cannot invent main findings")
    if set(linked) != finding_ids or len(linked) != len(finding_ids):
        raise AuditError("findings must be linked exactly once")
    return {
        "validator_status": "ok", "fixture_id": data.get("fixture_id"),
        "coverage_scope": "full" if complete else "partial",
        "five_facet_states": {facet: review["coverage"][facet]["status"] for facet in FACETS},
        "related_facet_states": {facet: review["related_facets"][facet]["status"] for facet in RELATED},
        "counts": {"findings": len(finding_ids), "variables": len(variables)},
        "calculations": numeric_results, "not_applicable_calculations": skipped,
        "note": "Numeric differences are review candidates, not automatic statistical errors."
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.ledger.read_text(encoding="utf-8"))
        result = validate(data)
    except json.JSONDecodeError as exc:
        print(json.dumps({"validator_status": "input_error", "error": {"code": "INVALID_JSON", "message": "line %d column %d" % (exc.lineno, exc.colno)}}), file=sys.stderr)
        return 2
    except (OSError, AuditError, KeyError, TypeError, AttributeError) as exc:
        code = "INPUT_READ_ERROR" if isinstance(exc, OSError) else "INVALID_LEDGER"
        print(json.dumps({"validator_status": "input_error", "error": {"code": code, "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
