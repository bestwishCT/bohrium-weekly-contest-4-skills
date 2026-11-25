#!/usr/bin/env python3
"""Deterministic provenance, statistics and inference-boundary audit.

Internal audit records are not the case output_contract.  Plot estimates are
never promoted to exact values; unresolved source limits produce partial.
"""

import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path


class Report:
    def __init__(self):
        self.checks = []

    def need(self, ok, code, message):
        self.checks.append({"code": code, "ok": bool(ok), "message": message})

    def pending(self, code, message):
        self.checks.append({"code": code, "ok": False, "status": "pending", "message": message})

    @property
    def failures(self):
        return [item for item in self.checks if not item["ok"] and item.get("status") != "pending"]

    @property
    def pending_checks(self):
        return [item for item in self.checks if item.get("status") == "pending"]


def load():
    if len(sys.argv) != 2:
        raise ValueError("usage: audit.py INPUT.json")
    path = Path(sys.argv[1])
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("top-level JSON must be object")
    return data, path


def indexed(records, label, report):
    result = {}
    if not isinstance(records, list):
        report.pending("input_type:" + label, "材料台账缺失或不是数组；未猜测任何记录")
        return result
    for position, record in enumerate(records):
        if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not record["id"].strip():
            report.need(False, "record_id:%s[%d]" % (label, position), "每条记录必须有非空字符串ID")
            continue
        record_id = record["id"]
        report.need(record_id not in result, "unique_id:" + record_id, "稳定ID不得重复")
        if record_id not in result:
            result[record_id] = record
    return result


def numeric_tokens(text):
    return re.findall(r"(?<![A-Za-z])[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?%?", text or "")


def decimal_value(value):
    if isinstance(value, bool):
        raise InvalidOperation
    return Decimal(str(value).rstrip("%"))


def label_matches_value(label, value):
    try:
        expected = decimal_value(value)
        return any(decimal_value(token) == expected for token in numeric_tokens(label))
    except (InvalidOperation, ValueError):
        return False


def numeric_statistic(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def main():
    data, path = load()
    report = Report()
    for key in ("panels", "readings", "statistics", "findings", "results", "discussion_claims", "discussion", "limitations", "design"):
        if key not in data:
            report.pending("input_missing:" + key, "内部审计台账缺项；对应材料或章节未完成")
    results = data.get("results", "") if isinstance(data.get("results", ""), str) else ""
    discussion = data.get("discussion", "") if isinstance(data.get("discussion", ""), str) else ""
    report.need(bool(results.strip()), "results_present", "结果段为真实可编辑正文")
    report.need(bool(discussion.strip()), "discussion_present", "讨论段为真实可编辑正文")
    report.need("# 结果" in results or re.search(r"^#\s+Results?\b", results, re.I | re.M) is not None, "results_section", "结果正文有明确章节边界")
    report.need("# 讨论" in discussion or re.search(r"^#\s+Discussion\b", discussion, re.I | re.M) is not None, "discussion_section", "讨论正文有明确章节边界")

    panels = indexed(data.get("panels", []), "panels", report)
    readings = indexed(data.get("readings", []), "readings", report)
    statistics = indexed(data.get("statistics", []), "statistics", report)
    findings = indexed(data.get("findings", []), "findings", report)
    literature = indexed(data.get("literature", []), "literature", report)
    if not panels:
        report.pending("panel_coverage", "没有可读图表/面板；不能猜图或编造读数")

    for panel_id, panel in panels.items():
        source_kind = panel.get("source_kind")
        report.need(source_kind in ("data_table", "plot", "caption"), "panel_source:" + panel_id, "区分精确表值、图上估读和图注信息")
        report.need(isinstance(panel.get("locator"), str) and bool(panel.get("locator", "").strip()), "panel_locator:" + panel_id, "图表身份含实际文件、面板、坐标或图注定位")
        report.need(isinstance(panel.get("unit"), str) and bool(panel.get("unit", "").strip()), "panel_unit:" + panel_id, "单位来自真实材料而非猜测")

    for reading_id, reading in readings.items():
        source = panels.get(reading.get("panel_id"))
        report.need(source is not None, "panel:" + reading_id, "读数必须绑定已登记面板")
        if source is None:
            continue
        report.need(label_matches_value(reading.get("label", ""), reading.get("value")), "reading_label:" + reading_id, "正文标签中的实际数值与读数一致")
        report.need(isinstance(reading.get("label"), str) and reading.get("label", "") in results, "results_value:" + reading_id, "读数转写进结果全文")
        report.need(isinstance(reading.get("locator"), str) and bool(reading.get("locator", "").strip()), "reading_locator:" + reading_id, "每个数值保留可独立回查的位置")
        if source.get("source_kind") == "plot":
            report.need(reading.get("precision") == "estimated", "estimate_precision:" + reading_id, "图上估读不得标为精确值")
            if not isinstance(reading.get("uncertainty"), str) or not reading.get("uncertainty", "").strip():
                report.pending("estimate_uncertainty:" + reading_id, "图像估读的不确定性未说明，不能按精确结果交付")
            report.need(re.search(r"约|估计|approx|~", reading.get("label", ""), re.I) is not None, "estimate_wording:" + reading_id, "估读在正文中有相应限定语")
        else:
            report.need(reading.get("value") == source.get("value") and reading.get("unit") == source.get("unit"), "reading:" + reading_id, "精确读数及单位与原表/图注一致")
            report.need(reading.get("precision") == "exact", "exact_precision:" + reading_id, "精确表值/图注读数明确标为exact")

    for statistic_id, statistic in statistics.items():
        report.need(isinstance(statistic.get("locator"), str) and bool(statistic.get("locator", "").strip()), "statistic_locator:" + statistic_id, "统计量与显著性来自实际输出位置")
        report.need(isinstance(statistic.get("comparison"), str) and bool(statistic.get("comparison", "").strip()), "statistic_comparison:" + statistic_id, "显著性绑定具体组间/条件比较")
        p_value = numeric_statistic(statistic.get("p_value"))
        alpha = numeric_statistic(statistic.get("alpha"))
        if p_value is not None and alpha is not None and isinstance(statistic.get("significant"), bool):
            report.need(statistic["significant"] == (p_value <= alpha), "statistic_consistency:" + statistic_id, "P值、阈值与显著性声明一致")

    if not findings:
        report.pending("finding_coverage", "材料尚不足以形成发现清单")
    used_readings = set()
    has_negative = False
    for finding_id, finding in findings.items():
        sentence = finding.get("sentence", "")
        report.need(isinstance(sentence, str) and bool(sentence) and sentence in results, "finding:" + finding_id, "主要/阴性/不一致发现进入结果正文")
        reading_ids = finding.get("reading_ids", [])
        report.need(isinstance(reading_ids, list) and bool(reading_ids) and all(item in readings for item in reading_ids), "finding_readings:" + finding_id, "发现绑定实际读数，覆盖条件与幅度")
        if isinstance(reading_ids, list):
            used_readings.update(item for item in reading_ids if isinstance(item, str))
        kind = finding.get("kind")
        report.need(kind in ("primary", "secondary", "negative", "inconsistent"), "finding_kind:" + finding_id, "区分主要、次要、阴性和不一致结果")
        has_negative = has_negative or kind == "negative"
        significant = finding.get("significant")
        if significant is not None:
            statistic_id = finding.get("statistic_id")
            statistic = statistics.get(statistic_id)
            report.need(isinstance(significant, bool) and statistic is not None, "significance_source:" + finding_id, "显著或不显著必须绑定真实统计输出")
            if statistic is not None:
                report.need(statistic.get("significant") is significant, "significance_match:" + finding_id, "发现与统计输出的显著性一致")
        if significant is False:
            report.need(not re.search(r"显著(?:提高|降低|改善)|证明无效", sentence), "significance:" + finding_id, "不显著不改写为显著或证明无效")
    if not has_negative:
        report.pending("negative_coverage", "未登记阴性结果；需回读材料确认是否确实不存在，不得补造")
    for reading_id in readings:
        report.need(reading_id in used_readings, "reading_finding_coverage:" + reading_id, "每项已抽取读数进入发现清单，避免选择性报告")
    for panel_id in panels:
        report.need(any(item.get("panel_id") == panel_id for item in readings.values()), "panel_reading_coverage:" + panel_id, "已登记面板均有读数或明确缺失记录")

    report.need(not re.search(r"可能是因为|机制是|据此推测", results), "results_interpretation_boundary", "机制解释不混入结果段")
    linked_literature = set()
    claims = data.get("discussion_claims", [])
    if not claims:
        report.pending("discussion_coverage", "讨论论断尚未完成证据绑定")
    if not isinstance(claims, list):
        claims = []
        report.need(False, "discussion_claims_type", "discussion_claims必须为数组")
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            report.need(False, "discussion_claim_type:%d" % index, "讨论论断必须为对象")
            continue
        claim_id = str(claim.get("id", index))
        sentence = claim.get("sentence", "")
        report.need(isinstance(sentence, str) and bool(sentence) and sentence in discussion, "discussion:" + claim_id, "讨论论断进入全文")
        finding_id = claim.get("finding_id")
        literature_id = claim.get("literature_id")
        report.need(finding_id in findings or literature_id in literature, "discussion_source:" + claim_id, "讨论绑定本研究发现或真实文献")
        report.need(isinstance(claim.get("relation"), str) and bool(claim.get("relation", "").strip()), "discussion_relation:" + claim_id, "说明一致、不一致或解释关系，不堆装饰引用")
        if literature_id in literature:
            linked_literature.add(literature_id)
            report.need(isinstance(claim.get("literature_locator"), str) and claim.get("literature_locator") == literature[literature_id].get("locator"), "discussion_literature_locator:" + claim_id, "文献论断绑定原登记的具体位置")
        if claim.get("kind") == "mechanism":
            report.need(any(word in sentence for word in ("可能", "推测", "hypothes", "suggest")), "mechanism:" + claim_id, "机制明确标为解释/推测")
    for literature_id, source in literature.items():
        report.need(all(isinstance(source.get(key), str) and source[key].strip() for key in ("author", "year", "venue", "locator")), "literature_metadata:" + literature_id, "讨论文献元数据和位置真实完整")
        if source.get("relation") == "inconsistent":
            report.need(literature_id in linked_literature, "inconsistent_discussion:" + literature_id, "不一致文献不得被隐藏")

    limitations = data.get("limitations", [])
    if not limitations:
        report.pending("limitations_coverage", "局限及其具体影响尚未完成，不能标为完整")
    if not isinstance(limitations, list):
        limitations = []
        report.need(False, "limitations_type", "limitations必须为数组")
    for index, limitation in enumerate(limitations):
        if not isinstance(limitation, dict):
            report.need(False, "limitation_type:%d" % index, "局限必须为对象")
            continue
        limitation_id = str(limitation.get("id", index))
        report.need(limitation.get("dimension") in ("method", "sample", "measurement", "statistics"), "limitation_dimension:" + limitation_id, "局限落到方法、样本、测量或统计层面")
        report.need(isinstance(limitation.get("impact"), str) and bool(limitation.get("impact", "").strip()), "limitation_impact:" + limitation_id, "说明局限对结论边界的具体影响")
        report.need(isinstance(limitation.get("statement"), str) and limitation.get("statement", "") in discussion, "limitation_text:" + limitation_id, "具体局限进入讨论正文")

    design = data.get("design")
    report.need(design in ("observational", "randomized_controlled", "controlled_experiment", "other"), "design_known", "研究设计真实登记")
    if design == "other":
        report.pending("design_inference_boundary", "设计类型未知，因果强度需人工确认")
    if design in ("observational", "other"):
        report.need(not re.search(r"导致|证明|causes?|proves?", discussion, re.I), "causal_boundary", "观察/未知设计讨论不作因果或证明宣称")
    report.need("证明" not in discussion, "proof_overclaim", "即使实验设计也不把结果写成普遍证明")

    status = "fail" if report.failures else ("partial" if report.pending_checks else "pass")
    output = {
        "artifact": str(path), "status": status, "checks": report.checks,
        "manual_confirmations": [{"code": item["code"], "message": item["message"]} for item in report.pending_checks],
        "note": "机械审计不证明读图或统计语义正确；估读、缺统计、未知设计和缺局限返回partial。",
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        sys.exit(2)
