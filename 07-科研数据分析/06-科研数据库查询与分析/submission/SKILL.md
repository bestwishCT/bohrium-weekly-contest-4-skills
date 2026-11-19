---
name: db-audit
description: Execute schema-grounded research analysis against authorized SQLite snapshots using read-only connections, constrained single SELECT/WITH SQL, key-grain diagnostics, stable result export and a separate real SQL recalculation. Use when actual tables, joins, SQL evidence and honest empty/failure states are required.
---

# 真实只读科研数据库查询与跨表复算

## 触发与真实性边界

当 case 提供授权 SQLite 数据快照，且任务要求连接、schema/字段解释、筛选、跨表 JOIN、聚合、稳定结果集和独立复核时触发。重点为“schema 接地 + 连接粒度控制 + SQL 至结果证据链”。不能仅给像 SQL 的文字或手填数字，也不得声称未实际连接的外部数据库/账号可用。

仅支持本地 SQLite 快照，不支持臆造网络连接、数据库驱动或云凭据。绝不修改源库、插入合理记录、制造空查询替代结果，或为得到结果而删除筛选/连接条件。缺表字段、字段歧义、权限不足、连接失败和有依据的空结果分别报告。不给没有要求的统计/图/Office 产物；若要求，结果必须读取查询表后另外执行并真实渲染检查。

## 输入、来源与路径

逐例读取 `/input/case.json`、`/input/task.md`、现场 `environment/resources.yaml`、真实 SQLite 文件和数据字典。公开修订提到 UCI Concrete（1030）、Iris（150，bezdek 变体）、Wine（178）和真实 SQLite；不能据此猜标准 UCI 表结构，不能沿用旧三行占位或声称旧 baseline 为新版本。

- 安装目录 `/skills/weekly-skill`，官方 DeepSeek Harness 触发；相对入口 `scripts/query_sqlite.py`，无个人机器路径。
- 先检查 SQLite 文件存在、文件头 `SQLite format 3\0`、字节和 SHA-256；路径必须来自真实 case/资源。缺失文件绝不自动新建替身库。
- 中间 schema 探查、job 参数、失败现场只在 `/app/work`；最终 SQL、schema 卡片、结果、语义/关联路径、质量检查和 `/app/output/result.json` 在 `/app/output`。
- 实际 `output_contract` 决定字段、层级和类型；contract values 键必须与契约对象完全一致，适配器加 `case_id`，失败不删已成功查询文件，也不自行发明 result/query 字段。
- 在线题面于 2026-11-18 与本地修订一致；运行时最新 case/task 优先。

## 查询 job 数据字典

`/app/work/job.json` 采用 `schema_version: 1.0`。必填：
- `query.sql/parameters`：一条 SELECT 或 WITH，主问题对应表、字段、筛选、排序、聚合。命名参数对象或位置参数列表，禁止插值拼接用户值。
- `verification.sql/parameters`：在同一真实快照执行的**不同**替代 SQL，独立计算同一组主结果；列名、顺序、行序和粒度必须对应，不能原样复制主 SQL。
- `dictionary`：现场表/字段含义、类型、NULL、键、约束、来源与快照版本；与实际 schema 冲突写 `dictionary_conflicts` 并暂停受影响解释。
- `question_spec`：目标、时间/筛选范围、分组/输出粒度、参照与方向；`result_units` 给结果列单位，`semantics` 说明 NULL 三值逻辑、未检测≠0。
- 跨表时 `join_path` 写键/基数/关联类型，`diagnostics` 给命名只读 SQL，至少探查 JOIN 前后行数、DISTINCT 主键和未匹配键，发现膨胀后先重写到正确粒度。

可选 `expected_columns` 锁定输出 schema，`row_limit` 1–1000000（默认100000，超限非零而非静默截断），`step_limit` 1000–100000000 SQLite VM 步预算，`numeric_tolerance` 十进制绝对差，`allow_unordered` 仅在任务允许非确定顺序时为 true。多行默认必须 ORDER BY，聚合总量也需唯一键稳定排序。

示意 job 片段（非官方数据）：

```json
{
  "schema_version": "1.0",
  "query": {
    "sql": "WITH per_subject AS (SELECT subject_id, AVG(value) AS m FROM observations GROUP BY subject_id) SELECT s.arm, COUNT(*) AS n, AVG(p.m) AS arm_mean FROM subjects s JOIN per_subject p ON p.subject_id=s.subject_id WHERE s.active=? GROUP BY s.arm ORDER BY s.arm",
    "parameters": [1]
  },
  "verification": {
    "sql": "WITH per_subject AS (SELECT subject_id, AVG(value) AS m FROM observations GROUP BY subject_id), included AS (SELECT DISTINCT s.subject_id, s.arm, p.m FROM subjects s JOIN per_subject p ON p.subject_id=s.subject_id WHERE s.active=?) SELECT arm, COUNT(*) AS n, SUM(m)/COUNT(*) AS arm_mean FROM included GROUP BY arm ORDER BY arm",
    "parameters": [1]
  },
  "expected_columns": ["arm", "n", "arm_mean"],
  "row_limit": 1000,
  "step_limit": 2000000,
  "join_path": "Pre-aggregate observations by subject_id before joining unique active subjects; this prevents one-to-many row inflation.",
  "result_units": {"arm_mean": "a.u."}
}
```

SQL 只是合成接口示意，不能拿去替代现场 schema 探查。AVG 忽略 NULL 的语义必须在字段解释中写出；需要计数分母/缺失报告时用额外实际 SQL 诊断，不把 NULL 变成 0。

## 真实执行与版本

```bash
python3 /skills/weekly-skill/scripts/query_sqlite.py \
  --db /input/snapshot.sqlite --job /app/work/job.json \
  --case /input/case.json --task /input/task.md \
  --resources environment/resources.yaml \
  --work /app/work --output /app/output \
  --contract-values /app/work/contract_values.json
```

只用 Python 标准库 sqlite3。开发与烟测实测 CPython 3.9.6、SQLite 3.51.0、Python sqlite3 module 2.6.0；实际执行另记现场版本。无需网络、安装或随机过程。其他 DBMS/版本不可用本脚本冒充支持。

## 专属查询分析流程

1. **只读确认**：使用 `file:...?mode=ro` 真实连接，`PRAGMA query_only=ON`，尽力 `trusted_schema=OFF` 并记录实际状态；设置 SQLite authorizer 禁止写入、DDL、ATTACH 与文件函数，保留源哈希前后对照。连接对象只记录引擎/快照，不输出秘密。
2. **schema/语义接地**：实际枚举 sqlite_master 中的表/视图、逐表行数、列类型/NOT NULL/default/PK/外键，结合字典生成 schema_card。若 key 类型、单位或同名列冲突，不能只选有利语义。
3. **查询规格门**：从 task 确定指标、时间、筛选、输出列、聚合粒度、join key/路径、排序与结果单位。SQL 只接受单条 SELECT/WITH；拒绝分号多语句、PRAGMA/ATTACH/扩展/readfile/writefile 与写关键词。CTE 中写操作也由 authorizer 阻断。
4. **键与基数门**：执行 EXPLAIN QUERY PLAN、真实 diagnostics，区分源表重复与 join 膨胀。一对多跨表先按真实分析单位预聚合或取定义明确的唯一集合，不用 DISTINCT 盲删行掩错；核对匹配率、NULL 键和未匹配数。
5. **有限真实执行**：以 bound parameters 运行，保留 SQL、参数、query plan 和实际 sqlite 版本。列名必须唯一，多行需 ORDER BY，超 row/VM step 限制即失败，不静默截断或强改条件。
6. **结果抽取**：稳定写出 JSON 和 CSV 的真实列名/值、类型（BLOB 以显式 hex 对象）、NULL 数、结果行数、完全重复数。空结果保留列 schema 且 status 为 empty，检查筛选与键后如实交付，不制造合理行。
7. **独立复算**：运行不同 alternative SQL，比较列名、行数、逐行逐列值，数值用 Decimal/容差，其他类型严格相等；不依赖官方 gold，也不手填相同结果。主/验证 SQL 相同直接拒绝。
8. **解释可追溯**：字段语义、单位、筛选、粒度、join path、诊断和剩余不确定性从 job 与真实 schema 合并；不能由快照推断总体、因果或未测字段。统计/图只可从查询结果另行计算。
9. **同源交付与恢复**：先保存主结果 verification_pending，再比较、写最终 verified/empty、field notes 和 receipt。schema、SQL、结果、日志版本一致；失败保留已完成 schema/SQL，源库不变，修复根因后再运行。

## 输出与失败恢复

正常产物：`schema_card.json`、`query.sql`、`verification.sql`、`query_result.json`、`query_result.csv`、`query_plan.json`、`quality_checks.json`、`field_notes.json`、`execution_receipt.json`，可选 `result.json`。

- 退出 0：主 SQL、诊断和替代 SQL 都实际成功且一致，结果可为 verified_empty；退出 2：源文件/job/schema/参数错误；3：非只读或禁止 SQL；4：连接/权限/语法/执行/步数限制等失败；5：替代 SQL 不一致。
- 连接失败/不存在：run_failure 明示未执行查询，不创建空库、不伪造 schema 或 rows。权限不足不尝试提权。
- 空结果：如果主与替代都是空且 schema 对齐，合法交付 empty，并留筛选/键检查；不能为“有结果”取消 where。
- 结果不一致/源哈希变化：标失败，保留主结果、SQL、诊断，停止下游解读。JSON 契约失败不删除二进制/表格证据。

## 最小非官方示意

合成建库：
- `subjects`：S1/A/active=1、S2/A/active=1、S3/B/active=0；
- `observations`：S1 值 1 和 3、S2 值 10；
- `batches`：S1 有两个批次，S2 有一个批次，制造一对多风险。

使用上部 SQL。先按 subject 聚合：S1 均值 2、S2 均值 10，active A 的 arm_mean 为 `(2+10)/2=6`、n=2；若直接 JOIN observations+batches 后对所有行 AVG，会被批次膨胀干扰。诊断记录 join 前观测数、JOIN 总行数、DISTINCT observation_id 和未匹配键，替代 SQL 真实复算 6。空库/`DELETE FROM subjects`/`missing.sqlite` 都不能被报告成成功查询。该例不是 UCI/正式结果。

## 自查与维护

更新时用临时合成 SQLite 实测正常两表 JOIN、NULL、重复键、未匹配、空结果、缺列、错误 SQL、危险写入、缺失/伪数据库文件、步数超限、重复运行，独立复算分母和连接前后粒度。检查原库 hash 不变、只读 URI/authorizer 生效、每条 SQL 真实执行且结果可重新读取。禁止使用生产凭据或把静态结果充当 fixture；schema/plan 结构变更同步更新脚本、示意和失败恢复。

仅文本 UTF-8 资源（运行 SQLite 独立）、相对路径、无符号链接/README，submission ≤50 文件、单≤500KB、总≤2MB。无真实公开 GitHub 作品 URL 不创建 `GITHUB_URL.txt`，不提交/PR/发布/生成令牌；正式提交须用户另行明确授权。
