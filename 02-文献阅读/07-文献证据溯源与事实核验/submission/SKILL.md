---
name: claim-verify
description: Decompose actual scientific claims and audit subclaim-level entailment against full source context using supported/partially_supported/contradicted/insufficient_evidence, exact evidence cards, comparator/negation/significance/condition retention, and conflict preservation. Use for citation checking, fact verification, or evidence tracing; keywords alone never establish support.
---

# 科研论断拆解与最小充分证据核验

## 触发与原则

当用户要求“核验这句话”、引用支持度、论断—段落匹配或证据卡片时使用。读取 claims-to-verify 中的句子作为待核问题，而非权威事实。版本 `1.0.0`，Python 3.9+ 标准库，Python 3.9.6 实测，无网络和外部服务。

Agent 必须结合完整上下文做蕴含判断；脚本只检查论断覆盖、原文字符串和四态逻辑，相关主题或关键词不等于支持，未支持外推也不等于实验证伪。

## 输入与合同

读取 `/input/case.json`、`task.md`、`environment/resources.yaml`、`claims-to-verify.json`、`material_path` 全文材料。

Agent 写 `/app/work/cards.json`：`verifications[]` 每条包含 `claim_id`、`verdict`、`reason`、`decomposition[]`。子判断给 `subclaim_id/text/verdict/evidence[]/search_scope` 和四个显式槽 `comparator/negation/significance/conditions`（无则空字符串并核过，不删除键）。矛盾证据标 `relation="contradicts"`；`source_conflicts[]` 保存双方。

结果写 `/app/output/result.json`，轨迹在 `/app/work`。Skill 安装 `/skills/weekly-skill`，引用 `scripts/common.py` 和 `scripts/workflow.py` 相对路径。按真实 `output_contract` 每个键及嵌套适配并保留 `case_id`，`claims/verification_results/result` 可对应 `verifications`。

## 核验流程

1. 把事实、比较、因果、范围拆成不重复且可独立核验的子判断。保留否定、程度、显著性、样本与前提。
2. 回到全部供应上下文寻找候选，按章节→段落→句子定位最小充分证据；区分原始实验、作者解释和转引。
3. 四态：所有关键元素有直接依据才可 supported；仅一部分成立为 partially_supported；相同条件/含义下明确相反才 contradicted；证据不足为 insufficient_evidence。
4. `contradicted` 至少一条 opposition evidence；无证据不能反判矛盾。来源冲突保存 conditions 和双quote，使用限定 verdict。
5. 整体 verdict 由子判断重新汇总：全支持→supported，全不足→insufficient，有矛盾且无冲突保留→contradicted，其余→partial。脚本拒收把混合子判断写成全支持。
6. 核验 claim 数、额外 ID、quote 存在、证据语义和限制；“已复核”只能指实际执行的步骤，不能虚构双人仲裁。

## 状态与恢复

- `success`：每条 claim 有非空拆解、合法整体/子判断和一致证据。
- `partial`/退出 2：未拆、非法状态、错 quote、显式 slots 缺失或整体与子判断不一致；保留卡片及 `validation_status=failed`，回到具体子判断补证或降级。
- 原文不可访问/OCR 失败属于运行限制，不可写成“研究无证据”；长文已查范围在 search_scope 列明。
- `trace.json` 记录真实检查，不生成误支持率、DSH 或人工耗时。
- 按合同保留不确定性及理由：原文缺失、证据范围有限、语义歧义和冲突均为待复核，不能与已证实不支持或运行失败混写。

若只输出证据 JSON 不渲染；其他可编辑格式按合同真实预览。

## 最小示例（合成、非正式）

p1=`Treatment increased yield by 10% under condition A.`；claim=`Treatment increased yield under A.`。子判断 comparator/negation/significance/conditions 均显式记录，verdict supported，quote `increased yield by 10% under condition A`。

```bash
python3 /skills/weekly-skill/scripts/workflow.py --case /app/work/verify-case.json \
  --task /app/work/task.md --input-dir /app/work --work /app/work/run --output /app/output \
  --candidate /app/work/cards.json
```

示意输出：一条 verification、一个 supported 子判断；`sources` 含 claim/p1/quote。若改成“never increased”而无反对证据，不能判 contradicted。

## 自查与维护

逐句核比较、否定、显著性、条件和冲突双方，逐键读回合同；规则变更增加“部分而非全支持”“缺证而非证伪”等回归并升版。

无真实 GitHub URL 不创建 `GITHUB_URL.txt`；新增加分不要求也不允许网络发布。
