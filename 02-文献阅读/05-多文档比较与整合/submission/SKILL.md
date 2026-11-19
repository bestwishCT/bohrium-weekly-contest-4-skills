---
name: doc-compare
description: Build a common-field multi-paper comparison matrix with exact per-cell document/paragraph evidence, then align task, metric, units, material, pollutant, dataset, split, baseline and budget before any synthesis. Use for literature comparison, method matrices, cross-document conflicts, or noncomparable-data audits; never pools unlike experiments.
---

# 多文档共同字段与可比条件两级对齐

## 触发与范围

用于同一研究问题下两篇及以上真实论文的比较矩阵、冲突卡片和整合结论。必须读取 organizer v2 工单与全部语料；逐篇摘要不是比较。版本 `1.0.0`，Python 3.9+ 标准库，Python 3.9.6 实测，无联网/第三方依赖。

语义字段和冲突解释由 Agent 从实际论文判断；脚本提供身份、证据和可比性硬约束，不自动产生总体效应、科学冲突原因或“更优”结论。

## 输入与逐键合同

读取 `/input/case.json`、`task.md`、`environment/resources.yaml`、`comparison-workorder.json` 及 workorder `documents[].path` 的每篇 JSON 语料。`source_document_id` 唯一指一篇，记录版本，拒绝重复标识。

Agent 在 `/app/work/cells.json` 形成：

```json
{"fields":["metric"],"cells":[{"field_id":"accuracy","source_document_id":"d1","paragraph_id":"p1","quote":"原文短引","value":0.8,"status":"reported","task":"classification","metric":"accuracy","unit":"fraction","material":"M","pollutant":"NA","budget":"1","dataset_version":"v1","split":"test","baseline":"B"}],"syntheses":[]}
```

脚本输出 `/app/output/result.json` 与 `/app/work/trace.json`。安装 `/skills/weekly-skill`，资源相对为 `scripts/common.py`、`scripts/workflow.py`。实际 `case.json.output_contract` 是最终 schema 唯一来源：逐键、逐嵌套适配，保留 `case_id`，矩阵别名 `comparison_matrix/cells/result` 可映射 `matrix`。

## 专属比较流程

1. 建文档—版本—段落索引，确认全部工单文献已处理。不相关、多版本或解析失败单列，不能跳过失败论文后宣称完成。
2. 按统一字段字典抽对象、数据、方法、条件、指标、结论、局限；每格保留 source document、paragraph 和 quote。`not_reported` 的 value 必须为 null。
3. 第一级做字段语义对齐；第二级将 `task/metric/unit/material/pollutant/budget/dataset_version/split/baseline` 组成可比组。任何未知条件保留，不默认相同。
4. 同一 field 才生成跨文档比较，条件组不同标 `noncomparable`；相同组的价值关系仍需 `requires_semantic_review`，脚本不把数字不同自动称科学矛盾。
5. 只有 `syntheses` 指定 cell IDs 且全部来自同一可比组才通过结构门；跨组立即 `rejected_noncomparable`，退出失败。不同染料、数据集或预算不求均值。
6. 对工单 field × source 覆盖做笛卡尔核查，缺格不是 0。每格精确短引存在性和冲突双方由原始文档复核，允许新增文档但不得改历史取值含义。

## 状态和恢复

- `success`：全部请求格覆盖、证据定位通过，无非法混池；不可比本身可作为正常研究结果保留。
- `partial`/退出 2：未知 source、错 quote、缺格、未报告却带值、跨组综合等。保留合法矩阵和具体失败格，先修复来源/条件再重跑。
- `trace.json` 记录真实版本、哈希/位置和变更；不伪造对照、效率或冲突归因。

普通 JSON 不需渲染；Office/图表若由合同要求则必须真实生成预览。无工具诚实限制。

## 最小示例（合成、非正式）

两篇 d1/d2 相同任务、accuracy、fraction、M、NA、budget 1、dataset v1/test、baseline B，分别为 0.8/0.7；每格 quote 真实位于自己段落，`syntheses=[]`。

```bash
python3 /skills/weekly-skill/scripts/workflow.py --case /app/work/comparison-case.json \
  --task /app/work/task.md --input-dir /app/work --work /app/work/run --output /app/output \
  --candidate /app/work/cells.json
```

示意输出：`comparability=comparable`、`value_relation=requires_semantic_review`，不会输出均值 0.75，也不擅自宣称方法优劣。

## 自查与维护

核对所有文档/字段、格级来源、条件组、冲突和不可比项；在修改任何单位换算前保留原值与明确法则，并增加跨组拒绝测试。新增字段需版本化字典和 changelog（写入运行 changes），不能抹掉旧资料。

无真实公开 GitHub URL 时不创建 `GITHUB_URL.txt`；最新加分不成为伪造链接理由，不联网提交。
