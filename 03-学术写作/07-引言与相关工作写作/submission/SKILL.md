---
name: draft-grounded-introduction
description: "依据真实论文及文献起草引言与相关工作，按研究路线比较、从有定位的局限推导空白，并核验论断级引用及贡献与论文证据的对应。"
---

# 证据链驱动的引言与相关工作

## 何时使用与交付边界
用于“写引言”“整理相关工作”“从文献局限推导研究空白”“补引用”“核对贡献”等任务。核心成品是按实际 case 组织的**研究背景、相关工作、研究空白、贡献及论断级引用**，而不是提纲、逐篇摘要、评分自述或审计表。纯问答、无来源的文献推荐不套用本技能。

不得从错误学科标签、文件名或训练记忆补造研究方法、实验结果、作者、年份、来源、DOI或“代表性/最新/首次/尚无研究”等身份。未来计划不得写成已完成。审计脚本只检查机械约束，不能替代逐篇阅读原文的语义判断。

## 实际运行顺序

### 1. 保留真实输入和原契约，先落盘 partial
先读 `/input/case.json`、`/input/task.md`、`environment/resources.yaml` 及 case 指向的实际论文、图表、结果和文献。立刻执行：

```bash
python3 /skills/weekly-skill/scripts/contract.py init \
  --case /input/case.json --task /input/task.md \
  --work-dir /app/work --output-dir /app/output
```

`contract.py` 只读取 case 显式 `materials` 中的 `path/file_path/source_path`；实读后发现的补充材料用可重复的 `--material 真实路径` 显式加入，不猜材料布局。它记录真实路径、可读性、字节数与 SHA-256，并在 `/app/output/result.json` 先写诚实 `partial`。无法读取、语义无法判断、工具/权限失败分别记录；已有真实产物不因后一步失败而删除。

`case_id` 与原 `output_contract` 只来自当前 case。原契约逐字深拷贝到 `/app/work/input_snapshot.json` 的 `original_output_contract`，包括嵌套 `properties/required/type/enum/items`；公开 result 填 `case_id` 和原合同声明的全部顶层字段，嵌套对象递归保留 required 字段。不能把内部模板字段复制成公开字段，不能改写 enum 或任选枚举值冒充事实。数值、布尔值和无安全状态枚举在未知时保持 null/空值并列入缺口，不默认写 0、false 或第一项。只有原合同允许时才把状态写为 partial。

### 2. 建材料与证据底稿
在 `/app/work` 保存材料索引、稳定 ID、分块状态和真实抽取文本：
- **本文证据**：研究问题、实际执行的方法、数据、图表/实验结果、结论边界。`paper_evidence_records` 的每个锚点同时保存实际位置和原文/数值，不能只写“Fig. 2”。
- **文献卡片**：作者、年份、来源、问题设定、路线、数据/评价、结论、适用条件、局限和精确位置；标明 `full_text/abstract/metadata_only`。仅摘要或元数据可读时收窄转述并保留 partial，不能假装核验全文。
- **冲突**：不同来源的原文、位置和受影响论断并列保留；没有 case 明示的兼容优先级就不静默择一。

### 3. 从矩阵推导，再连续成稿
1. 以问题、方法或技术路线为主分组轴，在问题设定、方法取舍、数据与评价等同一维度比较；说明组际差异和本文定位。每篇主要文献进入比较组，而非按年份流水账。
2. 背景从具体技术约束和未解决矛盾出发；重要性来自真实来源，作者判断明确标识。
3. 每条空白保存 `文献ID＋局限原文位置 → 空白`，且空白句实际进入正文。不得把未读到的文献当作不存在。
4. 每条空白绑定本文证据和 `fully_addressed/partially_addressed/unverified`，把回应程度及边界写入正文；unverified 必须 partial。
5. 每条贡献绑定真实方法/图表/结果锚点及支持原文，只写证据支持的动作、价值和适用范围。
6. 每个关键论断保存原句、所在部分、实际引用标记、逐源位置和支持关系。核对主要路线、关键工作、材料可见的近期进展以及作者/年份/来源，引用格式按 case；覆盖不足不得编造补齐。
7. 四部分按 case 的语言、顺序、篇幅和格式连成可直接编辑的稿件，证据矩阵留在 work，不能替代正文。

内部台账字段见 `references/record-template.json`（仅为结构示意）。`sections` 保存四部分真实正文，`manuscript` 是包含这些正文的完整稿件，使审计能检查“映射存在”且“论断确实入稿”。

### 4. 审计真实草稿并递归检查交付合同
```bash
python3 /skills/weekly-skill/scripts/audit.py /app/work/audit_input.json
python3 /skills/weekly-skill/scripts/contract.py check \
  --case /input/case.json --result /app/output/result.json \
  --snapshot /app/work/input_snapshot.json
```

`audit.py` 检查分节正文、元数据/证据层级、分组轴与覆盖、空白来源及回应程度、贡献原文锚点、论断—引用—具体来源位置。确定错误返回 `fail`，材料/定位不足或待验证返回 `partial`；两者均非零，只有机械检查完整通过才是 `pass`。`contract.py check` 逐项校验真实 case_id、原顶层字段以及嵌套 required/type/enum/items；partial 或校验失败均非零。失败须回到具体材料修复并重跑，不能修改断言或合同掩盖问题。

按契约将最终可编辑正文、指定来源/修改记录、不确定性、缺口及真实状态写入 `/app/output`；中间卡片、审计输入输出留在 `/app/work`。只有 case 明确要求时才生成 Office 等附件及真实 PDF/PNG 预览；工具不可用写 blocked/partial，不伪造文件。

## 预算、可移植性与异常
- 预算：64 次模型请求、900 秒、每响应含推理 16384 tokens。先材料索引和 partial，再分块卡片/矩阵、分节正文和局部回检；使用稳定 ID，不反复通读全文。未执行操作写未执行。
- 路径：技能安装于 `/skills/weekly-skill`，脚本仅 Python 3 标准库，无个人文件、网络检索或未声明依赖。运行时记录实际 `python3 --version`，不预称平台版本。
- 空文献库只能完成论文材料支持的部分；缺原文限制转述；期刊格式未知时使用 case 明示格式或保守统一并说明；语言与引用样式随 case，不用固定中文标题替代合同。
- 所有产物保留材料缺口、冲突、人工确认和修改差异。禁止捏造平台运行、基准对照、效率、分数、白板或官方轨迹。

## 最小合成示意与维护
**以下仅为非官方合成示意，不是正式案例或预写答案。** 构造含两条合成文献、一个共同局限、一个本文证据锚点的 `audit_input.json`，将论断原句和实际引用标记写入 `sections/manuscript`，运行 `audit.py`；删除某个 `source_locators` 应得到对应失败。该示意不得进入 `/app/output`。正式案例必须重新实读材料、执行上述 init、写作、审计和合同检查。

维护时新增独立合成 fixture 验证新学科/格式，不改旧断言迁就错误。脚本能验证结构和显式绑定，不能自动证明转述准确、空白在学界真实存在或贡献具有创新性；这些仍需逐条人工/Agent 回读真实证据。
