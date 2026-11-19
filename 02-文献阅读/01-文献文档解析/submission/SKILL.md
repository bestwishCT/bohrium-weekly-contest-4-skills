---
name: parse-pdf
description: Parse real text-layer scientific PDFs into page/block-anchored, column-ordered structural records with formula/caption candidates and explicit failure states. Use for PDF text extraction, two-column reading order, section/block indexing, or parsing audit; not for invented OCR, image-table recovery, or scientific chart interpretation.
---

# 文献文档结构与阅读顺序解析

## 触发与范围

当用户提供真实论文 PDF，并要求提取正文、恢复双栏阅读顺序、定位题注/公式或建立页级区块索引时使用。处理的是结构，不解释图表或公式的科学含义。加密件、对象流复杂封装、CMap 字体、扫描页、图片表格或损坏流全部显式列为限制；本技能不是完整 OCR/PDF 系统。

版本 `1.0.0`。仅需 Python 3.9+ 标准库；本次在 macOS、Python 3.9.6 实测。无第三方依赖、模型服务、网络或安装步骤。评测机上先记录实际 `python3 --version`，不把本机版本冒充固定镜像。

## 输入、路径与合同

1. 完整读取 `/input/case.json`、`task.md` 与 `/input/environment/resources.yaml`（若提供），从 `pdf_path`、`pdf` 或 `document` 获得实际 PDF，读取文件字节而非根据文件名猜内容。
2. 在 `/app/work` 保存 `trace.json` 和解析中间记录，在 `/app/output` 尽早保存 `result.json`。执行预算遵守 64 次模型请求、900 秒及单次响应限制。
3. 平台安装点为 `/skills/weekly-skill`；包内资源只用相对路径 `scripts/common.py`、`scripts/workflow.py`，不得引用作者本机路径。
4. `common.bind_contract` 递归读取真实 `case.json.output_contract`：普通模板的每个键、JSON Schema 的每层 `properties/items` 都按原名映射；只输出合同键及强制 `case_id`，不套固定自创 schema。无值键用 `null`、空表或诚实状态，不填科学猜测。
5. 主结果业务表示为 `pages`、`blocks`、`tables`、`formulas`、`sources`、`status`、`uncertainty`、`changes`。合同若另用 `parsed_document`/`document_blocks` 名，绑定器适配到 `blocks`。

## 专属工作流

1. 验证 `%PDF-` 头、直接页对象、`/Encrypt`、内容引用和 Flate 流。无直接页对象时停止复杂恢复，不伪造页数。
2. 逐页解析 `BT/ET` 中 `Tm/Td/TD/T*` 坐标及 `Tj/TJ` 字符串，保留 Latin-1、UTF-16BE 字符串和异常；不把失败字节“修齐”。
3. 按 y 容差合行，以页面中线 306 为保守栏界，生成页内“跨栏顶部→左栏→右栏→跨栏底部”顺序；记录 `bbox_approx` 是文字算子坐标，不冒称精确字框。跨栏标题不确定、跨页段落不自动续接。
4. 用可解释模式区分 `paragraph`、`heading_or_section`、`caption`、`formula_candidate`。题注和公式只形成候选结构，保留原文、编号字符串和位置，不恢复未见图片。
5. 页眉页脚不静默删除，原件留在区块中由任务判断是否排除；每块计算稳定 `block_id`，来源附实际 PDF 路径、页和块号。
6. 逐页核对 chunk 数、块顺序、重复/空文本、异常流及空文字层。部分页可读即保存，其他页写明未覆盖范围。

## 状态、失败恢复与来源

- `success`：所有直接页对象均恢复且无警告；并不等同于全 PDF 标准认证。
- `partial`（退出 2）：加密、空页文字层、未知过滤器、坐标/对象恢复受限。保留可读块并给人工接管页。
- `input_error`：缺 PDF 路径或输入不可读。不能用科学领域的“未报告”掩盖运行失败。
- `trace.json` 记录真实 Python/平台、读取页数、限制、变更和不确定性；每块 `extraction/correction` 区分原始抽取与修订。

缺 OCR、渲染器和 Office 工具时不得声称预览。只有合同要求 Office/图形文件时才生成，且必须实际渲染 PDF/PNG 并打开检查；普通 JSON 不虚称已渲染。

## 最小示例（合成、非正式）

输入：`/app/work/demo.pdf` 是实际生成的无压缩 PDF，两页各含左栏 `A-left`、右栏 `B-right` 与 `E=mc^2 (1)`。

调用：

```bash
python3 /skills/weekly-skill/scripts/workflow.py \
  --case /app/work/demo-case.json --task /app/work/task.md \
  --input-dir /app/work --work /app/work/run --output /app/output
```

示意输出（非官方结果）：`result.json.blocks` 中每块形如 `{"page":1,"kind":"paragraph","text":"A-left","source":"由 sources 中同 block_id 定位"}`；`formulas` 仅列 `formula_candidate`，没有编造推导。

## 自查与维护

- JSON 复读 `result.json`；检查合同逐键、`case_id`、所有页覆盖、块 ID 回链和公式/题注候选。
- 用真实合成最小 PDF 测正常左右栏顺序和 `/Encrypt` 失败分支；正式成绩只能来自平台。
- 新增 PDF 过滤器或栏位算法时增加对应畸形流、三栏和跨页回归样本，更新版本及 `changes`；不得用隐藏答案调参。
- 最新官方增加真实 GitHub 公开作品链接加分。仅在用户另行提供已核验公开 URL 时才填 `GITHUB_URL.txt`；无 URL 不创建、不猜测。
