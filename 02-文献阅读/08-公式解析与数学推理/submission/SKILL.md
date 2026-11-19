---
name: math-check
description: Parse publisher and source-formula MathML into source-linked LaTeX for fractions, scripts, roots, fences and matrices, validate variable-definition provenance, and run guarded arithmetic substitution only with supplied values. Use for formula transcription, C01/C03 XML cross-checks, C02 missing-evidence branches, or numerical audits without reverse-engineering absent equations.
---

# MathML 结构恢复与受约束数值核验

## 触发与材料边界

用于论文 MathML 结构转 LaTeX、符号表、公式来源核验和数值代入。C01/C03 必须读取 `artifacts/publisher-source.xml` 及派生 `artifacts/source-formulas.xml`，保留公式节点和来源 ID；目录不是用户要求证明的结论。C02 缺公式、预测或原始数据时只核对明确指标，返回 `partial/insufficient_evidence`，不反推公式或填 0。

版本 `1.0.0`，Python 3.9+ 标准库 `ElementTree/ast`，Python 3.9.6 实测。已测分式、上下标、根式、定界符、矩阵和受限四则/幂表达式；不调用 SymPy，不承诺覆盖全部 Presentation MathML、LaTeX 渲染或科学单位自动量纲。未建模标签递归保留并发出 warning。

## 输入与合同

读取 `/input/case.json`、`task.md`、`environment/resources.yaml`、实际两个 XML 和可选 `material_path`。XML 用真实解析器读取，不通过文件名猜学科公式。

Agent 在 `/app/work/analysis.json` 写：

- `variables[]`：`symbol/definition/unit/range/paragraph_id/quote`，`definition_kind` 为 `reported/context_inference/common_knowledge/not_reported`；reported 必须原文回链。
- `evaluations[]`：表达式、非空 `inputs`、单位说明、expected 与事先绝对容差（如任务有）。只能字面数值和已给变量的加減乘除幂/一元正负，禁止函数、导入和任意执行。
- 若任务要求推导，另在合同允许处写每步依赖、公式 ID、假设/近似/边界；脚本不凭公式目录补推导。

安装 `/skills/weekly-skill`，相对资源 `scripts/common.py`、`scripts/workflow.py`。过程在 `/app/work`，结果在 `/app/output/result.json`。真实 `output_contract` 逐键/层级适配并保留 `case_id`，业务别名 `math/mathml_formulas/result` 对应 `formulas`。

## 专属流程

1. 用 ElementTree 遍历命名空间无关 math 节点，携带最近公式来源 ID/label，保留原 MathML 序列化、位置和 SHA-256。
2. 递归生成 LaTeX：`mfrac/msub/msup/msubsup/msqrt/mroot/mfenced/mtable` 及希腊字母；检查分式、脚本子节点数，不合并相邻变量。publisher 同 source ID 逐项交叉核对，缺 ID 明确报出。
3. 符号定义区分原文、上下文推断、常识补充和未报告；reported 定义 quote 必须存在，推断不得冒充实测。
4. 数值代入前检查实际数值、表达式和单位。安全 AST 白名单求值，幂指数限幅；缺值返回 insufficient_evidence，绝不自动设零。
5. 有 expected/tolerance 时报告实际差及是否在事前容差内，不能看到值后修改容差。数值计算通过不等于公式科学正确或单位一致。
6. C02 走缺失分支，记录哪些能做和不能做；C01/C03 检查 source/publisher 链、LaTeX warnings、变量证据和可复跑计算。

## 状态、输出和恢复

- `success`：公式恢复、变量来源和可选数值核验无失败。
- `partial/insufficient_evidence`/退出 2：无公式对象、缺数据、值计算失败或 publisher ID 缺失。保留已恢复 XML/LaTeX 和可人工接管的 formula ID。
- XML 损坏、模糊图片、歧义符号或单位缺失各自说明，不把它们写成“原文无公式”。
- `trace.json` 记录实际版本、MathML 子集边界和变更，不生成全研究复现声明。

本技能输出 JSON/XML 派生文本，不冒充公式渲染预览；合同若要图形/Office，须用真实渲染工具打开检查。

## 最小示例（合成、非正式）

两个 XML 均含 `<disp-formula id="eq1"><math><mfrac><mi>a</mi><mi>b</mi></mfrac></math></disp-formula>`；variables 无杜撰，evaluation `a/b`，inputs `a=4,b=2`。

```bash
python3 /skills/weekly-skill/scripts/workflow.py --case /app/work/formula-case.json \
  --task /app/work/task.md --input-dir /app/work --work /app/work/run --output /app/output \
  --candidate /app/work/analysis.json
```

示意输出：`formulas[0].latex="\frac{a}{b}"`、source_id=`eq1`、computed=`2.0`。C02 无 math 时输出 insufficient，不生成该式。

## 自查与维护

检查原节点/ID、分式和脚本层级、变量定义类别、未知输入与 AST 限制，JSON 复读。新增 MathML 标签必须用合成 XML 做结构回归并记录 warning/支持范围变化。

真实公开 GitHub URL 才可填新增加分文件；无 URL 不创建 `GITHUB_URL.txt`。
