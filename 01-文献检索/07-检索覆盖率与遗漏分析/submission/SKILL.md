---
name: audit-coverage
description: 用独立冻结的已知参考集逐条核对检索命中，复算固定分母的覆盖率、漏检和补检净新增，并给出有证据的漏检归因。适用于检索质量审计与补充检索闭环，不用于用检索结果反推分母或声称全局召回。
---

# 冻结参考集的覆盖率与遗漏闭环

## 何时触发

已有指南、综述、专家清单或既有系统综述中的已知文献，需要计算某次真实检索遗漏了哪些条目、为什么漏以及补检是否真正新增命中时使用。参考集是独立输入，不能从当前候选结果构造。

## 正式输入与安装

1. 读取 `environment/resources.yaml`，确认实际来源和权限。读取 `/input/case.json` 与 `/input/task.md`，先拿真实 `case_id`、`output_contract`、参考集版本、统计口径和补检预算。
2. 逐份核查九份科学文档、真实检索响应、参考图与本地状态；以实际综述/清单为参考，不依据错误领域标签补条目。PDF 必须按原页阅读并留定位。
3. 制作 `/app/work/analysis_input.json`，保存参考集原始清单、纳入理由、可评价规则与每轮查询响应。撤稿、全文不可得和标识冲突不能因处理困难就暗中移出分母。
4. `/app/work` 存冻结与中间计算，`/app/output` 存最终结果。先保存部分 JSON，再独立复算分子、分母、待裁决与补检增量。
5. 安装运行 `/skills/weekly-skill/scripts/run.py`；脚本仅分析已授权快照，不进行实时补检。

## 覆盖与漏检专属算法

1. **先冻结再查结果**：对声明版本及按内容排序的参考原始记录计算 SHA-256，保留原始数、重复映射与纳入理由。参考版本冻结后，检索噪声不能反向扩大或缩小分母。
2. **可评价口径**：使用案例明确 `evaluable` 和原因。重复项映射有记录；重复副本间可评价状态冲突进入 denominator review，不擅自删除。零分母时覆盖值为 null，绝不报 100%。
3. **逐条对齐**：参考 DOI 规范化后精确命中；否则只有规范题名、第一作者、年份一致且 DOI 不冲突时命中。同题名但元数据不足或冲突为待裁决，不能计为命中或直接漏检。
4. **基线与补检分轮**：命中矩阵按真实执行轮次推进。failed/timeout 不导入该响应中的记录，更不能证明不存在。所有 baseline 完成后保存一次基线快照。
5. **补检净新增**：补检只计此前未命中的新唯一参考条目；重复命中、非参考候选和结果数量增长都不算覆盖改善。比较补检前后同一冻结分母。
6. **按证据归因**：漏检原因必须使用 `miss_causes` 指向特定库、query、字段或时间窗的实际证据，如缺术语变体、字段限制或时间截断。没有证据时统一 `undetermined`，不得泛称数据库覆盖不足。
7. **停止与残余**：记录实际预算、连续零净新增或来源不可用；每条残余遗漏保留已尝试动作与人工建议，不报告全局零遗漏，也不伪造人工抽检一致率。

核心结构：

```json
{
  "reference_set": {"source": "actual review", "version": "freeze-v1", "records": [{"id": "ref1", "doi": null, "title": "actual known paper", "authors": ["actual"], "year": 2022, "source_locator": "review#page4", "inclusion_reason": "named criterion", "evaluable": true}]},
  "search_rounds": [{"id": "baseline", "kind": "baseline", "executed": true, "status": "success", "expression": "actual query", "source": "actual source", "fields": ["title"], "response_locator": "query#1", "records": []}],
  "miss_causes": [{"reference_id": "ref1", "category": "synonym_not_covered", "evidence": "actual query variant absent", "query_ids": ["baseline"]}]
}
```

覆盖公式固定为 `命中参考身份数 / 可评价冻结参考身份数`。待裁决不算命中，另报数量；最终漏检率为未命中数除同一分母，不能用待裁决美化。

## 输出和状态

- `/app/output/result.json` 含实际 `case_id` 和合同全字段；内部有 `reference_freeze/coverage_matrix/baseline_coverage/final_coverage/supplement_increments/residual_omissions/reconciliation`。
- `/app/work/analysis.json` 保留逐条匹配和候选，`contract_validation.json` 警告实际合同未映射键，`run_log.json` 记录环境。不得伪造参考白板或人工判断。
- `completed` 表示矩阵可复算且漏检均有具体证据；`needs_review` 表示有待裁决身份或未确定归因；`partial` 表示分母问题/来源失败；`empty` 表示没有参考输入。
- 指标只对该冻结集合、实际来源和时间窗成立，不是全局 recall、最新性或完整系统综述结论。

## 依赖与异常

仅 Python 3 标准库，安装副本合成实测 **CPython 3.9.6**，无第三方包或联网依赖。退出码 0=算法完成，1=部分结果伴随领域异常，2=基础输入失败。

参考重复保留映射；撤稿/不可得按案例口径单列；DOI 冲突转待裁决；超时冻结相关查询面；补检零新增就报告零收益；零分母为 null 并停止百分比解释。合同变化先保存真实部分结果再修复。

## 最小示意（虚构、非正式）

```json
{"reference_set":{"source":"demo-known-list","version":"demo-v1","records":[{"id":"ref1","title":"SYNTHETIC KNOWN RECORD","authors":["Demo"],"year":2020,"source_locator":"demo#ref1","evaluable":true}]},"search_rounds":[{"id":"base","kind":"baseline","executed":true,"status":"success","expression":"demo","response_locator":"demo#query","records":[{"id":"hit1","title":"SYNTHETIC KNOWN RECORD","authors":["Demo"],"year":2020,"source_locator":"demo#hit1"}]}]}
```

```bash
python3 /skills/weekly-skill/scripts/run.py --case /input/case.json --task /input/task.md --data /app/work/analysis_input.json --input-root /input --resources environment/resources.yaml
```

若 `case_id: demo-coverage`，合同含 `status/results/sources/uncertainties/change_records`，结构示意节选是基线/最终分母均为 1、命中均为 1、`status: completed`。这里的 100% 仅是单条虚构单元测试计算，不是任何真实覆盖率或官方结果。

## 自查与维护

1. 第三人应能由冻结清单独立复算原始数、唯一身份数、可评价分母、基线命中、补检净新增和残余漏检。
2. 对每条 hit 核查标识/题名作者年份和 locator；每条 missed/pending 分开，原因必须有具体查询面证据。
3. 修改匹配或统计逻辑后用相同冻结输入复跑；记录真实的分母漂移（应为零）、净新增和待裁决变化，不构造“提升”。
4. 必测零分母、重复参考、撤稿、不可得、DOI 冲突、来源超时和补检重复命中；只提交必要脚本和说明，不附参考白板或统计答案。
5. 无真实公开 GitHub URL 时该项正式提交阻塞且不创建 `GITHUB_URL.txt`；不发布、不提交、不处理令牌。遵守 64 请求、900 秒、每响应含推理 16384 tokens。
