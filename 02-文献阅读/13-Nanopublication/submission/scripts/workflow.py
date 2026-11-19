#!/usr/bin/env python3
"""Evidence-bound atomic assertion bundles with safe local N-Triples export."""
import argparse
import json
import re
import sys

from common import Run, add_arguments, evidence_ok, paragraph_map, paragraphs, read_json, stable_id, write_json

RELATIONS = {"uses", "supports", "contradicts", "compares_with", "improves_over", "reports"}
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"


def iri(value):
    return "<" + str(value).replace(">", "%3E") + ">"


def literal(value):
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    return '"' + text + '"'


def parse_ntriples(path):
    pattern = re.compile(r'^\s*<([^>]+)>\s+<([^>]+)>\s+(?:<([^>]+)>|"((?:\\.|[^"\\])*)")\s*\.\s*$')
    triples = []
    with open(path, "r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            match = pattern.match(line.rstrip("\n"))
            if not match:
                raise ValueError("N-Triples parse failure on line %d" % number)
            subject, predicate, object_iri, object_literal = match.groups()
            if object_literal is not None:
                object_literal = object_literal.replace('\\"', '"').replace("\\n", "\n").replace("\\r", "\r").replace("\\\\", "\\")
            triples.append((subject, predicate, object_iri if object_iri is not None else object_literal))
    return triples


def main():
    args = add_arguments(argparse.ArgumentParser(description=__doc__)).parse_args()
    try:
        run = Run(args)
    except Exception as error:
        print(json.dumps({"status": "input_error", "error": str(error)}), file=sys.stderr)
        return 2
    try:
        material_path = run.path(run.case.get("material_path", "material.json"))
        index = paragraph_map(paragraphs(read_json(material_path)))
        candidate = run.candidate("assertions.json")
        supplied = candidate.get("assertions", [])
        bundles = []
        failures = []
        valid_ids = []
        sources = [{"paper_id": run.case.get("paper_id"), "path": str(material_path)}]
        for position, assertion in enumerate(supplied, 1):
            item = dict(assertion)
            assertion_id = item.get("assertion_id") or stable_id("urn:weekly-skill:assertion:", run.case_id,
                                                               item.get("subject"), item.get("predicate"), item.get("object"), position)
            item["assertion_id"] = assertion_id
            local_failures = []
            if not item.get("atomicity_confirmed"):
                local_failures.append("atomic single-verifiable-claim check missing")
            if item.get("predicate") not in RELATIONS:
                local_failures.append("predicate is absent from controlled relation vocabulary")
            if item.get("evidence_kind") not in {"direct", "indirect"}:
                local_failures.append("evidence_kind must be direct or indirect")
            references = item.get("evidence", [])
            if not references:
                local_failures.append("no evidence; a definite nanopublication assertion is not emitted")
            for reference in references:
                if not evidence_ok(index, reference):
                    local_failures.append("evidence paragraph/quote mismatch")
            if not item.get("confidence_basis"):
                local_failures.append("confidence rationale missing")
                item["confidence"] = None
            for key in ("subject", "object", "conditions", "predicate_original_text"):
                if item.get(key) in (None, ""):
                    local_failures.append("missing scoped assertion component: " + key)
            item["source_metadata"] = {
                "paper_id": run.case.get("paper_id", "not_reported"),
                "authors": run.case.get("authors", "not_reported"),
                "year": run.case.get("year", "not_reported"),
                "version": run.case.get("material_version", "not_reported"),
                "identifier_policy": "only supplied metadata and deterministic local URNs; no invented DOI or external URI"
            }
            item["validation_failures"] = local_failures
            item["status"] = "insufficient_evidence" if local_failures else "evidence_bound"
            bundles.append(item)
            if not local_failures:
                valid_ids.append(assertion_id)
            failures.extend({"assertion_id": assertion_id, "reason": reason} for reason in local_failures)
        triples = []
        for item in bundles:
            if item["status"] != "evidence_bound":
                continue
            assertion = item["assertion_id"]
            subject = stable_id("urn:weekly-skill:entity:", run.case_id, "subject", item["subject"])
            object_id = stable_id("urn:weekly-skill:entity:", run.case_id, "object", item["object"])
            relation = "urn:weekly-skill:relation:" + item["predicate"]
            evidence_id = stable_id("urn:weekly-skill:evidence:", assertion)
            statement = "%s %s %s" % (item["subject"], item["predicate"], item["object"])
            triples.extend([
                (assertion, RDF_TYPE, "urn:weekly-skill:EvidenceBoundAssertion"),
                (assertion, RDFS_LABEL, statement),
                (assertion, "urn:weekly-skill:hasSubject", subject),
                (assertion, relation, object_id),
                (assertion, "urn:weekly-skill:hasEvidence", evidence_id),
                (assertion, "urn:weekly-skill:conditions", str(item["conditions"])),
                (assertion, "urn:weekly-skill:evidenceKind", item["evidence_kind"]),
                (assertion, "urn:weekly-skill:confidenceBasis", item["confidence_basis"]),
                (evidence_id, "urn:weekly-skill:sourcePaper", str(item["source_metadata"]["paper_id"])),
            ])
            for reference in item["evidence"]:
                triples.append((evidence_id, "urn:weekly-skill:paragraph", str(reference["paragraph_id"])))
                triples.append((evidence_id, "urn:weekly-skill:quote", reference["quote"]))
        nt_path = run.output / "assertions.nt"
        with open(nt_path, "w", encoding="utf-8", newline="\n") as handle:
            for subject, predicate, obj in triples:
                object_text = iri(obj) if predicate in {RDF_TYPE, "urn:weekly-skill:hasSubject",
                                                        "urn:weekly-skill:hasEvidence"} or predicate.startswith("urn:weekly-skill:relation:") else literal(obj)
                handle.write("%s %s %s .\n" % (iri(subject), iri(predicate), object_text))
        readback = parse_ntriples(nt_path)
        if len(readback) != len(triples):
            raise ValueError("N-Triples readback count mismatch")
        write_json(run.output / "nanopublications.json", {"nanopublications": bundles})
        status = "partial" if failures or run.uncertainty or not bundles else "success"
        run.changes.append("exported only evidence-bound assertions and parsed every N-Triples line on readback")
        run.finish({"status": status, "nanopublications": bundles, "validation_failures": failures,
                    "relation_vocabulary": sorted(RELATIONS),
                    "export": {"path": str(nt_path), "format": "N-Triples", "triples": len(readback),
                               "scope": "local evidence bundles, not a published TrustyURI nanopublication"},
                    "sources": sources},
                   aliases={"assertions": "nanopublications", "nanopubs": "nanopublications", "result": "nanopublications"},
                   code=2 if status == "partial" else 0,
                   trace_extra={"ntriples_parser_readback": len(readback), "external publishing": False})
    except Exception as error:
        run.uncertainty.append(str(error))
        run.finish({"status": "partial", "nanopublications": [], "validation_failures": [str(error)]}, code=2)


if __name__ == "__main__":
    sys.exit(main())
