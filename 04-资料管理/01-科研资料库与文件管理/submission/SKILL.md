---
name: repair-research-library
description: 以内容SHA-256盘点并真实整理科研资料库；用于批量入库、内容去重与别名保留、版本候选、目录dry-run、重名冲突处置、幂等复制和按日志回滚。用户要求修复actual9document资料库、恢复真实文件或让科研文件批次可交接时触发；不用于开放文献检索或无依据补写元数据。
---

# 科研资料库修复与交接

## 触发与边界

接收到一批实际文件及“入库、修复资料库、去重、归位、版本梳理、恢复或交接”要求时使用。主线是**真实复制入库而非建议清单**；默认不改源文件，不自动删除重复别名，不把同名当同版本，不臆造DOI、作者、项目号、仪器或保存期限。只有任务明示归档时才另行归档；本工具不声称实现长期保存策略。

## 每个真实案例的硬门禁

1. 先完整读取真实`/input/case.json`和`/input/task.md`，逐字检查`case.json.output_contract`，不得用本说明里的名称代替契约字段。再核对案例全部材料、伴随表格、管理性版本说明、别名、既有索引及恢复状态。PDF等原文须用实际可用解析器读取；无解析器时标成未解析，不能假定存在答案层。
2. 列出输入路径、格式、大小、SHA-256、权限、可读性、来源、解析状态；分别登记材料缺失、无法判断和运行失败。不要凭领域标签补内容。
3. 若案例约定目录模板、保留规则或命名字段，将其转为受审规则；未约定的字段保持缺失。同名不同内容、相同哈希不同路径、不同哈希同DOI必须走不同分支。
4. 中间计划、日志与临时文件放`/app/work`，最终目录、清单和结果放`/app/output`。尽快写诚实部分结果，不超过64次模型请求、900秒和每响应16384 tokens。

## 安装与依赖实测

技能根目录固定视为`/skills/weekly-skill`，以下用`$SKILL=/skills/weekly-skill`仅作运行时变量示例，资源均以该根解析。实际执行路径为`$SKILL/scripts/library_ops.py`，不依赖作者目录。

- `library_ops.py`为随包原创资源，版本1.0.0，源码即来源；仅使用Python标准库。
- 作者环境实测`Python 3.9.6`；语法兼容Python 3.9及更新版本。无第三方安装步骤。
- 本地未提供`environment/resources.yaml`；因此不声称PDF/OCR工具已在本机存在。真实环境须先读取该文件并实测其声明工具，才能安排解析或预览。
- 运行命令前设置`PYTHONDONTWRITEBYTECODE=1`，避免在技能目录产生缓存。

## 案例专属操作流程

### 1. 建立受审规则

从真实任务生成`/app/work/library-rules.json`；每项`source`是源根下POSIX相对路径，`target`是目标根下相对路径。`metadata`只放可核对值，`metadata_sources`逐字段记录定位，例如`{"year":"metadata.csv#row=4/year"}`。

- 声明`version_group`或同DOI的不同哈希文件仅生成版本**候选**；文件时间和名称不证明替代关系。
- `version_relations`可登记`from/to/status/evidence`。只有`status=confirmed`且有管理性证据才保留confirmed；否则降为candidate。
- `only_configured=true`时未配置文件明确跳过；默认目标沿用源相对路径。规则不提供移动或破坏性去重选项。

### 2. Dry-run盘点

先只读生成计划：

```bash
python3 "$SKILL/scripts/library_ops.py" plan \
  --source /app/work/input-materials --library /app/output/library \
  --work /app/work --output /app/output \
  --case /input/case.json --task /input/task.md \
  --rules /app/work/library-rules.json
```

检查`work/plan.json`的守恒关系、重名冲突、无权限、同哈希别名、版本候选和字段来源后再批准apply。dry-run不会创建目标文件。中文、空格和特殊字符按路径原样处理；符号链接不入库；空文件仍有真实哈希。

### 3. 执行真实入库

```bash
python3 "$SKILL/scripts/library_ops.py" apply \
  --plan /app/work/plan.json --journal /app/work/library-journal.jsonl \
  --output /app/output --case /input/case.json
```

工具先复核源哈希，再写临时文件、复核哈希并原子落位。已存在且哈希一致的目标记`already_verified`，所以同一plan重跑不会二次复制。相同内容的非规范路径记`duplicate_alias`并指向`canonical_source`，只存一个实体；别名、原目标和各自元数据仍在计划/清单。存在冲突和失败时进程返回2且结果状态为partial；别把partial改写成完成。

### 4. 校验与回滚

逐项重新计算目标哈希，核对输入数=committed+already_verified+duplicate_alias+conflict_skipped+failed+明确未配置/无权限项；检查权限、mtime、来源和元数据定位。需要取消本批次时执行：

```bash
python3 "$SKILL/scripts/library_ops.py" rollback \
  --journal /app/work/library-journal.jsonl \
  --plan-id PLAN_ID --output /app/output --case /input/case.json
```

回滚只删除日志创建且当前哈希仍一致的目标；目标被修改则拒绝删除并保留现场。`rollback_report.json`记录每一项恢复状态，不能把`already_absent`说成刚完成恢复。

## 输出与契约适配

`intake_plan_report.json`、`intake_report.json`、`catalog.json`是Skill辅助审计产物，不是官方字段定义。`result.json`生成时保留`case_id`并遍历实际`output_contract.properties`或`fields`全部原字段；若契约缺字段定义，适配器只会放空值/空容器，Agent必须依据真实任务填入已证实内容并复核**原字段名、层级、类型和枚举**，不可只依赖适配器空默认。

最终摘要至少可追溯到：资料目录与目录树；输入/目标文件和元数据；哈希、来源、权限；版本候选/确认状态；committed、skipped、conflict、failed、duplicate和rollback日志；运行status及uncertainty。来源不清或证据矛盾时并列记录，不静默投票。

## 失败与恢复

- 权限不足：真实案例列入未处理；烟测可用`--simulate-denied 相对路径`安全模拟，不修改系统权限。
- 空间不足、复制中断或哈希变化：当前项失败，已committed项及日志保留；释放空间或固定源后重新plan。残留`.intake-*`只在同操作确认未committed后清理。
- 目标出现、已是不同内容或日志称完成但目标缺失：停止该项，不覆盖，人工核对后另建恢复计划。
- 部分完成时保留二进制/原文证据及日志，哪怕JSON契约尚未通过。不是可编辑Office/slide/diagram时不制造预览。

## 最小非官方示例（非正式案例结果）

输入`/app/work/demo-in/a 实验.txt`与`alias.txt`内容均为`demo`，规则仅给前者目标`P1/a.txt`、后者目标`P1/alias.txt`并注明来源。将上面命令中的源、规则、工作和输出换成demo路径执行plan/apply。示意状态为`completed`，一项`committed`、一项`duplicate_alias`；再apply第二项变为同一alias而不增加实体，rollback后仅删除committed副本。该例只演示接口，不是平台3个案例或金标准。

## 自查与维护

维护时修改规则解析、冲突逻辑或哈希流程后，必须用 submission外的合成fixture重测：dry-run零写入、别名去重、同名冲突不覆盖、无权限可解释、重跑幂等、目标改坏时回滚拒绝、正常回滚可复位。检查正式六类要素（文件类型、元数据字段、目录层级、重复、版本、权限与来源）均有结果或限制。不创建GitHub链接文件，除非获得真实公开链接并实测匿名可访问；不自动提交、发布、建PR或处理令牌。
