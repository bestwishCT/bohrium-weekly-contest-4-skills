#!/usr/bin/env python3
"""Versioned systematic-screening ledger with evidence and disagreement audit."""
from common import dedupe, digest, norm_text, records_at, run


LABELS = {"include", "exclude", "review"}


def own_record_text(record, locator=""):
    """Return text only from this record, restricted to its declared locator when known."""
    locator_key = norm_text(locator).casefold()
    if "abstract" in locator_key:
        return norm_text(record.get("abstract", ""))
    if "title" in locator_key:
        return norm_text(record.get("title", ""))
    for key, value in record.items():
        if locator_key and locator_key == norm_text(key).casefold():
            return norm_text(value)
    if any(token in locator_key for token in ("page", "full", "section", "locator")):
        return norm_text(" ".join(norm_text(record.get(key, "")) for key in ("page", "pages", "full_text", "text", "content", "sections", "locators")))
    return norm_text(str(record.get("title", "")) + " " + str(record.get("abstract", "")))


def declared_query_ids(record):
    values = []
    for key in ("query_id", "query_ids", "retrieved_by"):
        value = record.get(key)
        if isinstance(value, list):
            values.extend(value)
        elif value is not None:
            values.append(value)
    evidence = record.get("retrieval_evidence", [])
    evidence = evidence if isinstance(evidence, list) else [evidence]
    for item in evidence:
        if isinstance(item, dict) and item.get("query_id") is not None:
            values.append(item["query_id"])
    return sorted({norm_text(value) for value in values if norm_text(value)})


def query_hit_ids(query):
    hits = query.get("hits", query.get("record_ids", []))
    hits = hits if isinstance(hits, list) else [hits]
    answer = []
    for hit in hits:
        if isinstance(hit, dict):
            hit = hit.get("record_id", hit.get("id"))
        if norm_text(hit):
            answer.append(norm_text(hit))
    return answer


def process(case, data, task, inv):
    domain_errors = []
    criteria = data.get("criteria", {}) if isinstance(data, dict) else {}
    if not isinstance(criteria, dict):
        criteria = {}
    current_version = norm_text(criteria.get("version", criteria.get("id", "")))
    if not current_version:
        domain_errors.append({"type": "invalid_criteria", "message": "criteria.version is required"})
    codespec = criteria.get("reason_codes", {})
    valid_codes = set(codespec) if isinstance(codespec, dict) else {str(x) for x in codespec} if isinstance(codespec, list) else set()

    raw_records = records_at(data, ["records", "citations", "candidates"])
    records = []
    for index, record in enumerate(raw_records):
        if isinstance(record, dict):
            records.append(dict(record))
        else:
            domain_errors.append({"type": "invalid_record", "record_index": index, "message": "record must be an object"})
    keys, clusters, possible, identity_conflicts = dedupe(records)
    for index, record in enumerate(records):
        record["_key"] = keys[index]

    raw_queries = records_at(data, ["queries", "search_protocol"])
    query_logs, observed_queries = [], {}
    for index, query in enumerate(raw_queries):
        if not isinstance(query, dict):
            domain_errors.append({"type": "invalid_query", "query_index": index, "message": "query must be an object"})
            continue
        query_id = norm_text(query.get("id", query.get("query_id", "")))
        log = {"id": query_id or None, "query_id": query_id or None, "source": query.get("source"), "expression": query.get("expression", query.get("query", "")), "fields": query.get("fields", []), "time_window": query.get("time_window"), "language": query.get("language"), "search_date": query.get("search_date"), "executed": query.get("executed", False), "status": query.get("status", "planned"), "response_locator": query.get("response_locator", "")}
        query_logs.append(log)
        observed = bool(query_id) and query.get("executed") is True and query.get("status") == "observed" and bool(norm_text(query.get("response_locator", "")))
        if observed:
            observed_queries[query_id] = log

    query_hits = {}
    for query in raw_queries:
        if not isinstance(query, dict):
            continue
        query_id = norm_text(query.get("id", query.get("query_id", "")))
        if query_id in observed_queries:
            for record_id in query_hit_ids(query):
                query_hits.setdefault(record_id, set()).add(query_id)

    arbitrations = data.get("arbitrations", []) if isinstance(data, dict) else []
    arbitrations = arbitrations if isinstance(arbitrations, list) else []
    confirmations = data.get("researcher_confirmations", []) if isinstance(data, dict) else []
    confirmations = confirmations if isinstance(confirmations, list) else []
    rows, all_assessments, backlog = [], [], []
    for cluster in clusters:
        members = [record for record in records if record["_key"] in cluster["members"]]
        master = members[0]
        assessments = []
        for member in members:
            member_assessments = member.get("assessments", [])
            member_assessments = member_assessments if isinstance(member_assessments, list) else [member_assessments]
            for assessment in member_assessments:
                enriched = dict(assessment) if isinstance(assessment, dict) else {}
                enriched["_source_record"] = member
                enriched["_valid_assessment_shape"] = isinstance(assessment, dict)
                enriched["input_record_id"] = member.get("id", member.get("record_id"))
                enriched["fixture"] = member.get("fixture", enriched.get("fixture", False))
                assessments.append(enriched)
        validated, issues = [], []
        if not assessments:
            issues.append("no_independent_or_machine_assessment_supplied")
        for a_index, assessment in enumerate(assessments):
            source = assessment["_source_record"]
            label = assessment.get("label", assessment.get("decision", "review"))
            code = assessment.get("reason_code", "")
            role = assessment.get("role", "machine_suggested")
            version = assessment.get("criteria_version")
            version_ok = bool(version) and version == current_version
            evidence = assessment.get("evidence", [])
            evidence = evidence if isinstance(evidence, list) else [evidence]
            quotes = [norm_text(e.get("quote", e.get("text", "")) if isinstance(e, dict) else e) for e in evidence]
            quotes = [quote for quote in quotes if quote]
            locators_ok = bool(evidence) and all(isinstance(e, dict) and bool(norm_text(e.get("locator", ""))) for e in evidence)
            quote_ok = bool(quotes) and len(quotes) == len(evidence) and locators_ok and all(quote in own_record_text(source, evidence[index].get("locator", "")) for index, quote in enumerate(quotes))
            code_ok = isinstance(code, str) and (code in valid_codes if valid_codes else bool(code))
            label_ok = isinstance(label, str) and label in LABELS
            shape_ok = assessment["_valid_assessment_shape"]
            valid = shape_ok and quote_ok and version_ok and code_ok and label_ok
            validation_issues = []
            if not shape_ok:
                validation_issues.append("assessment_not_object")
                domain_errors.append({"type": "invalid_assessment", "record_id": cluster["master"], "assessment_index": a_index, "message": "assessment must be an object"})
            if not quote_ok:
                validation_issues.append("quote_or_locator_missing_or_not_verbatim_in_own_record")
            if not version_ok:
                validation_issues.append("version_missing" if not version else "version_mismatch")
            if not code_ok:
                validation_issues.append("reason_code_invalid")
            if not label_ok:
                validation_issues.append("label_invalid")
            if not version:
                issues.append("criteria_version_missing")
                domain_errors.append({"type": "invalid_assessment", "record_id": cluster["master"], "assessment_index": a_index, "message": "criteria_version is required"})
            elif not version_ok:
                issues.append("criteria_version_backlog:%s" % version)
            if label in {"include", "exclude"} and not quote_ok:
                issues.append("decisive_label_without_verbatim_matching_evidence_in_own_record")
            if not code_ok:
                issues.append("unknown_or_missing_reason_code")
            entry = {"assessment_index": a_index, "input_record_id": assessment["input_record_id"], "source_record_key": source["_key"], "role": role, "role_evidence": "declared in input; identity not independently verified", "label": label if label_ok else "review", "reason_code": code, "criteria_version": version, "evidence_quotes": quotes, "evidence_locators": evidence, "fixture": assessment["fixture"], "validation": "accepted_machine_suggestion" if valid else "rejected_for_final_aggregation", "validation_issues": validation_issues}
            if valid:
                validated.append(entry)
            all_assessments.append(dict(entry, record_id=cluster["master"]))
        labels = {entry["label"] for entry in validated}
        reasons = {entry["reason_code"] for entry in validated if entry["label"] in {"include", "exclude"}}
        conflict_type = "label" if len(labels) > 1 else "reason_code" if len(reasons) > 1 else "none"
        relevant_arbitrations = [item for item in arbitrations if isinstance(item, dict) and item.get("record_id") in {cluster["master"], master.get("id")}]
        arbitration = relevant_arbitrations[-1] if relevant_arbitrations else None
        arbitration_valid = False
        if arbitration:
            quote = norm_text(arbitration.get("evidence_quote", ""))
            locator = arbitration.get("evidence_locator", arbitration.get("locator", ""))
            arbitration_valid = arbitration.get("label") in ("include", "exclude") and arbitration.get("criteria_version") == current_version and bool(norm_text(arbitration.get("rationale", ""))) and bool(quote) and bool(norm_text(locator)) and quote in own_record_text(master, locator)

        retrieval_evidence = []
        unknown_query_ids = set()
        for member in members:
            own_query_ids = set(declared_query_ids(member))
            own_query_ids.update(query_hits.get(norm_text(member.get("id", member.get("record_id", ""))), set()))
            for query_id in sorted(own_query_ids):
                if query_id not in observed_queries:
                    unknown_query_ids.add(query_id)
                    continue
                retrieval_evidence.append({"query_id": query_id, "record_id": member["_key"], "input_record_id": member.get("id", member.get("record_id")), "criteria_version": current_version, "response_locator": observed_queries[query_id]["response_locator"], "evidence_status": "observed_query_hit_declared"})
        if unknown_query_ids:
            issues.append("query_hit_references_unobserved_query")
            domain_errors.append({"type": "invalid_retrieval_evidence", "record_id": cluster["master"], "query_ids": sorted(unknown_query_ids), "message": "record references missing or unobserved queries"})
        if not retrieval_evidence:
            issues.append("no_verified_query_record_link")
            domain_errors.append({"type": "invalid_retrieval_evidence", "record_id": cluster["master"], "message": "no observed query-hit evidence for this record"})
        if not retrieval_evidence:
            final = "review"
            resolution = "unresolved_or_invalid_audit_requires_adjudication"
        elif arbitration_valid:
            final = arbitration["label"]
            resolution = "explicit_input_arbitration_validated"
        elif conflict_type != "none" or issues:
            final = "review"
            resolution = "unresolved_or_invalid_audit_requires_adjudication"
        elif len(validated) == 1:
            final = validated[0]["label"]
            resolution = "single_valid_machine_or_declared_role_suggestion"
        elif len(labels) == 1 and len(reasons) <= 1:
            final = next(iter(labels))
            resolution = "consistent_valid_suggestions"
        else:
            final = "review"
            resolution = "unresolved_or_invalid_audit_requires_adjudication"
        confirmation = next((item for item in confirmations if isinstance(item, dict) and item.get("record_id") in {cluster["master"], master.get("id")} and item.get("label") in ("include", "exclude", "review")), None)
        scientific_status = "researcher_confirmed_from_actual_input" if confirmation else "awaiting_actual_researcher_confirmation"
        if final == "review" or conflict_type != "none" or issues or possible:
            backlog.append(cluster["master"])
        rows.append({
            "record_id": cluster["master"], "input_record_ids": cluster["members"], "record_level_duplicate_count": len(cluster["members"]),
            "study_id": master.get("study_id"), "record_study_boundary": "study_id groups reports; duplicate mapping only groups same record and never counts a study as independent evidence",
            "metadata": {key: master.get(key) for key in ["title", "authors", "year", "doi"]}, "criteria_version": current_version,
            "retrieval_evidence": retrieval_evidence,
            "assessments": validated, "original_assessment_count": len(assessments), "audit_issues": issues,
            "conflict_type": conflict_type if not arbitration_valid else conflict_type + "_addressed_by_explicit_arbitration",
            "arbitration": arbitration if arbitration else None, "arbitration_validation": arbitration_valid,
            "recommendation": final, "recommendation_basis": resolution, "scientific_confirmation_status": scientific_status,
            "researcher_confirmation": confirmation, "needs_reassessment": any(item.get("criteria_version") != current_version for item in assessments),
        })

    study_groups = {}
    for row in rows:
        study_id = norm_text(row.get("study_id")) or None
        group_key = "study:" + study_id if study_id else "record:" + row["record_id"]
        study_groups.setdefault(group_key, {"study_id": study_id, "study_id_status": "declared" if study_id else "not_supplied_record_counted_separately", "record_ids": []})["record_ids"].append(row["record_id"])
    by_record = {row["record_id"]: row for row in rows}
    study_ledger = []
    for group in study_groups.values():
        decisions = {by_record[record_id]["recommendation"] for record_id in group["record_ids"]}
        recommendation = next(iter(decisions)) if len(decisions) == 1 else "review"
        study_ledger.append(dict(group, report_count=len(group["record_ids"]), recommendation=recommendation, recommendation_basis="record_recommendations_grouped_not_independent_evidence"))
    duplicate_count = sum(len(cluster["members"]) - 1 for cluster in clusters)
    label_counts = {label: sum(row["recommendation"] == label for row in rows) for label in LABELS}
    counts = {"imported_records": len(raw_records), "invalid_records": len(raw_records) - len(records), "canonical_records_after_dedup": len(rows), "duplicate_mappings": duplicate_count, "unique_studies": len(study_ledger), "declared_studies": sum(group["study_id"] is not None for group in study_ledger), "multi_report_studies": sum(group["report_count"] > 1 and group["study_id"] is not None for group in study_ledger), "screened_canonical_records": len(rows), "recommendation_counts": label_counts, "disagreement_records": sum(row["conflict_type"] != "none" for row in rows), "version_reassessment_records": sum(row["needs_reassessment"] for row in rows), "actual_arbitrations_validated": sum(row["arbitration_validation"] for row in rows), "actual_researcher_confirmations": sum(row["researcher_confirmation"] is not None for row in rows), "accounting_closed": len(raw_records) == len(rows) + duplicate_count + len(raw_records) - len(records)}
    status = "partial" if domain_errors else "empty" if not raw_records else "needs_human_confirmation"
    return {
        "status": status, "status_detail": {"completed": ["search protocol and query-hit audit", "record/study-aware deduplication", "versioned reason-code and conflict ledger", "stage count reconciliation"], "incomplete": ["invalid input or retrieval evidence"] if domain_errors else ["actual researcher final confirmation for unconfirmed records", "backlog reassessment/adjudication"] if backlog else ["actual researcher final confirmation for unconfirmed records"]},
        "criteria": criteria, "criteria_version": current_version, "criteria_history": data.get("criteria_history", []) if isinstance(data, dict) else [],
        "reason_code_dictionary": codespec, "search_protocol": query_logs, "study_ledger": study_ledger,
        "screening_ledger": rows, "primary_results": rows, "preserved_assessments": all_assessments,
        "duplicate_clusters": clusters, "possible_duplicate_identities": possible, "identity_conflicts": identity_conflicts,
        "arbitration_backlog": backlog, "standard_version_backlog": [row["record_id"] for row in rows if row["needs_reassessment"]],
        "stage_counts": counts, "errors": domain_errors,
        "stage_audit": [{"stage": "import", "input_count": len(raw_records), "output_count": len(records), "invalid_count": len(raw_records) - len(records), "operation_role": "script"}, {"stage": "deduplicate", "input_count": len(records), "output_count": len(rows), "duplicate_mappings": duplicate_count, "operation_role": "script"}, {"stage": "recommend", "input_count": len(rows), "output_count": len(rows), "recommendation_counts": label_counts, "study_count": len(study_ledger), "operation_role": "machine_suggestions_only"}, {"stage": "researcher_confirmation", "input_count": len(rows), "output_count": counts["actual_researcher_confirmations"], "operation_role": "only_actual_input_confirmations"}],
        "uncertainties": ["No real dual screening, arbitration, signature or agreement metric is inferred from synthetic fixtures or model roles.", "A valid machine recommendation remains pending scientific confirmation.", "Different reports of one study remain distinct reports but are not independent evidence."],
        "change_records": ["Preserved every original assessment before validation; invalid evidence was not overwritten.", "Validated quotes against each assessment's own record and locator; linked observed queries to records and grouped reports by declared study_id.", digest({"criteria_version": current_version, "counts": counts, "rows": rows})],
        "_aliases": {"results": "screening_ledger", "candidates": "screening_ledger", "audit": "stage_audit", "counts": "stage_counts", "conflicts": "arbitration_backlog", "studies": "study_ledger"},
    }


if __name__ == "__main__":
    raise SystemExit(run("versioned-systematic-screening-audit", process))
