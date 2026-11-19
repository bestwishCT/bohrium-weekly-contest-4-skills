---
name: clean-rev
description: Dictionary-led, no-imputation cleaning for research CSV aggregate-table transcriptions. Use when a case supplies a data dictionary, explicit QC/workorder, missing sentinels, duplicate keys, encoding faults or equivalent units and requires a reversible cleaned table, issue ledger and automatically reconciled quality evidence.
---

# 聚合转录表的可逆清洗与对账

## 何时触发与边界

用于有真实表格、字段字典、逐指标单位/QC 规则和明确清洗工单的缺失、无效、异常、重复、格式/单位转换及质量报告任务。重点是已发表聚合表的转录故障，不是原始实验；不推断总体统计量，不寻找或假定 `quality-gold.md`。不做统计建模、假设检验、图表美化、数据库运维、实验设计或论文写作。

若工单没有明确授权，不编码修复、不改科学测量值、不删异常、不去重、不转换单位、不补空格。严禁用均值、中位数、零、邻值、回归或领域猜测填补缺失/无效源值；真值 `0` 不是缺失。

## 先读真实 case 与配置

每例依次读取 `/input/case.json`、`/input/task.md`、当时工作目录的 `environment/resources.yaml`，以及它们实际列出的全部表格、字典、工单和材料。核对文件字节、真实内容、编码、表头与版本，不能由题目标签推断字段。平台由官方 DeepSeek Harness 调用本草稿并安装到 `/skills/weekly-skill`；脚本资源从技能目录按 `scripts/clean_table.py` 和 `references/rules.example.json` 相对定位，不读取作者机器文件。

- 输入：只读 CSV、由真实字典/工单改写的 `schema_version: 1.0` 规则 JSON；规则含 `key_fields`、四至五类 `kind` 字段、缺失语义、QC、单位和重复动作。
- 来源/版本：在 `run_manifest.json` 保存 input/rules/case/task/resources 的文件名、字节、SHA-256，以及原始快照。本文依据 2026-11-18 在线复核的 `Organizer concrete cleaning revision2`；运行时新 case/task 优先。
- 路径：中间配置、快照和失败现场只在 `/app/work`；最终表、清单、报告、代码副本（任务要求时）与 `/app/output/result.json` 在 `/app/output`。
- 输出契约：逐项读取真实 `output_contract` 的字段、层级和类型。准备 `/app/work/contract_values.json`，其键必须与契约对象完全一致、且不包含 `case_id`；数值从最终 JSON/CSV 取，不手工回填。适配器写入 `case_id` 后校验键集合，未知子结构不得臆造。

## 配置成真实工单

复制 `references/rules.example.json` 到 `/app/work/rules.json` 后再依据本案字典修改，不能把示例单位或阈值直接用于真实材料。配置字段字典：

| 键 | 含义与限制 |
|---|---|
| `input` | 已确认编码和 `, ; tab |` 中一种分隔符；编码不明先诊断而非乱转。 |
| `fields[].name/kind` | 真实列名；`key/measure/attribute/time/unit` 决定语义。 |
| `required/missing` | 空、`NA`、`ND`、`<LOD`、`-999` 分别映射录入缺失、未检测、低于检出限或无效哨兵；每个标记需字典依据。 |
| `type/decimal_comma` | `decimal` 用十进制解析；逗号小数点仅在工单允许时开启。 |
| `literal_map` | 仅登记明确存储/编码修复映射，逐条留原值、修复值和依据。 |
| `unit` | 指定单位列或固定单位、目标单位、量纲一致的有限十进制因子及未知单位动作；量纲不明即停该字段。 |
| `qc` | 物理/QC 上下限及 `retain_unresolved` 或 `quarantine`；分布异常本身不能成为删值依据。 |
| `key_fields/duplicates` | 标准化后的真实主键；完全重复 `drop_first/keep_review`，冲突键 `quarantine_group/keep_review`，合法重复测量须把 `keys_expected_unique` 设为 false。 |

数值、单位、QC 和主键的规则版本来源写进变更记录。若字典与材料冲突，两项并列记录并暂停受影响列，不选择“更有利”的解释。

## 确定性执行

先实测解释器与资源清单，再从任意工作目录运行：

```bash
python3 /skills/weekly-skill/scripts/clean_table.py \
  --input /input/data.csv --rules /app/work/rules.json \
  --case /input/case.json --task /input/task.md \
  --resources environment/resources.yaml \
  --work /app/work --output /app/output \
  --contract-values /app/work/contract_values.json
```

脚本仅使用 Python 标准库。开发和烟测环境实测为 CPython 3.9.6；不需要也不假定 NumPy、pandas 或 SciPy，真实运行版本由 manifest 再记录。无需安装、网络或随机步骤；若官方环境版本不同，以现场探查为准，不声称版本兼容未经实测。

## 本题专属完整流程

1. **冻结证据**：输入只读，SHA-256 后把原始文件复制到 `/app/work/snapshots`，结束时再次核对输入哈希；所有输出路径与输入分离。
2. **表结构门**：严格 header、必需列、空列名、重复列名、单元格数检查；行号使用原 CSV 物理数据行号（首条为 2），不能先排序再追证据。
3. **逐字段映射**：形成“字段—原值—修复值—问题—规则—动作—结果—是否复核”。键先做去首尾/合并空白与 casefold；literal map 只执行授权映射。
4. **缺失语义**：检出标记保留 unresolved，输出单元格为空且原值留在 `issues.csv`；无效十进制或非有限数不猜测，按工单保留未决或隔离整行。
5. **无损转换**：`Decimal` 完成数值/单位换算，逆运算必须等于原十进制数；未知单位清空清洗字段但永不覆盖原快照，并按工单保留/隔离。默认不按有效位数四舍五入。
6. **QC 异常**：只在转换后按显式范围标记；动作来自工单。统计上罕见但 QC 合理的极端值保留，不擅自盖帽或剔除。
7. **真实重复**：主键组区分完全重复、同键冲突和合法重复测量；完全重复按工单保留第一条并隔离其余，冲突组按工单整体隔离或全部保留待复核，永不静默覆盖。
8. **最终质量闭环**：从最终 cleaned/quarantined/issues/duplicates 文件回读行数，计算最终缺失单元格与缺失率、异常、重复组、转换/映射记录；核对原始=保留+未保留、未保留=去重移除+隔离，任一不符非零退出。
9. **同源交付**：先写 cleaned/quarantined/issues/duplicates，再生成质量报告、manifest 和结果；材料不足、无法判断与程序失败分别标明。仅记录下游统计/绘图所需的数据粒度、可用记录和排除原因，不擅自分析。

## 输出与异常恢复

固定产物为 `cleaned.csv`、`quarantined.csv`、`issues.csv`、`duplicates.csv`、`quality_report.json`、`run_manifest.json`；有契约映射时另有 `result.json`。质量报告含缺失、QC 异常、重复、转换与行数对账；manifest 含状态、不确定性、变更、依赖、来源和退出码。

- 缺文件/列、坏编码、空表、表头损坏、不支持的规则版本：退出 2，保留 `run_failure.json` 与已有部分文件，不制造 result 成功状态。
- 全列缺失：不计算无分母缺失率，结果为 `null`；相关列转人工，不补值。单位未知、主键缺失或冲突：依据明确工单隔离/保留，全部标注待复核。
- 权限不足：不换路径覆盖输入，保存失败原因后从最后完整产物恢复；修正根因后重跑。正常运行可重复，科学产物确定；UTC 时间戳如实变化。
- 非对象 `output_contract` 或真实键集合不一致：停止结果适配，保留分析产物并说明限制；按真实结构补齐契约，绝不添加想当然字段。
- 只有 task 明确要求可编辑表格/图件时才生成相应原生文件，并用实际可用工具渲染 PDF/PNG 检查；不得把未渲染写成已检查。

## 最小非官方示意

以下是合成的聚合转录示意，不是官方案例或答案。将其保存为 `/app/work/demo.csv`：

```csv
sample_id,observed_at,value,unit
 S-01 ,2026-01-01,"1,25",g/L
S-01,2026-01-01,"1,25",g/L
S-02,2026-01-02,ND,mg/L
S-03,2026-01-02,0,mg/L
```

使用未改的 `references/rules.example.json`、省略 `--contract-values` 运行。示意保留两行：S-01 为 `1250 mg/L`，重复的第二行进入 `quarantined.csv`；S-02 为未检测空白并待复核；S-03 的 `0` 保留。契约键一致性、行数、缺失、重复和转换由 `quality_report.json` 自动复核；正式任务必须另写真实 rules/contract values。

## 自查与维护

每次维护先做语法检查，再用非官方含空、全缺失、非法编码、未知单位、非法小数、主键冲突、完全重复、超 QC、权限失败和二次运行的烟测。核对输入哈希不变、所有最终文件可重读、转换逆映射成立、报告能由最终表复算；测试文件不得放入 submission。

规则 schema 升级须同步脚本、示例与验证，记录 breaking change；不新增未经字典支持的“智能修复”。保留标准库优先策略，最多 50 个 UTF-8 文本资源、单文件 500KB、总量 2MB、无符号链接。无真实公开 GitHub 作品 URL 时不创建 `GITHUB_URL.txt`，不提交、建库、PR 或发布；正式提交仍须用户明确授权。
