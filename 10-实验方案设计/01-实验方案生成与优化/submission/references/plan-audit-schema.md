# plan-audit-v1：内部复核格式（不是官方result.json合同）

先按实际case/task抽取原始材料，再构造本格式到/app/work。**不得**把这些键复制为正式JSON的额外顶层键；最终结果只按case.output_contract组织。

- `schema`固定为`plan-audit-v1`；`case_id`来自实际输入。
- `scenario`：官方案例用`organizer_prospective`；虚构自测用`synthetic_nonofficial`。前者仍不代表现实库存、档期或获批。
- `baseline_ref`：指定修订基线；脚本只在该版本通过全部检查时保留它，不判定科学的“最佳版本”。
- `budget`：`{amount,currency,time_limit_minutes}`均来自case/task；缺少金额时不要填0。
- `versions`：恰好3个，每个有唯一`ref`、`parameters`、`cost_items`、`steps`。
- `parameters[]`：`name,value,unit,allowed,source`；`allowed`是材料明示的有限值及单位列表。先人工保留原文定位并写明任何单位换算，脚本不做无来源自动换算。
- `cost_items[]`：`id,quantity,unit,unit_price,currency,source`。`unit_price`必须是每一个`unit`的币种价格，逐项计算`quantity(unit) × unit_price(currency/unit)`；非预算币种不折算、不合计。可选`declared_total`用于核对原方案数字。
- `steps[]`：`id,duration_minutes,predecessors,source`。脚本以有向无环图最长路径计算占用的关键路径；并行关系必须明确，等待是否占机须由科学步骤解释，不能只谋求更短数字。
- 所有小数用十进制可解析字符串；`source`必须可定位到实际文件页/段/表行。示例中的fixture://仅用于非官方合成验证，不得进入真实case的来源。

输出含每版错误、有限域外修复菜单、分币种小计、逐项算式、关键路径和所选基线。`pass`仅表示可判定算术和结构通过；科学假设、人员授权、安全、伦理和实际可用性仍需Agent按合同写确认项。

建议同时保留`change_log`：旧ref、新ref、变更字段、旧值/新值/单位、来源、理由、受影响步骤/资源/成本/时间/风险、重跑命令与新旧验证结果。它不是脚本的虚构自动补全字段。
