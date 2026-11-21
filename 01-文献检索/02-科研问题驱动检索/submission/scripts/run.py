#!/usr/bin/env python3
"""Dependency-aware research-question evidence convergence; no query execution."""
from collections import deque
from common import dedupe, norm_text, record_key, records_at, run


def process(case, data, task, inv):
    raw_questions = records_at(data, ["subquestions", "questions", "information_needs"])
    questions, by_id = [], {}
    for index, item in enumerate(raw_questions):
        qid = norm_text(item.get("id", item.get("subquestion_id", "sq%02d" % (index + 1))))
        if qid in by_id:
            qid = "%s~%d" % (qid, index + 1)
        question = dict(item)
        question.update({"id": qid, "depends_on": item.get("depends_on", item.get("dependencies", [])), "required": item.get("required", True)})
        questions.append(question)
        by_id[qid] = question
    graph_errors, visiting, visited, order = [], set(), set(), []

    def visit(qid):
        if qid in visiting:
            graph_errors.append({"type": "dependency_cycle", "subquestion_id": qid})
            return
        if qid in visited:
            return
        visiting.add(qid)
        for dep in by_id[qid]["depends_on"]:
            if dep not in by_id:
                graph_errors.append({"type": "missing_dependency", "subquestion_id": qid, "dependency": dep})
            else:
                visit(dep)
        visiting.remove(qid)
        visited.add(qid)
        order.append(qid)

    for question in questions:
        visit(question["id"])
    if graph_errors:
        order = []
    depth = {qid: 0 for qid in order}
    for qid in order:
        usable = [depth[d] for d in by_id[qid]["depends_on"] if d in depth]
        depth[qid] = max(usable) + 1 if usable else 0
    batches = [{"dependency_level": level, "parallel_subquestions": [q for q in order if depth[q] == level]} for level in sorted(set(depth.values()))]

    records = records_at(data, ["records", "papers", "candidates"])
    keys, clusters, possible, identity_conflicts = dedupe(records)
    master_of = {}
    for cluster in clusters:
        for member in cluster["members"]:
            master_of[member] = cluster["master"]
    evidence = {}
    dispositions = []
    for item, key in zip(records, keys):
        raw_id = norm_text(item.get("id", item.get("record_id", "")))
        master = master_of[key]
        sqids = item.get("subquestion_ids", item.get("subquestion_id", []))
        sqids = [sqids] if isinstance(sqids, str) else sqids
        strength = item.get("evidence_strength", item.get("role", "pending"))
        locator = item.get("source_locator", item.get("evidence_locator", ""))
        eligible = strength == "direct" and bool(norm_text(locator))
        status = strength if strength in {"direct", "indirect", "background", "excluded"} else "pending"
        if strength == "direct" and not eligible:
            status = "pending"
        dispositions.append({"input_record_id": raw_id or key, "canonical_id": master, "subquestion_ids": sqids, "disposition": status, "source_locator": locator, "fixture": item.get("fixture", False), "metadata": {k: item.get(k) for k in ["title", "authors", "year", "doi"]}, "note": "direct evidence requires an actual source locator; missing evidence stays pending"})
        if eligible:
            for sqid in sqids:
                evidence.setdefault(sqid, {})[master] = {"record_id": master, "source_locator": locator, "evidence_quote": item.get("evidence_quote", ""), "fixture": item.get("fixture", False)}
        if raw_id:
            evidence.setdefault("_aliases", {})[raw_id] = key
        evidence.setdefault("_aliases", {})[key] = key
    aliases = evidence.pop("_aliases", {})

    queries = records_at(data, ["queries", "search_rounds", "rounds"])
    by_sq_rounds = {q["id"]: [] for q in questions}
    seen_direct = {q["id"]: set() for q in questions}
    # Accepted direct evidence already present must be introduced by an observed round to earn convergence credit.
    for index, query in enumerate(queries):
        sqid = query.get("subquestion_id", query.get("question_id", ""))
        qid = norm_text(query.get("id", query.get("query_id", "q%02d" % (index + 1))))
        executed = query.get("executed") is True
        state = query.get("status", "observed" if executed else "planned")
        success = executed and state in {"success", "observed", "completed", "partial"}
        hits = query.get("hits", query.get("record_ids", []))
        hits = [hits] if isinstance(hits, str) else hits
        resolved = {master_of.get(aliases.get(str(hit), str(hit)), aliases.get(str(hit), str(hit))) for hit in hits}
        seen_direct.setdefault(sqid, set())
        newly_accepted = sorted(x for x in resolved if x in evidence.get(sqid, {}) and x not in seen_direct[sqid]) if success else []
        seen_direct[sqid].update(newly_accepted)
        event = {"query_id": qid, "subquestion_id": sqid, "depends_on": query.get("depends_on", []), "purpose": query.get("purpose", ""), "trigger": query.get("trigger", ""), "expression": query.get("expression", query.get("query", "")), "source": query.get("source", ""), "status": state, "executed": executed, "response_locator": query.get("response_locator", ""), "input_hit_ids": hits, "new_direct_evidence_ids": newly_accepted, "observed_new_terms": query.get("new_terms", []), "trigger_audit": "documented" if index == 0 or norm_text(query.get("trigger", "")) else "unexplained_round"}
        by_sq_rounds.setdefault(sqid, []).append(event)

    coverage, stops, unresolved = [], [], {}
    for question in questions:
        qid = question["id"]
        minimum = question.get("min_direct_sources", 1)
        minimum = minimum if isinstance(minimum, int) and minimum > 0 else 1
        accepted = sorted(seen_direct.get(qid, set()))
        rounds = by_sq_rounds.get(qid, [])
        successes = [x for x in rounds if x["executed"] and x["status"] in {"success", "observed", "completed", "partial"}]
        failures = [x for x in rounds if x["executed"] and x["status"] not in {"success", "observed", "completed", "partial"}]
        dependencies_ok = all(dep in depth and len(seen_direct.get(dep, set())) >= max(1, int(by_id[dep].get("min_direct_sources", 1))) for dep in question["depends_on"])
        if question.get("ambiguity", False) or question.get("requires_human", False):
            stop, state = "terminology_or_scope_requires_human_adjudication", "unresolved"
        elif len(accepted) >= minimum and dependencies_ok:
            stop, state = "sufficient_direct_evidence_and_dependencies_satisfied", "sufficient"
        elif not dependencies_ok:
            stop, state = "dependency_not_closed", "blocked"
        elif len(successes) >= 2 and not successes[-1]["new_direct_evidence_ids"] and not successes[-2]["new_direct_evidence_ids"]:
            stop, state = "two_consecutive_rounds_without_new_direct_evidence; evidence remains insufficient", "unresolved"
        elif failures and not successes:
            stop, state = "source_failure; absence of evidence not treated as absence of research", "blocked"
        elif not rounds:
            stop, state = "no_observed_query_or_direct_evidence", "unresolved"
        else:
            stop, state = "more_targeted_evidence_required", "unresolved"
        coverage.append({"subquestion_id": qid, "text": question.get("text", question.get("question", "")), "required": question["required"], "depends_on": question["depends_on"], "minimum_direct_sources": minimum, "accepted_direct_ids": accepted, "direct_evidence_count": len(accepted), "coverage_state": state, "terms": question.get("terms", []), "observed_queries": len(successes), "failed_queries": len(failures)})
        stops.append({"subquestion_id": qid, "stop_reason": stop, "stop_is_success": state == "sufficient"})
        if question["required"] and state != "sufficient":
            unresolved[qid] = stop
    if not questions:
        status = "empty"
    elif graph_errors or unresolved:
        status = "partial"
    else:
        status = "completed"
    return {
        "status": status, "status_detail": {"completed": ["dependency validation", "all-record disposition", "evidence-linked stop audit"], "incomplete": sorted(unresolved) + [x["type"] for x in graph_errors]},
        "research_question": data.get("research_question", case.get("research_question", "")),
        "dependency_graph": [{"subquestion_id": q["id"], "depends_on": q["depends_on"], "required": q["required"]} for q in questions],
        "graph_errors": graph_errors, "topological_order": order, "parallel_batches": batches,
        "subquestion_coverage": coverage, "query_rounds": [x for rounds in by_sq_rounds.values() for x in rounds],
        "evidence_map": evidence, "record_dispositions": dispositions, "primary_results": coverage,
        "stop_decisions": stops, "unresolved_subquestions": unresolved,
        "identity_clusters": clusters, "possible_duplicate_identities": possible, "identity_conflicts": identity_conflicts,
        "input_conflicts": data.get("conflicts", []), "fixture_policy": "Input duplicates/opinions remain synthetic fixtures and are never counted as real independent reviewers.",
        "uncertainties": ["Finite supplied records only; no live database or global recall claim.", "Two zero-yield rounds justify stopping work, not concluding that no research exists."],
        "change_records": ["Validated subquestion dependencies before evaluating evidence closure.", "Counted only direct evidence with source locators and deduplicated identities; retained every input record disposition."],
        "_aliases": {"results": "subquestion_coverage", "queries": "query_rounds", "evidence": "evidence_map", "stop_reasons": "stop_decisions", "unresolved": "unresolved_subquestions"},
    }


if __name__ == "__main__":
    raise SystemExit(run("question-dependency-convergence", process))
