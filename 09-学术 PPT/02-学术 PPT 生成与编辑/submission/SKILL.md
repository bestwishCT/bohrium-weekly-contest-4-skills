---
name: edit-academic-slide-versions
description: Generate academic slides inside an actual template or perform guarded local edits on an existing PPTX, inserting data-bound native charts, preserving unauthorized pages and masters, recording slide diffs, and testing hash-guarded rollback before real PDF/PNG rendering. Use for slide creation, compression, audience/language edits, figure insertion, iterative revisions, or rollback; not for source reconstruction from plain outlines alone.
---

# Academic PPT generation and version-safe editing

核心对象是“实际 PPTX 的一次可回退编辑事务”，不是论文阅读结论生成器，也不是一次性换皮。用实际源材料决定内容，用页面指纹限定修改范围，用最终文件渲染决定可否交付。

## 触发与案例合同

适用于“新建学术 deck”“把第 N 页压缩/扩写”“换听众或语言但不动事实”“把有数据的图插入指定页”“保留上一版并比较”“撤销刚才那次修改”。若任务是整篇论文的证据主线重构或只做全册母版适配，优先采用相应专项流程；不能忽略实际起始稿从零替换。

逐例完整读取 `/input/case.json`、`/input/task.md`、`environment/resources.yaml`、真实 PDF/数据表/图注、模板及 actual starting deck。实际解析原 PDF，不能根据标题或示例猜科学结论。所有临时状态、页级 diff、候选稿和日志放 `/app/work`，最终 PPTX、PDF/逐页 PNG、备注、来源映射、diff、版本记录和 `result.json` 放 `/app/output`。

读取 case 固定的 `case_id`、`output_contract`、`audience`、`time_minutes`、`slide_count`、页序、编辑范围以及 full-source/uncertainty 条款。不能沿用示例数值或擅自增加备用页。将 `output_contract` 原字段、嵌套及类型物化为待填对象，最后保留所有键并写 `case_id`，不增加契约外字段。缺真实模板/起始稿、固定页数或来源条款时先阻塞，不自行补造。

## 先建立可回退基线

1. 复制源 deck 到 `/app/work/original.pptx`，计算文件 SHA-256；检查页数、每页标题/正文/备注/图片/原生图表、master/layout、theme、页脚和对象几何。
2. 运行 `inspect` 得到每页结构化状态及 `expected_slide_fingerprint`。同一文件发生外部改动时必须重新读取，禁止重放旧指纹。
3. 把任务归类：`from-template` 或 `existing-deck-edit`。前者只用任务提供的真实模板且**零示例页**；若 case 给了实际起始稿就必须选后者。整稿生成用顺序 `append_page`；局部编辑用 `replace_title`、`replace_body`、`replace_notes`、`replace_page`、`set_text_style`。不会自动删除整页或修改 master。
4. 每个操作记录唯一 `operation_id`、`slide`、目标页当前指纹、`reason` 和修改内容。目标页错配时停止整批事务，不跳过失败继续制造混合版本。

## 页级内容、图表与科学一致性

- 先列“请求允许改变的事实/措辞/对象”，再动页面。换受众改解释深度，换语言改表达，术语、符号、图号、数值、单位、结论强度不得变化。未授权页面、备注、媒体和 master/theme 必须保留。
- 为最终每页建立 `page_evidence`：页号、叙事角色、唯一 claim、可见事实、源 PDF 页/数据表单元格、精确短引、图号、seconds 和不确定性；包括未编辑页。不得用示例研究内容填充。
- `replace_page` 的标题只承载一个主张，points 放最少必要支撑。超框先缩短文字、减少同页主张或选源模板已有双内容 layout；不得把全段缩成不可读小字。
- 对 native chart：从有来源的结构化数值写 `categories` 和每个 `series.name/values`，逐项对齐图例、类别、单位、图注和 x/y 轴。脚本生成 PowerPoint chart + 内嵌 workbook，数值可继续编辑。位图没有结构化来源时引用真实源图并明确不可重绘，不能以“示意曲线”补缺。
- 原生图片按容器等比 fit，默认裁切为零；检查原图号、轴、图例和单位。需要裁切必须任务明确允许，并保留未改源文件。
- 备注按页面角色写 `transition`、`emphasis`、`backup`（额外来源或限制），不重复可见文案。整稿最终 `page_evidence[].seconds` 必须覆盖固定讲述预算且不超过 case 时间。
- 模板只用实际 layout 名和占位符 idx。`replace_page` 的有图页默认 idx=2 图槽；缺槽时失败并选择实际 layout/报告，不覆盖母版。编辑字体只允许 14–54pt 的目标形状微调，先遵从模板主题，不用匿名默认样式。

## 确定性调用

技能安装在 `/skills/weekly-skill`，资源一律从该根目录相对定位。先以 `resources.yaml` 校验环境中真实 Python、库、原生 Office 和字体；工具不存在就记录阻塞，不虚称可用。需要 `python-pptx>=1.0`，渲染另需 LibreOffice/soffice、PyMuPDF、Pillow；安装只能按该环境说明执行。

```bash
export SKILL_DIR=/skills/weekly-skill
python3 "$SKILL_DIR/scripts/edit_deck.py" inspect \
  --deck /app/work/original.pptx --out /app/work/baseline.json
python3 "$SKILL_DIR/scripts/edit_deck.py" apply \
  --spec /app/work/edit_plan.json --outdir /app/output
python3 "$SKILL_DIR/scripts/render_and_check.py" \
  --pptx /app/output/edited_deck.pptx --outdir /app/output \
  --work /app/work/render
```

`original_sha256` 来自实际基线，目标页指纹来自 `baseline.json`，不能伪造。候选稿先写临时文件，重开后比较固定页数、master/theme 摘要和所有未授权页；只有全部通过才原子替换 `edited_deck.pptx`，基线保留为 `previous_verified.pptx`。过程 stdout 及退出码、diff 和候选异常存入工作记录。

实际测试回退（`expected-current-sha256` 必须是当前编辑版真实哈希）：

```bash
python3 "$SKILL_DIR/scripts/edit_deck.py" rollback \
  --current /app/output/edited_deck.pptx \
  --backup /app/output/previous_verified.pptx \
  --expected-current-sha256 '<当前文件真实SHA-256>' \
  --outdir /app/work/rollback
```

回退生成独立 `rolled_back.pptx` 和报告，再重开比较基线，不覆盖用户后来产生的未知文件。恢复正式编辑版时必须明确选择版本并重跑渲染；不凭文件名宣称当前版。

`render_and_check.py` 只接受最终 PPTX，用真实 LibreOffice 转 PDF，再生成每页 PNG、contact sheet 及报告，检查页数/比例、渲染文本缺失/越界和源图裁切。若环境提供其他原生 renderer，按真实版本调用，保持相同检查；禁止将 HTML 拼版当 PPT 导出。无 renderer 则退出非零、`status:not-passed`、不造预览，输出状态为 partial。

逐页查看最终 PDF/PNG，核对新旧页序、修改点、字体替换、项目符号、公式、图表数值/单位/裁切、备注主题和未授权页。`structure-passed` 与 `automated-render-passed` 均不等于科学内容或视觉人工核验通过；实看后才能记录视觉结论。更新真实渲染信息到原始 output_contract 对应字段，不能遗留 pending。

## 最小非官方示例

`examples/edit_plan.minimal.json` 描述对 SYNTHETIC 起始稿第二页的数据绑定修改，并以原青第三页作为不得改变的对照。先复制到 `/app/work/edit_plan.json`，用 `inspect` 的真实结果替换原文件哈希和第二页指纹，再以 case 原始约束覆盖示意 audience/time/slide_count 与 output_contract。示例只可供独立工具链冒烟，不是固定案例答案。

执行后应真实得到可打开的第二页 native chart、`slide_diff.json`、未改的第一/三页指纹和可执行回退文件；再对编辑版实际导出/逐页检视。示意输入、退出码与预览保留在维护侧 verification，绝不混进 submission。

## 失败恢复

- 起始文件或页指纹变：停止，重新 inspect 后重新生成唯一操作；不覆盖未知版本。
- 任一编辑或重开失败：丢弃 candidate，保留 original/previous_verified 和错误页；最终状态不当 completed。
- 数值来源缺失、位图不可取数：拒绝创建假图表，引用真实源图或保留限制；所有 source/uncertainty 键按原契约填写。
- 字体缺失或图片拉伸：只改授权形状并记录替换/版面影响，必要时退回上一版；不宣称嵌入字体。
- 溢出或渲染失败：回到最近已渲染版本，小步修改后重跑整册检查；保留坏候选诊断而不当正式交付。
- 耗时逼近上限：优先保存哈希、基线、已验证局部版本和页级 diff，再恢复；绝不省掉未授权页和 master 检查。

## 自查与维护

核对 case 数值/条款、作用域、源数据与单位、最终页数/时间、全部非目标页及备注媒体是否保持、diff 是否真实、回退是否实测、渲染是否来自当前最终 PPTX。修改生成动作时分别以 from-template 和起始稿局部编辑回归，另测指纹冲突应失败、回退应恢复；不得烘焙固定案例内容或评分。

维护时记录实测 Python、`python-pptx`、renderer、PDF 库和字体/模板版本，预览随 PPTX 哈希失效。保持自包含资源、无符号链接与体积限制。无真实可匿名打开的 GitHub URL 就不建 `GITHUB_URL.txt`；维护流程不执行提交、PR、发布或令牌操作。
