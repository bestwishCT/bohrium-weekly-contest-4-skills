---
name: merge-databases
description: 对授权的多个学术数据库快照做按库查询审计、真实字段映射、稳定身份去重、字段级来源保留和冲突合并，并报告各库覆盖缺口。适用于跨库联合检索而非机械拼接响应，不用于虚构实时访问或绕过许可。
---

# 可重放的多数据库联合与字段溯源

## 使用场景

需要把 Crossref、Europe PMC、出版方或其他**实际提供**的响应合成不重复、可逐字段回溯的记录集时使用。重点不是接入数量，而是“库—查询—原始响应—映射—身份簇—字段取值”能否逐项复核。

## 正式启动

1. 先读 `environment/resources.yaml`，确认实际授权、账号、工具、版本和网络状态；再读 `/input/case.json`、`/input/task.md` 的真实 `case_id`、`output_contract`、必需库集合、额度和输出限制。
2. 逐份读取实际九份文档与保留响应。数据库名字出现在缓存里不等于实时访问成功；不能把本地增量状态写成外部实时状态。直接解析实际 PDF/JSON，保留路径、记录 ID、原字段、许可和材料日期。
3. 按实际字段语义制作 `/app/work/analysis_input.json`。在线年、出版年、作者顺序、文献类型必须有显式映射和 `field_semantics`；不要把未提供、不适用和解析失败合并为正常的空字符串。
4. `/app/work` 存原始解析和状态，`/app/output` 存最终 JSON。先用脚本生成部分结果，再查合并链、失败库和合同字段。
5. 安装后运行 `/skills/weekly-skill/scripts/run.py`；`common.py` 从脚本位置加载。不要把本 Skill 移到依赖私人配置的目录。

## 跨库合并专属算法

1. **能力矩阵**：逐库保存实际查询语法、字段能力、分页、状态、许可和响应定位。不同查询可以按库适配，但任何主题/时间扩大都需明确记录。
2. **失败隔离**：只有 `executed: true` 且 success/observed/completed/partial 的库能导入记录；429、超时、未执行进入覆盖缺口，绝不把失败响应当零命中。
3. **映射与规范值**：由 `field_mappings` 指明 title/authors/year/venue/doi/record_type 的实际来源字段。DOI 输出规范裸 DOI，URL 与原写法作为 `raw_value` 和 locator 保留；其他字段不按常识偷换语义。
4. **身份簇**：规范 DOI 精确合并，无 DOI 时需题名+第一作者+年份三者共同佐证。仅题名相似的候选不自动去重；版本关系和值冲突保留。
5. **逐字段合并**：每个候选值连同库、原始记录、原字段、原值、响应位置保存。相同语义值一致合并。冲突时只有显式 `source_priority[field]` 给出唯一最高优先值才选择；同优先级不同值置为 null 并把全部候选保留在 `field_conflicts`，严禁随机选一库。
6. **覆盖报告**：标出只在单库观察到的记录、系统字段缺失、失败库、字段无法映射及受限内容。撤回/状态只保留实际历史断言，不把缓存解释为当前状态。
7. **稳定复算**：身份簇和输出记录按键稳定排序；同一输入产生相同规范记录、来源链和冲突。

而非把原始响应拼成数组。核心结构：

```json
{
  "field_mappings": {"db-a": {"title": "article_title", "authors": "authors", "year": "online_year", "venue": "container", "doi": "doi", "record_type": "type"}},
  "source_priority": {"year": ["db-b", "db-a"]},
  "sources": [{"name": "db-a", "executed": true, "status": "success", "capabilities": {"pagination": true}, "field_semantics": {"year": "actual online year"}, "query": {"id": "qa", "expression": "adapted actual query", "parameters": {}}, "response_locator": "a.json#message", "records": [{"id": "a1", "article_title": "actual title", "authors": ["actual"], "online_year": 2024, "container": "actual journal", "doi": "actual DOI or null", "type": "article"}]}]
}
```

## 输出、状态和限制

- `/app/output/result.json` 逐键遵循实际 `output_contract`，含 `case_id`。内部数据为 `merged_records/source_capability_matrix/source_queries/field_sources/field_conflicts/dedup_clusters/single_source_records/coverage_gaps`。
- `/app/work/analysis.json` 保存全量合并审计，`contract_validation.json` 暴露未映射字段和类型，`run_log.json` 存环境。任何公开字段名须以实际合同为准。
- `completed` 表示成功记录已合并、冲突均有显式依据且没有覆盖失败；`partial` 表示来源失败、身份或同优先级冲突；`empty` 仅限无记录且无来源失败的情形。
- 结果适用于有限真实响应，不证明实时联合覆盖、全局新颖性或完整引文状态。许可不允许展示的原文不得为留证而外传。
- 按实际合同显式保存不确定性：来源缺口、字段冲突、身份待仲裁及权限边界均须定位；无法判断与库调用失败分开表达，不静默合并或补值。

## 依赖和降级

需 Python 3 标准库，合成安装副本实测 **CPython 3.9.6**；未安装任何包，其他版本未实测。退出码 0=算法执行完成（可为 partial），1=部分结果伴随领域异常，2=case/task 读取失败。

库失败保留其他成功库并标缺口；编码/返回字段变化进入解析失败，不乱码修复成正常值；关键字段缺失保持 null；DOI/题名/年份冲突保留候选供仲裁；合同变更先保存部分结果后映射修复。

## 最小结构示意（虚构非正式）

```json
{"field_mappings":{"demo-db":{"title":"title","authors":"authors","year":"year","venue":"venue","doi":"doi","record_type":"record_type"}},"sources":[{"name":"demo-db","executed":true,"status":"success","response_locator":"demo#1","query":{"expression":"demo","executed":true},"records":[{"id":"r1","title":"SYNTHETIC WORK","authors":["Demo"],"year":2024,"venue":"Demo Journal","doi":null,"record_type":"article"}]}]}
```

```bash
python3 /skills/weekly-skill/scripts/run.py --case /input/case.json --task /input/task.md --data /app/work/analysis_input.json --input-root /input --resources environment/resources.yaml
```

在 `case_id: demo-multidb` 和标准五字段合同时，主结果示意节选为 `{"case_id":"demo-multidb","status":"completed","results":[{"record_id":"id:r1","fields":{"title":"SYNTHETIC WORK","year":2024}}]}`，不代表真实数据库记录或响应。

## 自查与维护

- 从每个合并字段回指原库、原记录、原字段和原值；核对 DOI URL 与裸 DOI 不错判冲突，作者顺序和年份语义不改写。
- 重跑验证合并 ID 和冲突策略稳定；改变映射或来源优先级前先保存旧输出，用真实输入测量误并、缺字段和重复变化。
- 测试单库失败、同优先级冲突、只有题名相似、单库特有文献、系统字段缺失与权限限制。
- 只带必要 Python 文本，不附真实响应答案、验证输出或私有配置；遵守资源数量、大小和符号链接限制。
- 无用户确认的真实公开 GitHub URL 时正式提交该项阻塞，不创建 `GITHUB_URL.txt`，不发布、不提交、不处理令牌。平台预算为 64 请求、900 秒和每响应含推理 16384 tokens。
