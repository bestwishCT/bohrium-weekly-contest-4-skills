#!/usr/bin/env python3
"""Two-stage multi-document semantic-field and comparability alignment audit."""
import argparse
import itertools
import json
import sys

from common import Run, add_arguments, evidence_ok, paragraph_map, paragraphs, read_json, stable_id

CONDITION_KEYS = ["task", "metric", "unit", "material", "pollutant", "budget", "dataset_version", "split", "baseline"]


def main():
    args = add_arguments(argparse.ArgumentParser(description=__doc__)).parse_args()
    try:
        run = Run(args)
    except Exception as error:
        print(json.dumps({"status": "input_error", "error": str(error)}), file=sys.stderr)
        return 2
    try:
        workorder_value = run.case.get("workorder", "comparison-workorder.json")
        workorder_path = run.path(workorder_value)
        workorder = read_json(workorder_path)
        documents = workorder.get("documents", [])
        if len(documents) < 2:
            raise ValueError("a comparison requires at least two supplied source documents")
        indices = {}
        inventory = []
        for document in documents:
            source_id = str(document.get("source_document_id", ""))
            if not source_id or source_id in indices:
                raise ValueError("missing or duplicate source_document_id")
            path = run.path(document["path"])
            rows = paragraphs(read_json(path))
            indices[source_id] = paragraph_map(rows)
            inventory.append({"source_document_id": source_id, "path": str(path),
                              "version": document.get("version", "not_reported"),
                              "paragraph_ids": sorted(indices[source_id])})
        candidate = run.candidate("comparison-cells.json")
        cells = candidate.get("cells", [])
        requested_fields = workorder.get("fields", candidate.get("fields", []))
        failures = []
        normalized_cells = []
        cell_by_id = {}
        for position, cell in enumerate(cells, 1):
            source_id = str(cell.get("source_document_id", ""))
            cell_id = cell.get("cell_id") or stable_id("cell", run.case_id, cell.get("field_id"), source_id, position)
            item = dict(cell)
            item["cell_id"] = cell_id
            if source_id not in indices:
                failures.append({"cell_id": cell_id, "kind": "unknown_source_document"})
            elif cell.get("status", "reported") == "reported" and not evidence_ok(indices[source_id], cell):
                failures.append({"cell_id": cell_id, "kind": "source_or_quote_mismatch"})
            if not cell.get("field_id"):
                failures.append({"cell_id": cell_id, "kind": "missing_field_id"})
            if cell.get("status", "reported") == "not_reported" and cell.get("value") is not None:
                failures.append({"cell_id": cell_id, "kind": "unreported_value_must_be_null"})
            item.setdefault("value", None)
            item.setdefault("status", "reported")
            item["conditions"] = {key: item.get(key) for key in CONDITION_KEYS}
            group_key = json.dumps(item["conditions"], sort_keys=True, ensure_ascii=False)
            item["comparability_group"] = stable_id("group", cell.get("field_id"), group_key)
            item["conditions_complete"] = all(item.get(key) not in (None, "") for key in CONDITION_KEYS)
            if not item["conditions_complete"] and item.get("status") == "reported":
                run.uncertainty.append("missing comparability conditions on " + cell_id + "; not assumed equal")
            normalized_cells.append(item)
            cell_by_id[cell_id] = item
        present = {(cell.get("field_id"), cell.get("source_document_id")) for cell in normalized_cells}
        coverage = []
        for field in requested_fields:
            field_id = field.get("field_id") if isinstance(field, dict) else str(field)
            for source_id in indices:
                if (field_id, source_id) not in present:
                    coverage.append({"field_id": field_id, "source_document_id": source_id,
                                     "status": "not_extracted_requires_review"})
                    failures.append({"field_id": field_id, "source_document_id": source_id, "kind": "requested_cell_missing"})
        comparisons = []
        for left, right in itertools.combinations(normalized_cells, 2):
            if left.get("field_id") != right.get("field_id") or left.get("source_document_id") == right.get("source_document_id"):
                continue
            comparable = (left["comparability_group"] == right["comparability_group"]
                          and left["conditions_complete"] and right["conditions_complete"]
                          and left["status"] == right["status"] == "reported")
            differences = [key for key in CONDITION_KEYS if left.get(key) != right.get(key)]
            comparisons.append({"left_cell_id": left["cell_id"], "right_cell_id": right["cell_id"],
                                "comparability": "comparable" if comparable else "noncomparable",
                                "condition_differences": differences,
                                "value_relation": "requires_semantic_review" if comparable else "not_computed",
                                "reason": "all declared task/metric/unit/material/pollutant/budget/data conditions match" if comparable else "different or unreported comparability conditions"})
        syntheses = []
        for synthesis in candidate.get("syntheses", []):
            members = [cell_by_id.get(identifier) for identifier in synthesis.get("cell_ids", [])]
            if any(member is None for member in members):
                failures.append({"kind": "synthesis_unknown_cell", "detail": synthesis})
                continue
            groups = {member["comparability_group"] for member in members}
            record = dict(synthesis)
            if len(groups) != 1 or not all(member["conditions_complete"] for member in members):
                failures.append({"kind": "noncomparable_pool", "cell_ids": synthesis.get("cell_ids", [])})
                record["status"] = "rejected_noncomparable"
            else:
                record["status"] = "allowed_scope_but_semantics_requires_review"
            syntheses.append(record)
        status = "partial" if failures or run.uncertainty else "success"
        run.changes.append("kept source values unchanged and prohibited quantitative pooling across condition groups")
        run.finish({"status": status, "documents": inventory, "matrix": normalized_cells,
                    "comparisons": comparisons, "syntheses": syntheses, "coverage_gaps": coverage,
                    "failures": failures, "sources": inventory},
                   aliases={"comparisonmatrix": "matrix", "cells": "matrix", "result": "matrix"},
                   code=2 if status == "partial" else 0,
                   trace_extra={"pooling_rule": "no automatic means, effect sizes or scientific conflict attribution"})
    except Exception as error:
        run.uncertainty.append(str(error))
        run.finish({"status": "input_error", "documents": [], "matrix": [], "comparisons": [],
                    "failures": [{"kind": "input_error", "reason": str(error)}]}, code=2)


if __name__ == "__main__":
    sys.exit(main())
