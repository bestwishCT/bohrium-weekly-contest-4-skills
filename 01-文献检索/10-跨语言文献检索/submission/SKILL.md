---
name: align-languages
description: 基于有来源和许可的受控术语表开展双向跨语言文献检索，核对命中原文、正文语言和译文归属，并按作品与视图分别去重计数。适用于非英语文献召回及原文译文对齐，不用于整篇翻译、虚构语种覆盖或重复版本计数。
---

# 受控跨语言检索与作品/视图归并

## 触发与边界

科研问题需要跨源语言与目标语言找回非英语工作、复核译词、区分原文和译文版本时使用。词面对应不是语义等价，回译相似不是充分证明，英文摘要也不证明英文全文。

不执行整篇翻译或写作润色，不调用未授权跨境服务，不为覆盖率生成“全语种查询”，不把同一作品的多语言视图算成不同研究。

## 正式案例读取

1. 读取 `environment/resources.yaml`、真实 `/input/case.json` 与 `/input/task.md`，取得 `case_id`、`output_contract`、双向问题、目标语种和版本限制。记录真实翻译方式；没有实际服务时写模型/受控人工术语生成，不编服务名和版本。
2. 本题实际范围是 3 个双向已知条目科研问题、3 项真实作品/6 个视图。必须逐份核查原文葡萄牙语/英语正文、出版方完整译文、仅英文摘要视图和完全重复视图；不能按文件名先验归类。
3. 将真实视图与术语写入 `/app/work/analysis_input.json`，保留来源、DOI、版本、正文语言证据、署名与翻译归属。直接解析实际 PDF，不假设答案层；受限术语表记录来源和许可。
4. `/app/work` 运行，`/app/output` 交付。先保存部分 JSON，再核对两个方向各自的查询、命中和语义证据，不能把 A→B 的证明转移到 B→A。
5. 调用 `/skills/weekly-skill/scripts/run.py`，公共模块从脚本所在位置寻找。脚本不联网、不运行外部翻译服务。

## 跨语言专属算法

1. **受控术语表**：每条保存源/目标语言与原词、目标词、变体、学科范围、歧义上下文、attested/candidate 状态、来源和许可。目标词缺失进入 `term_gaps`，不武断唯一译名。
2. **查询计划与实绩分开**：根据目标词和变体生成 `query_candidates`，只是未执行计划。真正 `language_queries` 必须来自实际执行/快照，记录方向、语种、expression、term_ids、source、response locator 和执行的翻译方式。
3. **视图识别**：分别保存 metadata、abstract、body language 和 full-text 可用性。abstract-only 无正文语言证据时 body language 强制 unknown；出版方 full translation 缺 attribution 要告警；语言不从界面文字猜。
4. **重复核验**：重复视图只有 `duplicate_of` 指向真实视图且供给的 `content_sha256` 或实际规范化文本 SHA-256 一致时才确认 exact duplicate。各视图来源仍保留，仅从 nonduplicate view 统计中排除。
5. **作品归并**：按显式 work_id 或规范 DOI 建立 work registry。相同 DOI 却映射不同 work_id 会形成 identity conflict，不静默改写。原文、译文、摘要视图都保存在正确作品下。
6. **原文语义对齐**：alignment 同时给 source_view/target_view、逐字 source quote/target quote、实际 locator 和 aligned/divergent/uncertain。两 quote 都必须在相应视图文本出现；词面命中无有效 aligned evidence 时只能 `lexical_hit_semantics_unverified`，不进入确认作品结果。
7. **分别计数**：view hits 保留每个真实命中，work hits 按作品去重，并保存支持该作品的视图和两个方向证据。只有确认语义对齐的视图参与 confirmed work count；误译、版本冲突和未知视图均保留原因。
8. **稳定性**：同输入保持术语、视图指纹、命中状态和作品归属稳定；新增语种或新作品必须有实际材料，不能外推未知语种能力。

核心结构：

```json
{
  "terms": [{"id": "t1", "source_language": "pt", "source_term": "actual source term", "target_language": "en", "target_term": "attested target term", "variants": [], "status": "attested", "source": "actual glossary", "license": "actual license"}],
  "views": [{"id": "v1", "work_id": "work-a", "doi": null, "view_type": "original", "metadata_language": "pt", "abstract_language": "pt", "body_language": "pt", "body_language_evidence": "body.pdf#page1", "searchable_text": "actual original text", "attribution": "actual author", "source_locator": "body.pdf#page1"}],
  "queries": [{"id": "pt-en", "source_language": "pt", "target_language": "en", "direction": "pt->en", "term_ids": ["t1"], "expression": "actual query", "executed": true, "status": "success", "hits": ["v2"], "response_locator": "query#1"}],
  "alignments": [{"term_id": "t1", "source_view_id": "v1", "target_view_id": "v2", "source_quote": "actual original phrase", "target_quote": "actual translated phrase", "semantic_status": "aligned", "source_locator": "v1#page1", "target_locator": "v2#page1"}]
}
```

## 输出与状态

- `/app/output/result.json` 按真实合同生成且含 `case_id`；内部为 `controlled_terms/query_candidates/language_queries/view_hits/work_hits/views/work_registry/duplicate_views/alignment_conflicts/counts`。
- `/app/work/analysis.json` 保存完整视图/语义审计，`contract_validation.json` 与 `run_log.json` 保存合同警告及环境。
- `completed` 表示实际命中均有验证的语义对齐且没有版本/语言异常；`partial` 表示候选译词、未知正文语言、未验证命中或作品冲突；`empty` 是没有视图输入。没有真实译法或检索命中不补造。
- confirmed works、confirmed views 和 nonduplicate views 是不同口径，报告时不可混用。不能声称 live coverage、未见语种实测或创造新研究版本。

## 依赖与降级

仅 Python 3 标准库，合成安装副本实测 **CPython 3.9.6**，未实测其他版本。无翻译服务、网络或包依赖。退出码 0=算法执行结束，1=部分结果伴随异常，2=case/task 读取失败。

无学科对应译法保留候选和释义性查询计划；语言不确定则 unknown；翻译失败保存源词和未完成查询；原文不可得只评价可见元数据/摘要；原文与译文冲突并列归属，不静默修正出版译文。

## 最小示意（虚构非正式）

```json
{"terms":[{"id":"t1","source_language":"pt","source_term":"caminhada","target_language":"en","target_term":"walking","status":"attested","source":"demo-glossary","license":"synthetic fixture"}],"views":[{"id":"v-pt","work_id":"demo-work","view_type":"original","metadata_language":"pt","abstract_language":"pt","body_language":"pt","body_language_evidence":"demo#pt","searchable_text":"caminhada sintética","attribution":"Demo author","source_locator":"demo#pt"},{"id":"v-en","work_id":"demo-work","view_type":"publisher_full_translation","metadata_language":"en","abstract_language":"en","body_language":"en","body_language_evidence":"demo#en","searchable_text":"synthetic walking","attribution":"Demo author","translation_attribution":"Demo publisher translator","source_locator":"demo#en"}],"queries":[{"id":"q1","source_language":"pt","target_language":"en","direction":"pt->en","term_ids":["t1"],"expression":"walking","executed":true,"status":"success","hits":["v-en"],"response_locator":"demo#query"}],"alignments":[{"term_id":"t1","source_view_id":"v-pt","target_view_id":"v-en","source_quote":"caminhada","target_quote":"walking","semantic_status":"aligned","source_locator":"demo#pt","target_locator":"demo#en"}]}
```

```bash
python3 /skills/weekly-skill/scripts/run.py --case /input/case.json --task /input/task.md --data /app/work/analysis_input.json --input-root /input --resources environment/resources.yaml
```

若 `case_id: demo-language`，结构示意节选为 `{"case_id":"demo-language","status":"completed","results":[{"work_id":"demo-work","supporting_view_ids":["v-en"],"directions":["pt->en"]}]}`。该示例不是实际葡萄牙语论文、真实出版译文或翻译服务结果。

## 自查与维护

- 从每个确认 work 追到支持 view、每个方向 query 和两侧原文 quote；检查 attribution、版本及正文语言证据。
- 分别复算输入 views、完全重复 views、非重复 views、注册 works、确认 view hits、确认 works；不得用同一数字表示所有口径。
- 维护时测试无对应译法、candidate 术语、语种不明、摘要视图、翻译归属缺失、词面命中但语义分歧和 DOI/work 冲突。
- 任何术语表修改都保留来源/许可与译文版本，使用同输入复跑确认真正召回变化，不构造非英语召回指标。
- 仅提交 Python 与本文，保持 UTF-8、无符号链接，不附真实视图答案或烟测。无真实公开 GitHub URL 时正式提交该项阻塞且不创建 `GITHUB_URL.txt`；不发布、不提交、不处理令牌。预算为 64 请求、900 秒、每响应含推理 16384 tokens。
