---
name: preserve-reading-annotations
description: 从真实阅读批注导出层提取高亮、划线、页边批注等属性，核验文件哈希、页码、字符坐标、原文上下文和版本映射，再生成有证据的结构化笔记、可解释关联与主题/标签/实体/时间检索。适合actual9document批注沉淀、断链恢复；不用于凭页面文本猜高亮或无来源扩写。
---

# 批注锚点保真与知识沉淀

## 触发与边界

用户给定PDF/网页/Word/文本及批注层，要求提取阅读标记、生成笔记、建立知识关联、跨版本恢复或后续检索时使用。主线是**批注对象→锚点核验→有界笔记→显式关系→可检索入库**，不是泛化摘要。颜色、手写含义、章节内容和版本优先级都从真实材料读取；无法提取时明确区分没有批注与尚未检查。

## 每个真实案例先做什么

1. 完整读取`/input/case.json`和`/input/task.md`，逐字保存实际`output_contract`的字段、层级、类型、枚举和必填约束。不能用本技能的`notes/links`辅助名冒充官方字段。
2. 盘点案例指向的原件、批注对象层、伴随导出、网页剪藏、手写OCR结果、目录权限、文件哈希、重复别名与管理性版本说明。检查是否已有抽取文本和索引，但必须重新回核。
3. PDF/Word等先使用真实批注工具读取实际对象，而不是只取正文后猜高亮。保留对象类型、页码、quad/rect、颜色、注释文字、标签、时间等实际存在属性。扫描手写仅在真实OCR可用时提取，并记录置信及不确定坐标。工具不可用标`annotation_layer_not_checked_not_empty`。
4. 中间抽取、页映射、索引和diff放`/app/work`；最终笔记库、查询结果和`result.json`放`/app/output`。先保存断链列表与部分笔记，遵守64请求、900秒和16384 tokens限制。

## 安装路径与实际依赖

安装根为`/skills/weekly-skill`，主程序相对根为`scripts/annotation_notebook.py`；运行时可设`SKILL=/skills/weekly-skill`，不引用个人目录，并设置`PYTHONDONTWRITEBYTECODE=1`。

- 原创`annotation_notebook.py`版本1.0.0，标准库实现，来源为随包源码；本机实测`Python 3.9.6`，支持Python 3.9+，没有第三方安装。
- 它处理**真实批注工具产生的JSON导出与对应页文本**，原生可直接读取UTF-8 `.txt/.md/.html/.xml/.json`。它不是PDF/OCR解析器的伪装替代品。
- 本机没有`environment/resources.yaml`，未实测PDF对象/OCR/Word批注解析。真实环境先读该文件并实测工具，将导出放`/app/work`，在manifest记录parser实际名称/版本、annotation_export和extracted_text的哈希；缺工具就保留失败状态。

## 批注专属流程

### 1. 建立真实导出manifest

在`/app/work/annotations.json`按documents列出每份真实文件：`path/version/extracted_text/parser/annotation_export`，以及可选`annotation_layer_status=checked_none`。只有真实已检查但没有批注时才用checked_none。对象列表保留工具原字段，至少尽量包含：

```json
{"id":"a7","kind":"highlight","page":3,"printed_page":"ii","rect":[10,20,200,40],"color":"#ffcc00","quote":"逐字原文","before":"紧邻前缀","after":"紧邻后缀","comment":"读者原批注","tags":[],"entities":[],"methods":[],"created":"2026-01-02T03:04:05Z"}
```

`quote`必须来自正文而非为完成任务编写；手写文本和OCR坐标若未确认，输入低置信标记并留人工复核。印刷页与物理页分别记录，双栏文本先按真实工具的阅读顺序导出，跨页quote不可擅自拼成一页。rect原样保存但代码会标明尚未进行几何复验，不能声称已经校验图中位置。

### 2. 锚定、去重与结构化笔记

```bash
python3 "$SKILL/scripts/annotation_notebook.py" build \
  --library /app/work/reading-materials --manifest /app/work/annotations.json \
  --edits /app/work/reviewed-notes.json --case /input/case.json --task /input/task.md \
  --work /app/work --output /app/output
```

每个对象以相对页文本的`page+quote+before+after`核验。唯一逐字quote状态为exact；重复quote只有`char_span`逐字吻合才成为verified_span，否则ambiguous。quote缺失、页不存在或上下文不匹配分别进入broken/context_mismatch，**不会生成可搜索笔记**。char_span是页内0基字符坐标，定位还含段落、quote SHA-256、文件哈希与版本。

相同哈希文件合并来源别名，不重复计算批注支持；不同版本即使文本相同也保留版本ID。原批注comment只代表读者，不冒充原作者结论。没有comment时summary为null，source_quote仍保留，不把摘抄包装成自动综合。

### 3. 有边界的人工/Agent派生笔记与diff

`reviewed-notes.json`只修改实际note的`summary/note_type/topics/tags/entities/methods`。summary同时提供`evidence_quote`，必须是该note的source_quote逐字子串；Agent还须语义检查概括未越界。禁止通过edits改source/source_quote/annotation_id等锚点字段。每次build在`/app/work/manual-edits.diff`保存before/after，并记录变更字段；脚本只机械验证引文，不声称自动理解批注意图。

核实每条note可沿`source.locator`回到文件版本、物理/印刷页、段和字符范围。OCR低置信、跨栏错位和坐标漂移只保留可确认部分并降低定位状态，不强挂相邻句。

### 4. 证据化知识关联与跨版本映射

manifest的relations必须提供`from_path/from/to_path/to/basis_type/value/evidence`。支持basis_type：shared_method/shared_entity/shared_tag/same_question/shared_project。只有两端原始批注都显式拥有该属性且证据充分才建连；否则`not_created`。共享一个普通词不构成关系，输出EVIDENCE quotes供回核；按真实材料标candidate或confirmed，不确定就降级。

version_maps指向两个版本的批注且需行政证据。仅当两侧锚点有效且quote相同才映射，记录原/新版本与position_change；不同或缺quote就是`unresolved_no_forced_cross_version_anchor`。此映射也不证明出版商版本优先级。

### 5. 入库后检索自测

构建完成后按真实task涉及的主题、标签、实体、方法、来源和时间执行AND过滤：

```bash
python3 "$SKILL/scripts/annotation_notebook.py" query \
  --notebook /app/output/notebook.json --method centrifugation \
  --tag verify --created-after 2026-01-01T00:00:00Z \
  --output /app/output --case /input/case.json
```

时间需真实ISO-8601字符串，未知时间不能绕过过滤。`--text`只对来源原文和读者/派生摘要做不区分大小写字面子串筛选，不冒充语义检索。空结果不放宽条件，并提醒未锚定/不可读条目可能尚未覆盖。误召回时检查受控字段来源，漏召回先查锚定和解析失败，不伪造指标。

## 输出与结果诚实性

`notebook.json`保存原始属性、解析器、导出哈希、全部批注、有效笔记、关系、版本映射、重复、未解决、来源权限、状态、不确定性与变更；`notebook-index.json`和`notebook_query.json`支持复跑。`result.json`遍历实际output_contract全部原字段并含case_id；空默认只用于部分交付，Agent必须按真实任务填充并校验，不能添加自定官方字段。

逐项说明文件类型、元数据字段、目录层级、重复、版本、权限和来源的适用性。普通JSON/Markdown笔记不机械生成预览；存在可编辑Office/slide/diagram时才用真实工具渲染检查。

## 失败与恢复

- 无批注对象、工具无法读取批注层、尚未检查三者不可混淆。
- 权限失败单列；合成测试仅用`--simulate-denied 相对路径`，不改权限。完全无可读文档时状态partial而非空库成功。
- 中断时保留已验证annotations、manifest哈希、notes和diff；修复导出后从build恢复，不将旧断链改为已恢复。
- 源文件只读，脚本只生成派生产物。哈希、库路径或批注导出变化后应重新build并比较锚点，再重做查询自测。

## 最小非官方示例（非正式结果）

合成`paper.txt`含`[PAGE 1 PRINTED=3]`及正文`Alpha step. Centrifugation used 500 x g. Next step.`，批注a1是黄色highlight，quote为`Centrifugation used 500 x g.`，前后文对应，comment为`check force`。build后示意产生一条note，保留highlight/黄色/物理1/印刷3/字符位置，并用`--method centrifugation`且原始methods显式包含centrifugation时检索命中。把quote改为不存在文本，应进入broken且不产生note。此例不是正式案例或金标准。

## 自查维护与发布门禁

修改锚点、派生字段、关系或过滤逻辑后，使用合成高亮、underline、颜色、重复副本、跨版本、重复quote、前后文冲突、伪造编辑和无权限材料复测，保留命令、退出码、断言和smoke结果。检查50个文本、500KB/个、2MB总量、无符号链接。无真实公开GitHub链接不创建`GITHUB_URL.txt`；不自动提交、PR、发布或处理令牌。
