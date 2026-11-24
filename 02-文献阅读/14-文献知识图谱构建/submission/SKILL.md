---
name: kg-query
description: Build and query an evidence graph from two independent paper sources with per-node/edge source_id and JATS paragraph quotes, typed directed relations, concept/source-instance separation, alias decisions and at least three executed queries including cross-source and explicit-conflict checks. Use for multi-paper entity/relation extraction, graph export, provenance queries, or alias conflict audits without merging samples, models, datasets, or results across papers.
---

# 双论文来源隔离的可查询证据知识图谱

## 触发与输入修订

用于多篇论文实体关系抽取、别名归一、来源绑定和图查询。必须读取 corpus manifest、primary/secondary 原文，两篇来源独立且哈希不同；同篇 JSON/Markdown 不算两篇。根 DOI/title/authors 不自动传播给第二篇，各节点/边用自己 source_id。

版本 `1.1.0`，Python 3.9+ 标准库，Python 3.9.6 实测 JSON 图导出与查询；无 RDF/图数据库依赖。本包 JATS 段落 ID 不是 PDF 页码，不能造第 1 页；行内公式已线性化，像素、附件与底层数据缺口保留。

## 输入与证据格式

读取 `/input/case.json`、`task.md`、`environment/resources.yaml`、`corpus-manifest.json` 及 documents 各 path；manifest 记录 source_id、DOI、作者、hash、license。可用 `secondary-publisher.xml` 对原文做辅助核对，但不编造结构。

Agent 在 `/app/work/candidate.json` 写：

- `nodes[]`：非空 node_id/type/label、`entity_scope=concept|source_instance`、evidence[]；type 可按语料合理扩展，不在代码内设封闭枚举，但不能留空；
- `edges[]`：subject/object、relation（uses/compares_with/improves_over/supports/contradicts/reports）、direction=`subject_to_object`、conditions/evidence[]；
- 证据每条 `source_id/paragraph_id/quote`；短引必须属于该 source 的对应段落，禁止 page 字段；
- `alias_decisions[]`：merged/candidate_unmerged/must_not_merge 与具体 reason；
- `queries[]` 至少三个 DSL：`nodes_by_source`、`neighbors`、`edges_by_relation`、`cross_source`、`conflicts`、`unresolved`。

安装 `/skills/weekly-skill`，相对资源 `scripts/common.py`、`scripts/workflow.py`。工作 `/app/work`，`graph.json` 与 `result.json` `/app/output`。真实 `output_contract` 逐键嵌套适配并保留 `case_id`；`knowledge_graph/result` 对应 graph，`query_results` 对应 queries。

## 可执行查询 DSL

每个查询写 `query_id/type/parameters`；支持：

- `nodes_by_source`：`source_id`；
- `neighbors`：`node_id`，`direction=out|in|both`；
- `edges_by_relation`：`relation`；
- `cross_source`：可选 `min_sources`，默认 2，必须是严格正整数（布尔值也不是合法整数）。按每个节点证据中不同的非空 `source_id` 计数，同篇重复短引只算一个来源；返回达到阈值的节点及两端均入选的内部边，不改写或合并实体、实例和条件；
- `conflicts`：无额外参数，只返回两端存在的显式 `relation=contradicts` 边及关联节点；`result.check` 记录检查边数、命中数和解释。空结果表示本次机器检查未发现此类显式边，不表示开放世界中不存在科学冲突；
- `unresolved`：可选 `node_ids/verdict`，不把无定论推断成反向关系。

每次实际执行写回 `executed=true`、`query`、参数与 `result.nodes/result.edges`；节点和边对象保留原始 evidence，另以 `evidence.nodes[]{node_id,evidence}`、`evidence.edges[]{edge_id,evidence}` 逐项回链。`executed=true` 只证明该 DSL 已由机器执行，不证明开放世界推理、科学结论或冲突核查已经完备。参数或类型不支持时写 `executed=false`、failure 并使整体退出 2，不能拿模板冒充执行结果。

```json
[
  {"query_id":"q-cross","type":"cross_source","parameters":{"min_sources":2}},
  {"query_id":"q-conflicts","type":"conflicts","parameters":{}},
  {"query_id":"q-neighbors","type":"neighbors","parameters":{"node_id":"M","direction":"out"}}
]
```

## 构图与查询流程

1. 按 manifest 建两份独立文献和段落索引，复核实际 SHA。字节相同拒绝当独立文献；来源元数据不继承根字段。
2. 只抽实际出现的实体与关系，区分概念和论文特定实例；共现、综述转述和推测不直接当实验关系。
3. 别名合并先核类型、指称、上下文和 source。跨篇可合并规范概念（证据全保留），物理样品、模型实例、数据集和数值结论默认保持 source_instance。某个 source_instance 的证据涉及多个不同 source_id 时保留原节点并报 `source_instance_multi_source`，等待错并检视，绝不自动合并；concept 的合法双源证据不受此限制。
4. 检查端点、关系类型/方向、每节点/边证据短引及两篇覆盖；node_id/type/label/entity_scope 必须非空，未知但非空的 type 可保留。冲突关系并列保留，不把未报告当反向关系。
5. 实际运行至少 3 查询并保存 query/参数、执行标志、结果子图及逐节点/边证据。跨源与无定论/冲突查询也由同一执行器运行；空结果保留 check 或 verdict，不等于科学否定。
6. `graph.json` 写出后 `json.load` 复读并核对节点/边数；检查孤立点、多次 node ID、来源错配和合并规则。
7. 增量加入新论文时以稳定 node ID 加新记录，记录新增/合并/冲突，不重写历史证据；本实现不自动完成开放世界推理。

## 状态与恢复

- `success`：两篇表示与 hash、必填节点字段、所有节点/边证据、别名、至少 3 个实际执行查询和 JSON 复读通过；空 conflicts 不妨碍 success。
- `partial`/退出 2：单篇缺图、hash 不符、必填字段为空、任一 source/paragraph/quote 不匹配、非法关系/方向、source_instance 跨源错并、非法 min_sources 或查询失败等；保留合法节点及具体失败，未执行查询不得写已执行。
- 缺像素/附件/底层数据在 material_gaps 列出，扫描或 XML 权限故障另记，未报告不补数值。
- `trace.json` 记录真实执行查询数、版本和变更，不自造图评测分数。
- 按合同保留不确定性：实体指称、待合并别名、缺失关系和冲突证据须标待复核；无法判断不强行连边，也不与查询失败混写。

纯 JSON 图无需图形预览；只有合同另要可编辑图/Office 时才生成并真实渲染查标签/边。

## 最小示例（合成、非正式）

primary p1=`Method M uses catalyst C.`；secondary s1=`Method M uses solvent S.`。共享概念 M 有双源证据，C、S 为不同 source_instance；边分别挂各自 quote。实际执行上面的 `q-cross`、`q-conflicts`、`q-neighbors`：cross_source 返回 M（同篇重复证据不会制造跨源资格），neighbors 保留 C/S 与两边各自条件；因没有显式 contradicts 边，conflicts 返回空 nodes/edges、check 解释且 `executed=true`。只有实际出现并挂接对应短引的 contradicts 边才会成为冲突正例。

```bash
python3 /skills/weekly-skill/scripts/workflow.py --case /app/work/graph-case.json \
  --task /app/work/task.md --input-dir /app/work --work /app/work/run --output /app/output \
  --candidate /app/work/candidate.json
```

示意输出：两源节点/边可回链，三个 queries 均 `executed=true`，不会把 C 与 S 或两篇实验条件合并。

## 自查与维护

核 manifest hash、来源元数据、逐节点/边 JATS 证据、实例不混、合并理由与真实查询。实体/关系 DSL 升级需版本化并加同名对象、空查询、来源错配回归；不引入伪 PDF 页码。

无真实公开 GitHub URL 不创建 `GITHUB_URL.txt`，不 commit/PR/发布。
