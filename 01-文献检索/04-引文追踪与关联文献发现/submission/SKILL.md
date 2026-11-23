---
name: trace-citations
description: 从已核验种子论文出发执行有方向的前向施引与后向参考文献追踪，保留逐跳来源、最短证据路径及跨版本身份关系。适用于引文扩展和真实共被引或耦合关联发现，不用于把主题相似伪装成引用边或宣称完整引文网络。
---

# 方向明确的引文追踪与证据路径

## 触发与禁区

用户提供种子论文和实际引文图/响应，要求找方法或数据出处、后续改进、批评或平行工作时使用。先确认种子身份再扩展；同名歧义未解决时不得把多篇论文混成一条路径。普通关键词检索、引文内容精读及未经授权抓取不在范围内。

## 正式输入和调用

1. 读取 `environment/resources.yaml`、真实 `/input/case.json` 与 `/input/task.md`，提取 `case_id`、原始 `output_contract`、种子标识、深度和节点预算。实际九份科学文档、Crossref/Europe PMC/出版方响应、保留参考图及本地增量状态都需逐份核查。
2. 直接阅读实际 PDF 或保留响应，不从错误标签补边。使用 `/app/work/analysis_input.json` 保存实际节点、种子和有向边；PDF 页或响应 JSON 路径成为 `source_locator`。
3. 只有实际快照中观察到的 citation 才能写边。共被引、文献耦合、作者机构关联放 `associations`，必须另有实际证据。
4. 在 `/app/work` 计算并将最终结果写 `/app/output`，先生成可辨认的部分结果。使用 `/skills/weekly-skill/scripts/run.py` 和默认参数，或按最小示例显式提供路径；脚本不向外发请求。

## 有向图专属流程

1. **身份核验**：优先按种子 DOI，其次按给定 ID/题名匹配。多个不同身份共享题名时返回 `ambiguous_candidate_ids`，禁止扩展。
2. **边验收**：统一为 `citing → cited`。端点必须都能映射实际节点，状态为 observed/verified/success，且有来源定位；缺方向、缺定位或自环异常进入 `invalid_edges`，不参与 BFS。
3. **后向追踪**：当前节点为 `citing` 时取该边的 `cited`，得到当前论文参考文献；保留 `current_cites_target`。
4. **前向追踪**：当前节点为 `cited` 时取 `citing`，得到施引当前论文的后续文献；保留 `target_cites_current`。
5. **最短路径**：两个方向分别 BFS，保存从种子到目标的逐条边和层级，不因多跳可达而写成种子直接引用，不把关联边混入路径。达到 `max_depth` 时保留停止前沿；`max_nodes` 是跨两个方向合计的唯一扩展节点预算，同节点多方向命中只计一次。
6. **版本身份**：DOI 精确归一，题名+第一作者+年份作为保守副本证据。给定种子 DOI 时不以题名回退到异 DOI 或无 DOI 记录；无精确命中保持未解析/冲突。预印本、会议、期刊、增补版需显式 `version_relations`；每条含实际 from/to、same_work/version 类型和 `source_locator`，双方存在且关系不冲突才连接。版本关系只连接路径，不把异 DOI 抹成同一书目记录；合并不足保持独立并列待复核。
7. **结构解释**：以路径方向、深度和证据完整度组织候选；被引数不作为相关性。关联结果保留 `is_citation_edge: false` 及自身证据类型。
8. **稳定性**：邻接按节点和边 ID 排序，同输入保持路径与归属稳定；改深度/预算必须记录真实新增与停止变化。

输入骨架：

```json
{
  "seed": {"doi": null, "title": "actual seed title"},
  "max_depth": 2,
  "max_nodes": 50,
  "nodes": [{"id": "S", "title": "seed", "authors": ["actual"], "year": 2022}, {"id": "R", "title": "reference"}],
  "edges": [{"id": "e1", "citing": "S", "cited": "R", "status": "observed", "source": "retained-graph", "source_locator": "graph.json#edges/0"}],
  "associations": [{"type": "co_citation", "node_ids": ["S", "P"], "source_locator": "graph.json#assoc/0", "evidence": ["actual shared-citation record"]}],
  "version_relations": []
}
```

## 结果、证据和状态

- `/app/output/result.json` 包含真实 `case_id` 和实际合同全字段；内部候选在 `expanded_literature`，边在 `citation_edges`，路径在各候选 `path_from_seed`，种子核验在 `seed`，关联另放 `associations`。
- `/app/work/analysis.json`、`contract_validation.json` 和 `run_log.json` 分别保存完整图审计、合同警告与实际运行环境。只有合同明确要求图示产物时才生成其真实可编辑版本及渲染预览；默认不画装饰性网络图。
- `completed` 表示种子唯一且有效扩展路径已形成；`empty` 可表示种子已识别但该有限图未观察到可用扩展；`partial` 表示种子歧义、无效边或身份不确定。
- 未观察到边只代表当前快照未知，不等于无引用；新预印本无前向记录、前向来源不可用、撤稿/更正未知必须分别描述。

## 依赖与异常处置

只用 Python 3 标准库；合成安装副本实际运行版本为 **CPython 3.9.6**，无外部包或实时接口。其他版本未实测。退出码 0 表示算法完成且无输出合同警告，1 表示有领域异常或输出合同警告但已存部分结果，2 表示 case/task 输入失败。

种子无有效边时用 `empty` 加来源覆盖说明；不同时代/来源字段冲突并列原值；方向未知时拒绝定向；撤稿与更正只按材料原话和时点记录；限流/付费墙不绕过，相关层级明确未完成。

## 最小调用示意（虚构非正式数据）

```json
{"seed":"S","max_depth":1,"nodes":[{"id":"S","title":"SYNTHETIC SEED"},{"id":"R","title":"SYNTHETIC REFERENCE"}],"edges":[{"id":"e1","citing":"S","cited":"R","status":"observed","source_locator":"demo#e1"}],"associations":[]}
```

```bash
python3 /skills/weekly-skill/scripts/run.py --case /input/case.json --task /input/task.md --data /app/work/analysis_input.json --input-root /input --resources environment/resources.yaml
```

若案例 ID 为 `demo-graph` 且合同含 `status/results/sources/uncertainties/change_records`，示意节选为 `{"case_id":"demo-graph","status":"completed","results":[{"record_id":"id:R","direction":"backward","depth":1}]}`。这里仅证明结构，不是任何真实引用事实。

## 自查与维护

1. 沿每条输出路径逐跳验证端点、方向与 locator，反向复走应回到种子；C 关联不能出现在 citation path。
2. 复跑并比较种子、身份簇、深度、路径和前沿；改去重或排序后用同图记录真实差异，不虚构调用成本。
3. 维护必测同名种子、无引文、缺方向、环、深度截断、跨版本和状态冲突。节点/边结构变更必须同步更新示例。
4. 资源只含必要脚本和本文，UTF-8、无符号链接，不附预制网络或正式案例输出。
5. 无真实公开 GitHub URL 时该项正式提交阻塞且不创建 `GITHUB_URL.txt`；不发布、不提交、不处理令牌。遵守 64 请求、900 秒、每响应含推理 16384 tokens。
