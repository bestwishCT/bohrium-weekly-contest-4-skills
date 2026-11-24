#!/usr/bin/env python3
"""Source-conditioned DRAFT benchmark technical checker with traceable scoring."""
import argparse
import json
from pathlib import Path
import sys

from common import Run, add_arguments, digest, read_json, write_json


SCORE_DEFINITIONS = {
    "0": "A forbidden token occurs in the scored output text, or no required token occurs in that text.",
    "1": "No forbidden token occurs and at least one required token matches, but not all score-2 conditions are met.",
    "2": "No forbidden token occurs; required_tokens is nonempty and wholly matched; every source_quotes entry occurs verbatim in both the source bytes and the scored output text.",
}
REVIEW_LABEL = "technical_synthetic_check_not_DSH_or_independent_semantic_validation"


def output_text(path):
    raw = path.read_bytes()
    if not raw.strip():
        raise ValueError("empty_output")
    text = raw.decode("utf-8")
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return str(parsed.get("answer", parsed.get("output", ""))), parsed
        return str(parsed), parsed
    except json.JSONDecodeError:
        return text, None


def reported(container, key):
    value = container.get(key)
    return value is not None and value != ""


def actual_or_not_reported(container, key):
    return container.get(key) if reported(container, key) else "not_reported"


def locate_bytes(raw, quote):
    needle = quote.encode("utf-8")
    start = raw.find(needle)
    if start < 0:
        return False, None
    return True, {
        "quote": quote,
        "byte_range": [start, start + len(needle)],
        "indexing": "zero_based_half_open_utf8_bytes",
    }


def locate_text(text, quote):
    start = text.find(quote)
    if start < 0:
        return False, None
    return True, {
        "quote": quote,
        "character_range": [start, start + len(quote)],
        "indexing": "zero_based_half_open_unicode_characters_in_scored_output_text",
    }


def source_quote_evidence(raw_source, output, quotes):
    evidence = []
    for quote in quotes:
        found_in_source, source_match = locate_bytes(raw_source, quote)
        used_in_output, output_match = locate_text(output, quote)
        evidence.append({
            "quote": quote,
            "found_in_source": found_in_source,
            "used_in_output": used_in_output,
            "source_match": source_match,
            "output_match": output_match,
            "verified": found_in_source and used_in_output,
        })
    return evidence


def follow_up_for(record):
    """Turn only observed failures or below-maximum scores into scoped proposals."""
    question_id = record["question_id"]
    status = record["status"]
    if status == "execution_failed":
        return [{
            "question_id": question_id,
            "trigger": "execution_failed",
            "observed_failure": record["failure"],
            "follow_up_item_suggestion": "Add a focused output-validity case reproducing this observed execution failure; retain score=null until a valid actual output exists.",
            "rescore_scope": {
                "question_ids": [question_id],
                "reason": "execution_failed",
                "recompute": ["output_validation", "technical_check", "score", "dependent_summary_aggregates"],
                "precondition": "A valid replacement output is actually available; unchanged items are excluded.",
            },
            "automatic_repair_performed": False,
            "manual_review_completed": False,
        }]
    if status == "unscorable_reference_missing":
        return [{
            "question_id": question_id,
            "trigger": "reference_missing",
            "observed_failure": record["failure"],
            "follow_up_item_suggestion": "Add this item to a reference-completion work package; supply an actual reference_answer before proposing any technical score.",
            "rescore_scope": {
                "question_ids": [question_id],
                "reason": "reference_missing",
                "recompute": ["technical_check", "score", "dependent_summary_aggregates"],
                "precondition": "An actual reference_answer is supplied in a revised workorder; unchanged items are excluded.",
            },
            "automatic_repair_performed": False,
            "manual_review_completed": False,
        }]
    if status == "technical_check_completed" and record["score"] < record["max_score"]:
        missing_tokens = record["missing_required_tokens"]
        missing_quotes = [entry["quote"] for entry in record["source_quote_evidence"] if not entry["verified"]]
        forbidden = record["unexpected_forbidden_tokens"]
        missing_evidence = bool(missing_tokens or missing_quotes)
        return [{
            "question_id": question_id,
            "trigger": "missing_required_evidence" if missing_evidence else "low_score",
            "observed_evidence": {
                "score": record["score"],
                "matched_required_tokens": record["matched_required_tokens"],
                "missing_required_tokens": missing_tokens,
                "source_quotes_not_verified_in_both_source_and_output": missing_quotes,
                "unexpected_forbidden_tokens": forbidden,
            },
            "follow_up_item_suggestion": "Add a focused item for the listed missing or conflicting required evidence; this proposal does not alter the original output or assert semantic correctness.",
            "rescore_scope": {
                "question_ids": [question_id],
                "reason": "missing required evidence" if missing_evidence else "low score with forbidden-token or no-match evidence",
                "recompute": ["technical_check", "score", "dependent_summary_aggregates"],
                "precondition": "Only after the cited item evidence actually changes; unchanged items and scientific sources are excluded.",
            },
            "automatic_repair_performed": False,
            "manual_review_completed": False,
        }]
    return []


def score_reason(score, matched, missing, unexpected, quote_rows):
    verified_count = sum(row["verified"] for row in quote_rows)
    return (
        f"score={score}; matched_required_tokens={matched}; missing_required_tokens={missing}; "
        f"unexpected_forbidden_tokens={unexpected}; verified_source_quotes={verified_count}/{len(quote_rows)} "
        "(verbatim in both source and scored output). Automatic technical evidence only; semantic correctness remains pending manual review."
    )


def main():
    args = add_arguments(argparse.ArgumentParser(description=__doc__)).parse_args()
    try:
        run = Run(args)
    except Exception as error:
        print(json.dumps({"status": "input_error", "error": str(error)}), file=sys.stderr)
        return 2
    try:
        workorder_path = run.path(run.case.get("benchmark_workorder", "benchmark-workorder.json"))
        workorder = read_json(workorder_path)
        items = workorder.get("items", [])
        if len(items) != 4 or workorder.get("status") != "benchmark-DRAFT":
            raise ValueError("revision 2 requires a four-item benchmark-DRAFT workorder")
        seen = set()
        checks = []
        sources = []
        execution_failures = []
        for item_index, item in enumerate(items):
            question_id = str(item.get("question_id", ""))
            if not question_id or question_id in seen:
                raise ValueError("question_id must be unique and nonempty")
            seen.add(question_id)
            source_path = run.path(item["source_path"])
            source_bytes = source_path.read_bytes()
            source_record = {"question_id": question_id, "path": str(source_path),
                             "sha256": digest(source_path), "bytes": len(source_bytes),
                             "source_scope": "paragraph/table text only; no PDF-pixel gold"}
            sources.append(source_record)
            config = {"question_id": question_id, "workorder_sha256": digest(workorder_path),
                      "prompt_version": workorder.get("prompt_version"),
                      "tool_permissions": workorder.get("tool_permissions"),
                      "budget": workorder.get("budget"), "benchmark_version": workorder.get("version"),
                      "runner": "local deterministic technical checker; not DSH"}
            reference = item.get("reference_answer")
            reference_provided = bool(reference)
            rubric_version = actual_or_not_reported(reference, "rubric_version") if isinstance(reference, dict) else "not_reported"
            if reported(item, "data_license"):
                data_license = item["data_license"]
            elif reported(item, "license"):
                data_license = item["license"]
            elif reported(workorder, "data_license"):
                data_license = workorder["data_license"]
            elif reported(workorder, "license"):
                data_license = workorder["license"]
            else:
                data_license = "not_reported"
            reference_location = f"{workorder_path}#/items/{item_index}/reference_answer"
            task_card = {
                "task": actual_or_not_reported(item, "task"),
                "source_path": str(source_path),
                "source_sha256": source_record["sha256"],
                "output_path": None,
                "output_sha256": None,
                "reference_answer_provided": reference_provided,
                "reference_answer_location": reference_location,
                "reference_answer": reference if reference_provided else None,
                "rubric_version": rubric_version,
                "task_type": actual_or_not_reported(item, "task_type"),
                "difficulty": actual_or_not_reported(item, "difficulty"),
                "data_license": data_license,
            }
            record = {"question_id": question_id, "task": item.get("task"),
                      "task_card": task_card,
                      "rubric": {"rubric_version": rubric_version,
                                 "score_definitions": dict(SCORE_DEFINITIONS),
                                 "scope": "deterministic required-token, forbidden-token, and dual-source/output verbatim-quote checks only"},
                      "run_config": config, "original_output_path": None,
                      "raw_output_sha256": None,
                      "score": None, "max_score": 2, "status": None,
                      "manual_semantic_review_required": True,
                      "checker_label": REVIEW_LABEL,
                      "failure": None, "scoring_reason": "not scored",
                      "follow_up_recommendations": []}
            try:
                output_path = run.path(item["output_path"])
                record["original_output_path"] = str(output_path)
                task_card["output_path"] = str(output_path)
                record["raw_output_sha256"] = digest(output_path)
                task_card["output_sha256"] = record["raw_output_sha256"]
                text, parsed = output_text(output_path)
                if item.get("output_format") == "json" and parsed is None:
                    raise ValueError("format_error_json")
                record["raw_output"] = parsed if parsed is not None else text
                maximum_length = item.get("maximum_output_characters")
                if maximum_length is not None and len(text) > int(maximum_length):
                    raise ValueError("output_too_long")
            except Exception as error:
                record["status"] = "execution_failed"
                record["failure"] = str(error)
                record["scoring_reason"] = f"not scored: execution_failed ({error}); process failure is score=null, not score zero."
                execution_failures.append(record["failure"])
                record["follow_up_recommendations"] = follow_up_for(record)
                checks.append(record)
                continue
            if not reference_provided:
                record["status"] = "unscorable_reference_missing"
                record["failure"] = "reference answer absent; process failure must not be converted to score zero"
                record["scoring_reason"] = "not scored: reference_missing; execution succeeded but no reference_answer is available."
                record["follow_up_recommendations"] = follow_up_for(record)
                checks.append(record)
                continue
            required = reference.get("required_tokens", [])
            forbidden = reference.get("forbidden_tokens", [])
            exact_quotes = reference.get("source_quotes", [])
            matched = [token for token in required if token in text]
            unexpected = [token for token in forbidden if token in text]
            quote_rows = source_quote_evidence(source_bytes, text, exact_quotes)
            verified_quotes = [row["quote"] for row in quote_rows if row["verified"]]
            if unexpected:
                score = 0
            elif required and len(matched) == len(required) and len(verified_quotes) == len(exact_quotes):
                score = 2
            elif matched:
                score = 1
            else:
                score = 0
            missing = [token for token in required if token not in matched]
            record.update({"status": "technical_check_completed", "score": score,
                           "matched_required_tokens": matched, "missing_required_tokens": missing,
                           "unexpected_forbidden_tokens": unexpected,
                           "verified_source_quotes": verified_quotes,
                           "source_quote_evidence": quote_rows,
                           "scoring_reason": score_reason(score, matched, missing, unexpected, quote_rows),
                           "rubric_version": rubric_version})
            record["follow_up_recommendations"] = follow_up_for(record)
            checks.append(record)

        recommendations = [recommendation for record in checks for recommendation in record["follow_up_recommendations"]]
        scored = [item["score"] for item in checks if item["score"] is not None]
        grouped = {}
        for item, record in zip(items, checks):
            type_is_reported = reported(item, "task_type")
            group_name = str(item["task_type"]) if type_is_reported else "unclassified"
            group = grouped.setdefault(group_name, {
                "task_type": group_name,
                "classification_basis": "actual workorder item task_type" if type_is_reported else "unclassified because the workorder item did not provide task_type; no task type was inferred",
                "total_items": 0, "scored_items": 0, "excluded_items": 0,
                "excluded_score_null": 0, "excluded_execution_failed": 0,
                "excluded_reference_missing": 0, "score_sum": 0,
                "comparability_note": "Single-run descriptive aggregation only; not repeated-trial variability evidence. Compare only matching task, reference, rubric, and evaluation-object versions, retaining exclusions.",
            })
            group["total_items"] += 1
            if record["score"] is None:
                group["excluded_items"] += 1
                group["excluded_score_null"] += 1
                group["excluded_execution_failed"] += int(record["status"] == "execution_failed")
                group["excluded_reference_missing"] += int(record["status"] == "unscorable_reference_missing")
            else:
                group["scored_items"] += 1
                group["score_sum"] += record["score"]
        summary = {"total_items": len(items), "scored_items": len(scored), "score_sum": sum(scored),
                   "execution_failures": sum(item["status"] == "execution_failed" for item in checks),
                   "unscorable_items": sum(item["status"] == "unscorable_reference_missing" for item in checks),
                   "excluded_score_null": sum(item["score"] is None for item in checks),
                   "aggregation_excludes_failed_and_unscorable": True,
                   "by_task_type": grouped,
                   "comparability_note": "Counts and scores describe this single deterministic synthetic run, not repeated-trial fluctuation. Groups with unclassified task_type mean the workorder omitted task_type; no type was inferred. score_null items are excluded and are not zeros.",
                   "follow_up_recommendations": recommendations,
                   "recommendation_status": "proposals only; no automatic repair, manual semantic review, or rescore was performed",
                   "not_a_DSH_score": True}
        version_changes = []
        for item, record in zip(items, checks):
            version_changes.append({"question_id": item["question_id"],
                                    "source_sha256": next(source["sha256"] for source in sources if source["question_id"] == item["question_id"]),
                                    "revision_signature_sha256": item.get("revision_signature_sha256"),
                                    "rubric_version": record["task_card"]["rubric_version"],
                                    "task_type": record["task_card"]["task_type"],
                                    "rescore_scope": [recommendation["rescore_scope"] for recommendation in record["follow_up_recommendations"]],
                                    "history_comparability": "comparable_only_if_task_answer_rule_and_agent_versions_match",
                                    "unchanged_version_rescore_policy": "If task, reference_answer, rubric, and evaluation-object versions are unchanged, recompute only the affected question_ids and checks declared in rescore_scope plus dependent summary aggregates; never rewrite historical sources or scientific materials."})
        status = "partial" if execution_failures or any(item["status"] == "unscorable_reference_missing" for item in checks) else "success"
        write_json(run.output / "scores.json", {"checks": checks, "summary": summary})
        reread = read_json(run.output / "scores.json")
        if len(reread["checks"]) != 4:
            raise ValueError("score JSON readback did not retain four items")
        run.changes.append("preserved source bytes and raw output hashes; execution failure and missing reference were not scored zero")
        run.changes.append("verified quotes require verbatim evidence in both source bytes and scored output; baseline synthetic scores remain 2/1/0/2")
        run.changes.append("recorded derived task cards, per-band rubric definitions and reasons, task-type exclusions, scoped follow-up proposals, and unchanged-version rescore boundaries without automatic repair or manual review claims")
        run.uncertainty.append("Automatic token/quote checks do not establish scientific semantic correctness; every item still requires manual semantic review.")
        run.finish({"status": status, "benchmark_version": workorder.get("version"),
                    "checks": checks, "summary": summary, "version_changes": version_changes,
                    "sources": sources},
                   aliases={"scores": "checks", "results": "checks", "result": "checks"},
                   code=2 if status == "partial" else 0,
                   trace_extra={"scope": "technical synthetic checks only", "score_readback": True,
                                "follow_up_proposals": len(recommendations),
                                "automatic_repair_performed": False,
                                "manual_semantic_review_completed": False})
    except Exception as error:
        run.uncertainty.append(str(error))
        run.finish({"status": "input_error", "checks": [], "summary": {}, "failures": [str(error)]}, code=2)


if __name__ == "__main__":
    sys.exit(main())
