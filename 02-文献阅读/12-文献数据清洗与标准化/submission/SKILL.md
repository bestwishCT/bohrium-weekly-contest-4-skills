---
name: data-clean
description: Normalize identity-bearing literature extraction CSV records with configurable field/unit rules, Decimal scalar/interval conversion, duplicate-extraction mapping, preserved missing/unknown values, row-level changes and pending reviews. Use for literature table cleaning, unit standardization, source-safe deduplication, or CSV export/replay; never merges distinct experiments by matching labels alone.
---

# 保留行身份的文献抽取记录清洗标准化

## 触发与数据身份

用于一批文献抽取记录的字段映射、单位/数字格式、重复抽取、缺失与异常处理。输入是组织方加工记录，不是原作者原始实验数据；每行身份和实验上下文不可混淆，科学冲突不能靠单位换算自动裁决。

版本 `1.0.0`，Python 3.9+ 标准库 `csv/Decimal/json`，Python 3.9.6 实测；无 pandas 等依赖。负区间等符号有歧义时不猜，需人工规则扩展；本实现不把语义同名当重复实验。

## 输入、配置与导出

读取 `/input/case.json`、`task.md`、`environment/resources.yaml`、`records.csv` 与 `normalization-rules.json`。规则包含：

- `identity_columns`、`row_id` 和原记录所需字段；
- `field_map` 同义字段映射，支持 `target/rule_id/priority`；`duplicate_rule` 配置重复检测编号/优先级，执行顺序冲突即停止；
- `units`：每单位 `target/factor/offset/rule_id`，可选 `priority/decimal_places`；
- `numeric_ranges`：只使用组织方给定的 min/max，不新造科学阈值，规则可含 `rule_id/priority`。

原始 CSV 只读并记录 SHA。输出 `/app/output/clean.csv`、`changes.json`、`pending.json` 和 `result.json`，过程 `/app/work`。安装 `/skills/weekly-skill`，资源只用 `scripts/common.py`、`scripts/workflow.py` 相对路径。真实 `output_contract` 逐键嵌套适配并保留 `case_id`；`data/standardized_data/result` 可映射 `clean_rows`。

## 端到端清洗流程

1. 校验行 ID、paper/experiment/extraction_event/context 等身份列和 field/raw_value/raw_unit，建立输入行→来源→实验映射，原值永不覆盖。
2. 依 field_map 统一字段名和规则 ID；缺列、空表、非法表头立即错误，其他行可保留。
3. 按单位规则 `standard=(raw*factor)+offset` 用 Decimal 处理标量和区间，保留原单位/目标单位/公式/精度。未知单位标准值为空，缺失不补 0，非法数字进入 pending。
4. 按给定 numeric range 标越界，不把科学冲突强行修成正常值。区间转换后反向也拒绝。
5. 重复检测键包含完整声明身份、规范字段/值/单位/context；完全同键只映射“重复抽取”，首行成为 canonical，其余保留 changes。experiment/event 不同即分开，即便名称数值相同。
6. 每行产生 original_record、rule IDs、action、input/canonical ID、output 和来源身份；导出 clean、pending 列表及 summary 三类对账。
7. `clean.csv` 由 csv.DictReader 复读，changes/pending 由 json.load 复读；输入行数必须等于 changes 数，canonical 映射不丢行。

## 状态与恢复

- `success`：导出对账通过且无缺失/未知单位/解析等待确认；合法重复映射不算失败。
- `partial`/退出 2：存在 pending；干净行仍输出，待确认项具体给 row ID、原值和处置，不猜单位/冲突。
- `input_error`：空表/缺身份/规则错误，生成诚实 result，不用空数据表假装成功。
- 重跑顺序由输入和规则确定，记录版本、哈希与变更，不伪造人工修改量或科学数据。

普通 CSV/JSON 不需要图形渲染；Office/图形若合同要求另行真实预览。

## 最小示例（合成、非正式）

R1/R2 有相同 paper、experiment、extraction_event、field、500 mL、context，是重复抽取；规则 mL→L factor=0.001，R3 为另一 experiment。运行：

```bash
python3 /skills/weekly-skill/scripts/workflow.py --case /app/work/clean-case.json \
  --task /app/work/task.md --input-dir /app/work --work /app/work/run --output /app/output
```

示意输出：R1 standard_value=0.5 L，R2 mapped_duplicate_extraction→R1，R3 独立；clean 2 行、changes 3 行，0.5 可解析非字符串科学结论。

## 自查与维护

对照 raw CSV 与每行 changes，检查未知/缺失为空、不同实验未错并、单位公式精度和 parser 复读。规则要版本化、按明确优先级扩展并加空表/未知单位/同值不同实验回归；改规则仅重算派生值，不改原始输入。

无真实 GitHub URL 不创建 `GITHUB_URL.txt`，不发布或提交。
