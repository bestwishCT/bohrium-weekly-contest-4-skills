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
        title_candidates = sorted({master_of.get(key, key) for key, node in canonical.items() if seed_title and seed_title == norm_title(node.get("title", ""))})
        # A supplied DOI is an identity constraint, never a title-search fallback.
        seed_candidates = doi_candidates if seed_doi else title_candidates
        if len(seed_candidates) == 1:
            seed_id = seed_candidates[0]
    else:
        seed_id = alias.get(norm_text(seed))
    if len(seed_candidates) > 1:
        seed_id = None

    relation_rows, parsed_relations, conflict_pairs = [], [], set()
    same_work_types = {"same_work", "same_version", "version", "version_of", "version_relation", "preprint_of", "published_version_of"}
    conflict_types = {"different_work", "distinct_work", "not_same_work", "conflict", "conflicting", "unrelated"}
    for index, raw in enumerate(records_at(data, ["version_relations"])):
        rid = norm_text(raw.get("id", raw.get("relation_id", "v%04d" % (index + 1))))
        left_raw = next((raw.get(k) for k in ["from", "source", "source_id", "left", "version", "version_id", "record_id", "node_id"] if raw.get(k) not in (None, "")), "")
        right_raw = next((raw.get(k) for k in ["to", "target", "target_id", "right", "version_of", "related_version", "related_record_id", "related_node_id"] if raw.get(k) not in (None, "")), "")
        left, right = alias.get(norm_text(left_raw)), alias.get(norm_text(right_raw))
        relation_type = norm_text(raw.get("relation", raw.get("relation_type", raw.get("type", raw.get("state", ""))))).casefold().replace("-", "_").replace(" ", "_")
        locator = norm_text(raw.get("source_locator", raw.get("response_locator", "")))
        conflict = relation_type in conflict_types or raw.get("status") in {"conflicting", "rejected"}
        if left and right and left != right and conflict:
            conflict_pairs.add(tuple(sorted((left, right))))
        parsed_relations.append({"relation_id": rid, "left": left, "right": right, "relation_type": relation_type, "source_locator": locator, "raw": raw, "declared_conflict": conflict})

    version_parents = {key: key for key in canonical_by_master}

    def version_root(key):
        while version_parents[key] != key:
            version_parents[key] = version_parents[version_parents[key]]
            key = version_parents[key]
        return key

    def version_conflict(left_root, right_root):
        return any(tuple(sorted((a, b))) in conflict_pairs for a in canonical_by_master for b in canonical_by_master if version_root(a) == left_root and version_root(b) == right_root)

    version_adjacency = {key: [] for key in canonical_by_master}
    invalid_version_relations = []
    for relation in parsed_relations:
        left, right, raw = relation["left"], relation["right"], relation["raw"]
        problem = None
        if not left or not right or left == right:
            problem = "unknown endpoint or self relation"
        elif not relation["source_locator"]:
            problem = "missing version-relation source locator"
        elif relation["declared_conflict"]:
            problem = "conflicting version relation retained without connection"
        elif relation["relation_type"] not in same_work_types:
            problem = "unsupported same-work/version relation type"
        elif tuple(sorted((left, right))) in conflict_pairs:
            problem = "same-work relation contradicted by a conflicting relation"
        elif version_conflict(version_root(left), version_root(right)):
            problem = "connection would join a declared work conflict"
        if problem:
            row = dict(raw)
            row.update({"relation_id": relation["relation_id"], "verified": False, "validation_error": problem})
            invalid_version_relations.append(row)
            relation_rows.append(row)
            continue
        version_parents[version_root(right)] = version_root(left)
        version_adjacency[left].append((right, relation))
        version_adjacency[right].append((left, relation))
        row = dict(raw)
        row.update({"relation_id": relation["relation_id"], "verified": True, "from_record_id": left, "to_record_id": right, "relation_type": relation["relation_type"], "source_locator": relation["source_locator"], "validation_error": None})
        relation_rows.append(row)
        relation["verified"] = True

    work_groups = {}
    for key in canonical_by_master:
        work_groups.setdefault(version_root(key), []).append(key)
    work_clusters = [{"work_id": "work:%04d" % (index + 1), "record_ids": sorted(members), "version_relation_ids": sorted(r["relation_id"] for r in parsed_relations if r.get("verified") and version_root(r["left"]) == root)} for index, (root, members) in enumerate(sorted(work_groups.items(), key=lambda item: min(item[1])))]

    max_depth = data.get("max_depth", 1) if isinstance(data, dict) else 1
    max_depth = max(0, min(int(max_depth), 20)) if isinstance(max_depth, int) else 1
    budget = data.get("max_nodes", 50) if isinstance(data, dict) else 50
    budget = max(1, int(budget)) if isinstance(budget, int) else 50
    expansions, stopped = [], []
    expanded_nodes = set()
    if seed_id:
        for direction in ["backward", "forward"]:
            queue = deque([(seed_id, 0, [])])
            best = {seed_id: (0, [])}
            while queue:
                current, level, path = queue.popleft()
                for target, relation in sorted(version_adjacency.get(current, []), key=lambda x: (x[0], x[1]["relation_id"])):
                    if target in best and best[target][0] <= level:
                        continue
                    new_path = path + [{"relation_id": relation["relation_id"], "from": current, "to": target, "relation": relation["relation_type"], "traversal": "same_work_version", "source_locator": relation["source_locator"]}]
                    best[target] = (level, new_path)
                    queue.append((target, level, new_path))
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
            for target, (level, path) in sorted(best.items(), key=lambda x: (x[1][0], x[0])):
                if target == seed_id or target in expanded_nodes:
                    continue
                if len(expanded_nodes) >= budget:
                    stopped.append({"direction": direction, "node_id": target, "reason": "node_budget"})
                    continue
                node = canonical_by_master.get(target, {})
                expansions.append({"direction": direction, "depth": level, "record_id": target, "metadata": {k: node.get(k) for k in ["title", "authors", "year", "venue", "doi"]}, "path_from_seed": path, "path_semantics": "citation edges remain citing -> cited; version steps connect records without merging their bibliographic identities", "relevance": node.get("relevance", "not_scored"), "sort_basis": "depth first, then stable record id; one shared unique-node budget across both directions"})
                expanded_nodes.add(target)
    associations = []
    for index, association in enumerate(records_at(data, ["associations", "related_works"])):
        associations.append({"association_id": association.get("id", "a%04d" % (index + 1)), "type": association.get("type", "unspecified_association"), "node_ids": association.get("node_ids", []), "evidence": association.get("evidence", []), "source_locator": association.get("source_locator", ""), "is_citation_edge": False, "note": "association cannot be used as a direct citation path edge"})
    seed_node = canonical_by_master.get(seed_id, {}) if seed_id else {}
    if not nodes:
        status = "empty"
    elif not seed_id or invalid_edges or invalid_version_relations or possible:
        status = "partial"
    else:
        status = "completed" if expansions else "empty"
    return {
        "status": status, "status_detail": {"completed": ["seed identity audit", "directed bounded traversal", "per-edge path provenance"] if seed_id else [], "incomplete": (["seed disambiguation"] if not seed_id else []) + (["invalid edges"] if invalid_edges else []) + (["invalid or conflicting version relations"] if invalid_version_relations else [])},
        "seed": {"input": seed, "resolved_record_id": seed_id, "metadata": {k: seed_node.get(k) for k in ["title", "authors", "year", "venue", "doi"]}, "ambiguous_candidate_ids": seed_candidates if len(seed_candidates) > 1 else []},
        "max_depth": max_depth, "node_budget": budget, "expanded_literature": expansions, "primary_results": expansions,
        "citation_edges": edges, "invalid_edges": invalid_edges, "stopped_frontiers": stopped,
        "associations": associations, "identity_clusters": clusters, "possible_duplicate_identities": possible, "identity_conflicts": identity_conflicts,
        "version_relations": relation_rows, "invalid_version_relations": invalid_version_relations, "work_clusters": work_clusters,
        "graph_metrics": {"input_nodes": len(nodes), "canonical_nodes": len(clusters), "observed_valid_edges": len(edges), "excluded_edges": len(invalid_edges), "expansion_paths": len(expansions)},
        "uncertainties": ["No observed edge means unknown/not observed in this finite graph, never no citation.", "Preprint/conference/journal versions remain separate unless explicit version_relations are supplied.", "Current retraction/correction status is unknown unless actual source records state it."],
        "change_records": ["Rejected edges lacking source location, known endpoints or observed status.", "Traversed backward via citing=current and forward via cited=current; associations never entered citation paths.", digest({"seed": seed_id, "paths": expansions})],
        "_aliases": {"results": "expanded_literature", "candidates": "expanded_literature", "paths": "expanded_literature", "edges": "citation_edges", "seed_identity": "seed"},
    }


if __name__ == "__main__":
    raise SystemExit(run("directed-citation-paths", process))
