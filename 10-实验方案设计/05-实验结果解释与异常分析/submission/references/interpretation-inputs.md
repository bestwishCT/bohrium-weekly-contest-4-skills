# 实验解释辅助格式与特殊输入

以下均用于/app/work，不是正式output_contract。Raw case映射不得留下个人路径。

## experiment-anomaly-audit-v1

根（可位于合成fixture的`anomaly`键）：`schema,case_id,scenario,records,anomaly_rules,causes,review_actions,next_experiments,review_budget,unexplained`。`scenario`为`case_observed`（真实case既有观察）、`organizer_prospective`（题设前瞻复核资源）或`synthetic_nonofficial`（测试）之一；观察来源和拟议动作仍须分开。

- `records[]`：`metric,sample,batch,channel,well,replicate,time_index,value,unit,role(control/test),quality(ok/saturated/out_of_range/missing),source{file,sheet,row}`。按实际维度给定位，不存在的维度可在Agent正式结果中说明，但不可编孔位。饱和/超量程/缺失不作精确数值过滤；原值仍在警告中存在。
- `anomaly_rules[]`：`id,metric,batch,channel,time_index,control_sample,unit,deviation_gt,threshold_type(task_given/reference_calculated/exploratory),threshold_source`。控制重复在该维度取中位数，非控制对象`abs(value-median)>阈值`才标偏离。阈值来源不明不判异常；单位不同不提前统一；控制少于2只警告，不计算显著性。
- 趋势按同一metric/sample/batch/channel/unit在每个时间点取可用读数中位数，再给首末点差；数值time_index用Decimal排序（如1,2,10），字符串保持字符串字典序且不擅自数值化，混合类型仍报结构错误。这是受限描述，不自动声称拐点、平台或因果。
- `causes[]`：`id,statement,evidence_class(direct_observation/association/hypothesis),support_refs,counterevidence,remaining_alternatives`；按证据等级/ID排序，只保留候选，不制造湿实验原因。
- `review_actions[]`：`id,action,linked_cause,expected_observation,supports_or_excludes,failure_branch,owner_role,minutes,cost_items,source_refs`。每行成本`quantity,unit,unit_price,currency`；资源公式是分钟和及同币种数量×单价和，状态恒为proposed_not_executed。
- `next_experiments[]`保留`linked_cause,changed_variable,controlled_variables,judgment,source_refs`等实际计划，明确仅为建议。`review_budget`可给`amount,currency,time_limit_minutes`，仅已知总量与已知预算冲突才报错；缺预算写unknown、不判冲突。缺minutes、quantity、unit_price或currency写不确定性且辅助状态为partial（退出0），相应合计为null而非0/默认CNY；其他已知同币种行仍保留真实公式。显式非法值、负数及真实超预算仍失败。

## 三类实际路线（只执行当前case实际出现的）

1. **197 raw bending traces**：枚举真实轨迹文件并计数，关联原工作簿sheet/单元格及MATLAB变量；计到多少写多少，不为197造数。按实际变量做趋势/异常复算。
2. **Iris 5 split×2 estimator**：用case/task指定数据、split、estimator和种子。随附`iris_5x2.py`只提供明确允许的`nearest_centroid`与`nearest_neighbor`，要求输入5组非空且不重叠的train/test indices、2个不同estimator及dataset。若task指定随机森林、逻辑回归等其他算法，不得用本脚本改名替代；从resources.yaml选择已实测的真实实现。未指定实现配置时，在/app/work保存本次显式选择，不称官方配置。
3. **Cefixime**：实际存在该材料路线时读取主工作簿实际12个sheet和完整论文，记录sheet名、公式/单位/缺失、跨表样本关系和论文页码/章节。不读一两个表代替全部；paper与数据冲突保留。

Iris输入包含`config_source,dataset[{features,label}],splits[{id,train_indices,test_indices}],estimators`。脚本真实计算10组合并留预测与accuracy=correct/total；任何用于正式case的准确率都是本次真实运行，非预设标签。正式结果必须同时保留可复跑实现、split/estimator配置和实际退出状态。

## 17行复核最低要求（映射到实际合同而非新增JSON键）

每条趋势、异常、原因要求来源坐标；每条复核写输入、观察、支持/排除、失败分支和角色；未解释指标、对照、重复、边界清晰列出。无法解析PDF/MAT/XLSX时用已核实替代工具并保存错误，不把不可读对象写成已分析。
