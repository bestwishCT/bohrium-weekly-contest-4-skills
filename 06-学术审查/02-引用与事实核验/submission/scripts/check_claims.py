#!/usr/bin/env python3
"""Validate claim/source pairs and compare only explicitly aligned numeric facts."""
import argparse
from decimal import Decimal, InvalidOperation
import json
import sys
from pathlib import Path

FACETS = ("fact_verification", "citation_support", "methods_design",
          "statistics_report", "argumentation", "scholarly_norms")
STATES = {"passed", "issues_found", "review_required", "not_checked"}
JUDGEMENTS = {"full_support", "partial_support", "unsupported", "undetermined"}
SEVERITIES = {"critical", "major", "moderate", "minor", "advisory"}
KEYS = ("metric", "population", "time", "unit", "measure_kind")


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


def location(obj, where, key="location"):
    loc = obj.get(key)
    if not isinstance(loc, dict) or not loc:
        raise AuditError(where + " requires source location")


def judge_claim(claim, manuscript, sources, index, anchors):
    where = "claims[%d]" % index
    cid = text(claim, "id", where)
    quote = text(claim, "quote", where)
    location(claim, where)
    anchors.append({"claim_id": cid, **anchor(manuscript, quote, where)})
    verdict = claim.get("judgement")
    if verdict not in JUDGEMENTS:
        raise AuditError(where + " has invalid judgement")
    sid = claim.get("source_id")
    source = sources.get(sid, {})
    available = bool(source)
    if verdict != "undetermined" and not available:
        raise AuditError(where + " cannot be decided without its source")
    if available:
        text(source, "version", "sources." + sid)
        text(source, "publication_status", "sources." + sid)
        status = source.get("retraction_status")
        if status not in {"not_checked", "unknown", "clear_for_checked_scope", "correction_found", "retracted"}:
            raise AuditError("sources." + sid + " has invalid retraction_status")
    if verdict == "undetermined":
        text(claim, "reason", where)
        return
    text(source, "text", "sources." + sid)
    if source.get("availability") not in {"full", "abstract"}:
        raise AuditError(where + " lacks actual full text or abstract evidence")
    s_quote = text(claim, "source_quote", where)
    anchor(source["text"], s_quote, where + ".source_quote")
    location(claim, where + ".source", "source_location")
    if verdict == "partial_support":
        text(claim, "uncovered_part", where)
    elif verdict == "unsupported":
        text(claim, "contradiction", where)
    elif claim.get("alignment") != "aligned":
        raise AuditError(where + " full_support requires alignment=aligned")


def numeric_candidates(facts, manuscript):
    groups = {}
    for i, fact in enumerate(facts):
        where = "numeric_facts[%d]" % i
        if not isinstance(fact, dict):
            raise AuditError(where + " must be an object")
        fid = text(fact, "id", where)
        anchor(manuscript, text(fact, "quote", where), where)
        location(fact, where)
        try:
            value = Decimal(text(fact, "value", where))
        except InvalidOperation:
            raise AuditError(where + " has invalid decimal")
        if not value.is_finite():
            raise AuditError(where + " requires finite decimal")
        group = tuple(text(fact, key, where) for key in KEYS)
        groups.setdefault(group, []).append((fid, value))
    candidates = []
    for qualifiers, values in sorted(groups.items()):
        distinct = sorted(set(value for _, value in values))
        if len(distinct) > 1:
            candidates.append({
                "aligned_qualifiers": dict(zip(KEYS, qualifiers)),
                "fact_ids": [fid for fid, _ in values],
                "values": [str(value) for value in distinct],
                "disposition": "candidate_only_check_scope_rounding_before_reporting"
            })
    return candidates


def validate(data):
    if not isinstance(data, dict):
        raise AuditError("root must be an object")
    manuscript = data.get("manuscript", {})
    body = text(manuscript, "text", "manuscript")
    complete = manuscript.get("complete")
    if not isinstance(complete, bool):
        raise AuditError("manuscript.complete must be boolean")
    if not isinstance(manuscript.get("requested_sections"), list) or not isinstance(manuscript.get("readable_sections"), list):
        raise AuditError("section coverage must be arrays")
    if complete and set(manuscript["requested_sections"]) != set(manuscript["readable_sections"]):
        raise AuditError("complete manuscript has unread requested sections")
    sources = data.get("sources", {})
    claims = data.get("claims", [])
    if not isinstance(sources, dict) or not isinstance(claims, list):
        raise AuditError("sources and claims have invalid types")
    anchors = []
    ids = set()
    for i, claim in enumerate(claims):
        if not isinstance(claim, dict):
            raise AuditError("claim must be an object")
        cid = text(claim, "id", "claims[%d]" % i)
        if cid in ids:
            raise AuditError("duplicate claim id")
        ids.add(cid)
        judge_claim(claim, body, sources, i, anchors)
    facts = data.get("numeric_facts", [])
    if not isinstance(facts, list):
        raise AuditError("numeric_facts must be an array")
    candidates = numeric_candidates(facts, body)
    review = data.get("review", {})
    coverage = review.get("coverage")
    findings = review.get("findings")
    if not isinstance(coverage, dict) or set(coverage) != set(FACETS):
        raise AuditError("coverage must exactly contain six required facets")
    if not isinstance(findings, list):
        raise AuditError("findings must be an array")
    finding_ids = set()
    for i, finding in enumerate(findings):
        where = "findings[%d]" % i
        fid = text(finding, "id", where)
        if fid in finding_ids:
            raise AuditError("duplicate finding id")
        finding_ids.add(fid)
        facet = text(finding, "facet", where)
        if facet not in FACETS:
            raise AuditError(where + " has unknown facet")
        anchor(body, text(finding, "quote", where), where)
        location(finding, where)
        for key in ("type", "severity", "severity_reason", "basis", "recommendation", "certainty"):
            text(finding, key, where)
        if finding["severity"] not in SEVERITIES:
            raise AuditError(where + " has invalid severity")
    linked = []
    for facet, row in coverage.items():
        if not isinstance(row, dict) or row.get("status") not in STATES:
            raise AuditError(facet + " has invalid status")
        ids_for_row = row.get("finding_ids", [])
        if not set(ids_for_row).issubset(finding_ids):
            raise AuditError(facet + " has invalid finding IDs")
        if row["status"] == "issues_found" and not ids_for_row:
            raise AuditError(facet + " needs finding IDs")
        if row["status"] in {"not_checked", "review_required"}:
            text(row, "reason", facet)
        if not complete and row["status"] in {"passed", "issues_found"}:
            text(row, "scope", facet)
            if row["scope"] == "full":
                raise AuditError(facet + " cannot claim full partial-manuscript coverage")
        linked.extend(ids_for_row)
    if set(linked) != finding_ids or len(linked) != len(finding_ids):
        raise AuditError("findings must be linked exactly once")
    verdict_counts = {key: 0 for key in sorted(JUDGEMENTS)}
    for claim in claims:
        verdict_counts[claim["judgement"]] += 1
    return {
        "validator_status": "ok", "fixture_id": data.get("fixture_id"),
        "coverage_scope": "full" if complete else "partial",
        "coverage_states": {facet: coverage[facet]["status"] for facet in FACETS},
        "support_counts": verdict_counts, "evidence_anchors": anchors,
        "counts": {"findings": len(finding_ids), "claims": len(claims)},
        "numerical_consistency_candidates": candidates,
        "note": "Source access is not semantic support; numeric candidates require human scope review."
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
