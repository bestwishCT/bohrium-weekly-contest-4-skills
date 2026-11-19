---
name: classify-research-collections
description: 依据真实标题、摘要、关键词和正文为科研资料执行可解释的多标签分类、标签归一、规范命名、哈希去重及可回滚批量整理。适合文献/数据混排目录、actual9document修复、跨主题文献、重名冲突与版本候选处理；不用于开放检索、无原文标签猜测或不可逆删除。
---

# 文献与资料取证分类整理

## 触发与目标

当用户要求把现有文件实际归类、打标签、改名、去重并进入目标目录时触发。交付是目录变更及可复核记录，不是标签建议。每个分类/标签必须能指向材料位置；预印本、正式版、补充材料和修订版只在有证据时区分，不因同DOI或相似标题自动删除。

## 运行前输入与契约

1. 完整读取真实`/input/case.json`和`/input/task.md`，逐字读取`output_contract`。先建立精确字段/类型/层级/枚举核对表，本技能的`classification/tags/versions`只是辅助产物名。
2. 盘点案例指定的所有原件、元数据表、目录、权限、别名及管理性版本说明。记录格式、大小、SHA-256、来源和解析状态；PDF/扫描件须由真实工具读取，无工具或OCR失败就标未检查的范围，不能将文件名当作者、年份、期刊。
3. 从task和伴随材料提取分类维度、目录模板、词表及保留策略。案例模板优先；只有案例缺失模板时才用本技能的权重和命名降级，并在结果不确定性中标为Skill规则。
4. 所有解析与计划放`/app/work`，最终目标目录、清单、日志摘要和`result.json`放`/app/output`。提前保存部分结果，遵守64请求、900秒、16384 tokens限制。

## 安装与依赖实测

技能安装为`/skills/weekly-skill`，仅用相对根目录的`scripts/classify_batch.py`；不要依赖个人目录。运行时可将根路径赋给`SKILL`变量，并设置`PYTHONDONTWRITEBYTECODE=1`。

- 原创脚本`classify_batch.py`版本1.0.0，仅Python标准库，来源为随包源码；作者实测`Python 3.9.6`，支持Python 3.9+，没有额外安装。
- 原生读取UTF-8 `.txt/.md/.html/.xml/.json`及DOCX XML文本；其他需提供真实`extracted_text`抽取件、`parser`实测工具与版本。抽取件放`/app/work`，原文件哈希仍以原件计算。
- 作者环境未发现`environment/resources.yaml`，PDF/OCR/扫描件版式工具未实测，不声称可用。真实案例先读该清单并逐项实测，工具不可用则文件留在失败/未分类队列。

## 取证—分类—命名—整理流程

### 1. 建立规则与输入快照

在`/app/work/taxonomy.json`声明`documents`、`categories`、`synonyms`、`naming`及可选`version_relations`。每项documents可以含：

```json
{"path":"paper.md","metadata":{"journal":"Example Journal","doi":"10.x/example"},"metadata_sources":{"journal":"metadata.csv#row=2","doi":"paper.md#line=5"}}
```

原文自动读取`Title/Authors/Year/DOI/Journal/Keywords/Abstract:`字段及其行号。原文和声明值冲突时，字段置为不可用于命名，双方值与来源进入`metadata_conflicts`，不静默选值。信息缺失保持null。

### 2. 用分区证据生成分类与受控标签

每个category声明`name/directory/parent/minimum_score/terms`。同一个term只在最可信分区计一次：标题5、关键词4、摘要3、正文1；证据包括实际匹配词、区域、行号、原行，得分不是概率。默认至少2分，案例阈值优先。

- 多主题可以保留多个过阈值标签，最多`max_tags`；`parent_tags`只从所声明taxonomy派生，不冒充额外正文匹配。
- `synonyms`是`实际写法:受控标签`映射，实际别名必须在材料出现才计入证据；上下位关系不能凭空添加。
- 主目录只属于唯一最高分且证据达标的类。并列最高分进入`ambiguous_primary`复核，证据不足进入`insufficient_evidence`，不强行归入“其他”。
- 纯共享通用词不构成强分类。Agent应打开证据原文检查语义、主题边界和标签粒度，再批准计划。

### 3. 安全命名与版本判别

Dry-run：

```bash
python3 "$SKILL/scripts/classify_batch.py" classify \
  --library /app/work/inbox --destination /app/output/organized \
  --rules /app/work/taxonomy.json --work /app/work --output /app/output \
  --case /input/case.json --task /input/task.md
```

命名默认模板`{year}-{title}`仅为Skill降级，可从`{field}`引用实际元数据。清理控制字符和`<>:"/\|?*`，Unicode采用NFC、保留中文，文件名最多180 UTF-8字节；保留原扩展名。缺字段用`unknown`并附内容哈希前8位，明确标出`missing_naming_fields`，不是编造字段。

同哈希只存规范实体，其他来源成为`duplicate_alias`且保留requested_target。同DOI或题名著者年相同但哈希不同，只形成version_candidate；管理性关系若缺证据仍降为候选。同名不同内容形成`normalized_name_collision`，不自动加后缀掩盖真实冲突，不覆盖也不删除。补充材料不能靠名字直接当重复。

### 4. 实际整理与幂等

审查`classification-plan.json`后执行：

```bash
python3 "$SKILL/scripts/classify_batch.py" apply \
  --plan /app/work/classification-plan.json --journal /app/work/classification-journal.jsonl \
  --output /app/output --case /input/case.json
```

每次复制前重算源哈希，临时文件校验后原子落位；目标已同哈希记`already_verified`，不同哈希拒绝覆盖。重跑不重复复制、改名或生成冲突副本。需复核主类、元数据冲突、无权限、解析失败或冲突者不计完成，状态为partial并返回2。`accounting_conserved`必须与输入路径集合一致。

### 5. 回滚与续跑

```bash
python3 "$SKILL/scripts/classify_batch.py" rollback \
  --journal /app/work/classification-journal.jsonl --plan-id PLAN_ID \
  --output /app/output --case /input/case.json
```

只删除日志committed且当前哈希未变的目标。目标被修改时保留并失败，不伪造回滚成功。中断后保留已落地文件与JSONL，从源哈希/目标检查续跑；空间或权限修复后重新生成被审计划。临时文件只有在确认没有committed日志时方可清理。

## 结果与契约

`classification_plan_report.json`、`classification_report.json`和`catalog.json`包含输入快照、每类与每个标签的证据、原/新路径、命名降级、去重、版本关系、来源权限、操作状态、不确定性及变更。`result.json`遍历实际契约全部原字段并含`case_id`；适配器的空值/空容器只用于尽早保存，最终必须由Agent按真实任务填充并复核所有必填、类型、层级和枚举，不能创造官方字段。

逐项交代文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源。普通目录、JSON或清单不生成无意义PDF/PNG；若另有可编辑Office/slide/diagram才实测渲染。

## 失败与不确定性的处理

- 加密、损坏、乱码、扫描无文本：登记影响，不默认为其他类。
- 无权限：`access`失败单独列出；合成烟测只能用`--simulate-denied 相对路径`，不改权限位。
- 改名/版本/分类冲突：保留原文件、双方证据和候选处置，等明确规则。
- 回滚失败：保留现场和未恢复路径，停止后续危险操作。
- 契约或开赛规则更新：按最新真实规则适配并记录变更，不改写赛前规则文件。

## 最小非官方示例（非正式案例）

合成`cross.md`含`Title: Engineered hydrogel imaging`、`Authors: Demo`、`Year: 2024`、`Keywords: fluorescence microscopy; rheology`及正文实际描述。taxonomy分别声明Imaging（term为fluorescence microscopy）和Mechanics（term为rheology）且目录不同。运行classify/apply后，示意记录有两个真实命中标签、唯一最高分决定主目录；不是两个都机械复制。将另一文件内容改不同但元数据相同，会形成命名冲突而不覆盖。本例不是正式题集或金标准。

## 自查与维护

修改taxonomy、权重、命名、去重或事务逻辑后，必须用非官方合成批次复测：多标签及行号证据、并列主类不复制、缺失/冲突元数据、长中文名、重复下载仍为别名、版本候选不删除、重名不覆盖、无权限可解释、重跑幂等、回滚及改坏目标拒删。检查50个文本、500KB/个、2MB总量、无符号链接。没有真实公开链接就不创建`GITHUB_URL.txt`；不自动提交、PR、发布或处理令牌。
