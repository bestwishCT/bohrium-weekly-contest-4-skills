---
name: aligned-plan-choice
description: 为aligned_plan_comparison_and_choice任务把三个版本化实验方案对齐到目标、风险、成本、可行性与证据矩阵，复算全成本全周期，先执行硬约束淘汰再按明示判据形成可辩护推荐、逐项舍弃理由、备选切换与敏感性；适用于多路线选型，不伪造权重、实时报价或实验审批。
---

# 实验方案比较与选择

## 触发与任务边界

当用户需要比较多方案并选择路线，或成本/风险/可行性口径混乱导致无法选型时触发。贡献是“同口径矩阵、硬淘汰、证据挂载的选择链”，而非分别复述、原始方案创新、全文翻译或统计建模。不实际执行危险实验，不伪造预实验、风险事件、设备/预约/报价/审批状态。

## 案件输入与正式合同

1. 完整读取`/input/case.json`、`/input/task.md`及case引用的全部方案原文、真实论文、手册、报价/货期、机时人力、预约、预实验和历史日志。从真实PDF读取所需内容并定位页段表行，不猜领域标签。
2. 读取实际`case.output_contract`的原字段、类型、嵌套、矩阵/附件和必需验证。官方提到“七类字段”但赛前未公开键名，必须从实际case/task识别；只自创工作矩阵，不自创正式顶层键。
3. `/app/work`存证据表、复算和版本草稿，`/app/output/result.json`含真实`case_id`和合同全部字段并尽早保存部分结果。安装根`/skills/weekly-skill`，资源用相对路径`references/comparison-schema.md`、`scripts/plan_comparison.py`。
4. 读`environment/resources.yaml`并记录实测工具版本。事实、可复算值、比较假设、缺失项、待确认项分开；前瞻资源/预约只按题设。

## 对齐、淘汰与选择

1. 实际三版候选逐一固定ID、版本与原文定位，绝不跨版拼接参数、报价、风险或机时。先固定研究目标、共用硬约束、变量、对照和判据，再填同一矩阵；标不可比和缺失而非留空伪装一致。
2. 从实际合同识别七类语义，至少对齐目标、风险、成本、可行性、证据：共同终点与变量/对照，参数值/单位/有限域/步进/容差/上下限来自设备/材料/安全/文献，型号量程精度附件、人员与可得性，以及风险触发、后果依据、缓解、回退和转人工。理论可行和本案当前可做分开。
3. 复全成本：一次性与按次、机时、耗材、样前准备、人力、返工；数量单位×给定单价/计费单位逐行计算，缺单价/返工率不按0或造数。复全周期：准备、货期、预约、表征周转、等待、人工与执行，建立并行路径和关键路径，区分占机/人工/日历。每格保留原始值→换算→公式→结果→单位。
4. 检查有限域和设备材料安全兼容；不修改原始事实使候选获胜，补条件必须与原版分开。新增对照/返工/替代若降低风险，同步调整成本与排期并复查矩阵一致性。
5. **先硬淘汰**超安全、参数窗口、预算、时间窗、设备不可得等；硬未知标无法判断而非通过。其余候选只在任务明示方向、权重或优先级上比较。未给权重时采用非支配/分情景，不造总分；被舍弃候选逐一给硬冲突、成本差、时间差、证据缺口或风险不可接受理由。
6. 推荐写版本、目标/硬约束、成本工期、主要风险与直接/间接证据边界。敏感性只分析会影响结论的有来源数值或偏好，给算术差或明示情景，不编临界点/置信度。给合法备选及切换触发（有则）；全不可行则“无勉强推荐”，列解除条件。
7. 重要判断挂载实际来源页/表/行，风险成本和版本同步校验。安全、伦理、设备、预约及专家确认另列，选型建议不视为批准。

## 确定性同口径复核

先读`references/comparison-schema.md`，把实际材料映射到`/app/work/comparison.json`。真实case必须先preflight、尽早落部分正式结果，最后做合同finalization；辅助JSON仅在`--auxiliary`中核对case_id，绝不充当正式顶层字段：

```bash
python3 /skills/weekly-skill/scripts/case_io.py preflight --case /input/case.json --task /input/task.md --result /app/output/result.json --manifest /app/work/preflight.json --auxiliary /app/work/comparison.json
python3 /skills/weekly-skill/scripts/plan_comparison.py --input /app/work/comparison.json --output /app/work/comparison-result.json
# Agent按真实合同填完/app/output/result.json后：
python3 /skills/weekly-skill/scripts/case_io.py finalize --case /input/case.json --task /input/task.md --result /app/output/result.json --manifest /app/work/preflight.json --report /app/work/finalization.json
```

`case_io.py`为自包含标准库入口，读取case/task和case实际声明的本地材料，记录字节数/SHA-256及三版原ID、版本、来源，先写partial；缺材料、辅助/正式case_id不一致、preflight不完整均退出2。finalizer递归校验required/type/enum/items/嵌套对象，并拒绝未公开顶层auxiliary键；支持JSON-Schema-shaped output_contract，其他合同形态诚实报不支持而非猜测。

比较脚本处理恰好3版，复算分币种成本、明示换算下的关键路径、五维来源、硬门与单位一致。全部criteria有`priority`和`priority_source`时按明示顺序做字典序选择；完全无优先级时才要求唯一全指标支配者。硬淘汰/未知仍先处理，优先级不完整、完全并列或无唯一支配均退出2并保留实测差值；不强行套权重。旧`plan_comparison.py --input/--output`合成测试调用保持不变。

## 结果、恢复与失败分支

`/app/output/result.json`按合同容纳同口径矩阵、推荐版本、依据、未选理由、备选/切换和推荐方案的完整执行或缺口信息，以及风险证据、敏感性、确认项、版本变化、状态和不确定性。缺字段→保留不可比并限制选择；设备不可得/超预算/窗口冲突→显示冲突；全部不可行→不强选；来源冲突→并列定位与适用条件；工具失败→保存中间矩阵、真实错误与复跑命令。

仅合同要求时输出可复算附件；可编辑Office/图示必须由真实文件渲染PDF/PNG，不机械产生图表。验证JSON可解析、字段类型不变、全部artifact存在且确实可读。

## 最小非官方示例

虚构A/B/C同一tasks指标：成本CNY（min）、工期day（min）、风险缓解coverage（max）、证据level（max）。A耗材`2×2=4 CNY`加机时`30×0.1=3 CNY`，总7 CNY；两路径实验2日、采购3日，关键工期3日。其他方向均由fixture提供，A全指标不劣且至少一项严格更优。示意输出（非正式案例结果）：A总成本7 CNY、3日并被唯一选择；不是实际报价、预约或实验批准。

## 依赖、状态、维护、自查

本地实测Python 3.9.6（`/usr/bin/python3`）；需Python 3.9+标准库，实际运行再次记录`python3 -V`。每案限64模型请求、900秒、响应含推理16384 tokens；平台3案例×每条件3重复，模型/运行/版本轨迹平台留存，本技能不捏造。

版本1.0.0；规则基线来自2026-11-18 manifest与`challenge-7be76e91`快照（`ARMI:2611.000083v1`，SHA-256 `02ecd0981fb5d07ee3f2a7de6ac7267651fd3cbf35058e10219851f94351734c`，`current_matches_saved=true`）；最新实际case/task优先，更新时保留来源差异并提升技能版本。

验收：三版矩阵无交叉污染；五维及合同七类语义齐全；硬门与软比较分离；全成本/周期公式真实；风险与成本时间一致；推荐/舍弃/备选/敏感性可追溯；无权重时不造总分；合同JSON与预览真实；正常及关键异常脚本重跑。无真实公开GitHub链接不创建`GITHUB_URL.txt`，资源≤50文件、单≤500KB、总≤2MB、UTF-8且无符号链接。
