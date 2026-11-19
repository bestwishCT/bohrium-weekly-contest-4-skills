#!/usr/bin/env python3
"""Versioned systematic-screening ledger with evidence and disagreement audit."""
from common import dedupe, digest, norm_text, records_at, run


LABELS = {"include", "exclude", "review"}


def process(case, data, task, inv):
    criteria = data.get("criteria", {}) if isinstance(data, dict) else {}
    current_version = norm_text(criteria.get("version", criteria.get("id", "unspecified")))
    codespec = criteria.get("reason_codes", {})
    valid_codes = set(codespec) if isinstance(codespec, dict) else {str(x) for x in codespec} if isinstance(codespec, list) else set()
    records = records_at(data, ["records", "citations", "candidates"])
    keys, clusters, possible, identity_conflicts = dedupe(records)
    key_to_master = {}
    for cluster in clusters:
        for key in cluster["members"]:
            key_to_master[key] = cluster["master"]
    arbitrations = data.get("arbitrations", []) if isinstance(data, dict) else []
    confirmations = data.get("researcher_confirmations", []) if isinstance(data, dict) else []
    rows, all_assessments, backlog = [], [], []
    for cluster in clusters:
        members = [dict(item) for item, key in zip(records, keys) if key in cluster["members"]]
        master = members[0]
        combined_text = norm_text(" ".join(str(m.get("title", "")) + " " + str(m.get("abstract", "")) for m in members))
        assessments = []
        for member in members:
            for assessment in member.get("assessments", []):
                enriched = dict(assessment)
                enriched["input_record_id"] = member.get("id", member.get("record_id"))
                enriched["fixture"] = member.get("fixture", assessment.get("fixture", False))
                assessments.append(enriched)
        validated, issues = [], []
        if not assessments:
            issues.append("no_independent_or_machine_assessment_supplied")
        for a_index, assessment in enumerate(assessments):
            label = assessment.get("label", assessment.get("decision", "review"))
            code = assessment.get("reason_code", "")
            role = assessment.get("role", "machine_suggested")
            version = assessment.get("criteria_version", current_version)
            evidence = assessment.get("evidence", [])
            evidence = evidence if isinstance(evidence, list) else [evidence]
            quotes = [norm_text(e.get("quote", e.get("text", "")) if isinstance(e, dict) else e) for e in evidence]
            quotes = [q for q in quotes if q]
            quote_ok = bool(quotes) and all(q in combined_text for q in quotes)
            version_ok = version == current_version
            code_ok = code in valid_codes if valid_codes else bool(code)
            label_ok = label in LABELS
            valid = quote_ok and version_ok and code_ok and label_ok
            if not version_ok:
                issues.append("criteria_version_backlog:%s" % assessment.get("criteria_version"))
            if label in {"include", "exclude"} and not quote_ok:
                issues.append("decisive_label_without_verbatim_matching_evidence")
            if not code_ok:
                issues.append("unknown_or_missing_reason_code")
            entry = {"assessment_index": a_index, "input_record_id": assessment["input_record_id"], "role": role, "role_evidence": "declared in input; identity not independently verified", "label": label if label_ok else "review", "reason_code": code, "criteria_version": version, "evidence_quotes": quotes, "evidence_locators": evidence, "fixture": assessment["fixture"], "validation": "accepted_machine_suggestion" if valid else "rejected_for_final_aggregation", "validation_issues": ([] if quote_ok else ["quote_missing_or_not_verbatim"]) + ([] if version_ok else ["version_mismatch"]) + ([] if code_ok else ["reason_code_invalid"])}
            validated.append(entry) if valid else None
            all_assessments.append(dict(entry, record_id=cluster["master"]))
        labels = {x["label"] for x in validated if x["label"] in {"include", "exclude"}}
        reasons = {x["reason_code"] for x in validated if x["label"] == "exclude"}
        conflict_type = "label" if len(labels) > 1 else "reason_code" if len(reasons) > 1 else "none"
        relevant_arbitrations = [x for x in arbitrations if x.get("record_id") in {cluster["master"], master.get("id")}]
        arbitration = relevant_arbitrations[-1] if relevant_arbitrations else None
        arbitration_valid = False
        if arbitration:
            aq = norm_text(arbitration.get("evidence_quote", ""))
            arbitration_valid = arbitration.get("label") in {"include", "exclude"} and arbitration.get("criteria_version") == current_version and bool(norm_text(arbitration.get("rationale", ""))) and bool(aq) and aq in combined_text
        if arbitration_valid:
            final = arbitration["label"]
            resolution = "explicit_input_arbitration_validated"
        elif len(validated) == 1 and not issues and conflict_type == "none":
            final = validated[0]["label"]
            resolution = "single_valid_machine_or_declared_role_suggestion"
        elif len(labels) == 1 and not issues:
            final = next(iter(labels))
            resolution = "consistent_valid_suggestions"
        else:
            final = "review"
            resolution = "unresolved_or_invalid_audit_requires_adjudication"
        confirmation = next((x for x in confirmations if x.get("record_id") in {cluster["master"], master.get("id")} and x.get("label") in LABELS), None)
        scientific_status = "researcher_confirmed_from_actual_input" if confirmation else "awaiting_actual_researcher_confirmation"
        if final == "review" or conflict_type != "none" or issues or possible:
            backlog.append(cluster["master"])
        rows.append({
            "record_id": cluster["master"], "input_record_ids": cluster["members"], "record_level_duplicate_count": len(cluster["members"]),
            "study_id": master.get("study_id"), "record_study_boundary": "study_id groups reports; duplicate mapping only groups same record and never counts a study as independent evidence",
            "metadata": {k: master.get(k) for k in ["title", "authors", "year", "doi"]}, "criteria_version": current_version,
            "assessments": validated, "original_assessment_count": len(assessments), "audit_issues": issues,
            "conflict_type": conflict_type if not arbitration_valid else conflict_type + "_addressed_by_explicit_arbitration",
            "arbitration": arbitration if arbitration else None, "arbitration_validation": arbitration_valid,
            "recommendation": final, "recommendation_basis": resolution, "scientific_confirmation_status": scientific_status,
            "researcher_confirmation": confirmation, "needs_reassessment": any(x["criteria_version"] != current_version for x in assessments),
        })
    duplicate_count = sum(len(cluster["members"]) - 1 for cluster in clusters)
    label_counts = {label: sum(row["recommendation"] == label for row in rows) for label in LABELS}
    query_logs = [{"id": q.get("id"), "source": q.get("source"), "expression": q.get("expression", q.get("query", "")), "fields": q.get("fields", []), "time_window": q.get("time_window"), "language": q.get("language"), "search_date": q.get("search_date"), "executed": q.get("executed", False), "status": q.get("status", "planned"), "response_locator": q.get("response_locator", "")} for q in records_at(data, ["queries", "search_protocol"])]
    counts = {"imported_records": len(records), "canonical_records_after_dedup": len(rows), "duplicate_mappings": duplicate_count, "screened_canonical_records": len(rows), "recommendation_counts": label_counts, "disagreement_records": sum(row["conflict_type"] != "none" for row in rows), "version_reassessment_records": sum(row["needs_reassessment"] for row in rows), "actual_arbitrations_validated": sum(row["arbitration_validation"] for row in rows), "actual_researcher_confirmations": sum(row["researcher_confirmation"] is not None for row in rows), "accounting_closed": len(records) == len(rows) + duplicate_count}
    status = "empty" if not records else "needs_human_confirmation" if rows else "partial"
    return {
        "status": status, "status_detail": {"completed": ["search protocol audit", "record/study-aware deduplication", "versioned reason-code and conflict ledger", "stage count reconciliation"], "incomplete": ["actual researcher final confirmation for unconfirmed records", "backlog reassessment/adjudication"] if backlog else ["actual researcher final confirmation for unconfirmed records"]},
        "criteria": criteria, "criteria_version": current_version, "criteria_history": data.get("criteria_history", []),
        "reason_code_dictionary": codespec, "search_protocol": query_logs,
        "screening_ledger": rows, "primary_results": rows, "preserved_assessments": all_assessments,
        "duplicate_clusters": clusters, "possible_duplicate_identities": possible, "identity_conflicts": identity_conflicts,
        "arbitration_backlog": backlog, "standard_version_backlog": [row["record_id"] for row in rows if row["needs_reassessment"]],
        "stage_counts": counts,
        "stage_audit": [{"stage": "import", "input_count": len(records), "output_count": len(records), "operation_role": "script"}, {"stage": "deduplicate", "input_count": len(records), "output_count": len(rows), "duplicate_mappings": duplicate_count, "operation_role": "script"}, {"stage": "recommend", "input_count": len(rows), "output_count": len(rows), "recommendation_counts": label_counts, "operation_role": "machine_suggestions_only"}, {"stage": "researcher_confirmation", "input_count": len(rows), "output_count": counts["actual_researcher_confirmations"], "operation_role": "only_actual_input_confirmations"}],
        "uncertainties": ["No real dual screening, arbitration, signature or agreement metric is inferred from synthetic fixtures or model roles.", "A valid machine recommendation remains pending scientific confirmation.", "Different reports of one study remain distinct reports but are not independent evidence."],
        "change_records": ["Preserved every original assessment before validation; invalid evidence was not overwritten.", "Triggered reassessment for criteria version mismatch and retained conflicts for explicit arbitration.", digest({"criteria_version": current_version, "counts": counts, "rows": rows})],
        "_aliases": {"results": "screening_ledger", "candidates": "screening_ledger", "audit": "stage_audit", "counts": "stage_counts", "conflicts": "arbitration_backlog"},
    }


if __name__ == "__main__":
    raise SystemExit(run("versioned-systematic-screening-audit", process))
