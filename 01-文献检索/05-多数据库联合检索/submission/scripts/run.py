#!/usr/bin/env python3
"""Replayable source -> mapping -> identity -> field merge chain."""
from common import dedupe, digest, norm_doi, norm_text, records_at, run


FIELDS = ["title", "authors", "year", "venue", "doi", "record_type"]
DEFAULT_MAPPING = {"title": "title", "authors": "authors", "year": "year", "venue": "venue", "doi": "doi", "record_type": "record_type"}


def comparable(value):
    return digest(value)


def process(case, data, task, inv):
    sources = records_at(data, ["sources", "databases", "exports"])
    mappings = data.get("field_mappings", {}) if isinstance(data, dict) else {}
    priorities = data.get("source_priority", {}) if isinstance(data, dict) else {}
    occurrences, source_logs, query_logs, coverage = [], [], [], []
    for s_index, source in enumerate(sources):
        name = norm_text(source.get("name", source.get("source", "source%02d" % (s_index + 1))))
        state = source.get("status", "observed" if source.get("executed") is True else "planned")
        executed = source.get("executed") is True
        success = executed and state in {"success", "observed", "completed", "partial"}
        mapping = mappings.get(name, source.get("field_mapping", DEFAULT_MAPPING))
        raw_records = source.get("records", [])
        raw_records = raw_records if isinstance(raw_records, list) else []
        source_logs.append({"source": name, "executed": executed, "status": state, "capabilities": source.get("capabilities", {}), "field_mapping": mapping, "observed_record_count": len(raw_records) if success else 0, "response_locator": source.get("response_locator", ""), "license_scope": source.get("license_scope", "unspecified")})
        queries = source.get("queries", [source.get("query", {})])
        for q_index, query in enumerate(queries):
            if isinstance(query, str):
                query = {"expression": query}
            query_logs.append({"source": name, "query_id": query.get("id", "%s-q%d" % (name, q_index + 1)), "expression": query.get("expression", ""), "parameters": query.get("parameters", {}), "purpose": query.get("purpose", ""), "status": query.get("status", state), "executed": query.get("executed", executed), "response_locator": query.get("response_locator", source.get("response_locator", ""))})
        if not success:
            coverage.append({"source": name, "kind": "source_unavailable_or_not_executed", "status": state, "effect": "no-hit equivalence forbidden; coverage unknown"})
            continue
        for r_index, raw in enumerate(raw_records):
            if not isinstance(raw, dict):
                coverage.append({"source": name, "kind": "record_parse_failure", "record_index": r_index})
                continue
            normalized, field_sources = {}, {}
            for field in FIELDS:
                raw_key = mapping.get(field) if isinstance(mapping, dict) else None
                if isinstance(raw_key, list):
                    present = next((key for key in raw_key if key in raw), None)
                else:
                    present = raw_key if raw_key in raw else None
                if present is not None and raw[present] not in (None, ""):
                    raw_value = raw[present]
                    normalized[field] = (norm_doi(raw_value) or raw_value) if field == "doi" else raw_value
                    field_sources[field] = {"source": name, "raw_record_id": raw.get("id", raw.get("record_id", r_index)), "raw_field": present, "raw_value": raw_value, "response_locator": source.get("response_locator", ""), "raw_semantics": source.get("field_semantics", {}).get(field, "as_declared_by_mapping")}
                else:
                    normalized[field] = None
            normalized["record_status"] = raw.get("record_status")
            normalized["_source"] = name
            normalized["_raw_id"] = raw.get("id", raw.get("record_id", r_index))
            normalized["_response_locator"] = source.get("response_locator", "")
            normalized["_field_sources"] = field_sources
            normalized["_raw_semantics"] = source.get("field_semantics", {})
            occurrences.append(normalized)
    keys, clusters, possible, identity_conflicts = dedupe(occurrences)
    for item, key in zip(occurrences, keys):
        item["_key"] = key
    merged, conflicts = [], []
    for cluster in clusters:
        members = [item for item in occurrences if item["_key"] in cluster["members"]]
        record = {"record_id": cluster["master"], "source_members": [{"source": item["_source"], "raw_record_id": item["_raw_id"], "response_locator": item["_response_locator"]} for item in members], "duplicate_evidence": cluster["evidence"], "fields": {}, "field_sources": {}, "merge_policy": "explicit per-field source priority; equal-priority different values stay unresolved"}
        for field in FIELDS:
            candidates = [item for item in members if item[field] not in (None, "", [])]
            by_value = {}
            for item in candidates:
                by_value.setdefault(comparable(item[field]), {"value": item[field], "providers": []})["providers"].append(item)
            alternatives = list(by_value.values())
            chosen, resolution = None, "no_source_provided"
            if len(alternatives) == 1:
                chosen, resolution = alternatives[0]["value"], "consistent_sources"
            elif alternatives:
                priority = priorities.get(field, [])
                ranked = []
                for alternative in alternatives:
                    provider_names = {p["_source"] for p in alternative["providers"]}
                    ranks = [priority.index(name) for name in provider_names if name in priority]
                    ranked.append((min(ranks) if ranks else len(priority), alternative))
                best = min(rank for rank, _ in ranked)
                winners = [alt for rank, alt in ranked if rank == best]
                if priority and len(winners) == 1:
                    chosen, resolution = winners[0]["value"], "resolved_by_declared_source_priority"
                else:
                    resolution = "unresolved_equal_priority_conflict"
                conflict = {"record_id": cluster["master"], "field": field, "alternatives": [{"value": alt["value"], "providers": [p["_field_sources"][field] for p in alt["providers"]]} for alt in alternatives], "resolution": resolution}
                conflicts.append(conflict)
            record["fields"][field] = chosen
            record["field_sources"][field] = [item["_field_sources"][field] for item in candidates if chosen is not None and comparable(item[field]) == comparable(chosen)]
            if chosen is None and alternatives:
                record["field_sources"][field] = []
        record["record_status_assertions"] = [{"source": item["_source"], "value": item.get("record_status"), "response_locator": item["_response_locator"]} for item in members if item.get("record_status")]
        sources_for_record = sorted({item["_source"] for item in members})
        record["single_source"] = len(sources_for_record) == 1
        record["observed_sources"] = sources_for_record
        merged.append(record)
    merged.sort(key=lambda x: x["record_id"])
    present_fields = {field: sum(record["fields"][field] not in (None, "", []) for record in merged) for field in FIELDS}
    for field in FIELDS:
        if merged and present_fields[field] == 0:
            coverage.append({"kind": "systematic_field_absence", "field": field, "effect": "left null; not inferred from similar fields"})
    unresolved_conflicts = [c for c in conflicts if c["resolution"] == "unresolved_equal_priority_conflict"]
    status = "empty" if not occurrences and not coverage else "partial" if coverage or unresolved_conflicts or possible or not occurrences else "completed"
    return {
        "status": status, "status_detail": {"completed": ["per-source query log", "mapped field provenance", "conservative identity and field merge"], "incomplete": coverage + (["unresolved field conflicts"] if unresolved_conflicts else []) + (["identity conflicts require review; metadata merges across explicit DOI conflicts were blocked"] if possible else [])},
        "source_capability_matrix": source_logs, "source_queries": query_logs, "field_mappings": mappings or DEFAULT_MAPPING,
        "merged_records": merged, "primary_results": merged, "dedup_clusters": clusters, "possible_duplicate_identities": possible,
        "identity_conflicts": identity_conflicts, "field_conflicts": conflicts, "coverage_gaps": coverage,
        "single_source_records": [{"record_id": record["record_id"], "source": record["observed_sources"][0]} for record in merged if record["single_source"]],
        "metrics": {"source_count": len(sources), "successful_sources": sum(log["executed"] and log["status"] in {"success", "observed", "completed", "partial"} for log in source_logs), "input_records": len(occurrences), "merged_works": len(merged), "field_presence_counts": present_fields, "unresolved_field_conflicts": sum(c["resolution"] == "unresolved_equal_priority_conflict" for c in conflicts)},
        "uncertainties": ["Only supplied finite responses were merged; no live access, global novelty or exhaustive cross-database coverage is claimed.", "Publication-year and online-year semantics are never interchanged without explicit source field mappings."],
        "change_records": ["Ignored records from failed or unexecuted sources instead of treating failures as zero hits.", "Applied field-level source selection and retained all conflicting equal-priority values.", digest({"records": merged, "coverage": coverage})],
        "_aliases": {"results": "merged_records", "records": "merged_records", "queries": "source_queries", "sources_status": "source_capability_matrix", "conflicts": "field_conflicts", "metrics_summary": "metrics"},
    }


if __name__ == "__main__":
    raise SystemExit(run("multi-source-field-merge", process))
