---
name: reproduce-systematic-review
description: "按前瞻性版本化协议执行系统综述：构造可执行检索式，保留题录与全文两轮逐条判定、去重和计数守恒，依据原文逐维度评价质量并形成证据回链结论。用于系统性文献综述、可复跑纳排、PRISMA式实际计数与质量评价；不用于普通叙述性综述、伪造双人复核/数据库命中，或以检索为零推断全球研究空白。"
---

# 可复跑的系统综述协议

## 触发与范围

用户要求系统综述、可复跑检索、纳排版本审计、两轮筛选、质量评价或计数核对时触发。主线是协议约束与逐条记录，另人可对同一输入重算基本一致的纳入集；不把普通综述文本改称系统综述，不替代精读、翻译、润色或学术审查。

## 读取真实案例、工具和权限

1. 完整读取 `/input/case.json`、`/input/task.md`、真实协议/题录/全文和实际 `output_contract`。契约的字段、嵌套、枚举和必填性优先于本说明；辅助结构不得擅称官方新键。
2. 读取真实 `environment/resources.yaml`，核对数据库接口、题录解析、PDF、OCR、去重、计算工具的版本、参数与权限。官方资源的逻辑名称不是可用性证明。
3. 每次来源登记实际数据库/材料、平台/版本、检索日期（只有实际执行才填）、完整表达式、访问状态、返回 artifact 和哈希。PDF 必须实际解析并保留页码；无预抽取答案，不能从标签或 `9actual sources/10records` 标记推定记录身份。
4. 只检索获准来源，数据库无权限时记录 blocked 范围，绝不编造命中数、全文、作者、DOI 或评审人。结论只覆盖实际纳入集，不声称全球完整或完成实验。

安装资源根为 `/skills/weekly-skill`，工作中 `/app/work`，最终文件在 `/app/output`。冻结再分析：

```bash
python3 -B /skills/weekly-skill/scripts/systematic.py inspect \
  --case /input/case.json --task /input/task.md \
  --work /app/work --output /app/output
# 若可用，追加 --resources environment/resources.yaml
```

## 协议化专属流程

### 1. 先注册问题与纳排协议

按领域选择合适问题结构，不机械套医学 PICO。保存对象、干预/暴露/方法、比较、结局、设计、时间/语言中的适用项。每条纳排 criterion 有 ID、kind、可判定 test、priority；写明条款冲突规则和全文不可得处理，并设 `registered_before_screening=true`。

若中途修订，记录原/新版本、原因、受影响记录及 `rescreened=true`；所有受影响两轮判定重跑。不得为达到期望纳入数而放宽条款。

### 2. 写到能执行的检索式

每个数据库分别保留完整布尔表达式、字段、同义词/受控词、时间/语言限制、逐段意图和停止条件。`executed=true` 必须有真实 `search_date`、tool_run_id 及 ok/empty 状态；`reported_hits` 等于保留的 returned_record_ids 数量。未执行只能保留拟执行表达式、范围和 unexecuted_limit，命中数为空而非虚构。

同一概念的不同真实表述分别测试并记录歧义；不为调用次数重复相同查询。失败重试关联 retry_of 和真实错误。

检索停止条件：协议声明的数据库/本地来源与检索变体每项均已有成功、空结果或明确失败/未执行状态；所有返回记录已入待去重集；新增检索只针对具体未覆盖概念和预期边际价值；达到任务边界或预算即保存阶段结果。这里的停止不等于全球检索饱和。

### 3. 两轮筛选与计数守恒

每条输入记录只有真实 decision log。题录阶段为 include/exclude/awaiting/duplicate，全文阶段再次独立为 include/exclude/await_fulltext/review。每个决定含当前 protocol_version、真实判定者（Agent/规则/实际人工输入）、criterion_ids 和具体理由；全文排除还附原文 locator。条款冲突按预定优先级，无法决定进入待复核而非随意纳入。

仅有一个实际 Agent/规则判定时，不引入虚假第二评审人、共识或 kappa。重复文献直接映射独立记录，不串联重复。脚本按记录重算：输入 = duplicates + title_excluded + fulltext_excluded + included + awaiting。守恒失败时停止最终综合，先修映射而不是改数字。流程图只在任务要求且实际生成时制作，数字来自记录。

两轮停止条件：每条记录恰有一个最终/待审状态；每个全文 included 有可得原文与后续质量评价，awaiting 有原因和所需材料；冲突条款已依规则处理或转待复核。不得把 awaiting 算入纳入。

### 4. 质量评价与回链综合

根据真实设计选择可解释量表并写 `scale_basis`，不得冒称权威背书。每个维度采用 `low_concern/some_concern/high_concern/not_assessable`，给 evidence_id 与理由；无全文则不得完成全文质量评价。总体判断按公开的 `overall_rule` 汇总，不写无来源精确分数或“质量较高”。

每条 claim 连接 evidence、最终 included records 和 `quality_consideration`，区分 material_fact/bounded_inference/unknown；说明质量差异降低还是限制结论。冲突并列设计与质量维度差异；研究空白需有已执行 query_scope、支持原文、相近工作排除依据和精确 unresolved。未检索到只能进入限制。

完成停止条件：计数守恒、纳入=质量评价集合、每条实质结论回链质量与原文、反例和空白均有据。预算至多 64 请求、900 秒、16384 tokens/响应；优先保全逐条筛选与部分矩阵。

## 工具与恢复

每次检索、解析、全文获取、去重、重筛和计数记录实际 ID、参数、来源、状态、返回摘要/数量、artifact、错误与 retry_of；失败和空检索也必须留 artifact。状态限 `ok/empty/error/timeout/permission_denied/parse_failed/not_executed`。保留去重前 ID 映射和不可访问全文，解析恢复后只重跑其全文评价与受影响综合。

## Canonical input 与契约适配

写 `/app/work/systematic_input.json`：顶层 `sources`、`tool_runs`、`evidence`、`conflicts`、`uncertainty`、`changes`、`data`、`result`。`data` 为 `protocol`（含 criteria/revisions）、`queries`、`records`（title_decision/fulltext_decision）、`quality`、`claims`、`gaps`。

`result` 按真实 output_contract 映射，引用可来自 `$derived.flow_counts`、`$derived.record_final_states`、`$derived.included_record_ids`、`$derived.quality_by_record`、`$derived.claim_quality_links`、`$data.protocol`、`$data.queries`、`$evidence_matrix`、`$sources`、`$tool_runs`、`$uncertainty`、`$changes`、`$status`、`$case_id`。不得注入契约外状态或计数字段；若 `$status` 不属于真实枚举，用实际值表达限制，audit 始终保留标准运行态。

```bash
python3 -B /skills/weekly-skill/scripts/systematic.py build \
  --input /app/work/systematic_input.json \
  --case /input/case.json --task /input/task.md \
  --work /app/work --output /app/output
```

`/app/output/result.json` 必含真实 case_id 及全部契约字段；`/app/output/research_audit.json` 保存协议、检索原文、筛选、质量、计数、证据矩阵、来源定位、状态、不确定性和变更。work 保存 input_receipt、integrity_audit、recovery_point。六类检查为问题结构、检索覆盖、证据回链、冲突、综合与有据空白。

## 异常语义

0=完成或保留分歧的 needs_review；4=无可用证据并明确覆盖不足；5=部分工具失败但有可恢复成品；2=契约、筛选、计数或回链无效。超时/无权限不填命中数，全文不可得保留 awaiting，计数不一致不综合，证据矛盾降低强度，预算不足先保存完整决定表。

## 最小示例（非正式系统综述）

用五条原创合成题录演示：两条重复版本、一个题录排除、一个待全文、一个最终纳入。协议有自拟 criterion 与透明质量维度，检索仅标记“本地合成语料真实执行”；输出一条只覆盖该纳入记录的有限结论。不是双人筛选、真实数据库结果、平台固定案例或金标准。

## 依赖、自查与维护

只用 Python 3.9+ 标准库，作者实测 Python 3.9.6，证据在 submission 外 `verification/smoke_result.json`。真实数据库/PDF 工具需在目标环境另核，不私有会话、不静默安装。维护后重跑正常、空检索、冲突、工具失败；核对版本重筛、两轮理由、计数、实际判定者、逐维质量、六类覆盖和真实契约。遵守 50 文件、单文件 500KB、总 2MB、无符号链接与执行预算；不创建虚假 GITHUB_URL，不擅自提交、PR、发布或生成令牌。
