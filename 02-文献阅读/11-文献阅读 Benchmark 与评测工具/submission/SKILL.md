---
name: bench-check
description: Process four source-conditioned benchmark-DRAFT workorder items with derived task cards, dual source/output quote evidence, 0/1/2 deterministic checks, task-type exclusions, scoped follow-up proposals and version traceability. Use for literature-reading dataset checks, score chaining, raw-output audit, or benchmark maintenance; synthetic checker results are not DSH or independent semantic validation.
---

# 来源约束的文献阅读 Benchmark DRAFT 检查器

## 触发与评分边界

用于 revision 2 的四项 `benchmark-DRAFT` 工单、原始输出管理、规则检查及失败归因。提供材料只是段落/表格文字，不是 PDF 像素 gold。技术检查日志不得更名 DSH 成绩、基础 Agent 对照或独立人工语义验证。公开科学来源字节不改写，缺参考答案不可强判。

Skill 实现版本 `1.1.0`，Python 3.9+ 标准库，Python 3.9.6 实测 JSON/UTF-8/SHA-256/复读接口。无外部评测框架依赖；本 runner 不调用 Agent/DSH，只处理已经存在的真实输出。工单自身的 rubric_version 原样保留，不因 Skill 实现升版而改写。

## 输入、题卡和输出

读取 `/input/case.json`、`task.md`、`environment/resources.yaml` 和 `benchmark-workorder.json`。工单 `status=benchmark-DRAFT` 且恰有四个唯一 question IDs；每项含 task、source_path、output_path、reference_answer、output_format 和版本配置。

运行配置保留 prompt_version、工具权限、budget、benchmark version、工单 SHA 和原始 output path/hash。实际输出缺文件、空、JSON 格式错或超长都是 `execution_failed`，score=null；reference 缺失是 `unscorable_reference_missing`，也不把流程故障默认记 0。

安装 `/skills/weekly-skill`，相对引用 `scripts/common.py`、`scripts/workflow.py`。过程 `/app/work`，结果与 `scores.json` 在 `/app/output`。真实 `output_contract` 每层键适配并保留 `case_id`；`scores/results/result` 可映射 `checks`。只有原合同要求的原始输出、逐题依据及汇总进入主产物。

## 评测专属流程

1. 每项 `task_card` 记录 task、实际 source/output 路径与 SHA-256、reference_answer 是否提供及其在工单中的 JSON 定位、实际 reference 内容、rubric_version、task_type、difficulty、data_license。task_type/difficulty 缺省为 `not_reported`；授权优先 item/workorder 实际 data_license，再取 license，仍缺则 `not_reported`，绝不推断。逐项读取且不修改 source bytes，不补页码或图片。
2. 读取原始输出；UTF-8/空/长度/JSON 结构先验收，失败与内容低分隔离。输出文件即使为空，只要可读也保留路径和 SHA；文件不存在等无法取得的哈希留空，不伪造。
3. 仅在 reference 存在时，按工单版本化 `required_tokens/forbidden_tokens/source_quotes` 做确定性检查。已验证 quote 必须同时逐字出现在来源字节和实际参与评分的输出文本；每条 `source_quote_evidence` 分开保存 found_in_source、used_in_output、对应零基半开字节/字符范围及 verified，只有两者均真才进入 `verified_source_quotes`，不作语义推断。
4. 该评分版本每档定义随 `rubric.score_definitions` 保存：2=无违禁 token、非空 required_tokens 全部命中且所有 quote 均在来源与输出双端验证；1=无违禁 token、至少一个必需 token 命中但未完全满足 2 分；0=出现违禁 token，或没有任何必需 token 命中。流程失败和缺 reference 不在档内，score=null。`scoring_reason` 逐题记录实际命中、漏项、违禁项、双端 quote 数与得分。
5. 每项强制 `manual_semantic_review_required=true` 与 checker 标签；自动分数不证明回答事实正确，也不宣称与人工一致。
6. 以 question_id 串起 raw output→run config→source hash→逐题检查→failure。summary 只对 technical_check_completed 的 score 求和，失败/不可判另计且排除；`by_task_type` 按实际 task_type 聚合，未提供者明确进 `unclassified`，分别记录 scored、excluded、score_null、execution_failed、reference_missing。单次汇总不是重复试验波动证据，跨组/历史比较必须保持题、答案、规则和评测对象版本可比。
7. execution_failed、reference_missing、0/1 低分各自生成实际 `follow_up_recommendations`；建议包含观察证据、后续专项题方向、精确 question_ids/reason/recompute/precondition 的 `rescore_scope`，并明示未自动修复、未完成人工复核。没有触发项时数组为空，不生成无证据建议。
8. `version_changes` 保留来源、签名、rubric、实际 rescore_scope；任务、答案、规则和评测对象版本不变时，只可重算受影响项和依赖汇总，不可改历史来源或科学材料，旧结果仅在版本匹配时比较。
9. `scores.json` 通过 `json.load` 复读且保留四项，再写合同 result。新增信息只置于原合同已有 checks、summary、version_changes，不创建臆测的公开顶层字段。

## 状态和恢复

- `success`：四项均完成技术检查，可含内容为 0 的真实低分，但无执行失败或缺答案。
- `partial`/退出 2：空/损坏输出、格式失败、reference 缺失等；其他题保留分数，失败项 score 为空。
- 文件权限/服务故障另行记录，不能改作科学错误；待语义复核不当作已人工审核。
- `trace.json` 明示 scope 为 `technical synthetic checks only`，不生成伪 DSH/白板。
- 按合同记录不确定性：自动检查未覆盖的语义、缺参考答案、版本不可比和待人工复核项须单列，无法判断不折算成 0 分或运行失败。

纯文本面板必须真实可运行；若另生图形/Office，需要真实预览，不产伪截图。

## 最小示例（合成、非正式）

四题各有文本 source 和实际输出；reference 要求必需 token 与源 quote，输出 A 全中、B 部分、C 全缺、D 全中，rubric 版本 r1。

```bash
python3 /skills/weekly-skill/scripts/workflow.py --case /app/work/benchmark-case.json \
  --task /app/work/task.md --input-dir /app/work --work /app/work/run --output /app/output
```

示意输出：四个 question_id 的 2/1/0/2 技术检查，summary.score_sum=5，所有项仍 `manual_semantic_review_required=true`；这不是 DSH 总分。

## 自查与维护

核对四项完整题卡、来源/输出哈希、正常项 `failure=null`、失败 score=null、双端 quote 证据、0/1/2 逐题理由、task_type 分组与 score_null 排除、低分/失败建议及精确重算范围、语义复核和版本可比性。正常合成基线必须保持 2/1/0/2。回归须覆盖空输出、缺答案停判、quote 仅在来源而未用于输出、元数据缺失不猜测；修改工单 rubric 时升版并只重跑受影响范围。

无真实 GitHub URL 不创建 `GITHUB_URL.txt`，不发布或提交。
