#!/usr/bin/env python3
"""Annotation-anchor verification, bounded notes, explicit links and filtered retrieval."""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import sys

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


def pages(text):
    markers = list(re.finditer(r"(?m)^\[PAGE (\d+)(?: PRINTED=([^\]]+))?\]\s*$", text))
    if not markers:
        return [{"physical": None, "printed": None, "text": text}]
    output = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        output.append({"physical": int(marker.group(1)), "printed": marker.group(2),
                       "text": text[marker.end():end]})
    return output


def paragraph_at(text, offset):
    before = text[:offset]
    return len(re.split(r"\n\s*\n", before))


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
    reserved = {"type", "required", "title", "description", "additionalProperties", "examples"}
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
        else:
            kind = spec.get("type") if isinstance(spec, dict) else spec
            result[name] = [] if kind == "array" else {} if kind == "object" else "" if kind == "string" else None
    dump(Path(output) / "result.json", result)


def doc_text(path, declaration):
    if declaration.get("extracted_text"):
        extracted = Path(declaration["extracted_text"])
        return extracted.read_text(encoding="utf-8"), declaration.get("parser", "external-declared-unverified"), sha256(extracted)
    if path.suffix.casefold() in {".txt", ".md", ".html", ".xml", ".json"}:
        return path.read_text(encoding="utf-8"), "stdlib-utf8", None
    raise ValueError("no_real_annotation_or_text_adapter_for:" + path.suffix)


def raw_annotations(path, declaration):
    if declaration.get("annotation_export"):
        export = Path(declaration["annotation_export"])
        value = load(export)
        rows = value.get("annotations", value) if isinstance(value, dict) else value
        if not isinstance(rows, list):
            raise ValueError("annotation export must contain a list")
        return rows, sha256(export), declaration.get("parser", "external-declared-unverified")
    return declaration.get("annotations", []), None, declaration.get("parser", "inline-exported-annotations")


def verify_anchor(raw, page_blocks):
    quote = raw.get("quote")
    if not isinstance(quote, str) or not quote:
        return {"status": "broken", "reason": "missing_verbatim_quote"}
    physical = raw.get("page")
    candidates = [block for block in page_blocks if physical is not None and block["physical"] == physical]
    if physical is None:
        candidates = page_blocks
    if not candidates:
        return {"status": "broken", "reason": "physical_page_not_verified", "physical_page": physical}
    occurrences = []
    for block in candidates:
        text = block["text"]
        start = 0
        while True:
            offset = text.find(quote, start)
            if offset < 0:
                break
            occurrences.append((block, offset))
            start = offset + max(1, len(quote))
    span = raw.get("char_span")
    if isinstance(span, list) and len(span) == 2:
        for block in candidates:
            begin, end = span
            if isinstance(begin, int) and isinstance(end, int) and block["text"][begin:end] == quote:
                occurrences = [(block, begin)]
                break
    if not occurrences:
        return {"status": "broken", "reason": "quote_absent_on_declared_page", "physical_page": physical}
    if len(occurrences) > 1:
        return {"status": "ambiguous", "reason": "repeated_quote_requires_verified_char_span",
                "physical_page": physical, "occurrences": len(occurrences)}
    block, offset = occurrences[0]
    before, after = raw.get("before"), raw.get("after")
    context_ok = True
    context_reason = []
    if before is not None and not block["text"][:offset].endswith(before):
        context_ok = False
        context_reason.append("before_context_mismatch")
    if after is not None and not block["text"][offset + len(quote):].startswith(after):
        context_ok = False
        context_reason.append("after_context_mismatch")
    if not context_ok:
        return {"status": "context_mismatch", "reason": context_reason,
                "physical_page": block["physical"], "char_start": offset}
    return {"status": "verified_span" if span else "exact", "physical_page": block["physical"],
            "printed_page": raw.get("printed_page") or block["printed"], "char_start": offset,
            "char_end": offset + len(quote), "paragraph": paragraph_at(block["text"], offset),
            "quote_sha256": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
            "context_verified": before is not None and after is not None,
            "coordinate_status": "raw_rectangle_retained_not_geometrically_reverified" if raw.get("rect") else "unavailable"}


def canonical_documents(documents):
    groups = {}
    for document in documents:
        groups.setdefault((document["sha256"], document.get("version", "")), []).append(document["path"])
    result = {}
    for _versioned_hash, paths in groups.items():
        canonical = sorted(paths)[0]
        for path in paths:
            result[path] = {"canonical": canonical, "aliases": sorted(set(paths) - {canonical})}
    return result


def annotation_key(document, raw):
    return hashlib.sha256((document["sha256"] + "\0" + document["version"] + "\0" + str(raw.get("id"))).encode()).hexdigest()[:20]


def build(args):
    library = Path(args.library)
    if not library.is_dir():
        raise ValueError("library is not a directory")
    manifest = load(args.manifest)
    declarations = {item["path"]: item for item in manifest.get("documents", [])}
    denied = set(args.simulate_denied or [])
    inventory, documents, failures = [], [], []
    for path in sorted(library.rglob("*"), key=lambda p: p.relative_to(library).as_posix()):
        rel = path.relative_to(library).as_posix()
        if path.is_symlink():
            failures.append({"path": rel, "reason": "symlink_not_followed", "stage": "inventory"})
            continue
        if not path.is_file():
            continue
        inventory.append({"path": rel, "size": path.stat().st_size, "mode": oct(path.stat().st_mode & 0o777)})
        if rel in denied or not os.access(path, os.R_OK):
            failures.append({"path": rel, "reason": "permission_denied_simulated" if rel in denied else "permission_denied", "stage": "read"})
            continue
        if rel not in declarations:
            failures.append({"path": rel, "reason": "document_not_declared_annotation_layer_unchecked", "stage": "declaration"})
            continue
        declaration = declarations[rel]
        try:
            text, text_parser, extracted_hash = doc_text(path, declaration)
            raw, export_hash, annotation_parser = raw_annotations(path, declaration)
            documents.append({"path": rel, "sha256": sha256(path), "version": str(declaration.get("version", "undeclared")),
                              "declaration": declaration, "page_blocks": pages(text), "raw_annotations": raw,
                              "text_parser": text_parser, "annotation_parser": annotation_parser,
                              "extracted_text_sha256": extracted_hash, "annotation_export_sha256": export_hash})
        except (OSError, ValueError, KeyError, UnicodeError, json.JSONDecodeError) as error:
            failures.append({"path": rel, "reason": str(error), "stage": "annotation_or_text_parse"})
    duplicate_map = canonical_documents(documents)
    annotations, notes, unresolved = [], [], []
    if not documents:
        unresolved.append({"status": "no_readable_declared_documents", "note_created": False})
    annotation_lookup = {}
    seen_ids = set()
    for document in documents:
        path, declaration = document["path"], document["declaration"]
        canonical = duplicate_map[path]["canonical"]
        if path != canonical:
            continue
        if not document["raw_annotations"] and declaration.get("annotation_layer_status") != "checked_none":
            unresolved.append({"path": path, "status": "annotation_layer_not_checked_not_empty"})
        for sequence, raw in enumerate(document["raw_annotations"], 1):
            raw_id = str(raw.get("id", "annotation-%d" % sequence))
            unique_key = (path, raw_id)
            if unique_key in seen_ids:
                unresolved.append({"path": path, "annotation_id": raw_id, "status": "duplicate_annotation_id_rejected"})
                continue
            seen_ids.add(unique_key)
            anchor = verify_anchor(raw, document["page_blocks"])
            key = annotation_key(document, raw)
            annotation = {"annotation_id": key, "raw_id": raw_id, "path": path,
                          "aliases": duplicate_map[path]["aliases"], "document_sha256": document["sha256"],
                          "version": document["version"], "kind": raw.get("kind", raw.get("type", "unknown")),
                          "comment": raw.get("comment"), "quote": raw.get("quote"), "color": raw.get("color"),
                          "rect": raw.get("rect"), "tags": raw.get("tags", []), "label": raw.get("label"),
                          "created": raw.get("created"), "page": raw.get("page"), "printed_page": raw.get("printed_page"),
                          "raw": raw, "anchor": anchor, "annotation_parser": document["annotation_parser"],
                          "text_parser": document["text_parser"], "annotation_export_sha256": document["annotation_export_sha256"]}
            annotations.append(annotation)
            annotation_lookup[unique_key] = annotation
            note = None
            if anchor["status"] in {"exact", "verified_span"}:
                locator = "%s::version=%s;physical=%s;printed=%s;paragraph=%s;chars=%s-%s;quote_sha256=%s" % (
                    path, document["version"], anchor["physical_page"], anchor["printed_page"], anchor["paragraph"],
                    anchor["char_start"], anchor["char_end"], anchor["quote_sha256"])
                note = {"note_id": "note-" + key, "annotation_id": key, "raw_annotation_id": raw_id,
                        "summary": raw.get("comment"), "note_type": raw.get("note_type", "untyped"),
                        "source_quote": raw["quote"], "annotation_kind": annotation["kind"],
                        "color": raw.get("color"), "rect": raw.get("rect"), "created": raw.get("created"),
                        "topics": raw.get("topics", []), "tags": raw.get("tags", []), "entities": raw.get("entities", []),
                        "methods": raw.get("methods", []), "project": raw.get("project"),
                        "source": {"path": path, "aliases": duplicate_map[path]["aliases"],
                                   "sha256": document["sha256"], "version": document["version"], "locator": locator},
                        "evidence_status": "verbatim_source_anchor_verified",
                        "uncertainty": ["A comment represents the annotator, not necessarily the document author.",
                                        "No derived paraphrase exists until a reviewed edit is supplied."],
                        "derived_from_raw": False}
                notes.append(note)
                annotation_lookup[unique_key]["note_id"] = note["note_id"]
            else:
                unresolved.append({"path": path, "annotation_id": raw_id, "status": anchor["status"],
                                   "reason": anchor.get("reason"), "note_created": False})
    notes_before = json.loads(json.dumps(notes))
    edits_by_id = {item["note_id"]: item for item in load(args.edits).get("notes", [])} if args.edits else {}
    edit_changes = []
    for note in notes:
        edit = edits_by_id.get(note["note_id"])
        if not edit:
            continue
        if any(field in edit for field in ("source", "source_quote", "annotation_id", "raw_annotation_id")):
            failures.append({"note_id": note["note_id"], "stage": "manual_edit", "reason": "immutable_anchor_field_in_edit"})
            continue
        evidence_quote = edit.get("evidence_quote")
        if edit.get("summary") is not None and (not isinstance(evidence_quote, str) or evidence_quote not in note["source_quote"]):
            failures.append({"note_id": note["note_id"], "stage": "manual_edit", "reason": "summary_lacks_verbatim_source_evidence_quote"})
            continue
        before = json.loads(json.dumps(note))
        for field in ("summary", "note_type", "topics", "tags", "entities", "methods"):
            if field in edit:
                note[field] = edit[field]
        note["derived_from_raw"] = True
        note["derived_evidence_quote"] = evidence_quote
        note["uncertainty"].append("Bounded paraphrase requires agent semantic review; evidence quote was mechanically verified only.")
        edit_changes.append({"note_id": note["note_id"], "changed_fields": sorted(set(edit) - {"note_id", "evidence_quote"}),
                             "before": before, "after": json.loads(json.dumps(note))})
    before_text = json.dumps(notes_before, ensure_ascii=False, indent=2, sort_keys=True).splitlines(True)
    after_text = json.dumps(notes, ensure_ascii=False, indent=2, sort_keys=True).splitlines(True)
    Path(args.work, "manual-edits.diff").write_text("".join(difflib.unified_diff(
        before_text, after_text, fromfile="notes-before-derived-edits", tofile="notes-after-derived-edits")), encoding="utf-8")
    basis_fields = {"shared_method": "methods", "shared_entity": "entities", "shared_tag": "tags",
                    "same_question": "topics", "shared_project": "project"}
    links = []
    for relation in manifest.get("relations", []):
        left = annotation_lookup.get((relation.get("from_path"), str(relation.get("from"))))
        right = annotation_lookup.get((relation.get("to_path"), str(relation.get("to"))))
        if not left or not right or not left.get("note_id") or not right.get("note_id"):
            links.append({**relation, "status": "not_created", "reason": "missing_or_unanchored_note"})
            continue
        basis, value = relation.get("basis_type"), relation.get("value")
        field = basis_fields.get(basis)
        left_value, right_value = left["raw"].get(field), right["raw"].get(field) if field else None
        if field in {"methods", "entities", "tags", "topics"}:
            valid = isinstance(left_value, list) and isinstance(right_value, list) and value in left_value and value in right_value
        else:
            valid = bool(field) and value is not None and left_value == value and right_value == value
        if not valid or not relation.get("evidence"):
            links.append({**relation, "status": "not_created", "reason": "explicit_shared_attribute_or_evidence_missing"})
            continue
        links.append({"from_note_id": left["note_id"], "to_note_id": right["note_id"],
                      "basis_type": basis, "value": value, "status": relation.get("status", "candidate"),
                      "evidence": relation["evidence"], "evidence_quotes": [left["quote"], right["quote"]]})
    version_maps = []
    for mapping in manifest.get("version_maps", []):
        left = annotation_lookup.get((mapping.get("from_path"), str(mapping.get("from"))))
        right = annotation_lookup.get((mapping.get("to_path"), str(mapping.get("to"))))
        if (not left or not right or not left.get("note_id") or not right.get("note_id")
                or not left.get("anchor") or not right.get("anchor")
                or left["quote"] != right["quote"] or not mapping.get("evidence")):
            version_maps.append({**mapping, "status": "unresolved_no_forced_cross_version_anchor"})
            continue
        version_maps.append({"from_annotation_id": left["annotation_id"], "to_annotation_id": right["annotation_id"],
                             "from_version": left["version"], "to_version": right["version"],
                             "quote": left["quote"], "status": "mapped_" + mapping.get("status", "candidate"),
                             "evidence": mapping["evidence"],
                             "position_change": {"from": left["anchor"], "to": right["anchor"]}})
    duplicate_groups = {}
    for document in documents:
        duplicate_groups.setdefault(document["sha256"], set()).update(set(duplicate_map[document["path"]]["aliases"]) | {duplicate_map[document["path"]]["canonical"]})
    duplicate_records = [{"sha256": key, "paths": sorted(paths), "independent_support_count": 1}
                         for key, paths in duplicate_groups.items() if len(paths) > 1]
    public_documents = [{key: value for key, value in document.items() if key not in {"declaration", "page_blocks", "raw_annotations"}}
                        for document in documents]
    status = "partial" if failures or unresolved or any(link["status"] == "not_created" for link in links) else "completed"
    notebook = {"status": status, "documents": public_documents, "annotations": annotations, "notes": notes,
                "links": links, "version_maps": version_maps, "unresolved": unresolved,
                "duplicates": duplicate_records, "sources": [item["path"] for item in inventory],
                "failures": failures,
                "uncertainty": ["Raw color and label semantics are preserved but never invented.",
                                "Only exact or verified-span quotes become searchable notes.",
                                "Text equality plus administrative evidence does not prove publisher provenance.",
                                "Explicit shared attributes support links; keyword-only similarity is not used."],
                "changes": [{"action": "derived_notebook_rebuilt", "source_files_modified": False},
                            {"action": "reviewed_derived_edits_applied", "count": len(edit_changes)}],
                "change_records": edit_changes,
                "tool_version": VERSION, "tool_sha256": sha256(__file__)}
    dump(Path(args.work) / "notebook-index.json", {"notes": notes, "filter_dimensions": [
        "topic", "tag", "entity", "method", "source", "created_after", "created_before"]})
    dump(Path(args.output) / "notebook.json", notebook)
    write_result(args.case, args.output, notebook)
    print(json.dumps({"status": status, "annotations": len(annotations), "notes": len(notes),
                      "links": sum(link["status"] != "not_created" for link in links),
                      "unresolved": len(unresolved), "failures": len(failures)}, ensure_ascii=False))
    return 2 if failures else 0


def query(args):
    notebook = load(args.notebook)
    filters = {}
    for key in ("topic", "tag", "entity", "method", "source", "created_after", "created_before"):
        value = getattr(args, key)
        if value is not None:
            filters[key] = value
    hits = []
    for note in notebook.get("notes", []):
        matched = True
        for key, expected in filters.items():
            if key == "topic":
                actual = note["topics"]
            elif key == "tag":
                actual = note["tags"]
            elif key == "entity":
                actual = note["entities"]
            elif key == "method":
                actual = note["methods"]
            elif key == "source":
                actual = note["source"]["path"]
            elif key == "created_after":
                actual = note.get("created")
                matched = actual is not None and actual >= expected
                if not matched:
                    break
                continue
            else:
                actual = note.get("created")
                matched = actual is not None and actual <= expected
                if not matched:
                    break
                continue
            values = actual if isinstance(actual, list) else [actual]
            if not any(str(value).casefold() == str(expected).casefold() for value in values):
                matched = False
                break
        if matched:
            hits.append(note)
    exact_case = {key: str(value).casefold() for key, value in filters.items()}
    text = args.text.casefold() if args.text else None
    if text:
        hits = [note for note in hits if text in (note["source_quote"] + "\n" + str(note.get("summary") or "")).casefold()]
    report = {"status": "completed" if hits else "empty", "filters": filters,
              "casefold_query_controls": exact_case, "text_filter": args.text,
              "hits": hits, "notes": hits,
              "uncertainty": ["Filters are conjunctive and never relaxed; exact controlled fields are not semantic matches.",
                              "No result does not imply an unreadable or unresolved annotation is irrelevant."],
              "sources": sorted({note["source"]["path"] for note in hits}),
              "changes": [], "change_records": []}
    dump(Path(args.output) / "notebook_query.json", report)
    write_result(args.case, args.output, report)
    print(json.dumps({"status": report["status"], "hits": len(hits), "filters": filters}, ensure_ascii=False))
    return 0


def main():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--version", action="version", version=VERSION)
    commands = root.add_subparsers(dest="command", required=True)
    build_cmd = commands.add_parser("build")
    build_cmd.add_argument("--library", required=True)
    build_cmd.add_argument("--manifest", required=True)
    build_cmd.add_argument("--work", required=True)
    build_cmd.add_argument("--output", required=True)
    build_cmd.add_argument("--edits")
    build_cmd.add_argument("--case")
    build_cmd.add_argument("--task")
    build_cmd.add_argument("--simulate-denied", action="append", default=[])
    query_cmd = commands.add_parser("query")
    query_cmd.add_argument("--notebook", required=True)
    query_cmd.add_argument("--output", required=True)
    for name in ("topic", "tag", "entity", "method", "source", "created_after", "created_before", "text"):
        query_cmd.add_argument("--" + name.replace("_", "-"))
    query_cmd.add_argument("--case")
    args = root.parse_args()
    try:
        if getattr(args, "task", None):
            Path(args.task).read_text(encoding="utf-8")
        if hasattr(args, "work"):
            Path(args.work).mkdir(parents=True, exist_ok=True)
        Path(args.output).mkdir(parents=True, exist_ok=True)
        return build(args) if args.command == "build" else query(args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
