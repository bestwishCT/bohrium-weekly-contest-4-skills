#!/usr/bin/env python3
"""Source-conditioned DRAFT benchmark technical checker with traceable scoring."""
import argparse
import json
from pathlib import Path
import re
import sys

from common import Run, add_arguments, digest, read_json, write_json


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
        for item in items:
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
            record = {"question_id": question_id, "task": item.get("task"),
                      "run_config": config, "original_output_path": None,
                      "score": None, "max_score": 2, "status": None,
                      "manual_semantic_review_required": True,
                      "checker_label": "technical_synthetic_check_not_DSH_or_independent_semantic_validation",
                      "failure": None}
            try:
                output_path = run.path(item["output_path"])
                record["original_output_path"] = str(output_path)
                text, parsed = output_text(output_path)
                record["raw_output_sha256"] = digest(output_path)
                if item.get("output_format") == "json" and parsed is None:
                    raise ValueError("format_error_json")
                record["raw_output"] = parsed if parsed is not None else text
                maximum_length = item.get("maximum_output_characters")
                if maximum_length is not None and len(text) > int(maximum_length):
                    raise ValueError("output_too_long")
            except Exception as error:
                record["status"] = "execution_failed"
                record["failure"] = str(error)
                execution_failures.append(record["failure"])
                checks.append(record)
                continue
            reference = item.get("reference_answer")
            if not reference:
                record["status"] = "unscorable_reference_missing"
                record["failure"] = "reference answer absent; process failure must not be converted to score zero"
                checks.append(record)
                continue
            required = reference.get("required_tokens", [])
            forbidden = reference.get("forbidden_tokens", [])
            exact_quotes = reference.get("source_quotes", [])
            matched = [token for token in required if token in text]
            unexpected = [token for token in forbidden if token in text]
            verified_quotes = [quote for quote in exact_quotes if quote.encode("utf-8") in source_bytes]
            if unexpected:
                score = 0
            elif required and len(matched) == len(required) and len(verified_quotes) == len(exact_quotes):
                score = 2
            elif matched:
                score = 1
            else:
                score = 0
            record.update({"status": "technical_check_completed", "score": score,
                           "matched_required_tokens": matched, "missing_required_tokens": [token for token in required if token not in matched],
                           "unexpected_forbidden_tokens": unexpected,
                           "verified_source_quotes": verified_quotes,
                           "scoring_reason": "0/1/2 deterministic token/quote rubric; semantic correctness remains pending",
                           "rubric_version": reference.get("rubric_version")})
            checks.append(record)
        scored = [item["score"] for item in checks if item["score"] is not None]
        summary = {"total_items": len(items), "scored_items": len(scored), "score_sum": sum(scored),
                   "execution_failures": sum(item["status"] == "execution_failed" for item in checks),
                   "unscorable_items": sum(item["status"] == "unscorable_reference_missing" for item in checks),
                   "aggregation_excludes_failed_and_unscorable": True,
                   "not_a_DSH_score": True}
        version_changes = []
        for item in items:
            version_changes.append({"question_id": item["question_id"],
                                    "source_sha256": next(source["sha256"] for source in sources if source["question_id"] == item["question_id"]),
                                    "revision_signature_sha256": item.get("revision_signature_sha256"),
                                    "history_comparability": "comparable_only_if_task_answer_rule_and_agent_versions_match"})
        status = "partial" if execution_failures or any(item["status"] == "unscorable_reference_missing" for item in checks) else "success"
        write_json(run.output / "scores.json", {"checks": checks, "summary": summary})
        reread = read_json(run.output / "scores.json")
        if len(reread["checks"]) != 4:
            raise ValueError("score JSON readback did not retain four items")
        run.changes.append("preserved source bytes and raw output hashes; execution failure and missing reference were not scored zero")
        run.finish({"status": status, "benchmark_version": workorder.get("version"),
                    "checks": checks, "summary": summary, "version_changes": version_changes,
                    "sources": sources},
                   aliases={"scores": "checks", "results": "checks", "result": "checks"},
                   code=2 if status == "partial" else 0,
                   trace_extra={"scope": "technical synthetic checks only", "score_readback": True})
    except Exception as error:
        run.uncertainty.append(str(error))
        run.finish({"status": "input_error", "checks": [], "summary": {}, "failures": [str(error)]}, code=2)


if __name__ == "__main__":
    sys.exit(main())
