#!/usr/bin/env python3
"""Validate a quality-review ledger; never infer manuscript defects."""
import argparse
import json
import sys
from pathlib import Path

FACETS = ("overall_structure", "fact_consistency", "citation_support",
          "methods_design", "statistics_report", "argumentation", "scholarly_norms")
STATES = {"passed", "issues_found", "review_required", "not_checked"}
SEVERITIES = {"critical", "major", "moderate", "minor", "advisory"}


class AuditError(ValueError):
    pass


def required_text(obj, key, where):
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AuditError("%s requires non-empty string %s" % (where, key))
    return value


def anchor(text, quote, where):
    count = text.count(quote)
    if count != 1:
        raise AuditError("%s quote must occur exactly once; found %d" % (where, count))
    offset = text.index(quote)
    return {"line": text.count("\n", 0, offset) + 1,
            "column": offset - text.rfind("\n", 0, offset)}


def validate(data):
    if not isinstance(data, dict):
        raise AuditError("root must be an object")
    manuscript = data.get("manuscript", {})
    text = required_text(manuscript, "text", "manuscript")
    complete = manuscript.get("complete")
    if not isinstance(complete, bool):
        raise AuditError("manuscript.complete must be boolean")
    requested = manuscript.get("requested_sections")
    readable = manuscript.get("readable_sections")
    if not isinstance(requested, list) or not isinstance(readable, list) or not requested:
        raise AuditError("requested_sections and readable_sections must be arrays")
    if complete and set(requested) != set(readable):
        raise AuditError("complete manuscript cannot omit requested sections")
    review = data.get("review", {})
    coverage = review.get("coverage")
    if not isinstance(coverage, dict) or set(coverage) != set(FACETS):
        raise AuditError("coverage must exactly contain the seven quality facets")
    findings = review.get("findings")
    if not isinstance(findings, list):
        raise AuditError("review.findings must be an array")
    ids = set()
    anchors = []
    for index, finding in enumerate(findings):
        where = "findings[%d]" % index
        if not isinstance(finding, dict):
            raise AuditError(where + " must be an object")
        fid = required_text(finding, "id", where)
        if fid in ids:
            raise AuditError("duplicate finding id " + fid)
        ids.add(fid)
        facet = required_text(finding, "facet", where)
        if facet not in FACETS:
            raise AuditError(where + " has unknown facet")
        quote = required_text(finding, "quote", where)
        location = finding.get("location")
        if not isinstance(location, dict) or not required_text(location, "section", where):
            raise AuditError(where + " needs a section")
        if len([k for k, v in location.items() if k != "section" and v not in (None, "")]) == 0:
            raise AuditError(where + " needs paragraph, sentence, page, table or figure locator")
        for key in ("type", "severity", "severity_reason", "basis", "recommendation", "certainty"):
            required_text(finding, key, where)
        if finding["severity"] not in SEVERITIES:
            raise AuditError(where + " has unknown severity")
        anchors.append({"finding_id": fid, **anchor(text, quote, fid)})
        for eid, ev in enumerate(finding.get("evidence", [])):
            ewhere = "%s evidence[%d]" % (fid, eid)
            equote = required_text(ev, "quote", ewhere)
            source = required_text(ev, "source", ewhere)
            if source == "manuscript":
                anchor(text, equote, ewhere)
            elif source.startswith("source:"):
                name = source.split(":", 1)[1]
                sources = data.get("sources", {})
                if name not in sources:
                    raise AuditError(ewhere + " references absent source")
                required_text(sources[name], "text", ewhere)
                anchor(sources[name]["text"], equote, ewhere)
            else:
                raise AuditError(ewhere + " has unknown evidence source")
            if not isinstance(ev.get("location"), dict) or not ev["location"]:
                raise AuditError(ewhere + " needs location")
    linked = []
    for facet, row in coverage.items():
        if not isinstance(row, dict) or row.get("status") not in STATES:
            raise AuditError(facet + " has invalid status")
        if row["status"] in {"review_required", "not_checked"}:
            required_text(row, "reason", facet)
        finding_ids = row.get("finding_ids", [])
        if not isinstance(finding_ids, list) or not set(finding_ids).issubset(ids):
            raise AuditError(facet + " has invalid finding_ids")
        if row["status"] == "issues_found" and not finding_ids:
            raise AuditError(facet + " needs finding_ids")
        if row["status"] == "passed" and finding_ids:
            raise AuditError(facet + " cannot pass with linked findings")
        if row["status"] == "passed" and facet == "overall_structure":
            required_text(row, "scope", facet)
        if not complete and row["status"] in {"passed", "issues_found"}:
            required_text(row, "scope", facet)
            if row["scope"] == "full":
                raise AuditError(facet + " cannot claim full coverage for incomplete manuscript")
        linked.extend(finding_ids)
    if set(linked) != ids or len(linked) != len(ids):
        raise AuditError("every finding must be linked exactly once by coverage")
    for finding in findings:
        if finding["facet"] not in [f for f, r in coverage.items() if finding["id"] in r.get("finding_ids", [])]:
            raise AuditError("finding facet and coverage link disagree")
    return {
        "validator_status": "ok",
        "fixture_id": data.get("fixture_id"),
        "coverage_scope": "full" if complete else "partial",
        "coverage_states": {f: coverage[f]["status"] for f in FACETS},
        "evidence_anchors": anchors,
        "counts": {"findings": len(findings), "anchors": len(anchors)},
        "automated_defect_candidates": [],
        "note": "Mechanical evidence validation only; no scientific verdicts are inferred."
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.ledger.read_text(encoding="utf-8"))
        result = validate(data)
    except json.JSONDecodeError as exc:
        result = {"validator_status": "input_error", "error": {"code": "INVALID_JSON", "message": "line %d column %d" % (exc.lineno, exc.colno)}}
        print(json.dumps(result), file=sys.stderr)
        return 2
    except (OSError, AuditError, KeyError, TypeError) as exc:
        code = "INPUT_READ_ERROR" if isinstance(exc, OSError) else "INVALID_LEDGER"
        result = {"validator_status": "input_error", "error": {"code": code, "message": str(exc)}}
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
