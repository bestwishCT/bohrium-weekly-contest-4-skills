---
name: organize-audience-ready-talk
description: Organize existing research into an audience- and time-specific native PPTX with explainable content selection, one traceable claim per slide, calibrated conclusions, complementary speaker notes, sourced Q&A and real backup slides, then render the final deck to PDF/PNG. Use for lab meetings, defenses, reviews and adapting a talk's goal, audience, timing or questions; not for inventing research findings or mere visual template conversion.
---

# Audience-ready academic talk organization

把“这次汇报要让谁理解/判断什么”转换为内容选择、叙事节奏和有边界的问答。不是论文压缩工具、语言润色器或模板美化器；必须生成真实 deck，包含允许的主页面与备用页。

## 触发、事实源与交付合同

当用户指定组会/答辩/评审等场景、目标听众、时长、已有论文/研究材料，要求“重新组织汇报”“准备讲稿和问答”“取舍并安排备用页”时触发。若只是局部 PPT 操作或纯母版风格适配，改用对应专项技能；不在材料之外创建新的科研结论。

逐例完整读取 `/input/case.json`、`/input/task.md`、`environment/resources.yaml`，以及实际 PDF、数据表、图表、备注、模板和任务提供时的 actual starting deck。PDF 需实际提取/渲染/OCR，按物理页和原文核验，不期待预提取答案层。将候选池、决策、讲稿和检查点放 `/app/work`；最终 PPTX、PDF/逐页 PNG、取舍记录、逐页目标—主张—来源—时间映射、问答来源及 `result.json` 放 `/app/output`。

从当前 case 读取并锁定：`case_id`、原始 `output_contract`、`audience_profile`、`goal`、`time_minutes`、`slide_count`、备用页是否计入页数、模板/起始稿、全部 full-source/uncertainty 约定。不得用示例的青众、6 分钟、5 页等示意数值替代。`audience_profile` 至少应有实际 `role`、`prior_knowledge`、`goal`。原始 output_contract 的全部字段/层级/类型和 case_id 必须原样保留，只填对应值，不自创字段。

## 听众驱动的内容组织

1. **报告合同**：写成可检查句：“在固定时间内，使[实际青众]能基于[实际证据]判断[实际目标]”。列出青众已有概念、需要的定义、可能质疑。组会重方法与进度、答辩重贡献与边界、评审重证据强度仅是可选启发；以 case 为准，不擅自换场景。
2. **候选内容池**：为每个候选分配稳定 `id`、短文本、证据类型/强度、PDF 页/段/图表、精确 quote、与青众的相关性。区分已有结果、他人研究、计划中工作和未决问题。不能先编页面再倒找证据。
3. **明确取舍**：每个候选只能 `main`、`notes`、`backup` 或 `drop`，写清与青众/目标相关的 `reason`。不能处于 drop 又被偷塞页面。背景只留理解下一主张所需；细节去备注或允许的备用页；无证据事项标不可回答而不是升格为结论。
4. **按问题重排而非目录搬运**：为每页写 `audience_question`，然后安排 opening/problem→必要 method→按因果/支撑关系排序的 evidence→synthesis/limitations→case 允许的 qa-backup。检查上一页为何导向下一页，不能因源论文 Results 顺序而机械排序。
5. **唯一主张与证据强度**：每页 claim 只回答一个青众问题，提供最低必要 points 与绑定候选。观察结果用“观察到/报告”，实验设计支持才用相应因果措辞；不写来源没有的“显著”“机制已证实”。数字、单位、公式和图表编号精确一致。
6. **备注与节奏**：每页 `seconds` 按固定总时长预算，备注含 `transition`、`emphasis`、`backup`，补充术语/方法理由/额外原文证据，不复制可见文字。过长备注按其时间重新分配内容，不在交付前随意删页。
7. **源图/图表**：优先真实源图；有结构化数据才用 `native chart`，类别、系列、数值、单位、图注逐项来自指定源。作曲家只接受有 figure 编号的数据 chart，不为装饰编造曲线。图像等比不裁轴/图例。
8. **问答预演**：预测青众可能问的设计、对照、机制、数据、统计和边界；每题给 `question`、与页面同一口径的 `answer`、`answer_boundary`、`source PDF页+quote` 和现有 `page`。被引用页必须在 `qa_ids` 链接该题；Q&A 写入原生备注。源材料不支持时明确“现有材料无法回答”及下一步需要何材料，不能编写假答案。

## 将决策落实为可编辑 deck

先逐页读取实际 `master/layout`、placeholder 与主题字体；`page.layout` 必须匹配当前真实 layout。若采用合成回归的 `title_font_pt: 28`，必须已确认它符合实际模板的可读层级，正式 case 另有指定时从 case，不能缩小封面或内容标题来掩盖信息取舍失败。标题槽高度不足 100pt 且解析字号超过 36pt 时，只把该槽字号限制为 36pt 并记录原字号，保持模板几何与更大的标题样式；仍须实际渲染复核。

`compose_talk.py` 先校验青众字段、候选去留、唯一 claim、必需 opening/evidence/synthesis、页数总时间、问答与页面互链，再生成。缺一个必需环节即失败，不输出“看似完成”的空逻辑 deck。

- `mode: from-template` 只用于真实零示例页模板；按实际 layout 名创建页面。
- `mode: existing-deck-edit` 要求 actual starting deck、真实 `original_sha256` 和固定页数，直接在该稿副本中组织，不从默认 Presentation 重建 `deck` 路径为实际模板或起始稿而非匿名资源。
- 原有页面在改变内容前记录所有对象、备注和授权范围；`page.layout` 必须是当前页实际 layout。保留不需要改变的媒体/备注，缺槽不私改母版。保存前后检查 master/theme 哈希、固定页数、原生可编辑对象及逐页备注。
- 最终备用页只有在 case 明确允许且已计入 slide_count 才生成 `qa-backup`。不得把额外问答清单当备用页；所有允许页真实存在于 deck。
- 用临时文件保存，重开通过后才替换最终 `organized_talk.pptx`，原始留作 `organization_baseline.pptx`。失败清晰记录候选、页面和缺失证据，保留基线。

## 调用和真实渲染

技能安装根目录为 `/skills/weekly-skill`，资源按该目录相对路径引用。只需自包含文本脚本，不依赖个人模板。先依据 resources.yaml 检查可用 Python、库、Office renderer、字体及版本；生成要求 `python-pptx>=1.0`，页检要求 PyMuPDF、Pillow。默认用 LibreOffice/soffice 导出；没有 soffice 时可检查已真实渲染的外部 PDF，但必须同时提供 `--external-pdf` 与 `--external-receipt`。

```bash
export SKILL_DIR=/skills/weekly-skill
python3 "$SKILL_DIR/scripts/compose_talk.py" \
  --spec /app/work/talk_plan.json --outdir /app/output
python3 "$SKILL_DIR/scripts/render_and_check.py" \
  --pptx /app/output/organized_talk.pptx --outdir /app/output \
  --work /app/work/render
```

渲染脚本从最终 PPTX 用 LibreOffice 导 PDF、逐页 PNG/contact sheet，检测页面数、文字/媒体框及图片/图表槽位实际内容和明显溢出，并记录全部哈希。外部 PDF 回执必须含 renderer 名称/版本、完整命令或实际 tool invocation、退出码 0、非空执行日志/回执，以及与当前文件完全一致的 PPTX/PDF provenance SHA-256；输入 PDF 不在验证前删除，失败不登记为成品。工具或有效证据缺失时非零退出、报告 `not-passed`，不得造 PDF/PNG 或以 HTML 拼版冒充。

逐页实际查看最终 PDF/PNG：主线与页序是否服务青众、claim 是否超过证据、数字/图号/公式是否正确、备注是否互补并按时、问答是否能回到原页与来源、允许备用页是否真实、master/页脚/字体/图片裁切是否保持。自动通过不认证科学质量或全面视觉质量；只有 `--visual-review` 指向与当前 PPTX/PDF/逐页 PNG 哈希绑定、逐页 `actual_image_read: true` 且观察记录非空的 JSON，并且无其他待审项时，契约才可 `completed`。OCR、字体和科学证据待审必须保留。

## 最小非官方示例

`examples/talk_plan.minimal.json` 是面向 SYNTHETIC methods group 的虚构组织案例，含 main/notes/backup/drop 决策、5 页时间分配和三项有来源问答。复制到 `/app/work/talk_plan.json` 后，以真实合成起始稿 hash 替换示意 hash，并把具体 case 的青众/时长/页数/备用页规则与 output_contract 覆盖示例。不能将示例 claim 当真实论文结论。

预期实际输出是含原生图和原生备注的可编辑 PPTX、完整 choices/qa JSON，以及逐页渲染件（工具存在时）。维护验证的命令、退出码、fixture、断言与预览只存 verification，不进 submission，也不是正式案例答案。

## 失败恢复

- 原文缺失/OCR 不全：保存已读候选，标未读页和无法回答的问题，不补造论证。
- 内容太多：按青众目标解释性降级到 notes/backup/drop，不能简单缩字或超页；保留 opening、关键 evidence 和 synthesis。
- 结论强度不足：降写成相关性/描述或待定，不把潜在问答写成新发现。
- 起始稿 hash/layout/master 冲突：拒绝覆盖未知版本，重新读取或报告需真实模板；保留基线与已完成组织表。
- 低分辨率图/公式乱码/缺字体：使用真实源或声明降级，检查替换影响，不伪装嵌入。
- 渲染失败：保留可编辑成稿与最近验证版、逐页问题清单，状态 partial；修好当前稿后重新生成全套预览，不能使用旧 PNG。

## 自查与维护

逐项确认固定青众/目标/时长/页数与备用页规则、full-source/uncertainty、所有候选去向、唯一主张、讲稿时间/过渡、Q&A 答案边界和源定位、原生可编辑与真实渲染、最终契约字段类型。改变证据/页序后联动重算时间、问答映射和预览。

维护新增青众或材料类型时，用合成片段测试候选 drop 未被引用、QA 页链接必验、总时长不得超和实际回退。记录实测 Python、`python-pptx`、renderer、PDF 库、模板及字体版本，不把自动断言字样当人工观察。资源无符号链接、无不必要正文文件。没有真实可匿名打开的 GitHub URL 就不建 `GITHUB_URL.txt`；不执行提交、PR、发布或令牌操作。
