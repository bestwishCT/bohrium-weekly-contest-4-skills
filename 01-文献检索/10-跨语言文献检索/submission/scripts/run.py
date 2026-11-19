#!/usr/bin/env python3
"""Controlled cross-language querying, quote alignment and work/view accounting."""
import hashlib
from common import norm_doi, norm_text, records_at, run


SUCCESS = {"success", "observed", "completed", "partial"}


def text_of(view):
    value = view.get("searchable_text", "")
    if isinstance(value, list):
        return norm_text(" ".join(norm_text(x.get("text", "") if isinstance(x, dict) else x) for x in value))
    return norm_text(value)


def content_hash(view):
    supplied = view.get("content_sha256")
    if supplied:
        return norm_text(supplied)
    text = text_of(view)
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""


def process(case, data, task, inv):
    views = records_at(data, ["views", "records", "papers"])
    by_id = {norm_text(v.get("id", v.get("view_id", "view%03d" % i))): v for i, v in enumerate(views)}
    hashes = {vid: content_hash(v) for vid, v in by_id.items()}
    view_rows, duplicate_views, language_warnings = [], [], []
    view_work = {}
    for vid, view in by_id.items():
        doi = norm_doi(view.get("doi", ""))
        work = norm_text(view.get("work_id", "")) or ("doi:" + doi if doi else "unresolved:" + vid)
        view_work[vid] = work
        vtype = view.get("view_type", "unknown")
        duplicate_of = norm_text(view.get("duplicate_of", ""))
        exact = False
        if duplicate_of:
            exact = duplicate_of in by_id and bool(hashes[vid]) and hashes[vid] == hashes[duplicate_of]
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
        view_rows.append({"view_id": vid, "work_id": work, "doi": view.get("doi"), "view_type": vtype, "metadata_language": view.get("metadata_language", "unknown"), "abstract_language": view.get("abstract_language", "unknown"), "declared_body_language": declared_body, "body_language": body_language, "full_text_available": body_text_available, "version": view.get("version"), "attribution": view.get("attribution"), "translation_attribution": view.get("translation_attribution"), "source_locator": view.get("source_locator", ""), "content_sha256": hashes[vid], "exact_duplicate_of": duplicate_of if exact else None, "language_evidence": body_evidence})
    work_registry = {}
    for row in view_rows:
        work_registry.setdefault(row["work_id"], {"work_id": row["work_id"], "view_ids": [], "source_locators": [], "version_relation_evidence": "explicit work_id or normalized DOI; views never erased"})
        work_registry[row["work_id"]]["view_ids"].append(row["view_id"])
        if row["source_locator"]:
            work_registry[row["work_id"]]["source_locators"].append(row["source_locator"])
    works = list(work_registry.values())
    doi_works = {}
    for row in view_rows:
        doi = norm_doi(row.get("doi", ""))
        if doi:
            doi_works.setdefault(doi, set()).add(row["work_id"])
    identity_conflicts = [{"doi": doi, "work_ids": sorted(wids), "issue": "same DOI mapped to different declared works; retained for review"} for doi, wids in doi_works.items() if len(wids) > 1]

    terms = records_at(data, ["terms", "controlled_terms", "terminology"])
    term_by_id = {}
    query_candidates, term_gaps = [], []
    for index, term in enumerate(terms):
        tid = norm_text(term.get("id", "term%03d" % (index + 1)))
        status = term.get("status", "candidate")
        entry = {"term_id": tid, "source_language": term.get("source_language"), "source_term": term.get("source_term"), "target_language": term.get("target_language"), "target_term": term.get("target_term"), "variants": term.get("variants", []), "status": status, "semantic_scope": term.get("semantic_scope", ""), "source": term.get("source", ""), "license": term.get("license", "unspecified"), "ambiguity_context": term.get("ambiguity_context", "")}
        term_by_id[tid] = entry
        if not term.get("target_term"):
            term_gaps.append({"term_id": tid, "issue": "no controlled target equivalent", "action": "retain source term and use only reviewed explanatory queries"})
            continue
        variants = [term["target_term"]] + list(term.get("variants", []))
        query_candidates.append({"term_id": tid, "target_language": term.get("target_language"), "expression": " OR ".join('"%s"' % norm_text(v).replace('"', "") for v in variants if norm_text(v)), "execution_status": "planned_only_not_executed", "semantic_status": status, "requires_review": status != "attested", "context": term.get("ambiguity_context", "")})

    alignments = records_at(data, ["alignments", "evidence_alignments"])
    alignment_by_target = {}
    alignment_conflicts = []
    for index, alignment in enumerate(alignments):
        source_view_id = norm_text(alignment.get("source_view_id", ""))
        target_view_id = norm_text(alignment.get("target_view_id", alignment.get("view_id", "")))
        source_quote = norm_text(alignment.get("source_quote", ""))
        target_quote = norm_text(alignment.get("target_quote", ""))
        source_ok = source_view_id in by_id and bool(source_quote) and source_quote in text_of(by_id[source_view_id])
        target_ok = target_view_id in by_id and bool(target_quote) and target_quote in text_of(by_id[target_view_id])
        semantic = alignment.get("semantic_status", "uncertain")
        verified = source_ok and target_ok and semantic == "aligned"
        row = {"alignment_id": alignment.get("id", "align%03d" % (index + 1)), "term_id": alignment.get("term_id"), "source_view_id": source_view_id, "target_view_id": target_view_id, "source_quote": source_quote, "target_quote": target_quote, "source_locator": alignment.get("source_locator", ""), "target_locator": alignment.get("target_locator", ""), "semantic_status": semantic if source_ok and target_ok else "uncertain", "quotes_verified": source_ok and target_ok, "verified_semantic_alignment": verified, "note": "controlled comparison plus verbatim evidence; back-translation alone is never sufficient"}
        if semantic in {"divergent", "conflicting"} or (source_ok != target_ok):
            alignment_conflicts.append(row)
        alignment_by_target.setdefault((target_view_id, alignment.get("term_id")), []).append(row)

    queries = records_at(data, ["queries", "language_queries"])
    query_rows, view_hits = [], []
    for index, query in enumerate(queries):
        qid = norm_text(query.get("id", "q%03d" % (index + 1)))
        executed = query.get("executed") is True
        state = query.get("status", "observed" if executed else "planned")
        success = executed and state in SUCCESS
        term_ids = query.get("term_ids", [])
        direction = query.get("direction", "%s->%s" % (query.get("source_language", "unknown"), query.get("target_language", "unknown")))
        query_rows.append({"query_id": qid, "direction": direction, "source_language": query.get("source_language"), "target_language": query.get("target_language"), "expression": query.get("expression", query.get("query", "")), "term_ids": term_ids, "executed": executed, "status": state, "response_locator": query.get("response_locator", ""), "source": query.get("source", ""), "translation_method": query.get("translation_method", "controlled_terms_only"), "term_trace_complete": bool(term_ids) and all(t in term_by_id for t in term_ids)})
        hits = query.get("hits", query.get("view_ids", [])) if success else []
        for vid in hits:
            vid = norm_text(vid)
            if vid not in by_id:
                view_hits.append({"query_id": qid, "view_id": vid, "status": "unknown_view_not_counted"})
                continue
            related_by_term = {term_id: alignment_by_target.get((vid, term_id), []) for term_id in term_ids or [None]}
            related = [alignment for rows in related_by_term.values() for alignment in rows]
            every_term_aligned = bool(term_ids) and all(any(a["verified_semantic_alignment"] for a in rows) for rows in related_by_term.values())
            if any(a["semantic_status"] in {"divergent", "conflicting"} for a in related):
                hit_status = "divergent_not_confirmed"
            elif every_term_aligned:
                hit_status = "confirmed_alignment" if query_rows[-1]["term_trace_complete"] else "unverified_term_trace"
            else:
                hit_status = "lexical_hit_semantics_unverified"
            view = next(x for x in view_rows if x["view_id"] == vid)
            view_hits.append({"query_id": qid, "direction": direction, "target_language": query.get("target_language"), "view_id": vid, "work_id": view["work_id"], "view_type": view["view_type"], "body_language": view["body_language"], "doi": view["doi"], "status": hit_status, "alignments": related, "source_locator": view["source_locator"], "exact_duplicate_of": view["exact_duplicate_of"], "term_ids": term_ids})
    confirmed = [hit for hit in view_hits if hit["status"] == "confirmed_alignment"]
    work_hits = {}
    for hit in confirmed:
        work_hits.setdefault(hit["work_id"], {"work_id": hit["work_id"], "supporting_view_hit_ids": [], "directions": set(), "evidence": []})
        work_hits[hit["work_id"]]["supporting_view_hit_ids"].append(hit["view_id"])
        work_hits[hit["work_id"]]["directions"].add(hit["direction"])
        work_hits[hit["work_id"]]["evidence"].extend(hit["alignments"])
    work_results = [{"work_id": w["work_id"], "supporting_view_ids": sorted(set(w["supporting_view_hit_ids"])), "directions": sorted(w["directions"]), "evidence": w["evidence"], "counting_unit": "one work regardless of original/translation/duplicate views"} for w in work_hits.values()]
    unverified = [hit for hit in view_hits if hit["status"] != "confirmed_alignment"]
    status = "empty" if not views else "partial" if term_gaps or language_warnings or identity_conflicts or unverified or not confirmed else "completed"
    unique_views = [v for v in view_rows if not v["exact_duplicate_of"]]
    return {
        "status": status, "status_detail": {"completed": ["controlled terminology", "per-language query log", "bilingual quote checks", "work/view-level accounting"], "incomplete": term_gaps + language_warnings + identity_conflicts + (["semantic alignment review"] if unverified else [])},
        "controlled_terms": list(term_by_id.values()), "term_gaps": term_gaps, "query_candidates": query_candidates,
        "language_queries": query_rows, "view_hits": view_hits, "work_hits": work_results, "primary_results": work_results,
        "views": view_rows, "work_registry": works, "duplicate_views": duplicate_views, "alignment_conflicts": alignment_conflicts, "identity_conflicts": identity_conflicts,
        "counts": {"input_views": len(views), "nonduplicate_views": len(unique_views), "distinct_registered_works": len(works), "confirmed_hit_views": len(confirmed), "confirmed_hit_works": len(work_results), "unverified_or_divergent_hits": len(unverified)},
        "uncertainties": ["Only actual query languages and supplied views are evaluated; no unseen-language or live-coverage claim.", "Abstract language does not establish body language; translation and original views remain attributable versions.", "Lexical equivalence or back-translation is not semantic proof; divergent/unverified hits do not enter confirmed work count."],
        "change_records": ["Validated duplicate content before suppressing a view in nonduplicate view counts.", "Separated bidirectional query traces and aligned actual source/target quotes before work-level deduplication."],
        "_aliases": {"results": "work_hits", "works": "work_hits", "hits": "view_hits", "queries": "language_queries", "terms": "controlled_terms", "metrics": "counts"},
    }


if __name__ == "__main__":
    raise SystemExit(run("controlled-cross-language-views", process))
