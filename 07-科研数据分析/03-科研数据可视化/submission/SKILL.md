---
name: render-audit
description: Source-to-visual audited research charts from real CSV/SQLite-derived data. Use for categorical grouped comparisons, row-level relationships or numeric-x trends requiring grouped bars, scatter or line charts with explicit units/n/error definitions, editable SVG, real 300+dpi PNG, plot data, captions and render checks.
---

# 数据到视觉元素可核验的科研图件

## 触发与重点

当真实任务要求分类均值/中位数/和/计数比较、行级数值关系或数值 x 趋势，且需要实际图、代码、图注和导出证据时触发。主攻“目的/结构选型—源值到视觉元素对账—真实 SVG/PNG 渲染检查”，不是给出绘图建议或做后期美化。

本实现不画图饼、双轴、面积/角度编码或虚构显著性；不做实验设计、统计方法创新、论文写作。分组误差线只能是预设 SD 或 SE，不得解释为 CI；单组 n=1 不画零误差线，不生成 p 值或显著标记。缺数据、无完整数值、类别过密时不交付空白图冒充成功。

## 先核对真实 case 与版本

逐例读取 `/input/case.json`、`/input/task.md`、现场 `environment/resources.yaml`、真实数据字典、CSV/SQLite 和附加材料。公开修订提及完整 UCI Concrete（1030）、Iris（150，明确 bezdek 变体）、Wine（178）及真实 SQLite；仅将其作为版本线索，字段、单位、科学问题、样本量和输出规格以本案为准，禁止旧三行占位和旧 baseline 冒充新数据。

- 安装/调用：官方 DeepSeek Harness 使用 `/skills/weekly-skill`；入口相对路径 `scripts/render_chart.py`，不引用作者机器目录。
- 数据：UTF-8 CSV 唯一表头；数据库只读探查后用真实 SQL 导出并保留 schema、SQL、参数、行数和 DB 哈希。不得猜表名或把编码类别当连续值。
- 路径：中间表、绘图 spec、失败现场只在 `/app/work`；最终 SVG/PNG、绘图数据、配置、图注、校验和 `/app/output/result.json` 位于 `/app/output`。
- 契约：实际 `output_contract` 的字段、层级、类型逐项核对。`contract_values.json` 的键须与契约对象完全相同，适配器加 `case_id` 且拒绝键不匹配；不要编造 “figure” 等未公开字段。
- 最新优先：2026-11-18 在线题面与本地修订一致；若运行 case/task 更新，更新内容覆盖本文。

## 输入 spec 与选型数据字典

在 `/app/work/spec.json` 写 `schema_version: 1.0`。共同字段包括 `title,purpose,series,series_order,missing_tokens,units`；必须显式选择且匹配：

| `chart/purpose` | 输入与标签 | 使用条件/不推荐理由 |
|---|---|---|
| `grouped_bar/comparison` | `category,value,category_label,value_label`，可设 `series` | 无序类别比较使用从零开始的长度；趋势用 line，不用并列柱；基线不可非零。 |
| `scatter/relationship` | `x,y,x_label,y_label`，可设 `series` | 行级两个有限数值；不把聚合结果伪装成原始点。 |
| `line/trend` | 同 scatter；可增设 `trajectory` | 数值 x，按 x 与来源行稳定排序后仅在同一轨迹内连线；无 `trajectory` 时每个系列 x 必须唯一，平行重复轨迹必须给标识且各轨迹 x 唯一，否则改用散点或先聚合。 |

分组聚合字段：`aggregation` 为 `mean/median/sum/count`，`error` 只能 `sd/se`；`category_order/series_order` 预先声明；最多 12 类别、6 系列、72 个柱，标签各≤16字符，超限改用经任务支持的分面/另一布局。`qc` 的 min/max 只有在真实工单明确且 `exclude_authorized: true` 时才排除；分布异常不是自动排除依据。缺失标记只配置有字典依据的 token。

示意骨架：

```json
{
  "schema_version": "1.0",
  "chart": "grouped_bar",
  "purpose": "comparison",
  "title": "Synthetic response by arm",
  "category": "arm", "value": "response",
  "series": "batch", "aggregation": "mean", "error": "sd",
  "category_label": "Arm", "value_label": "Response (a.u.)",
  "units": {"response": "a.u."}, "missing_tokens": ["", "NA"]
}
```

## 实际执行与版本

```bash
python3 /skills/weekly-skill/scripts/render_chart.py \
  --input /app/work/plot_source.csv --spec /app/work/spec.json \
  --case /input/case.json --task /input/task.md \
  --resources environment/resources.yaml \
  --work /app/work --output /app/output --dpi 300 \
  --contract-values /app/work/contract_values.json
```

实现只用 Python 标准库；开发与烟测实测 CPython 3.9.6，无 matplotlib/NumPy/Pillow。真实环境先读 resources 并探测字体/库版本，若调用其他工具必须实测记录，无安装成功或字体证据不能声称。无需网络或随机步骤。

内置固定布局为 85×55 mm、SVG 850×550 viewBox、PNG 默认约 1004×650 px、pHYs 记录 300 dpi。任务要求其他精确规格、中文或特殊字体时，本 ASCII 位图降级不能冒充可用：保留 SVG/数据，使用资源清单中实测支持目标的工具重渲染；工具不满足则明确阻塞。禁止靠安装不可信包、手工改图或替换数字过关。

## 完整分析与出图流程

1. **定义科学问题**：从 task 写清比较/关系/趋势、长宽表、分类/连续/时间字段、分组与聚合粒度；记录采用及不采用图型的理由，最终实际渲染任务要求的图。
2. **锁定口径**：检查缺失、无效、完全重复和显式 QC；完全重复只标记不自动删除。每面板/组有效 n 从输入自动计算，缺失不用零代替。
3. **建立编码映射**：分类到 x、值到长度、系列到色觉友好色和交替纹理/空心点，散点到 x/y，趋势线到排序后数值 x。线性轴，无隐式对数或双轴；均值误差线仅 SD/SE。
4. **生成绘图数据**：`plot_data.csv` 是实际绘制对象，不再另起一套数。均值用所有有效源值，SD 为样本 SD，SE=SD/sqrt(n)；中位数/和/计数不冒算均值 SE。
5. **逐元素对账**：`encoding_audit.json` 保存源行数、绘图元素数、缺失/无效排除、重复标记、聚合/映射、系列与单位。柱状有效 n 必须>0，系列和数据一致。
6. **同源性图注**：轴标签含指标/单位，图例系列、聚合规则、各组 n、误差线定义和排除数自动从同对象生成；无独立图例文字手改。
7. **真实双格式渲染**：写出结构可编辑 SVG 与真实 RGB PNG，而非文本改后缀。用 XML 解析 SVG；回读 PNG 签名、每块 CRC、IHDR 尺寸/位深、pHYs DPI、IDAT 解压和像素，检查非空白与多色。
8. **最终尺寸查看**：结构检查后的 `run_manifest.json` 诚实标为 visual review pending。Agent 必须实际打开最终 PNG，检查轴、字体、裁切、图例遮挡、灰度/系列形状、最小字号和图意，再将真实观察写入检查记录；不得仅凭程序说“已人工检查”。任务要 PDF/Office 时还需实际渲染并逐页打开。
9. **同源交付**：源数据/spec、绘图数据、SVG/PNG、图注、render_check、manifest、契约 JSON 保持同版本；若修改上游数据或聚合，整个图重画，不沿用旧预览。

## 输出与失败恢复

正常产物：`figure.svg`、`figure.png`、`plot_data.csv`、`encoding_audit.json`、`render_check.json`、`caption.md`、`run_manifest.json`，可选 `result.json`。退出 0 表示真实文件和结构/像素检查通过，不等于统计有效或人工视觉验收完成。依赖与不确定性：只依赖实测 Python 标准库；字体、统计有效性、最终尺寸可读性及超出快照的结论均须单独标注不确定性。

- 缺列、混合类型全无效、空表、图型/目的不符、类别/系列过密、QC 未授权：退出 2，写 `run_failure.json`，留 plot 配置与已完成格式，禁止空图。
- 非 ASCII 字体不足：可留 SVG，但 PNG 失败明确退出；换用经验证字体的工具后再检，不画方框或省略标签。
- 权限/导出失败：保留数据和至少一种成功格式、真实 stdout/stderr 与退出状态；修复路径或工具后重跑，不覆盖输入。
- JSON 契约失败不删图；部分成果标记未验证范围。缺少统计依据时不补误差线、比例尺或显著性符号。

## 最小非官方示意

合成 CSV：

```csv
arm,response,batch
A,1,b1
A,3,b1
A,2,b2
B,4,b1
B,6,b1
B,NA,b2
```

用上述 grouped_bar spec 执行。示意在 A/b1 画均值 2、样本 SD 1.414… 的柱状与误差线，n=2；A/b2 和 B/b2 分别只有 1 和 0 个有效值，n=1 不画误差线，NA 计入缺失而不插零。SVG 可编辑，PNG 真实 300 dpi，图注、绘图表与 render 校验来自同一对象；此结果并非正式案例。

## 自查与维护

维护时实际运行 grouped_bar/scatter/line，另测缺失列、混合类型、空数据、中文标签、超类别、未知系列、QC 未授权、错误图型、权限和二次运行。源值需独立复算每个聚合点/SD/SE/n；图必须打开检查，不能只校验文件存在。修改布局、映射或规格须同步脚本、示意图、渲染 CRC/像素和人工预览记录。

资源采用相对路径且为 UTF-8 文本（运行 PNG 独立），无符号链接；submission ≤50 文件、单≤500KB、总≤2MB，不创建 README。无真实公开 GitHub 作品 URL 不创建 `GITHUB_URL.txt`，不提交、发布、PR 或生成令牌；正式提交须用户另行明确授权。
