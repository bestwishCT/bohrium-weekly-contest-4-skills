---
name: adapt-academic-ppt-template
description: Adapt real academic content to an actual PPTX master/layout system with template introspection, explicit same-master batch adaptation or traced cross-template migration, font discovery, readable type/spacing constraints, aspect-safe figures, all-page deviation audits, and final PDF/PNG rendering. Use for template inheritance, master/layout mapping, font/color corrections, overflow fixes, cross-page consistency, or paper-plus-template-to-deck work; not for screenshot-based pseudo-editing.
---

# Academic PPT template and format adaptation

把内容真正接入指定 master/layout、占位符与主题，不以默认幻灯片、整页截图或只修前三页冒充适配。内容来源仍服从真实论文/数据，排版检查不替代科学核验。

## 触发、输入与不可变合同

在“套用会议/单位模板”“把已有 deck 迁到新母版”“统一中英文字体和字号”“修长标题/多图/表格溢出”“整册一致性与导出复核”时触发。若任务主要是研究主线重建、局部科学内容编辑或青众问答组织，分别选对应专项；本技能的中心是版式结构、跨页一致性和渲染闭环。

逐例完整读取 `/input/case.json`、`/input/task.md`、`environment/resources.yaml`、实际来源 PDF/图表、真实模板与任务提供的 actual starting deck。实际 PDF 要文本解析，并渲染检查图注/扫描页；不能从标签猜内容。模板结构诊断、原稿、候选和日志放 `/app/work`，最终可编辑 PPTX、真实 PDF/逐页 PNG、结构/字体/偏差记录、页级来源及 `result.json` 放 `/app/output`。

锁定 case 的 `case_id`、全部原始 `output_contract` 字段/层级/类型、`audience`、`time_minutes`、`slide_count`、允许页型、实际模板/起始稿路径及 full-source/uncertainty 条款。数值、备用页规则和字体策略不得从示例继承。最终 `result.json` 只填这些契约字段与 case_id，保留类型，不造额外顶层字段。

## 先探查真实模板，不凭截图猜继承

1. 以 PPTX 包和 python-pptx 读取 slide size、slideMasters、theme、layouts、每页 layout 关系、占位符 type/idx/几何、主题字体/色和实际页脚/页码。`adapt_template.py` 生成 `template_probe.json`，记录源/目标 SHA-256、主题字体、master part、每个 layout part 和槽位边界。
2. 以 case 内容确定封面、章节/过渡、方法、结果、结论等页型，选择实际存在的 layout 名；标题填 idx=0，正文 idx=1，原生图表默认需要真实 idx=2。没有图表槽时选择其他真实双内容 layout 或按明确授权采用原生对象，不能覆盖页脚，不私改母版。正文槽缺失且本页有计划要点时脚本直接拒绝，不能静默丢内容；页序 `slide` 与每页非负 `seconds` 也逐页核验。
3. 从模板实际字号/版心/字体策略建立 `style_rules`：`title_font_pt`、`body_font_pt`、`min_font_pt`，及任务授权的 color/spacing。示例的 30/20/18 仅为合成 fixture，不是正式规则。先缩句、换行、减层级、换真实 layout，再在规则允许范围小调；不把全文缩成不可读字。
4. 探查实际字体（Linux `fc-list`，macOS `system_profiler SPFontsDataType -json`，或 case 明示工具），记录 family、字体文件/version 与可用性。缺字体或探测不可用默认阻塞，不声称回退后无影响；嵌入字体只在实际导出/结构核验可证时记录，本脚本不声称嵌入。
5. 内容—证据页映射：每页保留 claim、原始 PDF 页/精确 quote、图号与数值单位；版式任务不得顺手放大结论。`notes.transition/emphasis/backup` 写讲述过渡、证据强调和额外来源/排版降级信息，不重复页面，不编造科学内容。

## 三种模式，明确母版是否授权改变

### `existing-deck-edit`

从 actual starting deck 副本逐页适配，要求原稿哈希 match、固定页数 match，且模板与起始稿的 master/theme 内容哈希一致。对不同母版会拒绝静默覆盖。该模式保留实际 master、继承链和未更改授权的媒体，不搬入匿名模板。

### `from-template`

仅用于任务给定的真实零示例页模板，`starting_deck` 与该模板相同。按实际 layout 创建固定页数和各槽位内容，不创建无来源模板。

### `migrate-to-template`

只有 case 明确把不同真实起始稿内容迁入另一个真实零页目标模板时使用。每页 `origin` 必须含相同 `source_slide` 和起始稿现有文本/备注中的 `source_excerpt`（逐字短摘）；起始稿页数、目标页数都服从 slide_count，不能遗漏末页。脚本实际读取源 deck，验证摘录后在目标模板中重建原生文本/形状/图表，同时保存输入基线、source→target layout 关系和 authorized master 变化。不同母版没有该显式模式即失败。这是可追溯内容迁移，不是复制主题关系的虚假“继承原母版”。

全部模式保存后均检查最终 master/theme 哈希确实等于**目标模板**，每页 `slide_layout.name` 等于内容映射，所有页面都处理并记录。源未授权的母版/媒体变化、跨模板漏摘录或错 layout 都停止，不生成最终文件。

## 批量适配与整册审计

对每页依次填真实槽位、写原生备注，应用可读字号阶梯并关闭自动无限缩字。原生图表的默认文字与坐标轴刻度也服从 `min_font_pt`，不再使用独立的 12pt 硬编码。图像按容器等比 fit，默认零裁切；若有实际授权裁切必须记录区域且目检轴、图例、单位和图注全部保留。原生图表只接受具有真实 categories/series 的 PowerPoint chart + 内嵌 workbook，不从位图猜数；轴标题/图例/单位保持源含义。也接受按物理 PDF 页裁出的实际源图（`kind: figure` + `source_pdf_page` + PDF 点坐标 `source_pdf_crop`），脚本从当前 PDF 重裁并核对声明 PNG 的 SHA-256，且要求同页同 Figure 引用，以容器等比放置且零裁切；不能使用不在工作区内的匿名图片。每页 `sources` 必须非空并含精确引文。可用顶层 `source_sha256` / `template_sha256` 绑定已审版本；未提供外部预期哈希时仅证明本次构建使用同一源文件，不声称验证官方源。`source_pdf_ledger.json` 覆盖每个物理页并单列空文本/OCR 待读页；没有实际 OCR 不得把扫描页当作已读证据。

`page_deviations.json` 必须覆盖**每一页**并报告：

- 实际/计划 layout、占位符 idx、对象几何与 slide 出界；
- 文本字号、低于最小可读字号、基于字体与框高的保守溢出估算；
- 源图原始/显示宽高比和 crop、native chart 可编辑性；
- 原生备注是否存在、所有修复及仍未解决问题。

估算不是实际文本测量或视觉通过。出界/溢出/裁切/比例问题应先修订，保留带问题候选于 `/app/work` 与诊断，不覆盖最后已验证最终稿。

## 调用、真实导出与逐页复核

技能安装根目录 `/skills/weekly-skill`，只按相对路径引用脚本。先以 resources.yaml 确认实际 Python、字体与原生 Office；生成需要 `python-pptx>=1.0`，页检需要 PyMuPDF、Pillow。默认用 LibreOffice/soffice 导出；没有 soffice 时可检查已真实渲染的外部 PDF，但必须同时提供 `--external-pdf` 与 `--external-receipt`。安装按实际环境说明；不假设全局已装。

```bash
export SKILL_DIR=/skills/weekly-skill
python3 "$SKILL_DIR/scripts/adapt_template.py" \
  --spec /app/work/adapt_plan.json --outdir /app/output \
  --work /app/work/adaptation
python3 "$SKILL_DIR/scripts/render_and_check.py" \
  --pptx /app/output/adapted_deck.pptx --outdir /app/output \
  --work /app/work/render
```

渲染脚本先核对 `adaptation_record.json` 的最终 PPTX SHA-256，再生成或检查 PDF、逐页 PNG/contact sheet，检查页数和比例、文字/媒体框、图片/图表槽位实际内容和明显溢出并记录全部哈希。外部 PDF 回执必须含 renderer 名称/版本、完整命令或实际 tool invocation、退出码 0、非空执行日志/回执，以及匹配当前文件的 PPTX/PDF provenance SHA-256；输入 PDF 不预删，失败不登记成品。工具或有效证据缺失立刻非零、`not-passed`；哈希变化或陈旧预览保持 `partial`，不接受 HTML、SVG 拼版或手工图充当导出，也不要求先删可编辑 deck。

逐页查看最终 PNG/PDF，不只读 OOXML：检查长标题/末行/表格、项目符号/公式、字体替换与乱码、图像分辨率和裁切、母版页脚/页码、前后页标题位置/留白/层级一致性以及 PPTX↔PDF 页序对应。自动 `automated-render-passed-visual-required` 后仍需实际视觉复核和科学核对；只有 `--visual-review` 指向与当前 PPTX/PDF/逐页 PNG 哈希绑定、逐页 `actual_image_read: true` 且观察非空的 JSON，并且无其他待审项时才可 `completed`。真实 OCR、字体、科学证据等待审项必须保留；自动渲染或视觉复核都不等于科学内容验证。写回仅限原 output_contract 字段，递归保留 case_id 和其他深层数据。

## 最小非官方示例

`examples/adapt_plan.minimal.json` 是 SYNTHETIC 内容从另一主题起始稿迁入指定 Arial 主题、共 5 页的跨模板示例；每页都给出源 deck 逐字摘录，最后两页也实际适配，非只修封面。复制到 `/app/work/adapt_plan.json`，替换真实起始稿 SHA-256，并以当前 case 数值、规则、模板和 output_contract 覆盖示例。

真实运行应生成指定 master/theme 的 PPTX、page_deviations/template_probe/font_report、可编辑原生图表与原生备注，之后才有最终渲染件。验证 fixture、命令/退出码、断言与预览属于 verification，测试产物不得复制进 submission。

## 失败恢复

- 缺 layout/槽位、master/theme 冲突：保留输入和 probe，换真实 layout 或显式迁移，不硬改未知母版。
- 跨模板来源摘录缺页/错页：拒绝对应页并保存全页决策，不回退到默认 Presentation。
- 字体缺失：记录 `font_report.json` 未通过，采用环境明确可用字体后重测；不将字体替换伪装成无影响。
- 长文溢出：先缩句/换行/换 layout，保持下限；必要时将支撑移入允许备注，不靠超页解决。
- 图表缺失/源图失效：标缺失与不确定性，引用真实可用原图或保留无图，不造数据曲线；严禁拉伸。
- 导出失败/乱码：保留 actual starting baseline、最后可编辑候选及问题页，状态 partial；修复当前最终 PPTX 后重新导全套 PDF/PNG，旧预览作废。

## 自查与维护

核对 case 固定约束与原始契约、每页原生对象和目标继承、所有页（尤其后两页）风格/页脚/留白、来源数字/图号、备注、字体真实发现、图像比例与裁切、逐页实渲和视觉观察。任何内容或模板改变须联动重建证据、偏差和预览。

维护时同时跑同母版适配、显式跨模板迁移、错误摘录应失败、全页型及缺字体应诚实未通过等回归；记录实测 Python、`python-pptx`、renderer、PDF 库和字体/模板版本。保持资源自包含、无符号链接和少量文本。无真实匿名可打开的 GitHub URL 就不建 `GITHUB_URL.txt`；不提交、PR、发布或处理令牌。
