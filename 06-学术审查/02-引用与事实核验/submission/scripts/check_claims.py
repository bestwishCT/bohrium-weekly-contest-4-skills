#!/usr/bin/env python3
"""Validate source-bound reviews, or drive one real case and its JSON contract."""
import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sys

FACETS = ("fact_verification", "citation_support", "methods_design",
          "statistics_report", "argumentation", "scholarly_norms")
STATES = {"passed", "issues_found", "review_required", "not_checked"}
JUDGEMENTS = {"full_support", "partial_support", "unsupported", "undetermined"}
SEVERITIES = {"critical", "major", "moderate", "minor", "advisory"}
KEYS = ("metric", "population", "time", "unit", "measure_kind")
DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MISSING = object()


class AuditError(ValueError):
    pass


def text(obj, key, where):
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AuditError("%s requires %s" % (where, key))
    return value


def string_list(value, where, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        raise AuditError(where + " must be a nonempty string array" if nonempty else where + " must be a string array")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise AuditError(where + " must contain only nonempty strings")
    return value


def anchor(body, quote, where):
    count = body.count(quote)
    if count != 1:
        raise AuditError("%s quote count is %d, expected 1" % (where, count))
    offset = body.index(quote)
    return {"line": body.count("\n", 0, offset) + 1,
            "column": offset - body.rfind("\n", 0, offset)}


def location(obj, body, quote, where, key="location"):
    loc = obj.get(key)
    if not isinstance(loc, dict) or not loc:
        raise AuditError(where + " requires source location")
    anchors = [name for name in ("page", "paragraph", "line") if name in loc]
    if not anchors:
        raise AuditError(where + " location requires page, paragraph, or line")
    for name in anchors:
        if isinstance(loc[name], bool) or not isinstance(loc[name], int) or loc[name] < 1:
            raise AuditError("%s location.%s must be a positive integer" % (where, name))
    actual = anchor(body, quote, where)
    if "line" in loc and loc["line"] != actual["line"]:
        raise AuditError("%s location.line does not match the quoted text" % where)
    return actual


def canonical_doi(value, where):
    if not isinstance(value, str) or not value.strip():
        raise AuditError(where + " must be a nonempty DOI string")
    doi = value.strip()
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.IGNORECASE).lower()
    if not DOI_RE.fullmatch(doi):
        raise AuditError(where + " is not a syntactically valid DOI")
    return doi


def validate_source(sid, source):
    where = "sources." + sid
    if not isinstance(source, dict):
        raise AuditError(where + " must be an object")
    text(source, "version", where)
    text(source, "publication_status", where)
    if source.get("retraction_status") not in {"not_checked", "unknown", "clear_for_checked_scope", "correction_found", "retracted"}:
        raise AuditError(where + " has invalid retraction_status")
    if source.get("availability") not in {"full", "abstract"}:
        raise AuditError(where + " availability must be full or abstract")
    body = text(source, "text", where)
    snapshot = source.get("snapshot")
    if not isinstance(snapshot, dict):
        raise AuditError(where + " requires a snapshot object")
    digest = text(snapshot, "sha256", where + ".snapshot").lower()
    if not SHA256_RE.fullmatch(digest) or digest != hashlib.sha256(body.encode("utf-8")).hexdigest():
        raise AuditError(where + " snapshot sha256 does not match supplied text")
    text(snapshot, "media_type", where + ".snapshot")
    if "doi" in source:
        canonical_doi(source["doi"], where + ".doi")


def judge_claim(claim, manuscript, sources, index, anchors, limitations):
    where = "claims[%d]" % index
    cid = text(claim, "id", where)
    quote = text(claim, "quote", where)
    actual = location(claim, manuscript, quote, where)
    anchors.append({"claim_id": cid, **actual})
    verdict = claim.get("judgement")
    if verdict not in JUDGEMENTS:
        raise AuditError(where + " has invalid judgement")
    citation_ids = string_list(claim.get("citation_ids"), where + ".citation_ids", nonempty=True)
    if len(set(citation_ids)) != len(citation_ids):
        raise AuditError(where + " has duplicate citation IDs")
    evidence = claim.get("evidence")
    if not isinstance(evidence, list):
        raise AuditError(where + ".evidence must be an array")
    seen_sources = set()
    resolved_sources = set()
    roles = []
    for evidence_index, item in enumerate(evidence):
        item_where = "%s.evidence[%d]" % (where, evidence_index)
        if not isinstance(item, dict):
            raise AuditError(item_where + " must be an object")
        sid = text(item, "source_id", item_where)
        if sid in seen_sources:
            raise AuditError(item_where + " repeats a source; use separately registered versions")
        seen_sources.add(sid)
        if sid not in citation_ids:
            raise AuditError(item_where + " is not bound to this claim's citation_ids")
        if sid not in sources:
            if verdict != "undetermined":
                raise AuditError(item_where + " cannot be decided without its registered source")
            continue
        source = sources[sid]
        resolved_sources.add(sid)
        if "doi" in item:
            item_doi = canonical_doi(item["doi"], item_where + ".doi")
            if "doi" not in source or item_doi != canonical_doi(source["doi"], "sources." + sid + ".doi"):
                raise AuditError(item_where + " DOI does not match its registered source")
        source_quote = text(item, "quote", item_where)
        source_anchor = location(item, source["text"], source_quote, item_where)
        anchors.append({"claim_id": cid, "source_id": sid, "source_line": source_anchor["line"],
                        "source_column": source_anchor["column"]})
        role = item.get("role")
        if role not in {"supports", "contradicts", "context"}:
            raise AuditError(item_where + " role must be supports, contradicts, or context")
        roles.append(role)
        if source["availability"] == "abstract":
            limitations.append({"code": "ABSTRACT_ONLY", "message": cid + " uses only abstract evidence for " + sid})
    if len(seen_sources) > 1:
        text(claim, "source_relationship", where)
    missing = set(citation_ids) - resolved_sources
    if missing:
        limitations.append({"code": "MISSING_SOURCE", "message": cid + " lacks registered sources: " + ", ".join(sorted(missing))})
        if verdict != "undetermined":
            raise AuditError(where + " cannot be decided while cited sources are missing")
    if not evidence:
        if verdict != "undetermined":
            raise AuditError(where + " cannot be decided without evidence")
        text(claim, "reason", where)
        return
    if verdict == "undetermined":
        text(claim, "reason", where)
    elif verdict == "partial_support":
        text(claim, "uncovered_part", where)
        if "supports" not in roles:
            raise AuditError(where + " partial_support requires supporting evidence")
    elif verdict == "unsupported":
        text(claim, "contradiction", where)
        if "contradicts" not in roles:
            raise AuditError(where + " unsupported requires contradicting evidence, not absence of evidence")
    elif "supports" not in roles or "contradicts" in roles:
        raise AuditError(where + " full_support conflicts with its evidence roles")


def numeric_candidates(facts, manuscript):
    groups = {}
    for i, fact in enumerate(facts):
        where = "numeric_facts[%d]" % i
        if not isinstance(fact, dict):
            raise AuditError(where + " must be an object")
        fid = text(fact, "id", where)
        quote = text(fact, "quote", where)
        location(fact, manuscript, quote, where)
        try:
            value = Decimal(text(fact, "value", where))
        except InvalidOperation:
            raise AuditError(where + " has invalid decimal")
        if not value.is_finite():
            raise AuditError(where + " requires finite decimal")
        group = tuple(text(fact, key, where) for key in KEYS)
        groups.setdefault(group, []).append((fid, value))
    candidates = []
    for qualifiers, values in sorted(groups.items()):
        distinct = sorted(set(value for _, value in values))
        if len(distinct) > 1:
            candidates.append({"aligned_qualifiers": dict(zip(KEYS, qualifiers)),
                               "fact_ids": [fid for fid, _ in values],
                               "values": [str(value) for value in distinct],
                               "disposition": "candidate_only_check_scope_rounding_before_reporting"})
    return candidates


def validate(data):
    if not isinstance(data, dict):
        raise AuditError("root must be an object")
    manuscript = data.get("manuscript", {})
    body = text(manuscript, "text", "manuscript")
    complete = manuscript.get("complete")
    if not isinstance(complete, bool):
        raise AuditError("manuscript.complete must be boolean")
    if not isinstance(manuscript.get("requested_sections"), list) or not isinstance(manuscript.get("readable_sections"), list):
        raise AuditError("section coverage must be arrays")
    if complete and set(manuscript["requested_sections"]) != set(manuscript["readable_sections"]):
        raise AuditError("complete manuscript has unread requested sections")
    sources = data.get("sources", {})
    claims = data.get("claims", [])
    if not isinstance(sources, dict) or not isinstance(claims, list):
        raise AuditError("sources and claims have invalid types")
    for sid, source in sources.items():
        validate_source(sid, source)
    anchors = []
    limitations = []
    ids = set()
    for i, claim in enumerate(claims):
        if not isinstance(claim, dict):
            raise AuditError("claim must be an object")
        cid = text(claim, "id", "claims[%d]" % i)
        if cid in ids:
            raise AuditError("duplicate claim id")
        ids.add(cid)
        judge_claim(claim, body, sources, i, anchors, limitations)
    if not claims:
        limitations.append({"code": "EMPTY_CLAIMS", "message": "No claims were reviewed; citation coverage is partial."})
    used_sources = {item["source_id"] for claim in claims for item in claim.get("evidence", [])}
    for sid in sorted(set(sources) - used_sources):
        limitations.append({"code": "UNREFERENCED_SOURCE", "message": sid + " is registered but not used by any claim."})
    facts = data.get("numeric_facts", [])
    if not isinstance(facts, list):
        raise AuditError("numeric_facts must be an array")
    candidates = numeric_candidates(facts, body)
    review = data.get("review", {})
    coverage = review.get("coverage")
    findings = review.get("findings")
    if not isinstance(coverage, dict) or set(coverage) != set(FACETS):
        raise AuditError("coverage must exactly contain six required facets")
    if not isinstance(findings, list):
        raise AuditError("findings must be an array")
    if not findings:
        limitations.append({"code": "EMPTY_FINDINGS", "message": "No findings were supplied; this is not proof of a complete, defect-free review."})
    finding_ids = set()
    for i, finding in enumerate(findings):
        where = "findings[%d]" % i
        if not isinstance(finding, dict):
            raise AuditError(where + " must be an object")
        fid = text(finding, "id", where)
        if fid in finding_ids:
            raise AuditError("duplicate finding id")
        finding_ids.add(fid)
        facet = text(finding, "facet", where)
        if facet not in FACETS:
            raise AuditError(where + " has unknown facet")
        quote = text(finding, "quote", where)
        location(finding, body, quote, where)
        for key in ("type", "severity", "severity_reason", "basis", "recommendation", "certainty"):
            text(finding, key, where)
        if finding["severity"] not in SEVERITIES:
            raise AuditError(where + " has invalid severity")
    linked = []
    for facet, row in coverage.items():
        if not isinstance(row, dict) or row.get("status") not in STATES:
            raise AuditError(facet + " has invalid status")
        ids_for_row = row.get("finding_ids", [])
        if not isinstance(ids_for_row, list) or not set(ids_for_row).issubset(finding_ids):
            raise AuditError(facet + " has invalid finding IDs")
        if row["status"] == "issues_found" and not ids_for_row:
            raise AuditError(facet + " needs finding IDs")
        if row["status"] in {"not_checked", "review_required"}:
            text(row, "reason", facet)
        if not complete and row["status"] in {"passed", "issues_found"}:
            text(row, "scope", facet)
            if row["scope"] == "full":
                raise AuditError(facet + " cannot claim full partial-manuscript coverage")
        linked.extend(ids_for_row)
    if set(linked) != finding_ids or len(linked) != len(finding_ids):
        raise AuditError("findings must be linked exactly once")
    verdict_counts = {key: 0 for key in sorted(JUDGEMENTS)}
    for claim in claims:
        verdict_counts[claim["judgement"]] += 1
    unique_limitations = []
    seen_limitations = set()
    for issue in limitations:
        marker = (issue["code"], issue["message"])
        if marker not in seen_limitations:
            seen_limitations.add(marker)
            unique_limitations.append(issue)
    return {"validator_status": "partial" if unique_limitations else "ok",
            "fixture_id": data.get("fixture_id"),
            "coverage_scope": "full" if complete else "partial",
            "coverage_states": {facet: coverage[facet]["status"] for facet in FACETS},
            "support_counts": verdict_counts, "evidence_anchors": anchors,
            "counts": {"findings": len(finding_ids), "claims": len(claims)},
            "issues": unique_limitations,
            "numerical_consistency_candidates": candidates,
            "note": "Checks evidence structure and supplied-text consistency, not full-text scientific truth; abstract and numeric candidates require scoped human review."}


def issue(code, message):
    return {"code": code, "message": message}


def schema_type(schema):
    value = schema.get("type")
    if isinstance(value, str):
        return value
    if "properties" in schema or "required" in schema:
        return "object"
    if "items" in schema:
        return "array"
    return None


def matches_type(value, expected):
    checks = {"object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list),
              "string": lambda v: isinstance(v, str), "boolean": lambda v: isinstance(v, bool),
              "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
              "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
              "null": lambda v: v is None}
    return expected not in checks or checks[expected](value)


def validate_contract(value, schema, path="$"):
    """Validate the contract subset used by the task: nested required/type/enum/items."""
    problems = []
    expected = schema_type(schema)
    if expected and not matches_type(value, expected):
        return [issue("CONTRACT_TYPE", "%s expected %s" % (path, expected))]
    if "enum" in schema and value not in schema["enum"]:
        problems.append(issue("CONTRACT_ENUM", "%s is outside the contract enum" % path))
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                problems.append(issue("CONTRACT_REQUIRED", "%s.%s is required" % (path, name)))
        for name, child in schema.get("properties", {}).items():
            if name in value and isinstance(child, dict):
                problems.extend(validate_contract(value[name], child, path + "." + name))
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            problems.extend(validate_contract(item, schema["items"], "%s[%d]" % (path, index)))
    return problems


def desired_status(review, summary, partial=False):
    if partial or summary.get("validator_status") == "partial":
        return "partial"
    findings = review.get("review", {}).get("findings", [])
    return "issues_found" if findings else "passed"


def lookup_field(name, review, summary, materials):
    if name in review:
        return review[name]
    if name in summary:
        return summary[name]
    aliases = {"claims": review.get("claims"), "claim_reviews": review.get("claims"),
               "sources": review.get("sources"), "evidence_sources": review.get("sources"),
               "findings": review.get("review", {}).get("findings"),
               "issues": review.get("review", {}).get("findings"),
               "coverage": review.get("review", {}).get("coverage"),
               "review": review.get("review"), "manuscript": review.get("manuscript"),
               "numeric_facts": review.get("numeric_facts"),
               "materials": materials, "input_materials": materials}
    if name in aliases:
        return aliases[name]
    if name in {"limitations", "uncertainties", "validation_issues"}:
        return [row["message"] for row in summary.get("issues", [])]
    return MISSING


def project_value(name, schema, review, summary, materials, status, problems, path, required=True):
    expected = schema_type(schema)
    if name == "case_id":
        value = summary.get("case_id", MISSING)
    elif name in {"status", "result_status", "review_status"}:
        value = status
    elif name == "result" or (expected == "object" and name.isdigit()):
        value = review
    else:
        value = lookup_field(name, review, summary, materials)
    if value is MISSING:
        if required:
            problems.append(issue("UNMAPPED_CONTRACT_FIELD", path + " has no source-bound value"))
        if expected == "object":
            value = {}
        elif expected == "array":
            value = []
        elif expected in {"number", "integer"}:
            value = 0
        elif expected == "boolean":
            value = False
        elif expected == "null":
            value = None
        else:
            choices = schema.get("enum", [])
            value = choices[0] if choices else ""
    if expected == "object" and isinstance(value, dict) and schema.get("properties"):
        result = {}
        required_names = set(schema.get("required", []))
        for child_name, child_schema in schema["properties"].items():
            if isinstance(child_schema, dict):
                result[child_name] = project_value(child_name, child_schema, value, summary, materials,
                                                   status, problems, path + "." + child_name,
                                                   child_name in required_names or child_name == "case_id")
        return result
    if expected == "array" and isinstance(schema.get("items"), dict):
        if isinstance(value, dict):
            converted = []
            for key, item in value.items():
                if isinstance(item, dict):
                    item = dict(item)
                    item.setdefault("id", key)
                    item.setdefault("source_id", key)
                converted.append(item)
            value = converted
        if isinstance(value, list):
            return [project_value(str(index), schema["items"], item if isinstance(item, dict) else {},
                                  summary, materials, status, problems,
                                  "%s[%d]" % (path, index), True)
                    if schema_type(schema["items"]) == "object" and isinstance(item, dict)
                    else project_scalar(item, schema["items"])
                    for index, item in enumerate(value)]
    if expected == "string" and isinstance(value, str) and "enum" in schema and value not in schema["enum"]:
        if name in {"status", "result_status", "review_status"}:
            preferences = ("partial", "review_required", "failed") if status == "partial" else (status, "passed", "complete", "ok")
            for choice in preferences:
                if choice in schema["enum"]:
                    return choice
            problems.append(issue("NO_HONEST_STATUS_ENUM", path + " has no honest status enum value"))
            return schema["enum"][0] if schema["enum"] else value
    return project_scalar(value, schema)


def project_scalar(value, schema):
    expected = schema_type(schema)
    if expected == "array" and not isinstance(value, list):
        return []
    if expected == "object" and not isinstance(value, dict):
        return {}
    return value


def project_result(contract, case_id, review, summary, materials, partial=False):
    problems = []
    status = desired_status(review, summary, partial)
    root = dict(contract)
    root.setdefault("type", "object")
    if not isinstance(root.get("properties"), dict):
        root["properties"] = {}
    result = project_value("result", root, review, dict(summary, case_id=case_id), materials,
                           status, problems, "$", True)
    if not isinstance(result, dict):
        result = {}
    result["case_id"] = case_id
    return result, problems


def attach_diagnostics(result, contract, problems):
    """Use only a contract-declared diagnostic array; never overwrite review findings."""
    properties = contract.get("properties", {})
    for name in ("limitations", "validation_issues", "uncertainties", "issues"):
        if name not in properties or not isinstance(result.get(name), list):
            continue
        item_schema = properties[name].get("items", {}) if isinstance(properties[name], dict) else {}
        values = [row["message"] for row in problems] if schema_type(item_schema) == "string" else problems
        result[name] = list(result[name]) + [value for value in values if value not in result[name]]
        return True
    return False


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def material_references(case):
    refs = []
    container_names = {"materials", "material_files", "allowed_materials", "inputs", "files", "artifacts"}

    def visit(value, allowed=False):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "output_contract":
                    continue
                visit(child, allowed or key in container_names)
        elif isinstance(value, list) and allowed:
            for child in value:
                visit(child, True)
        elif isinstance(value, str) and allowed and not re.match(r"^[a-z]+://", value, re.IGNORECASE):
            suffixes = {".json", ".md", ".txt", ".pdf", ".csv", ".tsv", ".xml", ".html",
                        ".docx", ".xlsx", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
            looks_like_path = (value.startswith(("/", "./", "../")) or Path(value).suffix.lower() in suffixes)
            if "\n" not in value and looks_like_path:
                refs.append(value)
    visit(case)
    return list(dict.fromkeys(refs))


def read_materials(case, case_path, task_path, explicit):
    materials = [{"path": str(task_path), "sha256": hashlib.sha256(task_path.read_bytes()).hexdigest(),
                  "bytes": task_path.stat().st_size}]
    problems = []
    roots = (case_path.parent.resolve(), task_path.parent.resolve())
    for raw in material_references(case) + [str(path) for path in explicit]:
        path = Path(raw)
        if not path.is_absolute():
            path = case_path.parent / path
        path = path.resolve()
        if raw not in [str(item) for item in explicit] and not any(path == root or root in path.parents for root in roots):
            problems.append(issue("MATERIAL_NOT_ALLOWED", str(path) + " is outside the case input directories"))
            continue
        try:
            content = path.read_bytes()
            materials.append({"path": str(path), "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)})
        except OSError as exc:
            problems.append(issue("MATERIAL_READ_ERROR", str(path) + ": " + str(exc)))
    return materials, problems


def run_candidate(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        result = validate(data)
    except json.JSONDecodeError as exc:
        print(json.dumps({"validator_status": "input_error", "error": {"code": "INVALID_JSON", "message": "line %d column %d" % (exc.lineno, exc.colno)}}), file=sys.stderr)
        return 2
    except (OSError, AuditError, KeyError, TypeError, AttributeError) as exc:
        code = "INPUT_READ_ERROR" if isinstance(exc, OSError) else "INVALID_LEDGER"
        print(json.dumps({"validator_status": "input_error", "error": {"code": code, "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if result["validator_status"] == "partial" else 0


def run_case(args):
    output = args.output
    case = {}
    try:
        case = json.loads(args.case.read_text(encoding="utf-8"))
        if not isinstance(case, dict):
            raise AuditError("case root must be an object")
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise AuditError("case requires nonempty case_id")
        contract = case.get("output_contract")
        if not isinstance(contract, dict):
            raise AuditError("case requires object output_contract")
        task_text = args.task.read_text(encoding="utf-8")
        if not task_text.strip():
            raise AuditError("task.md must not be empty")
    except (OSError, json.JSONDecodeError, AuditError) as exc:
        result = {"case_id": case.get("case_id") if isinstance(case, dict) else None,
                  "status": "partial", "issues": [issue("CASE_INPUT_ERROR", str(exc))]}
        try:
            write_json(output, result)
        except OSError as write_exc:
            print(json.dumps({"status": "partial", "error": str(write_exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    materials, material_problems = read_materials(case, args.case, args.task, args.material)
    early_summary = {"validator_status": "partial", "issues": [issue("REVIEW_NOT_VALIDATED", "Review validation has not completed.")]}
    early, projection_problems = project_result(contract, case_id, {}, early_summary, materials, partial=True)
    early_problems = early_summary["issues"] + material_problems + projection_problems
    attach_diagnostics(early, contract, early_problems)
    try:
        write_json(output, early)
        review = json.loads(args.review.read_text(encoding="utf-8"))
        summary = validate(review)
    except (OSError, json.JSONDecodeError, AuditError, KeyError, TypeError, AttributeError) as exc:
        partial_summary = {"validator_status": "partial", "issues": [issue("REVIEW_VALIDATION_ERROR", str(exc))]}
        result, problems = project_result(contract, case_id, {}, partial_summary, materials, partial=True)
        problems = partial_summary["issues"] + material_problems + problems
        if not attach_diagnostics(result, contract, problems):
            print(json.dumps({"validator_status": "partial", "issues": problems}, ensure_ascii=False), file=sys.stderr)
        write_json(output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    all_problems = material_problems + summary.get("issues", [])
    result, projection_problems = project_result(contract, case_id, review, summary, materials,
                                                 partial=bool(all_problems))
    contract_problems = validate_contract(result, contract)
    all_problems += projection_problems + contract_problems
    if all_problems:
        result, more_problems = project_result(contract, case_id, review, summary, materials, partial=True)
        all_problems += more_problems
        if not attach_diagnostics(result, contract, all_problems):
            print(json.dumps({"validator_status": "partial", "issues": all_problems}, ensure_ascii=False), file=sys.stderr)
        write_json(output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 2 if projection_problems or contract_problems or material_problems else 1
    write_json(output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", nargs="?", type=Path, help="legacy candidate-ledger path")
    parser.add_argument("--candidate", type=Path, help="validate a synthetic candidate ledger only")
    parser.add_argument("--case", type=Path, default=Path("/input/case.json"))
    parser.add_argument("--task", type=Path, default=Path("/input/task.md"))
    parser.add_argument("--review", type=Path, default=Path("/app/work/claim_review.json"))
    parser.add_argument("--output", type=Path, default=Path("/app/output/result.json"))
    parser.add_argument("--material", type=Path, action="append", default=[], help="explicitly allowed local material")
    args = parser.parse_args()
    if args.candidate and args.ledger:
        parser.error("use either --candidate or the legacy ledger path, not both")
    candidate = args.candidate or args.ledger
    return run_candidate(candidate) if candidate else run_case(args)


if __name__ == "__main__":
    sys.exit(main())
