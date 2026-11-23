---
name: map-questions
description: 将复杂科研问题拆成带依赖的可检验子问题，并用真实查询轮次、证据映射和停止判据驱动态收敛。适用于问题拆解、并行检索规划和证据缺口管理，不用于关键词堆砌、全局召回声称或虚构人工复核。
---

# 依赖驱动的科研问题证据闭环

## 何时使用

用户的问题同时含对象、机制、方法、比较、时间和证据要求，且必须解释先查什么、后续查询由何触发、何时停止时使用。产物是子问题及证据覆盖矩阵，不是开放式科研回答或单篇翻译。

## 正式运行接线

1. 先读 `environment/resources.yaml`，再读真实 `/input/case.json` 与 `/input/task.md`，核对 `case_id`、`output_contract` 和任务约束。检查九篇实际署名论文、真实查询输入及全部筛选记录；PDF 必须按可读原页解析并保存定位。
2. 实际 `search09` 中标记为合成重复导出/冲突意见的内容只能作为输入 fixture。保留 `fixture` 和输入角色，不将其改造成真实论文版本、双人标注或正确答案。
3. 以 `/app/work/analysis_input.json` 记录 Agent 从实际材料抽取的子问题、依赖、查询轮次和证据。每个直接证据至少有 `source_locator`，首选再带原文片段，不能只给题目相似度。
4. 在 `/app/work` 计算、`/app/output` 交付；先落盘部分 `result.json`，再核验所有子问题和所有原始记录，而不是只保留“有用”条目。
5. 平台将本 Skill 放在 `/skills/weekly-skill`；使用 `/skills/weekly-skill/scripts/run.py`，公共输入模块会自动从脚本所在目录解析，不依赖作者目录。

## 依赖与停止算法

1. 为每个子问题保存 `required`、`depends_on`、`min_direct_sources`、术语和实际歧义。通过 DFS 检查缺依赖和环，生成拓扑顺序及按依赖层划分的可并行批次；有环时明确部分失败。
2. 保守去重证据身份。把每条输入记录映射到子问题，并区分 `direct/indirect/background/excluded/pending`；声称 direct 却没有实际来源定位时降为 pending。
3. 只处理显式已执行的查询。`executed` 缺失时，仅可把带 expression、非空 `response_locator` 且状态为 completed/observed/success 的已提供快照认定为已执行，并记录 `execution_basis`；显式 false、planned 或无定位不推定执行。按输入轮次计算相对此前集合的**新增唯一定位直接证据**，记录该轮触发原因、目的、表达式、响应定位、新术语和失败状态。
4. 必要子问题达到其直接证据阈值且前置依赖闭合时才为 `sufficient`。证据充足不表示查遍所有研究，只表示满足本次明确规则。
5. 连续两轮成功查询均无新增直接证据时可停止补查，但状态仍为 `unresolved`，原因是边际收益低，而非“没有研究”。资源失败使覆盖未知，术语歧义/范围冲突转人工，未解决依赖会阻塞后续闭合。
6. 输出每个停止动作、所有未解决子问题、全部记录处置、身份簇和输入冲突，不能用背景资料凑直接证据阈值。

最小抽取结构：

```json
{
  "research_question": "actual multipart question",
  "subquestions": [{"id": "sq1", "text": "define object", "required": true, "depends_on": [], "min_direct_sources": 1, "terms": ["term"]}, {"id": "sq2", "text": "measure effect", "depends_on": ["sq1"]}],
  "records": [{"id": "p1", "title": "actual paper", "subquestion_ids": ["sq1"], "evidence_strength": "direct", "source_locator": "paper.pdf#page=2", "evidence_quote": "verbatim evidence"}],
  "queries": [{"id": "q1", "subquestion_id": "sq1", "trigger": "initial need", "expression": "actual query", "executed": true, "status": "success", "hits": ["p1"], "response_locator": "query.json#result"}]
}
```

## 交付与不确定性

- `/app/output/result.json` 含真实 `case_id` 和实际 `output_contract` 的所有键；专用内部键包括 `subquestion_coverage/dependency_graph/parallel_batches/query_rounds/evidence_map/record_dispositions/stop_decisions/unresolved_subquestions`。只按实际合同映射，不把这些内部名强行添加为公开字段。
- `/app/work/analysis.json` 是全量证据审计，`contract_validation.json` 作逐键类型辅助，`run_log.json` 记录实际环境。
- `completed` 仅表示全部必要子问题按显式规则闭合；`partial` 表示依赖错误、来源失败或证据缺口；`empty` 表示没有可处理子问题。完成的查询轮数、必要的转人工和未解决证据冲突分别陈述。
- 所有结论仅属给定有限论文和查询快照，不声称 live database、全局召回或已完成真人复核。

## 依赖、错误与恢复

仅 Python 3 标准库；合成安装副本实测 **CPython 3.9.6**，其他版本未实测，无包安装或远程服务。退出码 0 表示算法执行结束，1 表示保留部分结果的异常，2 表示输入无法读取。科学状态必须另看 `status`。

环/缺依赖：停止声称可执行顺序，保留相关子问题；术语歧义：并列候选含义并标受影响证据；查询冲突：并列来源，不以多数覆盖；证据为空：留下已尝试轮次和未解决状态；合同漂移：保留 work 结果后修映射并复跑。

## 最小调用示意（虚构且非正式）

```json
{"subquestions":[{"id":"sq1","text":"demo need","depends_on":[],"required":true}],"records":[{"id":"demo-p","title":"SYNTHETIC PAPER","subquestion_ids":["sq1"],"evidence_strength":"direct","source_locator":"demo#1"}],"queries":[{"id":"demo-q","subquestion_id":"sq1","trigger":"demo gap","expression":"demo","executed":true,"status":"success","hits":["demo-p"]}]}
```

```bash
python3 /skills/weekly-skill/scripts/run.py --case /input/case.json --task /input/task.md --data /app/work/analysis_input.json --input-root /input --resources environment/resources.yaml
```

实际 `case.json` 用 `case_id: demo-question` 及要求 `status/results/sources/uncertainties/change_records` 的合同时，示意结果为 `{"case_id":"demo-question","status":"completed","results":[{"subquestion_id":"sq1","coverage_state":"sufficient"}]}`。此为虚构结构节选，非真实论文或官方答案。

## 自查与维护

- 复跑拓扑顺序、每轮新增证据、停止理由及全记录数；修改依赖判据后用同输入比较覆盖与无效轮次，不能虚构基线。
- 检查每条 direct 的定位、每个停止动作与真实证据状态一致；特别关注“连续零新增≠零研究”和“有环≠已执行”。
- 测试缺依赖、环、失败源、重复 fixture、冲突意见、直接证据无定位及合同字段改变。
- 依赖文件只有 `scripts/run.py` 和 `scripts/common.py`；修改算法同时更新对应示例。保持资源为 UTF-8 文本且无符号链接，不把烟测或正式结果随 Skill 打包。
- 未获真实公开 GitHub 链接时正式提交此附加项阻塞，不创建 `GITHUB_URL.txt`，不发布、不提交、不处理令牌。执行预算为 64 次模型请求、900 秒、每响应含推理 16384 tokens。
