---
name: appraise-quality
description: 按研究设计适配的质量量表，对实际论文执行原文证据定位、偏倚分级、报告完整性检查和缺证据弃权。适用于需要逐条复核研究质量而非摘要泛评的任务，不用于同行评审替代、临床政策建议或虚构统计与评分。
---

# 原文证据闸门下的研究质量评价

## 使用范围和限制

给定真实论文、研究类型和质量量表，需要完整覆盖研究设计、证据充分性、偏倚风险、报告完整性时使用。每条判断先找决定性原文，再评级；期刊声望、引用量和作者资历不参与质量评分。

仅在摘要可见时评价摘要能支撑的条目，其余 `not_assessable`。原文未报告不等于未实施，未见偏倚迹象不等于没有偏倚。

## 正式案例启动

1. 读 `environment/resources.yaml`，再读 `/input/case.json`、`/input/task.md`，提取真实 `case_id`、原始 `output_contract`、指定量表和研究类型。逐份读取实际九份科学文档、响应和质量工作流，不凭文件名或错误领域标签判断设计。
2. 使用资源中实际可用工具直接解析 PDF，记录页码、章节、表/图编号和不可解析位置。不能把 OCR 不清或缺图的数值写成已知，不能假定存在预提取答案层。
3. 制作 `/app/work/analysis_input.json`，保存量表版本、条目、阈值和证据评价。若案例有指定量表就逐字沿用；未指定时选择适配设计的最小检查框架，并显式标为辅助，不冒称公认标准。
4. `/app/work` 放抽取，`/app/output` 放最终 JSON。先保存部分评价，再检查所有必需维度和关键条目，最后核验合同。
5. 通过 `/skills/weekly-skill/scripts/run.py` 调用；`common.py` 从安装位置 `/skills/weekly-skill/scripts` 自动加载，无私人目录依赖。

## 量表驱动评价算法

1. **适用性**：`scale.study_designs` 和条目 `applicable_designs` 必须与实际 `study_design` 一致；不兼容时停止强套量表并进入复核。
2. **必需维度**：量表 items 必须映射 `research_design/evidence_sufficiency/bias_risk/reporting_completeness` 四维；缺维时整体 `indeterminate`，不得用高分条目替代。
3. **逐字证据**：每条 appraisal 提供 item ID、rating、rationale 和 evidence。quote 经空白规范化后必须在声明 section/page 的实际文本中找到；若 locator 位于 `unreadable_locators` 或页码不符，则该条证据无效。
4. **可解释弃权**：rating 不在条目允许枚举、量表不适用或证据无效时，最终条目改为 `not_assessable`，同时保留 `requested_rating` 和引用失败原因。数值不得从常识补齐，不以摘要复述代替方法检查。
5. **偏倚语义**：`explicitly_present/possibly_present/no_indication/insufficient_evidence` 分开保存；缺证据不会自动变成 no_indication。统计值、样本和结局只引用实际原句/表项，不把重算称为原文结果。
6. **声明式汇总**：支持量表给定顺序规则，包括 critical/any ratings include、all_valid_rated、max_invalid_items 和 default。按首条匹配规则产生维度/整体等级；没有匹配规则就不发明阈值。
7. **关键证据优先**：任何必需维度缺失或 critical 条目不可评价，强制整体 indeterminate，即使其他规则会给出更高等级。复核队列记录缺证据、图表不可读、适用性和冲突，人工是否复核只依实际事实。

核心输入：

```json
{
  "scale": {"id": "actual-scale", "version": "1.0", "study_designs": ["randomized_trial"], "items": [{"id": "allocation", "dimension": "research_design", "question": "actual checkpoint", "critical": true, "allowed_ratings": ["low_risk", "some_concerns", "high_risk", "not_assessable"]}], "aggregation": {"rules": [{"any_ratings_include": ["not_assessable"], "grade": "indeterminate"}, {"all_valid_rated": "low_risk", "grade": "low"}, {"default": true, "grade": "indeterminate"}]}, "dimension_aggregation": {}},
  "studies": [{"id": "paper-a", "study_design": "randomized_trial", "access": "full_text", "sections": [{"section": "Methods", "page": 3, "text": "actual extracted text"}], "unreadable_locators": ["Figure 2"], "appraisal": [{"item_id": "allocation", "rating": "low_risk", "rationale": "rule-linked reason", "evidence": [{"section": "Methods", "page": 3, "quote": "verbatim decisive phrase"}]}]}]
}
```

一个完整案例的量表仍需为另外三维配置条目；脚本会检查而非默认通过。

## 输出、状态与不确定性

- `/app/output/result.json` 含实际 `case_id` 和合同字段；内部有 `quality_evaluations`，每项含 `quality_items/dimensions/overall_grade/overall_basis/manual_review_queue`。
- `/app/work/analysis.json` 保存 requested rating 与最终 rating、引用匹配细节和放弃原因，`contract_validation.json`/`run_log.json` 存合同与环境。
- `completed` 表示材料支撑全部必需评价且声明汇总规则可闭合；`partial` 表示维度缺失、证据无效、关键弃权或适用性问题；`empty` 是未提供研究。
- 结果不是同行评审、事实核验、临床/政策决策或作者结论。没有第二评价者时只给复核建议，不生成一致率或人工裁决。

## 依赖及降级

仅需 Python 3 标准库，已用合成安装副本在 **CPython 3.9.6** 实测。其他版本未实测；无第三方包、远程模型或 PDF 引擎依赖。PDF 引擎须由案例资源单独实际提供。

退出码 0=算法完成（可 partial），1=保留部分结果的领域异常，2=输入文件失败。仅摘要/全文不可得缩小评价范围；图表不可读时保留表项定位；类型不明不强套；证据冲突并列原位置；预印本状态不从常识推断。

## 最小示意（虚构，非真实论文）

```json
{"scale":{"id":"demo-scale","version":"1","study_designs":["demo_design"],"items":[{"id":"d1","dimension":"research_design","question":"demo","critical":true},{"id":"d2","dimension":"evidence_sufficiency","question":"demo","critical":false},{"id":"d3","dimension":"bias_risk","question":"demo","critical":false},{"id":"d4","dimension":"reporting_completeness","question":"demo","critical":false}],"aggregation":{"rules":[{"any_ratings_include":["not_assessable"],"grade":"indeterminate"},{"all_valid_rated":"low_risk","grade":"low"}]}},"studies":[{"id":"demo-paper","study_design":"demo_design","access":"full_text","sections":[{"section":"Synthetic","page":1,"text":"Design sentence. Outcome sentence. Bias sentence. Report sentence."}],"appraisal":[{"item_id":"d1","rating":"low_risk","evidence":[{"section":"Synthetic","page":1,"quote":"Design sentence."}]},{"item_id":"d2","rating":"low_risk","evidence":[{"section":"Synthetic","page":1,"quote":"Outcome sentence."}]},{"item_id":"d3","rating":"low_risk","evidence":[{"section":"Synthetic","page":1,"quote":"Bias sentence."}]},{"item_id":"d4","rating":"low_risk","evidence":[{"section":"Synthetic","page":1,"quote":"Report sentence."}]}]}]}
```

```bash
python3 /skills/weekly-skill/scripts/run.py --case /input/case.json --task /input/task.md --data /app/work/analysis_input.json --input-root /input --resources environment/resources.yaml
```

若 `case_id: demo-quality`，结构示意节选为 `{"case_id":"demo-quality","status":"completed","results":[{"study_id":"demo-paper","overall_grade":"low"}]}`。所有句子和等级均为虚构单元示意，不是科学评价。

## 自查与维护

- 每条评价逐项核对研究类型、原句、页码/章节、偏倚语义、维度、汇总规则和最终等级；无效 quote 必须弃权。
- 复跑保持相同维度覆盖与等级。改量表/规则时保存前后真实版本，不能用摘要扩写或自造阈值宣称改进。
- 维护必测错页、错句、仅摘要、不可读表、类型不匹配、关键项缺失、四维缺项和证据冲突；量表变化同步更新实例。
- 资源仅含必要 Python 和本文，UTF-8、无符号链接，不附评价答案、白板或验证输出。
- 无真实公开 GitHub URL 时正式提交该项阻塞且不创建 `GITHUB_URL.txt`；不发布、不提交、不处理令牌。预算为 64 请求、900 秒、每响应含推理 16384 tokens。
