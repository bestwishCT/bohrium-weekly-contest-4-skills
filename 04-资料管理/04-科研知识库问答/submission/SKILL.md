---
name: answer-with-kb-evidence
description: 在给定科研知识库内完成多文件检索、原子论断取证、逐字引用回链及缺证/冲突/权限分级问答。适合actual9document修复后的资料问答、互补证据综合、重复版本辨析和“没有足够依据”判定；不用于自由知识填空、开放网络答案或把检索候选直接当事实。
---

# 证据约束的科研知识库问答

## 触发与不可突破的边界

用户提供知识库并提问，要求多文件综合、引用、回链或不确定性判断时使用。回答只能来自允许范围；库外知识只有task明示允许才可用，且必须与库内证据分列。默认不用通用常识补空。重点不是召回数量，而是每个原子论断的支持关系和原文坐标。

## 真实case/task/output_contract门禁

1. 完整读取`/input/case.json`与`/input/task.md`，逐字读取实际`output_contract`。它是唯一最终JSON结构依据，不能把本技能的`answer/claims/evidence`辅助名当成官方新字段。
2. 盘点目录、文件格式、哈希、权限、版本、重复副本、别名、元数据来源以及已有恢复/索引状态。实际PDF必须用真实工具读取；无文本扫描件只有真实OCR成功才能纳入，不可读不能当作“没有相关内容”。
3. 解析用户问题与子问题，固定时间、版本、目录及权限范围。硬条件不静默放宽；历史版本不默认最新优先，相同哈希副本不增加支持数。
4. 中间索引和候选包放`/app/work`，最终回答及`result.json`放`/app/output`。先保存“缺证/待审核”的部分结果，再改善答案；遵守64请求、900秒和16384 tokens预算。

## 安装与依赖实测

安装根为`/skills/weekly-skill`，资源相对根只有`scripts/evidence_qa.py`。运行时可设`SKILL=/skills/weekly-skill`，禁止改为作者路径，设置`PYTHONDONTWRITEBYTECODE=1`避免缓存。

- 原创`evidence_qa.py`版本1.0.0，只用Python标准库，来源为本提交源码；实测`Python 3.9.6`，可运行于3.9+，没有第三方安装步骤。
- 原生支持UTF-8 `.txt/.md/.json/.csv/.xml/.html`和DOCX XML正文。PDF/OCR需案例配置中的真实`extracted_text/parser`，抽取文件由实测工具写入`/app/work`，并保留页面标记。
- 本机未发现`environment/resources.yaml`，所以PDF、OCR、双栏与扫描定位未在本机验证。正式运行先读取该文件并实测声明工具；失败则报告未覆盖范围。

## 专属两阶段问答流程

### 阶段A：检索“未判定候选”

配置`/app/work/kb-config.json`的documents元数据及来自案例/用户的synonyms。执行：

```bash
python3 "$SKILL/scripts/evidence_qa.py" retrieve \
  --library /app/work/kb --question "What buffer and pH were used?" \
  --config /app/work/kb-config.json --filter version=v2 \
  --case /input/case.json --task /input/task.md \
  --work /app/work --output /app/output
```

程序以BM25和所声明同义扩展召回，候选带文件、哈希、版本、章节、物理/印刷页、段落、逐字段落及定位串。所有候选始终标`candidate_unjudged`，BM25得分不是答案置信概率。此阶段`preliminary_answer`只能是“待审核”或“当前知识库没有足够依据”，不可直接把候选拼成事实。

空库/全不可读是`no_readable_documents_or_empty_library`，有文件但无候选是`no_candidates_current_kb_does_not_provide_sufficient_basis`。后者仍需检查范围、解析和召回，不能证明文献没有答案。候选包含真实查询、过滤排除、失败、别名与独立版本计数，可复跑。

### 阶段B：逐原子论断审核

Agent必须读取候选周围的真实原文，把拟答内容拆成最小可判定论断，写入`/app/work/claims.json`：

```json
{"claims":[{"id":"c1","text":"The buffer contained 20 mM Tris.","support":[{"candidate_id":"从实际packet复制","relation":"direct","quote":"The buffer contained 20 mM Tris."}]}]}
```

`relation`语义：direct=原文直接支持整个论断；partial=只支持较弱限定版本；contradicts=原文与该论断反向冲突。仅在Agent实际完成语义对齐后标注，脚本不能也不假装自动证明语义。partial论断另写`qualified_text`降低强度，未写则不输出实质论断。

执行：

```bash
python3 "$SKILL/scripts/evidence_qa.py" answer \
  --packet /app/work/evidence_packet.json --claims /app/work/claims.json \
  --output /app/output --case /input/case.json
```

脚本验证candidate ID存在、关系合法且quote是候选原文的逐字子串，再复制真实文件/版本/页段坐标到claim audit。未知ID或伪造引文使该论断降为insufficient、整体`validation_failed`并返回2，同时保留审计，不得私自“修正”来源后宣称通过。

### 论断状态与答案渲染

- **supported**：direct有效且无contradicts，才输出原论断，并紧邻证据定位。
- **partial**：只有部分支持时，最多输出`qualified_text`；其他部分写无法确定。
- **conflict**：有contradicts或直接与反证并存，明确说无法形成统一结论，并列双方文件、版本和原文；不按多数或新旧投票。
- **insufficient**：没有有效直接/部分证据时，固定写“当前知识库没有足够依据”，列出已查范围和需要补充的文件/版本/段落，不输出无证据claim。

综合多文件的有限归纳必须在论断中标为归纳并分别取证，不把推断伪装成直接引文。脚本`/answer.md`把每个状态、支持关系和locator放在答案后，Agent需再次按坐标打开原件复核；仅定位串不是已验证可点击链接。

## 输出、来源、状态与变更

`evidence_packet.json`与`retrieval_report.json`记录检索范围；`answer.json/answer.md`记录最终直接回答、逐论断审计、冲突、失败、不确定性和审计输入哈希。`result.json`必须含`case_id`及实际契约的所有原字段，适配器提供的空默认需要Agent替换为真实结果，再检查类型、层级、必填与枚举。

最终检查六类资料要素：文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源分别有结果或不适用/未检查原因。保持来源、状态、不确定性、人工改动和索引/回答变更可复核。普通Markdown/JSON无需渲染预览。

## 异常、恢复与维护

- 空目录或全无权限：停止实质回答，不能推导“该结论不存在”。烟测用`--simulate-denied 相对路径`而非改权限位。
- 文件不可读：记录受影响问题；仍有可用材料时仅回答被支持部分。
- 路径或索引失效：重新计算源哈希，从retrieve恢复；候选/claims仍保留，防止凭记忆重建。
- 工具中断：保留索引、候选和已验证claims；从最后一个完整JSON阶段续跑。
- 修改抽取、召回或证据校验后，以合成材料复测多文件互补、重复别名、版本冲突、伪造quote拒绝、空库和无答案，并检查再次运行的一致性。

## 最小非官方示例（非正式案例）

合成`A.md`写`The buffer contained 20 mM Tris.`，`B.md`写`The measured pH was 8.0.`，配置两者为v2。retrieve问题为“buffer and pH”，随后建立两个原子claims，各自只引用对应实际candidate ID。answer示意状态中两个claim均为supported，直接答案为两句原文事实，证据指向两个独立文件；无依据的“孵化24小时”claim必须显示insufficient。示例不是平台答案或金标准。

无真实公开GitHub链接不得创建`GITHUB_URL.txt`。检查50个UTF-8文件、单500KB、总2MB、无符号链接；不自动提交、PR、发布或处理令牌。
