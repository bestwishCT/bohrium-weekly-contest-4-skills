#!/usr/bin/env python3
"""Validate source-bound answers against supplied scientific paragraphs only."""
import argparse
import json
import sys

from common import Run, add_arguments, evidence_ok, paragraph_map, paragraphs

STATUSES = {"answered", "partial", "insufficient_evidence", "input_error"}
KINDS = {"reported", "inferred"}


def main():
    args = add_arguments(argparse.ArgumentParser(description=__doc__)).parse_args()
    try:
        run = Run(args)
    except Exception as error:
        print(json.dumps({"status": "input_error", "error": str(error)}), file=sys.stderr)
        return 2
    try:
        rows = paragraphs(run.case)
        if not rows and run.case.get("material_path"):
            material = json.loads(run.path(run.case["material_path"]).read_text(encoding="utf-8"))
            rows = paragraphs(material)
        index = paragraph_map(rows)
        questions = run.case.get("questions", [])
        try:
            candidate = run.candidate("answers.json")
            supplied = candidate.get("answers", []) if isinstance(candidate, dict) else candidate
        except Exception:
            candidate = {"answers": []}
            supplied = []
            run.uncertainty.append("no candidate answer file; no answer was invented")
        if not isinstance(supplied, list):
            raise ValueError("candidate answers must be a list")
        by_id = {}
        for item in supplied:
            identifier = str(item.get("question_id", ""))
            if not identifier or identifier in by_id:
                raise ValueError("missing or duplicate candidate question_id: " + identifier)
            by_id[identifier] = item
        answers = []
        problems = []
        sources = []
        for question in questions:
            qid = str(question.get("question_id", question.get("id", "")))
            if not qid:
                raise ValueError("question without question_id")
            item = dict(by_id.get(qid, {}))
            answer_problems = []
            if qid not in by_id:
                item = {"question_id": qid, "answer": "", "conditions": [], "status": "input_error",
                        "claims": [], "unanswered_parts": [{"part": "entire question", "reason": "candidate answer missing"}]}
                answer_problems.append("candidate answer missing")
            status = item.get("status")
            if status not in STATUSES:
                answer_problems.append("invalid status")
                status = "input_error"
            item["status"] = status
            item["question_id"] = qid
            item.setdefault("answer", "")
            item.setdefault("conditions", [])
            claims = item.get("claims", [])
            if not isinstance(claims, list):
                raise ValueError("claims must be a list for " + qid)
            for claim_index, claim in enumerate(claims, 1):
                claim.setdefault("claim_id", qid + "-c" + str(claim_index))
                if claim.get("kind") not in KINDS:
                    answer_problems.append(claim["claim_id"] + ": invalid kind")
                evidence = claim.get("evidence", [])
                if not evidence:
                    answer_problems.append(claim["claim_id"] + ": evidence missing")
                for reference in evidence:
                    if not evidence_ok(index, reference):
                        answer_problems.append(claim["claim_id"] + ": quote is not an exact substring of its paragraph")
                    else:
                        sources.append({"paper_id": run.case.get("paper_id"),
                                        "paragraph_id": reference.get("paragraph_id"),
                                        "quote": reference.get("quote")})
            if status in {"answered", "partial"} and not claims:
                answer_problems.append("answered/partial item has no claim evidence")
            if status == "answered" and item.get("unanswered_parts"):
                answer_problems.append("answered item still has unanswered_parts")
            item.setdefault("unanswered_parts", [])
            if answer_problems:
                item["status"] = "input_error"
                item["unanswered_parts"] = list(item["unanswered_parts"]) + [
                    {"part": "validation", "reason": reason} for reason in answer_problems]
                problems.extend({"question_id": qid, "reason": reason} for reason in answer_problems)
            answers.append(item)
        expected = {str(q.get("question_id", q.get("id", ""))) for q in questions}
        extras = sorted(set(by_id) - expected)
        if extras:
            problems.append({"question_id": ",".join(extras), "reason": "candidate contains questions absent from input"})
        coverage = candidate.get("coverage", {}) if isinstance(candidate, dict) else {}
        supplied_coverage = {"read_paragraph_ids": sorted(index), "input_question_count": len(questions),
                             "answered_question_count": len(answers),
                             "paper_scope": "only supplied scientific paragraphs; no PDF pixels, internet, or external knowledge"}
        supplied_coverage.update(coverage)
        status = "partial" if problems or run.uncertainty else "success"
        run.changes.append("checked one result per input question and exact paragraph quotes")
        run.finish({"case_id": run.case.get("case_id"), "paper_id": run.case.get("paper_id"),
                    "material_version": run.case.get("material_version"), "status": status,
                    "answers": answers, "coverage": supplied_coverage, "validation_problems": problems,
                    "sources": sources},
                   aliases={"responses": "answers", "result": "answers"}, code=2 if status == "partial" else 0,
                   trace_extra={"questions_read": len(questions), "exact_semantic_support": "requires Agent/human review; script checks exact quote presence only"})
    except Exception as error:
        run.uncertainty.append(str(error))
        run.finish({"paper_id": run.case.get("paper_id"), "material_version": run.case.get("material_version"),
                    "status": "input_error", "answers": [], "coverage": {}, "validation_problems": [{"reason": str(error)}]},
                   code=2)


if __name__ == "__main__":
    sys.exit(main())
