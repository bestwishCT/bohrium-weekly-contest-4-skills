#!/usr/bin/env python3
"""Screen a supplied corpus, validate evidence-led themes, and assemble a linked draft."""
import argparse
import sys
sys.dont_write_bytecode = True
from runtime import cli, unique_ids


def validate_refs(owner, refs, available, label, errors, minimum=1):
    if not isinstance(refs, list) or len(refs) < minimum:
        errors.append("%s needs %s references" % (owner, label))
        return
    for ref in refs:
        if ref not in available:
            errors.append("%s references unknown %s %s" % (owner, label, ref))


def validate(data, sources, tools, evidence, base):
    errors, warnings = [], []
    d = data.get("data", {})
    criteria = d.get("criteria", {})
    if not criteria.get("version") or criteria.get("fixed_before_screening") is not True:
        errors.append("inclusion/exclusion criteria must be versioned and fixed before screening")
    if not criteria.get("include") or not criteria.get("exclude"):
        errors.append("both explicit inclusion and exclusion rules are required")
    records = unique_ids(d.get("records", []), "record", errors)
    for rid, record in records.items():
        status = record.get("decision")
        if status not in {"included", "excluded", "duplicate", "unclear"}:
            errors.append(rid + " has invalid screening decision")
        if record.get("criteria_version") != criteria.get("version"):
            errors.append(rid + " was not screened under the current criteria version")
        if status == "excluded" and not record.get("reason"):
            errors.append(rid + " lacks a concrete exclusion reason")
        if status == "unclear" and not record.get("pending_reason"):
            errors.append(rid + " lacks its pending/limited-coverage reason")
        if status == "duplicate":
            parent = record.get("duplicate_of")
            if parent not in records or parent == rid:
                errors.append(rid + " has an invalid duplicate merge target")
            elif records[parent].get("decision") == "duplicate":
                errors.append(rid + " must merge directly to an independent record")
        if record.get("fulltext_status") not in {"fulltext", "abstract_only", "unavailable", "metadata_only"}:
            errors.append(rid + " lacks honest full-text status")
    for revision in criteria.get("revisions", []):
        if not revision.get("reason") or not revision.get("affected_record_ids") or not revision.get("rerun_completed"):
            errors.append("criteria revision lacks reason, affected records, or complete rerun evidence")
        for rid in revision.get("affected_record_ids", []):
            if rid not in records:
                errors.append("criteria revision names unknown record " + rid)
    for eid, row in evidence.items():
        rid = row.get("record_id")
        if rid not in records:
            errors.append(eid + " lacks a real record link")
            continue
        if records[rid].get("decision") != "included":
            errors.append(eid + " cannot support drafting from a non-included or duplicate record")
        status = records[rid].get("fulltext_status")
        if status != "fulltext" and row.get("evidence_level") != "abstract":
            errors.append(eid + " must be explicitly limited to abstract-level evidence")
        if status == "fulltext" and row.get("evidence_level") != "fulltext":
            errors.append(eid + " must identify its full-text evidence level")
    themes = unique_ids(d.get("themes", []), "theme", errors)
    memberships = {}
    for tid, theme in themes.items():
        validate_refs(tid, theme.get("record_ids"), records, "record", errors)
        if len(theme.get("derived_from", [])) < 2 or not theme.get("boundary"):
            errors.append(tid + " needs empirical grouping dimensions and a thematic boundary")
        if not theme.get("record_ids"):
            errors.append(tid + " cannot be a heading without included literature")
        for rid in theme.get("record_ids", []):
            if records.get(rid, {}).get("decision") != "included":
                errors.append(tid + " contains excluded/duplicate record " + rid)
            memberships.setdefault(rid, []).append(tid)
        if len(theme.get("record_ids", [])) == 1:
            warnings.append(tid + " has single-record support; its boundary must not imply a field-wide theme")
    for rid, record in records.items():
        if record.get("decision") == "included" and rid not in memberships:
            errors.append("included record %s is omitted from all themes without explanation" % rid)
    draft_units = unique_ids(d.get("draft_units", []), "draft_unit", errors)
    citation_backlinks = {}
    used_evidence = set()
    for uid, unit in draft_units.items():
        if unit.get("theme_id") not in themes:
            errors.append(uid + " lacks a valid theme")
        validate_refs(uid, unit.get("evidence_ids"), evidence, "evidence", errors)
        if unit.get("kind") not in {"source_fact", "bounded_synthesis", "uncertainty"}:
            errors.append(uid + " must classify statement strength")
        if not unit.get("strength_rationale"):
            errors.append(uid + " lacks support for its calibrated strength")
        for eid in unit.get("evidence_ids", []):
            if ("[@" + eid + "]") not in unit.get("text", ""):
                errors.append(uid + " citation for " + eid + " is not adjacent to its statement")
                continue
            used_evidence.add(eid)
            citation_backlinks.setdefault(eid, []).append(uid)
            row = evidence.get(eid)
            if row and records.get(row.get("record_id"), {}).get("fulltext_status") != "fulltext":
                if unit.get("kind") == "source_fact" and not unit.get("abstract_qualified"):
                    errors.append(uid + " overstates abstract-only evidence")
            if row and unit.get("theme_id") not in memberships.get(row.get("record_id"), []):
                errors.append(uid + " cites a record outside its declared theme")
    unused = d.get("unused_evidence", {})
    for eid in evidence:
        if eid not in used_evidence and not unused.get(eid):
            errors.append("evidence %s is neither used nor explicitly reserved with a reason" % eid)
    for eid, reason in unused.items():
        if eid not in evidence or not reason:
            errors.append("unused_evidence has an invalid row or reason")
    conflicts = data.get("conflicts", [])
    for conflict in conflicts:
        validate_refs("conflict", conflict.get("evidence_ids"), evidence, "evidence", errors, minimum=2)
        if len(conflict.get("comparison_dimensions", [])) < 2 or not conflict.get("unresolved_difference"):
            errors.append("review conflict must compare methods/objects and preserve unresolved differences")
        for eid in conflict.get("evidence_ids", []):
            if eid not in used_evidence:
                errors.append("conflicting evidence %s was hidden from the draft" % eid)
    gaps = d.get("gaps", [])
    for gap in gaps:
        validate_refs(gap.get("id", "gap"), gap.get("basis_evidence_ids"), evidence, "evidence", errors)
        if not gap.get("covered_scope") or not gap.get("unresolved") or not gap.get("excluded_nearby"):
            errors.append("review gap needs covered scope, precise unresolved relation and nearby exclusions")
    if not evidence and (draft_units or gaps):
        errors.append("an empty corpus cannot yield a substantive draft or research gaps")
    batches = d.get("batches", [])
    batched = {eid for batch in batches for eid in batch.get("evidence_ids", [])}
    if evidence and (not batches or batched != set(evidence) or not d.get("cross_batch_check")):
        errors.append("evidence must be persisted in exact batches and receive a cross-batch consistency check")
    independent = [rid for rid, r in records.items() if r.get("decision") == "included"]
    duplicate_count = sum(r.get("decision") == "duplicate" for r in records.values())
    counts = {"input_records": len(records), "independent_included": len(independent),
              "duplicates_merged": duplicate_count,
              "excluded": sum(r.get("decision") == "excluded" for r in records.values()),
              "unclear": sum(r.get("decision") == "unclear" for r in records.values())}
    lines = []
    for unit in draft_units.values():
        lines.append(unit.get("text", ""))
    derived = {"screening_counts": counts, "included_record_ids": sorted(independent),
               "theme_record_map": {tid: t.get("record_ids", []) for tid, t in themes.items()},
               "citation_backlinks": citation_backlinks,
               "draft": "\n\n".join(lines), "coverage_note": "Only independent included records support counts; duplicates are not independent evidence."}
    audit = {"criteria": criteria, "screening": list(records.values()), "themes": list(themes.values()),
             "batches": batches, "cross_batch_check": bool(d.get("cross_batch_check")),
             "conflicts": conflicts, "gaps": gaps, "warnings": warnings,
             "bidirectional_check": {"draft_to_evidence": citation_backlinks,
                                     "evidence_to_draft_or_reason": {eid: citation_backlinks.get(eid, unused.get(eid)) for eid in evidence}}}
    return derived, audit, errors


if __name__ == "__main__":
    sys.exit(cli(argparse.ArgumentParser(description=__doc__), validate))
