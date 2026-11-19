---
name: qa-answer
description: Answer every supplied scientific-literature question using only provided numbered paragraphs, with per-question status, conditions, reported/inferred claims, exact evidence quotes, unanswered parts, and coverage. Use for paper QA, multi-section evidence synthesis, source-bound answers, or insufficient-evidence handling; excludes web search and PDF-pixel interpretation.
---

# 段落限定的文献智能问答

## 触发与证据边界

当输入给出带段落号的论文摘要、正文、表格文字或图注及问题清单，需要逐题回答并回链时使用。只用供应科学段落，不读取网络、外部论文或常识补足未报告内容，也不把本题改造成 PDF 像素识别。最新独立适配要求 Skill 和 `/app/output/result.json`，覆盖旧提示中不制作 Skill 的过时表述。

版本 `1.0.0`，Python 3.9+ 标准库；Python 3.9.6 实测。无额外依赖、模型或联网调用。语义支持由 Agent 通读判断，脚本只验 schema、问题覆盖和 quote 精确存在，不宣称字面命中等同蕴含。

## 实际输入与输出键

读取 `/input/case.json`、`task.md`、`environment/resources.yaml`，识别 `case_id/paper_id/material_version`、paragraphs/questions；如正文另由 `material_path` 给出则完整读取。任务子问逐项拆入 `/app/work/question-plan.json`。

Agent 将真实回答写 `/app/work/answers.json`。每个 `answers[]` 恰有：

- `question_id`、`answer`、`conditions[]`；
- `status` 仅为 `answered/partial/insufficient_evidence/input_error`；
- `claims[]` 每项含 `claim_id/text/kind/evidence[]`，kind 仅 `reported/inferred`；
- 每条证据 `paragraph_id/quote`，quote 是该段连续原文字符串；
- `unanswered_parts[]`（完整回答为空）；顶部 `coverage` 说明实际已读和未读。

运行后合同化结果写 `/app/output/result.json`，过程追踪在 `/app/work`。安装路径 `/skills/weekly-skill`，只相对引用 `scripts/common.py`、`scripts/workflow.py`。真实 `output_contract` 的字段/数组项/JSON Schema 逐键适配，强制保留 `case_id` 与输入 paper/material ID，业务别名 `responses/result` 可指向 `answers`，不发明未知层级。

## 问答专属流程

1. 建“问题—子问—所需证据”清单。逐个通读方法、结果、讨论、表格和图注；跨段综合保留各自段落，不混实验条件。
2. 数值、单位、比较对象、统计限定原样核回。作者实测/解释标 `reported`，自己的综合标 `inferred` 并说明推理边界。
3. 每个输入问题恰一项，无法回答也保留；`insufficient_evidence` 仅在确实检索过供应范围后使用，不能回避已存在答案。
4. 冲突时并列各段和条件，不挑“合理”的一方。材料未报告可以是有效回答，但需说明核查范围。
5. 先尽早在 `result.json` 保存完整结构的部分答案，再逐题补强。脚本拒收重复 ID、额外问题、无证据 answered/partial、非法枚举和错引 quote。
6. 最后逐条读回 `answers`，比对问题数、条件、未答项与来源；`coverage` 必须反映真实已读，不能默认全文读完。

## 状态与失败恢复

- `success`：问题全覆盖、ID/枚举/quote 和证据结构通过。
- `partial`/退出 2：候选缺题、虚假 quote、无证据回答等；错误条目标 `input_error` 并在 `unanswered_parts` 记录原因，已有其他回答保留。
- `input_error`：材料读取/格式故障，区别于内容不足。
- 新预算下匹配白板与实际 DSH 验收未由本技能证明，不生成伪增益或分数。

本题无 Office/图形主产物，不虚称渲染；合同额外要求时才生成并真实预览。

## 最小示例（合成、非正式）

段落 `p1="The control yielded 2 mg under condition A."`，问题 `q1` 问对照产量。候选答案写 2 mg、条件 A，claim kind 为 reported，证据 quote 为 `"2 mg under condition A"`。

```bash
python3 /skills/weekly-skill/scripts/workflow.py --case /app/work/qa-case.json \
  --task /app/work/task.md --input-dir /app/work --work /app/work/run --output /app/output \
  --candidate /app/work/answers.json
```

示意输出：`answers[0].question_id=q1`，`status=answered`，`coverage.input_question_count=1`；可沿 paper_id/p1 回原文。此为合成结构示例。

## 自查与维护

JSON 复读，逐键核对原合同，确保 answered 不再带未答项、partial/insufficient 不夸大、coverage 与实际读取一致。新增题型须补缺失材料、冲突、缺 quote 回归；修改字段绑定需升版。

GitHub 链接加分需真实 URL；无已验证链接不创建 `GITHUB_URL.txt`，不提交或发布。
