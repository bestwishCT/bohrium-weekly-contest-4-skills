#!/usr/bin/env python3
"""Source-bound multi-round query audit and conservative candidate ranking."""
from common import dedupe, digest, norm_text, norm_title, records_at, run


CONCEPT_TERMS = "lexical coverage only; not semantic proof"
DIRECTNESS = {"direct": 3, "indirect": 2, "background": 1, "unknown": 0}
DECISIONS = {"retain": 3, "pending": 2, "exclude": 0}


def process(case, data, task, inv):
    scope = data.get("scope", {}) if isinstance(data, dict) else {}
    queries = records_at(data, ["queries", "search_rounds", "rounds"])
    planned = records_at(data, ["planned_queries", "query_plan"])
    occurrences, query_log, failures = [], [], []
    for q_index, query in enumerate(queries):
        qid = norm_text(query.get("id", query.get("query_id", "q%02d" % (q_index + 1))))
        executed = query.get("executed") is True
        state = norm_text(query.get("status", "observed" if executed else "planned"))
        hits = query.get("hits", query.get("records", [])) if isinstance(query, dict) else []
        hits = hits if isinstance(hits, list) else []
        accepted_hits = hits if executed and state in {"success", "observed", "completed", "partial"} else []
        query_log.append({
            "query_id": qid, "purpose": query.get("purpose", ""), "expression": query.get("expression", query.get("query", "")),
            "source": query.get("source", ""), "parameters": query.get("parameters", {}), "executed": executed,
            "status": state, "response_locator": query.get("response_locator", ""), "observed_hit_count": len(accepted_hits),
            "stop_reason": query.get("stop_reason", ""), "new_terms": query.get("new_terms", []),
        })
        if executed and state not in {"success", "observed", "completed", "partial"}:
            failures.append({"query_id": qid, "status": state, "effect": "query coverage unknown; hits not used as candidates"})
        for hit_index, hit in enumerate(accepted_hits):
            if isinstance(hit, dict):
                item = dict(hit)
                item["_query_id"] = qid
                item["_query_index"] = q_index
                item["_hit_index"] = hit_index
                item["_response_locator"] = hit.get("source_locator", query.get("response_locator", ""))
                occurrences.append(item)
    keys, clusters, possible, conflicts = dedupe(occurrences)
    for item, key in zip(occurrences, keys):
        item["_key"] = key
    by_master = {}
    for cluster in clusters:
        by_master[cluster["master"]] = cluster
    grouped = {cluster["master"]: [] for cluster in clusters}
    for item in occurrences:
        for cluster in clusters:
            if item["_key"] in cluster["members"]:
                grouped[cluster["master"]].append(item)
                break
    concepts = scope.get("concepts", []) if isinstance(scope, dict) else []
    ranked = []
    for cluster in clusters:
        members = grouped[cluster["master"]]
        first = members[0]
        screening = first.get("screening", {}) if isinstance(first.get("screening", {}), dict) else {}
        decision = screening.get("decision", first.get("decision", "pending"))
        if decision not in DECISIONS:
            decision = "pending"
        directness = screening.get("directness", first.get("directness", "unknown"))
        text = norm_text(" ".join(norm_text(m.get("title", "")) + " " + norm_text(m.get("abstract", "")) for m in members)).casefold()
        covered, missing = [], []
        for concept in concepts:
            if isinstance(concept, str):
                name, terms = concept, [concept]
            else:
                name = norm_text(concept.get("id", concept.get("name", "concept")))
                terms = concept.get("variants", concept.get("terms", [concept.get("name", "")]))
            terms = [terms] if isinstance(terms, str) else terms
            used = sorted({norm_text(term) for term in terms if norm_text(term) and norm_text(term).casefold() in text})
            (covered if used else missing).append({"concept": name, "matched_variants": used})
        reasons = screening.get("reasons", first.get("reasons", []))
        evidence = screening.get("evidence", first.get("evidence", []))
        if not reasons:
            reasons = ["No explicit screened decision was supplied; kept pending rather than inferred from title."]
        metadata = {field: first.get(field) if field in first else None for field in ["title", "authors", "year", "venue", "doi"]}
        locators = sorted({norm_text(m.get("_response_locator")) for m in members if norm_text(m.get("_response_locator"))})
        ranked.append({
            "record_id": cluster["master"], "metadata": metadata, "decision": decision,
            "relevance_tier": screening.get("tier", directness if directness in DIRECTNESS else "unknown"),
            "facet_coverage": covered, "unmatched_facets": missing, "facet_rule": CONCEPT_TERMS,
            "reasons": reasons, "evidence": evidence, "source_locators": locators,
            "observed_in_queries": sorted({m["_query_id"] for m in members}),
            "duplicate_members": cluster["members"], "screening_confidence": screening.get("confidence", "unspecified"),
            "_rank": (DECISIONS[decision], DIRECTNESS.get(directness, 0), len(covered), len(members)),
            "_stable": norm_title(first.get("title", "")) + "|" + cluster["master"],
        })
    ranked.sort(key=lambda item: tuple(-x for x in item["_rank"]) + (item["_stable"],))
    previous, current_rank = None, 0
    for position, item in enumerate(ranked, 1):
        if item["_rank"] != previous:
            current_rank, previous = position, item["_rank"]
        item["rank"] = current_rank
        item["tie_semantics"] = "same rank when decision, directness, facet coverage and observed-source count tie; stable order uses normalized title and record id"
        item.pop("_rank")
        item.pop("_stable")
    retained = [x for x in ranked if x["decision"] != "exclude"]
    k = data.get("k", case.get("k")) if isinstance(data, dict) else None
    if not isinstance(k, int) or k < 0:
        k = len(retained)
    top = [{"rank": x["rank"], "record_id": x["record_id"], "reason": x["reasons"]} for x in retained[:k]]
    gaps = sorted({c["concept"] for item in ranked for c in item["unmatched_facets"]})
    pending = sum(item["decision"] == "pending" for item in ranked)
    if not occurrences:
        status = "partial" if failures or not queries else "empty"
    elif failures or pending:
        status = "partial"
    else:
        status = "completed"
    uncertainties = ["All conclusions are limited to supplied finite snapshots; no live or global recall is claimed.", CONCEPT_TERMS]
    if gaps:
        uncertainties.append("Concept facets without lexical coverage require targeted review: " + ", ".join(gaps))
    return {
        "status": status, "status_detail": {"completed": ["query accounting", "conservative identity clustering", "auditable candidate ranking"], "incomplete": [x["query_id"] for x in failures] + (["pending screening"] if pending else [])},
        "scope": scope, "query_plan": [{k: q.get(k) for k in ["id", "purpose", "expression", "source", "depends_on"]} for q in planned],
        "query_log": query_log, "candidates": ranked, "primary_results": ranked, "top_k": top,
        "dedup_clusters": clusters, "possible_duplicate_identities": possible, "source_conflicts": conflicts,
        "query_failures": failures, "residual_gaps": [{"concept": c, "status": "not_closed", "action": "review variants and run an authorized targeted query"} for c in gaps],
        "metrics": {"input_hit_records": len(occurrences), "unique_candidate_clusters": len(clusters), "pending": pending, "excluded": sum(x["decision"] == "exclude" for x in ranked), "failed_queries": len(failures), "k": k, "ranking_semantics": "head positions contain deduplicated clusters, not raw exports"},
        "uncertainties": uncertainties, "change_records": ["Audited only queries explicitly marked executed; unexecuted plans and failed responses generated no candidates.", "Applied conservative DOI/title-author-year clustering and preserved possible identities.", digest({"queries": query_log, "clusters": clusters})],
        "_aliases": {"results": "candidates", "hits": "candidates", "queries": "query_log", "gaps": "residual_gaps", "metrics_summary": "metrics"},
    }


if __name__ == "__main__":
    raise SystemExit(run("multi-round-source-search", process))
