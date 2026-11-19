---
name: locate-research-evidence
description: 在指定科研文件库内执行可复跑的跨文件检索、章节/段落定位、硬过滤与逐字证据回链；适合按同义词或缩写查找实验配方、图表、项目版本或批次位置。遇到库内语义检索、页码映射、重复版本或检索为空时触发，不用于开放网络搜文献或虚构可点击链接。
---

# 库内科研证据定位

## 触发与范围

用户已提供文件库并要求“哪份文件、哪一版、哪一节、哪一段/页写了什么”时使用。必须完成读取、索引、召回、硬过滤、原文回核，而不是只写检索式。不可擅自扩大目录、权限、年份、项目、批次、版本或类型范围；副本不增加独立支持数，历史版本不混成一个实体。

## 真实case/task/output_contract门禁

1. 先完整读取`/input/case.json`与`/input/task.md`，逐字解析`case.json.output_contract`，再读取案例实际文件、伴随元数据、管理性版本说明、别名及恢复/索引状态。不要把本技能中的`hits`等辅助名称当成官方字段。
2. 盘点每个路径的格式、大小、哈希、可读性、权限、年份/项目/批次/版本等实际可得元数据及来源；目录但无权限、文件不可解析、过滤后为空和没有找到回答是不同状态。
3. 实际PDF、Office、扫描件没有假定答案层。先读取真实环境`environment/resources.yaml`并实测工具；PDF用真实解析器导出到`/app/work`，保留每物理页标记及工具版本。双栏按真实坐标重建阅读顺序；跨页表格/图注保留所有来源页；OCR须记录实际置信和漂移。做不到时标明未覆盖，不得猜印刷页。
4. 中间索引、抽取文本、审计记录放`/app/work`，最终`result.json`和案例要求的可编辑产物放`/app/output`。先保存部分结果，严守64请求、900秒、每响应16384 tokens。

## 安装路径与依赖实测

设运行时`SKILL=/skills/weekly-skill`，主程序相对技能根为`scripts/local_search.py`，不得改写为作者路径。`PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL/scripts/local_search.py" --version`可核查版本。

- 随包原创`local_search.py`版本1.0.0，仅依赖Python标准库；作者本机实测`Python 3.9.6`，来源为本地实机，无第三方安装。
- 原生支持严格UTF-8文本、CSV/JSON/XML/HTML、OOXML DOCX/PPTX/XLSX的可获得文本；格式文本被读入不等于版式或语义完全正确。
- PDF和OCR不伪造标准库能力。只有当真实工具存在时，通过案例配置的`extracted_text/parser/page_map`读取实际抽取结果。`parser`应含实测工具名与版本；工具不可用时文件进入失败清单。
- 作者工作区未提供`environment/resources.yaml`，故未实测PDF/OCR、双栏重排或预览工具；这些是明确限制，不声称已经验证。

## 专属检索流程

### 1. 固定范围并解剖查询

把自然语言问题拆成信息需求、目标粒度、硬过滤和证据形式。将过滤条件写入明确命令，例如`--filter year=2024 --filter project=P7 --filter version=v2`。未知元数据不能通过对应过滤，多个条件取AND；条件有冲突时输出空过滤原因，不放宽。

同义词、缩写、跨语言与上下位词只能来自案例材料、用户给出的术语表或明确的人工审核。将映射写入`/app/work/search-config.json`的`synonyms`；扩展只扩充召回，原文未表达的概念仍不得判为回答命中。工具实际算法是BM25（k1=1.5,b=0.75）加所声明扩展，不把词法得分冒充语义置信概率。

### 2. 建立带来源的索引

`documents`为每个文件声明`path`和实际元数据，如`year/project/batch/version/file_type/access`。无版本说明时为`undeclared`。访问范围须服从任务，不能只修改参数绕过真实权限。

运行：

```bash
python3 "$SKILL/scripts/local_search.py" \
  --library /app/work/library --query "buffer concentration" \
  --config /app/work/search-config.json \
  --case /input/case.json --task /input/task.md \
  --filter project=P7 --filter version=v2 \
  --work /app/work --output /app/output --top-k 12
```

`search-index.json`记录文档哈希与每个段落，`retrieval-audit.json`记录实际查询、扩展、过滤与顺序；程序每次从当前源重新校验哈希并重建，旧路径或旧索引不会被当作仍有效。中文按单字、拉丁/数字按词位召回；结果必须再由Agent读原文判断是否与信息需求语义相符。

### 3. 回核章节与证据

每个候选带`path/sha256/version/section/physical_page/printed_page/paragraph/quote/locator`。无真实页信息时字段为null。逐条执行：

- 回到真实文件确认引文逐字出现于该段，且所在章节确实回答目标问题；不只因术语相同就交付。
- 对PDF同时区分物理页与印刷页，页码映射未知则如实缺省；对表格/图注核对跨页连续性。
- `locator`是`路径::version=...;physical_page=...;...`定位串，不声称可点击。只有生成链接且实际打开成功后才称回链可点击。
- 每份不同版本保留独立ID；相同哈希、相同版本的副本列在`aliases`，不重复计独立证据。证据冲突时并列版本和引文，不按最新或多数静默裁决。

### 4. 解释空结果和恢复

检查`empty_reason`：`no_searchable_text_check_errors_and_scope`要求先处理不可读/未入库；`hard_filter_empty_no_relaxation`说明过滤后为空；`no_recall_support_does_not_prove_source_lacks_answer`只表示本轮没有召回支持，不能宣称原文必然没有答案。必要时扩大多项同义召回但不得动硬条件，并保留每次审计。

工具中断后保留已生成索引和部分命中；重新计算源哈希、修复解析器或抽取文本，再从index步骤恢复。索引过期、路径失效和解析失败不许混作“无相关内容”。

## 结果、来源、状态、不确定性与变更

`search_report.json`是辅助产物，涵盖范围、命中、空结果原因、过滤排除、源清单、失败、版本、定位、去重、状态及`index_rebuilt`变更记录。`result.json`适配实际`output_contract.properties/fields`并保留`case_id`和全部原键；空默认不是合格答案，Agent须按真实契约填充、检查字段类型/层级/枚举及所有必填项，并让辅助文件与主结果一致。

最终报告至少说明：结果及章节定位；查询、扩展、过滤参数；源路径/哈希/版本/权限；运行状态、失败与恢复；不确定页码/OCR/语义范围；索引和人工修改。普通JSON/Markdown不需要PDF/PNG预览。

## 最小非官方示例（非正式结果）

合成库含`v2.md`：第一行`## Methods`，下一段`The lysis buffer contained 20 mM Tris.`；配置声明`version=v2,project=P7`及`"buffer wash":["lysis buffer"]`。用`--query "buffer wash" --filter project=P7 --filter version=v2`调用上述命令。示意`hits[0].quote`应是原句，物理/印刷页为null，locator能回到Methods段。把版本过滤改为v9时应为空，而非命中v2。这是合成接口演示，不是平台案例或语义金标准。

## 失败处理、自查与维护

解析/编码/OOXML错误逐项保留；加密的PDF或无文本扫描件只有真实OCR成功后才纳入，否则列未覆盖范围。权限烟测用`--simulate-denied 相对路径`，不改系统权限。异常时退出非零或给出partial/empty，绝不伪造结果。

维护词法、过滤、抽取或定位逻辑后，用合成文件复测：同义召回、硬过滤不放宽、空库、无权限、重复版本、章节与页码映射、逐字引文和重跑索引一致性。再检查50个UTF-8文本、单文件500KB、总计2MB、无符号链接等资源约束。无真实公开GitHub链接不创建`GITHUB_URL.txt`；不自动提交、PR、发布或处理令牌。
