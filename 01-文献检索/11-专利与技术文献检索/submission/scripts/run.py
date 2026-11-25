#!/usr/bin/env python3
"""Authorized finite patent corpus search with typed numbers and versioned claims."""
import hashlib
import re
from collections import deque
from common import norm_text, records_at, run


PUBLIC_FIELDS = ["title", "abstract", "applicants", "inventors", "classifications", "priority_date", "filing_date", "publication_date", "grant_date", "language"]
SUCCESS = {"success", "observed", "completed", "local_snapshot"}
FINISHED_QUERY_STATUSES = {"completed_local_snapshot", "no_hit_in_available_authorized_corpus"}


def compact_number(value):
    return re.sub(r"[^A-Z0-9]", "", norm_text(value).upper())


def normalized_number(entry):
    raw = norm_text(entry.get("value", entry.get("number", "")))
    country = norm_text(entry.get("country", "")).upper()
    compact = compact_number(raw)
    detected = compact[:2] if len(compact) > 2 and compact[:2].isalpha() else ""
    country = country or detected
    body = compact[2:] if country and compact.startswith(country) else compact
    return {"number_type": entry.get("type", entry.get("number_type", "unknown")), "raw": raw, "country": country or None, "kind_code": entry.get("kind_code"), "normalized": (country + ":" if country else "") + body, "normalization_scope": "case/punctuation and explicit country only; number types are never interchanged"}


def process(case, data, task, inv):
    source_specs = records_at(data, ["sources", "source_registry"])
    source_by_name = {norm_text(s.get("name")): s for s in source_specs}
    source_logs = []
    for source in source_specs:
        level = source.get("authorization", "red")
        if level not in {"green", "yellow", "red"}:
            level = "red"
        source_logs.append({"name": source.get("name"), "authorization": level, "version": source.get("version"), "material_date": source.get("material_date"), "query_date": source.get("query_date"), "license": source.get("license"), "display_fields": source.get("display_fields", []), "retention": source.get("retention"), "confidentiality": source.get("confidentiality"), "action": {"green": "process_authorized_fields", "yellow": "display_only_declared_fields", "red": "exclude_and_do_not_export"}[level]})
    records = records_at(data, ["patents", "patent_records", "records"])
    allowed_records, excluded = {}, []
    for index, record in enumerate(records):
        rid = norm_text(record.get("id", record.get("record_id", "patent%03d" % (index + 1))))
        source_name = norm_text(record.get("source"))
        source = source_by_name.get(source_name, {"authorization": "red", "name": source_name})
        level = source.get("authorization", "red")
        if level not in {"green", "yellow"}:
            excluded.append({"record_id": rid, "source": source_name, "reason": "authorization red or undeclared"})
            continue
        allowed = set(source.get("display_fields", [])) if level == "yellow" else set(PUBLIC_FIELDS + ["numbers", "claims", "legal_history"])
        numbers = [normalized_number(x) for x in record.get("numbers", [])]
        fields = {}
        for field in PUBLIC_FIELDS:
            if field in allowed and field in record:
                fields[field] = record[field]
        claims = []
        if "claims" in allowed:
            for c_index, claim in enumerate(record.get("claims", [])):
                raw_text = claim.get("text", "")
                text = raw_text if isinstance(raw_text, str) else ""
                ocr_confidence = claim.get("ocr_confidence")
                low_ocr = claim.get("ocr", False) and (not isinstance(ocr_confidence, (int, float)) or isinstance(ocr_confidence, bool) or ocr_confidence < data.get("ocr_review_threshold", 0.8))
                claims.append({"claim_version_id": claim.get("version_id", "version%02d" % (c_index + 1)), "source": claim.get("source", source_name), "source_locator": claim.get("source_locator", ""), "page": claim.get("page"), "text": text, "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None, "independent": claim.get("independent"), "depends_on": claim.get("depends_on"), "ocr": claim.get("ocr", False), "ocr_confidence": ocr_confidence, "ocr_review_required": low_ocr, "merged_ocr": claim.get("merged_ocr"), "ocr_boundary": claim.get("ocr_boundary"), "recognition_uncertainties": claim.get("recognition_uncertainties", []), "scope_comparison": "verbatim version text hash only; equal/different protection scope is not inferred"})
        legal_history = record.get("legal_history", []) if "legal_history" in allowed else []
        source_locator_allowed = level == "green" or "source_locator" in allowed
        allowed_records[rid] = {"record_id": rid, "source": source_name, "source_locator": record.get("source_locator") if source_locator_allowed else None, "authorization": level, "source_version": source.get("version"), "material_date": source.get("material_date"), "query_date": source.get("query_date"), "numbers": numbers if "numbers" in allowed else [], "fields": fields, "claims": claims, "legal_history_as_recorded": legal_history, "current_official_legal_status": "unknown_requires_live_official_verification", "redacted_fields": sorted((set(PUBLIC_FIELDS + ["claims", "legal_history", "numbers"]) - allowed) | (set() if source_locator_allowed else {"source_locator"}))}

    legal_status_conflicts = []
    for rid, record in allowed_records.items():
        assertions_at_date = {}
        for assertion in record["legal_history_as_recorded"]:
            if not isinstance(assertion, dict):
                continue
            as_of = norm_text(assertion.get("as_of", ""))
            assertions_at_date.setdefault(as_of, []).append(assertion)
        for as_of, assertions in assertions_at_date.items():
            statuses = sorted({norm_text(item.get("status", "")) for item in assertions if norm_text(item.get("status", ""))})
            if len(statuses) > 1:
                legal_status_conflicts.append({"record_id": rid, "as_of": as_of or None, "statuses": statuses, "historical_assertions": assertions, "resolution": "unresolved_source_conflict_retained"})

    scientific = []
    scientific_exclusions = []
    scientific_inputs = records_at(data, ["scientific_papers", "technical_literature"])
    scientific_source_name = norm_text(data.get("scientific_source", ""))
    scientific_source = source_by_name.get(scientific_source_name, {})
    scientific_level = scientific_source.get("authorization", "red")
    scientific_allowed = bool(scientific_source_name) and scientific_level in {"green", "yellow"}
    scientific_fields = {"title", "authors", "year", "venue", "doi", "methods"}
    allowed_scientific_fields = scientific_fields if scientific_level == "green" else set(scientific_source.get("display_fields", []))
    for index, paper in enumerate(scientific_inputs):
        paper_id = paper.get("id", "paper%03d" % (index + 1))
        if not scientific_allowed:
            scientific_exclusions.append({"paper_id": paper_id, "source": scientific_source_name or None, "reason": "scientific source undeclared, unregistered or authorization red"})
            continue
        entry = {"paper_id": paper_id, "source": scientific_source_name, "authorization": scientific_level, "source_locator": paper.get("source_locator") if scientific_level == "green" or "source_locator" in allowed_scientific_fields else None, "role": "technical comparison only; never patent-family or legal-status evidence", "redacted_fields": sorted(scientific_fields - allowed_scientific_fields)}
        for field in sorted(scientific_fields):
            entry[field] = paper.get(field) if field in allowed_scientific_fields else None
        scientific.append(entry)
    raw_links = records_at(data, ["family_links", "observed_family_links"])
    links, adjacency, excluded_family_links = [], {}, []
    for link in raw_links:
        left, right = link.get("from"), link.get("to")
        source_name = norm_text(link.get("source"))
        source = source_by_name.get(source_name, {})
        level = source.get("authorization", "red")
        fields_authorized = level == "green" or (level == "yellow" and "family_links" in source.get("display_fields", []))
        if left not in allowed_records or right not in allowed_records or not fields_authorized or not link.get("source_locator"):
            excluded_family_links.append({"from": left, "to": right, "source": source_name or None, "reason": "endpoints, source_locator, explicit authorization or family_links display scope insufficient"})
            continue
        edge = {"from": left, "to": right, "relation": link.get("relation", "observed_family_link"), "family_scope": link.get("family_scope", "unspecified"), "source": link.get("source"), "source_locator": link.get("source_locator"), "coverage": "observed_link_only_not_exhaustive"}
        links.append(edge)
        adjacency.setdefault(left, set()).add(right)
        adjacency.setdefault(right, set()).add(left)

    queries = records_at(data, ["queries", "search_requests"])
    query_results, families, claim_review = [], [], []
    for q_index, query in enumerate(queries):
        qid = norm_text(query.get("id", "query%03d" % (q_index + 1)))
        qtype = query.get("query_type", "number" if query.get("raw_number") else "topic")
        if qtype not in {"number", "topic"}:
            query_results.append({"query_id": qid, "query_type": qtype, "status": "unsupported_query_type", "matches": [], "scientific_matches": [], "effect": "only explicit number or topic queries are processed; no topic fallback for unknown types"})
            continue
        source_name = norm_text(query.get("source", ""))
        source = source_by_name.get(source_name, {})
        if not source_name or source_name not in source_by_name or source.get("authorization", "red") not in {"green", "yellow"}:
            query_results.append({"query_id": qid, "query_type": qtype, "status": "blocked_authorization", "matches": [], "scientific_matches": [], "effect": "query source must be declared, registered and authorized; no substitute results"})
            continue
        query_status = norm_text(query.get("status", "")) or "not_executed"
        if query.get("executed") is False or query_status not in SUCCESS:
            query_results.append({"query_id": qid, "query_type": qtype, "status": "not_executed" if query.get("executed") is False else query_status, "matches": [], "scientific_matches": [], "effect": "timeout, rate-limit, unavailable and other unsuccessful states are not zero hits or completed searches"})
            continue
        scoped_records = {rid: record for rid, record in allowed_records.items() if record["source"] == source_name}
        matches = []
        science_matches = []
        if qtype == "number":
            number_type = query.get("number_type")
            target = compact_number(query.get("raw_number", ""))
            if not number_type or not target:
                query_results.append({"query_id": qid, "query_type": qtype, "status": "ambiguous_number_type", "matches": [], "scientific_matches": [], "effect": "publication/application/grant/priority numbers are not guessed"})
                continue
            for rid, record in scoped_records.items():
                for number in record["numbers"]:
                    if number["number_type"] == number_type and compact_number(number["raw"]) == target:
                        matches.append({"record_id": rid, "source": source_name, "source_locator": record["source_locator"], "matched_number": number, "selection_basis": "exact match of the declared number type after case/punctuation normalization", "evidence_strength": "direct_typed_number_match"})
            result_scope = "typed exact match in the query's registered authorized source; no similar-number padding"
        else:
            terms = [norm_text(x).casefold() for x in query.get("terms", []) if norm_text(x)]
            classifications = {norm_text(x).upper() for x in query.get("classifications", []) if norm_text(x)}
            applicant = norm_text(query.get("applicant", "")).casefold()
            if not terms and not classifications and not applicant:
                query_results.append({"query_id": qid, "query_type": qtype, "status": "empty_query_conditions", "matches": [], "scientific_matches": [], "effect": "no topic, classification or applicant condition; empty conditions never match the whole corpus"})
                continue
            for rid, record in scoped_records.items():
                corpus = norm_text(" ".join(str(v) for v in record["fields"].values()) + " " + " ".join(c["text"] for c in record["claims"])).casefold()
                actual_classes = {norm_text(x).upper() for x in record["fields"].get("classifications", [])} if isinstance(record["fields"].get("classifications", []), list) else set()
                applicants = record["fields"].get("applicants", [])
                applicants = applicants if isinstance(applicants, list) else [applicants]
                actual_applicants = [norm_text(x).casefold() for x in applicants]
                if all(term in corpus for term in terms) and (not classifications or classifications & actual_classes) and (not applicant or any(applicant in value for value in actual_applicants)):
                    matched_fields = sorted(field for field, value in record["fields"].items() if any(term in norm_text(str(value)).casefold() for term in terms))
                    if any(term in norm_text(" ".join(c["text"] for c in record["claims"])).casefold() for term in terms):
                        matched_fields.append("claims")
                    if classifications & actual_classes:
                        matched_fields.append("classifications")
                    if applicant and any(applicant in value for value in actual_applicants):
                        matched_fields.append("applicants")
                    matches.append({"record_id": rid, "source": source_name, "source_locator": record["source_locator"], "matched_terms": terms, "matched_classifications": sorted(classifications & actual_classes), "matched_fields": sorted(set(matched_fields)), "selection_basis": "all requested terms occur in authorized searchable fields; requested classification and applicant filters also match", "evidence_strength": "declared_local_topic_field_match"})
            if terms:
                for paper in scientific:
                    if paper["source"] != source_name:
                        continue
                    corpus = norm_text(str(paper.get("title", "")) + " " + str(paper.get("methods", ""))).casefold()
                    if all(term in corpus for term in terms):
                        science_matches.append(paper["paper_id"])
            result_scope = "supplied topic/classification/applicant conditions in the query's registered authorized source; scientific papers counted separately"
        query_results.append({"query_id": qid, "query_type": qtype, "status": "completed_local_snapshot" if matches or science_matches else "no_hit_in_available_authorized_corpus", "number_type": query.get("number_type"), "raw_number": query.get("raw_number"), "terms": query.get("terms", []), "classifications": query.get("classifications", []), "applicant": query.get("applicant"), "source": source_name, "source_version": source.get("version"), "material_date": query.get("material_date", source.get("material_date")), "query_date": query.get("query_date", source.get("query_date")), "matches": matches, "scientific_matches": science_matches, "scope": result_scope})
        for match in matches:
            rid = match["record_id"]
            if rid not in {f["seed_record_id"] for f in families}:
                seen, queue = {rid}, deque([rid])
                while queue:
                    for neighbor in sorted(adjacency.get(queue.popleft(), set())):
                        if neighbor not in seen:
                            seen.add(neighbor)
                            queue.append(neighbor)
                families.append({"seed_record_id": rid, "observed_member_ids": sorted(seen), "evidence_links": [e for e in links if e["from"] in seen and e["to"] in seen], "family_scope": "observed_linked_component_only", "exhaustive": False, "equivalent_claim_scope_claimed": False})
    direct_match_ids = {m["record_id"] for q in query_results for m in q["matches"]}
    matched_ids = sorted(direct_match_ids | {rid for family in families for rid in family["observed_member_ids"]})
    for rid in matched_ids:
        for claim in allowed_records[rid]["claims"]:
            issues = []
            if claim["ocr_review_required"]:
                issues.append("low or missing OCR confidence; original image/page review required")
            if claim["merged_ocr"] or claim["ocr_boundary"]:
                issues.append("merged/OCR source boundary retained; do not infer another version or equivalent scope")
            if issues:
                claim_review.append({"record_id": rid, "claim_version_id": claim["claim_version_id"], "source": claim["source"], "source_locator": claim["source_locator"], "page": claim["page"], "issues": issues, "ocr_boundary": claim["ocr_boundary"], "recognition_uncertainties": claim["recognition_uncertainties"]})
    incomplete_queries = [q for q in query_results if q["status"] not in FINISHED_QUERY_STATUSES]
    # This finite corpus has no live official legal-status evidence, so even a
    # completed local query is only an honest partial answer to the full task.
    status = "empty" if not queries else "partial"
    return {
        "status": status, "status_detail": {"completed": ["authorization matrix"] + ([] if incomplete_queries else ["typed number/topic search"]) + ["observed family expansion and claim-version audit"], "incomplete": ["current official legal status remains unverified; aggregate result is partial without live official evidence", "red/yellow restricted scopes"] + (["queries incomplete: " + ", ".join(q["query_id"] + "=" + q["status"] for q in incomplete_queries)] if incomplete_queries else []) + (["undeclared or unauthorized scientific literature excluded"] if scientific_exclusions else []) + (["unauthorized or under-scoped family links excluded"] if excluded_family_links else []) + (["unresolved legal-history conflicts: " + ", ".join(sorted({c["record_id"] for c in legal_status_conflicts}))] if legal_status_conflicts else []) + (["OCR/manual facsimile review"] if claim_review else [])},
        "authorization_matrix": source_logs, "excluded_restricted_records": excluded, "excluded_scientific_records": scientific_exclusions, "excluded_family_links": excluded_family_links,
        "query_results": query_results, "matched_patents": [dict(allowed_records[rid], retrieval_basis="direct_query_match" if rid in direct_match_ids else "observed_family_expansion", direct_query_ids=sorted(q["query_id"] for q in query_results if any(m["record_id"] == rid for m in q["matches"]))) for rid in matched_ids],
        "primary_results": query_results, "observed_families": families,
        "scientific_literature": [p for p in scientific if any(p["paper_id"] in q["scientific_matches"] for q in query_results)],
        "claim_review_queue": claim_review,
        "legal_status": [{"record_id": rid, "current": "unknown_requires_live_official_verification", "historical_assertions": allowed_records[rid]["legal_history_as_recorded"], "conflicts": [c for c in legal_status_conflicts if c["record_id"] == rid], "warning": "old facsimile/web/fee inference is not current official status"} for rid in matched_ids],
        "legal_status_conflicts": legal_status_conflicts,
        "risk_register": [{"area": "legal_status", "level": "conflict" if legal_status_conflicts else "unknown", "reason": "no real-time official verification in this finite task; conflicting historical assertions remain unresolved"}, {"area": "family", "level": "limited", "reason": "observed links are non-exhaustive and do not imply equal claim scope"}, {"area": "ocr", "level": "review_required" if claim_review else "source_bound", "reason": "facsimile recognition and merged-source boundaries remain located and uncertain"}],
        "metrics": {"declared_sources": len(source_specs), "authorized_patent_records": len(allowed_records), "excluded_restricted_records": len(excluded), "excluded_scientific_records": len(scientific_exclusions), "excluded_family_links": len(excluded_family_links), "queries": len(queries), "directly_matched_patents": len(direct_match_ids), "matched_patents": len(matched_ids), "matched_patents_including_observed_family": len(matched_ids), "observed_family_links": len(links), "legal_status_conflicts": len(legal_status_conflicts), "scientific_comparison_records": sum(len(q["scientific_matches"]) for q in query_results)},
        "uncertainties": ["This is source-located information retrieval, not infringement/FTO/validity/value advice or attorney review.", "Current official legal status is unavailable; local completed queries therefore remain partial for the full task.", "Observed families are not global exhaustive families; claim versions are never flattened into one scope.", "No paywall/login/anti-bot restriction was bypassed and no restricted source text is exported."],
        "change_records": ["Applied explicit source authorization and display-field scopes before using patent, scientific and family-link records; undeclared sources default to exclusion.", "Separated typed number and topic searches, retained per-match selection evidence, expanded only observed family members, and preserved verbatim claim-version hashes and unresolved legal-history conflicts."],
        "_aliases": {"results": "query_results", "patents": "matched_patents", "families": "observed_families", "papers": "scientific_literature", "risks": "risk_register", "metrics_summary": "metrics"},
    }


if __name__ == "__main__":
    raise SystemExit(run("authorized-patent-technical-search", process))
