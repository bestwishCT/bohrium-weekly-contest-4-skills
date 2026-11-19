---
name: rank-papers
description: 对给定候选文献逐条执行标准驱动筛选、确定性相关性分层、稳定同分排序、保守版本去重和冲突升级。适用于候选总账、人工复核优先队列和头部排序优化，不用于数据库接入或系统综述正式纳排。
---

# 全记录候选筛选与可复核排序

## 触发条件

已有候选集、研究问题和判定标准，需要知道每条为何保留、待定或排除，以及关键文献为何排在前列时触发。目标不是生成一个神秘分数，而是让第三人可用同一标准重算标签、计数和名次。

## 正式案例接线

1. 读取 `environment/resources.yaml`、实际 `/input/case.json`、`/input/task.md`，确认 `case_id`、`output_contract`、原始标签体系与 `k`。按真实文件读取九篇署名论文、查询和全部候选，不根据领域标签或文件名补摘要。
2. 将实际筛选判断写入 `/app/work/analysis_input.json`。保留每条原始序号、来源和 fixture 属性；`search09` 合成重复/冲突意见不是现实双人评审或标准答案。
3. 若案例已有标签定义、优先级或排除条款，原样采用。模型在标题/摘要不足时不补研究对象、方法或结论；应进入待定并指出缺失。
4. 工作写 `/app/work`，结果写 `/app/output`。首先保留带状态的 `result.json`，然后检查每条输入都进入主记录或重复映射，最后才截 `top_k`。
5. 脚本从 `/skills/weekly-skill/scripts/run.py` 调用，`common.py` 由脚本位置解析。正式环境不得引用开发目录。

## 筛选、去重与排序算法

1. **候选总账**：给每条输入保留稳定键、原始 ordinal 和簇成员，检查 `input_records = canonical_records + duplicate_mappings`。
2. **证据闸门**：保留/排除建议必须带实际标题或摘要中的逐字证据片段。无证据、片段不在原文字符串内、摘要为空且无决定性证据，均降为待定，并保留原建议供审计。
3. **标准覆盖**：标签优先沿用案例；记录每条标准的 `met/not_met` 和未报告项。未报告的 criterion 不自动当满足或违反。
4. **冲突升级**：不同角色标签冲突、标注标准冲突或版本归属不清时设高复核优先级。保留全部原始意见、合成属性和理由，不平均、不投票、不虚构仲裁或第二位真人。
5. **保守身份**：同规范 DOI，或规范题名、第一作者、年份全部一致，才自动构成重复簇；仅题名相似但佐证不足者列入 `possible_duplicate_identities`。预印本/正式版或同研究多报告只能按真实 `version_relations/study_id` 表示，不能简单当独立证据或强制合并。
6. **稳定排序**：排序元组为处置层级（保留→待定→排除）、证据直接性（直接→间接→背景→未知）、明确满足标准数、有效证据数，没有小数权重或 citation 特征。元组相同者同名次，原输入 ordinal 只用于显示稳定；`k` 由实际输入指定，未指定则保留完整排序。
7. **评审队列**：冲突、证据闸门降级和信息不足分别列优先级及原因；`human_review_performed` 始终只依据事实，脚本不会擅自置真。

核心结构：

```json
{
  "criteria": {"items": [{"id": "population"}, {"id": "design"}]},
  "criteria_conflicts": [],
  "k": 10,
  "records": [{"id": "r1", "doi": null, "title": "actual title", "authors": ["actual author"], "year": 2024, "abstract": "actual abstract", "screening": {"decision": "retain", "directness": "direct", "criteria": {"population": "met", "design": "met"}, "reasons": ["criterion-linked reason"], "evidence": [{"quote": "verbatim quote", "locator": "abstract"}]}}],
  "version_relations": []
}
```

## 输出和状态

- 最终 `/app/output/result.json` 严格取实际 `output_contract` 字段并含 `case_id`；内部主结果为 `ranked_candidates/top_k/review_queue/duplicate_clusters/preserved_judgments/counts`。
- `/app/work/analysis.json` 保存全量记录，`contract_validation.json` 是映射警告清单，`run_log.json` 记录环境。必须处理警告，不能只看 JSON 可解析就宣布合同通过。
- `completed` 表示所有主记录已有经过证据闸门的处置且无不确定身份；`needs_review` 是存在待定/冲突的有效工作结果，不是程序失败；`empty` 是真实空候选。
- 排序不是科学有效性评分；待定数量、冲突和输入 fixture 限制必须写入不确定性。快照不支持 live recall 或真实双人一致率。

## 依赖与异常

仅 Python 3 标准库，安装副本合成实测版本 **CPython 3.9.6**，无第三方依赖。其他版本未实测。退出码 0/1/2 分别表示算法执行结束/保留部分结果的领域异常/基础输入无法读取。

空集不执行无意义排序；缺 DOI/年份保留空值；乱码或全文不可得不转成正常摘要；大量同分保持同名次；来源失败不应被当作排除依据；实际合同变更时先保存部分结果，再修正并复跑。

## 最小示意（虚构、非正式）

```json
{"criteria":{"items":[{"id":"population"}]},"k":1,"records":[{"id":"r1","title":"SYNTHETIC TRIAL","authors":["Demo"],"year":2024,"abstract":"Adults received demo intervention.","screening":{"decision":"retain","directness":"direct","criteria":{"population":"met"},"evidence":[{"quote":"Adults received demo intervention.","locator":"abstract"}],"reasons":["demo criterion met"]}}]}
```

```bash
python3 /skills/weekly-skill/scripts/run.py --case /input/case.json --task /input/task.md --data /app/work/analysis_input.json --input-root /input --resources environment/resources.yaml
```

在 `case_id: demo-ranking` 且合同要求 `status/results/sources/uncertainties/change_records` 时，主结果示意节为 `{"case_id":"demo-ranking","status":"completed","results":[{"record_id":"id:r1","rank":1,"decision":"retain"}]}`。这不是实际论文、正式排序或人为评审结果。

## 自查与维护

1. 重跑保持簇、计数、标签和名次稳定；按输入序号追踪每条记录，尤其看重复簇是否漏掉其原始意见。
2. 随机抽取一条保留、一条待定、一条排除，逐字核对证据及标准，不接受“相关/不相关”空理由。
3. 维护时测试空集、空摘要、缺年份、同分、同题不同文、预印本关系、标准冲突及 fixture 冲突；只有真实复测可以支持头部位置或待定区改善。
4. 仅附带实际脚本，不打包候选答案、白板、验证输出或 Benchmark。保持 UTF-8、大小和无符号链接限制。
5. 没有用户确认的真实公开 GitHub URL，则该项正式提交阻塞且不创建 `GITHUB_URL.txt`；不发布、不提交、不处理令牌。预算为 64 请求、900 秒、每响应含推理 16384 tokens。
