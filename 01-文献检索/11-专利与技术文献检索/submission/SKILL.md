---
name: search-patents
description: 在明确授权的数据源内执行专利号码规范定位、技术主题与科技文献联合检索，保留观察到的同族、权利要求版本及法律状态不确定性。适用于技术查新和专利证据审计，不用于侵权、FTO、价值判断或伪造官方实时法律状态。
---

# 授权优先的专利与技术文献检索

## 触发与禁区

用户需要由公开号/申请号/授权号/优先权号定位记录，或结合关键词、IPC/CPC 查专利及科技论文，并核对同族、权利要求版本和法律状态时使用。授权与展示许可先于检索。

不提供专利撰写、侵权、自由实施（FTO）、有效性或价值意见，不替代专利律师。不得绕过登录、付费墙、反爬或数据展示限制，不上传受限原文。

## 正式输入与启动

1. 读取 `environment/resources.yaml`，确认真实源、版本、授权和工具；读取 `/input/case.json` 与 `/input/task.md` 的 `case_id`、`output_contract`、查询类型、允许操作及必需 artifacts。
2. 逐份核查实际 6 条专利记录、3 份 USPTO 原始影印本、真实科技对比论文及各自号码/主题查询。影印本 OCR 必须保留原图页定位和不确定字符，不假定存在答案层。
3. 将授权源表、实际专利/论文、权利要求版本、观察到同族链接和查询写入 `/app/work/analysis_input.json`。材料日期与真实查询日期分开，缺失日期不得从出版常识补齐。
4. `/app/work` 留中间产物，`/app/output` 留最终 JSON。先落盘明确限制的部分结果，再检查号码类型、族、claim version 与授权；受限原文不因留证复制到 output。
5. 在 `/skills/weekly-skill/scripts/run.py` 执行；`common.py` 从脚本位置加载。不创建外部监控或实时法律状态连接。

## 专利检索专属算法

1. **授权矩阵**：每库保存名称、版本、材料/查询日期、许可、display fields、留存及保密要求。green 处理允许字段；yellow 只输出明确 display fields，其他字段列入 redacted；red 或未声明授权的记录直接排除且不导出内容。
2. **号码规范化**：保留原号码，做大小写/标点和明确国家代码处理；`number_type` 始终区分 publication/application/grant/priority。号码查询缺 type 会报 ambiguous，绝不跨类型模糊匹配；优先权日、申请日、公开日和授权日各字段独立。
3. **本地号码路径**：在实际授权记录中执行同类型规范化精确匹配，输出 matched number 和直接证据。无匹配仅限可用授权有限语料，不用相似号码补位；来源失败不是零命中。
4. **本地主题路径**：`terms` 在授权 title/abstract/classification/claim 可检索字段中全部出现，且分类号条件（若给）存在交集才命中。科技论文单独按研究对象和方法匹配，绝不当同族或法律状态证据。
5. **观察同族**：只用有来源定位的真实 family_links 建立无向连通观察集合，保留每条边的原始方向和优先级/链接类型。输出是 observed linked component，`exhaustive: false`；简单/扩展族口径不明就保留不明，不按关键词相似归族。
6. **权利要求版本**：每个 version 独立保存正文、hash、来源/页、独立项与从属关系、OCR 标志和置信度。低/缺 OCR 置信度进入人工影印本复核；版本间即使文本接近也不推断保护范围等价，WO 合并 OCR 边界保留。
7. **法律状态降级**：历史断言带状态、时点和来源单独保存；每个命中记录的当前状态固定为 `unknown_requires_live_official_verification`，因为本案没有当前官方核验。旧影印、网页、年费常识或申请人状态不能改变这一结论。
8. **证据风险**：号码直接命中、族观察、OCR/权利要求版本和法律未知分别标证据强度及缺口，稳定复跑，不发明实时变更追踪。

核心结构：

```json
{
  "sources": [{"name": "actual-authorized-source", "authorization": "green", "version": "actual version", "material_date": "actual date", "query_date": "actual date", "license": "actual authorized scope", "display_fields": []}],
  "patents": [{"id": "P1", "source": "actual-authorized-source", "title": "actual title", "classifications": ["actual IPC/CPC"], "numbers": [{"type": "publication", "value": "actual publication number", "country": "XX", "kind_code": "A1"}], "priority_date": "actual", "filing_date": "actual", "publication_date": "actual", "claims": [{"version_id": "actual version", "source_locator": "facsimile#page", "page": 3, "text": "actual claim", "independent": true, "ocr": true, "ocr_confidence": 0.62}], "legal_history": []}],
  "family_links": [{"from": "P1", "to": "P2", "source": "actual-authorized-source", "source_locator": "record#priority-link", "family_scope": "observed only"}],
  "scientific_papers": [{"id": "N1", "title": "actual paper", "methods": "actual methods", "source_locator": "paper#page"}],
  "queries": [{"id": "number-1", "query_type": "number", "number_type": "publication", "raw_number": "actual publication number", "source": "actual-authorized-source", "executed": true, "status": "observed", "query_date": "actual date"}],
  "ocr_review_threshold": 0.8
}
```

`ocr_review_threshold` 是辅助复核阈值，不是法律或质量判断阈值。

## 输出、证据和状态

- `/app/output/result.json` 含真实 `case_id` 及实际合同全字段；内部为 `authorization_matrix/query_results/matched_patents/observed_families/scientific_literature/legal_status/claim_review_queue/risk_register`。
- `/app/work/analysis.json` 是完整字段与版本审计，`contract_validation.json` 保存合同警告，`run_log.json` 保存环境。受限字段不会因为这些审计文件而获得展示许可。
- `completed_with_current_legal_status_unknown` 表示查询完成但法律状态仍未实时官方核验；`partial` 表示授权、号码类型、来源失败等缺口；`empty` 是未给查询。
- 所有“族”仅为观察到的链接，所有 claim 仅为版本定位，所有科学比较仅为技术关联。不能外推全球同族、完整权利范围或最新技术覆盖。

## 依赖与降级

仅 Python 3 标准库，合成安装副本实测 **CPython 3.9.6**；无 OCR 引擎、商业库、账号或联网依赖。实际影印本 OCR 需在资源清单中另行提供并真实执行。其他 Python 版本未验证。

脚本退出码 0=算法完成，1=保留部分结果的领域异常，2=case/task 读取失败。鉴权失败即不访问该记录；号码未命中与来源不可用分别写；状态冲突并列历史时点；OCR 不清进入复核；字段缺失不以相似日期或号码补齐。

## 最小示意（虚构非官方案例）

```json
{"sources":[{"name":"demo-green","authorization":"green","version":"demo-v1","license":"synthetic fixture"}],"patents":[{"id":"P1","source":"demo-green","title":"SYNTHETIC SENSOR DOCUMENT","classifications":["G01D"],"numbers":[{"type":"publication","value":"XX 0001 A1","country":"XX","kind_code":"A1"}],"claims":[],"legal_history":[]}],"family_links":[],"scientific_papers":[],"queries":[{"id":"q1","query_type":"number","number_type":"publication","raw_number":"XX 0001 A1","source":"demo-green","executed":true,"status":"observed"}]}
```

```bash
python3 /skills/weekly-skill/scripts/run.py --case /input/case.json --task /input/task.md --data /app/work/analysis_input.json --input-root /input --resources environment/resources.yaml
```

若 `case_id: demo-patent` 且合同含常用五字段，示意节为 `{"case_id":"demo-patent","status":"completed_with_current_legal_status_unknown","results":[{"query_id":"q1","matches":[{"record_id":"P1"}]}]}`。号码和内容完全虚构，不是官方专利或登记状态。

## 自查与维护

1. 每条输出回查查询对象、号码类型、授权源/版本/日期、原记录定位；主题查询检查概念和分类条件，科技论文不进入族/法律证据。
2. 逐条审查观察族链接、claim version/hash/page 与 OCR 不确定性。当前状态必须未知，历史状态有明确时点，不能出现有效/失效/FTO 暗示。
3. 维护必测鉴权失败、红黄权限、缺号码类型、无命中、同族缺边、日期字段混淆、低 OCR、不同权利要求版本和法律状态冲突。
4. 同一授权语料复跑号码与观察族稳定；只有真实新证据才新增关系。修改过滤逻辑后重查黄色 redaction 和红色内容未泄漏。
5. 只提交 Skill 与脚本，不附影印本、受限原文、预制法律结果或验证输出。无真实公开 GitHub URL 及展示许可时正式提交该项阻塞，不创建 `GITHUB_URL.txt`，不发布、不提交、不处理令牌。预算为 64 请求、900 秒、每响应含推理 16384 tokens。
