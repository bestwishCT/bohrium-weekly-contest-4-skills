---
name: evaluate-research-novelty
description: "把科研设想改写为含变量、基线与可证伪条件的问题，按任务、方法、数据、评价和结论比较最近工作，逐条判定原子创新主张覆盖并核查可行性前提。用于选题论证、立项前创新性评估、相近研究比较与带条件继续/修改/暂缓建议；不用于全球 novelty 证明、场景迁移自动升格方法创新、无依据评分或虚构资源条件与实验结果。"
---

# 证据约束的选题与创新性评估

## 触发与结论性质

用户提交研究设想、创新点、开题方向或要求比较最近工作和判断可行性时触发。目标是 `可证伪问题→最近工作比较→原子主张覆盖→带前提建议`，只给当前证据范围内的决策支持，不是项目批准、全球首创证明或已完成实验。

## 真实 case、授权材料和工具

完整读取 `/input/case.json`、`/input/task.md`、真实设想、约束、论文/记录及实际 `output_contract`，最终字段与枚举只以它为准。读取真实 `environment/resources.yaml`，核实检索、全文解析、比较矩阵和计算工具的名称、版本、参数及权限。实际 PDF 无答案层，必须解析原文；不从错误领域标签或 `9actual sources/10records` 标记推定来源、资源或 novelty。

逐源登记真实路径/URL、全文/摘要状态、权限/授权、内容哈希和实际 artifact。用户提供的数据/设备/人员条件也要定位到原材料；未经材料确认的一律标 assumption/missing。仅有摘要时不能补写全文实验细节，不可比指标不合并或强排名。

安装根 `/skills/weekly-skill`，中间比较和恢复点放 `/app/work`，最终评估放 `/app/output`。先执行：

```bash
python3 -B /skills/weekly-skill/scripts/novelty.py inspect \
  --case /input/case.json --task /input/task.md \
  --work /app/work --output /app/output
# 若当前目录提供清单，追加 --resources environment/resources.yaml
```

## 本题专属评估流程

### 1. 从设想到可证伪问题

提取研究对象、变量/机制、比较 baseline、预期 outcome、边界，以及可观察的 falsifiable_observation 和 refute_if。指出歧义、循环表述和不可测形容词；形成主问题及必要子问题。对象不同、指标不同或基线缺失导致不能比较时，先进入限制而不是强行排名。

同时建立六类前提：data、compute、experiment、time、personnel、baseline。每项为 verified/assumption/missing，标 critical、对建议的影响和所需补件。verified 必须连接真实证据，缺失项不能默认为已具备。

### 2. 最近工作检索与比较矩阵

按对象、任务、方法、数据、评价、结论构造各来源的完整 expression 和 fields，固定时间窗、纳排及 stop_condition。记录实际 tool_run_id、ok/empty/error 等状态；未执行写 unexecuted_limit，不写零命中。重复版本和摘要-only 明示。

每条最近工作矩阵记录上述六个字段及 non_comparable_reason；即使 `non_comparable_reason` 为“无额外不可比限制”也显式填写。支持与反对设想的材料都入 evidence 和 conflicts，不能只找有利材料。

检索停止条件：每条原子主张至少经历与其差异相关的真实检索维度；声明的来源/变体已执行或逐项受阻；最相近候选均完成六维比较或明确缺项；新增同义改写/引文追溯只针对具体未闭合主张。空检索或权限不足时停止并标未知，不宣称“无人研究”。总预算 64 请求、900 秒、16384 tokens/响应，优先保存矩阵和部分判断。

### 3. 原子创新主张覆盖

把候选创新拆为不可再分的 atomic_claims，类型限 method、data、context_transfer、conclusion_revision。每条列最相近记录、shared、remaining_difference、comparison_limits 和 evidence_ids，判定 `fully_covered/partially_covered/not_directly_covered_in_scope/unknown`，并写 coverage_rationale。

`not_directly_covered_in_scope` 必须限定实际已执行范围，并设置 `global_novelty_claimed=false`；它不等于全球首创或尚无人研究。已有工作完全覆盖就如实降低主张，部分覆盖不能擦除共同部分。context_transfer 只能标 `context_transfer_only/no_established_increment`，不能自动升格方法或理论创新。结论修正必须有相反证据，不能只凭直觉。

每条 bounded_statement 写清增量、边界和反向材料。无事实依据的创新性总分、排名、统计显著性与“领先”措辞禁用。

### 4. 有条件可行性与停止/转向规则

综合覆盖状态、冲突与前提，给 proceed、revise、defer 或 unknown。建议连接 premise_ids、evidence_ids 和 conditions；所有 critical 前提必须进入判断。critical missing 时不得 proceed；所有原子主张已完全覆盖时，不得以 novelty 为由 proceed，应修改或暂缓。

`minimal_validation` 仅是 `planned_only` 的可观察验证，写 observation、失败条件和停止/转向条件，不代做实验或暗示已完成。给 invalidation_triggers：哪项关键前提、最近工作或反向证据变化会使建议失效。每条判断反查比较矩阵或前提登记。

完成停止条件：问题有可证伪条件；每条创新原子有覆盖状态和最近工作比较；所有六类前提有来源或标假设；建议与缺失资源、反向证据和最小验证一致。未达到则输出 unknown/有限部分，不提高确定性。

## 工具血缘与异常恢复

每次实际检索、全文解析、矩阵计算和版本比较记录 ID、完整参数、来源、状态、返回摘要/数量、artifact、错误和 retry_of。可用状态 `ok/empty/error/timeout/permission_denied/parse_failed/not_executed`。工具失败只影响相关主张与建议，保住已完成矩阵；恢复后从失败记录重跑其覆盖与决策。冲突双方保留 supporting_and_opposing 与 comparison_limits。

0=形成完成或 needs_review 的有界建议；4=无可用证据、建议 unknown；5=部分工具失败、保存可恢复评估；2=覆盖逻辑、前提链接或真实契约无效。规则/契约改变重映射并记录变化，不能继续沿用旧题面键名。

## Canonical input 与真实契约

创建 `/app/work/novelty_input.json`，顶层 `sources`、`tool_runs`、`evidence`、`conflicts`、`uncertainty`、`changes`、`data`、`result`。`data` 包含 `problem`、`searches`、`records`、`atomic_claims`、`assumptions`、`feasibility`、`gaps`。

按真实 output_contract 编写 `result` 映射。可用 `$data.problem`、`$derived.nearest_work_dimensions`、`$derived.coverage_by_atomic_claim`、`$derived.premise_dependencies`、`$data.atomic_claims`、`$data.assumptions`、`$data.feasibility`、`$data.gaps`、`$evidence_matrix`、`$sources`、`$tool_runs`、`$uncertainty`、`$changes`、`$status`、`$case_id`。不得把辅助概念当作新增官方键；真实枚举不接受 `$status` 时用其自有受限值，标准状态仍完整保留在 audit。

```bash
python3 -B /skills/weekly-skill/scripts/novelty.py build \
  --input /app/work/novelty_input.json \
  --case /input/case.json --task /input/task.md \
  --work /app/work --output /app/output
```

输出 `/app/output/result.json` 与 `/app/output/research_audit.json`，保证问题定义、已有工作比较、创新识别、可行性四项相互一致，并保留证据矩阵、精确来源、状态、不确定性和变更。work 保留 input_receipt、integrity_audit 与 recovery_point。Office/slide/diagram 只有实际生成时才附真实 PDF/PNG，普通矩阵不机械画图。

## 最小示例（非正式选题）

以原创合成设想“把已存在的方法甲迁移到虚构低照度数据”为例，提供两条授权合成最近工作和一张明确标记为假设的算力前提。评估演示问题可证伪化、部分覆盖、`context_transfer_only`、缺失授权数据时的 defer，以及 planned_only 的最小验证；不得写真实 novelty、真实资源到位或实验成功。

## 依赖、自查与维护

依赖仅 Python 3.9+ 标准库；作者实测 Python 3.9.6，详见 submission 外 `verification/smoke_result.json`。外部检索/PDF 与资源状态在目标环境重新实测，未证实不写可用。维护后重跑正常、空检索、冲突和工具失败，检查问题可证伪性、六维矩阵、完全/部分覆盖、迁移分类、六类前提、反证、停止条件、建议失效条件及真实契约。遵守 50 文件、单文件 500KB、总 2MB、无符号链接和预算；无真实链接不创建 GITHUB_URL，不擅自提交、PR、发布或生成令牌。
