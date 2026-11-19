#!/usr/bin/env python3
"""Validate argument maps and surface only explicitly structured logic candidates."""
import argparse
import json
from pathlib import Path
import sys

AXES = ("claim_evidence", "reasoning_chain", "cross_section_delivery", "figure_text_scope")
RELATED = ("fact_verification", "citation_support", "methods_design",
           "statistics_report", "argumentation", "scholarly_norms")
STATES = {"passed", "issues_found", "review_required", "not_checked"}
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


def matrix(value, required, complete, finding_ids, prefix):
    if not isinstance(value, dict) or set(value) != set(required):
        raise AuditError(prefix + " has incorrect facets")
    linked = []
    for facet, row in value.items():
        where = prefix + "." + facet
        if not isinstance(row, dict) or row.get("status") not in STATES:
            raise AuditError(where + " has invalid status")
        ids = row.get("finding_ids", [])
        if not isinstance(ids, list) or not set(ids).issubset(finding_ids):
            raise AuditError(where + " has invalid finding IDs")
        if row["status"] == "issues_found" and not ids:
            raise AuditError(where + " needs a finding")
        if row["status"] in {"not_checked", "review_required"}:
            text(row, "reason", where)
        if not complete and row["status"] in {"passed", "issues_found"}:
            text(row, "scope", where)
            if row["scope"] == "full":
                raise AuditError(where + " cannot claim full truncated-text scope")
        linked.extend(ids)
    return linked


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
    ids = set()
    evidence_rows = data.get("evidence", [])
    if not isinstance(evidence_rows, list):
        raise AuditError("evidence must be an array")
    evidence = {}
    for i, row in enumerate(evidence_rows):
        where = "evidence[%d]" % i
        eid = text(row, "id", where)
        if eid in evidence:
            raise AuditError("duplicate evidence ID")
        evidence[eid] = row
        locate(row, where)
        availability = row.get("availability", "available")
        if availability not in {"available", "missing"}:
            raise AuditError(where + " has invalid availability")
        if availability == "available":
            anchor(body, text(row, "quote", where), where)
            if row.get("design_capacity") not in {"associational", "causal", "descriptive"}:
                raise AuditError(where + " has invalid design_capacity")
    claims = data.get("claims", [])
    if not isinstance(claims, list):
        raise AuditError("claims must be an array")
    inference = []
    unavailable = []
    anchors = []
    for i, claim in enumerate(claims):
        where = "claims[%d]" % i
        cid = text(claim, "id", where)
        if cid in ids:
            raise AuditError("duplicate claim ID")
        ids.add(cid)
        quote = text(claim, "quote", where)
        locate(claim, where)
        anchors.append({"claim_id": cid, **anchor(body, quote, where)})
        if claim.get("speaker") not in {"author", "reported", "hypothesis"}:
            raise AuditError(where + " has invalid speaker")
        if claim.get("strength") not in {"causal", "associational", "bounded_descriptive", "hypothetical"}:
            raise AuditError(where + " has invalid strength")
        refs = claim.get("evidence_ids", [])
        if not isinstance(refs, list) or not set(refs).issubset(evidence):
            raise AuditError(where + " references absent evidence")
        if any(evidence[ref]["availability"] == "missing" for ref in refs):
            if claim.get("evidence_state") != "not_checked":
                raise AuditError(where + " unavailable evidence requires not_checked")
            unavailable.append(cid)
            continue
        basis = claim.get("causal_identification_basis", "")
        if claim.get("causal_identification_claimed"):
            text(claim, "causal_identification_basis", where)
        if (claim.get("speaker") == "author" and claim.get("strength") == "causal" and
                claim.get("evidence_state") == "direct" and refs and
                all(evidence[ref].get("design_capacity") == "associational" for ref in refs) and not basis):
            inference.append({"claim_id": cid, "evidence_ids": refs,
                              "reason": "author_causal_claim_with_only_association_capacity",
                              "disposition": "review_candidate_only_read_context"})
    promises = data.get("promises", [])
    if not isinstance(promises, list):
        raise AuditError("promises must be an array")
    delivery = []
    for i, promise in enumerate(promises):
        where = "promises[%d]" % i
        pid = text(promise, "id", where)
        anchor(body, text(promise, "promise_quote", where), where)
        locate(promise, where)
        missing = []
        if not complete:
            if promise.get("delivery") != "not_checked":
                raise AuditError(where + " cross-section delivery must be not_checked for truncated text")
            continue
        for field in ("method_quote", "result_quote", "conclusion_quote"):
            value = promise.get(field, "")
            if value:
                anchor(body, value, where + "." + field)
            else:
                missing.append(field)
        if missing:
            text(promise, "undelivered_aspects", where)
            delivery.append({"promise_id": pid, "missing_nodes": missing,
                             "disposition": "review_candidate_only_check_appendices"})
    contrasts = data.get("contrasts", [])
    if not isinstance(contrasts, list):
        raise AuditError("contrasts must be an array")
    consistency = []
    for i, pair in enumerate(contrasts):
        where = "contrasts[%d]" % i
        cid = text(pair, "id", where)
        for side in ("left_quote", "right_quote"):
            anchor(body, text(pair, side, where), where)
        if pair.get("relation") not in {"direct_contradiction", "intentional_contrast", "different_population", "timepoint_change", "unclassified"}:
            raise AuditError(where + " has invalid relation")
        if (pair.get("relation") == "direct_contradiction" and pair.get("same_scope") is True and
                pair.get("left_speaker") == "author" and pair.get("right_speaker") == "author"):
            consistency.append({"contrast_id": cid, "disposition": "review_candidate_only_verify_context"})
    review = data.get("review", {})
    findings = review.get("findings", [])
    if not isinstance(findings, list):
        raise AuditError("findings must be an array")
    finding_ids = set()
    for i, finding in enumerate(findings):
        where = "findings[%d]" % i
        fid = text(finding, "id", where)
        if fid in finding_ids:
            raise AuditError("duplicate finding ID")
        finding_ids.add(fid)
        if text(finding, "facet", where) not in AXES:
            raise AuditError(where + " has unknown facet")
        anchor(body, text(finding, "quote", where), where)
        locate(finding, where)
        for key in ("type", "severity", "severity_reason", "basis", "recommendation", "certainty"):
            text(finding, key, where)
        if finding["severity"] not in SEVERITIES:
            raise AuditError(where + " has invalid severity")
    linked = matrix(review.get("coverage"), AXES, complete, finding_ids, "coverage")
    related = matrix(review.get("related_facets"), RELATED, complete, set(), "related_facets")
    if related or set(linked) != finding_ids or len(linked) != len(finding_ids):
        raise AuditError("main findings must be linked exactly once; related rows cannot create findings")
    return {
        "validator_status": "ok", "fixture_id": data.get("fixture_id"),
        "coverage_scope": "full" if complete else "partial",
        "axis_states": {facet: review["coverage"][facet]["status"] for facet in AXES},
        "related_facet_states": {facet: review["related_facets"][facet]["status"] for facet in RELATED},
        "evidence_anchors": anchors, "counts": {"claims": len(claims), "findings": len(finding_ids)},
        "inference_candidates": inference, "cross_section_candidates": delivery,
        "consistency_candidates": consistency, "source_unavailable_claims": unavailable,
        "note": "Candidates are derived only from explicit annotations and require contextual review."
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
