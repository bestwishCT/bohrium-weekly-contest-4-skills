#!/usr/bin/env python3
"""Deterministic checks for evidence-bound introduction/related-work drafts.

The input is an internal work ledger, never a replacement for the case's public
output_contract.  Semantic equivalence still requires reading the real sources.
"""

import json
import re
import sys
from pathlib import Path


SECTION_NAMES = ("background", "related_work", "gap", "contributions")


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


def section_text(sections, name):
    value = sections.get(name, "") if isinstance(sections, dict) else ""
    return value if isinstance(value, str) else ""


def main():
    data, path = load()
    report = Report()
    for key in ("literature", "groups", "gaps", "paper_evidence", "contributions", "claims", "manuscript", "sections"):
        if key not in data:
            report.pending("input_missing:" + key, "内部审计台账缺项；对应能力未完成，不按完整交付")

    manuscript = data.get("manuscript", "")
    sections = data.get("sections", {})
    for name in SECTION_NAMES:
        text = section_text(sections, name)
        report.need(bool(text.strip()), "section:" + name, "四部分均有真实正文")
        report.need(bool(text.strip()) and text in manuscript, "section_binding:" + name, "分节正文确实进入完整稿件")

    literature = indexed(data.get("literature", []), "literature", report)
    if not literature:
        report.pending("literature_coverage", "文献库为空或不可读；不得用训练记忆补造参考文献")
    for record_id, card in literature.items():
        report.need(all(isinstance(card.get(key), str) and card[key].strip() for key in ("author", "year", "venue")), "metadata:" + record_id, "真实作者、年份和来源齐备")
        for key in ("problem", "route", "conclusion", "limitation"):
            if not isinstance(card.get(key), str) or not card[key].strip():
                report.pending("card_%s:%s" % (key, record_id), "原文未支持该卡片字段时必须保留待核")
        access = card.get("access_level")
        report.need(access in ("full_text", "abstract", "metadata_only"), "access_level:" + record_id, "证据可读层级如实记录")
        if access != "full_text":
            report.pending("evidence_scope:" + record_id, "仅摘要/元数据可读；结论、条件和局限未冒充全文核验")
        if not isinstance(card.get("locator"), str) or not card["locator"].strip():
            report.pending("source_locator:" + record_id, "尚无可核对的页码、段落或原文位置")

    groups = data.get("groups", [])
    grouped = set()
    if not groups and literature:
        report.need(False, "groups_present", "已有文献必须按问题或路线比较，而非逐篇罗列")
    if not isinstance(groups, list):
        groups = []
        report.need(False, "groups_type", "groups必须为数组")
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            report.need(False, "group_type:%d" % index, "分组必须为对象")
            continue
        route = str(group.get("route", index))
        members = group.get("members", [])
        valid_members = isinstance(members, list) and bool(members) and all(item in literature for item in members)
        report.need(valid_members, "group:" + route, "路线分组必须引用已登记文献")
        report.need(isinstance(group.get("axis"), str) and bool(group.get("axis", "").strip()), "group_axis:" + route, "记录统一的问题/方法/技术路线分组轴")
        dimensions = group.get("dimensions")
        report.need(isinstance(dimensions, list) and bool(dimensions) and all(isinstance(item, str) and item.strip() for item in dimensions), "group_dimensions:" + route, "在同维度比较问题、方法、数据或评价")
        report.need(isinstance(group.get("comparison"), str) and bool(group.get("comparison", "").strip()), "comparison:" + route, "比较取舍而非只贴主题标签")
        if isinstance(members, list):
            grouped.update(item for item in members if isinstance(item, str))
    for record_id in literature:
        report.need(record_id in grouped, "group_membership:" + record_id, "主要文献进入至少一个比较组")

    evidence = data.get("paper_evidence", [])
    evidence_records = data.get("paper_evidence_records", {})
    if not isinstance(evidence, list):
        evidence = []
        report.need(False, "paper_evidence_type", "本文证据锚点必须为数组")

    gaps = data.get("gaps", [])
    if not gaps:
        report.pending("gap_coverage", "没有足够文献局限推导空白；保留未证项")
    if not isinstance(gaps, list):
        gaps = []
        report.need(False, "gaps_type", "gaps必须为数组")
    for index, gap in enumerate(gaps):
        if not isinstance(gap, dict):
            report.need(False, "gap_type:%d" % index, "空白记录必须为对象")
            continue
        gap_id = str(gap.get("id", index))
        sources = gap.get("limitations_from", [])
        report.need(isinstance(sources, list) and bool(sources) and all(item in literature for item in sources), "gap_sources:" + gap_id, "空白只能由已登记文献的真实局限推出")
        report.need(isinstance(gap.get("statement"), str) and gap.get("statement", "") in section_text(sections, "gap"), "gap_text:" + gap_id, "空白论断进入研究空白正文")
        locators = gap.get("limitation_locators", {})
        for source_id in sources if isinstance(sources, list) else []:
            card = literature.get(source_id, {})
            report.need(isinstance(card.get("limitation"), str) and bool(card.get("limitation", "").strip()), "gap_limitation:%s:%s" % (gap_id, source_id), "推导所用局限必须实际存在")
            report.need(isinstance(locators, dict) and isinstance(locators.get(source_id), str) and bool(locators.get(source_id, "").strip()), "gap_locator:%s:%s" % (gap_id, source_id), "每条局限有原文定位")
        report.need(gap.get("response_anchor") in evidence, "gap_response_source:" + gap_id, "空白回应必须落到本文真实证据")
        response_status = gap.get("response_status")
        report.need(response_status in ("fully_addressed", "partially_addressed", "unverified"), "gap_response_status:" + gap_id, "区分完全解决、部分回应和尚待验证")
        if response_status == "unverified":
            report.pending("gap_unverified:" + gap_id, "本文回应尚待验证，不能写成已解决")
        report.need(isinstance(gap.get("response_statement"), str) and gap.get("response_statement", "") in section_text(sections, "gap"), "gap_response_text:" + gap_id, "本文回应程度进入连贯正文")

    contributions = data.get("contributions", [])
    if not contributions:
        report.pending("contribution_coverage", "本文证据不足时不能拔高贡献")
    if not isinstance(contributions, list):
        contributions = []
        report.need(False, "contributions_type", "contributions必须为数组")
    for index, contribution in enumerate(contributions):
        if not isinstance(contribution, dict):
            report.need(False, "contribution_type:%d" % index, "贡献必须为对象")
            continue
        contribution_id = str(contribution.get("id", index))
        anchor = contribution.get("evidence_anchor")
        report.need(anchor in evidence, "contribution:" + contribution_id, "贡献有论文实际方法、图表或结果锚点")
        report.need(isinstance(contribution.get("statement"), str) and contribution.get("statement", "") in section_text(sections, "contributions"), "contribution_text:" + contribution_id, "贡献进入成稿对应部分")
        record = evidence_records.get(anchor, {}) if isinstance(evidence_records, dict) else {}
        report.need(all(isinstance(record.get(key), str) and record[key].strip() for key in ("locator", "excerpt")), "contribution_evidence:" + contribution_id, "贡献锚点保留实际位置和支持性原文，不只填图号")

    claims = data.get("claims", [])
    if not claims:
        report.pending("claim_coverage", "关键论断尚未完成论断级引用绑定")
    if not isinstance(claims, list):
        claims = []
        report.need(False, "claims_type", "claims必须为数组")
    cited = set()
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            report.need(False, "claim_type:%d" % index, "论断必须为对象")
            continue
        claim_id = str(claim.get("id", index))
        source_ids = claim.get("source_ids", [])
        report.need(isinstance(source_ids, list) and bool(source_ids) and all(item in literature for item in source_ids), "citation_coverage:" + claim_id, "关键论断引用全部指向已登记来源")
        section = claim.get("section")
        report.need(section in SECTION_NAMES, "claim_section:" + claim_id, "论断归属实际四部分")
        text = section_text(sections, section)
        report.need(isinstance(claim.get("sentence"), str) and claim.get("sentence", "") in text, "claim_text:" + claim_id, "被审论断精确定位到成稿对应部分")
        report.need(isinstance(claim.get("citation_text"), str) and bool(claim.get("citation_text", "").strip()) and claim.get("citation_text", "") in text, "citation_adjacent:" + claim_id, "引用文本在论断所在部分真实出现")
        report.need(isinstance(claim.get("relation"), str) and bool(claim.get("relation", "").strip()), "claim_relation:" + claim_id, "说明引用如何支持该论断")
        locators = claim.get("source_locators", {})
        for source_id in source_ids if isinstance(source_ids, list) else []:
            cited.add(source_id)
            report.need(isinstance(locators, dict) and isinstance(locators.get(source_id), str) and bool(locators.get(source_id, "").strip()), "claim_locator:%s:%s" % (claim_id, source_id), "论断逐源绑定具体原文位置")
    for record_id in literature:
        report.need(record_id in cited, "citation_use:" + record_id, "主要/关键文献至少支撑一个具体论断")
    report.need(not re.search(r"首次|国际领先|尚无研究", manuscript), "unverified_novelty", "不含无依据首创、领先或空白宣称")

    status = "fail" if report.failures else ("partial" if report.pending_checks else "pass")
    output = {
        "artifact": str(path), "status": status, "checks": report.checks,
        "manual_confirmations": [{"code": item["code"], "message": item["message"]} for item in report.pending_checks],
        "note": "机械审计不替代真实原文语义复核；仅摘要或定位不足时返回partial。",
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        sys.exit(2)
