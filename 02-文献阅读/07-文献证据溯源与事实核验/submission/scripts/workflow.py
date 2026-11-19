#!/usr/bin/env python3
"""Audit compound-claim entailment cards with four verdicts and conflict retention."""
import argparse
import json
import sys

from common import Run, add_arguments, evidence_ok, paragraph_map, paragraphs, read_json

VERDICTS = {"supported", "partially_supported", "contradicted", "insufficient_evidence"}


def main():
    args = add_arguments(argparse.ArgumentParser(description=__doc__)).parse_args()
    try:
        run = Run(args)
    except Exception as error:
        print(json.dumps({"status": "input_error", "error": str(error)}), file=sys.stderr)
        return 2
    try:
        claims_path = run.path(run.case.get("claims", "claims-to-verify.json"))
        claims_data = read_json(claims_path)
        claims = claims_data.get("claims", claims_data) if isinstance(claims_data, dict) else claims_data
        material_path = run.path(run.case.get("material_path", "material.json"))
        index = paragraph_map(paragraphs(read_json(material_path)))
        candidate = run.candidate("verification-cards.json")
        supplied = candidate.get("verifications", [])
        by_id = {}
        for item in supplied:
            identifier = str(item.get("claim_id", ""))
            if not identifier or identifier in by_id:
                raise ValueError("missing or duplicate verification claim_id")
            by_id[identifier] = item
        cards = []
        failures = []
        sources = []
        for claim in claims:
            claim_id = str(claim.get("claim_id", claim.get("id", "")))
            card = dict(by_id.get(claim_id, {"claim_id": claim_id, "verdict": "insufficient_evidence",
                                             "decomposition": [], "reason": "candidate verification missing"}))
            card["claim_id"] = claim_id
            card["original_claim"] = claim.get("text", claim.get("claim"))
            parts = card.get("decomposition", [])
            local_failures = []
            if not parts:
                local_failures.append("compound claim was not decomposed/processed")
            for part in parts:
                verdict = part.get("verdict")
                if verdict not in VERDICTS:
                    local_failures.append(part.get("subclaim_id", "unknown") + ": invalid verdict")
                references = part.get("evidence", [])
                if verdict in {"supported", "partially_supported", "contradicted"} and not references:
                    local_failures.append(part.get("subclaim_id", "unknown") + ": verdict needs evidence")
                if verdict == "insufficient_evidence" and not part.get("search_scope"):
                    local_failures.append(part.get("subclaim_id", "unknown") + ": insufficient evidence needs search scope")
                if verdict == "contradicted" and not any(item.get("relation") == "contradicts" for item in references):
                    local_failures.append(part.get("subclaim_id", "unknown") + ": contradiction requires explicit opposition evidence")
                for reference in references:
                    if not evidence_ok(index, reference):
                        local_failures.append(part.get("subclaim_id", "unknown") + ": exact quote mismatch")
                    else:
                        sources.append({"claim_id": claim_id, "subclaim_id": part.get("subclaim_id"), **reference})
                for field in ("comparator", "negation", "significance", "conditions"):
                    if field not in part:
                        local_failures.append(part.get("subclaim_id", "unknown") + ": missing explicit " + field + " field")
            declared = card.get("verdict")
            if declared not in VERDICTS:
                local_failures.append("invalid overall verdict")
            elif parts and all(part.get("verdict") in VERDICTS for part in parts):
                verdicts = [part["verdict"] for part in parts]
                if all(value == "supported" for value in verdicts):
                    expected_verdict = "supported"
                elif all(value == "insufficient_evidence" for value in verdicts):
                    expected_verdict = "insufficient_evidence"
                elif any(value == "contradicted" for value in verdicts) and not card.get("source_conflicts"):
                    expected_verdict = "contradicted"
                else:
                    expected_verdict = "partially_supported"
                if declared != expected_verdict:
                    local_failures.append("overall verdict %s is inconsistent with subclaims; expected %s" % (declared, expected_verdict))
            if local_failures:
                failures.extend({"claim_id": claim_id, "reason": reason} for reason in local_failures)
                card["validation_status"] = "failed"
            else:
                card["validation_status"] = "passed"
            cards.append(card)
        expected = {str(claim.get("claim_id", claim.get("id", ""))) for claim in claims}
        extras = sorted(set(by_id) - expected)
        if extras:
            failures.append({"claim_id": ",".join(extras), "reason": "unrequested claims present"})
        status = "partial" if failures or run.uncertainty else "success"
        run.changes.append("preserved all subclaims, counter-evidence, conditions and conflict cards")
        run.finish({"status": status, "verifications": cards, "failures": failures, "sources": sources},
                   aliases={"claims": "verifications", "verificationresults": "verifications", "result": "verifications"},
                   code=2 if status == "partial" else 0,
                   trace_extra={"entailment": "Agent-assessed from complete context; exact quote presence is only a technical check"})
    except Exception as error:
        run.uncertainty.append(str(error))
        run.finish({"status": "input_error", "verifications": [], "failures": [{"reason": str(error)}]}, code=2)


if __name__ == "__main__":
    sys.exit(main())
