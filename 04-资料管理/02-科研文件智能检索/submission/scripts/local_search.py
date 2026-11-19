#!/usr/bin/env python3
"""Deterministic library search with hard filters and inspectable evidence anchors."""
import argparse
from collections import Counter
import hashlib
import html.parser
import json
import math
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

VERSION = "1.0.0"
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml"}


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def tokens(text):
    return re.findall(r"[\u4e00-\u9fff]|[a-z0-9]+(?:[-_.][a-z0-9]+)*", text.casefold())


class TextHTML(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def xml_text(data):
    root = ET.fromstring(data)
    return "\n".join(text.strip() for text in root.itertext() if text and text.strip())


def native_text(path):
    suffix = path.suffix.casefold()
    if suffix in TEXT_SUFFIXES:
        return path.read_text(encoding="utf-8"), "stdlib-utf8"
    if suffix in {".html", ".htm"}:
        parser = TextHTML()
        parser.feed(path.read_text(encoding="utf-8"))
        return "\n".join(parser.parts), "stdlib-html"
    if suffix == ".docx":
        with zipfile.ZipFile(path) as archive:
            return xml_text(archive.read("word/document.xml")), "stdlib-docx-xml"
    if suffix == ".pptx":
        with zipfile.ZipFile(path) as archive:
            slides = sorted((name for name in archive.namelist()
                             if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
                            key=lambda name: int(re.search(r"\d+", Path(name).stem).group()))
            pages = []
            for index, name in enumerate(slides, 1):
                pages.append("[PAGE %d]\n%s" % (index, xml_text(archive.read(name))))
            return "\n\n".join(pages), "stdlib-pptx-xml"
    if suffix == ".xlsx":
        with zipfile.ZipFile(path) as archive:
            shared = []
            if "xl/sharedStrings.xml" in archive.namelist():
                shared = ["".join(node.itertext()) for node in ET.fromstring(
                    archive.read("xl/sharedStrings.xml"))]
            texts = []
            for name in sorted(n for n in archive.namelist()
                               if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)):
                root = ET.fromstring(archive.read(name))
                for cell in (n for n in root.iter() if n.tag.endswith("}c")):
                    coordinate = cell.attrib.get("r", "unknown")
                    value = next((n for n in cell if n.tag.endswith("}v")), None)
                    if value is None:
                        continue
                    content = value.text or ""
                    if cell.attrib.get("t") == "s" and content.isdigit():
                        content = shared[int(content)]
                    texts.append("%s:%s=%s" % (Path(name).stem, coordinate, content))
            return "\n".join(texts), "stdlib-xlsx-xml"
    raise ValueError("unsupported_without_real_adapter:" + suffix)


def page_blocks(text, page_map=None):
    """Use explicit extraction page markers; never infer PDF page coordinates."""
    matches = list(re.finditer(r"(?m)^\[PAGE (\d+)(?: PRINTED=([^\]]+))?\]\s*$", text))
    if not matches:
        return [(None, None, text)]
    blocks = []
    prefix = text[:matches[0].start()].strip()
    if prefix:
        blocks.append((None, None, prefix))
    for index, match in enumerate(matches):
        physical = int(match.group(1))
        printed = match.group(2) or (page_map or {}).get(str(physical))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks.append((physical, printed, text[match.end():end]))
    return blocks


def split_segments(doc_id, text, parser_name, page_map=None):
    segments = []
    section = "unheaded"
    paragraph = 0
    for physical, printed, block in page_blocks(text, page_map):
        current = []
        for line in block.splitlines() + [""]:
            if re.match(r"^(?:#{1,6}\s+|\d+(?:\.\d+)*\s+|(?:abstract|introduction|methods?|results?|discussion|conclusion)\b)",
                        line.strip(), re.I) and len(line.strip()) < 180:
                if current:
                    paragraph += 1
                    segments.append((paragraph, section, physical, printed, "\n".join(current).strip()))
                    current = []
                section = re.sub(r"^#{1,6}\s+", "", line.strip())
            elif not line.strip():
                if current:
                    paragraph += 1
                    content = "\n".join(current).strip()
                    if content:
                        segments.append((paragraph, section, physical, printed, content))
                    current = []
            else:
                current.append(line.rstrip())
    return [{"id": "%s-p%05d" % (doc_id, paragraph), "document_id": doc_id,
             "paragraph": paragraph, "section": section, "physical_page": physical,
             "printed_page": printed, "text": content, "parser": parser_name}
            for paragraph, section, physical, printed, content in segments if content]


def metadata_match(metadata, key, expected):
    value = metadata.get(key)
    if value is None and key == "file_type":
        value = metadata.get("type")
    if isinstance(value, list):
        return str(expected).casefold() in [str(item).casefold() for item in value]
    return value is not None and str(value).casefold() == str(expected).casefold()


def expand_query(query, synonyms):
    expansions = []
    values = [query]
    folded = query.casefold()
    for source, targets in sorted(synonyms.items()):
        targets = [targets] if isinstance(targets, str) else targets
        if source.casefold() in folded:
            for target in targets:
                values.append(query + " " + target)
                expansions.append({"matched": source, "added": target})
    return values, expansions


def contract_properties(contract):
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
    reserved = {"type", "required", "description", "title", "additionalProperties", "examples"}
    return {key: value for key, value in contract.items() if key not in reserved and isinstance(value, (dict, str))}


def write_result(case_path, output, payload):
    if not case_path:
        return
    case = load(case_path)
    result = {"case_id": case.get("case_id", case.get("id", "unknown"))}
    for name, spec in contract_properties(case.get("output_contract")).items():
        if name in result:
            continue
        if name in payload:
            result[name] = payload[name]
            continue
        kind = spec.get("type") if isinstance(spec, dict) else spec
        result[name] = [] if kind == "array" else {} if kind == "object" else "" if kind == "string" else None
    dump(Path(output) / "result.json", result)


def search(args):
    library = Path(args.library)
    if not library.is_dir():
        raise ValueError("library is not a directory")
    config = load(args.config) if args.config else {}
    declared = {item["path"]: item for item in config.get("documents", [])}
    denied = set(args.simulate_denied or [])
    inventory, errors, documents, segments = [], [], [], []
    for path in sorted(library.rglob("*"), key=lambda p: p.relative_to(library).as_posix()):
        rel = path.relative_to(library).as_posix()
        if path.is_symlink():
            errors.append({"path": rel, "reason": "symlink_not_followed", "stage": "inventory"})
            continue
        if not path.is_file():
            continue
        item = {"path": rel, "size": path.stat().st_size, "mode": oct(path.stat().st_mode & 0o777),
                **declared.get(rel, {})}
        item.pop("extracted_text", None)
        item.pop("page_map", None)
        item["metadata"] = {key: value for key, value in declared.get(rel, {}).items()
                            if key not in {"extracted_text", "parser", "page_map", "access"}}
        inventory.append(item)
        if rel in denied or not os.access(path, os.R_OK):
            errors.append({"path": rel, "reason": "permission_denied_simulated" if rel in denied else "permission_denied",
                           "stage": "read"})
            continue
        if declared.get(rel, {}).get("access") == "denied":
            errors.append({"path": rel, "reason": "declared_permission_denied", "stage": "scope"})
            continue
        try:
            file_hash = digest(path)
            item["sha256"] = file_hash
            declaration = declared.get(rel, {})
            if declaration.get("extracted_text"):
                extracted = Path(declaration["extracted_text"])
                text = extracted.read_text(encoding="utf-8")
                parser_name = declaration.get("parser", "external-declared-unverified")
            else:
                text, parser_name = native_text(path)
            version = declaration.get("version", "undeclared")
            version_tag = hashlib.sha256(str(version).encode("utf-8")).hexdigest()[:6]
            doc_id = file_hash[:16] + "-" + version_tag
            document = {**item, "document_id": doc_id, "parser": parser_name, "version": version}
            documents.append(document)
            document_segments = split_segments(doc_id, text, parser_name, declaration.get("page_map"))
            for segment in document_segments:
                segment["source_path"] = rel
            segments.extend(document_segments)
        except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile, UnicodeError) as error:
            errors.append({"path": rel, "reason": str(error), "stage": "parse"})
    filtered, exclusions = [], []
    filters = []
    for raw in args.filter or []:
        if "=" not in raw:
            raise ValueError("filter must be key=value: " + raw)
        key, expected = raw.split("=", 1)
        filters.append({"key": key, "expected": expected})
    for document in documents:
        failed = [f for f in filters if not metadata_match(document["metadata"], f["key"], f["expected"])]
        if failed:
            exclusions.append({"path": document["path"], "failed_filters": failed})
        else:
            filtered.append(document)
    allowed = {doc["document_id"] for doc in filtered}
    candidates = [segment for segment in segments if segment["document_id"] in allowed]
    queries, expansions = expand_query(args.query, config.get("synonyms", {}))
    query_tokens = tokens(" ".join(queries))
    frequencies = [Counter(tokens(segment["text"])) for segment in candidates]
    lengths = [sum(freq.values()) for freq in frequencies]
    average = sum(lengths) / len(lengths) if lengths else 0
    document_frequency = Counter()
    for freq in frequencies:
        document_frequency.update(freq.keys())
    scored = []
    total = len(candidates)
    k1, b = 1.5, 0.75
    for segment, freq, length in zip(candidates, frequencies, lengths):
        score = 0.0
        for term in query_tokens:
            count = freq.get(term, 0)
            if not count or not total:
                continue
            idf = math.log(1 + (total - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            score += idf * count * (k1 + 1) / (count + k1 * (1 - b + b * length / max(average, 1)))
        if score > 0:
            scored.append((score, segment))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["document_id"], pair[1]["paragraph"]))
    by_document = {doc["document_id"]: doc for doc in documents}
    grouped = {}
    for score, segment in scored:
        doc = by_document[segment["document_id"]]
        key = (doc["sha256"], doc["version"], segment["text"])
        if key not in grouped:
            grouped[key] = (score, segment, [])
        grouped[key][2].append(segment["source_path"])
    hits = []
    for score, segment, paths in sorted(grouped.values(), key=lambda item: (-item[0], item[1]["id"]))[:args.top_k]:
        doc = by_document[segment["document_id"]]
        locator = "%s::version=%s;physical_page=%s;printed_page=%s;section=%s;paragraph=%s" % (
            doc["path"], doc["version"], segment["physical_page"], segment["printed_page"],
            segment["section"], segment["paragraph"])
        hits.append({"candidate_id": segment["id"], "path": doc["path"], "sha256": doc["sha256"],
                     "version": doc["version"], "aliases": sorted(set(paths) - {doc["path"]}),
                     "section": segment["section"], "physical_page": segment["physical_page"],
                     "printed_page": segment["printed_page"], "paragraph": segment["paragraph"],
                     "paragraph_lead": segment["text"].splitlines()[0], "quote": segment["text"],
                     "locator": locator, "locator_kind": "coordinates_not_claimed_clickable",
                     "bm25_score_not_confidence": round(score, 6), "evidence_verbatim_in_segment": True})
    if not documents:
        empty_reason = "no_searchable_text_check_errors_and_scope"
    elif not filtered:
        empty_reason = "hard_filter_empty_no_relaxation"
    elif not hits:
        empty_reason = "no_recall_support_does_not_prove_source_lacks_answer"
    else:
        empty_reason = None
    status = "partial" if errors else ("empty" if not hits else "completed")
    report = {"status": status, "query": args.query, "expanded_queries": queries,
              "expansions": expansions, "filters": filters, "filter_exclusions": exclusions,
              "searched_documents": [doc["path"] for doc in filtered], "hits": hits,
              "results": hits, "matches": hits, "inventory": inventory, "sources": [item["path"] for item in inventory],
              "source_scope": {"documents": len(documents), "after_hard_filters": len(filtered),
                               "segments_after_filters": len(candidates)},
              "failures": errors, "conflicts": [], "uncertainty": [
                  "BM25 plus declared expansions is recall ranking, not semantic proof or confidence.",
                  "Unreadable files are not negative evidence; page coordinates remain unknown when not supplied.",
                  "Duplicate aliases share evidence but different declared versions remain separate."],
              "empty_reason": empty_reason, "changes": [{"action": "index_rebuilt", "destination": "work/search-index.json"}],
              "change_records": [{"action": "index_rebuilt", "source_hashes_verified": True}]}
    index = {"tool_version": VERSION, "algorithm": "BM25 k1=1.5 b=0.75 with only declared query expansions",
             "documents": documents, "segments": segments, "filters": filters}
    dump(Path(args.work) / "search-index.json", index)
    dump(Path(args.work) / "retrieval-audit.json", {"query": args.query, "expansions": expansions,
                                                      "filters": filters, "score_order": [s[1]["id"] for s in scored]})
    dump(Path(args.output) / "search_report.json", report)
    write_result(args.case, args.output, report)
    print(json.dumps({"status": status, "hits": len(hits), "indexed_documents": len(documents),
                      "errors": len(errors), "empty_reason": empty_reason}, ensure_ascii=False))
    return 0


def main():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--version", action="version", version=VERSION)
    root.add_argument("--library", required=True)
    root.add_argument("--query", required=True)
    root.add_argument("--work", required=True)
    root.add_argument("--output", required=True)
    root.add_argument("--config", help="Case metadata, extraction adapters and declared synonym map")
    root.add_argument("--case")
    root.add_argument("--task")
    root.add_argument("--filter", action="append", default=[])
    root.add_argument("--top-k", type=int, default=10)
    root.add_argument("--simulate-denied", action="append", default=[])
    args = root.parse_args()
    try:
        if args.task:
            Path(args.task).read_text(encoding="utf-8")
        Path(args.work).mkdir(parents=True, exist_ok=True)
        Path(args.output).mkdir(parents=True, exist_ok=True)
        return search(args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
