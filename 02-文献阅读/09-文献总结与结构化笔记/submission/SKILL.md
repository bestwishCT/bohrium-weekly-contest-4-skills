---
name: note-align
description: Turn actual paper sections into chapter-order structured notes with research question, data, methods, results, conclusions, limitations, prerequisites, condition-aware knowledge deduplication, quote anchors and a searchable index. Use for paper cards, chapter notes, reusable knowledge entries, or anchor/coverage audits instead of generic one-paragraph summaries.
---

# 章节原生对齐的可检索科研笔记

## 触发与区别

用户要求论文卡片、按章节总结或结构化笔记时使用。笔记骨架服从供应材料的实际 section ID/标题/顺序，不套 IMRaD 想象章节；缺读章节列入未覆盖。版本 `1.0.0`，Python 3.9+ 标准库，Python 3.9.6 实测，无第三方依赖。

Agent 负责沿论证顺序提炼和语义去重；脚本负责真实章节覆盖、证据短引、条件键去重和 JSON 检索导出，不把文本压缩当阅读完整证明。

## 输入与字段

读取 `/input/case.json`、`task.md`、`environment/resources.yaml` 及 `material_path`，材料需含 `sections[]`，各节有 `section_id/title/order/paragraphs[]`。

Agent 写 `/app/work/candidate.json`：

- `paper_card`：论文整体研究问题、数据、方法、主要发现和限制；
- `section_notes[]` 与原 section 一一对应，字段 `purpose/research_question/data/methods/results/conclusions/limitations/prerequisites`；
- 每节 `evidence[]` 与每个 `knowledge_items[]` 的证据均含 `paragraph_id/quote`；知识条目给 `text/dedupe_key/conditions`，必要时标作者报告、作者解释或读者推断。

安装 `/skills/weekly-skill`，相对引用 `scripts/common.py`、`scripts/workflow.py`。中间稿在 `/app/work`，`notes.json` 与 `result.json` 在 `/app/output`。真实 `output_contract` 每键逐层适配，保留 `case_id`；`notes/chapter_notes/result` 对应 `section_notes`，不自创隐藏输出键。

## 处理步骤

1. 核对真实章节 ID、标题和 order，建立每节独立 paragraph 索引；无章节结构的原 PDF 先由任务允许的真实解析步骤恢复并标不确定性。
2. 沿原顺序写节目的问题、方法、结果、结论和承接，不从结后段偷填未读前提；数值与方法条件不脱离来源。
3. 拆可检索知识项，规范缩写/同义表达；仅 `dedupe_key + conditions + text` 一致时合并并保留全部证据。key 同而条件不同必须分列；条件同而文本不同进入冲突不合并。
4. 每节和每条知识点绑定真实短引，不能仅给论文标题或猜页码。分类、意义和语义支持由 Agent 核，脚本只验精确存在。
5. 输出章节笔记、知识项与词项→索引搜索表，JSON 写后复读并核对原章节数；未总结 section 明列。
6. 逐节检查字段缺漏、条目前提、结论语气、去重和来源；压缩失败不能掩盖扫描/OCR 等问题。

## 状态与恢复

- `success`：全部实际章节有笔记且节/知识项证据通过、无未归并冲突。
- `partial`/退出 2：缺章节、错段号/短引、知识项无证据或冲突未解决；保留完成节及 `coverage.not_summarized_section_ids`。
- 文字层缺失、跨栏错序和图表公式缺失进入 uncertainty；需要读取时不能声称全文已读。
- `trace.json` 记录 JSON 复读、真实版本与变更，不伪造整理耗时或对照增益。

普通 JSON 笔记无需图形渲染；Office/图形合同另需真实预览。

## 最小示例（合成、非正式）

材料按 S1/S2 两节，S1 p1=`We measured yield under condition A.`，S2 p1=`Yield was 2 mg under condition A.`。两节均有笔记，一条知识项 `Yield measured` 的 conditions=`A`、quote=`2 mg under condition A`。

```bash
python3 /skills/weekly-skill/scripts/workflow.py --case /app/work/notes-case.json \
  --task /app/work/task.md --input-dir /app/work --work /app/work/run --output /app/output \
  --candidate /app/work/candidate.json
```

示意输出：section_notes 保持 S1→S2，coverage 未读为空，search_index 可定位知识项；不是通用摘要。

## 自查与维护

对照目录查漏节，逐条回查锚点并人工核语义；验证同概念不同条件不合并、重复条目证据不丢。修改字段/去重键增配回归并升版，不能用隐藏笔记作模板答案。

无真实 GitHub URL 不创建 `GITHUB_URL.txt`，不 commit/PR/发布。
