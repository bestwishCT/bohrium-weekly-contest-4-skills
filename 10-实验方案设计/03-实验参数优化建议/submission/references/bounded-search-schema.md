# bounded-search-v1：内部搜索格式

这是/app/work中的辅助数据，不是正式result.json合同。每版独立枚举，禁止跨版拼接参数或响应。

- 根：`schema=bounded-search-v1,case_id,budget,responses,versions`；可选`enumeration_limit`默认4096且最大100000、`max_batch_size`、`batch_cost_limit`。
- `budget`：`amount,currency,time_limit_minutes`。`responses[]`：`name,direction`且direction只能`min/max`，测量、单位、方法等科学口径需在实际结果中另附来源。
- `versions`恰好3项，每项：`ref,scenario,source_refs,parameters,baseline,constraints,fixed_cost,currency,per_replicate_cost,replicates,parameter_costs,setup_minutes,minutes_per_replicate,observations`。
- `parameters[]`：`name,allowed_values[{value,unit}]`，只来自明示离散域。`baseline[]`逐项参数值与单位；不一致报`BASELINE_UNIT_OR_DOMAIN_MISMATCH`。
- `constraints[]`：线性组合`terms{参数名:系数}`、`op`(`<=/>=/==`)、`limit,source`。这里只能表达给定可数值化约束；安全伦理文本仍需人工审查，不能因枚举通过就视为批准。
- 单候选成本：`fixed_cost + per_replicate_cost × replicates + Σ(parameter_costs[p] × value[p])`；分钟：`setup_minutes + minutes_per_replicate × replicates`。参数成本系数的“币种/参数单位”必须能从case材料解释；不能为制造目标函数杜撰系数。
- `observations[]`：`values`必须含每版的全部参数（与point同格式），`responses`匹配根响应名，`source`指向实际历史数据。脚本只回传给定响应，不预测、不把合成测量当wet数据；一个点重复观察会拒绝。
- `source_refs`至少含`domain,baseline,cost,schedule`实际定位；`scenario`实际案例用`organizer_prospective`，测试用`synthetic_nonofficial`。

候选先过联动、预算、时间，再在实际有响应的可行点中按“成本min、分钟min、响应方向”求Pareto集。未评估点按成本和字典序提出下一批，受批量与批次预算限制；批次成本单列。`provided_evaluations_complete_not_convergence_claim`不表示最优或收敛，只表示给定点均有输入观察。

`tradeoff_edges`给保留点间的成本差和响应差，用于解释牺牲而非造权重。无可行域、币种不同、响应缺失或格式异常均不得强行输出推荐，须由Agent按task给出阻塞与补证路径。
