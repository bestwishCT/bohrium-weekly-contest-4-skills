---
name: exec-verify
description: Execute reviewed generated research-analysis code with schema preflight, allowlisted imports, isolated subprocess timeout/resource limits, deterministic logs and independent numerical validation. Use when generated Python consumes in-memory CSV rows and must return a JSON RESULT without silent data or calculation errors.
---

# 生成代码的执行、静默错误验证与现场恢复

## 触发与边界

当 Agent 已依据真实数据字典生成一段 Python，给内存中的 CSV 记录计算科学结果并写入 JSON `RESULT`，同时要求真实退出状态、调试证据和独立复算时触发。重点是“跑通但算错”的阻断，不是增加代码长度或无条件执行模型生成的文本。

本入口只支持无文件 I/O、无数据库、无网络、无绘图/Office API 的纯计算代码；这些任务必须在真实环境另建专用、审查过的只读工具链并保存 SQL/图/渲染验证，不能用本入口绕过或虚构。它不是防御恶意代码的容器：AST 和 POSIX 限制只降低意外风险，无法对抗绕过。永不直接执行未审查、来源不明或含秘密处理需求的代码；敌对输入需在额外容器/seccomp/只读挂载等系统隔离下运行。

## 案例预检与路径

先读取 `/input/case.json`、`/input/task.md`、现场 `environment/resources.yaml`、全部真实数据字典、CSV/SQLite schema 和实际材料。公开修订提及 UCI Concrete（1030）、Iris（150，bezdek 来源变体）、Wine（178）和真实 SQLite；不得据名字硬编码列名、答案或依赖。若需要数据库，以 `mode=ro` 实测 schema/工具并另存查询，不得由纯计算代码假装查询成功。

- 安装 `/skills/weekly-skill`，官方 DeepSeek Harness 触发；技能资源使用 `scripts/run_verified.py` 相对路径。
- `/app/work`：预检、生成的初始代码、job JSON、payload、sandbox、失败现场与修改差异；最终成功代码、实际结果、验证、日志及 `/app/output/result.json` 在 `/app/output`。
- 输入保持只读，代码与数据各计算 SHA-256。每个失败尝试保留，定位路径/编码/类型/依赖/超时/验证根因后最多人工决定一次修复重跑，不能无限换代码而抹掉历史。
- 实际 `output_contract` 提供字段和类型；`contract_values.json` 的键必须与契约对象一致，适配器加 `case_id` 并拒绝不一致。验证结果与任务契约分文件保存，不擅自加顶层契约字段。

## 代码与 job 数据字典

生成的 Python 接收 `ROWS`（字符串字典列表）和 `CONFIG`（仅 `public_config`），必须赋 `RESULT` 为 JSON 对象。禁止 open/eval/exec/compile、动态 import、dunder、文件/进程/网络属性；只允许 `math, statistics, json, decimal, collections, itertools, functools, datetime` 中明确需要的 import。科学缺失标记必须在代码中显式判断，不能靠 `float(value or 0)`。输出禁止 NaN/Infinity。

`job.json` 使用 `schema_version: 1.0`：
- `required_columns`：来自实际字典，预检缺列即停止。
- `missing_tokens`：只列有依据的 token。
- `human_review_confirmed`：Agent/人审查代码和边界后才能设 true；不是橡皮图章。
- `public_config`：算法参数，传给代码；不能混入凭据。
- `numeric_tolerance`：独立复算容许的十进制绝对差，按数据精度预设。
- `validations`：独立预期计算规格；`result_key` 指定要检查的 RESULT 字段，`op` 取 `count_rows/count_missing/count_valid/mean/sum/min/max/unique_count`，`column` 指定源列，可选 `where_column/equals`：`mean/sum/min/max` 使用源值 Decimal，`count_valid` 忽略缺失与非法值。

```json
{
  "schema_version": "1.0",
  "required_columns": ["group", "value"],
  "missing_tokens": ["", "NA"],
  "human_review_confirmed": true,
  "public_config": {"missing_tokens": ["", "NA"]},
  "numeric_tolerance": 1e-12,
  "validations": [
    {"result_key": "n", "op": "count_rows"},
    {"result_key": "mean_a", "op": "mean", "column": "value", "where_column": "group", "equals": "A"}
  ]
}
```

验证不是官方 gold；它独立用源 CSV 重新推导 task 必须满足的行数、缺失和聚合。无 gold 时优先数据不变量和替代实现，不得捏造参考结果。若预期来自生成代码本身的回写，验证无效。

## 执行与实测版本

```bash
python3 /skills/weekly-skill/scripts/run_verified.py \
  --code /app/work/generated_analysis.py --input /input/data.csv \
  --job /app/work/job.json --case /input/case.json --task /input/task.md \
  --resources environment/resources.yaml \
  --work /app/work --output /app/output --timeout 10 --memory-mb 256 \
  --contract-values /app/work/contract_values.json
```

只用 Python 标准库；开发和正常/异常烟测实测 CPython 3.9.6（macOS arm64；CPU/文件大小/描述符资源限制可用，实测系统拒绝 RLIMIT_AS），科学库未安装。默认会明确降级并记录 memory_limit_applied=false，不能宣称有强制内存隔离；`--strict-memory-limit` 会在无法设置地址空间限制时失败。正式环境须重新记录 `sys.executable`、实际 Python、系统、limits 支持和 resources；版本或 OS 能力未验证时不可承诺相同隔离效果。无网络、安装或随机过程。

## 完整执行闭环

1. **契约与 schema 门**：从 task/字典生成目标字段、类型、单位、主键、缺失语义和输出清单；先检查路径、可读性、列、编码、行数与代码大小，缺关键信息先报告。
2. **任务代码生成**：将缺失/异常/重复、统计前提、聚合图表口径写进代码，不硬编码 Concrete/Iris/Wine 答案。所有输入参数化，单任务不要求时不要机械同时做统计、绘图和查询。
3. **审查与安全门**：阅读完整代码，`human_review_confirmed` 后跑 AST allowlist；禁止 FS/process/network 和动态执行。资源限制为 timeout 1–60s、memory 32–2048MB、code 200KB、AST 5000 节点、RESULT 256000B。
4. **真实执行**：独立 `python -I` 子进程，cwd 为 `/app/work/sandbox`，空 PATH/LC_ALL=C；POSIX 下设置 CPU、文件大小、文件描述符上限，并尝试地址空间上限；外层 timeout 再兜底。每个资源项的实际应用状态/失败原因写入 resource_limits，无法隔离内存时明示降级或按 strict 选项终止。只派生一次，保存真实命令结构、cwd、stdout/stderr、child exit 和 timeout。
5. **独立验证**：父进程按 job 从原始 CSV Decimal 复算缺失、有效 n 和聚合；对照 RESULT 的字段、数值、容差。验证失败即 exit 5，即使子进程 exit 0 也不交付为成功。
6. **同源落盘**：先保存生成的实际 RESULT、最终源代码、校验、运行收据和日志，再映射 output_contract。`run_receipt.json` 是最终成功/失败状态，不用人工合理数字改 RESULT。
7. **结果验收**：非空 JSON 可读且为对象，验证全过，源代码 hash 与执行版本一致。对缺列、SQL、图或 Office 要求的任务，还需分别复算行数/键粒度、打开 PNG/PDF 检查；这些额外API未运行时明确标 unimplemented，而不是本 runner 默认已做。
8. **失败恢复**：定位最小根因，保留失败 code/log/receipt，不覆盖输入；修正后生成新 hash 与 attempt 记录。重复执行科学结果应一致，日志时间/耗时如实变化。

## 状态码与产物
- 0：执行和全部独立验证通过；2：输入/job/schema/路径检查失败；3：静态拒绝或未审查；4：子进程失败、超时、缺 RESULT、非 JSON；5：静默数值不匹配。
- 正常：`generated_analysis.py`、`result_data.json`、`validation.json`、`stdout.log`、`stderr.log`、`run_receipt.json`，可选 `result.json`。
- 验证失败仍保留错误 RESULT 和代码，以便诊断，但 receipt/validation 标失败；连接依赖缺包、权限不足、超限或数据库错误都不能包装成统计“无差异”。不确定性、未执行步骤与降级边界必须显式列出。

## 最小非官方示意

合成 CSV：

```csv
group,value
A,1
A,3
B,5
B,NA
```

合成生成代码：

```python
values = [float(row["value"]) for row in ROWS
          if row["group"] == "A" and row["value"] not in CONFIG["missing_tokens"]]
RESULT = {"n": len(ROWS), "mean_a": sum(values) / len(values)}
```

配合上述 job 运行。示意子进程 returncode=0，独立复算 n=4、mean_a=2；缺失 B/NA 不会被当成 0。若把 `mean_a` 改为 999，子代码仍能跑完，但父验证应 exit 5；若代码改为 `open(...)`，静态门应在派生子进程之前 exit 3。两者均不算成功。

## 自查与维护

修改 runner 必须重跑成功、危险 API、静默错数、空结果、缺列、非法类型、超时、高内存、RESULT 缺失/NaN、权限和重复运行。验证退出码必须由真实命令产生；比较父进程独立计算、输入哈希、代码 hash 与子进程日志，不能只检查“文件存在”。升级 job schema 时同步示例与恢复测试，记录为什么改动安全边界。

坚持最小标准库、相对资源路径、no README，submission 全部 UTF-8、≤50 个文件、单≤500KB、总≤2MB、无 symlink。无真实公开 GitHub URL 不创建 `GITHUB_URL.txt`，不发布/PR/正式提交或生成令牌；正式提交需用户另行明确授权。
