---
name: orchestrate-evidence-research
description: "将复杂科研问题拆成有依赖的可检索子问题，编排真实检索、网页、PDF、表格与计算工具，并产出可逐句回链的证据矩阵和有界研究结论。用于深度研究 Agent、多源科研问答、需要调用血缘、检索停止条件、冲突比较与断点恢复的任务；不用于单篇精读、翻译润色或脱离材料证明全球空白。"
---

# 依赖感知的深度研究

## 触发、输入与不变量

用户给出复杂研究问题、要求多步检索/解析、追问“依据在哪里”或需要复跑研究路径时触发。只做研究拆解、工具编排和证据综合；接收而不替代精读、方法学审查和润色。

1. 完整读取 `/input/case.json`、`/input/task.md`、真实材料及实际 `case.json.output_contract`。逐项保留输入标识、类型、层级和枚举；不得把本技能的辅助字段冒充官方字段。
2. 读取实际 `environment/resources.yaml`，确认检索、网页、PDF、OCR、表格、计算工具的名称、版本、权限和调用条件。快照只列资源名，不能据此宣称某个 CLI 已安装或测试。
3. 实际 PDF 无预抽取答案层。先用获准工具读取原文，文本按页保存到 `/app/work`；不依据领域标签、紧凑修复标记或记忆补造文献、数量或科学事实。`9actual sources/10records` 仅是待实际材料核实的官方原文标记。
4. 每个来源登记实际路径/URL、来源版本或检索日期（仅真实可得时）、全文/摘要状态、权限/授权和内容哈希。未许可、抓取失败或权限不足的来源不可据为已读全文。
5. 全程区分案例内检索与获准外部检索；不声称全球覆盖、首创、完成实验或平台增益。平台负责三案例重复与原生轨迹，本技能不得伪造。

## 冻结真实任务与部分结果

安装根目录固定为 `/skills/weekly-skill`，中间物放 `/app/work`，最终结果放 `/app/output`。先运行：

```bash
python3 -B /skills/weekly-skill/scripts/research.py inspect \
  --case /input/case.json --task /input/task.md \
  --work /app/work --output /app/output
# 若资源清单在当前目录，追加 --resources environment/resources.yaml
```

该命令原样保存 task 和 output_contract 到 work，记录 Python 实际版本、输入哈希、资源读取状态，并先保存 `run_status.json`。冻结后再生成 `/app/work/research_input.json`，不要预先假定固定案例答案或 JSON 字段。

## 本题专属编排

### 1. 问题与依赖 DAG

先写对象、机制/变量、比较、方法约束、时间范围和证据要求。拆为原子子问题，每项指定：回答目标、所需原文、结论用途、`depends_on`、`execution_step`、来源范围、检索式字段与停止条件。术语界定先行；全文获取先于表格抽取；可并行者共享同一步。不要把依赖计划写成已执行结果。

### 2. 有边界的检索与停止

每个计划保留 `query`、字段、数据库/材料范围、时间/语言限制、计划状态、真实工具调用 ID。停止须同时满足：声明的检索变体已完成或逐项记为受阻；每个已答子问题至少有一行精确原文证据，未答项明确 blocked；关键结论的实质分句均可回链。关键冲突若材料中存在双方，至少各核一条原文。

只有指出尚未闭合的证据缺口与预期边际收益时才追加检索；同义改写、引文追溯仅在工具可用且与缺口相关时执行。相同来源与参数不重复执行，真实重试必须关联失败父调用。达到案例约束或 64 请求、900 秒、16384 tokens/响应预算时保存已得证据并停止，不以“饱和”冒充全球穷尽。

### 3. 实际工具血缘

每次真实检索、抓取、PDF/OCR、表格抽取或计算登记 ID、类型、完整参数、来源、状态、返回摘要、返回数（空检索为 0）、相对 artifact 路径和错误。产物必须实际存在。状态限用 `ok`、`empty`、`error`、`timeout`、`permission_denied`、`parse_failed`、`not_executed`。后步读取结构化 artifact，失败时只重跑受损调用及其后继。未发生的调用不得入成功日志。

### 4. 证据矩阵与有界综合

每个证据行含 ID、子问题、真实来源、逐字摘录、页/段/表定位、成功 tool_run_id、比较字段、支持范围与限制。PDF 文本路径相对 `/app/work/research_input.json`；页间用 form-feed 保持页码可核。

先写只含一个可核实质论断的 claim，再连接 evidence/subquestion；标记 `material_fact`、`limited_inference` 或 `unknown`。冲突至少比较对象与方法两个维度并保留双方来源；可以解释适用范围时解释，否则标 unresolved，不按票数裁决。研究空白须写入已有覆盖、精确缺失比较及相近工作排除依据；空检索只入运行限制。

## 可执行校验与输出契约

构建 `/app/work/research_input.json`：顶层为 `sources`、`tool_runs`、`evidence`、`conflicts`、`uncertainty`、`changes`、`data`、`result`。`data` 含 `question`、`subquestions`、`claims`、`gaps`；脚本 schemas 以本文件和 `--help` 为准，不引用作者机器文件。

`result` 是依据真实契约编写的映射模板。可用 `$data.claims`、`$derived.coverage`、`$evidence_matrix`、`$sources`、`$tool_runs`、`$uncertainty`、`$changes`、`$status`、`$case_id` 引用同源数据。只映射真实契约允许的字段；`$status` 仅在其枚举允许 `completed/needs_review/partial/blocked` 时使用，否则按真实枚举重写，运行状态始终还在 audit 保留。

```bash
python3 -B /skills/weekly-skill/scripts/research.py build \
  --input /app/work/research_input.json \
  --case /input/case.json --task /input/task.md \
  --work /app/work --output /app/output
```

输出 `/app/output/result.json`（必有真实 case_id 和全部原契约字段）与 `/app/output/research_audit.json`。后者始终携带主结果审计、证据矩阵、来源定位、运行状态、不确定性、变更和恢复信息。`/app/work/input_receipt.json`、`integrity_audit.json`、`recovery_point.json` 保留血缘。只有实际生成 Office/slide/diagram 才另附真实 PDF/PNG 预览；普通 JSON/Markdown 不机械绘图。

## 异常与恢复

| 异常 | 行为 |
|---|---|
| 空检索/术语歧义 | 保存每次实际查询；只在授权范围改写相关变体，随后标 blocked，不推出研究空白。 |
| PDF/网页/表格失败、限流、权限不足 | 保存错误与 artifact，已 admission 证据仍可用；受影响结论降级或移除，恢复后从失败点续跑。 |
| 证据冲突 | 双方并列进矩阵和不确定性，完整输出 `needs_review`，不隐藏反方。 |
| 契约/规则变更 | 最新官方规则与本次真实 output_contract 优先；重映射并重跑，保留差异与受影响产物。 |
| 计数/引用/依赖校验失败 | 输出 invalid 与可修复位置，不把无效 JSON 宣称为成功。 |

退出码：0=完成或需复核的诚实结果；4=无可用证据；5=部分工具失败但已保存部分结果；2=输入、证据链或契约无效。4/5 不是补造成功结果的理由。

## 最小示例（非正式研究案例）

以两条明确授权的合成文本比较“方法甲/乙在虚构装置中的限制”，只演示一条 Q DAG、两次本地工具调用、一条 claim。将本地合成 case、task 与 canonical input 分别放在 `/app/work/example/input`、`/app/work/example`，运行上述 build；示意状态可为 `partial`，示意 claim 必须写“仅在这两条合成材料内”，不得引用真实论文或冒充平台案例。

## 依赖、自查与维护

依赖仅 Python 3.9+ 标准库；作者烟测版本 Python 3.9.6，实测记录在同题 submission 外 `verification/smoke_result.json`。生产仍以启动时实测版本、resources.yaml 和实际 PDF/检索工具为准。运行时若缺少真实工具，保留降级结果，不静默安装或联网。

维护时修改脚本后必须用同一合成夹具重跑正常、空检索、冲突和工具失败；抽查原文→证据行→claim 与 claim→原文双向链，核对 DAG、停止条件、无重复检索、六类视图及实际契约。50 个 UTF-8 文件、单文件 500KB、总计 2MB、无符号链接为提交硬限制；作者改动仅在用户明确授权后发布，不自行创建 GitHub 链接、PR、提交或令牌。
