#!/usr/bin/env python3
"""Deduplicate records and recompute bounded time/topic trends without inventing growth."""
import argparse
from collections import Counter
import sys
sys.dont_write_bytecode = True
from runtime import cli, unique_ids


def refs(owner, values, available, label, errors, minimum=1):
    if not isinstance(values, list) or len(values) < minimum:
        errors.append("%s needs %s references" % (owner, label))
        return
    for value in values:
        if value not in available:
            errors.append("%s references unknown %s %s" % (owner, label, value))


def validate(data, sources, tools, evidence, base):
    errors, warnings = [], []
    d = data.get("data", {})
    scope = d.get("scope", {})
    window = scope.get("window")
    if (not isinstance(window, list) or len(window) != 2 or
            not all(isinstance(y, int) and not isinstance(y, bool) for y in window) or window[0] > window[1]):
        errors.append("scope.window must contain start/end integer years")
        window = [0, -1]
    for field in ("time_basis", "source_scope", "inclusion_rule", "denominator_rule", "stop_condition"):
        if not scope.get(field):
            errors.append("trend scope lacks " + field)
    records = unique_ids(d.get("records", []), "record", errors)
    vocabulary = unique_ids(d.get("vocabulary", []), "vocabulary", errors)
    variant_map = {}
    for term, rule in vocabulary.items():
        if not rule.get("mapping_reason") or not rule.get("disambiguation"):
            errors.append(term + " needs mapping rationale and same-name/drift disambiguation")
        refs(term, rule.get("evidence_ids"), evidence, "evidence", errors)
        for variant in rule.get("variants", []) + [term]:
            if variant in variant_map and variant_map[variant] != term:
                errors.append("ambiguous variant mapped twice: " + variant)
            variant_map[variant] = term
    rows, years_with_data = [], set()
    independent, duplicates, missing = {}, [], []
    topic_by_record = {}
    for rid, record in records.items():
        parent = record.get("duplicate_of")
        if parent:
            if parent not in records or parent == rid or records[parent].get("duplicate_of"):
                errors.append(rid + " has an invalid direct duplicate/version map")
            duplicates.append(rid)
            continue
        if not isinstance(record.get("included"), bool) or not record.get("selection_reason"):
            errors.append(rid + " lacks inclusion state/reason")
        if not record.get("included"):
            continue
        independent[rid] = record
        dates = record.get("dates", {})
        if any(not isinstance(value, (int, type(None))) or isinstance(value, bool) for value in dates.values()):
            errors.append(rid + " has non-integer/non-missing date")
        year = dates.get(scope.get("time_basis"))
        if year is None:
            missing.append(rid)
        elif window[0] <= year <= window[1]:
            years_with_data.add(year)
        raw_topics = record.get("topics_raw", [])
        unmapped = [topic for topic in raw_topics if topic not in variant_map]
        if unmapped:
            errors.append(rid + " has unreviewed ambiguous topics: " + ",".join(unmapped))
        topic_by_record[rid] = sorted({variant_map[t] for t in raw_topics if t in variant_map})
    stats_keys = set()
    for year in range(window[0], window[1] + 1):
        yearly_ids = [rid for rid, record in independent.items() if record.get("dates", {}).get(scope.get("time_basis")) == year]
        counts = Counter(topic for rid in yearly_ids for topic in topic_by_record.get(rid, []))
        topic_rows = []
        for topic, count in sorted(counts.items()):
            topic_rows.append({"topic": topic, "document_count": count, "denominator": len(yearly_ids),
                               "share": round(count / len(yearly_ids), 6) if yearly_ids else None})
            stats_keys.add("topic:%s@%d" % (topic, year))
        stats_keys.add("year:%d" % year)
        rows.append({"year": year, "document_count": len(yearly_ids), "record_ids": sorted(yearly_ids), "topics": topic_rows})
    growth = None
    growth_limit = "At least three non-empty years and six independent documents are required for a descriptive period delta."
    observed_years = sorted(r["year"] for r in rows if r["document_count"])
    if len(observed_years) >= 3 and len(independent) >= 6:
        first, last = observed_years[0], observed_years[-1]
        first_n = next(r["document_count"] for r in rows if r["year"] == first)
        last_n = next(r["document_count"] for r in rows if r["year"] == last)
        growth = {"from_year": first, "to_year": last, "from_count": first_n, "to_count": last_n,
                  "absolute_delta": last_n - first_n, "kind": "descriptive_period_delta_not_significance"}
    trend_claims = unique_ids(d.get("trend_claims", []), "trend_claim", errors)
    for cid, claim in trend_claims.items():
        refs(cid, claim.get("statistic_refs"), stats_keys, "statistic", errors)
        refs(cid, claim.get("evidence_ids"), evidence, "evidence", errors)
        if claim.get("kind") not in {"descriptive_count", "bounded_interpretation"}:
            errors.append(cid + " must distinguish counts from bounded interpretation")
        if growth is None and claim.get("requires_growth"):
            errors.append(cid + " claims a growth calculation despite the small-sample guard")
    evolution = unique_ids(d.get("theme_evolution", []), "theme_evolution", errors)
    for eid, event in evolution.items():
        if event.get("transition") not in {"emergence", "merge", "split", "decline", "terminology_drift"}:
            errors.append(eid + " has invalid transition type")
        refs(eid, event.get("statistic_refs"), stats_keys, "statistic", errors)
        refs(eid, event.get("record_ids"), independent, "included record", errors)
        refs(eid, event.get("evidence_ids"), evidence, "evidence", errors)
        if not event.get("interval") or not event.get("rationale"):
            errors.append(eid + " needs an evidence-based interval/rationale, not just a keyword label")
    phases = unique_ids(d.get("phases", []), "phase", errors)
    for pid, phase in phases.items():
        if not isinstance(phase.get("start"), int) or not isinstance(phase.get("end"), int) or phase["start"] > phase["end"]:
            errors.append(pid + " has invalid interval")
        refs(pid, phase.get("evidence_ids"), evidence, "evidence", errors)
        if not phase.get("rationale"):
            errors.append(pid + " phase division lacks evidence")
    reps = unique_ids(d.get("representatives", []), "representative", errors)
    rep_phases = set()
    for rid, rep in reps.items():
        record_id, phase_id = rep.get("record_id"), rep.get("phase_id")
        if record_id not in independent or phase_id not in phases:
            errors.append(rid + " representative lacks included record or phase")
            continue
        rep_phases.add(phase_id)
        refs(rid, rep.get("evidence_ids"), evidence, "evidence", errors)
        for evidence_id in rep.get("evidence_ids") or []:
            if evidence_id in evidence and evidence[evidence_id].get("record_id") != record_id:
                errors.append(rid + " representative evidence record_id mismatch: " + evidence_id)
        criteria = rep.get("criteria", [])
        if not criteria or criteria == ["citation_rank"] or not rep.get("rationale"):
            errors.append(rid + " representative selection needs stage/method/route rationale beyond raw citation rank")
        year = independent[record_id].get("dates", {}).get(scope.get("time_basis"))
        phase = phases[phase_id]
        if year is None or not phase["start"] <= year <= phase["end"]:
            errors.append(rid + " representative falls outside its declared phase")
    for pid, phase in phases.items():
        if pid not in rep_phases and not phase.get("no_representative_reason"):
            errors.append(pid + " lacks representative coverage or an explicit limit")
    gaps = d.get("gaps", [])
    for gap in gaps:
        refs(gap.get("id", "gap"), gap.get("basis_evidence_ids"), evidence, "evidence", errors)
        if not gap.get("query_scope") or not gap.get("unresolved") or not gap.get("excluded_nearby"):
            errors.append("trend gap needs actual scope, unresolved mechanism and excluded nearby work")
        for nearby in gap.get("excluded_nearby", []):
            rid = nearby.get("record_id")
            if rid not in records or not nearby.get("reason"):
                errors.append("gap exclusion lacks real record/reason")
    conflicts = data.get("conflicts", [])
    for conflict in conflicts:
        refs("conflict", conflict.get("evidence_ids"), evidence, "evidence", errors, minimum=2)
        if not conflict.get("comparison_dimensions") or not conflict.get("time_or_method_boundary"):
            errors.append("trend conflict needs time/method boundary")
    if not evidence and (trend_claims or evolution or reps or gaps):
        errors.append("empty data cannot establish trends, stages, representatives or research gaps")
    conservation = len(independent) == sum(r["document_count"] for r in rows) + len(missing) + sum(
        1 for r in independent.values() if r.get("dates", {}).get(scope.get("time_basis")) is not None
        and not window[0] <= r["dates"][scope["time_basis"]] <= window[1])
    if not conservation:
        errors.append("included records do not conserve across in-window, missing-year and out-of-window buckets")
    derived = {"year_statistics": rows, "normalized_topics_by_record": topic_by_record,
               "duplicate_record_ids": sorted(duplicates), "missing_year_record_ids": sorted(missing),
               "independent_included_count": len(independent), "growth": growth, "growth_limit": growth_limit,
               "formulas": {"topic_share": "topic document count / all independent included documents with selected year in that year",
                            "missing_year": "reported separately; never imputed or counted as a zero-year publication"},
               "analysis_guard": "Counts and descriptive deltas only; no statistical turning point, global frontier or first-priority claim."}
    audit = {"scope": scope, "selection": list(records.values()), "vocabulary": list(vocabulary.values()),
             "phase_evidence": list(phases.values()), "representatives": list(reps.values()),
             "conflicts": conflicts, "gaps": gaps, "warnings": warnings,
             "records_accounted_for": conservation, "coverage_is_global": False}
    return derived, audit, errors


if __name__ == "__main__":
    sys.exit(cli(argparse.ArgumentParser(description=__doc__), validate))
