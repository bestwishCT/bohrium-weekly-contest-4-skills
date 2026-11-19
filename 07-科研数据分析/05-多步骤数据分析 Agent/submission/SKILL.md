---
name: agent-dag
description: Run a file-backed research analysis DAG through ingest, dictionary alignment, cleaning, descriptive statistics, auditable visualization and same-source report backfill. Use for multi-step CSV/SQLite-derived analyses needing quality gates, content-hash lineage, cache invalidation, partial recovery and reproducible 300dpi figures.
---

# 带质量门、血缘与断点恢复的多步骤分析

## 触发与专门能力

当任务不仅要求一组数字，还要求把导入/字段对齐—清洗—分析—可视化—报告回填串成可恢复流水线时触发。五阶段传递实际文件，不能把五个文字建议拼起来。重点在阶段契约、内容哈希血缘、上游变更使下游失效、旧输出隔离、报告数字同源回读。

内置窄版 `analysis_scope: descriptive_only` 只做描述统计，不给 p 值；task 需要推断、聚类/重复测量或其他模型时明确替换/扩展统计阶段并使用真实设计，不得伪成已完成。内置绘图仅短 ASCII 类别，中文、特殊字体和精确版式须使用现场实测工具，失败不伪装成功。不做无要求的论文阅读、实验设计或所有类型图表。

## 读取真实输入与版本

每例先读 `/input/case.json`、`/input/task.md`、现场 `environment/resources.yaml`、全部数据字典、CSV、SQLite 与材料，先生成涵盖真实问题和所需产物的计划。公开修订提及完整 UCI Concrete（1030）、Iris（150，bezdek 来源变体）、Wine（178）和真实 SQLite；这是版本核查而非字段答案。SQLite 必须真实只读 `mode=ro` 探查后导出当前阶段 CSV，保存 schema、SQL、参数、键粒度与数据库哈希，不能由库名推断表结构。

- 平台 `/skills/weekly-skill`，官方 DeepSeek Harness 调用；资源按 `scripts/pipeline.py` 与 `scripts/render_chart.py` 相对定位，渲染模块随本 submission 自包含，不跨题引用。
- 中间快照、阶段文件、checkpoint、缓存、历史、归档和失败现场只在 `/app/work`；最终数据、统计、图、报告、代码副本（按任务）与 `/app/output/result.json` 位于 `/app/output`。
- `case/task/resources` 的哈希进入 ingest 来源和缓存指纹。真实 `output_contract` 决定字段/层级/类型；`contract_values.json` 键必须完全一致，适配器加 `case_id`，完成不全或键不匹配不生成成功结果。
- 2026-11-18 在线复核与公开修订一致；case/task 若有更新，以最新真实材料为准。

## 计划与数据字典

`/app/work/plan.json` 必填 `schema_version: 1.0`、`analysis_scope: descriptive_only`、`dictionary.id/group/value/unit/design`、目标 `grain`、`duplicate_action: quarantine_conflicts|keep_review`，以及有依据的 `missing_tokens`。`qc` 须明确 `min/max/on_violation: quarantine|retain_flag`；缺 QC 不进行分布驱动剔除。`drop_exact_duplicates_authorized` 仅在工单允许时为 true。

```json
{
  "schema_version": "1.0",
  "analysis_scope": "descriptive_only",
  "source_description": "synthetic local smoke, not official",
  "grain": "one row per synthetic sample",
  "dictionary": {"id": "sample_id", "group": "arm", "value": "response", "unit": "a.u.", "design": "three independent descriptive groups"},
  "missing_tokens": ["", "NA"],
  "keys_expected_unique": true,
  "duplicate_action": "quarantine_conflicts",
  "drop_exact_duplicates_authorized": true,
  "qc": {"min": "0", "max": "10", "on_violation": "quarantine"},
  "error": "sd", "dpi": 300,
  "figure_title": "Synthetic group means"
}
```

按真实 task 定义源/字典版本、统计假设与图的必要性。不要把示例 ID、单位、QC 上限用于正式案例。没有重复规则的近似重复和同主键冲突只可保留待复核或隔离，不静默覆盖。没有 task 的缺失授权不得均值/零填补。

## 实际执行与依赖

```bash
python3 /skills/weekly-skill/scripts/pipeline.py \
  --input /app/work/analysis_input.csv --plan /app/work/plan.json \
  --case /input/case.json --task /input/task.md \
  --resources environment/resources.yaml \
  --work /app/work --output /app/output \
  --contract-values /app/work/contract_values.json
```

只用 CPython 标准库。开发与烟测实测 Python 3.9.6；未发现 NumPy/SciPy/matplotlib 安装。运行环境若不同须重新实测记录，不能声称锁文件、字体或依赖可用。报告、CSV 与 SVG/PNG 可重建；无网络、安装或随机过程。

## 五阶段契约与质量门

| 阶段 | 实际输入 → 输出 | 通过条件与失败传播 |
|---|---|---|
| ingest | 源 CSV/case/task/resources/plan → `source_snapshot.csv,ingest.json` | 有唯一表头、必需字段、非空行和字段映射，记录来源/字节/SHA-256/粒度；缺列即失败，后续 blocked。 |
| clean | ingest 快照 → `analysis.csv,row_audit.csv,duplicate_groups.json,cleaning_report.json` | 缺失保持未补，十进制规范化，按工单 QC/完全重复/冲突；全量行审计可定位，原始=可分析+未纳入，文件回读一致。 |
| analyze | clean/analysis.csv → analysis.json | 只从最终可分析表按组算 n/mean/sample SD/min/max/unit，设计与前提单列；零可分析记录停止，不输出 p。 |
| visualize | analyze/analysis.json → figure.svg/png,plot_data.csv,render_check.json | 使用同版统计而不读旧原表；柱从 0、SD 仅 n>1，SVG XML 可解析、PNG CRC/IDAT/像素/300dpi 回读通过，元素数等于统计组数。 |
| report | analysis.json + render_check + clean → report.md,report_audit.json | 每个 n/mean/SD 与单位自动格式化回填，回读报告逐项检查；不手工复制数字，图表人工预览仍显式待做。 |

任何阶段失败：保存当前 failed checkpoint、错误与后续 blocked 状态，仅把成功前缀复制到 output。成功阶段若支撑不了下游结论，标部分成果，不把旧时 p 或报告拿来补缺。

## 血缘、缓存与恢复算法

1. 每阶段计算 source fingerprint：`stage + 输入 CSV 哈希 + plan 哈希 + pipeline/渲染代码哈希 + 上游实际输出哈希集合摘要`；ingest 另含 case/task/resources。
2. checkpoint 仅当 fingerprint 相同、状态 done 且每个输出 hash 仍存在/一致时方可 cached；否则重算。下游指纹包含上游产物内容，修改上游数据、规则、代码或产物即从必要节点重跑。
3. 新运行先把旧 output 文件移入 `/app/work/recovery/<UTC-run-id>`，防止部分新结果夹在旧报告中被误认。当前输出由本运行成功/缓存的阶段重建；归档不是删除输入。
4. 失败修复后用同一 work/output 再跑，未变阶段复用、失效阶段重算；`work/run_history.json` 追加真实状态，不覆盖历史。
5. output 的 `pipeline_state.json` 为本次唯一状态源：success、partial_or_failed，包含每阶段 cached/recomputed/failed/blocked、fingerprint、错误、退出码和归档位置。缓存只代表相同来源不需要再计算，不代表科学结论更可靠。
6. 只有五阶段全成功才执行契约映射；契约失败保留所有科学产物并标 delivery 限制，不删图或统计。

## 可视化与证据查看

脚本实际生成 SVG 和 RGB PNG，并做 XML、PNG 签名/CRC、pHYs、IDAT 解压、非空白/多色检查；这不是人工视觉验收。Agent 必须打开最终 PNG 检查文字、截断、图例、系列和 n/SD 是否与分析一致，并记录观察；ASCII 位图预览不能替代 SVG 可编辑源。若 task 要求 PDF/Office/报告页面，使用实际可用工具渲染并逐页打开，未做不可声称已检查。

## 正常产物与异常处置

成功 output 常含 `ingest.json,source_snapshot.csv,analysis.csv,row_audit.csv,duplicate_groups.json,cleaning_report.json,analysis.json,figure.svg,figure.png,plot_data.csv,render_check.json,report.md,report_audit.json,pipeline_state.json,result.json`。同名统计分析文件与绘图输入均来自当前 plan，不可混用上一版本。依赖、结果不确定性、人工预览待办与未执行步骤需在 pipeline_state/报告中明确标记。

- 空表、缺列、非法类型、全值缺失、QC 规则不明：在相应门失败并保留前缀；缺失与运行失败分开报告。
- 主键冲突/编码或单位歧义：暂停受影响路径；不把“最后一个值”当正确值。
- 图导出/权限失败：保留统计、代码、已成功格式和渲染错误；修好根因后由缓存恢复，不输出空图。
- 非对象契约或类型不明：不以自创字段保证“JSON 看起来成功”；保留原始契约证据，部分交付注明限制。
- 程序不提供强制通用重试；每次恢复必须记录实际原因和修改后的 hash，防止掩盖静默科学错误。

## 最小非官方示意

使用上面的计划与以下合成 CSV（末行是明确重复，B/NA 是缺失，B/20 超出示例 QC）：

```csv
sample_id,arm,response
S1,A,1
S2,A,3
S3,B,5
S4,B,NA
S5,B,20
S6,C,2
S7,C,4
S1,A,1
```

示意输出原始 8 行、可分析 5 行、未纳入 3 行；A 有效 n=2、均值 2、SD≈1.414，B 只有 n=1 不规范造 SD=0，C 均值 3。第二次运行五阶段 cached；追加 `S8,C,6` 后五阶段因血缘变更重算，报告自动回填 C 的有效 n=3、均值 4。`response` 列缺失时 ingest 失败，后续全部 blocked，无伪造 result.json。此例只作非官方验证。

## 自查与维护

维护须实际运行正常、再一次缓存、修改上游全部失效、缺列/空表、非法类型、全列缺失、冲突键、DB 失败、渲染失败、权限和恢复后重跑。检查输入只读、每阶段输入输出 hash、状态/退出码真实、缓存未引用陈旧报告；聚合/描述独立复算，最终 PNG 打开检查。升级 plan/stage contract 时同时更新两脚本、示例、checkpoint 版本与恢复测试，禁止绕过质量门。

资源 UTF-8、相对路径、无 symlink 和 README；submission ≤50 文件、单≤500KB、总≤2MB。无真实公开 GitHub URL 不创建 `GITHUB_URL.txt`，不发布/PR/提交或生成令牌，正式提交仍需用户另行明确授权。
