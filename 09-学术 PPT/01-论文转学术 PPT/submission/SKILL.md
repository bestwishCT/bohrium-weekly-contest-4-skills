---
name: paper-to-evidence-slides
description: Convert an actual research paper PDF into a native editable academic PPTX with a contribution-led narrative, exact page-level PDF evidence, source figures or data-bound charts, useful speaker notes, and final-file PDF/PNG checks. Use for paper-to-PPT, manuscript-to-defense or journal-club work; not for mere copyediting, generic slide polishing, or template-only restyling.
---

# Paper to evidence-backed academic slides

把论文的连续论证重写成可讲且可回溯的页面，不把摘要切块，不把相邻课题的润色、绘图或纯模板适配冒充论文转换。

## 触发、输入与输出

触发：用户提供论文/科研材料及模板，要求“论文转 PPT”“按原文做组会/答辩”“保留图表证据和讲稿”时。若只有匿名页纲，先取得并实际读取真实材料；若任务只有改一页措辞、母版修复或已有内容面向新听众取舍，分别用编辑、模板或内容组织流程，不套用本流程。

每个案例完整读取 `/input/case.json`、`/input/task.md` 和可定位的 `environment/resources.yaml`，记录其中真实资源、工具及版本。逐字读取任务指定的全部论文 PDF、图表、补充材料、模板，以及任务要求时提供的 actual starting deck。不能按文件名、领域标签或本文件示例推断研究内容。中间提取、证据表和版本放 `/app/work`；最终可编辑 PPTX、PDF、逐页 PNG、证据图、讲稿/备注、技术记录及 `result.json` 放 `/app/output`。

先建立 `/app/work/case_contract.json`，至少记录 `case_id`、原始 `output_contract`、`audience`、`time_minutes`、`slide_count`、是否含备用页、模板/起始稿路径、全部 full-source 和 uncertainty 条款。**全部数值和条款以当前 case 为准，不采用本示例的示意值，也不擅自更改 master/layout。**

输出契约处理：把 case 的 `output_contract` 原字段、嵌套层级及类型保真物化为 `deck_spec.json.output_contract` 的待填对象；最后只填这些键和 `case_id`，不新增公开契约之外的字段。将生成与真实渲染状态、来源和不确定性写入契约已有对应字段；类型不合或字段缺失时停止并报告，不能用自造 `extra` 绕过。

## 论文专属证据转换

1. 用 `resources.yaml` 中实际可用的 pdf-text-tool 解析全文页文本，以 pdf-render-tool/OCR 检查双栏、扫描页、公式和图注。工具能力不足时记录未读页；不得声称已读全文。
2. 在 `/app/work/source_index.jsonl` 为每个可引用单元记录：PDF 物理页码、章节/段首、原文精确短引、图/表/公式号、图注、作者归属、`own-result|prior-work|schematic|planned|uncertain`。全文与图表阶段先保存可恢复检查点。
3. 建立本文“问题→方法选择→关键证据→边界”链。优先依据 Discussion/Conclusion 中作者自己的贡献和限定，而不是照摘要顺序。区分相关性与因果、样本内验证与外部推广、实验图与示意图。
4. 选择页序：开场给出研究问题；随后建立最小必要背景；解释为何该方法能回答问题；按支撑关系展示主要结果；以同证据强度的结论与限制收束。每页只许一个可证主张，非必要的相关工作、次要稳健性结果移入允许范围内的备注，不能靠加页或空页凑数。
5. 每页映射标题、每个要点、数字、单位、图号及讲稿补证到源索引。无法回指原文的句子删除或标为未决；不得凭印象取整。引用他人研究必须标作者/引文，不得写成“本研究”。
6. 优先使用原论文图或保留完整图注的可追溯局部；裁切必须经任务允许且不丢轴、图例、单位和必要条件。只有结构化源数据和语义核实均具备才建立原生图表；系列名称、类别、数值和单位必须逐项对应，位图不可猜数。
7. 在 `deck_spec.json` 写 `role`（opening/question/method/evidence/synthesis/limitation）、`claim`、`points`、`sources`、`layout`、`seconds`、`notes` 与可选 `visual`。`sources[].quote` 是源 PDF 的逐字短引，不能写得像答案。
8. 备注分写 `transition`（如何从上一页进入）、`emphasis`（口头解释与为何重要）、`backup`（被追问时的额外源证据/限制），不得复制可见要点，也不得创造新结论。为每页按固定时长分配秒数，检查总预算。

## 真实模板内生成

`build_paper_deck.py` 只从指定真实模板/起始 deck 读 master/layout，按名称选择实际 layout，填原生标题、正文/对象占位符；图表是带内嵌数据的原生 chart，源图保持原始宽高比、默认不裁切。不得用整页 PNG 代替可编辑内容。缺 idx=2 图表占位符时会拒绝而非越过版心；应另选实际双内容 layout 或据授权声明原生替代方案，不私改母版。

先探测 `Presentation(template).slide_layouts` 的真实名称、占位符类型/idx、几何和主题。任务要求编辑已有 deck 时，从该 actual starting deck 的副本保留已有材料，不能用匿名默认 `Presentation()`。模板若为加密切换受信任 renderer；缺字体先查资源，禁止声称嵌入未嵌入字体。保持主题字体与颜色；正文不小于模板可读层级，先改写和换 layout，最后才在规则允许范围小调。

先跑完整最小版，所有未通过科学证据或结构检查的问题保留记录，不得把失败版覆盖已验证版本。

## 调用

技能安装根目录为 `/skills/weekly-skill`，所有资源从该目录按相对路径解析。先读资源清单、核实可用 Python 与库；可用时安装命令只应按该清单，不默认可联网或全局安装。

```bash
export SKILL_DIR=/skills/weekly-skill
python3 "$SKILL_DIR/scripts/build_paper_deck.py" \
  --spec /app/work/deck_spec.json --outdir /app/output
python3 "$SKILL_DIR/scripts/render_and_check.py" \
  --pptx /app/output/paper_talk.pptx --outdir /app/output \
  --work /app/work/render
```

脚本需要 `python-pptx>=1.0`；使用源图时还需 Pillow。渲染检查需要 LibreOffice/`soffice`、PyMuPDF 和 Pillow。若案例资源指定不同原生 Office renderer，按说明从**最终 PPTX** 导出，再用 PDF 页渲染 PNG；不能将 HTML、 SVG 或手工拼图当作渲染。README 式手工步骤不是依赖。

当前结构检查含精确页数、PPTX 重开、原生可编辑对象、备注、layout 名、几何边界与保守文本框估算；估算不等同于视觉无误。真实渲染脚本检查页数/比例、PDF 文本缺失或越界、源图裁切并输出逐页 PNG 和 contact sheet。运行后逐页查看最终预览，核对科学内容、公式、乱码、项目符号、字体替换、图像轴/图例/裁切、模板页脚及 PPTX-PDF 对应。自动“通过”后仍标 `visual_review: required`，只有实际看过才可改为已复核。

若渲染器不存在，脚本以非零码结束并诚实写 `render_report.json` 的 `not-passed`，不产生伪 PDF/PNG。保留 PPTX 和证据映射，状态为 partial，将限制写进原输出契约对应字段；渲染成功后同步更新最终记录，不能留下“render-pending”假终态。

## 最小非官方示例

`examples/spec.minimal.json` 仅包含标有 SYNTHETIC 的虚构研究片段与指示值，不是案例答案。将其复制到 `/app/work/deck_spec.json`，用 synthetic PDF 和真实模板替换 `source_pdf`/`template`，把示例 `output_contract` 换成本 case 契约后执行上面命令。输出页主张与 `pdf_page/quote` 形成可检查映射，图表数值来自该示例片段；不得把示例词迁移到真实论文。

独立合成输入、实际生成断言与命令/退出码属于维护侧 verification，不得复制进 submission。外部验证不是正式案例结果，也不代表科学质量自动通过。

## 失败恢复

- PDF 加密、扫描/OCR 不全：保存已定位部分，列未读页、缺图和证据未决；核心结论不可证就不生成强结论页。
- 图提取失败：引用真实可用源图或明确缺图，不造替代曲线；公式保留源形式并查看渲染。
- 模板槽位不足/母版冲突：另存诊断，选择实际 layout 或报告阻塞，不重建匿名母版。
- 页数超限/时间不足：先删非必要支撑、把补证移入备注，保留主问题/方法/关键证据/限制，再重新验页数和秒数。
- 文本估算或视觉溢出：先缩句/拆层级/换 layout，再小步调格式；禁止全局自动缩到不可读。
- 任何生成/编辑/导出异常：优先保留 `/app/work` 检查点、`/app/output` 最近已验证 PPTX 和问题清单；上一版未验证不得称完成。恢复后从相同 spec 确定性重建并复跑全套检查。

## 自查与维护

逐页核对固定 audience/time/slide count、full-source/uncertainty、每页唯一主张、原文数字/单位/图号、图表归属、备注互补、真实模板继承及最终契约键型。预览必须来自当前 PPTX 哈希；重建后使旧预览失效。

维护新论文类型、layout 或字体时先加合成回归片段，再修改提取/映射，不把任何固定案例内容写进脚本。每次修改都运行真实生成、局部失败恢复和最终渲染；记录实测 Python、`python-pptx`、renderer、PDF 库及模板/字体版本。资源保持自包含、相对路径、无符号链接，不携带来源不明或私人字体。没有已验证的真实公开 GitHub URL 时不创建 `GITHUB_URL.txt`；不代用户提交、PR、发布或处理令牌。
