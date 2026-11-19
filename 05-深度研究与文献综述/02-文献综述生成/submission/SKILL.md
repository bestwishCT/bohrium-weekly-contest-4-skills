---
name: draft-evidence-linked-review
description: "在真实授权文献集上先执行版本化纳排与去重，再从对象、方法和结论分歧归纳主题，建立原文—证据行—草稿句双向回链并生成保留冲突和强度的综述。用于给定论文/PDF/题录集合的文献综述生成、带引用草稿与引文支持核对；不用于系统综述计数审计、单篇逐句精读、翻译润色、选刊或虚构文献补齐综述。"
---

# 证据表驱动的综述生成

## 触发与任务边界

用户已有题录、摘要或全文并要求筛选、主题归纳、证据组织或生成带引用综述时触发。核心不是拼接摘要，而是 `筛选→主题→证据表→逐句草稿`。不做系统综述的数据库覆盖声明、双人筛选或数量流程审计；这些交给专门流程。

## 真实输入、授权与初始化

完整读取 `/input/case.json`、原样 `/input/task.md`、实际题录/摘要/PDF 和真实 `case.json.output_contract`，把真实字段名、类型、层级、枚举当作唯一最终契约。读取实际 `environment/resources.yaml` 并核实题录/PDF/全文工具的名称、版本和权限。不能由官方紧凑标记或错误领域标签推定来源身份；`9actual sources/10records` 只能在实际材料中核对。

逐源保存实际路径/URL、解析 artifact、内容哈希、全文或摘要状态、访问限制和授权。PDF 必须用获准工具实际解析并按页保存至 `/app/work`，不得预置答案层。没有全文只可引用摘要且显著降级；缺 DOI、年份、页码保持未知。只使用许可材料，未访问数据库不可写成已查。

安装路径 `/skills/weekly-skill`；中间表、文本和版本放 `/app/work`，成品放 `/app/output`。先冻结真实输入：

```bash
python3 -B /skills/weekly-skill/scripts/review.py inspect \
  --case /input/case.json --task /input/task.md \
  --work /app/work --output /app/output
# 有清单时追加 --resources environment/resources.yaml
```

## 本题专属四步流程

### 1. 筛选在写作之前

从问题生成版本化 `criteria`，设 `fixed_before_screening=true`，列出对象、方法、时间、语言、研究类型和全文可得性的纳排规则。每条记录保留真实元数据、全文状态、唯一 ID、criteria_version、决定与具体理由；状态为 `included/excluded/duplicate/unclear`。

重复版本直接映射到一个独立记录，合并其来源而不是重复计入主题支持。低相关、无法判断和摘要-only 单列，不强行纳入。若确需修订标准，记录版本、原因、受影响记录并重筛；不得为主题叙事暗改规则。

停止条件：每个输入记录恰有筛选/合并/待复核状态；每条排除有规则或具体理由；每个独立纳入记录已进入至少一个主题或大上下文待审限制；写作前不留下静默未筛条目。空集时停止综合，而不是补造文献。

### 2. 从比较字段归纳主题

提取对象、研究设计/方法路线、材料/数据、结论、限制和分歧，并以成功工具 artifact 与原文为据。主题的 `derived_from` 至少包含两个实际比较维度，列出纳入记录、支持证据与 boundary；不能预设“背景/方法/挑战/展望”再装文献。单篇主题标单源限制，不扩大为该领域共识；无法归类的相关反例仍须保留。

### 3. 原文—证据行双向回链

证据行含 `record_id`、真实 source_id、逐字 quote、页/段定位、成功 tool_run_id、全文/abstract 证据层级、可支持范围及限制。全文记录用 `evidence_level=fulltext`，摘要-only 必须为 abstract，不得用摘要推断全文实验细节。

原文解析文本以 `text_path` 相对 canonical input 保存；PDF 页使用 form-feed。冲突证据至少两篇独立记录，比较对象和方法，保留 unresolved_difference。研究空白须说明本批纳入证据覆盖、未解决的具体关系、相近工作为何被排除；空检索仅可形成覆盖限制。

### 4. 证据表生成草稿

以 `draft_units` 为最小写作单元：一条实质陈述、theme_id、evidence_ids、类型（source_fact/bounded_synthesis/uncertainty）、strength_rationale 和相邻引用标记 `[@E1]`。脚本从单元组装草稿，不另行凭记忆写不受核对的结语。摘要证据的陈述必须 `abstract_qualified=true` 并使用有限措辞。冲突双方都实际出现在单元中，不设“尚有争议”一句带过。

每行证据必须被草稿使用或在 `unused_evidence` 给出未采用理由。将证据按 `batches` 持久化，即使上下文受限也不能漏行；最后执行 `cross_batch_check`，跨批检查重复、冲突、主题遗漏和强度变化。每个实质句反查证据，再抽查证据回原文；修改写 changes。

停止条件：所有独立纳入记录有主题位置；所有实质单元有紧邻证据定位；所有冲突进入草稿；证据行有草稿回链或不使用理由；具体空白有支持和排除依据。达不到时保存有限草稿与未决项，不能宣称全部覆盖。

## 工具调用与停止

每次实际题录解析、去重、PDF/OCR、表格读取和必要的本地检索记录 ID、参数、来源、状态、真实返回摘要/数量、artifact、错误与 retry_of。状态仅用 `ok/empty/error/timeout/permission_denied/parse_failed/not_executed`。失败不删除旧版本，不用另一个来源冒名替代。

外部补充检索若获准，须先写缺口、来源、检索式和停止条件；在实际执行完声明的来源/变体、或遇到明确权限/工具限制时停止，不以检索数量衡量质量。预算上限为 64 个模型请求、900 秒、16384 tokens/响应，先保存筛选和已核证据，再完成草稿。

## Canonical input 与真实契约适配

创建 `/app/work/review_input.json`，顶层为 `sources`、`tool_runs`、`evidence`、`conflicts`、`uncertainty`、`changes`、`data`、`result`。`data` 包含 `criteria`、`records`、`themes`、`draft_units`、`unused_evidence`、`gaps`、`batches`、`cross_batch_check`。

`result` 依据真实 output_contract 编写同源引用，不添加臆造官方键。可用 `$derived.draft`、`$derived.screening_counts`、`$derived.theme_record_map`、`$derived.citation_backlinks`、`$data.criteria`、`$evidence_matrix`、`$sources`、`$tool_runs`、`$uncertainty`、`$changes`、`$status`、`$case_id`。只有契约枚举相符才映射 `$status`，否则用真实枚举并在 audit 保留运行态。

```bash
python3 -B /skills/weekly-skill/scripts/review.py build \
  --input /app/work/review_input.json \
  --case /input/case.json --task /input/task.md \
  --work /app/work --output /app/output
```

生成 `/app/output/result.json` 与 `/app/output/research_audit.json`，分别承载真实契约结果和完整血缘/状态/不确定性/变更。work 中保留 input_receipt、integrity_audit、recovery_point。结果至少核对筛选、主题方法路线、正反证据、引用定位、草稿、具体空白；辅助文件名仍以真实 task 为先。Office/slide/diagram 仅在真实生成时附 PDF/PNG 预览，普通综述不绘图。

## 失败与恢复

空检索退出 4，保留实际零命中；工具失败但有已核证据退出 5，保存部分筛选/草稿；冲突输出 needs_review、退出 0；结构、回链或契约错误退出 2。工具修复后只重新解析受影响记录，更新其主题与草稿单元并重跑；计数、版本和引用不能手工改成看似合理。

## 最小示例（非正式文献集）

使用四段原创合成材料：两个相同 canonical work 的版本、一个摘要-only 相反结果、一个低相关排除项。以虚构“传感器甲在低样本条件下的报告差异”为问题，演示 duplicate 合并、abstract 限定、主题和带 `[@E...]` 的两句草稿。所有论断限定“仅本合成文献集”，不是正式案例、白皮书、真实文献或科研结论。

## 依赖、自查与维护

仅依赖 Python 3.9+ 标准库；作者实测 Python 3.9.6，详见 submission 外 `verification/smoke_result.json`。PDF/题录外部工具必须在目标环境实测，未实测能力不得写成可用。维护后重跑正常、空集、冲突、工具失败，检查重复计数、摘要限制、主题分布、双向引用、跨批一致性、变更和真实契约。遵守 50 文件、单文件 500KB、总量 2MB、无符号链接和 64/900/16384 预算；无真实公开链接不创建 GITHUB_URL，不擅自提交、PR、发布或生成令牌。
