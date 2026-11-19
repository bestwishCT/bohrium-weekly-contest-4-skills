---
name: analyze-research-frontiers
description: "基于跨年授权文献集合去重和统一在线/见刊/预印本年份口径，复算时间切片与主题演化，按阶段口径选代表工作，并用正反证据约束研究空白。用于研究前沿、主题变迁、领域时间趋势和代表性工作分析；不用于把检索前几条或高被引榜包装成趋势、凭关键词频率宣布拐点，或在缺年、小样本、空检索时虚构增速和全球前沿。"
---

# 可复算的研究前沿与趋势分析

## 触发与边界

当用户要求跨年份趋势、主题出现/合并/分裂/衰退、阶段代表作或有排除证据的研究空白时触发。主产物固定为四类：时间趋势、主题演化、代表性工作、研究空白；不做泛泛领域综述、计量图形展示或全球领先性证明。

## 真实材料与口径审计

完整读取 `/input/case.json`、`/input/task.md`、实际论文/题录及真实 `output_contract`，严格沿用其字段和枚举。读取实际 `environment/resources.yaml`，核实题录、PDF/OCR、去重、统计和可选绘图工具的真实版本与权限。实际 PDF 无答案层；必须解析原文与对应页，不由领域标签、缓存或 `9actual sources/10records` 标记补造年份、引用数、主题或优先权。

每个来源保留实际路径/URL、访问范围、授权、全文状态、内容哈希和真实解析 artifact。在线、见刊、预印本日期分别保存；缺失保持 null，不估算。重复/版本映射保留差异，不能为了去重抹去预印本与正式版关系。只使用授权材料；未执行的来源不是零命中。

根目录固定 `/skills/weekly-skill`，中间量表与原文放 `/app/work`，最终分析放 `/app/output`。先冻结：

```bash
python3 -B /skills/weekly-skill/scripts/trends.py inspect \
  --case /input/case.json --task /input/task.md \
  --work /app/work --output /app/output
# 若清单存在，追加 --resources environment/resources.yaml
```

## 本题专属分析流程

### 1. 固定数据集与时间分母

在 scope 中写 source_scope、纳排、time_basis、window、denominator_rule 和停止条件。每条记录有输入 ID、included 与理由、各实际日期、原始主题词和 duplicate_of（若有）。去重只合并有依据的版本，其他缺失不可静默删除。

主统计按声明 time_basis；必要时只有在真实字段齐全时才另做敏感性对照。逐年统计独立纳入记录，分母是该年有选定年份的独立纳入数。缺年、窗口外、重复版本分别守恒，缺年绝不算“0 年发文”。

### 2. 术语归一和主题演化

为每个 canonical topic 登记 variants、mapping_reason、disambiguation 和证据行。区分术语漂移、同名异义与实质路线改变；不符规则的词保留待复核，不静默并类。主题出现、合并、分裂、衰退或 terminology_drift 均需 interval、statistic_refs、included record_ids、evidence_ids 与 rationale。

每篇同一主题只计一次；多标签主题的占比可合计超过 100%，必须在分母说明中明确，不能误算为互斥分布。演化必须回答何时及因何证据划分，不以关键词排行榜替代。

### 3. 时间趋势不是热度清单

脚本从原始记录复算 document_count、topic document_count、denominator 与 share。主题 share=`该年该主题独立文献数/该年所有有选定年份的独立纳入文献数`，分母 0 时 share 为 null。保存实际统计行 ID 供主张回链。

只有至少三个有数据年份且至少六篇独立纳入文献，脚本才给描述性首末观测期 absolute_delta；这只是有界计数差，不是 CAGR、显著增长或拐点。小样本只描述离散分布，不制造精确增速。实际统计仍受来源覆盖与版本口径限制。

趋势 claim 同时连接 statistic_refs 和 evidence_ids，区分 descriptive_count 与 bounded_interpretation。冲突需比较时间、对象和方法边界，不按高引或多数抹平。切片结束条件：所有独立记录完成去重/日期/主题归一或标待审，所有比例可由表复算，缺年与样本限制已入不确定性。

### 4. 分阶段代表作与空白

phase 必须有区间、证据和划分 rationale。代表作先写纳入口径：方法路线覆盖、阶段影响、材料中可核的方法引入或任务指定指标；每个阶段至少一件，或明确 no_representative_reason。不得只以 citation_rank 选“代表作”，不得无全球优先权核查写首次/最早/领先。检查子领域与长尾路线遗漏。

每个研究空白写 basis_evidence_ids、query_scope、unresolved 和 excluded_nearby（真实 record_id + 排除理由）。空白可来自纳入文献反复未决问题、特定对象/尺度失效或未决冲突，但检索为空单独绝不能构成空白。

整体停止条件：声明的来源和变体已真实完成或逐项受阻；四类产物均有实际证据/统计或明确无法形成的原因；每条趋势、演化、代表作、空白主张回链；追加检索仅针对具体覆盖缺口。受 64 请求、900 秒、16384 tokens/响应限制时先保存原始表、去重和真实部分统计，不抽样冒充全量。

## 工具血缘与失败恢复

所有检索、全文解析、日期提取、去重、映射与统计记录 ID、完整参数、状态、返回摘要/数量、artifact、错误和 retry_of。状态限 `ok/empty/error/timeout/permission_denied/parse_failed/not_executed`。文献量过大时按时间片持久化输入 ID 和计数，合并后做守恒检查；解析失败只重跑对应记录及有依赖的统计行。统计/绘图失败保留原始表，不能伪造预览或数据。

## Canonical input 与真实输出契约

创建 `/app/work/trends_input.json`：顶层 `sources`、`tool_runs`、`evidence`、`conflicts`、`uncertainty`、`changes`、`data`、`result`。`data` 含 `scope`、`records`、`vocabulary`、`trend_claims`、`theme_evolution`、`phases`、`representatives`、`gaps`。

`result` 按实际 output_contract 引用 `$derived.year_statistics`、`$derived.growth`、`$derived.missing_year_record_ids`、`$derived.duplicate_record_ids`、`$derived.normalized_topics_by_record`、`$data.theme_evolution`、`$data.representatives`、`$data.trend_claims`、`$data.gaps`、`$evidence_matrix`、`$sources`、`$tool_runs`、`$uncertainty`、`$changes`、`$status`、`$case_id`。不要用辅助键扩展官方 schema；真实枚举不允许 `$status` 时按其含义映射，audit 始终保留标准运行态。

```bash
python3 -B /skills/weekly-skill/scripts/trends.py build \
  --input /app/work/trends_input.json \
  --case /input/case.json --task /input/task.md \
  --work /app/work --output /app/output
```

输出 `/app/output/result.json` 和 `/app/output/research_audit.json`，包括实际契约要求的四类结果、可复算统计、证据矩阵、来源定位、状态、不确定性与变更。work 保存 input_receipt、integrity_audit、recovery_point。只有真实任务需要或实际生成可编辑图表/Office/slide/diagram 时，基于真实表格另做 PDF/PNG 预览；不机械绘图。

## 异常与退出

0=完成或保留冲突待复核；4=无可用证据；5=部分工具失败但保留真实统计与分析；2=口径、回链或契约无效。缺年单列，主题歧义待复核，空检索不增长不空白，小样本只描述，全文/权限失败保存恢复点，规则改变以最新官方和真实契约重算。

## 最小示例（非正式趋势数据）

用一组原创合成题录跨 2020–2023，包含一个重复版本、一个缺年记录、一个术语旧称/新称映射和两条阶段代表候选。演示按见刊年重算、缺年分离、方法路线选代表作以及有相近工作排除证据的空白；所有结论仅适用于该合成集，不是平台三案例或真实领域趋势。

## 依赖、自查与维护

只用 Python 3.9+ 标准库；作者实测 Python 3.9.6，详见 submission 外 `verification/smoke_result.json`。目标环境仍须实测真实检索/PDF/绘图工具，不把自己的运行态写成工具版本。维护后重跑正常、空检索、冲突、工具失败；复算去重/缺年/窗口守恒、主题分母、增长护栏、阶段代表覆盖、空白排除证据和真实契约。遵守 50 文件、单文件 500KB、总 2MB、无符号链接及预算；不虚构 GITHUB_URL，不擅自提交、PR、发布或生成令牌。
