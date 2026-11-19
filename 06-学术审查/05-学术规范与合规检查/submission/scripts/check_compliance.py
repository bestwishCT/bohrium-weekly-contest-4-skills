#!/usr/bin/env python3
"""Validate evidence-bound compliance ledgers without inferring misconduct."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

RISKS = ("integrity_and_authorship", "citation_integrity", "privacy_and_data",
         "copyright_and_license", "research_ethics", "target_submission_rules")
STATES = {"passed", "issues_found", "review_required", "not_checked", "not_applicable"}
RESULTS = {"conforms", "nonconforming", "reporting_gap", "review_required", "not_applicable", "unchecked"}
SEVERITIES = {"critical", "major", "moderate", "minor", "advisory"}


class AuditError(ValueError):
    pass


def text(obj, key, where):
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AuditError("%s requires %s" % (where, key))
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


def validate(data):
    if not isinstance(data, dict):
        raise AuditError("root must be an object")
    manuscript = data.get("manuscript", {})
    body = text(manuscript, "text", "manuscript")
    complete = manuscript.get("complete")
    if not isinstance(complete, bool):
        raise AuditError("manuscript.complete must be boolean")
    if not isinstance(manuscript.get("requested_sections"), list) or not isinstance(manuscript.get("readable_sections"), list):
        raise AuditError("section coverage arrays required")
    if complete and set(manuscript["requested_sections"]) != set(manuscript["readable_sections"]):
        raise AuditError("complete manuscript has unread requested sections")
    authorities = data.get("authorities", {})
    rules = data.get("rules", [])
    materials = data.get("materials", [])
    if not isinstance(authorities, dict) or not isinstance(rules, list) or not isinstance(materials, list):
        raise AuditError("authorities, rules and materials must be object/array")
    rules_by_id = {}
    for i, rule in enumerate(rules):
        where = "rules[%d]" % i
        rid = text(rule, "id", where)
        if rid in rules_by_id:
            raise AuditError("duplicate rule ID")
        aid = text(rule, "authority_id", where)
        if aid not in authorities:
            raise AuditError(where + " has absent authority")
        authority = authorities[aid]
        text(authority, "version", where + ".authority")
        if authority.get("status") not in {"available", "unavailable"}:
            raise AuditError(where + " has invalid authority status")
        applicability = rule.get("applicability")
        if applicability not in {"applicable", "not_applicable", "unverified"}:
            raise AuditError(where + " has invalid applicability")
        if authority["status"] == "available":
            body_of_rule = text(authority, "text", where + ".authority")
            anchor(body_of_rule, text(rule, "rule_quote", where), where + ".rule_quote")
            locate(rule, where)
        elif applicability == "applicable":
            raise AuditError(where + " cannot apply unavailable authority as verified rule")
        if authority["status"] == "unavailable" and applicability != "unverified":
            raise AuditError(where + " unavailable authority requires unverified applicability")
        rules_by_id[rid] = rule
    materials_by_id = {}
    for i, material in enumerate(materials):
        where = "materials[%d]" % i
        mid = text(material, "id", where)
        if mid in materials_by_id:
            raise AuditError("duplicate material ID")
        text(material, "kind", where)
        locate(material, where)
        quote = text(material, "quote", where)
        anchor(body, quote, where)
        if not isinstance(material.get("sensitive"), bool):
            raise AuditError(where + " sensitive must be boolean")
        materials_by_id[mid] = material
    assessments = data.get("assessments", [])
    if not isinstance(assessments, list):
        raise AuditError("assessments must be an array")
    assessments_by_id = {}
    for i, row in enumerate(assessments):
        where = "assessments[%d]" % i
        aid = text(row, "id", where)
        if aid in assessments_by_id:
            raise AuditError("duplicate assessment ID")
        result = row.get("result")
        if result not in RESULTS:
            raise AuditError(where + " has invalid result")
        mid = text(row, "material_id", where)
        rid = text(row, "rule_id", where)
        if mid not in materials_by_id or rid not in rules_by_id:
            raise AuditError(where + " references absent material or rule")
        rule = rules_by_id[rid]
        if result in {"conforms", "nonconforming", "reporting_gap"} and rule["applicability"] != "applicable":
            raise AuditError(where + " needs verified applicable authority")
        if result in {"conforms", "nonconforming", "reporting_gap"} and authorities[rule["authority_id"]]["status"] != "available":
            raise AuditError(where + " cannot decide from unavailable authority")
        if result in {"review_required", "not_applicable", "unchecked"}:
            text(row, "reason", where)
        if result == "review_required":
            text(row, "missing_evidence", where)
        if result == "reporting_gap":
            text(row, "absence_scope", where)
        assessments_by_id[aid] = row
    review = data.get("review", {})
    findings = review.get("findings", [])
    if not isinstance(findings, list):
        raise AuditError("findings must be an array")
    finding_ids = set()
    safe_anchors = []
    for i, finding in enumerate(findings):
        where = "findings[%d]" % i
        fid = text(finding, "id", where)
        if fid in finding_ids:
            raise AuditError("duplicate finding ID")
        finding_ids.add(fid)
        if text(finding, "facet", where) not in RISKS:
            raise AuditError(where + " has invalid risk facet")
        assessment_id = text(finding, "assessment_id", where)
        if assessment_id not in assessments_by_id:
            raise AuditError(where + " references absent assessment")
        result = assessments_by_id[assessment_id]["result"]
        ftype = text(finding, "type", where)
        expected = {"nonconforming": "confirmed_noncompliance", "review_required": "risk_signal", "reporting_gap": "reporting_gap"}
        if ftype in {"confirmed_noncompliance", "risk_signal", "reporting_gap"} and expected.get(result) != ftype:
            raise AuditError(where + " finding type lacks matching assessment result")
        mid = text(finding, "material_id", where)
        if mid not in materials_by_id or assessments_by_id[assessment_id]["material_id"] != mid:
            raise AuditError(where + " material linkage mismatch")
        locate(finding, where)
        for key in ("severity", "severity_reason", "basis", "recommendation", "certainty"):
            text(finding, key, where)
        if finding["severity"] not in SEVERITIES:
            raise AuditError(where + " has invalid severity")
        material = materials_by_id[mid]
        if material["sensitive"]:
            if finding.get("quote_mode") != "redacted_hash":
                raise AuditError(where + " sensitive finding requires redacted_hash")
            digest = hashlib.sha256(material["quote"].encode("utf-8")).hexdigest()
            if finding.get("quote_sha256") != digest:
                raise AuditError(where + " sensitive quote hash mismatch")
            safe_anchors.append({"finding_id": fid, "quote_sha256": digest, "location": material["location"]})
        else:
            safe_anchors.append({"finding_id": fid,
                                 **anchor(body, text(finding, "quote", where), where)})
    coverage = review.get("coverage")
    if not isinstance(coverage, dict) or set(coverage) != set(RISKS):
        raise AuditError("coverage must exactly contain six risk facets")
    linked = []
    for facet, row in coverage.items():
        where = "coverage." + facet
        if not isinstance(row, dict) or row.get("status") not in STATES:
            raise AuditError(where + " has invalid status")
        ids = row.get("finding_ids", [])
        if not isinstance(ids, list) or not set(ids).issubset(finding_ids):
            raise AuditError(where + " has invalid finding IDs")
        if row["status"] == "issues_found" and not ids:
            raise AuditError(where + " needs findings")
        if row["status"] in {"not_applicable", "not_checked", "review_required"}:
            text(row, "reason", where)
        if not complete and row["status"] in {"passed", "issues_found"}:
            text(row, "scope", where)
            if row["scope"] == "full":
                raise AuditError(where + " cannot claim full truncated-text scope")
        linked.extend(ids)
    if set(linked) != finding_ids or len(linked) != len(finding_ids):
        raise AuditError("findings must be linked exactly once")
    return {
        "validator_status": "ok", "fixture_id": data.get("fixture_id"),
        "coverage_scope": "full" if complete else "partial",
        "risk_states": {facet: coverage[facet]["status"] for facet in RISKS},
        "safe_evidence_anchors": safe_anchors,
        "counts": {"findings": len(finding_ids), "rules": len(rules), "materials": len(materials)},
        "automated_misconduct_candidates": [],
        "note": "This ledger check makes no legal, ethical, licensing or integrity verdict."
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
