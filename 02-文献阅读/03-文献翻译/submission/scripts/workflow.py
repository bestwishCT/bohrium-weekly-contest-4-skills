#!/usr/bin/env python3
"""Prepare and audit segment-aligned scientific translation, never auto-paraphrase."""
import argparse
import json
import re
import sys
from pathlib import Path

from common import Run, add_arguments, paragraph_map, paragraphs, write_json

PROTECTED = re.compile(r"\$[^$]+\$|\[[0-9,;\-\s]+\]|(?<![\w.])[-+]?\d+(?:\.\d+)?(?:\s?(?:%|[numkKM]?[mLsVgWHzPaJ]|[kM]?mol))?")


def load_source(path):
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = paragraphs(data)
        return [{"paragraph_id": str(row.get("paragraph_id", row.get("id"))),
                 "source": row.get("text", row.get("content", "")),
                 "section": row.get("section", ""), "page": row.get("page"),
                 "block_id": row.get("block_id")} for row in rows]
    chunks = re.split(r"\n\s*\n", path.read_text(encoding="utf-8").strip())
    return [{"paragraph_id": "p%04d" % (index + 1), "source": chunk.strip(),
             "section": "", "page": None, "block_id": None}
            for index, chunk in enumerate(chunks) if chunk.strip()]


def main():
    args = add_arguments(argparse.ArgumentParser(description=__doc__)).parse_args()
    try:
        run = Run(args)
    except Exception as error:
        print(json.dumps({"status": "input_error", "error": str(error)}), file=sys.stderr)
        return 2
    try:
        source_value = run.case.get("source_path") or run.case.get("source")
        if not source_value:
            raise ValueError("source_path is required")
        source_path = run.path(source_value)
        segments = load_source(source_path)
        if not segments:
            raise ValueError("no source paragraphs recovered")
        try:
            candidate = run.candidate("translations.json")
            supplied = candidate.get("translations", candidate) if isinstance(candidate, dict) else candidate
        except Exception:
            candidate = {"translations": []}
            supplied = []
            run.uncertainty.append("translations.json is absent; segments were preserved but not machine-translated")
        translated = {str(item.get("paragraph_id")): item for item in supplied if isinstance(item, dict)}
        glossary = run.case.get("glossary", [])
        failures = []
        pairs = []
        protected_audits = []
        for segment in segments:
            identifier = segment["paragraph_id"]
            item = translated.get(identifier, {})
            target = item.get("target", "")
            source_tokens = PROTECTED.findall(segment["source"])
            target_tokens = PROTECTED.findall(target)
            token_failures = [token for token in source_tokens if token not in target_tokens]
            if not target:
                failures.append({"paragraph_id": identifier, "kind": "not_translated"})
            elif token_failures:
                failures.append({"paragraph_id": identifier, "kind": "protected_token_mismatch", "tokens": token_failures})
            terminology = []
            exceptions = set(item.get("glossary_exceptions", [])) | set(candidate.get("glossary_exceptions", []) if isinstance(candidate, dict) else [])
            for term in glossary:
                if term.get("source", "") in segment["source"]:
                    required = term.get("target", "")
                    passed = required in target or term.get("source") in exceptions
                    terminology.append({"source": term.get("source"), "target": required, "passed": passed})
                    if not passed:
                        failures.append({"paragraph_id": identifier, "kind": "glossary_mismatch", "term": term.get("source")})
            semantics = item.get("semantic_confirmation", {})
            required_checks = ["negation_checked", "modality_checked", "relations_checked"]
            semantic_ok = all(semantics.get(key) is True for key in required_checks)
            if target and not semantic_ok:
                failures.append({"paragraph_id": identifier, "kind": "semantic_checks_not_confirmed"})
            pairs.append({**segment, "target": target,
                          "translation_status": "translated" if target else "not_translated",
                          "source_location": {"paragraph_id": identifier, "page": segment.get("page"),
                                              "block_id": segment.get("block_id")}})
            protected_audits.append({"paragraph_id": identifier, "source_tokens": source_tokens,
                                     "target_tokens": target_tokens, "missing_tokens": token_failures,
                                     "terminology": terminology,
                                     "semantic_confirmation": {key: semantics.get(key) is True for key in required_checks},
                                     "semantic_note": "explicit translator confirmation is not independent semantic validation"})
        coverage = {"source_paragraphs": len(segments), "translated": sum(bool(pair["target"]) for pair in pairs),
                    "missing_or_repeated_source_ids": len(segments) - len(set(pair["paragraph_id"] for pair in pairs))}
        write_json(run.output / "bilingual.json", {"case_id": run.case_id, "pairs": pairs, "audits": protected_audits})
        status = "partial" if failures or run.uncertainty else "success"
        run.changes.append("aligned every source segment and audited numbers, units, citations, formulas, glossary and declared semantic checks")
        run.finish({"status": status, "segments": pairs, "glossary": glossary,
                    "verification": protected_audits, "failures": failures, "coverage": coverage,
                    "sources": [{"path": str(source_path), "paragraph_id": pair["paragraph_id"]} for pair in pairs]},
                   aliases={"translation": "segments", "bilingualsegments": "segments", "result": "segments"},
                   code=2 if status == "partial" else 0,
                   trace_extra={"semantic_validation": "translator declarations only; no automatic claim of correctness"})
    except Exception as error:
        run.uncertainty.append(str(error))
        run.finish({"status": "partial", "segments": [], "verification": [], "failures": [{"kind": "input_error", "reason": str(error)}]},
                   code=2)


if __name__ == "__main__":
    sys.exit(main())
