---
name: chart-read
description: Inspect the actual scientific PNG and every supplied source table, calibrate linear/log axes from explicit tick anchors, digitize identified points with units and residual evidence, and export readback-tested CSV. Use for chart data extraction, multi-subfigure calibration, pixel-to-data conversion, or chart/table provenance audits without fabricating missing pixels.
---

# 科研图表标定与原表交叉核验

## 触发与边界

用户给出科研图片及原表，要求轴/单位识别、曲线点数字化、子图关联、图表冲突记录时使用。严格执行输入修订 v2：每例处理实际 1 张原图片及全部原表；只有图注没有像素的对象只列缺口。脚本不通过光谱外形识别物质，不把视觉差异改称统计显著性。

版本 `1.0.0`；Python 3.9+ 标准库，已在 Python 3.9.6 实测。PNG 用标准库读取并逐块验 CRC；CSV 用 `csv` 实际读写。无 OCR、Pillow 或外部网络依赖。JPEG、显微目标自动分割、曲线自动追踪和图像 OCR 不在已实测范围。

## 材料、相对资源与输出

读取 `/input/case.json`、`task.md`、实际 `image_path`、全部 `table_paths` 和 `environment/resources.yaml`。Agent 必须通过可用图像查看器直接看图，记录子图、图例、系列和可见刻度；`calibration` 中的点必须来自这次观察，不得由领域标签生成。

过程在 `/app/work`，结果和 `digitized.csv` 在 `/app/output`。Skill 位于 `/skills/weekly-skill`，资源相对引用 `scripts/common.py`、`scripts/workflow.py`。输入 JSON 同时给出 x/y 轴 `scale/unit/anchors` 与 `points[]`（含真实像素、系列、子图）。

`output_contract` 逐层逐键适配，普通键与 JSON Schema 均支持，保留 `case_id`；不额外制造隐藏字段。业务结果为 `image`、`axes`、`points`、`tables`、`conflicts`、`sources`、`exports`，合同别名 `digitized_data/chart_data/result` 可映射 `points`。

## 标定工作流

1. 对 PNG 验签名、IHDR、IEND 和每个 chunk CRC，记录宽高、字节数、SHA-256；缺像素、裁剪、过曝等另记，不把读取失败当科学零变化。
2. 对全部原表分别用 CSV 解析器读取，保留表头、原始单元格、行数和哈希。图片与表冲突写双方位置，不平均或覆盖。
3. 每轴至少两个真实刻度锚点，区分 `linear/log10`。求解像素到数据的映射；对数值必须为正，记录变换单位、全部锚点及最大绝对残差。
4. Agent 从原图确定标记中心像素和系列/子图，脚本计算 x/y 和导出 CSV。导数标签为 `inferred_from_calibrated_pixels`，区分“直接读图坐标”和“由标定推导值”，不把双精度格式当仪器精度。
5. CSV 写出后由 `csv.DictReader` 复读并核对行数。逐项检查系列、子图、单位前缀、对数变换、像素证据和残差。
6. 图注提到但没有图片的对象进入 `uncertainty`，不生成伪坐标；误差线/显著性只有在图注或原表明确时由 Agent 加入实际合同字段。

## 状态与失败恢复

- `success`：真实图片、全部表、轴标定点和 CSV 复读通过。
- `partial`/退出 2：图片缺失/损坏、表缺失、刻度少于两点、对数值非正、未识别点或导出对账失败；保留已完成表和错误阶段。
- 对无刻度、图例缺失、曲线重叠，Agent 在 candidate 中减少声明范围并向用户指出子图坐标，不猜系列或补 0。
- `trace.json` 保存真实版本、失败阶段、变更与不确定性，不生成伪 DSH 对照或准确率。

若只是 JSON/CSV 不要求图形预览；若另产 Office/图形，必须实际渲染并打开。无工具时说明不能预览。

## 最小示例（合成、非正式）

输入：真实 2×2 PNG；`table1.csv` 一列 `group,value`；x 锚点 `(0,0),(100,10)` 秒，y 锚点 `(0,0),(100,20)` mV，点 `A` 位于 `(50,40)`。

调用：

```bash
python3 /skills/weekly-skill/scripts/workflow.py --case /app/work/chart-case.json \
  --task /app/work/task.md --input-dir /app/work --work /app/work/run --output /app/output
```

示意输出：点的 `x=5.0,x_unit=s,y=8.0,y_unit=mV`，证据仍保留 `[50,40]` 像素；`digitized.csv` 复读 1 行。该数只用于合成回归，不是正式论文读数。

## 自查、维护与发布

复核 v2 材料完整性、每轴锚点/残差、图属性和表哈希、冲突及 CSV 解析。新增图型先加真实合成图片与缺轴/坏 CRC 回归，修改标定公式必须升版并记录影响范围。

最新 GitHub 加分仅在提供真实、免登录可访问 URL 后处理；无 URL 不创建 `GITHUB_URL.txt`，不联网发布。
