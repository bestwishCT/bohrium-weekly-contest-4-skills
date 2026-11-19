# feasibility-audit-v1：内部审查格式

构造/app/work中的辅助输入，然后按实际output_contract写正式结果。不得把这里的`validation_status`或内部键冒充官方字段。

- `schema=feasibility-audit-v1`；`versions`恰好3项，每项保留唯一`ref`。
- 每版`scenario`为`organizer_prospective`（实际官方案例）或`synthetic_nonofficial`（虚构测试）；前瞻场景仍不是预约事实。
- 每版`findings`恰好覆盖七域：假设与变量、对照组、设备材料、时间成本、参数范围、风险与异常、伦理与安全。每条必须含`domain,status,plan_ref,source,judgment`。`status`只能为`pass/conditional/block/info_missing`。
- `conditional`必须含非空`conditions`，每项含`action,owner_role,before,verify,review_point`。条件不齐全不算可闭合。
- `resources[]`含`item,state,source`。状态只能为“已具备、可外协、需采购、不具备、信息不足、待核验”；中文顿号属于“信息不足、待核验”整体，不拆成两个状态。
- `sample_rule`：`groups,replicates,per_sample,loss_factor,available,unit`。用量=`groups × replicates × per_sample × loss_factor`；没有损耗依据时只有在明确无损口径下填1，否则留空并报缺值，不能默认为1或0。
- `cost_items[]`：`id,quantity,unit,unit_price,currency`；只汇总同币种，外币没有明示汇率时保留分开结果。
- `budget`：`amount,currency`；`booking_available_day`、`procurements[].ready_day`、`duration_days`、`window_end_day`采用case明示的同一日序号口径。完成日=`max(预约可用日,采购就绪日)+duration_days`，未建模的人员/等待不得被省略后宣称真实完工。
- 可选`changed_domains`标明本版本相对前版需要复审的七类域；保留旧判断、新判断和变更定位。

可行类输出仅为辅助判断：阻塞事实→“不可行”；信息缺口/结构错误→“无法判断”；仅有完整条件→“有条件可行”；其余→“可行（仅本次文档审查口径）”。正式result必须使用case/task允许的结论词，不得强行复制内部结论。
