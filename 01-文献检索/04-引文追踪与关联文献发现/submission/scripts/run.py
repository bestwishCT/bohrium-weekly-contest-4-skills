#!/usr/bin/env python3
"""Directed citation traversal, shortest evidence paths and identity audit."""
from collections import deque
from common import dedupe, digest, norm_doi, norm_text, norm_title, records_at, run


def process(case, data, task, inv):
    nodes = records_at(data, ["nodes", "papers", "records"])
    keys, clusters, possible, identity_conflicts = dedupe(nodes)
    alias, canonical = {}, {}
    for node, key in zip(nodes, keys):
        raw_id = norm_text(node.get("id", node.get("record_id", "")))
        if raw_id:
            alias[raw_id] = key
        alias[key] = key
        doi = norm_doi(node.get("doi", ""))
        if doi:
            alias[doi] = key
        canonical.setdefault(key, dict(node))
    master_of = {}
    for cluster in clusters:
        for member in cluster["members"]:
            master_of[member] = cluster["master"]
    alias = {key: master_of.get(value, value) for key, value in alias.items()}
    canonical_by_master = {}
    for key, node in canonical.items():
        canonical_by_master.setdefault(master_of.get(key, key), node)

    raw_edges = records_at(data, ["edges", "citation_edges"])
    edges, invalid_edges = [], []
    for index, raw in enumerate(raw_edges):
        eid = norm_text(raw.get("id", raw.get("edge_id", "e%04d" % (index + 1))))
        citing_raw = norm_text(raw.get("citing", raw.get("citing_id", "")))
        cited_raw = norm_text(raw.get("cited", raw.get("cited_id", "")))
        citing, cited = alias.get(citing_raw), alias.get(cited_raw)
        state = norm_text(raw.get("status", "observed"))
        locator = raw.get("source_locator", raw.get("response_locator", ""))
        problem = None
        if not citing or not cited or citing == cited:
            problem = "unknown endpoint or self-loop after identity normalization"
        elif state not in {"observed", "verified", "success"} or not norm_text(locator):
            problem = "edge is not an observed, source-located citation"
        if problem:
            invalid_edges.append({"edge_id": eid, "input": raw, "reason": problem})
            continue
        edges.append({"edge_id": eid, "citing": citing, "cited": cited, "relation": "cites", "status": "observed", "source_locator": locator, "source": raw.get("source", "")})

    seed = data.get("seed", data.get("seed_id", case.get("seed_id", ""))) if isinstance(data, dict) else ""
    seed_candidates, seed_id = [], None
    if isinstance(seed, dict):
        seed_doi = norm_doi(seed.get("doi", ""))
        seed_title = norm_title(seed.get("title", ""))
        doi_candidates = sorted({master_of.get(key, key) for key, node in canonical.items() if seed_doi and seed_doi == norm_doi(node.get("doi", ""))})
        seed_candidates = doi_candidates if doi_candidates else sorted({master_of.get(key, key) for key, node in canonical.items() if seed_title and seed_title == norm_title(node.get("title", ""))})
        if len(seed_candidates) == 1:
            seed_id = seed_candidates[0]
    else:
        seed_id = alias.get(norm_text(seed))
    if len(seed_candidates) > 1:
        seed_id = None

    max_depth = data.get("max_depth", 1) if isinstance(data, dict) else 1
    max_depth = max(0, min(int(max_depth), 20)) if isinstance(max_depth, int) else 1
    budget = data.get("max_nodes", 50) if isinstance(data, dict) else 50
    budget = max(1, int(budget)) if isinstance(budget, int) else 50
    expansions, stopped = [], []
    if seed_id:
        for direction in ["backward", "forward"]:
            queue = deque([(seed_id, 0, [])])
            best = {seed_id: (0, [])}
            while queue:
                current, level, path = queue.popleft()
                if level >= max_depth:
                    if level == max_depth:
                        stopped.append({"direction": direction, "node_id": current, "reason": "depth_limit"})
                    continue
                adjacent = []
                for edge in edges:
                    if direction == "backward" and edge["citing"] == current:
                        adjacent.append((edge["cited"], edge, "current_cites_target"))
                    if direction == "forward" and edge["cited"] == current:
                        adjacent.append((edge["citing"], edge, "target_cites_current"))
                for target, edge, traversal in sorted(adjacent, key=lambda x: (x[0], x[1]["edge_id"])):
                    new_path = path + [{"edge_id": edge["edge_id"], "citing": edge["citing"], "cited": edge["cited"], "relation": "cites", "traversal": traversal, "source_locator": edge["source_locator"], "source": edge["source"]}]
                    if target in best and best[target][0] <= level + 1:
                        continue
                    best[target] = (level + 1, new_path)
                    if target != seed_id:
                        queue.append((target, level + 1, new_path))
            count = 0
            for target, (level, path) in sorted(best.items(), key=lambda x: (x[1][0], x[0])):
                if target == seed_id:
                    continue
                if count >= budget:
                    stopped.append({"direction": direction, "node_id": target, "reason": "node_budget"})
                    continue
                node = canonical_by_master.get(target, {})
                expansions.append({"direction": direction, "depth": level, "record_id": target, "metadata": {k: node.get(k) for k in ["title", "authors", "year", "venue", "doi"]}, "path_from_seed": path, "path_semantics": "each edge remains citing -> cited; multi-hop reachability is not direct citation", "relevance": node.get("relevance", "not_scored"), "sort_basis": "depth first, then stable record id; citation counts not used as relevance"})
                count += 1
    associations = []
    for index, association in enumerate(records_at(data, ["associations", "related_works"])):
        associations.append({"association_id": association.get("id", "a%04d" % (index + 1)), "type": association.get("type", "unspecified_association"), "node_ids": association.get("node_ids", []), "evidence": association.get("evidence", []), "source_locator": association.get("source_locator", ""), "is_citation_edge": False, "note": "association cannot be used as a direct citation path edge"})
    seed_node = canonical_by_master.get(seed_id, {}) if seed_id else {}
    if not nodes:
        status = "empty"
    elif not seed_id or invalid_edges or possible:
        status = "partial"
    else:
        status = "completed" if expansions else "empty"
    return {
        "status": status, "status_detail": {"completed": ["seed identity audit", "directed bounded traversal", "per-edge path provenance"] if seed_id else [], "incomplete": (["seed disambiguation"] if not seed_id else []) + (["invalid edges"] if invalid_edges else [])},
        "seed": {"input": seed, "resolved_record_id": seed_id, "metadata": {k: seed_node.get(k) for k in ["title", "authors", "year", "venue", "doi"]}, "ambiguous_candidate_ids": seed_candidates if len(seed_candidates) > 1 else []},
        "max_depth": max_depth, "node_budget": budget, "expanded_literature": expansions, "primary_results": expansions,
        "citation_edges": edges, "invalid_edges": invalid_edges, "stopped_frontiers": stopped,
        "associations": associations, "identity_clusters": clusters, "possible_duplicate_identities": possible, "identity_conflicts": identity_conflicts,
        "version_relations": data.get("version_relations", []),
        "graph_metrics": {"input_nodes": len(nodes), "canonical_nodes": len(clusters), "observed_valid_edges": len(edges), "excluded_edges": len(invalid_edges), "expansion_paths": len(expansions)},
        "uncertainties": ["No observed edge means unknown/not observed in this finite graph, never no citation.", "Preprint/conference/journal versions remain separate unless explicit version_relations are supplied.", "Current retraction/correction status is unknown unless actual source records state it."],
        "change_records": ["Rejected edges lacking source location, known endpoints or observed status.", "Traversed backward via citing=current and forward via cited=current; associations never entered citation paths.", digest({"seed": seed_id, "paths": expansions})],
        "_aliases": {"results": "expanded_literature", "candidates": "expanded_literature", "paths": "expanded_literature", "edges": "citation_edges", "seed_identity": "seed"},
    }


if __name__ == "__main__":
    raise SystemExit(run("directed-citation-paths", process))
