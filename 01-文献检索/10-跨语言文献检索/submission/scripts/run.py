#!/usr/bin/env python3
"""Controlled cross-language querying, quote alignment and work/view accounting."""
import hashlib
import re
from common import norm_doi, norm_text, records_at, run


SUCCESS = {"success", "observed", "completed", "partial"}
SNAPSHOT_SUCCESS = {"success", "observed", "completed"}
MISSING = object()


def query_execution(query):
    explicit = query.get("executed") is True
    state = query.get("status", "observed" if explicit else "planned")
    locator = norm_text(query.get("response_locator", ""))
    snapshot = "executed" not in query and state in SNAPSHOT_SUCCESS and bool(locator)
    executed = explicit or snapshot
    if "executed" in query:
        basis = "explicit_executed_true" if explicit else "explicit_executed_not_true"
    else:
        basis = "completed_snapshot_status_and_response_locator" if snapshot else "snapshot_not_verified"
    return executed, state, executed and state in SUCCESS, basis


def query_term_trace(query):
    plural = query.get("term_ids", MISSING)
    single = query.get("term_id", MISSING)
    plural_ids = [norm_text(value) for value in plural] if isinstance(plural, list) else []
    single_ids = [norm_text(single)] if single is not MISSING and not isinstance(single, (list, dict)) and norm_text(single) else []
    conflict = False
    if plural is not MISSING and single is not MISSING:
        conflict = not isinstance(plural, list) or plural_ids != single_ids
    ids = plural_ids if plural is not MISSING else single_ids
    if conflict:
        ids = sorted(set(plural_ids + single_ids))
    invalid_type = plural is not MISSING and not isinstance(plural, list)
    return ids, conflict, invalid_type


def text_of(view):
    value = view.get("searchable_text", "")
    if isinstance(value, list):
        return norm_text(" ".join(norm_text(x.get("text", "") if isinstance(x, dict) else x) for x in value))
    return norm_text(value)


def content_hash(view):
    supplied = view.get("content_sha256", "")
    if supplied:
        return norm_text(supplied)
    text = text_of(view)
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""


def expression_contains(expression, phrase):
    expression, phrase = norm_text(expression).casefold(), norm_text(phrase).casefold()
    if not expression or not phrase:
        return False
    return re.search(r"(?<!\w)%s(?!\w)" % re.escape(phrase), expression, flags=re.UNICODE) is not None


def identifiers(records, kind, prefix):
    ids = [norm_text(row.get("id", "%s%03d" % (prefix, index + 1))) for index, row in enumerate(records)]
    counts = {value: ids.count(value) for value in set(ids)}
    conflicts = [{"kind": kind, "id": value, "occurrences": count, "issue": "duplicate identifier; evidence retained for review"} for value, count in sorted(counts.items()) if count > 1]
    return ids, {value for value, count in counts.items() if count > 1}, conflicts


def process(case, data, task, inv):
    views = records_at(data, ["views", "records", "papers"])
    view_ids, ambiguous_views, id_conflicts = identifiers(views, "view", "view")
    by_id = {vid: view for vid, view in zip(view_ids, views)}
    hashes = {vid: content_hash(view) for vid, view in by_id.items()}
    view_rows, duplicate_views, language_warnings = [], [], []
    view_work = {}
    for vid, view in zip(view_ids, views):
        doi = norm_doi(view.get("doi", ""))
        work = norm_text(view.get("work_id", "")) or ("doi:" + doi if doi else "unresolved:" + vid)
        view_work[vid] = work
        vtype = view.get("view_type", "unknown")
        duplicate_of = norm_text(view.get("duplicate_of", ""))
        exact = False
        if duplicate_of:
            if duplicate_of == vid:
                id_conflicts.append({"kind": "view_duplicate", "id": vid, "issue": "self-duplication is not a valid version relation"})
            exact = duplicate_of != vid and duplicate_of in by_id and duplicate_of not in ambiguous_views and vid not in ambiguous_views and bool(hashes.get(vid)) and hashes.get(vid) == hashes.get(duplicate_of)
            duplicate_views.append({"view_id": vid, "duplicate_of": duplicate_of, "confirmed_exact_content": exact, "source_locator": view.get("source_locator", ""), "action": "exclude from unique nonduplicate view count only; preserve this view and source"})
        body_text_available = vtype in {"original", "publisher_full_translation", "bilingual_full_text"} and bool(text_of(view))
        declared_body = view.get("body_language")
        body_evidence = view.get("body_language_evidence", "")
        if vtype == "abstract_only" and not body_text_available and not body_evidence:
            body_language = "unknown"
            if declared_body:
                language_warnings.append({"view_id": vid, "issue": "body language declaration lacks body evidence; abstract language not substituted"})
        else:
            body_language = declared_body or "unknown"
        if vtype == "publisher_full_translation" and not view.get("translation_attribution"):
            language_warnings.append({"view_id": vid, "issue": "publisher translation attribution missing"})
        view_rows.append({"view_id": vid, "work_id": work, "doi": view.get("doi"), "view_type": vtype, "metadata_language": view.get("metadata_language", "unknown"), "abstract_language": view.get("abstract_language", "unknown"), "declared_body_language": declared_body, "body_language": body_language, "full_text_available": body_text_available, "version": view.get("version"), "attribution": view.get("attribution"), "translation_attribution": view.get("translation_attribution"), "source_locator": view.get("source_locator", ""), "content_sha256": hashes.get(vid, ""), "exact_duplicate_of": duplicate_of if exact else None, "language_evidence": body_evidence})
    view_row_by_id = {row["view_id"]: row for row in view_rows}
    parsed_relations, conflict_pairs = [], set()
    same_work_types = {"same_work", "same_version", "version", "version_of", "version_relation", "preprint_of", "published_version_of"}
    conflict_types = {"different_work", "distinct_work", "not_same_work", "conflict", "conflicting", "unrelated"}
    for index, raw in enumerate(records_at(data, ["version_relations"])):
        rid = norm_text(raw.get("id", raw.get("relation_id", "v%04d" % (index + 1))))
        left_raw = next((raw.get(k) for k in ["from", "source", "source_id", "source_view_id", "left", "view_id", "version", "version_id"] if raw.get(k) not in (None, "")), "")
        right_raw = next((raw.get(k) for k in ["to", "target", "target_id", "target_view_id", "right", "related_view_id", "version_of", "related_version"] if raw.get(k) not in (None, "")), "")
        left, right = norm_text(left_raw), norm_text(right_raw)
        relation_type = norm_text(raw.get("relation", raw.get("relation_type", raw.get("type", raw.get("state", ""))))).casefold().replace("-", "_").replace(" ", "_")
        locator = norm_text(raw.get("source_locator", raw.get("response_locator", "")))
        conflict = relation_type in conflict_types or raw.get("status") in {"conflicting", "rejected"}
        if left in by_id and right in by_id and left != right and conflict:
            conflict_pairs.add(tuple(sorted((left, right))))
        parsed_relations.append({"relation_id": rid, "left": left, "right": right, "relation_type": relation_type, "source_locator": locator, "raw": raw, "declared_conflict": conflict})

    version_parents = {vid: vid for vid in view_ids}

    def version_root(vid):
        while version_parents[vid] != vid:
            version_parents[vid] = version_parents[version_parents[vid]]
            vid = version_parents[vid]
        return vid

    def version_conflict(left_root, right_root):
        return any(tuple(sorted((a, b))) in conflict_pairs for a in view_ids for b in view_ids if version_root(a) == left_root and version_root(b) == right_root)

    version_relation_rows, invalid_version_relations = [], []
    for relation in parsed_relations:
        left, right, raw = relation["left"], relation["right"], relation["raw"]
        problem = None
        if left not in by_id or right not in by_id or left == right or left in ambiguous_views or right in ambiguous_views:
            problem = "unknown, ambiguous, or self view relation"
        elif not relation["source_locator"]:
            problem = "missing version-relation source locator"
        elif relation["declared_conflict"]:
            problem = "conflicting version relation retained without alignment"
        elif relation["relation_type"] not in same_work_types:
            problem = "unsupported same-work/version relation type"
        elif tuple(sorted((left, right))) in conflict_pairs:
            problem = "same-work relation contradicted by a conflicting relation"
        elif version_conflict(version_root(left), version_root(right)):
            problem = "alignment would join a declared work conflict"
        row = dict(raw)
        row.update({"relation_id": relation["relation_id"], "verified": not problem, "from_view_id": left, "to_view_id": right, "relation_type": relation["relation_type"], "source_locator": relation["source_locator"], "validation_error": problem})
        version_relation_rows.append(row)
        if problem:
            invalid_version_relations.append(row)
            continue
        version_parents[version_root(right)] = version_root(left)
        relation["verified"] = True

    aligned_work = {}
    for vid, row in zip(view_ids, view_rows):
        members = [other for other in view_ids if version_root(other) == version_root(vid)]
        aligned_work[vid] = sorted({view_work[other] for other in members})[0]
        row["aligned_work_id"] = aligned_work[vid]
        view_row_by_id[vid]["aligned_work_id"] = aligned_work[vid]

    def version_connection(source_view_id, target_view_id):
        if source_view_id not in version_parents or target_view_id not in version_parents:
            return []
        if version_root(source_view_id) != version_root(target_view_id):
            return []
        return sorted(r["relation_id"] for r in parsed_relations if r.get("verified") and version_root(r["left"]) == version_root(source_view_id))

    work_registry = {}
    for row in view_rows:
        wid = row["aligned_work_id"]
        work_registry.setdefault(wid, {"work_id": wid, "view_ids": [], "declared_work_ids": [], "source_locators": [], "version_relation_ids": [], "version_relation_evidence": "explicit work_id, normalized DOI, or source-located verified version relation; every view and DOI remains separately attributable"})
        work_registry[wid]["view_ids"].append(row["view_id"])
        if row["work_id"] not in work_registry[wid]["declared_work_ids"]:
            work_registry[wid]["declared_work_ids"].append(row["work_id"])
        for rid in version_connection(row["view_id"], row["view_id"]):
            if rid not in work_registry[wid]["version_relation_ids"]:
                work_registry[wid]["version_relation_ids"].append(rid)
        if row["source_locator"]:
            work_registry[wid]["source_locators"].append(row["source_locator"])
    works = list(work_registry.values())
    doi_works = {}
    for row in view_rows:
        doi = norm_doi(row.get("doi", ""))
        if doi:
            doi_works.setdefault(doi, set()).add(row["work_id"])
    identity_conflicts = [{"doi": doi, "work_ids": sorted(wids), "issue": "same DOI mapped to different declared works; retained for review"} for doi, wids in doi_works.items() if len(wids) > 1]
    identity_conflicts.extend({"kind": "version_relation", "relation_id": row["relation_id"], "issue": row["validation_error"]} for row in invalid_version_relations)

    terms = records_at(data, ["terms", "controlled_terms", "terminology"])
    term_ids, ambiguous_terms, term_id_conflicts = identifiers(terms, "term", "term")
    id_conflicts.extend(term_id_conflicts)
    term_by_id, term_entries, query_candidates, term_gaps = {}, {}, [], []
    for tid, term in zip(term_ids, terms):
        status = term.get("status", "candidate")
        entry = {"term_id": tid, "source_language": term.get("source_language"), "source_term": term.get("source_term"), "target_language": term.get("target_language"), "target_term": term.get("target_term"), "variants": term.get("variants", []), "status": status, "semantic_scope": term.get("semantic_scope", ""), "source": term.get("source", ""), "license": term.get("license", "unspecified"), "ambiguity_context": term.get("ambiguity_context", "")}
        term_entries[tid] = entry
        term_by_id[tid] = term
        if not term.get("target_term"):
            term_gaps.append({"term_id": tid, "issue": "no controlled target equivalent", "action": "retain source term and use only reviewed explanatory queries"})
            continue
        variants = [term["target_term"]] + list(term.get("variants", []))
        query_candidates.append({"term_id": tid, "direction": "%s->%s" % (term.get("source_language", "unknown"), term.get("target_language", "unknown")), "source_language": term.get("source_language"), "target_language": term.get("target_language"), "expression": " OR ".join('"%s"' % norm_text(v).replace('"', "") for v in variants if norm_text(v)), "execution_status": "planned_only_not_executed", "semantic_status": status, "requires_review": status != "attested", "context": term.get("ambiguity_context", "")})

    alignments = records_at(data, ["alignments", "evidence_alignments"])
    alignment_ids, ambiguous_alignments, alignment_id_conflicts = identifiers(alignments, "alignment", "align")
    id_conflicts.extend(alignment_id_conflicts)
    alignment_by_target, alignment_conflicts = {}, []
    verified_term_ids = set()
    for index, (aid, alignment) in enumerate(zip(alignment_ids, alignments)):
        source_view_id = norm_text(alignment.get("source_view_id", ""))
        target_view_id = norm_text(alignment.get("target_view_id", alignment.get("view_id", "")))
        tid = norm_text(alignment.get("term_id", ""))
        source_quote = norm_text(alignment.get("source_quote", ""))
        target_quote = norm_text(alignment.get("target_quote", ""))
        source_locator = norm_text(alignment.get("source_locator", ""))
        target_locator = norm_text(alignment.get("target_locator", ""))
        source_ok = source_view_id in by_id and source_view_id not in ambiguous_views and bool(source_quote) and source_quote in text_of(by_id[source_view_id])
        target_ok = target_view_id in by_id and target_view_id not in ambiguous_views and bool(target_quote) and target_quote in text_of(by_id[target_view_id])
        term = term_by_id.get(tid, {})
        self_mapping = bool(source_view_id) and source_view_id == target_view_id
        declared_same_work = source_view_id in view_work and target_view_id in view_work and view_work[source_view_id] == view_work[target_view_id]
        relation_ids = version_connection(source_view_id, target_view_id)
        same_work = declared_same_work or bool(relation_ids)
        locators_ok = bool(source_locator and target_locator)
        language_ok = bool(term) and source_view_id in view_row_by_id and target_view_id in view_row_by_id and view_row_by_id[source_view_id]["body_language"] == term.get("source_language") and view_row_by_id[target_view_id]["body_language"] == term.get("target_language")
        term_quote_ok = bool(term) and expression_contains(source_quote, term.get("source_term", "")) and expression_contains(target_quote, term.get("target_term", ""))
        identity_ok = aid not in ambiguous_alignments and tid not in ambiguous_terms and bool(term)
        semantic = alignment.get("semantic_status", "uncertain")
        verified = all([source_ok, target_ok, same_work, not self_mapping, locators_ok, language_ok, term_quote_ok, identity_ok]) and semantic == "aligned"
        issues = []
        if self_mapping:
            issues.append("self_mapping")
        if source_view_id in view_work and target_view_id in view_work and not same_work:
            issues.append("cross_work_alignment")
        if not locators_ok:
            issues.append("missing_source_or_target_locator")
        if not source_ok or not target_ok:
            issues.append("quote_or_view_not_verified")
        if term and not language_ok:
            issues.append("direction_language_mismatch")
        if term and not term_quote_ok:
            issues.append("term_quote_mismatch")
        if not identity_ok:
            issues.append("unknown_or_duplicate_identity")
        row = {"alignment_id": aid, "term_id": alignment.get("term_id"), "source_view_id": source_view_id, "target_view_id": target_view_id, "source_quote": source_quote, "target_quote": target_quote, "source_locator": alignment.get("source_locator", ""), "target_locator": alignment.get("target_locator", ""), "semantic_status": semantic if verified else ("uncertain" if semantic == "aligned" else semantic), "quotes_verified": source_ok and target_ok, "same_work_verified": same_work and not self_mapping, "same_work_basis": "declared_work_id_or_doi" if declared_same_work else "verified_version_relation" if relation_ids else "not_verified", "version_relation_ids": relation_ids, "locators_verified": locators_ok, "verified_semantic_alignment": verified, "evidence_issues": issues, "note": "controlled comparison plus verbatim evidence; back-translation alone is never sufficient"}
        if issues or semantic in {"divergent", "conflicting"}:
            alignment_conflicts.append(row)
        if verified:
            verified_term_ids.add(tid)
        alignment_by_target.setdefault((target_view_id, alignment.get("term_id")), []).append(row)

    queries = records_at(data, ["queries", "language_queries"])
    query_ids, ambiguous_queries, query_id_conflicts = identifiers(queries, "query", "q")
    id_conflicts.extend(query_id_conflicts)

    def query_matches_term(query, term_id, require_expression=True):
        term = term_by_id.get(term_id)
        if not term or term_id in ambiguous_terms:
            return False
        expected_direction = "%s->%s" % (term.get("source_language"), term.get("target_language"))
        declared_direction = norm_text(query.get("direction", ""))
        direction_ok = not declared_direction or declared_direction == expected_direction
        source = query.get("source_language")
        target = query.get("target_language")
        languages_ok = source in (None, "", term.get("source_language")) and target in (None, "", term.get("target_language"))
        expression = query.get("expression", query.get("query", ""))
        variants = [term.get("target_term", "")] + list(term.get("variants", []))
        expression_ok = not require_expression or any(expression_contains(expression, variant) for variant in variants)
        return direction_ok and languages_ok and expression_ok

    query_rows, view_hits, query_evidence_issues = [], [], []
    for qid, query in zip(query_ids, queries):
        executed, state, success, execution_basis = query_execution(query)
        query_term_ids, term_field_conflict, invalid_term_type = query_term_trace(query)
        expected_directions = {"%s->%s" % (term_by_id[tid].get("source_language"), term_by_id[tid].get("target_language")) for tid in query_term_ids if tid in term_by_id}
        direction = query.get("direction") or (next(iter(expected_directions)) if len(expected_directions) == 1 else "%s->%s" % (query.get("source_language", "unknown"), query.get("target_language", "unknown")))
        response_locator = norm_text(query.get("response_locator", ""))
        trace_complete = bool(query_term_ids) and not term_field_conflict and not invalid_term_type and qid not in ambiguous_queries and all(t in term_by_id and t not in ambiguous_terms for t in query_term_ids)
        evidence_valid = success and bool(response_locator) and trace_complete and all(query_matches_term(query, tid) for tid in query_term_ids)
        issues = []
        if success and not response_locator:
            issues.append("missing_response_locator")
        if term_field_conflict:
            issues.append("term_id_field_conflict")
        if invalid_term_type and not term_field_conflict:
            issues.append("invalid_term_ids_type")
        if qid in ambiguous_queries:
            issues.append("duplicate_query_id")
        if success and not trace_complete and not term_field_conflict and not invalid_term_type:
            issues.append("unknown_empty_or_duplicate_term_trace")
        if success and trace_complete and not all(query_matches_term(query, tid) for tid in query_term_ids):
            issues.append("expression_variant_direction_or_language_mismatch")
        if issues:
            query_evidence_issues.append({"query_id": qid, "issues": issues})
        query_rows.append({"query_id": qid, "direction": direction, "source_language": query.get("source_language"), "target_language": query.get("target_language"), "expression": query.get("expression", query.get("query", "")), "term_ids": query_term_ids, "executed": executed, "execution_basis": execution_basis, "status": state, "response_locator": query.get("response_locator", ""), "source": query.get("source", ""), "translation_method": query.get("translation_method", "controlled_terms_only"), "term_trace_complete": trace_complete, "evidence_valid": evidence_valid, "evidence_issues": issues})
        raw_hits = query.get("hits", query.get("view_ids", [])) if success else []
        if not isinstance(raw_hits, list):
            raw_hits = []
        for raw_vid in raw_hits:
            vid = norm_text(raw_vid)
            if vid not in by_id or vid in ambiguous_views:
                view_hits.append({"query_id": qid, "view_id": vid, "status": "unknown_or_ambiguous_view_not_counted"})
                continue
            related_by_term = {term_id: alignment_by_target.get((vid, term_id), []) for term_id in query_term_ids or [None]}
            related = [alignment for rows in related_by_term.values() for alignment in rows]
            every_term_aligned = bool(query_term_ids) and all(any(a["verified_semantic_alignment"] for a in rows) for rows in related_by_term.values())
            if any(a["semantic_status"] in {"divergent", "conflicting"} for a in related):
                hit_status = "divergent_not_confirmed"
            elif not evidence_valid:
                hit_status = "query_evidence_unverified"
            elif every_term_aligned:
                hit_status = "confirmed_alignment"
            else:
                hit_status = "lexical_hit_semantics_unverified"
            view = view_row_by_id[vid]
            view_hits.append({"query_id": qid, "direction": direction, "target_language": query.get("target_language"), "view_id": vid, "work_id": view["work_id"], "aligned_work_id": view["aligned_work_id"], "view_type": view["view_type"], "body_language": view["body_language"], "doi": view["doi"], "status": hit_status, "alignments": related, "source_locator": view["source_locator"], "exact_duplicate_of": view["exact_duplicate_of"], "term_ids": query_term_ids})

    coverage_matrix, coverage_missing = [], []
    for tid, term in zip(term_ids, terms):
        if tid in ambiguous_terms or not term.get("target_term"):
            continue
        expected_direction = "%s->%s" % (term.get("source_language"), term.get("target_language"))
        variants = []
        for variant in [term.get("target_term")] + list(term.get("variants", [])):
            variant = norm_text(variant)
            if variant and variant not in variants:
                variants.append(variant)
        for variant in variants:
            matched, review_queries = [], []
            for qid, query in zip(query_ids, queries):
                traced_ids, term_field_conflict, invalid_term_type = query_term_trace(query)
                if term_field_conflict or invalid_term_type or tid not in traced_ids:
                    continue
                if not expression_contains(query.get("expression", query.get("query", "")), variant):
                    continue
                executed, state, success, execution_basis = query_execution(query)
                if query_matches_term(query, tid) and success and norm_text(query.get("response_locator", "")) and qid not in ambiguous_queries:
                    matched.append((qid, query))
                else:
                    review_queries.append(qid)
            hit_ids = {norm_text(vid) for qid, query in matched for vid in (query.get("hits", query.get("view_ids", [])) if isinstance(query.get("hits", query.get("view_ids", [])), list) else []) if norm_text(vid) in by_id and norm_text(vid) not in ambiguous_views}
            unique_hit_ids = sorted(hit_ids)
            state = "missing" if not matched else "hit" if hit_ids else "no_hit"
            row = {"direction": expected_direction, "source_language": term.get("source_language"), "target_language": term.get("target_language"), "term_id": tid, "variant": variant, "state": state, "executed": bool(matched), "query_ids": sorted({qid for qid, query in matched}), "hit_view_ids": sorted(hit_ids), "unique_hit_view_ids": unique_hit_ids, "no_hit_query_ids": sorted(qid for qid, query in matched if not query.get("hits", query.get("view_ids", []))), "review_query_ids": sorted(set(review_queries))}
            coverage_matrix.append(row)
            if not matched:
                coverage_missing.append({"direction": expected_direction, "term_id": tid, "variant": variant, "issue": "required direction/concept/variant query not executed with valid evidence"})

    candidate_gaps = [{"term_id": tid, "issue": "candidate term lacks verified same-work bilingual evidence"} for tid, term in zip(term_ids, terms) if term.get("status", "candidate") != "attested" and tid not in verified_term_ids]
    confirmed = [hit for hit in view_hits if hit["status"] == "confirmed_alignment"]
    confirmed_view_ids = {hit["view_id"] for hit in confirmed}
    work_hits = {}
    for hit in confirmed:
        wid = hit["aligned_work_id"]
        work_hits.setdefault(wid, {"work_id": wid, "supporting_view_hit_ids": [], "directions": set(), "evidence": []})
        work_hits[wid]["supporting_view_hit_ids"].append(hit["view_id"])
        work_hits[wid]["directions"].add(hit["direction"])
        work_hits[wid]["evidence"].extend(hit["alignments"])
    work_results = [{"work_id": w["work_id"], "supporting_view_ids": sorted(set(w["supporting_view_hit_ids"])), "directions": sorted(w["directions"]), "evidence": w["evidence"], "counting_unit": "one work regardless of original/translation/duplicate views"} for w in work_hits.values()]
    unverified_groups = {}
    for hit in view_hits:
        if hit["status"] == "confirmed_alignment":
            continue
        key = (hit["query_id"], hit["view_id"])
        unverified_groups.setdefault(key, {"query_id": hit["query_id"], "view_id": hit["view_id"], "statuses": [], "occurrences": 0, "raw_hits": []})
        unverified_groups[key]["occurrences"] += 1
        if hit["status"] not in unverified_groups[key]["statuses"]:
            unverified_groups[key]["statuses"].append(hit["status"])
        unverified_groups[key]["raw_hits"].append(hit)
    unverified = []
    for group in unverified_groups.values():
        group["statuses"] = sorted(group["statuses"])
        group["conflicting_statuses"] = len(group["statuses"]) > 1
        unverified.append(group)
    blocking = term_gaps + language_warnings + identity_conflicts + id_conflicts + query_evidence_issues + coverage_missing + candidate_gaps
    status = "empty" if not views else "partial" if blocking or unverified else "completed"
    unique_views = [v for v in view_rows if not v["exact_duplicate_of"]]
    return {
        "status": status, "status_detail": {"completed": ["controlled terminology", "per-language query log", "bilingual quote checks", "direction/concept/variant coverage", "work/view-level accounting"], "incomplete": blocking + (["semantic alignment review"] if unverified else [])},
        "controlled_terms": list(term_entries.values()), "term_gaps": term_gaps, "query_candidates": query_candidates,
        "language_queries": query_rows, "coverage_matrix": coverage_matrix, "coverage_missing": coverage_missing, "candidate_gaps": candidate_gaps, "query_evidence_issues": query_evidence_issues,
        "view_hits": view_hits, "unverified_hit_groups": unverified, "work_hits": work_results, "primary_results": work_results,
        "views": view_rows, "work_registry": works, "version_relations": version_relation_rows, "invalid_version_relations": invalid_version_relations, "duplicate_views": duplicate_views, "alignment_conflicts": alignment_conflicts, "identity_conflicts": identity_conflicts, "id_conflicts": id_conflicts,
        "counts": {"input_views": len(views), "nonduplicate_views": len(unique_views), "distinct_registered_works": len(works), "confirmed_hit_views": len(confirmed_view_ids), "confirmed_hit_works": len(work_results), "unverified_or_divergent_hits": len(unverified), "coverage_cells": len(coverage_matrix), "coverage_executed": sum(row["executed"] for row in coverage_matrix), "coverage_hit": sum(row["state"] == "hit" for row in coverage_matrix), "coverage_no_hit": sum(row["state"] == "no_hit" for row in coverage_matrix), "coverage_missing": len(coverage_missing)},
        "uncertainties": ["Only actual query languages and supplied views are evaluated; no unseen-language or live-coverage claim.", "Abstract language does not establish body language; translation and original views remain attributable versions.", "Lexical equivalence or back-translation is not semantic proof; divergent/unverified hits do not enter confirmed work count."],
        "change_records": ["Validated duplicate content before suppressing a view in nonduplicate view counts.", "Separated bidirectional query traces and aligned actual source/target quotes before work-level deduplication.", "Counted confirmed views once per view ID and audited every direction/concept/variant cell."],
        "_aliases": {"results": "work_hits", "works": "work_hits", "hits": "view_hits", "queries": "language_queries", "terms": "controlled_terms", "metrics": "counts"},
    }


if __name__ == "__main__":
    raise SystemExit(run("controlled-cross-language-views", process))
