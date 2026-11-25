---
name: adapt-academic-template
description: "解析实际投稿或学位模板，映射标题、图表、参考文献与页面样式，执行真实格式转换并检查内容保真和导出；用于模板适配、格式迁移和版式核验。"
---

# 模板驱动的格式转换与保真验收

## 触发与交付边界
当用户给出适配后源文件、规格表、内容与版式保真报告相关材料或明确要求“解析实际投稿或学位模板，映射标题、图表、参考文献与页面样式，执行真实格式转换并检查内容保真和导出”时使用。核心交付：**实际适配后的可编辑文件；规格来源与内容锚点同时可查；revision3使用随题Liberation Serif。**

不得把提纲、原则、评分自述或审计表当作任务成品；按真实契约交付完整、可编辑结果。纯内容问答、事实检索或超出明确支持范围的任务不要擅自套用本技能。

## 专属工作流
1. 从模板样式、正文实例和补充规范提取页面、栏数、边距、字体字号、行距、对齐、标题编号、页眉脚、题注位置、参考文献和导出要求；每项记录来源、版本、确定性与冲突。
2. **organizer template font revision3**：必须使用随题实际提供的Liberation Serif，先定位并实测；不得声称以Times New Roman替代或已嵌入不存在字体。缺失时标降级/阻断并保留证据。
3. 转换前登记标题、正文、图表、公式、脚注、批注、修订、域、目录、分节符和嵌入对象，建立稳定内容锚点与原文哈希；禁止因美观改写或图片化正文。
4. 以真实层级绑定原/目标样式，更新编号、目录、书签、交叉引用、三线表、图表注、页码和页眉脚；参考文献只改格式不造来源。
5. 真正转换成case要求的可编辑格式，再在可用工具中导出；逐类比较文本、数量与对象，实测页面/边距/栏数、字体嵌入、裁切、溢出和可读性。
6. 错误按规格抽取、样式映射、内容丢失或环境问题分类修复，每轮重跑受影响检查。无法保留批注、域等单列缺口，不以全栅格化掩盖。

## 本机字体验证边界
开发机未检测到 Liberation Serif，verification只做合成OOXML ZIP/XML机械结构回归，未宣称完成真实字体导出、嵌入或Office渲染。正式case必须使用随题资源再执行导出与预览；脚本会读取SFNT name表核对字体family，缺失或无法可靠解析时标pending/blocked并以partial非零交付，不猜默认字号或自动通过。

## 真实运行入口与资源
- 先完整读取 `/input/case.json` 与 `/input/task.md`，再读取 case 指向的全部实际材料；执行前也读取 `environment/resources.yaml`。以原始 `case_id`、章节范围、语言、格式和 `output_contract` 为唯一契约，Unknown 不猜测，未要求的附件不制造。
- 平台把本技能安装到 `/skills/weekly-skill`。以技能根目录为基准使用 `scripts/audit.py` 和 `references/record-template.json`；例如 `python3 /skills/weekly-skill/scripts/audit.py /app/work/audit_input.json`。模板只是结构示意，不是答案。
- `/app/work` 保存只读输入快照清单、抽取文本、稳定ID、分块状态、审计输入/输出与中间稿；`/app/output` 只保存契约要求的完整可编辑成品、指定预览和 `result.json`。制作目录是 `/app/submission`，不要污染输入。
- `result.json` 必须原样保留 `case_id` 和实际 `output_contract` 的全部原始字段，把成品、来源、状态、不确定性、变更、缺口落到契约指定字段；不得把示意字段强加给 case。各状态枚举以契约为准。
- 材料编目至少记录材料ID、真实路径、类型、版本线索、可读范围、缺失及定位方式；证据映射记录成品锚点、论断/对象、来源ID、精确位置、原文或数值、支持范围和处理动作。冲突双列，不静默择一。

## 增量执行、状态与异常
按64次请求、900秒和单响应16384 tokens预算分块：先落盘材料索引与审计台账，再完成全部可核实正文，随后替换局部文本、分块重建，最后运行确定性审计。长文使用稳定ID和术语/参数状态，不能循环通读全文。
- 状态区分材料缺失、科学/语义无法判断、工具或权限失败、部分完成；先保存诚实的部分成品和真实日志，能修JSON就修JSON，不删除已生成的真实产物。未执行的操作写未执行，不写成成功。
- 必须遵守原始case的 `output_contract` 和官方预览要求，同时保留可编辑Office成品及合同要求的真实PDF/PNG；不能因为本机无渲染器、旧示例未要求或只需修改格式就省略预览。只有真实renderer实际执行并打开核查后才能声明渲染成功；无真实renderer时明确写 `pending`/`blocked` 和缺口，不造图、不用结构检查替代视觉验收。纯文本/Markdown任务不主动增加合同外预览。
- 审计在 `/app/work` 生成独立审计输入，避免把测试数据混入正式输出；失败项回到具体源位置修复并重跑。禁止捏造实验、文献、DOI、人员、工具版本、分数、效率、白板或平台轨迹。
- **先写诚实 `/app/output/result.json`，再审计真实文件。** 审计JSON以 `case_json` 接原case，以 `result_json` 接交付记录，并提供 `source_file`、`template_file`（或在原case中提供）；`converted_file` 可省略并从合同 `deliverable` 取得。输出根默认 `/app/output`，只有 `synthetic: true` 测试可用材料根内的 `allowed_output_dir` 显式覆盖。脚本拒绝重复源ID、路径逃逸、缺失/不可读源模板成品、坏Office ZIP/XML、无真实页面的PDF及结构不完整PNG。
- 实文件模式直接比较正文/页眉脚/脚注尾注/批注文本及公式、批注、域、媒体、嵌入对象和关系；只有case `allowed_differences` 明确允许的类别才可豁免，格式变化不免除对象保留。`style_requirements` 按实际 `docDefaults`→`basedOn`→direct/character style→theme 链检查字号和字体，也检查 `line_spacing_multiple`（auto规则下240=单倍）、`line_rule`、`alignment`、`space_before_twips`、`space_after_twips`，并验证实际段落的direct格式没有覆盖错误值。`layout_requirements` 对每个 `sectPr` 实测 `page_width_in/page_height_in`、四边 `margin_*_in`、`header_in/footer_in/gutter_in`、`columns`、`column_spacing_in`，英寸容差0.01；不是只查样式定义或正文首页。`numbering_required` 检查numId/ilvl到abstractNum/lvl的解析。继承链缺失/循环、字体family无法从SFNT name表核实或包不可可靠解析均为pending，绝不填猜测默认值。旧的自报 `source_units/specs/applied` 映射模式只对 `synthetic: true` 回归开放，正式输入缺元数据不能借此绕过。此审计不是真实Office渲染，也不把机械尺寸检查冒充逐页视觉验收。

## 最小非官方示意与验收
以下仅为虚构“示意”，不是官方案例：将一段含“12 samples, p=0.04, Fig. 1 [A]”的短材料写入 `/app/work/audit_input.json`，执行上述 `audit.py`。应得到机器可读检查项；示例文本不进入 `/app/output`。实际案例必须替换为真实材料并产出契约指定的完整结果。
- 自查：核对所有契约字段和任务动作、抽样重读源/成品双向映射、搜索空产物与占位语、运行脚本并处理失败、在全新相对路径下复跑。结果必须可继续编辑，审计表/建议不能替代合约要求的成品。
- 维护：扩学科或格式时先加独立合成fixture，不改写旧断言掩盖错误；脚本只断言可机械验证项，语义判断仍由Agent逐条回读。来源为用户给定材料；无外部词表已捆绑。
- 依赖：核心脚本仅 Python 3 标准库，开发机实测 CPython **3.9.6**；运行时重新执行 `python3 --version` 并如实记录。任何可选外部工具都先探测，未探测不声称可用。
