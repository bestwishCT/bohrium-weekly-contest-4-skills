#!/usr/bin/env python3
"""Two-corpus evidence graph validator, exporter, and executable query engine."""
import argparse
import json
import sys

from common import Run, add_arguments, digest, paragraph_map, paragraphs, read_json, stable_id, write_json

RELATIONS = {"uses", "compares_with", "improves_over", "supports", "contradicts", "reports"}


def internal_subgraph(selected_nodes, edges):
    ids = set(selected_nodes)
    selected_edges = [edge for edge in edges
                      if edge.get("subject") in ids and edge.get("object") in ids]
    return {"nodes": list(selected_nodes.values()), "edges": selected_edges}


def execute_query(query, nodes, edges):
    kind = query.get("type")
    parameters = query.get("parameters", {})
    result = internal_subgraph(nodes, edges)
    if kind == "nodes_by_source":
        wanted = parameters.get("source_id")
        selected_nodes = {key: node for key, node in nodes.items()
                          if any(ref.get("source_id") == wanted for ref in node.get("evidence", []))}
        result = internal_subgraph(selected_nodes, edges)
    elif kind == "neighbors":
        center = parameters.get("node_id")
        direction = parameters.get("direction", "both")
        selected_edges = [edge for edge in edges if
                          (direction in {"out", "both"} and edge["subject"] == center) or
                          (direction in {"in", "both"} and edge["object"] == center)]
        ids = {center}
        for edge in selected_edges:
            ids.update([edge["subject"], edge["object"]])
        selected_nodes = {key: node for key, node in nodes.items() if key in ids}
        result = {"nodes": list(selected_nodes.values()), "edges": selected_edges}
    elif kind == "edges_by_relation":
        relation = parameters.get("relation")
        selected_edges = [edge for edge in edges
                          if edge.get("relation") == relation and
                          edge.get("subject") in nodes and edge.get("object") in nodes]
        ids = {value for edge in selected_edges for value in (edge["subject"], edge["object"])}
        selected_nodes = {key: node for key, node in nodes.items() if key in ids}
        result = {"nodes": list(selected_nodes.values()), "edges": selected_edges}
    elif kind == "cross_source":
        min_sources = parameters.get("min_sources", 2)
        if isinstance(min_sources, bool) or not isinstance(min_sources, int) or min_sources <= 0:
            raise ValueError("cross_source parameters.min_sources must be a strictly positive integer")
        selected_nodes = {}
        for key, node in nodes.items():
            source_ids = {ref.get("source_id") for ref in node.get("evidence", [])
                          if isinstance(ref, dict) and ref.get("source_id")}
            if len(source_ids) >= min_sources:
                selected_nodes[key] = node
        result = internal_subgraph(selected_nodes, edges)
        result["check"] = {
            "min_sources": min_sources,
            "matching_nodes": len(selected_nodes),
            "explanation": "distinct nonempty evidence source_id values were counted; repeated evidence from one paper counts once; no nodes, instances or conditions were merged; executed retrieval is not completed open-world inference"
        }
    elif kind == "conflicts":
        selected_edges = [edge for edge in edges
                          if edge.get("relation") == "contradicts" and
                          edge.get("subject") in nodes and edge.get("object") in nodes]
        ids = {value for edge in selected_edges for value in (edge["subject"], edge["object"])}
        selected_nodes = {key: node for key, node in nodes.items() if key in ids}
        result = {"nodes": list(selected_nodes.values()), "edges": selected_edges,
                  "check": {"relation": "contradicts", "examined_edges": len(edges),
                                      "matching_internal_edges": len(selected_edges),
                                      "explanation": "only explicit internal contradicts edges are returned; an empty result reports that this check found none, not that scientific conflict is impossible"}}
    elif kind == "unresolved":
        verdict = parameters.get("verdict", "no_determinate_relation")
        ids = set(parameters.get("node_ids", list(nodes)))
        selected_nodes = {key: node for key, node in nodes.items() if key in ids}
        result = {"nodes": list(selected_nodes.values()), "edges": [],
                  "verdict": verdict,
                  "reason": "no determinate edge is returned; absence is not a negative scientific result"}
    else:
        raise ValueError("unsupported executable query type: " + str(kind))
    return result


def query_evidence(subgraph):
    """Return explicit evidence backlinks for every node and edge in a result."""
    return {
        "nodes": [{"node_id": node["node_id"], "evidence": node.get("evidence", [])}
                  for node in subgraph["nodes"]],
        "edges": [{"edge_id": edge["edge_id"], "evidence": edge.get("evidence", [])}
                  for edge in subgraph["edges"]]
    }


def main():
    args = add_arguments(argparse.ArgumentParser(description=__doc__)).parse_args()
    try:
        run = Run(args)
    except Exception as error:
        print(json.dumps({"status": "input_error", "error": str(error)}), file=sys.stderr)
        return 2
    try:
        manifest_path = run.path(run.case.get("corpus_manifest", "corpus-manifest.json"))
        manifest = read_json(manifest_path)
        documents = manifest.get("documents", manifest.get("sources", []))
        if len(documents) < 2:
            raise ValueError("input revision requires two independent papers")
        indices = {}
        inventory = []
        source_hashes = []
        for document in documents:
            source_id = str(document.get("source_id", ""))
            if not source_id or source_id in indices:
                raise ValueError("source_id must be nonempty and unique")
            path = run.path(document["path"])
            text_index = paragraph_map(paragraphs(read_json(path)))
            actual_hash = digest(path)
            source_hashes.append(actual_hash)
            indices[source_id] = text_index
            inventory.append({"source_id": source_id, "path": str(path),
                              "doi": document.get("doi", "not_reported"),
                              "authors": document.get("authors", "not_reported"),
                              "license": document.get("license", "not_reported"),
                              "declared_sha256": document.get("sha256"), "actual_sha256": actual_hash,
                              "hash_matches": document.get("sha256") in (None, actual_hash)})
        if len(set(source_hashes)) < 2:
            raise ValueError("the two inputs are byte-identical; multiple formats of one paper are not independent papers")
        candidate = run.candidate("graph.json")
        nodes_list = candidate.get("nodes", [])
        edges_list = candidate.get("edges", [])
        failures = []
        nodes = {}
        for node in nodes_list:
            raw_node_id = node.get("node_id")
            node_id = raw_node_id if isinstance(raw_node_id, str) else ""
            if not node_id.strip():
                failures.append({"kind": "missing_or_empty_node_field", "node_id": node_id,
                                 "fields": [name for name in ("node_id", "type", "label", "entity_scope")
                                            if not isinstance(node.get(name), str) or not node.get(name).strip()]})
                continue
            if node_id in nodes:
                failures.append({"kind": "missing_or_duplicate_node_id", "node_id": node_id})
                continue
            missing_fields = [name for name in ("type", "label", "entity_scope")
                              if not isinstance(node.get(name), str) or not node.get(name).strip()]
            if missing_fields:
                failures.append({"kind": "missing_or_empty_node_field", "node_id": node_id,
                                 "fields": missing_fields})
            if node_id not in nodes and isinstance(node.get("entity_scope"), str) and node.get("entity_scope").strip() and node.get("entity_scope") not in {"concept", "source_instance"}:
                failures.append({"kind": "entity_scope_required", "node_id": node_id})
            references = node.get("evidence", [])
            if not references:
                failures.append({"kind": "node_without_evidence", "node_id": node_id})
            if node.get("entity_scope") == "source_instance":
                source_ids = {reference.get("source_id") for reference in references
                              if isinstance(reference, dict) and reference.get("source_id")}
                if len(source_ids) > 1:
                    failures.append({"kind": "source_instance_multi_source", "node_id": node_id,
                                     "source_ids": sorted(source_ids),
                                     "reason": "a source-specific instance cannot combine evidence from different papers; inspect for an erroneous merge rather than merging automatically"})
            for reference in references:
                source_id = reference.get("source_id")
                paragraph_id = str(reference.get("paragraph_id", ""))
                if "page" in reference or "page_number" in reference:
                    failures.append({"kind": "invented_pdf_page_anchor", "node_id": node_id})
                if source_id not in indices or not reference.get("quote") or reference["quote"] not in indices.get(source_id, {}).get(paragraph_id, ""):
                    failures.append({"kind": "node_source_quote_mismatch", "node_id": node_id,
                                     "source_id": source_id, "paragraph_id": paragraph_id})
            nodes[node_id] = dict(node)
        edges = []
        for edge in edges_list:
            edge_id = edge.get("edge_id") or stable_id("edge", edge.get("subject"), edge.get("relation"), edge.get("object"))
            item = dict(edge)
            item["edge_id"] = edge_id
            if item.get("relation") not in RELATIONS or item.get("direction") != "subject_to_object":
                failures.append({"kind": "invalid_relation_or_direction", "edge_id": edge_id})
            if item.get("subject") not in nodes or item.get("object") not in nodes:
                failures.append({"kind": "edge_endpoint_missing", "edge_id": edge_id})
            references = item.get("evidence", [])
            if not references:
                failures.append({"kind": "edge_without_evidence", "edge_id": edge_id})
            for reference in references:
                source_id = reference.get("source_id")
                paragraph_id = str(reference.get("paragraph_id", ""))
                if source_id not in indices or not reference.get("quote") or reference["quote"] not in indices.get(source_id, {}).get(paragraph_id, ""):
                    failures.append({"kind": "edge_source_quote_mismatch", "edge_id": edge_id,
                                     "source_id": source_id, "paragraph_id": paragraph_id})
            edges.append(item)
        for document in inventory:
            if not any(reference.get("source_id") == document["source_id"] for node in nodes_list for reference in node.get("evidence", [])):
                failures.append({"kind": "paper_not_represented", "source_id": document["source_id"]})
            if not document["hash_matches"]:
                failures.append({"kind": "manifest_hash_mismatch", "source_id": document["source_id"]})
        alias_decisions = candidate.get("alias_decisions", [])
        for decision in alias_decisions:
            ids = decision.get("node_ids", [])
            if len(ids) < 2 or any(identifier not in nodes for identifier in ids):
                failures.append({"kind": "alias_decision_unknown_nodes", "detail": decision})
            elif decision.get("decision") not in {"merged", "candidate_unmerged", "must_not_merge"} or not decision.get("reason"):
                failures.append({"kind": "invalid_alias_decision", "detail": decision})
            elif decision["decision"] == "merged" and any(nodes[identifier].get("entity_scope") == "source_instance" for identifier in ids):
                if len({reference.get("source_id") for identifier in ids for reference in nodes[identifier].get("evidence", [])}) > 1:
                    failures.append({"kind": "source_specific_instances_wrongly_merged", "node_ids": ids})
        query_results = []
        queries = candidate.get("queries", [])
        for query in queries[:]:
            try:
                subgraph = execute_query(query, nodes, edges)
                query_results.append({"query_id": query.get("query_id"), "query": query,
                                      "executed": True, "result": subgraph,
                                      "evidence": query_evidence(subgraph)})
            except Exception as error:
                failures.append({"kind": "query_execution_failed", "query_id": query.get("query_id"), "reason": str(error)})
                query_results.append({"query_id": query.get("query_id"), "query": query,
                                      "executed": False, "result": None, "failure": str(error)})
        if sum(item["executed"] for item in query_results) < 3:
            failures.append({"kind": "at_least_three_actual_queries_required"})
        status = "partial" if failures or run.uncertainty else "success"
        graph = {"nodes": list(nodes.values()), "edges": edges,
                 "alias_decisions": alias_decisions, "sources": inventory}
        write_json(run.output / "graph.json", graph)
        reread = read_json(run.output / "graph.json")
        if len(reread["nodes"]) != len(nodes) or len(reread["edges"]) != len(edges):
            raise ValueError("graph JSON readback mismatch")
        run.changes.append("kept independent source metadata and JATS anchors; executed evidence-linked cross_source/conflicts and other DSL queries without open-world inference or PDF-page claims")
        run.finish({"status": status, "graph": graph, "nodes": list(nodes.values()), "edges": edges,
                    "alias_decisions": alias_decisions, "queries": query_results,
                    "failures": failures, "sources": inventory,
                    "material_gaps": ["image pixels", "supplementary attachments", "underlying clinical or experimental data"]},
                   aliases={"knowledgegraph": "graph", "queryresults": "queries", "result": "graph"},
                   code=2 if status == "partial" else 0,
                   trace_extra={"queries_actually_executed": sum(item["executed"] for item in query_results),
                                "graph_json_readback": True})
    except Exception as error:
        run.uncertainty.append(str(error))
        run.finish({"status": "partial", "graph": {"nodes": [], "edges": []}, "queries": [],
                    "failures": [{"reason": str(error)}]}, code=2)


if __name__ == "__main__":
    sys.exit(main())
