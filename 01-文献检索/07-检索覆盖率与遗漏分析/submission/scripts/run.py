#!/usr/bin/env python3
"""Frozen-reference coverage matrix and measured supplement increments."""
from common import dedupe, digest, first_author, norm_doi, norm_text, norm_title, records_at, run


def match_one(reference, candidates, round_id):
    ref_doi, ref_title = norm_doi(reference.get("doi", "")), norm_title(reference.get("title", ""))
    ref_author, ref_year = first_author(reference), norm_text(reference.get("year", ""))
    review = []
    for index, candidate in enumerate(candidates):
        doi = norm_doi(candidate.get("doi", ""))
        title = norm_title(candidate.get("title", ""))
        cid = norm_text(candidate.get("id", candidate.get("record_id", "%s-record-%d" % (round_id, index))))
        locator = candidate.get("source_locator", "")
        if ref_doi and doi and ref_doi == doi:
            return {"status": "hit", "candidate_id": cid, "rule": "normalized_doi_exact", "source_locator": locator, "round_id": round_id}, review
        if ref_title and title and ref_title == title:
            if ref_author and ref_year and ref_author == first_author(candidate) and ref_year == norm_text(candidate.get("year", "")) and (not ref_doi or not doi or ref_doi == doi):
                return {"status": "hit", "candidate_id": cid, "rule": "normalized_title_first_author_year", "source_locator": locator, "round_id": round_id}, review
            review.append({"candidate_id": cid, "reason": "title candidate lacks corroborating metadata or has conflicting identifiers", "source_locator": locator})
    return None, review


def process(case, data, task, inv):
    reference_container = data.get("reference_set", {}) if isinstance(data, dict) else {}
    references = reference_container.get("records", records_at(data, ["reference_records", "references"]))
    keys, clusters, possible, identity_conflicts = dedupe(references)
    canonical_refs = []
    for cluster in clusters:
        members = [dict(item) for item, key in zip(references, keys) if key in cluster["members"]]
        first = members[0]
        votes = [item.get("evaluable", True) for item in members]
        mixed = len(set(bool(v) for v in votes)) > 1
        evaluable = bool(first.get("evaluable", True)) and not mixed
        canonical_refs.append({"reference_id": norm_text(first.get("id", first.get("record_id", cluster["master"]))), "canonical_id": cluster["master"], "original_ids": cluster["members"], "record": first, "evaluable": evaluable, "denominator_review": mixed, "source_locator": first.get("source_locator", ""), "inclusion_reason": first.get("inclusion_reason", ""), "not_evaluable_reason": first.get("not_evaluable_reason", "")})
    frozen_refs = sorted(references, key=lambda item: digest(item))
    freeze_hash = digest({"declared_version": reference_container.get("version"), "records": frozen_refs})
    rounds = records_at(data, ["search_rounds", "rounds", "queries"])
    states = {ref["canonical_id"]: {"status": "not_missed_yet", "matches": [], "review_candidates": []} for ref in canonical_refs}
    round_audit, failures, increments = [], [], []
    baseline_done = False
    before_states = None
    for r_index, search in enumerate(rounds):
        rid = norm_text(search.get("id", search.get("query_id", "round%02d" % (r_index + 1))))
        kind = search.get("kind", "baseline" if not baseline_done else "supplement")
        executed = search.get("executed") is True
        state = search.get("status", "observed" if executed else "planned")
        success = executed and state in {"success", "observed", "completed", "partial"}
        candidates = search.get("records", search.get("hits", [])) if success else []
        newly_hit, review_count = [], 0
        for ref in canonical_refs:
            current = states[ref["canonical_id"]]
            if current["status"] == "hit":
                continue
            match, reviews = match_one(ref["record"], candidates, rid)
            current["review_candidates"].extend({**review, "round_id": rid} for review in reviews)
            review_count += len(reviews)
            if match:
                current["status"] = "hit"
                current["matches"].append(match)
                newly_hit.append(ref["canonical_id"])
        if not success:
            failures.append({"round_id": rid, "status": state, "effect": "query surface unknown; does not establish a miss"})
        event = {"round_id": rid, "kind": kind, "expression": search.get("expression", search.get("query", "")), "source": search.get("source", ""), "fields": search.get("fields", []), "time_window": search.get("time_window"), "status": state, "executed": executed, "response_locator": search.get("response_locator", ""), "new_unique_reference_hits": newly_hit, "new_hit_count": len(newly_hit), "review_candidate_count": review_count}
        round_audit.append(event)
        if kind == "supplement":
            increments.append(event)
        else:
            before_states = {key: value["status"] if value["status"] == "hit" else "pending" if value["review_candidates"] else "missed" for key, value in states.items()}
            baseline_done = True
    if before_states is None:
        before_states = {key: value["status"] if value["status"] == "hit" else "pending" if value["review_candidates"] else "missed" for key, value in states.items()}
    for ref in canonical_refs:
        state = states[ref["canonical_id"]]
        if state["status"] != "hit":
            state["status"] = "pending" if state["review_candidates"] else "missed"
    denominator_refs = [ref for ref in canonical_refs if ref["evaluable"]]
    denominator = len(denominator_refs)

    def snapshot(status_map):
        numerator = sum(status_map.get(ref["canonical_id"]) == "hit" for ref in denominator_refs)
        missed = sum(status_map.get(ref["canonical_id"]) == "missed" for ref in denominator_refs)
        pending = sum(status_map.get(ref["canonical_id"]) == "pending" for ref in denominator_refs)
        return {"denominator": denominator, "numerator_hits": numerator, "coverage": numerator / denominator if denominator else None, "missed": missed, "pending": pending, "miss_rate": missed / denominator if denominator else None, "note": "pending is not a hit; empty denominator yields null, never 100%"}

    before = snapshot(before_states)
    final_states = {key: value["status"] for key, value in states.items()}
    after = snapshot(final_states)
    supplied_causes = data.get("miss_causes", []) if isinstance(data, dict) else []
    matrix = []
    for ref in canonical_refs:
        state = states[ref["canonical_id"]]
        causes = []
        if state["status"] == "missed":
            causes = [{k: v for k, v in cause.items() if k in {"category", "evidence", "query_ids", "source", "fields", "time_window"}} for cause in supplied_causes if cause.get("reference_id") in {ref["reference_id"], ref["canonical_id"]}]
            if not causes:
                causes = [{"category": "undetermined", "evidence": "no cause-specific source/query evidence supplied; no speculative attribution"}]
        matrix.append({**{k: ref[k] for k in ["reference_id", "canonical_id", "original_ids", "source_locator", "inclusion_reason", "evaluable", "denominator_review", "not_evaluable_reason"]}, "hit_status": state["status"], "matches": state["matches"], "review_candidates": state["review_candidates"], "miss_causes": causes, "is_residual_omission": state["status"] != "hit" and ref["evaluable"]})
    unresolved_denominator = sum(ref["denominator_review"] for ref in canonical_refs)
    if not references:
        status = "empty"
    elif denominator == 0 or unresolved_denominator or failures:
        status = "partial"
    elif any(x["hit_status"] == "pending" for x in matrix) or any(c["category"] == "undetermined" for x in matrix for c in x["miss_causes"]):
        status = "needs_review"
    else:
        status = "completed"
    stop = data.get("stop_reason", "no observed supplement rounds" if not increments else "last supplement yielded zero new unique reference hits" if increments[-1]["new_hit_count"] == 0 else "supplement produced observed new hits; stop only under declared case budget")
    return {
        "status": status, "status_detail": {"completed": ["reference freeze and denominator audit", "per-reference matching", "supplement delta recomputation"], "incomplete": failures + (["denominator review"] if unresolved_denominator or denominator == 0 else []) + (["cause adjudication"] if status == "needs_review" else [])},
        "reference_freeze": {"declared_source": reference_container.get("source", ""), "declared_version": reference_container.get("version"), "computed_sha256": freeze_hash, "raw_records": len(references), "unique_identities": len(canonical_refs), "duplicate_mappings": len(references) - len(canonical_refs), "evaluable_denominator": denominator, "unresolved_denominator_records": unresolved_denominator},
        "coverage_matrix": matrix, "primary_results": matrix, "baseline_coverage": before, "final_coverage": after,
        "search_rounds": round_audit, "supplement_increments": increments, "query_failures": failures,
        "stop_decision": {"reason": stop, "global_completeness_claimed": False},
        "residual_omissions": [x for x in matrix if x["is_residual_omission"]], "possible_duplicate_identities": possible, "identity_conflicts": identity_conflicts,
        "reconciliation": {"frozen_denominator_unchanged": denominator == before["denominator"] == after["denominator"], "new_unique_supplement_hits": sum(x["new_hit_count"] for x in increments), "observed_hit_increase": (after["numerator_hits"] or 0) - (before["numerator_hits"] or 0), "missed_reduction": (before["missed"] or 0) - (after["missed"] or 0), "note": "baseline hit/pending/missed states are frozen at baseline completion; only new supplement hits change final accounting"},
        "uncertainties": ["Coverage applies only to this frozen reference set and observed sources; it is not global recall.", "A miss or pending match is never relabeled irrelevant; unexecuted/failed searches cannot prove absence.", "No human sample audit was performed unless separately documented in actual inputs."],
        "change_records": ["Computed reference freeze before inspecting search results.", "Matched per reference and recomputed supplements without denominator drift or duplicate-hit credit.", digest({"freeze": freeze_hash, "before": before, "after": after})],
        "_aliases": {"results": "coverage_matrix", "references": "coverage_matrix", "coverage": "final_coverage", "omissions": "residual_omissions", "supplements": "supplement_increments"},
    }


if __name__ == "__main__":
    raise SystemExit(run("frozen-reference-coverage", process))
