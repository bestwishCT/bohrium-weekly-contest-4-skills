#!/usr/bin/env python3
"""Field-by-field conclusion/data extraction with four-state evidence discipline."""
import argparse
import json
import sys

from common import Run, add_arguments, evidence_ok, paragraph_map, paragraphs, read_json

STATES = {"reported", "not_reported", "source_conflict", "extraction_failed"}
VALUE_KEYS = ["scalar", "unit", "statistic", "experimental_condition", "comparator"]


def main():
    args = add_arguments(argparse.ArgumentParser(description=__doc__)).parse_args()
    try:
        run = Run(args)
    except Exception as error:
        print(json.dumps({"status": "input_error", "error": str(error)}), file=sys.stderr)
        return 2
    try:
        targets_path = run.path(run.case.get("target_fields", "target-fields.json"))
        targets_data = read_json(targets_path)
        targets = targets_data.get("fields", targets_data) if isinstance(targets_data, dict) else targets_data
        if not isinstance(targets, list) or not targets:
            raise ValueError("target-fields.json must contain requested fields")
        material_path = run.path(run.case.get("material_path", "material.json"))
        index = paragraph_map(paragraphs(read_json(material_path)))
        candidate = run.candidate("field-results.json")
        supplied = candidate.get("fields", candidate) if isinstance(candidate, dict) else candidate
        by_id = {}
        for item in supplied:
            identifier = str(item.get("field_id", ""))
            if not identifier or identifier in by_id:
                raise ValueError("missing or duplicate field_id in candidate")
            by_id[identifier] = item
        requested_ids = [str(field.get("field_id", "")) for field in targets]
        if len(set(requested_ids)) != len(requested_ids) or any(not value for value in requested_ids):
            raise ValueError("target field identifiers must be unique and nonempty")
        results = []
        failures = []
        sources = []
        for target in targets:
            field_id = str(target["field_id"])
            item = dict(by_id.get(field_id, {"status": "extraction_failed", "reason": "no candidate result"}))
            item["field_id"] = field_id
            item["field_definition"] = target.get("definition")
            state = item.get("status")
            reasons = []
            if state not in STATES:
                reasons.append("invalid four-state status")
            if state == "reported":
                if not evidence_ok(index, item):
                    reasons.append("reported result lacks exact paragraph_id/quote")
                else:
                    sources.append({"field_id": field_id, "paragraph_id": item["paragraph_id"], "quote": item["quote"]})
                if not item.get("condition_match_confirmed"):
                    reasons.append("experimental condition/comparator match was not explicitly confirmed")
                if item.get("scalar") is None and not item.get("text"):
                    reasons.append("reported field lacks scalar or text")
            elif state == "not_reported":
                for key in VALUE_KEYS:
                    if item.get(key) not in (None, ""):
                        reasons.append("not_reported field must not carry a guessed " + key)
                if not item.get("search_scope"):
                    reasons.append("not_reported needs a documented search scope")
            elif state == "source_conflict":
                references = item.get("evidence", [])
                if len(references) < 2:
                    reasons.append("source_conflict needs at least two source positions")
                for reference in references:
                    if not evidence_ok(index, reference):
                        reasons.append("conflict evidence quote/paragraph mismatch")
                    else:
                        sources.append({"field_id": field_id, **reference})
                if len(item.get("variants", [])) < 2:
                    reasons.append("source_conflict must preserve both values/conditions")
            elif state == "extraction_failed":
                if not item.get("reason") or not item.get("failed_location"):
                    reasons.append("extraction_failed needs reason and failed_location")
            else:
                reasons.append("missing candidate status")
            if field_id not in by_id:
                reasons.append("requested field was not processed")
            if reasons:
                item["status"] = "extraction_failed"
                item["reason"] = "; ".join(reasons)
                failures.extend({"field_id": field_id, "reason": reason} for reason in reasons)
            results.append(item)
        extras = sorted(set(by_id) - set(requested_ids))
        if extras:
            failures.append({"field_id": ",".join(extras), "reason": "candidate contains unrequested fields"})
        status = "partial" if failures or run.uncertainty else "success"
        run.changes.append("preserved one result per requested field and did not coerce absence to zero")
        run.finish({"status": status, "fields": results, "failures": failures,
                    "field_contract": targets, "sources": sources},
                   aliases={"extractedfields": "fields", "fieldresults": "fields", "result": "fields"},
                   code=2 if status == "partial" else 0,
                   trace_extra={"literal_quote_check": True, "semantic_support": "Agent confirmation required beyond literal match"})
    except Exception as error:
        run.uncertainty.append(str(error))
        run.finish({"status": "input_error", "fields": [], "failures": [{"reason": str(error)}]}, code=2)


if __name__ == "__main__":
    sys.exit(main())
