---
name: bench-check
description: Process four source-conditioned benchmark-DRAFT workorder items with preserved source/output hashes, run configurations, 0/1/2 deterministic technical checks, execution-failure separation and version traceability. Use for literature-reading dataset checks, score chaining, raw-output audit, or benchmark maintenance; synthetic checker results are not DSH or independent semantic validation.
---

# 来源约束的文献阅读 Benchmark DRAFT 检查器

## 触发与评分边界

用于 revision 2 的四项 `benchmark-DRAFT` 工单、原始输出管理、规则检查及失败归因。提供材料只是段落/表格文字，不是 PDF 像素 gold。技术检查日志不得更名 DSH 成绩、基础 Agent 对照或独立人工语义验证。公开科学来源字节不改写，缺参考答案不可强判。

版本 `1.0.0`，Python 3.9+ 标准库，Python 3.9.6 实测 JSON/CSV 类接口、UTF-8、哈希及复读。无外部评测框架依赖；本 runner 不调用 Agent/DSH，只处理已经存在的真实输出。

## 输入、题卡和输出

读取 `/input/case.json`、`task.md`、`environment/resources.yaml` 和 `benchmark-workorder.json`。工单 `status=benchmark-DRAFT` 且恰有四个唯一 question IDs；每项含 task、source_path、output_path、reference_answer、output_format 和版本配置。

运行配置保留 prompt_version、工具权限、budget、benchmark version、工单 SHA 和原始 output path/hash。实际输出缺文件、空、JSON 格式错或超长都是 `execution_failed`，score=null；reference 缺失是 `unscorable_reference_missing`，也不把流程故障默认记 0。

安装 `/skills/weekly-skill`，相对引用 `scripts/common.py`、`scripts/workflow.py`。过程 `/app/work`，结果与 `scores.json` 在 `/app/output`。真实 `output_contract` 每层键适配并保留 `case_id`；`scores/results/result` 可映射 `checks`。只有原合同要求的原始输出、逐题依据及汇总进入主产物。

## 评测专属流程

1. 建题卡：任务、实际来源、参考答案/依据、rubric、难度/授权（实际提供者）；逐项读 source bytes 并 SHA-256，不能补页码或图片。
2. 读取原始输出；UTF-8/空/长度/JSON 结构先验收，失败与内容低分隔离。
3. 在有 reference 时，按版本化 `required_tokens/forbidden_tokens/source_quotes` 做确定性 0/1/2 技术检查：全部必需且源 quote 验证给 2，部分给 1，否则给 0，违禁 token 给 0；保留匹配、漏项和理由。
4. 每项强制 `manual_semantic_review_required=true` 与 checker 标签；自动分数不证明回答事实正确，也不宣称与人工一致。
5. 以 question_id 串起 raw output→run config→source hash→逐题检查→failure。summary 只对 technical_check_completed 的 score 求和，失败/不可判另计且排除。
6. `version_changes` 记录来源与题/答案/规则/Agent 签名；只有任务、答案、规则和评测对象匹配的历史结果才可比较，受影响项可重算但不改旧科学材料。
7. `scores.json` 通过 `json.load` 复读且保留四项，再写合同 result。

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

核对四字题卡、来源/输出哈希、失败 score=null、聚合排除、语义复核及版本可比性。修改 rubric 必须升版，只重跑受影响题；补空输出、错 JSON、缺答案回归。

无真实 GitHub URL 不创建 `GITHUB_URL.txt`，不发布或提交。
