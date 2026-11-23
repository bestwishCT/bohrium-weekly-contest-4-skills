---
name: explain-experiment-anomalies
description: 解释真实实验表格、197条弯曲轨迹、MATLAB、多批次/通道/孔位或Cefixime十二表与全文的结果趋势和异常，按证据等级列原因并生成可执行复核及后续方案；支持任务指定的Iris 5 split×2 estimator真实计算、阈值来源追溯和饱和/缺失/单位冲突处理，不伪造湿测量或因果。
---

# 实验结果解释与异常分析

## 触发与边界

用户要求从原始实验数据判读趋势、定位异常、解释原因或规划复核/下一轮实验时触发；实际Iris split计算也适用。贡献为“五段结论+数据坐标+证据分级+区分性复核”，不是通顺复述读数或事后编机理。可做真实数据分析和实验设计，但不声称完成湿补测、设备检查、对照或统计显著性。

## 真实输入、路径、依赖与合同

1. 完整读取`/input/case.json`、`/input/task.md`及全部数据、 curves/logs、工作簿、MATLAB、论文。按文件实际内容识别cases，不从“弯曲/Iris/Cefixime”标签猜变量、单位或结论。PDF解析真实全文件。
2. 从`case.output_contract`保留原字段、层级、类型、数据/图表/附件及验证。`/app/output/result.json`必须有真实`case_id`和全部合同字段；五段标题及本技能辅助键不得臆造为正式顶层键。
3. `/app/work`存来源索引、清洗/换算、代码配置和中间结果；真实最终JSON、实际计算日志及合同可编辑产物存`/app/output`。尽早保存可解析部分结果，标明已解析/未解析范围。
4. 安装根`/skills/weekly-skill`，资源以其下相对路径`references/interpretation-inputs.md`、`scripts/anomaly_audit.py`、`scripts/iris_5x2.py`调用。读取`environment/resources.yaml`并实测所用工具、库与版本；优先标准库，无法解析格式时明确受限范围而非猜测。

## 数据谱系与五段专属工作流

### 0. 先保全原值和可比较性

建立case/数据版本→文件、sheet、单元格、行列、轨迹、MATLAB变量→样本/批次/通道/孔位/时间点→设备材料批次/参数/日志的来源台账。原始值、清洗、换算、真实计算和模型输出分层；样本改名、批次错配、单位/方法/阈值歧义原样保留，只凭明示关系换算。缺失、重复、对照/重复不足、饱和/超量程单独标记，不插补真值。

### 1. 结果趋势

按每个指标、对照、重复和边界覆盖真实走势；为范围、单位、对照、重复、算法留定位。拐点/平台只在case明示或实际可复算判据下判断，不够拟合就只给受限描述。多指标/多批次冲突分开呈现；无设计依据不算p值/区间/显著性。

### 2. 异常点

异常绑定实际存在的样本、批次、通道、孔位和时间维度，返回原始值、对照/重复参照、偏离幅度、阈值及其来源。区分任务阈值、由实际reference计算、探索性提示；统计偏离、采集/仪器质量和科学异常分开。饱和/超量程/缺失/对照失败单独列，不将截断值当真值。

### 3. 可能原因

每个候选写支持、反对/排除证据、未排除替代解释、定位和证据等级（直接观察/关联/假说）；按证据与可检验性排序。湿原因无日志/对照支持只能是假说，不因异常发生在某批次就断言污染、设备故障或处理错误。

### 4. 复核步骤

每条重要结论产生第二人可做动作：回查文件/sheet/字段/轨迹/单元格、复算指标、核对样本批次映射或真实设备日志、提出有针对性的对照/平行重复。逐步写输入、操作、期望观察、支持/排除哪个候选、失败分支与负责角色；未知时间/成本/设备/安全标待确认，只提建议不冒充已执行。

### 5. 后续实验建议

每个建议映射原因与证据缺口，记改变变量、保持controls、补对照/重复/采样点/参数范围和下一步判据；优先数据澄清以减少不必要实验。样本量依据不足给待确认，不编功效。结果链接全链所依假设变量、对照、设备材料批次、时间成本、参数范围和风险。

## 三类实际输入分别处理

- **197 raw bending traces**：仅在case实际出现时枚举真实轨迹与样本工作簿/MATLAB变量，实际计数、映射并按原轨迹复算；不等于197时报告实际数及差异，不补造。
- **Iris**：仅task要求时真实运行**5 split×2 estimator**并保存代码、配置、索引/种子、逐组合结果和预测。未指定实现选择记为本次选择而非官方配置。任何组合失败保存已完成结果与真实错误，不补剩余指标。
- **Cefixime**：仅该路线实际存在时读取主工作簿实际12-sheet全部工作表及完整论文，核对方法、单位、公式/缺失、阈值与跨表关系；论文页段和数据表冲突不静默裁决。三路线不机械同时套用。

特殊输入说明见相对资源`references/interpretation-inputs.md`；官方附加`Original16LLMJudge`不扩写公式，不伪Judge结果。

## 可复算异常与Iris工具

真实case先运行自包含preflight，再构造/app/work输入；Agent填完正式合同后运行finalizer：

```bash
python3 /skills/weekly-skill/scripts/case_io.py preflight --case /input/case.json --task /input/task.md --result /app/output/result.json --manifest /app/work/preflight.json --auxiliary /app/work/anomaly.json
python3 /skills/weekly-skill/scripts/anomaly_audit.py --input /app/work/anomaly.json --output /app/work/anomaly-result.json
python3 /skills/weekly-skill/scripts/iris_5x2.py --input /app/work/iris.json --output /app/output/iris-actual-run.json
# Agent按真实合同填完/app/output/result.json后：
python3 /skills/weekly-skill/scripts/case_io.py finalize --case /input/case.json --task /input/task.md --result /app/output/result.json --manifest /app/work/preflight.json --report /app/work/finalization.json
```

`case_io.py`读取真实case/task及case声明的本地材料并记录字节数/SHA-256，先落partial；缺材料、辅助/正式case_id不一致、preflight不完整均退出2。finalizer递归校验required/type/enum/items/嵌套并拒绝未公开顶层auxiliary键。支持JSON-Schema-shaped output_contract；其他形态诚实报不支持。无Iris/auxiliary输入时省略相应`--auxiliary`和Iris命令，不伪造配置或组合。

首个分析脚本核对坐标/单位/质量标记，以实际控制中位数和给定阈值复算偏离；数值time_index用Decimal排序，未知字符串不数值化。复核资源缺预算/报价写unknown和不确定性，未知合计为null，只有已知预算才判冲突；已知同币种数量×单价仍真实复算，负值、非法值和真实超预算退出2。第二个脚本只在task允许这两种方法时运行5 split×2 estimator，输出实际10组合、预测和accuracy；配置或指数非法退出2。它不能代替task指定其他estimator的已核实真实实现，也不把本地16个预测正确称为官方16/16。所有脚本stdout和退出状态需如实记录，旧`--input/--output`调用不变。

## 正式输出、恢复与限制

严格按合同容纳趋势、异常点、可能原因、复核步骤、后续实验建议五段主结果及来源、状态、不确定性、未解释部分和编辑记录；同时容纳语义上的后续实验方案、依据风险和确认项。区分材料缺失、无法判断、计算失败。Iris真实代码、配置和实际输出按任务存`/app/output`；湿实验只分析既有材料，不造新测量。

仅合同要求图/表/Office时从真实分析制作，并真实渲染可编辑源文件的PDF/PNG，不机械画图。PDF/MAT/曲线/工作簿失败时用已核实替代、保存错误和已完成范围；不能用文件名推断。完成前按真实合同做JSON/类型/附件验证，不增加顶层字段。

## 最小非官方示例

合成fixture第1时刻控制值`1,1.2 u`（median 1.1 u），样本值4 u，任务演示阈值`abs偏离>2 u`；异常为sample=s1,batch=b1,channel=c1,well=A4,time=1，偏离2.9 u。拟议复核10 min+5 CNY与5 min+(2×3 CNY)，合计15 min、11 CNY。示意输出（非正式案例结果）：一个可定位探索性/给定阈值偏离；不是湿污染证明。Iris合成6行、5 split、nearest_centroid与nearest_neighbor实际运行，示意输出10组合，指标以实际JSON为准而非官方结果。

## 依赖、维护与自查

本机实测Python 3.9.6（`/usr/bin/python3`）；两脚本仅需Python 3.9+标准库，实际运行再记录真实`python3 -V`。XLSX/MATLAB/PDF/sklearn等只在resources.yaml实测可用后使用并记录版本。每案64模型请求、900秒、响应含推理16384 tokens；平台3案例×每条件3重复，真实轨迹和LLM Judge由平台生成，不能预报。

版本1.0.0；依据2026-11-18 manifest与`challenge-8354c69a`快照（`ARMI:2611.000085v1`，SHA-256 `a34152af044e1a85c15a09531a82440333f8b7cd4674006dfb8caaa5bf686f69`，`current_matches_saved=true`）；实际最新case/task优先，规则变化记录差异、修订版本并重测。本地合成验证不包含官方197轨迹、12-sheet或Iris正式数据。

验收：五段逐条定位且涵盖所有指标/对照/重复/边界；未解释项存在；单位/阈值/缺失/饱和保留；原因含支持与排除且区分相关因果；复核可判读且资源复算；特殊路线按实际材料完成；Iris真实5×2而非抄数；正式合同与真实预览合规；无真实URL不创建`GITHUB_URL.txt`；资源≤50文件、单≤500KB、总≤2MB、UTF-8无符号链接，异常与正常烟测可复跑。
