#!/usr/bin/env python3
"""Validate a dependency-aware research run and produce an audit trail."""
import argparse
import json
import sys
sys.dont_write_bytecode = True
from runtime import cli, unique_ids


def ids(rows):
    return {row["id"]: row for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)}


def ref_errors(owner, refs, available, label, errors, minimum=1):
    if not isinstance(refs, list) or len(refs) < minimum:
        errors.append("%s needs at least %d %s reference(s)" % (owner, minimum, label))
        return
    for ref in refs:
        if ref not in available:
            errors.append("%s references unknown %s %s" % (owner, label, ref))


def topological(questions, errors):
    pending = dict(questions)
    levels, done, level = {}, set(), 0
    while pending:
        ready = [qid for qid, q in pending.items() if set(q.get("depends_on", [])) <= done]
        if not ready:
            errors.append("subquestion dependency graph has a cycle: " + ",".join(sorted(pending)))
            break
        for qid in sorted(ready):
            levels[qid] = level
        done.update(ready)
        for qid in ready:
            pending.pop(qid)
        level += 1
    return levels


def validate(data, sources, tools, evidence, base):
    errors, warnings = [], []
    d = data.get("data", {})
    for field in ("object", "comparison", "time_scope", "evidence_requirement"):
        if not d.get("question", {}).get(field):
            errors.append("question lacks " + field)
    questions = unique_ids(d.get("subquestions", []), "subquestion", errors)
    levels = topological(questions, errors)
    plans, execution_steps = {}, {}
    for qid, q in questions.items():
        execution_steps[qid] = q.get("execution_step")
        if q.get("status") not in {"answered", "partial", "blocked"}:
            errors.append(qid + " has invalid coverage state")
        if not q.get("source_scope") or not q.get("stop_condition") or not q.get("stop_reason"):
            errors.append(qid + " lacks source scope or an actually evaluated stop condition/reason")
        if not isinstance(q.get("execution_step"), int):
            errors.append(qid + " lacks an integer execution_step")
        for dep in q.get("depends_on", []):
            if dep not in questions:
                errors.append(qid + " has unknown dependency " + dep)
            elif q.get("execution_step") is not None and not isinstance(q.get("execution_step"), int):
                errors.append(qid + " has non-integer execution ordering")
        for plan in q.get("search_plan", []):
            if not isinstance(plan, dict) or not plan.get("id"):
                errors.append(qid + " has a search plan without id")
                continue
            pid = plan["id"]
            plans[pid] = plan
            if not plan.get("query") or not plan.get("fields") or not plan.get("stop_condition"):
                errors.append(pid + " is not an executable bounded search plan")
            if plan.get("state") not in {"executed", "not_executed"}:
                errors.append(pid + " lacks honest execution state")
            tid = plan.get("tool_run_id")
            if tid and tid not in tools:
                errors.append(pid + " references an unknown tool run")
            if plan.get("state") == "executed" and (tid not in tools or tools[tid].get("status") == "not_executed"):
                errors.append(pid + " claims execution without an executed tool")
            if qid not in plan.get("subquestion_ids", [qid]):
                errors.append(pid + " does not point back to its subquestion")
    for qid, q in questions.items():
        for dep in q.get("depends_on", []):
            if dep in questions and isinstance(q.get("execution_step"), int) and isinstance(questions[dep].get("execution_step"), int):
                if questions[dep]["execution_step"] >= q["execution_step"]:
                    errors.append(qid + " executes before its dependency " + dep)
    seen_queries = set()
    for tid, tool in tools.items():
        if "search" not in tool.get("kind", "") or tool.get("status") == "not_executed":
            continue
        if tool.get("retry_of"):
            continue
        signature = json.dumps({"source": tool.get("source"), "parameters": tool.get("parameters")},
                               sort_keys=True, ensure_ascii=False)
        if signature in seen_queries:
            errors.append("meaningless duplicate executed search at " + tid)
        seen_queries.add(signature)
    claims = unique_ids(d.get("claims", []), "claim", errors)
    backlinks = {}
    for cid, claim in claims.items():
        ref_errors(cid, claim.get("evidence_ids"), evidence, "evidence", errors)
        ref_errors(cid, claim.get("subquestion_ids"), questions, "subquestion", errors)
        if claim.get("kind") not in {"material_fact", "limited_inference", "unknown"}:
            errors.append(cid + " must distinguish fact, bounded inference, or unknown")
        if claim.get("kind") != "unknown" and not claim.get("evidence_ids"):
            errors.append(cid + " makes an unsupported substantive statement")
        for eid in claim.get("evidence_ids", []):
            if eid in evidence:
                overlap = set(claim.get("subquestion_ids", [])) & set(evidence[eid].get("subquestion_ids", []))
                if not overlap:
                    errors.append(cid + " and " + eid + " do not share a subquestion")
                backlinks.setdefault(eid, []).append(cid)
    for qid, q in questions.items():
        if q.get("status") == "answered":
            linked = [eid for eid, e in evidence.items() if qid in e.get("subquestion_ids", [])]
            if not linked:
                errors.append(qid + " is marked answered without evidence")
    conflicts = d.get("conflicts", data.get("conflicts", []))
    for index, conflict in enumerate(conflicts):
        label = "conflict[%d]" % index
        ref_errors(label, conflict.get("evidence_ids"), evidence, "evidence", errors, minimum=2)
        if len(conflict.get("comparison_dimensions", [])) < 2 or not conflict.get("resolution"):
            errors.append(label + " needs comparison dimensions and resolved/unresolved boundary")
        source_ids = {evidence[e]["source_id"] for e in conflict.get("evidence_ids", []) if e in evidence}
        if len(source_ids) < 2:
            errors.append(label + " must retain at least two independent source records")
    gaps = d.get("gaps", [])
    for gap in gaps:
        ref_errors(gap.get("id", "gap"), gap.get("basis_evidence_ids"), evidence, "evidence", errors)
        if not gap.get("covered_scope") or not gap.get("missing_comparison") or "excluded_nearby" not in gap:
            errors.append("gap must state covered scope, exact missing comparison and nearby exclusions")
    if not evidence and gaps:
        errors.append("empty retrieval cannot independently establish a research gap")
    if not evidence and claims:
        errors.append("empty retrieval cannot contain substantive claims")
    stats = {"subquestion_count": len(questions), "answered_subquestions": sorted(qid for qid, q in questions.items() if q.get("status") == "answered"),
             "claim_count": len(claims), "evidence_count": len(evidence), "search_plan_count": len(plans),
             "executed_unique_search_count": len(seen_queries), "dependency_levels": levels}
    derived = {"coverage": stats, "claim_evidence_backlinks": backlinks,
               "search_plans": list(plans.values()),
               "conclusion_guard": "unlinked factual or causal clauses are invalid; empty searches cannot establish novelty"}
    audit = {"question_dimensions": d.get("question", {}), "subquestions": list(questions.values()),
             "stop_conditions_evaluated": True, "conflicts": conflicts, "gaps": gaps,
             "six_required_views": {"decomposition": bool(questions), "search_coverage": bool(plans),
                                    "evidence_backlinks": bool(backlinks) or not claims,
                                    "conflicts_retained": conflicts, "bounded_synthesis": list(claims),
                                    "research_gaps_or_limit": gaps or "none established within declared scope"}}
    return derived, audit, errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sys.exit(cli(parser, validate))
