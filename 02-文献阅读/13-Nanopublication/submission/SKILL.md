---
name: nano-export
description: Split source text into Agent-verified atomic assertions with controlled relations, conditions, direct/indirect evidence, real source metadata and confidence rationale, then export parser-readback-tested JSON and local N-Triples. Use for nanopublication drafting, evidence-bound assertion export, or assertion QA; never fabricates DOI, external URI, high confidence, or published TrustyURI status.
---

# 单断言、证据链与本地 Nanopublication 草稿导出

## 触发与边界

用于将论文结论拆成可独立判真假的断言，绑定最小充分证据和真实论文元数据，并输出机器可读结构。脚本导出的是 local evidence-bound assertion bundles 和 N-Triples，不宣称已发布 nanopublication、TrustyURI 或完整可信纳米出版协议。

版本 `1.0.0`，Python 3.9+ 标准库，Python 3.9.6 实测；没有 rdflib（本机不可用），因此只声明内置严格逐行 N-Triples 子集复读通过，不夸称全 RDF 生态/SPARQL 验证。无网络、DOI 生成或发布服务。

## 输入与合同

读取 `/input/case.json`、`task.md`、`environment/resources.yaml`、真实 `material_path` 及论文实际 `paper_id/authors/year/material_version`。Agent 在 `/app/work/assertions.json` 写：

- `subject/predicate/object/predicate_original_text/conditions`；
- `atomicity_confirmed=true`，一个断言只含一个可检验关系；
- `evidence_kind=direct|indirect`、`evidence[]={paragraph_id,quote}`；
- `confidence_basis` 文本，说明依据，不无依据给高置信数；
- 受控 predicate：`uses/supports/contradicts/compares_with/improves_over/reports`。

安装 `/skills/weekly-skill`，相对资源 `scripts/common.py`、`scripts/workflow.py`。工作文件在 `/app/work`，`nanopublications.json`、`assertions.nt` 与 `result.json` 在 `/app/output`。最终字段/序列化服从真实 `output_contract`，逐键嵌套适配且保留 `case_id`；`assertions/nanopubs/result` 映射 `nanopublications`，不自行把 JSON 改称合同要求的 RDF 格式。

## 构建流程

1. 从供应段落实别候选结论，拆分并列/因果/多重对象；保留样本、条件、时间/空间和“可能”等语气，过宽断言退回限制。
2. 映射受控关系并保留原文谓词，检查主体、关系方向和强度。不能凭主题相关创建实验关系。
3. 每断言选最小充分证据，direct 表示直接支持；综合推理标 indirect 并写边界。冲突断言并列，不静默去重。
4. 绑定真实 paper/authors/year/version；只生成确定性的 `urn:weekly-skill:*` 本地稳定标识，不伪造 DOI、作者或外部 URI。
5. 对 atomicity、relation、evidence_kind、条件、证据 quote、confidence_basis 做检查；缺证据状态 insufficient_evidence，不输出确定科学三元组。
6. 合法断言输出 bundle JSON 与 N-Triples，证 据节点保留 paragraph/quote。严格逐行解析 `<iri> <iri> <iri|"literal"> .` 并核对 triple 数；JSON 也可实际复读。
7. 检查重复、来源和导出字段。20 条示例若后续提供，必须另用合法公开材料制作，不能从隐藏案例预写。

## 状态和恢复

- `success`：存在合法断言且全部验证/导出复读通过。
- `partial`/退出 2：缺证据、非受控关系、无原子性确认或来源错引；合法断言仍导出，失败断言不进入科学三元组。
- `input_error`：材料/workflow 读取失败。扫描不可读、证据冲突与字段缺失分别说明。
- `trace.json` 记录实际 triple 数、未发布状态、变更与版本，不伪造 benchmark。
- 按合同保留不确定性：间接推论、条件歧义、来源冲突及置信依据不足均不得输出确定断言；待复核与序列化失败分开说明。

结构化导出以真实解析器验证；图形/Office 并非默认主产物，若生成必须真实渲染。

## 最小示例（合成、非正式）

p1=`Method M uses catalyst C under condition A.`；断言 subject=M、predicate=uses、object=C、conditions=A，direct evidence quote=`uses catalyst C under condition A`，confidence_basis=`direct sentence in p1`。

```bash
python3 /skills/weekly-skill/scripts/workflow.py --case /app/work/nano-case.json \
  --task /app/work/task.md --input-dir /app/work --work /app/work/run --output /app/output \
  --candidate /app/work/assertions.json
```

示意输出：`nanopublications[0].status=evidence_bound`，N-Triples 含 evidence paragraph/quote，readback triples>0；本地 URN 不是 DOI。

## 自查与维护

逐断言核原子性、方向、条件、直接/间接、真实来源和 confidence 依据；复读 JSON/NT 并检查失败断言未混入。关系词表扩展需补方向/缺证/冲突回归并升版。

无真实公开 GitHub URL 不创建 `GITHUB_URL.txt`，不发布 nanopublication 或代码。
