#!/usr/bin/env python3
"""Rebuild systematic-review flow counts and audit protocol, quality and synthesis links."""
import argparse
import sys
sys.dont_write_bytecode = True
from runtime import cli, unique_ids


def refs(owner, values, available, label, errors, minimum=1):
    if not isinstance(values, list) or len(values) < minimum:
        errors.append("%s needs at least %d %s reference(s)" % (owner, minimum, label))
        return
    for value in values:
        if value not in available:
            errors.append("%s references unknown %s %s" % (owner, label, value))


def validate(data, sources, tools, evidence, base):
    errors, warnings = [], []
    d = data.get("data", {})
    protocol = d.get("protocol", {})
    if not protocol.get("version") or protocol.get("registered_before_screening") is not True:
        errors.append("systematic protocol must be versioned and registered before screening")
    if not protocol.get("question_structure") or not protocol.get("conflict_rule"):
        errors.append("protocol needs a structured question and criterion conflict rule")
    criteria = unique_ids(protocol.get("criteria", []), "criterion", errors)
    if not criteria:
        errors.append("protocol needs explicit inclusion/exclusion criteria")
    for criterion in criteria.values():
        if criterion.get("kind") not in {"include", "exclude"} or not criterion.get("test") or not isinstance(criterion.get("priority"), int):
            errors.append("criterion %s needs kind, test and explicit priority" % criterion.get("id"))
    for revision in protocol.get("revisions", []):
        if not revision.get("from_version") or not revision.get("reason") or not revision.get("affected_record_ids"):
            errors.append("protocol revision lacks prior version, reason or affected records")
        if revision.get("rescreened") is not True:
            errors.append("protocol revision did not rerun all affected records")
    queries = unique_ids(d.get("queries", []), "query", errors)
    returned = set()
    for qid, query in queries.items():
        for field in ("database", "expression", "fields", "time_limits", "language_limits", "stop_condition", "stop_reason"):
            if not query.get(field):
                errors.append(qid + " lacks executable search component " + field)
        if not isinstance(query.get("executed"), bool):
            errors.append(qid + " lacks actual execution status")
            continue
        hit_ids = query.get("returned_record_ids", [])
        returned.update(hit_ids)
        tid = query.get("tool_run_id")
        if query["executed"]:
            if not query.get("search_date") or tid not in tools or tools[tid].get("status") not in {"ok", "empty"}:
                errors.append(qid + " claims execution without a date and successful/empty actual run")
            if query.get("reported_hits") != len(hit_ids):
                errors.append(qid + " hit count does not equal retained returned record IDs")
        else:
            if hit_ids or query.get("reported_hits") not in (None, 0):
                errors.append(qid + " reports hits for a search that was not executed")
            if query.get("execution_limit") is None:
                errors.append(qid + " needs its unexecuted scope/permission limit")
    records = unique_ids(d.get("records", []), "record", errors)
    for rid in returned:
        if rid not in records:
            errors.append("query returns unregistered record " + rid)
    flow = {"input_records": len(records), "duplicates": 0, "title_excluded": 0,
            "fulltext_excluded": 0, "included": 0, "awaiting": 0}
    record_state = {}
    for rid, record in records.items():
        title = record.get("title_decision", {})
        full = record.get("fulltext_decision", {})
        if not title.get("reviewer") or title.get("protocol_version") != protocol.get("version"):
            errors.append(rid + " lacks actual reviewer or current protocol version at title stage")
        tstatus = title.get("status")
        if tstatus not in {"include", "exclude", "awaiting", "duplicate"}:
            errors.append(rid + " has invalid title-stage state")
        if tstatus == "duplicate":
            parent = record.get("duplicate_of")
            if parent not in records or parent == rid or records.get(parent, {}).get("title_decision", {}).get("status") == "duplicate":
                errors.append(rid + " has an invalid direct duplicate map")
            record_state[rid] = "duplicates"
        elif tstatus == "exclude":
            if not title.get("criterion_ids") or not title.get("reason"):
                errors.append(rid + " title exclusion lacks criterion and reason")
            record_state[rid] = "title_excluded"
        elif tstatus == "include":
            fstatus = full.get("status")
            if fstatus not in {"include", "exclude", "await_fulltext", "review"}:
                errors.append(rid + " needs an independent full-text decision or pending state")
                record_state[rid] = "awaiting"
            else:
                if not full.get("reviewer") or full.get("protocol_version") != protocol.get("version"):
                    errors.append(rid + " lacks actual full-text reviewer/current protocol version")
                if fstatus == "include":
                    record_state[rid] = "included"
                elif fstatus == "exclude":
                    if not full.get("criterion_ids") or not full.get("reason") or not full.get("locator"):
                        errors.append(rid + " full-text exclusion needs criterion, reason and source locator")
                    record_state[rid] = "fulltext_excluded"
                else:
                    record_state[rid] = "awaiting"
        else:
            record_state[rid] = "awaiting"
        for decision in (title, full):
            for criterion_id in decision.get("criterion_ids", []):
                if criterion_id not in criteria:
                    errors.append(rid + " cites unknown criterion " + criterion_id)
        flow[record_state[rid]] += 1
    included = {rid for rid, state in record_state.items() if state == "included"}
    for eid, row in evidence.items():
        if row.get("record_id") not in included:
            errors.append(eid + " evidence belongs outside the final included set")
    quality = unique_ids(d.get("quality", []), "quality", errors)
    quality_records = set()
    for qid, assessment in quality.items():
        rid = assessment.get("record_id")
        if rid not in included:
            errors.append(qid + " assesses a non-included study")
            continue
        if rid in quality_records:
            errors.append("duplicate quality assessment for " + rid)
        quality_records.add(rid)
        if not assessment.get("scale_basis") or not assessment.get("overall_rule") or not assessment.get("overall"):
            errors.append(qid + " lacks applicability, aggregation rule or bounded overall judgment")
        if isinstance(assessment.get("overall"), (int, float)):
            errors.append(qid + " invents an unsupported numeric quality score")
        dims = assessment.get("dimensions", [])
        if not dims:
            errors.append(qid + " has no dimension-level assessment")
        for dim in dims:
            if dim.get("judgment") not in {"low_concern", "some_concern", "high_concern", "not_assessable"}:
                errors.append(qid + " has invalid dimension judgment")
            refs(qid, [dim.get("evidence_id")], evidence, "evidence", errors)
            evidence_id = dim.get("evidence_id")
            if evidence_id in evidence and evidence[evidence_id].get("record_id") != rid:
                errors.append(qid + " evidence record_id mismatch: " + evidence_id)
            if not dim.get("rationale"):
                errors.append(qid + " lacks dimension rationale")
    if included != quality_records:
        errors.append("every and only finally included studies need quality assessment")
    claims = unique_ids(d.get("claims", []), "claim", errors)
    for cid, claim in claims.items():
        refs(cid, claim.get("evidence_ids"), evidence, "evidence", errors)
        refs(cid, claim.get("record_ids"), {rid: {} for rid in included}, "included record", errors)
        if not claim.get("quality_consideration") or claim.get("kind") not in {"material_fact", "bounded_inference", "unknown"}:
            errors.append(cid + " lacks quality-aware bounded synthesis classification")
    conflicts = data.get("conflicts", [])
    for conflict in conflicts:
        refs("conflict", conflict.get("evidence_ids"), evidence, "evidence", errors, minimum=2)
        if not conflict.get("comparison_dimensions") or "quality_dimension_differences" not in conflict:
            errors.append("systematic conflict must compare design and quality dimensions")
    gaps = d.get("gaps", [])
    for gap in gaps:
        refs(gap.get("id", "gap"), gap.get("basis_evidence_ids"), evidence, "evidence", errors)
        if not gap.get("query_scope") or not gap.get("excluded_nearby") or not gap.get("unresolved"):
            errors.append("systematic gap needs executed query scope, nearby exclusions and precise unresolved issue")
    if not evidence and (claims or gaps or included):
        errors.append("empty search cannot support synthesis, gaps, or completed full-text inclusion")
    flow["conserved"] = flow["input_records"] == sum(flow[k] for k in ("duplicates", "title_excluded", "fulltext_excluded", "included", "awaiting"))
    if not flow["conserved"]:
        errors.append("PRISMA-like counts are not conserved; synthesis must stop")
    derived = {"flow_counts": flow, "record_final_states": record_state, "included_record_ids": sorted(included),
               "quality_by_record": {qid: q.get("overall") for qid, q in quality.items()},
               "claim_quality_links": {cid: {"record_ids": c.get("record_ids", []), "quality": c.get("quality_consideration")} for cid, c in claims.items()},
               "resynthesis_guard": "stop if any record lacks exactly one terminal/pending state or any claim lacks quality-aware evidence"}
    audit = {"protocol": protocol, "queries": list(queries.values()), "screening_decisions": list(records.values()),
             "quality": list(quality.values()), "conflicts": conflicts, "gaps": gaps, "warnings": warnings,
             "second_reviewer_claimed": False,
             "stop_rule": "all declared searches are terminal or explicitly blocked; conservation errors prohibit synthesis"}
    return derived, audit, errors


if __name__ == "__main__":
    sys.exit(cli(argparse.ArgumentParser(description=__doc__), validate))
