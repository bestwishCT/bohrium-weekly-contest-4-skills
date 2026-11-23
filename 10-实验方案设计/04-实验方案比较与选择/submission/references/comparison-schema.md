# aligned-plan-comparison-v1：内部比较格式

在/app/work构造本辅助结构；不得把其字符串或“七类字段”一栏直接复制成未经case公开的正式JSON键。

根字段：
- `schema=aligned-plan-comparison-v1,case_id,cost_currency,time_conversions,criteria,plans`。
- `criteria[]`含`name,unit,direction(min/max)`；可含派生的`cost_total`、`duration_days`，其余对应各plan.metrics同名值。只在task明示时设置比较方向，不能编造权重或综合分。task/case明示字典序时，每个criterion另给唯一正整数`priority`（1最高）及定位`priority_source`；缺任一项、来源或优先级重复均不推荐，不能靠指标的metric source冒充优先级来源。
- `time_conversions.hours_per_day`只有case明示才给。各条时间路径都可包含并行采购/预约/实验，按path内求和、path间取最大；缺转换判错。

每个plan：
- `ref,scenario,fields,hard_checks,cost_items,declared_cost(可选),paths,metrics,switch_trigger(可选)`。恰好3个独立版本。
- `fields`至少含`objective,risk,cost,feasibility,evidence`，每项含`value,source`。只是五维比较语义，不是官方额外顶层字段。
- `hard_checks[]`含`id,status(pass/fail/unknown),source`，可附detail/missing；未知不能算通过，失败不能被其他指标抵消。
- `cost_items[]`：`id,quantity,unit,unit_price{value,currency,per_unit},source`。只有数量单位与单价分母一致才计算；异币种分列不完整，不能按0。
- `paths[]`：`id,components[{value,unit}]`。单位为day，或hour并明示hours_per_day。工期=各路径组件日和的最大值，关键路径保留输入定位。
- `metrics`中每个比较指标是`{value,unit,source}`；单位与criteria不一致则无法比较。
- `scenario`实际案例为`organizer_prospective`，自测为`synthetic_nonofficial`，均不代表现实预约/报价/审批事实。

脚本先处理硬失败和未知。全部criteria有来源化唯一`priority`时，在eligible候选间按priority做字典序选择，不要求全指标支配；完全没有priority时才沿用“一个eligible候选对其余全部候选所有明示指标不劣、至少一个严格更优”的唯一支配规则。两种模式都不造权重或总分，完全并列仍不推荐。未获选者逐一给硬失败、不可比、优先级或被支配理由；敏感性只给alternative-selected实际差值，不造临界概率。无可靠唯一选择时退出2并保留矩阵与解除条件。
