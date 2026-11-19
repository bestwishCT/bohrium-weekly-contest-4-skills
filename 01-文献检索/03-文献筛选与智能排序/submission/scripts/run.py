#!/usr/bin/env python3
"""Full-ledger candidate screening, conflict escalation and stable ranking."""
from common import dedupe, digest, norm_text, records_at, run


LABEL_RANK = {"retain": 3, "pending": 2, "exclude": 0}
DIRECT_RANK = {"direct": 3, "indirect": 2, "background": 1, "unknown": 0}


def evidence_valid(evidence, item):
    quotes = []
    for entry in evidence if isinstance(evidence, list) else [evidence]:
        quote = entry.get("quote", entry.get("text", "")) if isinstance(entry, dict) else entry
        if norm_text(quote):
            quotes.append(norm_text(quote))
    source = norm_text(str(item.get("title", "")) + " " + str(item.get("abstract", "")))
    return bool(quotes) and all(quote in source for quote in quotes), quotes


def process(case, data, task, inv):
    records = records_at(data, ["records", "candidates", "papers"])
    keys, clusters, possible, identity_conflicts = dedupe(records)
    canonical = {cluster["master"]: [] for cluster in clusters}
    for item, key in zip(records, keys):
        item = dict(item)
        item["_input_index"] = len([x for values in canonical.values() for x in values])
        item["_key"] = key
        for cluster in clusters:
            if key in cluster["members"]:
                canonical[cluster["master"]].append(item)
                break
    global_conflicts = data.get("criteria_conflicts", []) if isinstance(data, dict) else []
    criteria = data.get("criteria", {}) if isinstance(data, dict) else {}
    criteria_ids = [norm_text(x.get("id", x.get("code", ""))) for x in criteria.get("items", [])] if isinstance(criteria, dict) else []
    rows, review_rows = [], []
    for cluster in clusters:
        members = canonical[cluster["master"]]
        item = members[0]
        screening = item.get("screening", {}) if isinstance(item.get("screening", {}), dict) else {}
        judgements = item.get("judgments", item.get("judgements", []))
        labels = [x.get("label") for x in judgements if isinstance(x, dict) and x.get("label") in LABEL_RANK]
        conflict = len(set(labels)) > 1
        affected = [x for x in global_conflicts if not x.get("record_ids") or item.get("id") in x.get("record_ids", []) or cluster["master"] in x.get("record_ids", [])]
        requested = screening.get("decision", item.get("decision", labels[0] if labels else "pending"))
        if requested not in LABEL_RANK:
            requested = "pending"
        evidence = screening.get("evidence", item.get("evidence", []))
        valid, quotes = evidence_valid(evidence, item)
        reasons = screening.get("reasons", item.get("reasons", []))
        reasons = [reasons] if isinstance(reasons, str) else list(reasons)
        downgrade = []
        if requested != "pending" and not valid:
            downgrade.append("decision lacked verbatim title/abstract evidence; changed to pending")
            requested = "pending"
        if conflict or affected:
            downgrade.append("judgment or criteria conflict requires genuine adjudication")
            requested = "pending"
            conflict = True
        if not norm_text(item.get("abstract", "")) and not valid:
            downgrade.append("abstract and decisive evidence unavailable")
            requested = "pending"
        if not reasons:
            reasons = ["No validated decision supplied; pending preserves doubt."]
        reasons.extend(downgrade)
        match_map = screening.get("criteria", item.get("criteria", {}))
        if not isinstance(match_map, dict):
            match_map = {}
        matched = [cid for cid in criteria_ids if match_map.get(cid) in {"met", "yes", True}]
        missing = [cid for cid in criteria_ids if cid not in match_map]
        directness = screening.get("directness", item.get("directness", "unknown"))
        priority = 100 if conflict else 80 if downgrade else 70 if requested == "pending" else 0
        row = {
            "record_id": cluster["master"], "input_record_ids": cluster["members"], "original_ordinal": item["_input_index"] + 1,
            "metadata": {k: item.get(k) for k in ["title", "authors", "year", "venue", "doi"]},
            "decision": requested, "requested_decision": screening.get("decision", item.get("decision")),
            "evidence_quotes": quotes, "evidence_locators": evidence if isinstance(evidence, list) else [],
            "criteria_matched": matched, "criteria_unresolved": missing, "directness": directness,
            "reasons": reasons, "confidence": screening.get("confidence", "undefined"),
            "conflict": conflict, "review_priority": priority, "fixture": item.get("fixture", False),
            "version_relationships": [r for r in data.get("version_relations", []) if cluster["master"] in r.get("record_ids", []) or item.get("id") in r.get("record_ids", [])],
            "_rank": (LABEL_RANK[requested], DIRECT_RANK.get(directness, 0), len(matched), len(quotes)),
            "_tie": norm_text(item.get("title", "")).casefold(), "_ordinal": item["_input_index"],
        }
        rows.append(row)
        if priority:
            review_rows.append({"record_id": cluster["master"], "priority": priority, "reasons": downgrade or ["Information remains insufficient for a confident decision."], "human_review_performed": False})
    rows.sort(key=lambda x: tuple(-v for v in x["_rank"]) + (x["_ordinal"],))
    previous = None
    visible_rank = 0
    for position, row in enumerate(rows, 1):
        if row["_rank"] != previous:
            visible_rank, previous = position, row["_rank"]
        row["rank"] = visible_rank
        row["tie_policy"] = "equal decision/directness/criteria/evidence tuples share rank; original input ordinal only stabilizes display order"
        row.pop("_rank")
        row.pop("_tie")
        row.pop("_ordinal")
    review_rows.sort(key=lambda x: (-x["priority"], x["record_id"]))
    k = data.get("k", case.get("k")) if isinstance(data, dict) else None
    if not isinstance(k, int) or k < 0:
        k = len(rows)
    duplicates = sum(len(c["members"]) - 1 for c in clusters)
    decision_counts = {label: sum(row["decision"] == label for row in rows) for label in LABEL_RANK}
    status = "empty" if not records else "needs_review" if review_rows or possible else "completed"
    return {
        "status": status, "status_detail": {"completed": ["all-record ledger", "validated evidence decisions", "stable layered ranking"], "incomplete": ["human adjudication for review queue"] if review_rows else []},
        "criteria": criteria, "k": k, "ranked_candidates": rows, "primary_results": rows,
        "top_k": [{"rank": row["rank"], "record_id": row["record_id"], "decision": row["decision"], "reasons": row["reasons"]} for row in rows[:k]],
        "duplicate_clusters": clusters, "possible_duplicate_identities": possible, "identity_conflicts": identity_conflicts,
        "review_queue": review_rows, "criteria_conflicts": global_conflicts,
        "preserved_judgments": [{"record_id": item.get("id", key), "judgments": item.get("judgments", item.get("judgements", [])), "fixture": item.get("fixture", False)} for item, key in zip(records, keys)],
        "counts": {"input_records": len(records), "canonical_records": len(rows), "duplicate_mappings": duplicates, "decision_counts": decision_counts, "review_queue": len(review_rows), "accounting_identity": "input_records = canonical_records + duplicate_mappings", "accounting_closed": len(records) == len(rows) + duplicates},
        "uncertainties": ["Ranking is reproducible decision support, not completed human review.", "Fixture conflicts remain synthetic; no second reviewer or inter-rater agreement is inferred."],
        "change_records": ["Downgraded decisive labels lacking verbatim title/abstract evidence to pending.", "Kept every input through canonical or duplicate mapping and applied competition ranking with deterministic display ties.", digest({"rows": rows, "counts": decision_counts})],
        "_aliases": {"results": "ranked_candidates", "candidates": "ranked_candidates", "ranking": "ranked_candidates", "review": "review_queue", "screening_counts": "counts"},
    }


if __name__ == "__main__":
    raise SystemExit(run("candidate-ledger-ranking", process))
