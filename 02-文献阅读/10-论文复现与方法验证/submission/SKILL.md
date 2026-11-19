---
name: repro-check
description: Execute exactly two supplied numerical method candidates with pre-committed per-metric tolerances, provenance, actual environment snapshots, unedited stdout/stderr logs and reproducible/partial/not-reproducible conclusions. Use for parameter/code/metric verification and difference analysis; never claims author-code, physical-experiment, full-PMLB, or missing-repository reproduction.
---

# 参数／代码／指标验算与差异归因

## 固定范围与禁区

本技能只执行实际运行包两份候选数值方法，不另选宽泛训练复现。组织方合成试验不是原论文作者代码、物理实验或全量 PMLB。缺原始数据/仓库或许可证就保留缺失，不下载、不伪造 commit/log。

版本 `1.0.0`，Python 3.9+ 标准库，Python 3.9.6 实测两份独立脚本的 subprocess 执行与日志。候选自身依赖必须在实际环境验证后另列；本运行器不声明候选所需第三方包已安装，不联网安装。

## 输入、事前约定和路径

读取 `/input/case.json`、`task.md`、`environment/resources.yaml`、`repro-workorder.json`、两个 method 脚本及论文/参数来源。workorder 每方法有 `method_id/script/args/source/parameter_provenance/expected_metrics/tolerances/timeout_seconds`，顶部固定 seed、data_rule、statistic 和 comparison_scope。

脚本执行前先写 `/app/work/tolerance-precommit.json`，含 workorder SHA、UTC、每指标 absolute/relative 容差和比较口径，不可看后修改。实际环境写 `/app/output/environment.json`，记录真实 Python executable/version、平台、机器和存在的线程变量。

安装 `/skills/weekly-skill`，相对资源 `scripts/common.py`、`scripts/workflow.py`。所有工作过程 `/app/work`；结果、`environment.json`、每方法 stdout/stderr log 在 `/app/output`。真实 `output_contract` 逐键嵌套适配，保留 `case_id`；`runs/reproduction_results/result` 映射 `methods`。

## 强制执行流程

1. 对输入材料、参数、版本哈希/不可得状态登记。论文明确、代码推断、自行设定三类来源不混写，默认值冲突并列。
2. 在任何运行前锁数据生成规则、划分、种子、统计与绝对/相对容差，保存事前文件。
3. 用参数列表和 `subprocess`（shell=False）分别执行两个候选，工作目录分开，设置真实 timeout，捕获 returncode、原始 stdout/stderr 和实际墙钟时长。
4. 候选最后一行必须是其实际 JSON 指标。逐项对齐论文/工单同任务同指标 expected，计算绝对差/相对差；未知或不互比的指标不强算。
5. 每方法全部预期指标通过才 `reproducible`；两法综合给 `reproducible/partially_reproducible/not_reproducible`。一个失败不掩盖另一个真实通过。
6. 差异无法由实际诊断支持时写 `not_determined_requires_diagnostic_evidence`，不猜作者错误。需要新增对照必须称本轮诊断试验且不改原容差。
7. 复读日志、事前文件、环境与结果，确保两候选、参数来源和 scope_limit 齐全。

## 失败恢复与安全

- 全部两法通过退出 0；任一失败退出 2，但保留已运行指标和原始失败 log。
- 超时/缺脚本/非零退出/stdout 非 JSON/缺真实值分别记录，不包装成服务正常；禁止吞错误继续给通过。
- 容差缺失或方法数不是两份，执行前即失败。运行不用网络、令牌或提权；输入代码仍需在任务允许的沙箱内执行。
- 本机烟测不是 DSH、原研究复现或科学有效性证明，实际环境差异需版本化。

本流程无必需 Office/图形；若合同另需，必须实际渲染并核查。

## 最小示例（合成、非正式）

A 脚本输出 `{"metric":1.0}`，B 输出 `{"metric":1.01}`，共同 expected=1.0、事前 absolute=0.02，数据规则 synthetic-v1、seed=7。

```bash
python3 /skills/weekly-skill/scripts/workflow.py --case /app/work/repro-case.json \
  --task /app/work/task.md --input-dir /app/work --work /app/work/run --output /app/output
```

示意输出：两法 status=reproducible，A/B 的 stdout log 分离，差值 0/0.01；不是原论文成绩。B 缺失时 overall partial。

## 自查与维护

确认事前文件时间早于执行、日志未改、环境可追、哈希和参数来源完整；核对小数浮点边界并必要换 Decimal。新增诊断必须独立记录、不得修改历史输出，规则更新跑失败/超时回归并升版。

最新 GitHub 加分需真实公开 URL；没有则不创建 `GITHUB_URL.txt`，不 commit/PR/发布。
