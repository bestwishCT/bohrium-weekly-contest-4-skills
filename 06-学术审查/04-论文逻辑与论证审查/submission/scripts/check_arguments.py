#!/usr/bin/env python3
"""Validate argument maps or validate a candidate against a real case contract."""
import argparse
import json
from pathlib import Path
import sys

AXES = ("claim_evidence", "reasoning_chain", "cross_section_delivery", "figure_text_scope")
RELATED = ("fact_verification", "citation_support", "methods_design",
           "statistics_report", "argumentation", "scholarly_norms")
STATES = {"passed", "issues_found", "review_required", "not_checked"}
SEVERITIES = {"critical", "major", "moderate", "minor", "advisory"}
NODE_TYPES = {"claim", "evidence", "premise", "counterexample", "alternative_explanation", "scope"}
EDGE_RELATIONS = {"supports", "premise_for", "refutes", "alternative_to", "scoped_by"}
CHECK_STATES = {"checked_none", "checked_present", "unresolved", "not_applicable"}


class AuditError(ValueError):
    pass


class ContractError(ValueError):
    pass


def text(obj, key, where):
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AuditError("%s requires %s" % (where, key))
    return value


def anchor(body, quote, where):
    count = body.count(quote)
    if count != 1:
        raise AuditError("%s quote count is %d, expected 1" % (where, count))
    offset = body.index(quote)
    return {"line": body.count("\n", 0, offset) + 1,
            "column": offset - body.rfind("\n", 0, offset)}


def locate(obj, where):
    if not isinstance(obj.get("location"), dict) or not obj["location"]:
        raise AuditError(where + " requires location")


def string_list(value, where):
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise AuditError(where + " must be an array of nonempty strings")
    return value


def matrix(value, required, complete, finding_ids, prefix):
    if not isinstance(value, dict) or set(value) != set(required):
        raise AuditError(prefix + " has incorrect facets")
    linked = []
    for facet, row in value.items():
        where = prefix + "." + facet
        if not isinstance(row, dict) or row.get("status") not in STATES:
            raise AuditError(where + " has invalid status")
        ids = row.get("finding_ids", [])
        string_list(ids, where + ".finding_ids")
        if not set(ids).issubset(finding_ids) or len(ids) != len(set(ids)):
            raise AuditError(where + " has invalid or duplicate finding IDs")
        if row["status"] == "issues_found" and not ids:
            raise AuditError(where + " needs a finding")
        if row["status"] != "issues_found" and ids:
            raise AuditError(where + " can link findings only in issues_found state")
        if row["status"] in {"not_checked", "review_required"}:
            text(row, "reason", where)
        if not complete and row["status"] in {"passed", "issues_found"}:
            text(row, "scope", where)
            if row["scope"] == "full":
                raise AuditError(where + " cannot claim full truncated-text scope")
        if not complete and prefix == "coverage" and facet == "cross_section_delivery" and row["status"] == "passed":
            raise AuditError(where + " cannot pass cross-section review with truncated text")
        linked.extend(ids)
    return linked


def validate_graph(data, body, claim_rows, evidence):
    graph = data.get("argument_graph")
    if not isinstance(graph, dict):
        raise AuditError("argument_graph must be an object")
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise AuditError("argument_graph nodes and edges must be arrays")
    claims = {row["id"]: row for row in claim_rows}
    node_rows = {}
    claim_nodes = set()
    all_ids = set()
    for i, node in enumerate(nodes):
        where = "argument_graph.nodes[%d]" % i
        if not isinstance(node, dict):
            raise AuditError(where + " must be an object")
        nid = text(node, "id", where)
        if nid in all_ids:
            raise AuditError(where + " has duplicate node/edge ID")
        all_ids.add(nid)
        node_rows[nid] = node
        kind = node.get("node_type")
        if kind not in NODE_TYPES:
            raise AuditError(where + " has invalid node_type")
        if kind in {"claim", "evidence"}:
            ref = text(node, "ref_id", where)
            expected = claims if kind == "claim" else evidence
            if ref not in expected:
                raise AuditError(where + " references an absent " + kind)
            node["ref_id"] = ref
            if kind == "claim":
                if ref in claim_nodes:
                    raise AuditError(where + " duplicates a claim node")
                claim_nodes.add(ref)
                text(node, "scope", where)
        elif kind in {"premise", "counterexample", "alternative_explanation", "scope"}:
            text(node, "statement", where)
            if kind == "premise":
                locate(node, where)
                anchor(body, text(node, "quote", where), where)
            elif node.get("quote"):
                locate(node, where)
                anchor(body, node["quote"], where)
    if claim_nodes != set(claims):
        raise AuditError("every claim must have exactly one claim node")
    edge_rows = {}
    incoming_premises = set()
    for i, edge in enumerate(edges):
        where = "argument_graph.edges[%d]" % i
        if not isinstance(edge, dict):
            raise AuditError(where + " must be an object")
        eid = text(edge, "id", where)
        if eid in all_ids:
            raise AuditError(where + " has duplicate node/edge ID")
        all_ids.add(eid)
        edge_rows[eid] = edge
        source = text(edge, "from", where)
        target = text(edge, "to", where)
        if source not in node_rows or target not in node_rows:
            raise AuditError(where + " has an absent endpoint")
        relation = edge.get("relation")
        if relation not in EDGE_RELATIONS:
            raise AuditError(where + " has invalid relation")
        source_kind = node_rows[source]["node_type"]
        target_kind = node_rows[target]["node_type"]
        valid = ((relation == "supports" and source_kind in {"evidence", "premise"} and target_kind == "claim") or
                 (relation == "premise_for" and source_kind in {"evidence", "premise"} and target_kind == "claim") or
                 (relation == "refutes" and source_kind == "counterexample" and target_kind in {"claim", "premise"}) or
                 (relation == "alternative_to" and source_kind == "alternative_explanation" and target_kind in {"claim", "premise"}) or
                 (relation == "scoped_by" and source_kind == "scope" and target_kind in {"claim", "premise"}))
        if not valid:
            raise AuditError(where + " has invalid endpoint types for " + relation)
        if relation == "premise_for":
            incoming_premises.add(node_rows[target]["ref_id"])
    for cid, claim in claims.items():
        if claim.get("speaker") == "author" and claim.get("strength") != "hypothetical" and cid not in incoming_premises:
            raise AuditError("claim %s has no incoming premise_for edge" % cid)
    reviews = graph.get("claim_checks")
    if not isinstance(reviews, list):
        raise AuditError("argument_graph.claim_checks must be an array")
    checked = set()
    for i, review in enumerate(reviews):
        where = "argument_graph.claim_checks[%d]" % i
        if not isinstance(review, dict):
            raise AuditError(where + " must be an object")
        cid = text(review, "claim_id", where)
        if cid not in claims or cid in checked:
            raise AuditError(where + " has an absent or duplicate claim_id")
        checked.add(cid)
        text(review, "rationale", where)
        for prefix, kind in (("counterexample", "counterexample"),
                             ("alternative_explanation", "alternative_explanation")):
            status = review.get(prefix + "_status")
            if status not in CHECK_STATES:
                raise AuditError(where + " has invalid " + prefix + "_status")
            refs = review.get(prefix + "_node_ids", [])
            string_list(refs, where + "." + prefix + "_node_ids")
            if any(ref not in node_rows or node_rows[ref]["node_type"] != kind for ref in refs):
                raise AuditError(where + " has invalid " + prefix + " node IDs")
            if status == "checked_present" and not refs:
                raise AuditError(where + " checked_present requires " + prefix + " nodes")
    required_checks = {cid for cid, row in claims.items()
                       if row.get("speaker") == "author" and row.get("strength") == "causal"}
    if not required_checks.issubset(checked):
        raise AuditError("causal claims require counterexample and alternative_explanation checks")
    return {"nodes": len(nodes), "edges": len(edges), "claim_checks": len(reviews)}


def validate(data):
    if not isinstance(data, dict):
        raise AuditError("root must be an object")
    manuscript = data.get("manuscript", {})
    body = text(manuscript, "text", "manuscript")
    complete = manuscript.get("complete")
    if not isinstance(complete, bool):
        raise AuditError("manuscript.complete must be boolean")
    if not isinstance(manuscript.get("requested_sections"), list) or not isinstance(manuscript.get("readable_sections"), list):
        raise AuditError("section coverage arrays required")
    if complete and set(manuscript["requested_sections"]) != set(manuscript["readable_sections"]):
        raise AuditError("complete manuscript has unread requested sections")
    ids = set()
    evidence_rows = data.get("evidence", [])
    if not isinstance(evidence_rows, list):
        raise AuditError("evidence must be an array")
    evidence = {}
    for i, row in enumerate(evidence_rows):
        where = "evidence[%d]" % i
        eid = text(row, "id", where)
        if eid in evidence:
            raise AuditError("duplicate evidence ID")
        evidence[eid] = row
        locate(row, where)
        availability = row.get("availability", "available")
        if availability not in {"available", "missing"}:
            raise AuditError(where + " has invalid availability")
        if availability == "available":
            anchor(body, text(row, "quote", where), where)
            if row.get("design_capacity") not in {"associational", "causal", "descriptive"}:
                raise AuditError(where + " has invalid design_capacity")
    claims = data.get("claims", [])
    if not isinstance(claims, list):
        raise AuditError("claims must be an array")
    inference = []
    unavailable = []
    anchors = []
    for i, claim in enumerate(claims):
        where = "claims[%d]" % i
        cid = text(claim, "id", where)
        if cid in ids:
            raise AuditError("duplicate claim ID")
        ids.add(cid)
        quote = text(claim, "quote", where)
        locate(claim, where)
        anchors.append({"claim_id": cid, **anchor(body, quote, where)})
        if claim.get("speaker") not in {"author", "reported", "hypothesis"}:
            raise AuditError(where + " has invalid speaker")
        if claim.get("strength") not in {"causal", "associational", "bounded_descriptive", "hypothetical"}:
            raise AuditError(where + " has invalid strength")
        refs = claim.get("evidence_ids", [])
        string_list(refs, where + ".evidence_ids")
        if not set(refs).issubset(evidence):
            raise AuditError(where + " references absent evidence")
        if any(evidence[ref]["availability"] == "missing" for ref in refs):
            if claim.get("evidence_state") != "not_checked":
                raise AuditError(where + " unavailable evidence requires not_checked")
            unavailable.append(cid)
        basis = claim.get("causal_identification_basis", "")
        if claim.get("causal_identification_claimed"):
            text(claim, "causal_identification_basis", where)
        available_refs = [ref for ref in refs if evidence[ref]["availability"] == "available"]
        no_support = not available_refs
        only_association = bool(available_refs) and all(evidence[ref].get("design_capacity") == "associational" for ref in available_refs)
        if (claim.get("speaker") == "author" and claim.get("strength") == "causal" and
                (no_support or only_association) and not basis):
            inference.append({"claim_id": cid, "evidence_ids": refs,
                              "evidence_state": claim.get("evidence_state"),
                              "reason": "author_causal_claim_without_available_evidence_or_identification" if no_support else "author_causal_claim_with_only_association_capacity",
                              "disposition": "review_candidate_only_read_context"})
    graph_counts = validate_graph(data, body, claims, evidence)
    promises = data.get("promises", [])
    if not isinstance(promises, list):
        raise AuditError("promises must be an array")
    delivery = []
    for i, promise in enumerate(promises):
        where = "promises[%d]" % i
        pid = text(promise, "id", where)
        anchor(body, text(promise, "promise_quote", where), where)
        locate(promise, where)
        missing = []
        if not complete:
            if promise.get("delivery") != "not_checked":
                raise AuditError(where + " cross-section delivery must be not_checked for truncated text")
            continue
        for field in ("method_quote", "result_quote", "conclusion_quote"):
            value = promise.get(field, "")
            if value:
                anchor(body, value, where + "." + field)
            else:
                missing.append(field)
        if missing:
            text(promise, "undelivered_aspects", where)
            delivery.append({"promise_id": pid, "missing_nodes": missing,
                             "disposition": "review_candidate_only_check_appendices"})
    contrasts = data.get("contrasts", [])
    if not isinstance(contrasts, list):
        raise AuditError("contrasts must be an array")
    consistency = []
    for i, pair in enumerate(contrasts):
        where = "contrasts[%d]" % i
        cid = text(pair, "id", where)
        for side in ("left_quote", "right_quote"):
            anchor(body, text(pair, side, where), where)
        if pair.get("relation") not in {"direct_contradiction", "intentional_contrast", "different_population", "timepoint_change", "unclassified"}:
            raise AuditError(where + " has invalid relation")
        if (pair.get("relation") == "direct_contradiction" and pair.get("same_scope") is True and
                pair.get("left_speaker") == "author" and pair.get("right_speaker") == "author"):
            consistency.append({"contrast_id": cid, "disposition": "review_candidate_only_verify_context"})
    review = data.get("review", {})
    findings = review.get("findings", [])
    if not isinstance(findings, list):
        raise AuditError("findings must be an array")
    finding_ids = set()
    for i, finding in enumerate(findings):
        where = "findings[%d]" % i
        fid = text(finding, "id", where)
        if fid in finding_ids:
            raise AuditError("duplicate finding ID")
        finding_ids.add(fid)
        if text(finding, "facet", where) not in AXES + RELATED:
            raise AuditError(where + " has unknown facet")
        anchor(body, text(finding, "quote", where), where)
        locate(finding, where)
        for key in ("type", "severity", "severity_reason", "basis", "recommendation", "certainty"):
            text(finding, key, where)
        if finding["severity"] not in SEVERITIES:
            raise AuditError(where + " has invalid severity")
    linked = matrix(review.get("coverage"), AXES, complete, finding_ids, "coverage")
    linked += matrix(review.get("related_facets"), RELATED, complete, finding_ids, "related_facets")
    if set(linked) != finding_ids or len(linked) != len(finding_ids):
        raise AuditError("every finding must be linked exactly once by its main or related facet")
    return {
        "validator_status": "ok", "fixture_id": data.get("fixture_id"),
        "coverage_scope": "full" if complete else "partial",
        "axis_states": {facet: review["coverage"][facet]["status"] for facet in AXES},
        "related_facet_states": {facet: review["related_facets"][facet]["status"] for facet in RELATED},
        "evidence_anchors": anchors, "counts": {"claims": len(claims), "findings": len(finding_ids), "argument_graph": graph_counts},
        "inference_candidates": inference, "cross_section_candidates": delivery,
        "consistency_candidates": consistency, "source_unavailable_claims": unavailable,
        "note": "Candidates are derived only from explicit annotations and require contextual review."
    }


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def material_paths(case, case_dir):
    raw = case.get("materials", case.get("material_paths", case.get("files", [])))
    if isinstance(raw, dict):
        raw = list(raw.values())
    if not isinstance(raw, list) or not raw:
        raise AuditError("case.json requires a nonempty materials/material_paths/files array")
    paths = []
    for item in raw:
        value = item if isinstance(item, str) else item.get("path", item.get("file_path")) if isinstance(item, dict) else None
        if not isinstance(value, str) or not value:
            raise AuditError("each material must be a path string or contain path/file_path")
        path = Path(value)
        paths.append(path if path.is_absolute() else case_dir / path)
    return paths


def validate_contract(value, schema, path="$"):
    if not isinstance(schema, dict):
        raise ContractError(path + " schema must be an object")
    if "enum" in schema:
        if not isinstance(schema["enum"], list) or value not in schema["enum"]:
            raise ContractError(path + " is not in enum")
    expected = schema.get("type")
    checks = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
    }
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if not types or any(kind not in checks for kind in types):
            raise ContractError(path + " has unsupported schema type")
        if not any(checks[kind](value) for kind in types):
            raise ContractError(path + " expected type " + str(expected))
    if isinstance(value, dict):
        required = schema.get("required", [])
        if not isinstance(required, list):
            raise ContractError(path + ".required schema must be an array")
        for key in required:
            if key not in value:
                raise ContractError(path + " missing required field " + key)
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ContractError(path + ".properties schema must be an object")
        for key, child in properties.items():
            if key in value:
                validate_contract(value[key], child, path + "." + key)
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            validate_contract(item, schema["items"], "%s[%d]" % (path, i))


def emit(payload, stream=sys.stdout):
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), file=stream)


def run_case(args, candidate):
    case = read_json(args.case_dir / "case.json")
    if not isinstance(case, dict) or not isinstance(case.get("case_id"), str) or not case["case_id"]:
        raise AuditError("case.json requires nonempty case_id")
    task_path = args.task if args.task else args.case_dir / "task.md"
    task_path.read_text(encoding="utf-8")
    if not isinstance(candidate, dict) or not isinstance(candidate.get("ledger"), dict) or not isinstance(candidate.get("result"), dict):
        raise AuditError("case candidate requires ledger and result objects")
    result = dict(candidate["result"])
    result["case_id"] = case["case_id"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    bodies = []
    for path in material_paths(case, args.case_dir):
        bodies.append(path.read_text(encoding="utf-8"))
    ledger = candidate["ledger"]
    manuscript = ledger.setdefault("manuscript", {})
    manuscript["text"] = "\n".join(bodies)
    validation = validate(ledger)
    contract = case.get("output_contract")
    validate_contract(result, contract)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    (args.work_dir / "argument_validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    emit({"validator_status": "ok", "case_id": case["case_id"], "result_path": str(args.output),
          "materials_read": len(bodies), "contract_status": "valid", "counts": validation["counts"]})
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", nargs="?", type=Path, help="legacy standalone ledger path")
    parser.add_argument("--candidate", type=Path, help="standalone ledger, or {ledger,result} in case mode")
    parser.add_argument("--case-dir", type=Path, help="directory containing case.json, task.md and real materials")
    parser.add_argument("--task", type=Path, help="optional task.md override in case mode")
    parser.add_argument("--output", type=Path, default=Path("/app/output/result.json"))
    parser.add_argument("--work-dir", type=Path, default=Path("/app/work"))
    args = parser.parse_args()
    if bool(args.ledger) == bool(args.candidate):
        parser.error("provide exactly one positional ledger or --candidate")
    if args.case_dir and not args.candidate:
        parser.error("case mode requires --candidate")
    candidate_path = args.candidate or args.ledger
    try:
        data = read_json(candidate_path)
        if args.case_dir:
            return run_case(args, data)
        result = validate(data)
    except json.JSONDecodeError as exc:
        emit({"validator_status": "input_error", "error": {"code": "INVALID_JSON", "message": "line %d column %d" % (exc.lineno, exc.colno)}}, sys.stderr)
        return 2
    except ContractError as exc:
        emit({"validator_status": "partial", "error": {"code": "OUTPUT_CONTRACT_ERROR", "message": str(exc)}, "result_path": str(args.output)}, sys.stderr)
        return 3
    except (OSError, AuditError, KeyError, TypeError, AttributeError) as exc:
        code = "INPUT_READ_ERROR" if isinstance(exc, OSError) else "INVALID_LEDGER"
        emit({"validator_status": "input_error", "error": {"code": code, "message": str(exc)}}, sys.stderr)
        return 2
    emit(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
