---
name: stat-design
description: Design-first reproducible statistics for research CSV snapshots and read-only SQLite extracts. Use for explicit independent two-group contrasts, two-condition paired analyses or single-predictor correlation/slope questions requiring missingness accounting, assumption diagnostics, effect estimates, confidence intervals, multiplicity adjustment and cautious interpretation.
---

# 研究设计约束下的统计分析与复算

## 触发与适用边界

当任务给出真实表格/数据库、数据字典、目标估计和明确研究设计，并要求描述统计、两组 Welch、两条件配对或单预测元相关/OLS 斜率、效应量、CI 与解释时触发。代码不把多组硬塞进两样本检验，不把聚类/重复测量改作独立样本，不机械以正态性 p 值切换方法。

超出上述模型时保留描述统计并说明未支持设计；可用现场科学库另行预先设定合适模型，但不得试多种方法挑显著。不做实验设计、样本量估算、因果宣称或论文写作。缺失、未检测不能当零；Tukey 异常仅报告不自动删除；不完全重复/主键冲突不能静默覆盖。

## 真实输入与路径

每例先读 `/input/case.json`、`/input/task.md`、现场 `environment/resources.yaml`、完整输入表、字段字典、SQLite schema 和材料。当前公开修订为 UCI Concrete（1030 行）、Iris（150 行，明确 bezdek 来源变体）、Wine（178 行）及真实 SQLite；这些只是来源/版本核查线索，实际字段、行数、单位和 design 必须以现场 case 为准，不能按数据集名预先写答案。

- 平台安装目录 `/skills/weekly-skill`，由官方 DeepSeek Harness 触发；资源相对引用 `scripts/analyze.py`，禁止个人绝对路径。
- 输入 CSV：UTF-8、唯一表头。长表独立/配对分析用 `group,outcome`；配对另有 `subject`；相关/斜率用 `x,y`。
- SQLite：先以只读 URI `file:<path>?mode=ro` 连接并查看 schema，以可复核 SQL 导出分析 CSV；保存查询、参数和数据库哈希。不得伪造连接、字段或结果，也不能只根据名字假定 UCI schema。
- 分析计划：根据 task/字典创建 `/app/work/plan.json`。中间文件、探查与失败现场只在 `/app/work`；最终数值、表、报告、代码和 `/app/output/result.json` 放在 `/app/output`。
- 结果契约：读取实际 `output_contract` 的名称、层级、类型。`contract_values.json` 键须与契约对象完全一致；适配器加真实 `case_id`，键不一致即停止。所有数字从 `analysis.json` 取，不补造契约外字段。

## 分析计划数据字典

计划使用 `schema_version: 1.0`、`analyses` 列表和统一 `missing_tokens`。每个分析含唯一 `analysis_id`、预设 `design`、目标 `target_estimand` 与来自 task 的 `design_evidence`：

```json
{
  "schema_version": "1.0",
  "missing_tokens": ["", "NA"],
  "drop_exact_duplicates_authorized": false,
  "max_missing_fraction": 1.0,
  "family": "primary endpoint contrasts",
  "analyses": [{
    "analysis_id": "contrast-1",
    "design": "independent",
    "design_evidence": "task states independently sampled units",
    "target_estimand": "mean(A)-mean(B)",
    "outcome": "response",
    "group": "arm",
    "group_levels": ["A", "B"],
    "unit": "mmol/L"
  }]
}
```

`group_levels` 顺序就是差值方向。配对改设 `design: paired`、`subject`；相关改设 `design: correlation`、`x,y`。`drop_exact_duplicates_authorized` 只有工单明确允许才能设真。`max_missing_fraction` 超过即阻止相应推断；默认 1.0 只是完全披露并计算可用值，不是缺失方法推荐。

## 执行与实测依赖

```bash
python3 /skills/weekly-skill/scripts/analyze.py \
  --input /app/work/analysis.csv --plan /app/work/plan.json \
  --case /input/case.json --task /input/task.md \
  --resources environment/resources.yaml \
  --work /app/work --output /app/output --confidence 0.95 \
  --contract-values /app/work/contract_values.json
```

脚本只依赖 Python 标准库；开发与烟测实测 CPython 3.9.6，未安装 NumPy/SciPy/pandas。Welch t 分布用正则不完全 beta 连分式，分位数用有界二分；维护必须保留数值复算。无需网络、安装或随机过程。现场若提供其他统计包，先实测版本并记录，未验证的版本不可写成依赖。

## 完整统计流程

1. **问题与数据字典门**：还原结局、解释变量、单位、分组、参照、分析单位、独立/配对/时间/聚类结构、目标估计；保持 `group_levels` 预设方向。字段、单位或 design 不明时阻塞，不靠数据结构猜研究设计。
2. **统计前数据门**：记录原始行、缺失标记和显式授权的完全重复删除；按变量列出有效 n/缺失 n/排除原因。样本量按分析变化，非整行默认删除。独立两组只纳入预设水平，范围外行另计。
3. **描述统计**：按组/配对差/完整对给出 n、缺失、均值、样本 SD、中位数、Q1/Q3、范围和单位；对组计算 Tukey 1.5 IQR 诊断但不删除，编码字段不求均值。
4. **前提诊断**：给出 n≥8 时 Jarque–Bera 统计及 χ²(df=2) 渐近尾概率；它不是正态性裁决开关。小样本、图形、方差与残差需结合解释。独立性只来自设计证据，数据检验不能证明。
5. **预设主分析**：独立两组用 Welch（不需等方差），报告均值差、SE、Welch t、Satterthwaite df、双侧 p 与 t CI；效应用合并 SD 的 Hedges g 小样本修正。配对按受试者完整配对，报告差值、paired t、df、CI 和 dz；同一受试者-条件重复或冲突不任意覆盖，整受试者排除并披露。
6. **相关路径**：相关性设计计算 Pearson r、r²、OLS slope/intercept、残差 SSE、slope t、df 与 CI；线性、独立观测和同方差残差仍需设计/图形复核，不把斜率解释为因果。
7. **退化与多重性**：各组 n<2、完整配对不足、零 SE/df/残差方差时主分析标为不可计算，p/CI 为 null，不写 p=0。一个计划内的所有 p 完成后按配置 family 做 Benjamini–Hochberg，报告原始和校正 p。
8. **解释绑定**：将效应方向、参照组、估计区间、原始/BH p 放在同一结构。CI 是估计不确定性而非个体取值范围；统计显著、实际意义、因果三者分开，实际阈值必须由领域任务给出。
9. **独立复算与交付**：均值差用 `statistics.mean`、方差用独立实现复核，行数核对不达标则非零退出；先写分析 JSON/CSV、复算记录，再自动从同一对象生成报告、manifest 与契约结果。

## 输出与失败恢复

固定产物：`analysis.json`（设计、描述、推断、效应、CI、假设、限制）、`descriptive.csv`、`reconciliation.json`、`report.md`、`run_manifest.json`，可选 `result.json`。状态区分 `complete`、`complete_with_limitations`、`failed`；主分析未运行/不适用/零方差与小样本保持可区分。

- 缺列、混合非法字段、空表、未知或含糊 design、坏计划：退出 2，留 `run_failure.json` 与部分输出；不能把失败写成“无显著”。
- 缺失率超阈值：保留描述，阻止相应推断；调整阈值必须有预设科学理由，不得显著性驱动。
- 多组、重复/聚类、零方差、单位不明：不套用本脚本其他模型；保留可复核结果并转人工。
- 权限/依赖/SQLite 失败：记录真实错误、已执行命令和退出码，保留只读源库；修正根因后重跑，不安装未核实依赖。
- JSON 契约失败不删除已经生成的表和报告。任务要求图或 Office 时才另外生成原生文件，并真实渲染 PDF/PNG 检查字体、截断和数值。

## 最小非官方示意

合成 CSV：

```csv
arm,response
A,1
A,2
A,3
A,4
B,3
B,4
B,5
B,6
B,NA
```

使用上文 `independent` 计划，以方向 A−B 调用。示意均值差为 −2，两组有效 n 都为 4，B 的缺失为 1；脚本输出 Welch t、df、双侧 p、95% Welch CI、Hedges g 及独立均值/方差复算。输出只说明估计及其不确定性，不声称“证明有效”。配对或相关任务必须输出相应计划和结构，不得复用两组答案。

## 自查与维护

维护时用合成正常、缺失、完全重复、零方差、n 不足、配对键冲突、非有限数、错误 design、缺列和重复运行烟测。统计至少以独立数值积分、手算简单样例或另一实现复算统计量、df、p、CI、效应量与 BH；比较容差写清，不能以同一公式复制粘贴冒称独立金标准。资源改动须同步计划 schema、脚本、示意和恢复测试，记录实际解释器/库版本。

坚持标准库、相对路径、UTF-8、无符号链接，submission 总数不超过 50 文件、单文件 500KB、总计 2MB，不创建 README。无真实公开 GitHub 作品 URL 不创建 `GITHUB_URL.txt`，不发布/PR/提交或生成令牌；任何正式提交须用户另行明确授权。
