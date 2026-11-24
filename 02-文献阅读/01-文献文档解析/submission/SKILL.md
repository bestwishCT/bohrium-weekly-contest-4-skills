---
name: parse-pdf
description: Parse supported text-layer scientific PDFs into page/line/block-anchored structural records with evidence-based two-column ordering, row/cell table candidates, linked numbered-section candidates, and formula/caption candidates. Use for limited PDF text extraction or parsing audit; not for OCR, image-table recovery, verified TOC reconstruction, merged-cell detection, or scientific interpretation.
---

# 文献文档结构与阅读顺序解析

## 触发与范围

当用户提供论文 PDF，并要求提取可恢复文字、恢复受证据支持的双栏阅读顺序、建立表格行列候选、章节层级候选、题注/公式候选或页级区块索引时使用。只处理结构，不解释图表、表格或公式的科学含义。加密件、对象流复杂封装、CMap 字体、扫描页、图片表格或损坏流全部显式列为限制；本技能不是完整 OCR/PDF 系统，也不声称通用支持全部 PDF 版式。

版本 `1.1.0`。仅需 Python 3.9+ 标准库；本次在 macOS、Python 3.9.6 实测。无第三方依赖、模型服务、网络或安装步骤。评测机上先记录实际 `python3 --version`，不把本机版本冒充固定镜像。

## 输入、路径与合同

1. 完整读取 `/input/case.json`、`task.md` 与 `/input/environment/resources.yaml`（若提供），从 `pdf_path`、`pdf` 或 `document` 获得实际 PDF，读取文件字节而非根据文件名猜内容。
2. 在 `/app/work` 保存 `trace.json` 和解析中间记录，在 `/app/output` 尽早保存 `result.json`。执行预算遵守 64 次模型请求、900 秒及单次响应限制。
3. 平台安装点为 `/skills/weekly-skill`；包内资源只用相对路径 `scripts/common.py`、`scripts/workflow.py`，不得引用作者本机路径。
4. `common.bind_contract` 递归读取真实 `case.json.output_contract`：普通模板的每个键、JSON Schema 的每层 `properties/items` 都按原名映射；只输出合同键及强制 `case_id`，不套固定自创 schema。无值键用 `null`、空表或诚实状态，不填科学猜测。
5. 主结果业务表示为 `pages`、`blocks`、`tables`、`table_structure_candidates`、`sections`、`formulas`、`sources`、`limitations`、`status`、`uncertainty`、`changes`。合同若另用 `parsed_document`/`document_blocks` 名，绑定器适配到 `blocks`；合同未声明的新字段不绕过绑定器强行注入。
6. 保持旧 `tables` 字段兼容：它仍只装 `Table ...` 题注块，不把题注伪装成已恢复表格。真实行列候选另放 `table_structure_candidates`。

## 专属工作流

1. 验证 `%PDF-` 头、直接页对象、`/Encrypt`、内容引用和 Flate 流。无直接页对象时停止复杂恢复，不伪造页数。
2. 逐页解析 `BT/ET` 中 `Tm/Td/TD/T*` 坐标及 `Tj/TJ` 字符串，保留 Latin-1、UTF-16BE 字符串和异常；不把失败字节“修齐”。
3. 按 y 容差合行，保留大行间距产生的行内 `segments`；在 `pages[].lines[]` 保存各段文本、`line_id`、块 ID 回链及近似坐标，在 `blocks[]` 保留来源行号与 `segment_index`。`bbox_approx`/`bbox_extent_approx` 来自文字算子和固定宽度估计，不冒称精确字框。
4. 不使用固定页面中线。逐页从实际段 x 区间构造候选空隙，以左右行支撑、成对行数和空隙宽度选择一个栏界，并允许通栏标题/页脚穿过。`pages[].layout` 和 `trace.page_layouts` 记录真实 `gap_start/gap_end/column_boundary`、支撑计数、备选空隙及范围。可靠双栏页保持“跨栏顶部→左栏→右栏→跨栏底部”。
5. 无可靠双栏证据时保留从上到下、同 y 从左到右的单栏顺序。多段行仍无可靠栏界，或多个空隙支撑相近（最大空隙宽度的 1.25 倍以内）时，标记布局歧义、写入页级 `uncertainty`，统一采用上述保守单栏回退并返回 `partial`；不使用 306 或其他固定栏界。单栏回退不是三栏识别。
6. 表格结构检测要求至少两行情物理相邻，每行至少两段，连续行段数相同、各列起点在 12–36pt 动态容差内且 y 间距不超过页内行距推导阈值。每个候选输出 `row_count/column_count/rows/cells`、页码、每格原文、近似坐标、`block_id` 与来源行；证据记录实际参数。只称为行列结构候选，明确 `merged_cells: not_evaluated`、`cross_page_continuation: not_evaluated`，不合并单元格、不拼接跨页表。
7. 对 `1`、`1.1` 等短编号标题形状生成 `section_level` 候选。编号每段最多两位、层级最多六级，标题不超过 12 个词，排除句末 `.!?;` 的数字文段和超过长度的行。`sections[]` 与对应块通过相同 `section_id/block_id` 回链，`parent_section_id`、`section_path_ids` 只按当前抽取阅读序连接最近的更高层父候选，不声称验证了目录。`Abstract`/`References` 仍可为标题块，但不强造编号层级。
8. 区分 `paragraph`、`heading_or_section`、`caption`、`formula_candidate`。题注和公式只形成候选结构，保留原文、编号字符串和位置，不恢复未见图片。
9. 页眉页脚不静默删除，原件留在区块中由任务判断是否排除；每块计算稳定 `block_id`，来源附实际 PDF 路径、页和块号。跨页段落不自动续接。
10. 逐页核对 chunk 数、块顺序、重复/空文本、异常流及空文字层。部分页可读即保存，其他页写明未覆盖范围。

## 状态、失败恢复与来源

- `success`：所有直接页对象均恢复且无警告；并不等同于全 PDF 标准认证。
- `partial`（退出 2）：加密、空页文字层、未知过滤器、坐标/对象恢复受限或栏界检测歧义。保留可读块并给人工接管页。
- `input_error`：缺 PDF 路径或输入不可读。不能用科学领域的“未报告”掩盖运行失败。
- `trace.json` 记录真实 Python/平台、读取页数、页级栏界证据、表格/章节候选数、限制、变更和不确定性；`capabilities` 明示 OCR、图片表格恢复、合并单元格检测、跨页表拼接不可用。每块 `extraction/correction` 区分原始抽取与修订。

缺 OCR、渲染器和 Office 工具时不得声称预览。只有合同要求 Office/图形文件时才生成，且必须实际渲染 PDF/PNG 并打开检查；普通 JSON 不虚称已渲染。

## 最小示例（合成、非正式）

输入：`/app/work/demo.pdf` 是实际生成的单页无压缩 PDF，含左栏 `A-left`、右栏 `B-right` 与 `E=mc^2 (1)`。另有仅用于技术回归的非等宽栏界、三行两列文本、章节链接、三栏歧义、空文字层和加密合成 PDF；这些都不是官方案例或成绩证据。

调用：

```bash
python3 /skills/weekly-skill/scripts/workflow.py \
  --case /app/work/demo-case.json --task /app/work/task.md \
  --input-dir /app/work --work /app/work/run --output /app/output
```

示意输出（非官方结果）：`result.json.blocks` 中每块形如 `{"page":1,"kind":"paragraph","text":"A-left","line_id":"实际稳定 ID","segment_index":1}`，并由 `sources` 中同 `block_id` 定位；`pages[].layout` 给出真实候选空隙。`formulas` 仅列 `formula_candidate`，`tables` 仅列表题，`table_structure_candidates` 与 `sections` 仅列带限制说明的结构候选，没有编造推导或语义。

## 自查与维护

- JSON 复读 `result.json`；检查合同逐键、`case_id`、所有页覆盖、块/行/表格单元格 ID 回链、章节父链和公式/题注候选。
- 用真实合成最小 PDF 测动态非等宽栏界、通栏顶部→左→右→通栏底部顺序、至少两行两列的表格候选、数字文段过滤、栏界歧义回退和 `/Encrypt` 失败分支；正式成绩只能来自平台。
- 新增 PDF 过滤器或栏位算法时增加对应畸形流、三栏和跨页回归样本，更新版本及 `changes`；不得用隐藏答案调参。
- 最新官方增加真实 GitHub 公开作品链接加分。仅在用户另行提供已核验公开 URL 时才填 `GITHUB_URL.txt`；无 URL 不创建、不猜测。
