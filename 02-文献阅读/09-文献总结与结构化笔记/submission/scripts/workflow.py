#!/usr/bin/env python3
"""Build chapter-aligned, evidence-linked reusable notes without IMRaD reshaping."""
import argparse
import json
import re
import sys

from common import Run, add_arguments, evidence_ok, paragraph_map, read_json, write_json

NOTE_FIELDS = ["purpose", "research_question", "data", "methods", "results", "conclusions", "limitations", "prerequisites"]


def main():
    args = add_arguments(argparse.ArgumentParser(description=__doc__)).parse_args()
    try:
        run = Run(args)
    except Exception as error:
        print(json.dumps({"status": "input_error", "error": str(error)}), file=sys.stderr)
        return 2
    try:
        material_path = run.path(run.case.get("material_path", "material.json"))
        material = read_json(material_path)
        sections = material.get("sections", [])
        if not sections:
            raise ValueError("actual source sections are required; a generic template is not a substitute")
        indices = {}
        global_index = {}
        for section in sections:
            section_id = str(section.get("section_id", ""))
            if not section_id or section_id in indices:
                raise ValueError("source section IDs must be nonempty and unique")
            rows = section.get("paragraphs", [])
            indices[section_id] = paragraph_map(rows)
            global_index.update(indices[section_id])
        candidate = run.candidate("notes.json")
        supplied = candidate.get("section_notes", [])
        by_id = {str(item.get("section_id")): item for item in supplied}
        if len(by_id) != len(supplied):
            raise ValueError("duplicate candidate section notes")
        notes = []
        failures = []
        sources = []
        for section in sections:
            section_id = str(section["section_id"])
            note = dict(by_id.get(section_id, {}))
            note["section_id"] = section_id
            note["source_title"] = section.get("title", "")
            note["source_order"] = section.get("order", len(notes) + 1)
            references = note.get("evidence", [])
            local_failures = []
            if section_id not in by_id:
                local_failures.append("actual section not covered")
            if section_id in by_id:
                for field in NOTE_FIELDS:
                    if field not in note:
                        local_failures.append("required note field was not explicitly supplied: " + field)
            for field in NOTE_FIELDS:
                note.setdefault(field, None)
            if not references:
                local_failures.append("section note lacks evidence")
            for reference in references:
                if not evidence_ok(indices[section_id], reference):
                    local_failures.append("section anchor/quote mismatch")
                else:
                    sources.append({"section_id": section_id, **reference})
            for item in note.get("knowledge_items", []):
                item.setdefault("dedupe_key", item.get("text", "").strip().lower())
                item.setdefault("conditions", "")
                refs = item.get("evidence", [])
                if not refs:
                    local_failures.append("knowledge item lacks evidence: " + item.get("text", ""))
                for reference in refs:
                    if not evidence_ok(indices[section_id], reference):
                        local_failures.append("knowledge item quote mismatch: " + item.get("text", ""))
                    else:
                        sources.append({"section_id": section_id, "knowledge_item": item.get("text"), **reference})
            if local_failures:
                note["status"] = "partial"
                failures.extend({"section_id": section_id, "reason": reason} for reason in local_failures)
            else:
                note["status"] = "reviewed_structurally"
            notes.append(note)
        merged = {}
        kept_separate = []
        conflicts = []
        for note in notes:
            for item in note.get("knowledge_items", []):
                condition_key = json.dumps(item.get("conditions"), sort_keys=True, ensure_ascii=False)
                key = (item["dedupe_key"], condition_key)
                if key not in merged:
                    merged[key] = dict(item)
                    merged[key]["section_ids"] = [note["section_id"]]
                elif merged[key].get("text") == item.get("text"):
                    merged[key].setdefault("evidence", []).extend(item.get("evidence", []))
                    merged[key]["section_ids"].append(note["section_id"])
                else:
                    conflicts.append({"dedupe_key": item["dedupe_key"], "conditions": item["conditions"],
                                      "reason": "same key/conditions but different assertion text; not merged"})
        seen_keys = {}
        for key, item in merged.items():
            if item["dedupe_key"] in seen_keys and seen_keys[item["dedupe_key"]] != key[1]:
                kept_separate.append({"dedupe_key": item["dedupe_key"], "reason": "conditions differ"})
            seen_keys[item["dedupe_key"]] = key[1]
        knowledge_items = list(merged.values())
        search_index = {}
        for position, item in enumerate(knowledge_items):
            for token in sorted(set(re.findall(r"[\w-]+", (item.get("text", "") + " " + item["dedupe_key"]).lower()))):
                search_index.setdefault(token, []).append(position)
        coverage = {"source_sections": [str(section["section_id"]) for section in sections],
                    "not_summarized_section_ids": [note["section_id"] for note in notes if note["section_id"] not in by_id],
                    "source_order_preserved": True}
        if conflicts:
            failures.extend(conflicts)
        status = "partial" if failures or run.uncertainty else "success"
        export = {"case_id": run.case_id, "section_notes": notes, "knowledge_items": knowledge_items,
                  "search_index": search_index, "coverage": coverage}
        write_json(run.output / "notes.json", export)
        reread = read_json(run.output / "notes.json")
        if len(reread["section_notes"]) != len(sections):
            raise ValueError("structured notes readback section mismatch")
        run.changes.append("used actual source section order, condition-aware deduplication and JSON parser readback")
        run.finish({"status": status, "paper_card": candidate.get("paper_card", {}),
                    "section_notes": notes, "knowledge_items": knowledge_items,
                    "search_index": search_index, "coverage": coverage,
                    "kept_separate": kept_separate, "failures": failures, "sources": sources},
                   aliases={"notes": "section_notes", "chapternotes": "section_notes", "result": "section_notes"},
                   code=2 if status == "partial" else 0,
                   trace_extra={"json_readback": True, "machine_quote_check": True,
                                "semantic_fields": "Agent-authored from read sources; not independently human validated"})
    except Exception as error:
        run.uncertainty.append(str(error))
        run.finish({"status": "partial", "section_notes": [], "knowledge_items": [], "coverage": {},
                    "failures": [{"reason": str(error)}]}, code=2)


if __name__ == "__main__":
    sys.exit(main())
