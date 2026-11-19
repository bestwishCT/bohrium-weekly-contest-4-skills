---
name: compose-graphical-abstracts
description: "把真实研究内容压缩成可追溯的单页问题—方法—核心结果—结论 Graphical Abstract，并支持保留原稿的局部编辑；适用于跨面板对象同形同色、源数据视觉编码、确定性阅读路径、真实分层 SVG 及同源 PNG/PDF 回读，不靠漂亮信息图掩盖缺失结论或统计证据。"
---

# 可追溯的单页图形摘要

## 触发与边界
- 用于把研究描述、实验记录、论文、实际源数据或已有摘要压缩为单页 `question → method → result → conclusion`，以及定向修改指定面板/对象/关系/数值/文本。
- workorder 为 `graphical_abstract`；不负责正文、重新统计或实验设计。本模板不画没有材料支持的显著性、结论、关键数值或“热闹”元素。

## 真实 case 与输出契约
1. 完整读取 `/input/case.json`、`/input/task.md`、`environment/resources.yaml`、实际研究材料/PDF/图稿/数据。PDF 直接按真实页解析，不用标题或领域标签代替材料；`figure03` 仅实际出现时采用主要生物来源。
2. 记录研究问题、实际方法、核心结果与支持结论的页/段/表/行/图，以及单页尺寸、期刊字体、分辨率、色彩和印刷要求。
3. 读取真实 `output_contract`，`/app/output/result.json` 必须保留 `case_id` 及全部原始字段/类型/层级。中间内容模型、台账、原稿备份和编译日志放 `/app/work`；最终单页 SVG、PNG/PDF、四要素/数据/修改/预览记录放 `/app/output`。

## 四要素、面板与视觉通道映射
- 先形成一句主线，再建“面板—要点—来源”表。四个角色必须恰好出现一次，每区标题和正文都应在原文引文中；次要条件移出图，不用空泛结论填第四区。
- `objects` 记录跨面板核心研究对象、名称和颜色；`method_steps` 绑定对象与真实方法；`result_records` 将对象、字段/记录、真实值和单位映射到独立柱形 `data-record-id`。颜色、长度、面积、亮度若编码数据都必须保留字段、范围、单位与缺失处理。
- `comparison` 将结论中的 higher/lower 对象绑定原文，脚本必须验证其结果确实大于/小于对应对象；相同大小或相反方向不能画成高/低。缺关键数值可画已支持的定性结果或留空，不补趋势、p 值、星号和比例尺。
- `sources[]` 存真实定位与逐字 `text`，面板、对象、步骤、数据、比较各有 `source` 和 `quote`；不把 `synthetic://` 示例台账用于正式案例。标签短、来源实，不让标签自身承担科学证据。

## 确定性生成与保源编辑
1. 按 `examples/minimal.json` 在 `/app/work/model.json` 建真实内容模型，`unresolved` 独自记录未解决问题/结果；当前确定性模板为 1200×760 的四面板单页，其他比例需真实重排并重新验证。
2. 调用安装目录内的相对资源 `scripts/draw.py`：
   ```bash
   SKILL=/skills/weekly-skill
   python3 "$SKILL/scripts/draw.py" --input /app/work/model.json --output /app/output/abstract.svg --audit /app/output/panel_value_audit.json
   ```
   脚本校验四区、引文、对象/步骤/结果归属、单位一致、方向比较和文本容量；按固定角色顺序生成 reading-path/panels/question/method/result/conclusion/annotation/background 命名层。所有文字、对象、箭头和柱独立非转曲，无整幅 `<image>` 位图，根记录模型 SHA-256。
3. 同一对象在问题、方法、结果复用相同颜色；避免只看红绿，保留对象文字，并用间距/形状形成唯一阅读路径。随机布局禁止未受控使用，重复输入模型哈希应一致。
4. 编辑已有摘要先复制、哈希并记录非目标面板、字体、图层、来源及视觉语言。限定 ID 编辑：
   ```bash
   python3 "$SKILL/scripts/edit_svg.py" --source /app/work/original.svg --changes /app/work/changes.json --output /app/output/edited.svg --log /app/output/edit_log.json
   ```
   工具拒绝原地覆盖、未知/重叠 ID、陈旧值、越权属性、DTD 和位图包装，并逐段证明非目标字节不变。数据、关系或结论改变必须同步源数据、几何、色例、四区语义与审计，禁止只改数值文本或尺度标签；无安全 ID/仅位图时如实说明保留范围，不静默整体重画。

## 真实 PNG/PDF 导出
- 先读 `environment/resources.yaml` 并检查 `command -v python3 swiftc rsvg-convert inkscape` 和版本。生成/编辑仅用 Python 3 标准库；`scripts/export.swift` 需 macOS 11+、AppKit、WebKit、PDFKit、Swift 编译器。
- 本机实测 Python 3.9.6、Apple Swift 6.3.3、WebKit 21624.5.1.11.3、Helvetica 17.0d1e1，NSFont 解析为 Helvetica。SVG 字体栈 `Helvetica,Arial,sans-serif`；其他环境用实测字体替换并重查特殊字符、字号和嵌入状态。
- 最终 SVG 经 WebKit 真实导航加载，生成 PNG、PDF，再由 PDFKit 将 PDF 回渲染：
  ```bash
  swiftc "$SKILL/scripts/export.swift" -o /app/work/export-svg
  /app/work/export-svg --input /app/output/abstract.svg --png /app/output/abstract.png --pdf /app/output/abstract.pdf --pdf-preview /app/output/abstract_pdf.png --report /app/output/export_report.json --width 1200 --height 760 --scale 1
  ```
  其他现场渲染器只在实测存在时使用，记录真实命令/版本/尺寸/分辨率/字体/色彩参数；仅有建议、代码或文件扩展名不算真实导出。

## 科学、视觉与导出三重复核
- 将四区反查回原始材料，逐对象/箭头/数字/单位/比例尺核对来源；复查结果柱长与视觉通道、结论方向和图例，确认流程箭头没有被叙述为机制因果。
- 实际打开 SVG PNG 和 PDF 回渲染，检查单页阅读路径、视觉焦点、跨面板同形色、灰度可辨、最小字号、字体替换、裁切、重叠和元素缺失；不要靠缩图隐去问题。
- 读取 PNG 签名/像素、PDF 头/页数/media box、SVG 文本/分组/无位图、源模型哈希、编辑与导出报告，保证来自同一最终版本。export pass 与 literal quote 不代替科学判定。

## 失败与部分交付
- 四区缺失、引文不符、单位混用、对象重复、矛盾比较、异常数值或文字溢出会非零失败；不自动补结论。先保存来源支持的面板、数据映射和单页局部源，列出未覆盖问题/结果。
- 文字过长、字体缺失、画布超限或导出失败应保存 SVG/成功格式和错误日志，标 partial/failed；不以微小字号、裁切或伪 PDF 隐瞒。遵守 900 秒/64 请求，不虚构增益、原生轨迹或评分。

## 最小非官方示例
`examples/minimal.json` 为两个对象、两步方法、两条真实示例记录和一个方向比较，所有 `synthetic://` 引文随文件自包含，图内标 `SYNTHETIC DEMO — NOT OFFICIAL`。将命令输入改为该文件，先输出 `/app/work/example.svg` 与审计，再真实渲染；不是正式论文摘要。

## 自查与维护
- 保留合法 YAML、唯一小写连字符 name、description 单行≤1024、正文<500 行；最多 50 个 UTF-8 文本、单件≤500KB、总≤2MB、无符号链接，验证图和编译件不进 submission。
- 变更后用不同合成材料重复跑，比较模型哈希与四层结构，另测缺数值、单位冲突、错误比较、异常 JSON、非目标保留编辑和双格式回读；新视觉通道必须同步来源映射、图例与负例。
- 不把固定论文内容烘焙入模板；无真实公开 GitHub URL 不创建链接，不提交/PR/发布，也不生成、索取或保存令牌。
