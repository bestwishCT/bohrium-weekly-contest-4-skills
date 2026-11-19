---
name: plan-search
description: 面向有限真实来源的多轮科研文献检索与可追溯筛选。适用于需要拆解主题、审计逐轮查询、合并重复候选并解释排序和遗漏的任务；不用于实时数据库覆盖、单篇精读或预写检索答案。
---

# 多轮来源约束文献检索

## 触发与边界

当用户需要围绕科研主题规划多轮检索、核对真实查询响应、候选去重和遗漏分析时触发。核心不是多发查询，而是保留“范围→目的明确的查询→原始响应→身份簇→筛选理由→残余缺口”。

不把本地快照称为实时服务，不生成建议文献填补空集，不按被引量或题名流畅度判断相关性，不执行全文精读、写作或资料库整理。

## 每次正式案例的启动协议

1. 读取实际 `environment/resources.yaml`，确认被授权的解析工具、来源、版本与网络能力；缺失的工具不得写成已使用。
2. 完整读取 `/input/case.json`、`/input/task.md`，取得真实 `case_id`、原始 `output_contract`、研究范围和限制。逐份检查 `/input` 中实际论文、Crossref/Europe PMC/出版方响应、参考图及本地状态；PDF 用资源清单中的真实解析能力读取，不假定答案层。
3. 将实际读取结果整理到 `/app/work/analysis_input.json`。这是 Agent 的事实抽取层，不是收集网络新结果的工具。保留来源响应定位、原值和缺失值；只有真实执行过的查询才设 `executed: true`，限流/失败仍保留其状态。
4. 在 `/app/work` 留中间结果，在 `/app/output` 留最终结果。先确认脚本已经写出诚实的部分 `/app/output/result.json`，再逐条复核科学判断与合同。
5. 平台安装目录固定为 `/skills/weekly-skill`。通过 `python3 /skills/weekly-skill/scripts/run.py` 调用；模块与资源由 `__file__` 定位，不能改为开发机路径。

## 专属算法

`scripts/run.py` 不联网，执行以下确定性步骤：

1. 将查询分成已执行观察和仅计划两类；失败、未执行响应不能产生候选。
2. 汇总每次命中的原始序号、查询目的、响应定位、来源及新术语，形成可重放总账。
3. DOI 大小写、URL 前缀和尾标点归一后优先合并；无 DOI 时仅在规范题名、第一作者和年份三者同时吻合时合并。只有题名相似者进入待复核而非强并。
4. 用范围概念的受控变体计算题名字段的**词面覆盖辅助量**，明确它不是语义证明。保留、待定、排除及证据直接性来自 Agent 对实际材料的逐条复核；未给出 screened decision 的记录保持待定。
5. 按处置层级、直接性、概念覆盖和观察来源数形成整数元组，排序不引入伪精确小数。同元组同名次，规范题名与 ID 仅稳定显示顺序；`k` 来自实际输入，未给时取所有非排除簇。
6. 比较失败来源、未覆盖概念、待定身份与待定筛选，明确未完成项及下一步定向动作；不宣称零遗漏。

`analysis_input.json` 的必要结构为：

```json
{
  "scope": {"concepts": [{"id": "concept-a", "variants": ["term", "abbreviation"]}]},
  "k": 5,
  "queries": [{"id": "q1", "purpose": "core", "expression": "actual expression", "source": "actual-source", "executed": true, "status": "success", "response_locator": "path#records", "hits": [{"id": "r1", "title": "actual title", "authors": ["actual author"], "year": 2024, "doi": null, "abstract": "actual visible abstract", "screening": {"decision": "retain", "directness": "direct", "reasons": ["evidence-specific reason"], "evidence": ["verbatim fragment"]}}]}],
  "planned_queries": [{"id": "q2", "depends_on": ["q1"], "purpose": "close concept-b", "expression": "not executed"}]
}
```

## 输出、证据与状态

- `/app/output/result.json` 仅保留 `case_id` 与实际 `output_contract` 允许的根键，内部缓存为 `candidates/query_log/dedup_clusters/top_k/metrics/residual_gaps`。Agent 必须逐键、逐类型按实际合同复核映射，不得附加自创公开字段。
- `/app/work/analysis.json` 保存完整审计结果，`contract_validation.json` 保存缺失映射/类型警告，`run_log.json` 保存真实 Python 版本和资源文件可见状态。
- `completed` 表示已有候选且没有待定项或失败查询；`empty` 表示没有候选且没有来源失败；`partial` 表示限流、失败、无输入命中或尚有待定判断。空、未查到、信息不足和确认不相关必须分开。
- 所有科学结论仅限实际有限快照。来源清单含文件 SHA-256 只证明输入字节，不证明实时状态；状态、不确定性和变化记录均据实写入。

## 依赖与降级

仅需 Python 3 标准库。本次合成安装副本实测为 **CPython 3.9.6**，无第三方包、账号、网络或后台服务依赖；未实测其他版本。脚本退出码：0 表示算法完成（科学状态仍可能是 `partial/empty`），1 表示保留部分结果的领域异常，2 表示案例或任务文件无法读取。

- 限流/权限不足：保留 query、状态和已授权材料，不绕过限制。
- 元数据冲突：并列原值与来源，DOI/作者/年份不猜补。
- 空结果：报告实际范围与零命中，只把有目的的补检放入计划，不能伪装成已执行。
- 合同不兼容：先保留部分结果及 work 审计，再按真实合同修正映射并复跑。

## 最小示意（非正式结果）

以下为**虚构接口结构示意**，不是科学文献、真实数据库响应或官方答案：

```json
{"case_id":"demo-search","output_contract":{"type":"object","properties":{"case_id":{"type":"string"},"status":{"type":"string"},"results":{"type":"array"},"sources":{"type":"array"},"uncertainties":{"type":"array"},"change_records":{"type":"array"}}}}
```

```json
{"scope":{"concepts":["alpha"]},"queries":[{"id":"demo-q","executed":true,"status":"success","expression":"alpha","source":"demo-fixture","response_locator":"fixture#1","hits":[{"id":"r1","title":"SYNTHETIC ALPHA RECORD","authors":["Demo"],"year":2024,"abstract":"alpha","screening":{"decision":"retain","directness":"direct","reasons":["demo"],"evidence":["alpha"]}}]}]}
```

```bash
python3 /skills/weekly-skill/scripts/run.py --case /input/case.json --task /input/task.md --data /app/work/analysis_input.json --input-root /input --resources environment/resources.yaml
```

示意主结果节选：`{"case_id":"demo-search","status":"completed","results":[{"record_id":"id:r1","decision":"retain","rank":1}],"uncertainties":["finite snapshot only"]}`。实际输出还包括合同要求的其他字段，以运行生成的 JSON 为准。

## 自查与维护

1. 同一输入复跑，候选身份簇、计数、名次与理由应稳定；没有真实基线不得宣称增益或指标提升。
2. 核对每条候选可回指 query/response，输入命中数与簇成员数闭合，排除和待定均有证据；关注头部 `Precision@k/nDCG@k` 时只用真实标注计算。
3. 必测空命中、429、缺 DOI、同题名不同身份和合同字段变更。修改去重键、排序元组或词面覆盖算法后，用相同输入记录前后真实差异。
4. 只提交本目录的 Skill 与必需脚本；保持 UTF-8、无链接、无测试输出。没有用户确认的真实公开 GitHub URL 时不创建 `GITHUB_URL.txt`，该项是正式提交阻塞，不发布、不提交、不处理令牌。
5. 遵守平台单次执行 64 次模型请求、900 秒、每响应含推理 16384 tokens 的预算。
