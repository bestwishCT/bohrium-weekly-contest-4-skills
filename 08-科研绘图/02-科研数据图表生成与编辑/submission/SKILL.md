---
name: map-source-value-charts
description: "从实际科研表格与统计结果生成分组数据图并保留原稿定向修订；适用于需要源文件/行/字段到柱形、点或误差线的双向映射、坐标和单位校验、真实可编辑 SVG/PNG/PDF 及同源回读的任务，不做未经请求的均值聚合、显著性推断或实验设计。"
---

# 源记录驱动的科研数据图

## 触发与边界
- 用于柱、线、散点、热图等数据编码的生成、图例/样式调整、单位/误差标注复核，以及 existing chart 的指定数据/标签/样式编辑。
- workorder 为 `source_value_chart`。不负责统计方法选择与 p 值计算；源数据的科学定义和真实图表规范优先于模板。
- 附带生成器实现 `grouped-bar`、零基线、单记录柱和误差线；其他图型沿用同一记录映射原则并另做实际校验。

## 读取真实 case 与输出契约
1. 完整读取 `/input/case.json`、`/input/task.md`、`environment/resources.yaml`、表格/图稿/PDF/期刊或团队规范。PDF 按真实页解析；不从文件名或领域标签猜字段。`figure03` 仅在现场存在时采用主要生物来源。
2. 锁定数据文件版本、哈希、sheet/表/行/记录 ID、字段、长宽表结构、单位、空值、异常标签及画布/分辨率/色彩要求。
3. 读取真实 `output_contract`；`/app/output/result.json` 保留 `case_id` 和全部原字段、类型、层级，不发明字段。中间表/模型/编译日志放 `/app/work`，最终数据副本、映射、SVG、PNG/PDF、编辑/预览记录放 `/app/output`。

## 双向映射与科学检查
- `sources[]` 记录定位与逐字文本；图表定义、系列、每个记录还要有 `source` 与原文 `quote`。数据缺失与冲突分别列入 `unresolved`，不得插值或补均值迎合图形。
- 每个数据记录使用 `record id → category/series/value/error → source/quote → svg id → geometry`。SVG 柱形另有 `data-record-id`、`data-source`、`data-value`、`data-field`，支持从源行找图元和从图元找回源行。
- 生成器要求完整 category×series 矩阵、非负有限数值、零基线和明确 `SD/SE/CI/none`；不自动聚合，不新增星号、p 值、样本量或趋势。已有统计标记若来自材料，只沿用其定义并注明，不为其科学有效性背书。
- 核对轴范围/刻度、字段量级、系列顺序、图例颜色、误差帽与源审计的高度换算；颜色不足再用形状/文字冗余，色觉与灰度下均能区分。

## 生成、样式与保源编辑
1. 参考 `examples/minimal.json` 在 `/app/work/model.json` 建真实 case 模型。当前确定性模板固定 1200×760；其他契约必须重排并重新目视/尺寸验证，不以拉伸掩盖溢出。
2. 资源使用安装目录内的相对路径 `scripts/draw.py`：
   ```bash
   SKILL=/skills/weekly-skill
   python3 "$SKILL/scripts/draw.py" --input /app/work/model.json --output /app/output/chart.svg --audit /app/output/value_glyph_audit.json
   ```
   输出 `grid/axes/bars/labels/legend/background` 命名层、独立柱与误差线、可编辑非转曲 text，根有模型 SHA-256；拒绝 `<image>` 位图伪造可编辑性。
3. 样式由配置/样式文本改动，不手调截图。中文、希腊字母、上下标与长标签需现场字体验证；必要时换已实际存在的字体并重查布局。
4. 原图先复制/哈希。指定标签或样式可用：
   ```bash
   python3 "$SKILL/scripts/edit_svg.py" --source /app/work/original.svg --changes /app/work/changes.json --output /app/output/edited.svg --log /app/output/edit_log.json
   ```
   allow-list 只修改目标 ID；拒绝原地覆盖、陈旧值、未知 ID、重叠编辑与位图包装，日志逐段证明非目标字节不变。数值改动必须联动柱高/点位置、误差、刻度及值标签，并更新双向审计；优先生成模型重绘，同时保留目标之外的原样式，禁止只改 `data-value` 冒充数值更新。任意源图若无安全 ID 或没有矢量层，清楚说明可恢复范围，不谎称保留原图层。

## 实际 PNG/PDF 导出
- 先检查 `environment/resources.yaml` 与 `command -v python3 swiftc rsvg-convert inkscape`，记录实际可用工具。Python 脚本只用标准库；`scripts/export.swift` 依赖 macOS 11+ AppKit/WebKit/PDFKit 与 `swiftc`。
- 本机实测为 Python 3.9.6、Apple Swift 6.3.3、WebKit 21624.5.1.11.3、Helvetica 17.0d1e1 且解析为 Helvetica；不是对其他环境的承诺。SVG 字体栈为 `Helvetica,Arial,sans-serif`；字体替换或嵌入能力必须记录并按契约另查。
- 从最终 SVG 真实加载并创建 PNG/PDF，PDF 再用 PDFKit 回渲染：
  ```bash
  swiftc "$SKILL/scripts/export.swift" -o /app/work/export-svg
  /app/work/export-svg --input /app/output/chart.svg --png /app/output/chart.png --pdf /app/output/chart.pdf --pdf-preview /app/output/chart_pdf.png --report /app/output/export_report.json --width 1200 --height 760 --scale 1
  ```
  其他渲染器只在实测存在时采用，记录命令、版本、分辨率/色彩/透明参数；不能只写出扩展名或截屏冒充分辨率合规。

## 语义、布局、导出三重复核
- 逐记录核对文本与属性的值、几何高度、零基线/刻度、单位、图例—系列—色彩、误差定义及样本/显著性来源；源修改后映射与图应同步。
- 打开源 PNG 和 PDF 回渲染预览，比较乱码、裁切、文字/数值遮挡、多面板错位、线条/误差帽丢失、色彩与画布尺寸；Retina 的像素倍率以报告为准。
- 独立读取 PNG 签名/尺寸、PDF 头/单页 media box、SVG XML/命名层、模型与编辑哈希。科学正确不能由 XML/导出 pass 推断。

## 失败、部分交付与预算
- 缺列、空值、非有限值、重复记录、单位混用/量级异常、引文不符会非零失败；不得擅自清洗后称为源数据。保存已核实记录/映射和获支持的局部图。
- 字体、遮挡或导出失败时保留 SVG 与成功格式，真实错误日志和未通过项必须进入 partial/failed 状态。先保存基础图再美化，遵守每例 900 秒与 64 请求；不虚报耗时、增益、分数或软件状态。

## 最小非官方示例
`examples/minimal.json` 是完整 `synthetic://` 两组两系列四记录例子，含 SD 和单位；图内标 `SYNTHETIC DEMO — NOT OFFICIAL`。把命令输入改为 `$SKILL/examples/minimal.json`，先输出 `/app/work/example.svg` 与审计，再按需要导出；它不是固定案例或统计结论。

## 自查与维护
- 保持 YAML 合法、唯一小写连字符 name、description 单行≤1024、正文<500 行；最多 50 个 UTF-8 文本、单件≤500KB、总量≤2MB，无符号链接，确认测试图未进入 submission。
- 改映射后重跑同数据并核对模型哈希/几何，另测缺列、异常值、错误单位、重复记录、非目标保留编辑与导出回读；新增图型必须同步图例、双向映射、版面和负例。
- 无真实公开 GitHub URL 不创建链接；不自动提交/PR/发布，不索取或保存令牌。
