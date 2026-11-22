#!/usr/bin/env python3
"""Audit falsifiable research ideas, nearest-work coverage and conditional feasibility."""
import argparse
import sys
sys.dont_write_bytecode = True
from runtime import cli, unique_ids

COVERAGE = {"fully_covered", "partially_covered", "not_directly_covered_in_scope", "unknown"}


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
    problem = d.get("problem", {})
    for field in ("object", "baseline", "expected_outcome", "boundary"):
        if not problem.get(field):
            errors.append("research idea lacks " + field)
    if not problem.get("variables") or not problem.get("falsifiable_observation") or not problem.get("refute_if"):
        errors.append("research idea needs variables plus a falsifiable observation and rejection condition")
    searches = unique_ids(d.get("searches", []), "nearest-work search", errors)
    executed_scope = []
    for sid, search in searches.items():
        for field in ("source", "expression", "fields", "time_window", "inclusion_rule", "stop_condition", "stop_reason"):
            if not search.get(field):
                errors.append(sid + " lacks bounded nearest-work search component " + field)
        tid = search.get("tool_run_id")
        if search.get("executed") is True:
            executed_scope.append(search)
            if tid not in tools or tools[tid].get("status") not in {"ok", "empty"}:
                errors.append(sid + " claims search execution without actual success/empty status")
        elif search.get("executed") is not False or not search.get("unexecuted_limit"):
            errors.append(sid + " needs honest unexecuted state/limit")
    records = unique_ids(d.get("records", []), "nearest-work record", errors)
    included = set()
    for rid, record in records.items():
        if not isinstance(record.get("eligible"), bool) or not record.get("reason"):
            errors.append(rid + " lacks eligibility decision/reason")
        if record.get("eligible") is True:
            included.add(rid)
            for field in ("task", "method", "data", "evaluation", "conclusion", "non_comparable_reason"):
                if field not in record:
                    errors.append(rid + " comparison matrix lacks " + field)
        if record.get("duplicate_of") and (record["duplicate_of"] not in records or record["duplicate_of"] == rid):
            errors.append(rid + " has invalid duplicate map")
    for eid, row in evidence.items():
        if row.get("record_id") and row["record_id"] not in included:
            errors.append(eid + " supports nearest-work comparison from a non-included record")
    claims = unique_ids(d.get("atomic_claims", []), "atomic novelty claim", errors)
    coverage_by_claim = {}
    claim_evidence = {}
    malformed_neighbor = False
    search_by_tool = {search.get("tool_run_id"): sid for sid, search in searches.items()}
    for cid, claim in claims.items():
        if claim.get("type") not in {"method", "data", "context_transfer", "conclusion_revision"}:
            errors.append(cid + " must classify method/data/context-transfer/conclusion-revision increment")
        if claim.get("overall_coverage") not in COVERAGE or not claim.get("coverage_rationale"):
            errors.append(cid + " lacks bounded coverage state/rationale")
        if claim.get("global_novelty_claimed") is not False or not claim.get("bounded_statement"):
            errors.append(cid + " must explicitly reject an unsupported global novelty claim")
        neighbors = claim.get("neighbors", [])
        if not isinstance(neighbors, list):
            warnings.append(cid + " neighbors must be an array; coverage is degraded")
            malformed_neighbor = True
            neighbors = []
        seen_states = set()
        linked_evidence = set()
        valid_neighbors = 0
        for neighbor in neighbors:
            if not isinstance(neighbor, dict):
                warnings.append(cid + " has a non-object neighbor; it was excluded from coverage")
                malformed_neighbor = True
                continue
            rid = neighbor.get("record_id")
            if rid not in included:
                errors.append(cid + " nearest work is absent or ineligible")
                continue
            state = neighbor.get("coverage")
            if state not in COVERAGE:
                errors.append(cid + " neighbor has invalid coverage state")
            else:
                seen_states.add(state)
            valid_neighbors += 1
            for field in ("shared", "remaining_difference", "comparison_limits"):
                if not neighbor.get(field):
                    errors.append(cid + " nearest-work comparison lacks " + field)
            refs(cid, neighbor.get("evidence_ids"), evidence, "evidence", errors)
            for evidence_id in neighbor.get("evidence_ids") or []:
                linked_evidence.add(evidence_id)
                if evidence_id in evidence and evidence[evidence_id].get("record_id") != rid:
                    errors.append(cid + " nearest-work evidence record_id mismatch: " + evidence_id)
        claim_evidence[cid] = linked_evidence
        declared_search_ids = claim.get("search_ids")
        if declared_search_ids is not None:
            refs(cid, declared_search_ids, searches, "atomic-claim search", errors)
        evidence_search_ids = {search_by_tool[evidence[eid]["tool_run_id"]] for eid in linked_evidence
                               if eid in evidence and evidence[eid].get("tool_run_id") in search_by_tool}
        bound_search_ids = set(declared_search_ids) if isinstance(declared_search_ids, list) else evidence_search_ids
        if declared_search_ids is not None and not evidence_search_ids <= bound_search_ids:
            errors.append(cid + " neighbor evidence comes from an unbound search")
        overall = claim.get("overall_coverage")
        if valid_neighbors:
            if "fully_covered" in seen_states:
                expected = "fully_covered"
            elif "partially_covered" in seen_states:
                expected = "partially_covered"
            elif "unknown" in seen_states:
                expected = "unknown"
            else:
                expected = "not_directly_covered_in_scope"
            if overall != expected:
                errors.append(cid + " overall_coverage must be derived from its neighbors as " + expected)
        elif overall in {"fully_covered", "partially_covered"}:
            errors.append(cid + " cannot derive direct/partial coverage without valid neighbors")
        bound_scope = [searches[sid] for sid in bound_search_ids if sid in searches and searches[sid].get("executed") is True]
        if overall != "unknown" and not bound_scope:
            errors.append(cid + " lacks an executed search bound to this atomic claim")
        if overall == "not_directly_covered_in_scope" and (not bound_scope or not claim.get("scope_limited_rationale")):
            errors.append(cid + " cannot claim no direct coverage without claim-bound executed scope and qualified rationale")
        if overall == "unknown" and not claim.get("coverage_rationale"):
            errors.append(cid + " unknown coverage needs its cause")
        increment = claim.get("increment_label")
        if increment not in {"method_increment", "data_increment", "context_transfer_only", "conclusion_revision", "no_established_increment"}:
            errors.append(cid + " lacks an honest increment label")
        if claim.get("type") == "context_transfer" and increment not in {"context_transfer_only", "no_established_increment"}:
            errors.append(cid + " inflates context transfer into method/data/theory novelty")
        if claim.get("type") == "conclusion_revision" and not data.get("conflicts"):
            errors.append(cid + " conclusion correction needs opposing evidence rather than intuition")
        coverage_by_claim[cid] = overall
    assumptions = unique_ids(d.get("assumptions", []), "feasibility premise", errors)
    required_categories = {"data", "compute", "experiment", "time", "personnel", "baseline"}
    categories = {a.get("category") for a in assumptions.values()}
    if categories != required_categories:
        errors.append("feasibility must cover exactly data/compute/experiment/time/personnel/baseline categories")
    critical_missing = set()
    for aid, assumption in assumptions.items():
        state = assumption.get("status")
        if state not in {"verified", "assumption", "missing"}:
            errors.append(aid + " has invalid premise state")
        if state == "verified":
            refs(aid, assumption.get("evidence_ids"), evidence, "evidence", errors)
        else:
            if not assumption.get("needed_material") or not assumption.get("impact"):
                errors.append(aid + " unverified premise lacks needed material/impact")
            if state == "missing" and assumption.get("critical"):
                critical_missing.add(aid)
    feasibility = d.get("feasibility", {})
    decision = feasibility.get("decision")
    if decision not in {"proceed", "revise", "defer", "unknown"}:
        errors.append("recommendation must be proceed/revise/defer/unknown")
    refs("feasibility", feasibility.get("premise_ids"), assumptions, "premise", errors)
    for aid, assumption in assumptions.items():
        if assumption.get("critical") and aid not in feasibility.get("premise_ids", []):
            errors.append("feasibility omits critical premise " + aid)
    if decision == "proceed" and critical_missing:
        errors.append("cannot recommend proceed while critical premises are missing")
    if decision != "unknown":
        refs("feasibility", feasibility.get("evidence_ids"), evidence, "evidence", errors)
        if not feasibility.get("conditions"):
            errors.append("bounded recommendation needs explicit conditions")
    if not evidence and decision != "unknown":
        errors.append("empty retrieval must lead to an unable-to-judge recommendation")
    if claims and all(c.get("overall_coverage") == "fully_covered" for c in claims.values()) and decision == "proceed":
        errors.append("fully covered atomic novelty claims require revision/deferral, not novelty-based proceed")
    validation = feasibility.get("minimal_validation", {})
    if validation.get("status") != "planned_only" or not validation.get("observation"):
        errors.append("minimum validation must remain a planned observable test, never a claimed completed experiment")
    if not feasibility.get("failure_conditions") or not feasibility.get("stop_conditions") or not feasibility.get("invalidation_triggers"):
        errors.append("feasibility needs failure, observable stop/turn and recommendation-invalidation conditions")
    conflicts = data.get("conflicts", [])
    for conflict in conflicts:
        label = conflict.get("id", "conflict")
        conflict_evidence = set(conflict.get("evidence_ids") or [])
        refs(label, conflict.get("evidence_ids"), evidence, "evidence", errors, minimum=2)
        roles = conflict.get("supporting_and_opposing")
        if not isinstance(roles, dict) or not conflict.get("comparison_limits"):
            errors.append("novelty conflict must retain support/opposition and comparison limits")
            roles = {}
        supporting = roles.get("supporting")
        opposing = roles.get("opposing")
        if not isinstance(supporting, list) or not supporting or not isinstance(opposing, list) or not opposing:
            errors.append(label + " needs non-empty supporting and opposing evidence lists")
            supporting, opposing = supporting or [], opposing or []
        refs(label, supporting, evidence, "supporting evidence", errors)
        refs(label, opposing, evidence, "opposing evidence", errors)
        if set(supporting) & set(opposing) or set(supporting) | set(opposing) != conflict_evidence:
            errors.append(label + " supporting/opposing roles must partition evidence_ids")
        cid = conflict.get("claim_id")
        candidates = {claim_id for claim_id, linked in claim_evidence.items() if conflict_evidence <= linked}
        if cid is None and len(candidates) == 1:
            cid = next(iter(candidates))
        if cid not in claims:
            errors.append(label + " must bind to exactly one atomic claim")
            continue
        if candidates != {cid} or not conflict_evidence <= claim_evidence.get(cid, set()):
            errors.append(label + " evidence is not bound to atomic claim " + cid)
        for evidence_id in supporting + opposing:
            if evidence_id not in claim_evidence.get(cid, set()):
                errors.append(label + " role evidence is unrelated to atomic claim " + cid + ": " + evidence_id)
    gaps = d.get("gaps", [])
    for gap in gaps:
        refs(gap.get("id", "gap"), gap.get("basis_evidence_ids"), evidence, "evidence", errors)
        if not gap.get("query_scope") or not gap.get("excluded_nearby") or not gap.get("remaining_difference"):
            errors.append("proposal gap needs query scope, nearby exclusions and residual difference")
    if not evidence and gaps:
        errors.append("empty search cannot establish a proposal gap")
    derived = {"coverage_by_atomic_claim": coverage_by_claim,
               "nearest_work_dimensions": {rid: {k: records[rid].get(k) for k in ("task", "method", "data", "evaluation", "conclusion", "non_comparable_reason")} for rid in included},
               "premise_dependencies": {aid: {"status": a.get("status"), "critical": bool(a.get("critical"))} for aid, a in assumptions.items()},
               "recommendation_guard": "No novelty score, global-first assertion, assumed resource availability or completed experiment."}
    audit = {"problem": problem, "executed_search_scope": executed_scope, "nearest_work": list(records.values()),
             "atomic_claims": list(claims.values()), "assumptions": list(assumptions.values()),
             "feasibility": feasibility, "conflicts": conflicts, "gaps": gaps, "warnings": warnings,
             "partial": malformed_neighbor,
             "coverage_boundary": "not directly covered in the actual supplied scope is not global novelty"}
    return derived, audit, errors


if __name__ == "__main__":
    sys.exit(cli(argparse.ArgumentParser(description=__doc__), validate))
