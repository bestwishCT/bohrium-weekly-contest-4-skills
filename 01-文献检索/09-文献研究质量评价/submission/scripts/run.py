#!/usr/bin/env python3
"""Scale-driven quality appraisal with verbatim evidence gates and abstention."""
from common import norm_text, records_at, run


DIMENSIONS = ["research_design", "evidence_sufficiency", "bias_risk", "reporting_completeness"]
BIAS = {"explicitly_present", "possibly_present", "no_indication", "insufficient_evidence"}


def quote_match(evidence, sections, unreadable):
    quote = norm_text(evidence.get("quote", evidence.get("text", "")) if isinstance(evidence, dict) else evidence)
    if not quote:
        return False, {"reason": "empty evidence quote"}
    requested_section = evidence.get("section", "") if isinstance(evidence, dict) else ""
    requested_page = evidence.get("page") if isinstance(evidence, dict) else None
    locator = evidence.get("locator", "") if isinstance(evidence, dict) else ""
    if locator and locator in unreadable:
        return False, {"reason": "declared unreadable figure/table locator", "locator": locator}
    candidates = []
    for section in sections:
        if requested_section and section.get("section") != requested_section:
            continue
        if requested_page is not None and section.get("page") != requested_page:
            continue
        if quote in norm_text(section.get("text", "")):
            candidates.append(section)
    return bool(candidates), {"quote": quote, "matched_locations": [{k: s.get(k) for k in ["section", "page", "locator"]} for s in candidates], "reason": "verbatim_match" if candidates else "quote_not_found_at_declared_location"}


def rule_matches(rule, ratings, valid):
    if rule.get("default") is True:
        return True
    critical = [rating for rating, is_critical, ok in ratings if is_critical and ok]
    all_valid = [rating for rating, _, ok in ratings if ok]
    if "critical_ratings_include" in rule:
        return any(r in rule["critical_ratings_include"] for r in critical)
    if "any_ratings_include" in rule:
        return any(r in rule["any_ratings_include"] for r in all_valid)
    if "all_valid_rated" in rule:
        return len(all_valid) == len(ratings) and bool(all_valid) and all(r == rule["all_valid_rated"] for r in all_valid)
    if "max_invalid_items" in rule:
        return len(ratings) - len(all_valid) <= rule["max_invalid_items"]
    return False


def aggregate(aggregation, ratings):
    rules = aggregation.get("rules", []) if isinstance(aggregation, dict) else []
    for rule in rules:
        if rule_matches(rule, ratings, None):
            return {"grade": rule.get("grade", "indeterminate"), "rule": rule, "basis": "first matching declared scale rule"}
    return {"grade": "indeterminate", "rule": None, "basis": "no declared aggregation rule matched; no threshold invented"}


def process(case, data, task, inv):
    scale = data.get("scale", {}) if isinstance(data, dict) else {}
    items = scale.get("items", []) if isinstance(scale, dict) else []
    item_by_id = {norm_text(item.get("id")): item for item in items}
    declared_dimensions = {item.get("dimension") for item in items}
    missing_dimensions = [d for d in DIMENSIONS if d not in declared_dimensions]
    studies = records_at(data, ["studies", "papers", "records"])
    evaluations, global_review = [], []
    for study in studies:
        design = study.get("study_design", "unknown")
        sections = study.get("sections", [])
        unreadable = set(study.get("unreadable_locators", []))
        access = study.get("access", "unknown")
        supplied = study.get("appraisal", study.get("assessments", []))
        supplied_by_id = {norm_text(a.get("item_id")): a for a in supplied}
        rows, review = [], []
        design_known = not scale.get("study_designs") or design in scale.get("study_designs", [])
        if not design_known:
            review.append({"type": "scale_design_mismatch", "study_design": design, "effect": "do not force this scale onto another design"})
        for item in items:
            iid = norm_text(item.get("id"))
            appraisal = supplied_by_id.get(iid, {})
            rating = appraisal.get("rating", "not_assessable")
            if rating in {"not_reported", "insufficient_evidence"}:
                rating = "not_assessable"
            allowed = item.get("allowed_ratings", ["low_risk", "some_concerns", "high_risk", "not_assessable"])
            applicable = design_known and (not item.get("applicable_designs") or design in item["applicable_designs"])
            evidences = appraisal.get("evidence", [])
            evidences = evidences if isinstance(evidences, list) else [evidences]
            checks = [quote_match(e, sections, unreadable) for e in evidences]
            evidence_ok = bool(checks) and all(ok for ok, detail in checks)
            valid = applicable and evidence_ok and rating in allowed
            if not valid:
                rating = "not_assessable"
            bias = appraisal.get("bias_judgment", "insufficient_evidence")
            if bias not in BIAS:
                bias = "insufficient_evidence"
            row = {"item_id": iid, "dimension": item.get("dimension", "unmapped"), "checkpoint": item.get("question", ""), "critical": item.get("critical", False), "requested_rating": appraisal.get("rating"), "rating": rating, "applicable": applicable, "evidence_valid": evidence_ok, "evidence": [detail for ok, detail in checks], "bias_judgment": bias, "rationale": appraisal.get("rationale", ""), "status": "evaluated" if valid else "abstained_missing_or_invalid_evidence"}
            rows.append(row)
            if not valid:
                review.append({"item_id": iid, "reason": row["status"], "human_review_performed": False})
        dimension_results = []
        for dimension in DIMENSIONS:
            selected = [row for row in rows if row["dimension"] == dimension]
            ratings = [(row["rating"], row["critical"], row["evidence_valid"] and row["rating"] != "not_assessable") for row in selected]
            aggregation = scale.get("dimension_aggregation", {}).get(dimension, {})
            grade = aggregate(aggregation, ratings) if selected else {"grade": "not_applicable_or_missing", "basis": "no scale item for required dimension"}
            dimension_results.append({"dimension": dimension, "item_ids": [r["item_id"] for r in selected], "coverage_state": "complete_scale_mapping" if selected else "missing_scale_mapping", "grade": grade["grade"], "aggregation_basis": grade["basis"], "judgments": [{"item_id": r["item_id"], "rating": r["rating"], "evidence": r["evidence"]} for r in selected]})
        overall_ratings = [(row["rating"], row["critical"], row["evidence_valid"] and row["rating"] != "not_assessable") for row in rows]
        overall = aggregate(scale.get("aggregation", {}), overall_ratings)
        if missing_dimensions or any(row["critical"] and row["status"] != "evaluated" for row in rows):
            overall = {"grade": "indeterminate", "basis": "required dimension or critical evidence missing; aggregation cannot override abstention"}
        evaluation = {"study_id": study.get("id", study.get("record_id", "")), "study_design": design, "scale_id": scale.get("id"), "scale_version": scale.get("version"), "scale_applicable": design_known, "material_access": access, "full_text_claimed": access == "full_text", "quality_items": rows, "dimensions": dimension_results, "overall_grade": overall["grade"], "overall_basis": overall["basis"], "manual_review_queue": review, "statistics_boundary": "only supplied verbatim source values are evidence; no statistic is inferred or recomputed as an original result"}
        evaluations.append(evaluation)
        global_review.extend({"study_id": evaluation["study_id"], **entry} for entry in review)
    status = "empty" if not studies else "partial" if missing_dimensions or global_review or any(e["overall_grade"] == "indeterminate" for e in evaluations) else "completed"
    return {
        "status": status, "status_detail": {"completed": ["scale/design applicability audit", "verbatim evidence gate", "declared-rule aggregation and abstention"], "incomplete": missing_dimensions + (["evidence/manual review"] if global_review else [])},
        "scale": {"id": scale.get("id"), "version": scale.get("version"), "declared_study_designs": scale.get("study_designs", []), "item_count": len(items), "dimensions_declared": sorted(str(x) for x in declared_dimensions), "required_dimensions_missing": missing_dimensions, "aggregation": scale.get("aggregation", {}), "dimension_aggregation": scale.get("dimension_aggregation", {})},
        "quality_evaluations": evaluations, "primary_results": evaluations, "manual_review_queue": global_review,
        "coverage_counts": {"studies": len(studies), "scale_items": len(items), "evaluated_items": sum(row["status"] == "evaluated" for study in evaluations for row in study["quality_items"]), "abstained_items": sum(row["status"] != "evaluated" for study in evaluations for row in study["quality_items"]), "required_dimensions": DIMENSIONS},
        "uncertainties": ["Unreported is not unperformed; no indication of bias is not absence of bias; both can require abstention.", "Abstract-only and unreadable tables cannot support full-text claims.", "Outputs are appraisal support, not peer review, clinical/policy advice or fabricated reviewer agreement."],
        "change_records": ["Replaced invalid or unverifiable ratings with not_assessable while retaining requested ratings and evidence checks.", "Aggregated only with scale-declared rules and forced indeterminate when critical evidence or required dimensions were missing."],
        "_aliases": {"results": "quality_evaluations", "evaluations": "quality_evaluations", "appraisal": "quality_evaluations", "review_queue": "manual_review_queue", "counts": "coverage_counts"},
    }


if __name__ == "__main__":
    raise SystemExit(run("evidence-gated-quality-appraisal", process))
