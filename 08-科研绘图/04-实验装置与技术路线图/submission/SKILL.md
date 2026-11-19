---
name: route-apparatus-workflows
description: "从实际装置清单、端口说明和编号工艺生成装配连接图与技术路线，并可保留原稿定向修改；适用于物料/能量/信息流向、上下游、并行依赖、真实端口和分层可编辑 SVG/PNG/PDF 的任务，不因部件邻近而接管，不臆造数量、条件或步骤。"
---

# 来源约束的实验装置与技术路线

## 触发与范围
- 用于装置结构/装配图、编号工艺流程、阶段技术路线的生成，以及指定部件、端口、连接、步骤或样式的局部编辑。
- workorder 为 `apparatus_and_technical_route`。不评价实验方案本身的科学性、不做数据拟合，不把三类图无依据地合并。

## 真实输入与契约
1. 完整读取 `/input/case.json`、`/input/task.md`、`environment/resources.yaml`、实际部件表/装配说明/工艺文字/已有源图和 PDF。PDF 按真实页解析，不假定有答案文本层；不根据“某反应器”标签补结构。
2. 记录文件/版本、页/段/表/步骤号、目标尺寸/分辨率与编辑范围。`figure03` 仅现场存在时使用主要生物来源，任何数据编码来自实际源数据。
3. 从真实 `output_contract` 写 `/app/output/result.json`，保留 `case_id` 和全部字段、层级、类型，不自创。模型/备份/编译日志在 `/app/work`，可编辑源、导出件、来源/修改/预览证据在 `/app/output`。

## 实体—端口—连接—步骤模型
- `components`：材料原文中的准确部件名、实例 ID、类型/数量与参数。多个同类部件不得合并，缺参数不靠相似装置补齐。
- `ports`：每个接口绑定所属部件、标签、侧边与来源，脚本计算可编辑端口坐标。`connections` 只按上游端口→下游端口建立，记录 `material/information/energy`、方向及引文；位置接近不是连接证据。
- `steps` 保留真实 Step 编号、名称和顺序；`dependencies` 表达先后与依赖，forward dependency 必须编号递增。并行、循环、终止和阶段输入/输出先在中间表还原，再选支持的真实路线；本最小直线模板不能安全表达回流时必须扩展弧线或标为未决，不画重叠假路线。
- 来源台账 `sources[]` 含定位与逐字文本；每个部件、端口、连接、步骤及依赖都有 `source` 和非空 `quote`，连接引文同时出现两端口和流类。图例仅解释已有流类，不暗编码其他参数；物料实线、信息虚线、能量点线，步骤箭头使用独立灰色语义。
- 缺失接口、冲突连接、未知编号分别列入 `unresolved`，交付获支持子集；不生成断头箭头补齐图面。

## 生成与保留原稿编辑
1. 按 `examples/minimal.json` 在 `/app/work/model.json` 建实际模型。当前示例含装置区和独立技术路线区；纯装置/纯路线任务按真实范围删减并保留相应审计。
2. 调用技能相对资源 `scripts/draw.py`：
   ```bash
   SKILL=/skills/weekly-skill
   python3 "$SKILL/scripts/draw.py" --input /app/work/model.json --output /app/output/apparatus.svg --audit /app/output/port_route_audit.json
   ```
   脚本验证引文、部件/端口归属、端点存在、方向与流类、步骤编号/依赖和画布；生成 route/connections/components/annotation/background 命名层，节点、端口、管路、步骤与文字独立，非转曲，无嵌位图 SVG。
3. 编辑先保存原稿和结构/哈希，不能为“更漂亮”整体重排。指定元素修改：
   ```bash
   python3 "$SKILL/scripts/edit_svg.py" --source /app/work/original.svg --changes /app/work/changes.json --output /app/output/edited.svg --log /app/output/edit_log.json
   ```
   仅 allow-list 文本/几何/样式属性可改；拒绝原地覆盖、未知/重叠目标、陈旧预期值、非法结构与位图包装，日志证明非目标字节保持。连接或步骤语义改变必须同步端口台账、x/y 端点和审计再全图复核；其他源格式用实际可保留工具，位图输入如实报告不可恢复图层。

## 实际 PNG/PDF 导出
- 先读现场工具清单并检查 `command -v python3 swiftc rsvg-convert inkscape`。Python 3 标准库足够生成/校验/编辑；`scripts/export.swift` 需 macOS 11+ AppKit/WebKit/PDFKit 与 `swiftc`，其他系统必须使用现场真实存在的渲染器。
- 实测本机 Python 3.9.6、Apple Swift 6.3.3、WebKit 21624.5.1.11.3、Helvetica 17.0d1e1 且实际解析为 Helvetica；不可外推。SVG 字体为 `Helvetica,Arial,sans-serif`；中文、单位、上下标和细线在有字体替换时重查。
- 同一最终 SVG 真实加载，导出 PNG/PDF 并用 PDFKit 回渲染：
  ```bash
  swiftc "$SKILL/scripts/export.swift" -o /app/work/export-svg
  /app/work/export-svg --input /app/output/apparatus.svg --png /app/output/apparatus.png --pdf /app/output/apparatus.pdf --pdf-preview /app/output/apparatus_pdf.png --report /app/output/export_report.json --width 1200 --height 800 --scale 1
  ```
  其他工具须记录实测版本和导出参数，不能只生成文件扩展名；契约要求的 PDF/PNG 分辩率、色彩和透明参数另按真实能力核对。

## 科学、图面与导出复核
- 把 SVG 反查回部件名/数、实例、端口、连接上下游、分流/合流/回流/废物流、步骤顺序和阶段依赖；每条新增/丢失线均要解释，不能以箭头“好看”修正方向。
- 实际打开 SVG PNG 和 PDF 回渲染，比较字体、单位、细线/箭头端点、端口对齐、管线交叉、步骤编号、图例、画布裁切和重叠；不能只看低分辨率截图。
- 独立检查 SVG XML/命名层/无 `<image>`，PNG 签名及像素，PDF 头/单页 media box，源与预览版本、映射与模型哈希；科学判断不委托给字面或导出 pass。

## 失败与部分交付
- 缺部件/参数/引文、未知接口、方向类型不明、非法步骤或画布溢出返回非零；不简单猜默认值。先保存已核实部件—接口—连接—步骤清单和部分可编辑 SVG。
- 字体/导出/图面过载失败保留真实 SVG、已成功导出格式、错误日志与未完成项，标 partial/failed；接口冲突须单列，不把多个输入静默合并。900 秒/64 请求内优先正确最小结构，不伪造效率、分数或软件状态。

## 最小非官方示例
`examples/minimal.json` 是 `synthetic://` 三部件主物料线、传感器信息反向输入与三步编号路线；图内标 `SYNTHETIC DEMO — NOT OFFICIAL`。把生成命令输入改为该文件，先输出 `/app/work/example.svg` 与审计，再按示例尺寸真实导出；不是正式装置答案。

## 自查与维护
- 保持合法 YAML、唯一小写连字符 name、单行 description≤1024、正文<500 行；≤50 个 UTF-8 文本、单件≤500KB、总≤2MB，无符号链接，测试产物不入 submission。
- 修改后做不同流向、并行/回流布局和重复运行，测缺失/冲突接口、编号逆序、异常 JSON、非目标保留编辑与真实重导出；新端口/图元必须同步图例、模型、审计和负例。
- 不在模板烘入未经核验的装置关系；无真实公开 GitHub URL 不建链接，不提交/PR/发布/处理令牌。
