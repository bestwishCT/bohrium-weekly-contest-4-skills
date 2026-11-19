---
name: build-source-schematics
description: "从真实科研材料生成或按原稿定向编辑包含、并列、上下游与时序关系示意图；当任务要求来源可追溯的实体—关系中间表示、真正分层可编辑 SVG、source-preserving edit，以及从最终 SVG 实际导出并回读检查 PNG/PDF 时使用，不用于统计计算、艺术插画或编造流程。"
---

# 来源约束的科研关系示意图

## 触发与范围
- 处理科研流程、模块包含、结构层级、概念关系及时序图的生成与指定元素编辑。
- 不替用户选择统计方法，不把机制、材料属性或实验参数从标签推断出来；纯生物通路、端口级装置和四要素图形摘要分别用专门技能。
- workorder 为 `editable_relationship_schematic`；现场题面与真实 case 优先于本说明。

## 必做输入与契约
1. 完整读取 `/input/case.json`、`/input/task.md`、`environment/resources.yaml`、实际材料与已有图稿。PDF 必须按实际页解析，记录页码；不得假定存在预提取答案。
2. 以任务材料为事实源；仅在 case 确有 `figure03` 时读其主要生物来源，图表只读实际源数据。
3. 读取 case 的真实 `output_contract`。最终 `/app/output/result.json` 原样保留 `case_id` 及契约所有字段、层级和类型，不自创字段。
4. 中间模型、备份、编译器和日志放 `/app/work`；最终 SVG、PNG、PDF、来源/关系/修改/渲染记录与预览放 `/app/output`。先保存可用基础源，再优化。

## 来源台账与关系中间表示
- 为每个来源记录 ID、文件/版本、页/段/表/图定位及原文。脚本输入的 `sources[].text` 是逐字事实摘录；每个组、实体和关系都必须有 `source` 与能在该文本找到的 `quote`，不是只有文件名。
- `groups` 表示容器，`entities` 表示独立对象；`relations` 只接受 `contains`、`sequence`、`parallel`。`contains` 从组指向实体且必须与实体 `group` 一致，`sequence` 保留从上游到下游的方向；无法确认的方向不补边。
- 图例、颜色与相对比例若承载数据，另建真实记录映射；本最小模板不以颜色暗编码未提供的数据。把缺失、歧义和冲突分别写入 `unresolved`，只绘制获支持子集。

## 生成与保留原稿编辑
1. 依据 `examples/minimal.json` 的结构在 `/app/work/model.json` 建立实际 case 模型；不得把示例对象带入正式图。
2. 执行（资源在实际安装目录中的相对路径为 `scripts/draw.py`）：
   ```bash
   SKILL=/skills/weekly-skill
   python3 "$SKILL/scripts/draw.py" --input /app/work/model.json --output /app/output/figure.svg --audit /app/output/relationship_audit.json
   ```
   脚本检查引文、ID、端点、包含/布局一致性与画布；生成 `background/groups/relations/entities/annotation` 命名层，实体、文字、各条边均独立，文字非转曲，禁止整图 `<image>`/base64 位图。
3. 编辑输入源图时先复制原稿并计算哈希。只在请求允许的元素 ID 上应用文本/几何/样式修改：
   ```bash
   python3 "$SKILL/scripts/edit_svg.py" --source /app/work/original.svg --changes /app/work/changes.json --output /app/output/edited.svg --log /app/output/edit_log.json
   ```
   `changes.json` 为唯一目标数组；拒绝原地覆盖、未知 ID、陈旧预期值、重叠修改、DTD/实体和位图包装。日志以分段字节比对证明非目标保持。语义/数值修改还须更新关系模型与台账；不能仅改标签遮掩冲突。已有图无安全 ID 时用能保留结构的实际工具，不能静默整图重画；位图输入只能报告可恢复范围。

## 真实导出与回读
- 每次先检查 `environment/resources.yaml`、`command -v python3 swiftc rsvg-convert inkscape` 与版本。生成/编辑只用 Python 3 标准库；附带 `scripts/export.swift` 需要 macOS 11+、AppKit、WebKit、PDFKit 与 Swift 编译器。
- 本机实测：Python 3.9.6；Apple Swift 6.3.3；WebKit bundle 21624.5.1.11.3；Helvetica 字体名表版本 17.0d1e1，实测解析为 `Helvetica`。其他平台不得声称这些版本可用；SVG 声明 `Helvetica,Arial,sans-serif`，替换字体须记录并复查特殊字符。
- macOS 路径实际加载最终 SVG，生成 PNG、PDF，再用 PDFKit 将 PDF 回渲染：
  ```bash
  swiftc "$SKILL/scripts/export.swift" -o /app/work/export-svg
  /app/work/export-svg --input /app/output/figure.svg --png /app/output/figure.png --pdf /app/output/figure.pdf --pdf-preview /app/output/figure_pdf.png --report /app/output/export_report.json --width 1200 --height 760 --scale 1
  ```
  尺寸必须随契约修改。若使用现场其他真实渲染器，记录实际命令与版本，并从同一最终 SVG 导出；不得伪造成功。仅 XML 可解析不等于导出通过。

## 三重质量门
1. **语义**：把 SVG 反查回组/实体/关系及逐项引文；确认包含边界、方向、分组、图例/数据映射、单位和子图编号。导出成功不能替代科学复核。
2. **布局**：实际打开 SVG/PNG 与 PDF 回渲染，比较标题、文字、字体替换、箭头端点、裁切、重叠、透明度、细线和最小字号；像素尺寸以导出报告为准，Retina 像素倍率不当作尺寸错误。
3. **导出**：检查 PNG 签名和解码尺寸、PDF 头/页数/media box、预览非空、源哈希、导出记录和编辑日志为同一版本；契约要求 Other formats 时再按真实参数输出。

## 失败与部分交付
- 无来源、引文不逐字、未知端点或非法坐标会返回非零；不要补猜。先交已证实关系子集、台账、`unresolved` 与可编辑 SVG，再尝试导出。
- 字体缺失或版面过载必须调整并重查，不缩到不可读；PNG/PDF 任一失败保留真实 SVG、已成功格式和错误日志，并将结果标为 partial/failed，禁止宣称完成。
- 900 秒/64 请求内优先保源头文件；每例不伪造执行时间、软件能力、评分或对照。

## 最小非官方示例
`examples/minimal.json` 自带 `synthetic://` 台账、两个容器及三个有向时序边，图内注明 `SYNTHETIC DEMO — NOT OFFICIAL`。用上面生成/导出命令，将 `--input` 改为 `$SKILL/examples/minimal.json`，输出先写 `/app/work/example.svg` 与审计，再在契约要求时导出；它不是正式案例答案。

## 自查与维护
- 保持 frontmatter 合法、name 唯一小写连字符、description 单行且不超过 1024 字、正文少于 500 行；资源不超过 50 个 UTF-8 文本、单件 500KB、总计 2MB，无符号链接或测试产物。
- 改模型/脚本后用合成 fixture 重复生成并比较结构与模型哈希，测反向/缺失来源/未知端点、原稿保留编辑和异常输入；重新实际渲染两种格式而非只跑单元测试。
- 新图形语法必须同步更新解析、图例、审计和负例；无真实公开 GitHub URL 不编造链接，不提交、不发布、不处理令牌。
