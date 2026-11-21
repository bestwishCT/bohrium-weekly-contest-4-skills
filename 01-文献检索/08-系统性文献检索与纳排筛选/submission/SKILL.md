---
name: audit-screening
description: 为系统性文献检索建立版本化纳排标准、真实片段理由码、记录与研究层级去重、独立判断分歧仲裁和阶段审计链。适用于可复跑的系统综述题录筛选，不用于虚构双人评审、替代研究者终审或自动质量评价。
---

# 版本化系统检索与纳排审计

## 触发与科学边界

用户需要从检索协议到题录去重、标题摘要纳排建议、冲突仲裁和计数审计的一条链时使用。最终科学判断必须由实际研究者确认；没有真实人工记录时，输出永远只是机器建议或待确认状态。

不开展全文偏倚评价、数据提取、 meta 分析或论文写作；不能把同一研究多篇报告当独立证据，也不能用平均分、多数票或单方覆盖解决分歧。

## 正式输入流程

1. 读取 `environment/resources.yaml`、真实 `/input/case.json` 与 `/input/task.md`，取得 `case_id`、原始 `output_contract`、研究问题、现行标准版本、已有角色判断和审计要求。
2. 读取九篇署名论文、实际查询输入和全部筛选记录。`search09` 合成重复导出和冲突意见只作 fixtures；保留合成属性，不能包装成真实双人判断或仲裁历史。
3. 在 `/app/work/analysis_input.json` 整理实际标准和题录。查询只记录真实执行式、字段、时间和定位，并以记录侧 `query_id`（或查询侧 `hits`）保留真实查询—命中关系；每条 assessment 保留原角色、版本、理由码和证据片段。摘要缺失/乱码不能自动批准排除。
4. 中间结果写 `/app/work`，最终结果写 `/app/output`。先保存部分结果，再从原始导出数逐步核对到唯一记录、建议、仲裁和确认。
5. 在 `/skills/weekly-skill/scripts/run.py` 运行算法，依赖的 `common.py` 从脚本所在位置解析；任何私有标注文件都不能成为隐含依赖。

## 审计链专属算法

1. **标准与理由码**：固定当前 `criteria.version` 和 `reason_codes`。每条判断必须声明使用的版本；历史 version 不匹配时触发 `needs_reassessment` 和 `standard_version_backlog`，绝不暗中选有利版本。
2. **记录/研究分层**：规范 DOI 或题名+第一作者+年份仅合并同记录副本，保留每个导入 ID；`study_id` 另用于同研究的多个报告。自动簇和显式报告关系不可混用。
3. **证据核验**：include/exclude 判断的理由码必须在标准字典中，且 evidence quote 经空白规范化后逐字存在于该 assessment 所属输入记录的声明 locator；不得借用同簇其他副本的文本。无检索命中关系、证据、错片段、未知理由码或版本不符不能进入最终决定，但其原判断永不删除。
4. **独立角色**：保留每个输入角色和 fixture 标签。角色只是输入声明，脚本不验证身份，也不把 machine-pass 写成真人。重复导出的无独立判断记录不会放大一致意见。
5. **分歧分类**：直接标签不一致为 `label`，排除理由码不同为 `reason_code`；标准版本另列回溯项。有效分歧保持 review，除非真实输入 arbitration 给出当前版本、明确决定、理由和可在原文定位的 quote。
6. **仲裁与终审**：只有明确且通过证据核验的仲裁可解决建议冲突，仍保留 `conflict_type` 和原双方判断。`researcher_confirmations` 单独核验记录，缺省时每条是 `awaiting_actual_researcher_confirmation`。
7. **计数闭合**：至少审计 import→deduplicate→recommend→researcher_confirmation。强制 `imported = canonical + duplicate mappings`，输出每阶段角色、输入输出数、冲突数、有效仲裁数与真实确认数。

核心结构：

```json
{
  "criteria": {"version": "criteria-v2", "reason_codes": {"POP": {"description": "wrong population"}, "OK": {"description": "meets criteria"}}},
  "criteria_history": [{"version": "criteria-v1", "change": "actual change"}],
  "queries": [{"id": "q1", "source": "actual source", "expression": "actual expression", "fields": ["title", "abstract"], "executed": true, "status": "observed", "search_date": "actual date", "response_locator": "response#1"}],
  "records": [{"id": "r1", "query_id": "q1", "study_id": "study-a", "doi": null, "title": "actual title", "abstract": "actual abstract", "assessments": [{"role": "actual input role or machine-pass-a", "label": "include", "reason_code": "OK", "criteria_version": "criteria-v2", "evidence": [{"quote": "verbatim quote", "locator": "page/section"}]}]}],
  "arbitrations": [],
  "researcher_confirmations": []
}
```

## 结果、状态和证据

- `/app/output/result.json` 使用真实 `case_id` 与实际合同；内部主结果为 `screening_ledger/preserved_assessments/duplicate_clusters/arbitration_backlog/standard_version_backlog/stage_counts/stage_audit`。
- `/app/work/analysis.json` 保留无法通过验证的输入判断、理由和版本，`contract_validation.json` 与 `run_log.json` 记录环境。真实查询日志不是数据库执行能力证明。
- `needs_human_confirmation` 是非空题录的正常终审边界；`empty` 是空题录。即使程序退出 0 也必须披露待仲裁、回溯和人工终审。
- fixture、角色声明、实际仲裁与真实确认分栏呈现。没有真人就不得输出一致率变化、签名、审核时间或最终科学纳排。

## 依赖和异常降级

仅 Python 3 标准库；合成安装副本实测 **CPython 3.9.6**，没有其他包。未验证其他 Python 版本。退出码 0=算法执行结束，1=保留部分结果的领域异常，2=case/task 输入失败。

DOI 缺失不猜；强相似题名不强制合并且列待复核；空摘要进入能力受限；旧标准冻结相关判断；僵持保留双方依据；无实际确认不写 final scientific conclusion；合同不兼容时保存已有真实日志后修复。

## 最小示意（虚构，非双人评审）

```json
{"criteria":{"version":"demo-v1","reason_codes":{"OK":{}}},"queries":[{"id":"q1","source":"demo-db","expression":"demo","executed":true,"status":"observed","response_locator":"demo-response#1"}],"records":[{"id":"r1","query_id":"q1","title":"SYNTHETIC RECORD","abstract":"Adults received demo treatment.","assessments":[{"role":"machine-demo","label":"include","reason_code":"OK","criteria_version":"demo-v1","evidence":[{"quote":"Adults received demo treatment.","locator":"abstract"}]}]}],"arbitrations":[],"researcher_confirmations":[]}
```

```bash
python3 /skills/weekly-skill/scripts/run.py --case /input/case.json --task /input/task.md --data /app/work/analysis_input.json --input-root /input --resources environment/resources.yaml
```

若 `case_id: demo-screen`，示意结果节选为 `{"case_id":"demo-screen","status":"needs_human_confirmation","results":[{"record_id":"id:r1","recommendation":"include","scientific_confirmation_status":"awaiting_actual_researcher_confirmation"}]}`。这不是系统综述结论或真实场景。

## 自查与维护

- 任取每条记录复走标准版本→证据→理由码→原始角色→分歧→仲裁/确认，反向也能从最终数回到导入清单。
- 同输入复跑计数和标签稳定，版本变化应有明确受影响记录，不得只改新数据而不回溯旧决定。
- 维护时测试缺 DOI、空摘要、乱码、未知理由码、错引文、旧版本、双人僵持、同研究多报告、空输入和合同变化。
- 只提交两个脚本和本文；保持 UTF-8、无符号链接，不附伪造审计日志、测试输出或正式答案。
- 未获真实公开 GitHub URL，则正式提交该项阻塞且不创建 `GITHUB_URL.txt`；不发布、不提交、不处理令牌。执行预算为 64 请求、900 秒、每响应含推理 16384 tokens。
