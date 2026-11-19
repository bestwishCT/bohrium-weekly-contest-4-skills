---
name: track-updates
description: 在组织方本地文献快照与增量状态中回放时间窗、识别相对新增和版本事件、合并重复身份并抑制重复提醒。适用于主题订阅的新增、暂停和增量审计，不用于把旧文献重新入库当全球最新研究或伪造定时投递。
---

# 可回放的最新文献增量追踪

## 触发与三种操作

用于主题订阅、连续时间窗、快照差异、预印本/正式版更新和提醒幂等。`subscription.action` 支持：

- `subscribe`：用实际主题、来源、时间字段、窗口及去重设置初始化或恢复本地状态；不是创建远程订阅。
- `view_increment`：只查看一次给定窗口与已有快照的差异。
- `pause`：在本地 `tracker_state.json` 置暂停，随后窗口不处理；不会停止任何外部调度器，因为本 Skill 不创建调度器。

## 正式输入契约

1. 读取 `environment/resources.yaml`，核查是否真的有调度、存储或通知工具；没有就明确只有拟投递能力。读取实际 `/input/case.json`、`/input/task.md` 的 `case_id`、`output_contract`、初始水位、允许动作和时限。
2. 读取九份科学文档、真实 Crossref/Europe PMC/出版方响应、参考图及组织方本地增量状态，不能从文件名或错误标签补日期、版本、状态。
3. 把真实订阅与快照写入 `/app/work/analysis_input.json`。记录 `time_field` 是入库日期还是其他真实字段，绝不默认拿出版年替代入库时间。实际响应的抓取/材料日期只按原值使用。
4. 工作及持久状态位于 `/app/work`，最终结果位于 `/app/output`。脚本先落部分 `result.json`，再在 `/app/work/tracker_state.json` 原子保存状态；异常时不得越过失败窗口。
5. 安装后执行 `/skills/weekly-skill/scripts/run.py`。资源按脚本所在位置解析，不创建系统定时任务、后台进程或外部网络订阅。

## 窗口、事件和幂等专属算法

1. 用带时区的 ISO 8601 时间和 `window_start/window_end` 构造窗口；支持 `[)`、`[]`、`(]`、`()`，起止按声明时区解释，`Z` 和显式偏移可正确转换。跨窗边界不漂移。
2. 当前记录必须有配置的 `time_field`。缺字段、日期不可解析或时区配置缺失时归 `window_uncertain`，既不提醒也不作为水位推进依据。窗口外条目单列，不用旧文献凑新增。
3. 稳定身份依次取规范 DOI、arXiv ID、真实记录 ID、题名+第一作者+年份。无稳定键的记录进入错误/限制，不强行生成“新论文”。同身份的当前多源记录合并为一个事件。
4. 与上一持久快照比较：`new_to_snapshot` 仅表示本地快照新增；已知重复为 `known_record`，显式 `version_of` 或版本变化为 `version_update`，实际状态变化记为 corrected/retracted/withdrawn observed；其他科学字段变化为 `metadata_update`。
5. 每个事件生成含订阅、窗口、身份、类型和元数据的 delivery ID，并写入 ledger。相同窗口重跑从已存快照与 ledger 恢复，重复准备和提醒被抑制；没有真实通道的条目始终是 `prepared_not_delivered`。
6. 所有声明来源均已执行并成功，且没有窗口日期/身份不确定时，才把水位推进到窗口终点；429、失败或未执行导致水位冻结。快照合并累计保存，避免下一窗只看到局部。
7. 状态和 ledger 原子写入，记录推进前后水位、ledger 大小、快照大小和本次是否真正推进，便于逐窗复算。

核心输入：

```json
{
  "subscription": {"id": "sub-a", "action": "view_increment", "time_field": "indexed_at", "timezone": "UTC", "window_start": "2024-01-02T00:00:00Z", "window_end": "2024-01-03T00:00:00Z", "bounds": "[)", "previous_watermark": "2024-01-01T00:00:00Z"},
  "previous_snapshot": [{"id": "p1", "title": "known work", "authors": ["actual"], "year": 2020, "version": "v1", "indexed_at": "2024-01-01T00:00:00Z"}],
  "current_snapshot": [{"id": "p2", "title": "actual candidate", "authors": ["actual"], "year": 2019, "version": "v1", "indexed_at": "2024-01-02T08:00:00+00:00", "source_locator": "response#2"}],
  "source_runs": [{"source": "actual-local-snapshot", "executed": true, "status": "observed", "response_locator": "state.json#window"}]
}
```

## 输出与状态语义

- `/app/output/result.json` 遵循实际合同并含 `case_id`；内部 `events/reminders/window/source_runs/state_transition/duplicate_identities/window_uncertain` 均带判定依据。
- `/app/work/tracker_state.json` 是下一次运行的事实基线；不得在最终 JSON 中虚构外部状态。`analysis.json`、`contract_validation.json` 和 `run_log.json` 保存完整审计。
- `completed` 表示本窗口有有效事件且覆盖闭合；`empty` 表示窗口有效但无事件或空输入；`partial` 表示来源失败、时间/身份不明；`paused` 表示本地暂停。
- 新入库旧论文仍可能是 `new_to_snapshot`，不是全球新研究。旧预印本正式版是版本事件而非自动的新独立研究。所有提醒都是拟制记录，除非实际授权渠道另给可验证回执。

## 依赖与异常降级

Python 3.9+ 标准库（使用 `zoneinfo`）；合成安装副本实测 **CPython 3.9.6**，固定 UTC 烟测不需外部包。其他时区依赖运行环境时区数据库，加载失败时保持 `partial`，不要猜时区。无第三方包、网络或提醒服务。

退出码 0=算法完成，1=领域异常但保留部分结果，2=基本输入不可读。空窗不补旧文献；失败窗口冻结水位；日期缺失不猜；版本关系不清保持待复核；实际撤稿/更正只按材料记录且不删除旧项。

## 最小示意（虚构，非实时、非正式）

使用上面骨架，把 p2 标题设为 `SYNTHETIC SNAPSHOT RECORD`，`case_id` 设为 `demo-tracker`：

```bash
python3 /skills/weekly-skill/scripts/run.py --case /input/case.json --task /input/task.md --data /app/work/analysis_input.json --input-root /input --resources environment/resources.yaml
```

首次示意主结果节选：`{"case_id":"demo-tracker","status":"completed","results":[{"identity":"id:p2","event_type":"new_to_snapshot"}],"uncertainties":["not a delivery receipt"]}`。相同工作目录再次运行应为零新增事件、零拟提醒且水位不变。此处日期、记录和事件均为接口结构演示。

## 自查与维护

1. 先连续跑两个相邻窗，再原窗重跑：提醒不重复、水位不倒退、ledger 不因无效整理漂移；中途中断后从已保存状态恢复。
2. 每条 event 核对旧快照、时间字段、时区、去重键及来源，不能用出版年偷换入库日，不能用 retraction 猜测当前状态。
3. 维护时测空窗、边界刚好等于起止、延迟入库、日期缺失、时区错误、同文多源、版本更新、失败重试和暂停。
4. 新增提醒字段或事件类型后同步修改持久状态兼容逻辑，并用同窗口两次执行验证字节级幂等；无真实通道不得增加 delivered 字样。
5. 只提交 Skill 和脚本，不附真实提醒、状态历史或案例结果。无真实 GitHub URL 时正式提交该项阻塞且不创建 `GITHUB_URL.txt`；不发布、不提交、不处理令牌。预算为 64 请求、900 秒、每响应含推理 16384 tokens。
