---
name: draw-evidence-pathways
description: "把原始生物机制材料转换为可追溯的实体—边—区室图并保留原稿局部编辑；适用于激活、抑制、结合、化学修饰、转运、分支与反馈通路，要求真实分层 SVG 和 SVG 同源 PNG/PDF 回读时使用，不根据常识补边、不以装饰样式代替机制方向与命名核对。"
---

# 证据约束的生物机制与通路图

## 触发与边界
- 用于线性、分支、反馈、多区室机制/通路图的生成与指定实体、边、区室、标签、样式编辑。
- workorder 为 `biological_mechanism_pathway`；不承担统计推断、实验设计或文献正文。材料未证实的实体、方向、定位与数据编码宁可标未决，也不画成事实。

## 真实输入与 case 契约
1. 完整读取 `/input/case.json`、`/input/task.md`、`environment/resources.yaml` 与实际机制文字、PDF、数据、源图。真实 PDF 无预提取答案时逐页解析，保留页/图/段定位；不从领域或图名补生物知识。
2. `figure03` 只有实际出现时才使用其主要生物来源；任何表达、显著性、置信度、时间点只用实际源数据。
3. 按真实 `output_contract` 写 `/app/output/result.json`，保留 `case_id` 及原字段/层级/类型。解析账本、中间模型、备份和编译日志在 `/app/work`；最终 SVG、PNG/PDF、来源/修改/预览记录在 `/app/output`。

## 实体消歧、边和区室台账
- 先在 `/app/work` 记录原名、规范名/同义名、物种/实体 ID（仅材料给出时）、基因/蛋白/RNA/复合物/细胞等类型及来源；同名歧义未解决不得合并，复合物不等于其成员。
- 每条边记录主体、客体、类型、方向、条件、证据强度与原文。允许模型类型为 `activation`、`inhibition`、`binding`、`phosphorylation`、`transport`；其他修饰按同一原则扩展，但须同步图例与校验。结合不自动表示激活，相关不自动表示因果，不为连通图补边。
- 区室记录边界/角色和来源；节点必须在声明区室，受体位于膜，跨膜/跨区室 transport 与反馈环按原文连线。反馈和分支不能用方向含糊的装饰箭头。
- 颜色、大小、线宽若编码表达或置信度需另有字段—范围—视觉通道映射及缺失处理；本模板统一节点色，明确不暗示表达量或显著性。
- `sources[]` 存真实定位和逐字 `text`；每个区室、实体、边的 `quote` 必须逐字包含，且边引文必须同时含主体、客体及 `activates/inhibits/binds/phosphorylates/transports` 方向词。测试之外应从实际资料构建，不从标签造 `synthetic://` 来源。

## 生成与局部保源编辑
1. 参考 `examples/minimal.json` 在 `/app/work/model.json` 建模型，只纳入已核实部分，其余写 `unresolved`。
2. 调用（技能内相对资源 `scripts/draw.py`）：
   ```bash
   SKILL=/skills/weekly-skill
   python3 "$SKILL/scripts/draw.py" --input /app/work/model.json --output /app/output/pathway.svg --audit /app/output/mechanism_audit.json
   ```
   脚本验证引文、类型、端点、区室包含、受体膜定位与 transport 跨区室，输出 compartments/interactions/entities/annotation/background 命名层；每条边、节点、标签独立，文字非转曲，无整图 `<image>` 位图。
3. 编辑现有源图先复制、哈希并保存原节点/边/区室/样式。限定 ID 的修改使用：
   ```bash
   python3 "$SKILL/scripts/edit_svg.py" --source /app/work/original.svg --changes /app/work/changes.json --output /app/output/edited.svg --log /app/output/edit_log.json
   ```
   工具拒绝原地覆盖、未知/重叠目标、陈旧预期值、非 allow-list 属性、DTD 和位图包装；分段字节证明非目标保持。任何方向、端点、区室或数据改动都要更新机制审计并重新全图复核，不能只改箭头外观。源图无安全 ID 或只有位图时，明确实际可保留范围，不静默重画或捏造图层。

## 实际导出与字体
- 先检查 `environment/resources.yaml`、`command -v python3 rsvg-convert inkscape magick soffice sips pdftoppm gs` 和可用版本。生成/编辑只用 Python 3 标准库；`scripts/export.py` 探测同一批真实渲染器并只执行实际存在的一个。
- 本机实测 Python 3.9.6、sips（CoreGraphics）SVG→PNG 1200×760、PDF 预览回渲一致；未装 rsvg-convert/inkscape 时 PDF 以 `png_wrapped_raster` 方法封装真实 PNG 像素并在报告标注。SVG 字体栈为 `Helvetica,Arial,sans-serif`，其他系统按实际字体替换，并复查中文、希腊字母、上下标和粗细。
- 同一最终 SVG 生成并回读：
  ```bash
  SKILL=/skills/weekly-skill
  python3 "$SKILL/scripts/export.py" --input /app/output/pathway.svg --png /app/output/pathway.png --pdf /app/output/pathway.pdf --pdf-preview /app/output/pathway_pdf.png --report /app/output/export_report.json --width 1200 --height 760
  ```
  报告记录真实发现顺序、命令、退出码、PNG 签名/像素、PDF 头/页数/media box 与逐文件 SHA-256；未发现的渲染器绝不声称执行，缺渲染器的输出写 `not_executed` 并保持 partial，不删除已成功格式。

## 三重质量门
1. **机制语义**：把最终 SVG 反查至实体类型/同义名、边类型/方向、分支/反馈、区室和引文；检查跨膜位置和图例数据范围，杜绝逆因果和无来源节点。
2. **布局**：实际看 SVG PNG 及 PDF 回渲染，检查箭头/T 端、虚线、边标签、进核/跨膜路线、层叠、字体/特殊字符、裁切、交叠、画布与最小字号。
3. **导出/版本**：读取 PNG 签名/实际像素、PDF 头/页数/media box，比较源/导出预览完整性、模型哈希和编辑日志。XML 合法、可导出或字面命中均不等于科学通过。

## 失败与部分交付
- 缺方向、同名歧义、非逐字证据、未知端点、区室错位、非法数据或非有限坐标返回非零，不补造。先保留已核实节点/边表和有来源的部分矢量源，并列出未覆盖机制。
- 图面过载先简化次要元素或扩展真实布局，不通过微小字号掩盖；字体/PNG/PDF 失败时保留 SVG 与已成功格式、错误日志和 partial/failed 状态。900 秒/64 请求内优先科学正确和可恢复源，不虚构成绩或工具能力。

## 最小非官方示例
`examples/minimal.json` 包含 `synthetic://` 区室/定位与五类机制陈述，演示结合、分支激活、磷酸化和抑制反馈；图内明确 `SYNTHETIC DEMO — NOT OFFICIAL`。把命令输入替换为该文件，输出先到 `/app/work/example.svg` 和审计，再按契约真实导出；不是正式机制答案。

## 自查与维护
- 保持合法 YAML、唯一 hyphen-case name、单行 description≤1024、正文<500 行；资源≤50 个 UTF-8 文本、单件≤500KB、合计≤2MB、无符号链接，验证产物不随技能提交。
- 修改图元或脚本后至少做线性、分支、反馈、多区室合成回归，测试缺方向、歧义、跨膜错误、异常输入和非目标保留编辑，再实际渲染两种格式。
- 新边类型必须同步边模型、端点符号、图例、语义反查与负例；不把未核实生物知识烘焙进模板。无真实公开 GitHub URL 不创建链接，不提交/PR/发布/处理令牌。
