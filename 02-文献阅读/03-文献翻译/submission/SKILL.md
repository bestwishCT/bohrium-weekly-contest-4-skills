---
name: trans-check
description: Prepare source-segment work orders for scientific translation and audit Agent-written bilingual paragraphs for complete IDs, protected numbers/units/formulas/citations, locked glossary use, and explicit semantic checks. Use for long-paper translation, terminology control, bilingual alignment, or translation QA; never substitutes a summary or automatic paraphrase for translation.
---

# 术语锁定与段落级双语翻译核验

## 触发与职责分工

当用户要求论文翻译、术语一致、长文续译或双语段号回链时使用。Agent 负责实际阅读原文并逐段翻译；脚本只提供确定性分段、完整性/保护符/术语与显式语义确认审计，不冒充机器翻译模型，也不以字面命中宣告语义正确。

版本 `1.0.0`，Python 3.9+ 标准库；Python 3.9.6 实测 `re/json/csv` 类处理，无翻译 API、联网或第三方依赖。扫描件 OCR、图片公式识别不在已测能力，文字层缺失需先由真实工具处理并标限制。

## 输入与接口

读取 `/input/case.json`、`task.md`、实际 `source_path`、`glossary`、任务指定目标语言和 `environment/resources.yaml`。TXT 按空行形成 `p0001...`；JSON 接受原 `paragraph_id/text/section/page/block_id`，不重建原 ID。

Agent 将译文写 `/app/work/translations.json`：

```json
{"translations":[{"paragraph_id":"p1","target":"实际译文","glossary_exceptions":[],"semantic_confirmation":{"negation_checked":true,"modality_checked":true,"relations_checked":true}}]}
```

运行脚本后，过程在 `/app/work`，`bilingual.json` 与合同化 `result.json` 在 `/app/output`。安装于 `/skills/weekly-skill`；资源只使用 `scripts/common.py` 和 `scripts/workflow.py` 相对引用。

严格读取真实 `output_contract` 每个键和嵌套层级，保留 `case_id`，缺候选不擅自换 schema。业务输出 `segments/glossary/verification/failures/coverage/sources/status/uncertainty/changes`，`translation/bilingual_segments/result` 适配到段组。

## 翻译与核验流程

1. 沿原章节/段落边界建立工作单，保留跨页/跨栏定位，不把相邻段合并后无法回溯。源残缺、混语言分别标记。
2. 从实际原文和任务术语表确定译名、多义语境及例外；同一概念在标题、图注和结论使用统一译名，不能用机械替换制造错误。
3. Agent 携带前后必要上下文逐段翻译；保留指代、因果/让步、否定、限定与作者假设语气。残缺译文标具体段，不用省略号掩盖漏译。
4. 对每段抽取数字及相邻单位、方括号引用和 `$...$` 公式作为保护对象，检查译文存在且未失真；回填后仍需人工语义核对。
5. 检查每段出现术语的目标译名或显式例外；`semantic_confirmation` 三项必须由实际译者逐项检查填写，脚本明确这只是译者声明，不是独立验证。
6. 核对源段数、唯一段号、漏译/重译、保护 token、术语和状态，输出双语 JSON 并用 JSON 解析器写读自检。

## 状态与异常恢复

- `success`：所有源段有译文且合同、保护符、术语及显式语义检查无缺口。
- `partial`/退出 2：缺译、数字/单位/引用不符、术语未执行或语义检查未确认。保存已有段与具体 `paragraph_id`，先恢复最早失败段再全量复查。
- 原文未含 ≠ OCR 失败 ≠ 语义存疑 ≠ 模型未译；写入相应不确定性，绝不生成假补文。
- `trace.json` 记录版本、真实读取、失败与变更，不虚称术语一致率、DSH 或人工节省时间。

普通双语 JSON/Markdown 无需渲染；合同若要求 Office，另行真实渲染预览，缺工具则明示。

## 最小示例（合成、非正式）

源 `source.txt`：`The yield did not increase at 2 mV [1].\n\nEquation $E=mc^2$ is retained.`；glossary `yield→产率`。候选两段译文分别保留 `2 mV`、`[1]` 与 `$E=mc^2$` 及三项语义确认。

```bash
python3 /skills/weekly-skill/scripts/workflow.py --case /app/work/translation-case.json \
  --task /app/work/task.md --input-dir /app/work --work /app/work/run --output /app/output \
  --candidate /app/work/translations.json
```

示意输出：`coverage.source_paragraphs=2`、`translated=2`；`verification` 回链表内的 `2 mV`、`[1]` 和公式。示例不是正式译文质量证明。

## 自查与维护

逐键复读合同 JSON；抽检段号映射、否定/限定、比较对象、引用、数字单位和术语例外。删除错误译文后重跑应保留失败来源；规则变化必须加漏段/ token 错误回归并升版。

只有真实公开 GitHub URL 才用于新增加分文件；未取得 URL 不创建 `GITHUB_URL.txt`，不发布或提交。
