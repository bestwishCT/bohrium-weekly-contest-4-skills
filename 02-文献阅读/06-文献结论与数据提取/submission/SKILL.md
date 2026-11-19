---
name: field-extract
description: Extract one auditable result per requested target-fields.json field_id from actual paper paragraphs, preserving scalar/text, units, statistics, experimental condition, comparator and exact quotes under reported/not_reported/source_conflict/extraction_failed states. Use for scientific conclusion tables, evidence-bound metrics, or field extraction QA without guessing absent values.
---

# 结论—条件—指标同源结构化抽取

## 触发与边界

用户给论文与具体字段表，要求实验条件、数据或结论结构化时使用。每个真实 `field_id` 必须处理，不能把摘要改写冒充抽取，也不能只抽容易找到的项。版本 `1.0.0`，Python 3.9+ 标准库，Python 3.9.6 实测，无第三方工具。

Agent 负责语义查找、实验条件匹配和来源冲突判定；脚本验证覆盖与状态不变量，字面 quote 检查之外仍需 Agent 复核语义。

## 输入、输出和资源

读取 `/input/case.json`、`task.md`、`environment/resources.yaml`、`target-fields.json` 和 `material_path` 对应实际材料；保留 field 定义、允许来源和任务范围。

Agent 在 `/app/work/fields.json` 给字段结果。`reported` 至少含：

```json
{"field_id":"yield","scalar":2.0,"unit":"mg","statistic":"single reported value","experimental_condition":"A","comparator":"control","paragraph_id":"p1","quote":"2 mg under condition A","condition_match_confirmed":true}
```

其余状态：`not_reported` 的 scalar/unit/statistic/condition/comparator 为空且给 `search_scope`；`source_conflict` 给至少两组 `variants` 和各自 `evidence[]`；`extraction_failed` 给 `reason/failed_location`。

安装于 `/skills/weekly-skill`，相对资源为 `scripts/common.py`、`scripts/workflow.py`。工作文件在 `/app/work`，`result.json` 在 `/app/output`。真实 `output_contract` 逐键、逐层级适配并保留 `case_id`；`extracted_fields/field_results/result` 可映射 `fields`，不擅加未公开字段。

## 抽取流程

1. 建 field ID—定义—目标类型—允许来源清单，检查 ID 唯一和候选不越界。
2. 在摘要、正文、表格、图注和补充材料（实际供应者）中查找语义匹配候选，恢复表头/脚注后读取；不同目标条件数值不挪用。
3. 对数值绑定材料/样本、数据版本、时间点、比较基线、统计口径和单位。区间、误差、显著性按原文或其合同字段保留。
4. 明确区分作者确定结论、限制和“可能/提示”等推测，不改强语气。任务明确要求单位换算时，另留原值、公式和精度，脚本不静默换算。
5. 冲突保留各原值和位置，不能自动挑一个；缺单位、跨页表解析失败、权限不足都走失败状态而非未报告。
6. 逐字段复查 value—condition—comparator—quote 同源性，复读结果 JSON 并确保一项不多一项不少。

## 状态和失败恢复

- `success`：全部 target IDs 有志项、四态合法、reported/conflict 证据与条件确认通过。
- `partial`/退出 2：未处理字段、quote 错、未确认条件、来源冲突信息不全等；出错字段转 `extraction_failed`，其他字段不丢。
- 不把 `not_reported` 写 0，不把 `source_conflict` 静默平均，不把 `extraction_failed` 写成科学不存在。
- 追踪真实版本、失败位置和变更，不宣称平台对照收益。
- 按合同显式保留不确定性：字段定义歧义、单位或条件不全、来源冲突及推测语气均须定位；无法判断与运行失败分开记录，不擅自补值。

若合同另要可视化/Office，必须实际渲染；纯 JSON 字段表不需要伪预览。

## 最小示例（合成、非正式）

`target-fields.json` 请求 yield 和 unmeasured。p1 为 `The control yielded 2 mg under condition A.`；yield 按上述 JSON 抽取；unmeasured 给 `not_reported`、空值和 `search_scope="all supplied paragraphs"`。

```bash
python3 /skills/weekly-skill/scripts/workflow.py --case /app/work/extract-case.json \
  --task /app/work/task.md --input-dir /app/work --work /app/work/run --output /app/output \
  --candidate /app/work/fields.json
```

示意输出：fields 恰 2 项，yield 的单位 mg/条件 A/quote 回链，unmeasured 不为 0。

## 自查与维护

逐键核对合同、四态、冲突双方、数值/单位/误差及语义条件。改变字段定义或状态规则需新版本和缺值/冲突/错引回归，不能用隐藏目标值做资源。

最新 GitHub 加分须基于真实公开 URL；没有 URL 不创建 `GITHUB_URL.txt`、不发布。
