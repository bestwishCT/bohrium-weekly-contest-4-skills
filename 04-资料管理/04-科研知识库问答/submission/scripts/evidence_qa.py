#!/usr/bin/env python3
"""Two-stage knowledge-base QA: retrieve candidates, then audit atomic claims."""
import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

VERSION = "1.0.0"


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def words(text):
    return re.findall(r"[\u4e00-\u9fff]|[a-z0-9]+(?:[.-][a-z0-9]+)*", text.casefold())


def read_native(path):
    if path.suffix.casefold() in {".txt", ".md", ".json", ".csv", ".xml", ".html"}:
        return path.read_text(encoding="utf-8"), "stdlib-text"
    if path.suffix.casefold() == ".docx":
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
            return "\n".join(item.strip() for item in root.itertext() if item.strip()), "stdlib-docx-xml"
    raise ValueError("parser_unavailable_for:" + path.suffix)


def parse_pages(text):
    matches = list(re.finditer(r"(?m)^\[PAGE (\d+)(?: PRINTED=([^\]]+))?\]\s*$", text))
    if not matches:
        return [(None, None, text)]
    blocks = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks.append((int(match.group(1)), match.group(2), text[match.end():end]))
    return blocks


def paragraphs(text):
    output = []
    number = 0
    section = "unheaded"
    for physical, printed, block in parse_pages(text):
        pending = []
        for line in block.splitlines() + [""]:
            if re.match(r"^(?:#{1,6}\s+|\d+(?:\.\d+)*\s+|(?:abstract|introduction|methods?|results?|discussion|conclusion)\b)",
                        line.strip(), re.I) and len(line.strip()) < 180:
                if pending:
                    number += 1
                    output.append((number, section, physical, printed, "\n".join(pending).strip()))
                    pending = []
                section = re.sub(r"^#+\s*", "", line.strip())
            elif not line.strip():
                if pending:
                    number += 1
                    content = "\n".join(pending).strip()
                    if content:
                        output.append((number, section, physical, printed, content))
                    pending = []
            else:
                pending.append(line.rstrip())
    return output


def expanded_query(query, synonyms):
    variants = [query]
    expansions = []
    for source, targets in sorted(synonyms.items()):
        if source.casefold() in query.casefold():
            for target in ([targets] if isinstance(targets, str) else targets):
                variants.append(query + " " + target)
                expansions.append({"source": source, "added": target})
    return variants, expansions


def properties(contract):
    if not isinstance(contract, dict):
        return {}
    if isinstance(contract.get("properties"), dict):
        return contract["properties"]
    fields = contract.get("fields")
    if isinstance(fields, list):
        return {field if isinstance(field, str) else field.get("name"): field if isinstance(field, dict) else {}
                for field in fields if isinstance(field, str) or isinstance(field, dict) and field.get("name")}
    if isinstance(fields, dict):
        return fields
    reserved = {"type", "required", "title", "description", "additionalProperties", "examples"}
    return {key: value for key, value in contract.items() if key not in reserved and isinstance(value, (dict, str))}


def write_result(case_path, output, payload):
    if not case_path:
        return
    case = load(case_path)
    result = {"case_id": case.get("case_id", case.get("id", "unknown"))}
    for name, spec in properties(case.get("output_contract")).items():
        if name in result:
            continue
        if name in payload:
            result[name] = payload[name]
        else:
            kind = spec.get("type") if isinstance(spec, dict) else spec
            result[name] = [] if kind == "array" else {} if kind == "object" else "" if kind == "string" else None
    dump(Path(output) / "result.json", result)


def retrieve(args):
    library = Path(args.library)
    if not library.is_dir():
        raise ValueError("library is not a directory")
    config = load(args.config) if args.config else {}
    declared = {item["path"]: item for item in config.get("documents", [])}
    denied = set(args.simulate_denied or [])
    documents, segments, failures, inventory = [], [], [], []
    for path in sorted(library.rglob("*"), key=lambda p: p.relative_to(library).as_posix()):
        rel = path.relative_to(library).as_posix()
        if path.is_symlink():
            failures.append({"path": rel, "stage": "inventory", "reason": "symlink_not_followed"})
            continue
        if not path.is_file():
            continue
        inventory.append({"path": rel, "size": path.stat().st_size, "mode": oct(path.stat().st_mode & 0o777)})
        if rel in denied or not os.access(path, os.R_OK) or declared.get(rel, {}).get("access") == "denied":
            failures.append({"path": rel, "stage": "read", "reason": "permission_denied_simulated" if rel in denied else "permission_denied"})
            continue
        try:
            file_hash = sha256(path)
            declaration = declared.get(rel, {})
            if declaration.get("extracted_text"):
                text = Path(declaration["extracted_text"]).read_text(encoding="utf-8")
                parser_name = declaration.get("parser", "declared-external-unverified")
            else:
                text, parser_name = read_native(path)
            version = str(declaration.get("version", "undeclared"))
            document_id = file_hash[:16] + "-" + hashlib.sha256(version.encode()).hexdigest()[:6]
            documents.append({"document_id": document_id, "path": rel, "sha256": file_hash,
                              "version": version, "metadata": declaration.get("metadata", {}), "parser": parser_name})
            for number, section, physical, printed, content in paragraphs(text):
                segments.append({"candidate_id": "%s-p%05d" % (document_id, number), "document_id": document_id,
                                 "source_path": rel, "paragraph": number, "section": section,
                                 "physical_page": physical, "printed_page": printed, "text": content})
        except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile, UnicodeError) as error:
            failures.append({"path": rel, "stage": "parse", "reason": str(error)})
    filters = []
    for raw in args.filter or []:
        if "=" not in raw:
            raise ValueError("invalid filter: " + raw)
        key, expected = raw.split("=", 1)
        filters.append({"key": key, "expected": expected})
    allowed_documents = []
    exclusions = []
    for document in documents:
        mismatched = []
        for condition in filters:
            value = document["version"] if condition["key"] == "version" else document["metadata"].get(condition["key"])
            if value is None or (not any(str(v).casefold() == condition["expected"].casefold() for v in value)
                                 if isinstance(value, list) else str(value).casefold() != condition["expected"].casefold()):
                mismatched.append(condition)
        if mismatched:
            exclusions.append({"path": document["path"], "failed": mismatched})
        else:
            allowed_documents.append(document)
    allowed = {document["document_id"] for document in allowed_documents}
    candidates = [segment for segment in segments if segment["document_id"] in allowed]
    variants, expansions = expanded_query(args.question, config.get("synonyms", {}))
    query_terms = words(" ".join(variants))
    frequencies = [Counter(words(segment["text"])) for segment in candidates]
    lengths = [sum(item.values()) for item in frequencies]
    average = sum(lengths) / len(lengths) if lengths else 0
    df = Counter()
    for frequency in frequencies:
        df.update(frequency.keys())
    scored = []
    for segment, frequency, length in zip(candidates, frequencies, lengths):
        score = 0.0
        for term in query_terms:
            count = frequency.get(term, 0)
            if count:
                idf = math.log(1 + (len(candidates) - df[term] + 0.5) / (df[term] + 0.5))
                score += idf * count * 2.5 / (count + 1.5 * (0.25 + 0.75 * length / max(average, 1)))
        if score > 0:
            scored.append((score, segment))
    scored.sort(key=lambda item: (-item[0], item[1]["candidate_id"], item[1]["source_path"]))
    grouped = {}
    by_document = {document["document_id"]: document for document in documents}
    for score, segment in scored:
        document = by_document[segment["document_id"]]
        key = (document["sha256"], document["version"], segment["text"])
        if key not in grouped:
            grouped[key] = {"score": score, "segment": segment, "paths": []}
        grouped[key]["paths"].append(segment["source_path"])
    output_candidates = []
    for group in sorted(grouped.values(), key=lambda g: (-g["score"], g["segment"]["candidate_id"]))[:args.top_k]:
        segment, document = group["segment"], by_document[group["segment"]["document_id"]]
        output_candidates.append({**{key: value for key, value in segment.items() if key not in {"text", "source_path"}},
            "path": document["path"], "sha256": document["sha256"], "version": document["version"],
            "aliases": sorted(set(group["paths"]) - {document["path"]}),
            "quote": segment["text"], "locator": "%s::version=%s;physical_page=%s;printed_page=%s;section=%s;paragraph=%s" % (
                document["path"], document["version"], segment["physical_page"], segment["printed_page"], segment["section"], segment["paragraph"]),
            "locator_kind": "coordinates_not_claimed_clickable", "bm25_score_not_confidence": round(group["score"], 6),
            "support_status": "candidate_unjudged"})
    independent_versions = len({(item["sha256"], item["version"]) for item in output_candidates})
    if not documents:
        status, reason = "insufficient", "no_readable_documents_or_empty_library"
    elif not output_candidates:
        status, reason = "insufficient", "no_candidates_current_kb_does_not_provide_sufficient_basis"
    else:
        status, reason = "awaiting_claim_review", None
    preliminary_answer = "当前知识库没有足够依据。" if status == "insufficient" else "尚需逐论断审核候选证据，不能将召回直接当作回答。"
    packet = {"tool_version": VERSION, "question": args.question, "query_variants": variants,
              "expansions": expansions, "filters": filters, "filter_exclusions": exclusions,
              "documents": documents, "searched_paths": [d["path"] for d in allowed_documents],
              "candidates": output_candidates, "status": status, "independent_version_count": independent_versions,
              "failures": failures, "inventory": inventory, "insufficient_reason": reason,
              "uncertainty": ["Candidates are retrieval results, not validated support.",
                              "Unreadable files and excluded versions cannot support absence claims.",
                              "Identical content aliases are not independent evidence; different versions persist."]}
    dump(Path(args.work) / "evidence-index.json", {"documents": documents, "segments": segments,
                                                     "filters": filters, "algorithm": "BM25"})
    dump(Path(args.work) / "evidence_packet.json", packet)
    payload = {"status": status, "answer": preliminary_answer, "direct_answer": preliminary_answer,
               "evidence": output_candidates, "sources": [item["path"] for item in inventory],
               "failures": failures, "uncertainty": packet["uncertainty"],
               "claims": [], "changes": [{"action": "candidate_packet_created", "library_modified": False}],
               "change_records": [{"action": "candidate_packet_created"}]}
    dump(Path(args.output) / "retrieval_report.json", packet)
    write_result(args.case, args.output, payload)
    print(json.dumps({"status": status, "candidates": len(output_candidates), "errors": len(failures),
                      "independent_versions": independent_versions}, ensure_ascii=False))
    return 0


def answer(args):
    packet = load(args.packet)
    claims_input = load(args.claims)
    candidates = {item["candidate_id"]: item for item in packet.get("candidates", [])}
    audits, rendered, validation_errors = [], [], []
    for index, claim in enumerate(claims_input.get("claims", []), 1):
        claim_id = str(claim.get("id", "claim-%d" % index))
        text = str(claim.get("text") or claim.get("topic") or "未命名问题")
        evidence, errors, relations = [], [], set()
        for support in claim.get("support", []):
            candidate = candidates.get(support.get("candidate_id"))
            relation = support.get("relation")
            quote = support.get("quote")
            if not candidate:
                errors.append("unknown_candidate_id:" + str(support.get("candidate_id")))
                continue
            if relation not in {"direct", "partial", "contradicts"}:
                errors.append("invalid_relation:" + str(relation))
                continue
            if not isinstance(quote, str) or quote not in candidate["quote"]:
                errors.append("quote_not_verbatim_for:" + candidate["candidate_id"])
                continue
            relations.add(relation)
            evidence.append({"candidate_id": candidate["candidate_id"], "relation": relation,
                             "path": candidate["path"], "sha256": candidate["sha256"], "version": candidate["version"],
                             "section": candidate["section"], "physical_page": candidate["physical_page"],
                             "printed_page": candidate["printed_page"], "paragraph": candidate["paragraph"],
                             "quote": quote, "locator": candidate["locator"]})
        if errors:
            status = "insufficient"
            validation_errors.extend({"claim_id": claim_id, "reason": reason} for reason in errors)
        elif "direct" in relations and "contradicts" in relations or "contradicts" in relations:
            status = "conflict"
        elif "direct" in relations:
            status = "supported"
        elif "partial" in relations:
            status = "partial"
        else:
            status = "insufficient"
        if status == "supported":
            answer_text = text
        elif status == "partial" and claim.get("qualified_text"):
            answer_text = "部分依据：" + str(claim["qualified_text"])
        elif status == "conflict":
            answer_text = "证据冲突，无法形成统一结论：" + text
        else:
            answer_text = "当前知识库没有足够依据，不能回答：" + str(claim.get("topic") or text)
        audits.append({"claim_id": claim_id, "claim": text, "status": status, "evidence": evidence,
                       "validation_errors": errors,
                       "verification": "quote_substring_and_candidate_coordinates_verified; semantic_alignment_requires_agent_review"})
        rendered.append(answer_text)
    statuses = {audit["status"] for audit in audits}
    if validation_errors:
        overall = "validation_failed"
    elif not audits or statuses == {"insufficient"}:
        overall = "insufficient"
    elif "conflict" in statuses:
        overall = "conflict"
    elif "partial" in statuses or "insufficient" in statuses:
        overall = "partial"
    else:
        overall = "completed"
    if not rendered:
        rendered = ["当前知识库没有足够依据。"]
    answer_text = "\n".join(rendered)
    output = {"status": overall, "answer": answer_text, "direct_answer": answer_text,
              "claims": audits, "claim_audits": audits, "evidence": [e for audit in audits for e in audit["evidence"]],
              "sources": packet.get("searched_paths", []), "failures": packet.get("failures", []),
              "conflicts": [audit for audit in audits if audit["status"] == "conflict"],
              "validation_errors": validation_errors,
              "uncertainty": packet.get("uncertainty", []) + [
                  "A verbatim quote check does not replace semantic review of the claim-evidence relation.",
                  "Partial, conflict, and unsupported claims are not promoted to a unified factual answer."],
              "changes": [{"action": "claims_and_quotes_audited", "library_modified": False,
                           "input_claims_sha256": sha256(args.claims)}],
              "change_records": [{"action": "claims_and_quotes_audited", "replay_packet": str(args.packet)}]}
    dump(Path(args.output) / "answer.json", output)
    markdown = ["# 知识库问答", "", answer_text, "", "## 证据审计"]
    for audit in audits:
        markdown.append("- %s: %s" % (audit["claim_id"], audit["status"]))
        for evidence in audit["evidence"]:
            markdown.append("  - %s | %s | %s" % (evidence["relation"], evidence["locator"], evidence["quote"]))
        for error in audit["validation_errors"]:
            markdown.append("  - validation: " + error)
    Path(args.output, "answer.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
    write_result(args.case, args.output, output)
    print(json.dumps({"status": overall, "claims": len(audits), "validation_errors": len(validation_errors)}, ensure_ascii=False))
    return 2 if validation_errors else 0


def main():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--version", action="version", version=VERSION)
    commands = root.add_subparsers(dest="command", required=True)
    retrieve_cmd = commands.add_parser("retrieve")
    retrieve_cmd.add_argument("--library", required=True)
    retrieve_cmd.add_argument("--question", required=True)
    retrieve_cmd.add_argument("--config")
    retrieve_cmd.add_argument("--work", required=True)
    retrieve_cmd.add_argument("--output", required=True)
    retrieve_cmd.add_argument("--case")
    retrieve_cmd.add_argument("--task")
    retrieve_cmd.add_argument("--filter", action="append", default=[])
    retrieve_cmd.add_argument("--top-k", type=int, default=12)
    retrieve_cmd.add_argument("--simulate-denied", action="append", default=[])
    answer_cmd = commands.add_parser("answer")
    answer_cmd.add_argument("--packet", required=True)
    answer_cmd.add_argument("--claims", required=True)
    answer_cmd.add_argument("--output", required=True)
    answer_cmd.add_argument("--case")
    args = root.parse_args()
    try:
        if getattr(args, "task", None):
            Path(args.task).read_text(encoding="utf-8")
        if hasattr(args, "work"):
            Path(args.work).mkdir(parents=True, exist_ok=True)
        Path(args.output).mkdir(parents=True, exist_ok=True)
        return retrieve(args) if args.command == "retrieve" else answer(args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
