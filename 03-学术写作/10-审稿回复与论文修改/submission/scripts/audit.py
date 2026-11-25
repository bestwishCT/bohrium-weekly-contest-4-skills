#!/usr/bin/env python3
"""Audit controlled-baseline revisions, replies, evidence and original contracts."""
import json
import re
import sys
from pathlib import Path


class Report:
    def __init__(self):
        self.checks = []

    def need(self, ok, code, message, **details):
        check = {"code": code, "ok": bool(ok), "message": message}
        check.update(details)
        self.checks.append(check)

    @property
    def failures(self):
        return [c for c in self.checks if not c["ok"]]


def finish(data, path, report):
    out = {"artifact": str(path), "status": "pass" if not report.failures else "fail", "checks": report.checks}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if not report.failures else 1


def load():
    if len(sys.argv) != 2:
        raise ValueError("usage: audit.py INPUT.json")
    path = Path(sys.argv[1]).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("top-level JSON must be object")
    return data, path


def resolve_path(value, base):
    path = Path(value)
    return (path if path.is_absolute() else base / path).resolve()


def within(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def readable_file(path):
    try:
        with path.open("rb") as stream:
            stream.read(1)
        return path.is_file()
    except OSError:
        return False


def json_equal(left, right):
    return json.dumps(left, sort_keys=True, ensure_ascii=False, separators=(",", ":")) == json.dumps(right, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def type_ok(value, expected):
    checks = {
        "object": lambda x: isinstance(x, dict),
        "array": lambda x: isinstance(x, list),
        "string": lambda x: isinstance(x, str),
        "boolean": lambda x: isinstance(x, bool),
        "null": lambda x: x is None,
        "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
        "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
    }
    expected_types = expected if isinstance(expected, list) else [expected]
    return bool(expected_types) and all(isinstance(x, str) and x in checks for x in expected_types) and any(checks[x](value) for x in expected_types)


def schema_errors(value, schema, path="result"):
    errors = []
    if not isinstance(schema, dict):
        return errors
    if "type" in schema and not type_ok(value, schema["type"]):
        errors.append(("contract_type:" + path, path, "应为type=%r" % schema["type"]))
        return errors
    if "enum" in schema:
        choices = schema["enum"]
        if not isinstance(choices, list) or not any(json_equal(value, choice) for choice in choices):
            errors.append(("contract_enum:" + path, path, "值不在原enum中"))
    if isinstance(value, dict):
        required = schema.get("required", [])
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in value:
                    errors.append(("contract_required:" + path + "." + key, path + "." + key, "缺少原required字段"))
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for key, child in properties.items():
                if key in value:
                    errors.extend(schema_errors(value[key], child, path + "." + key))
    if isinstance(value, list) and "items" in schema:
        items = schema["items"]
        if isinstance(items, list):
            for index, child in enumerate(items):
                if index < len(value):
                    errors.extend(schema_errors(value[index], child, "%s[%d]" % (path, index)))
        else:
            for index, child_value in enumerate(value):
                errors.extend(schema_errors(child_value, items, "%s[%d]" % (path, index)))
    return errors


PATH_KEY = re.compile(r"(?:^|_)(?:path|file|artifact|deliverable)$", re.I)


def result_paths(value, key=""):
    if isinstance(value, dict):
        if isinstance(value.get("path"), str):
            yield key or "path", value["path"]
        for child_key, child in value.items():
            if child_key != "path":
                yield from result_paths(child, child_key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from result_paths(child, "%s[%d]" % (key, index))
    elif isinstance(value, str) and PATH_KEY.search(key):
        yield key, value


def audit_delivery(data, base, report):
    case_ref = data.get("case_json")
    report.need(bool(case_ref), "case_json_present", "交付审计必须引用实际case.json")
    if not case_ref:
        return
    case_path = resolve_path(case_ref, base)
    if not within(case_path, base) or not readable_file(case_path):
        report.need(False, "case_json_exists", "原case真实可读且不越出材料根目录", path=str(case_path))
        return
    case = json.loads(case_path.read_text(encoding="utf-8"))
    if not isinstance(case, dict):
        raise ValueError("case top-level must be object")
    for key in ("case_id", "output_contract"):
        if key in data:
            report.need(json_equal(data[key], case.get(key)), "case_%s_consistent" % key, "审计副本与原case一致")
    case_id = case.get("case_id")
    contract = case.get("output_contract")
    report.need(isinstance(case_id, str) and bool(case_id.strip()), "case_id_present", "真实case_id存在")
    report.need(isinstance(contract, dict), "output_contract_present", "原output_contract对象存在")
    if not isinstance(contract, dict):
        return
    allowed_root = resolve_path(data.get("allowed_output_dir", "/app/output"), base)
    if "allowed_output_dir" in data:
        report.need(data.get("synthetic") is True and within(allowed_root, base), "output_root_override", "非默认输出根仅允许明确标注的synthetic测试且不得逃逸")
    output_dir = resolve_path(data.get("output_dir", allowed_root), base)
    report.need(output_dir == allowed_root, "output_root", "实际输出目录等于允许根目录")
    result_ref = data.get("result_json")
    report.need(bool(result_ref), "result_json_present", "交付审计必须指定真实result.json")
    if not result_ref:
        return
    result_path = resolve_path(result_ref, base)
    if not within(result_path, allowed_root) or not readable_file(result_path):
        report.need(False, "result_json_exists", "result.json真实可读且位于允许输出根目录", path=str(result_path))
        return
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("result top-level must be object")
    report.need(json_equal(result.get("case_id"), case_id), "case_id_match", "result保留真实case_id")
    report.need(json_equal(result.get("output_contract"), contract), "output_contract_preserved", "result逐字段保留原嵌套required/type/enum/items")
    schema = any(key in contract for key in ("type", "properties", "required", "items", "enum"))
    properties = contract.get("properties", {}) if schema else contract
    required = contract.get("required", []) if schema else list(contract)
    properties = properties if isinstance(properties, dict) else {}
    required = required if isinstance(required, list) else []
    allowed = {"case_id", "output_contract"} | set(properties) | {x for x in required if isinstance(x, str)}
    report.need(not (set(result) - allowed), "no_invented_contract_fields", "result不含case_id/output_contract之外的自拟字段", extra=sorted(set(result) - allowed))
    missing = [key for key in required if key not in result]
    report.need(not missing, "contract_fields_complete", "result包含合同required字段", missing=missing)
    if schema:
        projected = {key: result[key] for key in properties if key in result}
        for key in required:
            if key in result and key not in projected:
                projected[key] = result[key]
        for code, path, message in schema_errors(projected, contract):
            report.need(False, code, message, path=path)
    for key in properties if schema else contract:
        if key not in result:
            continue
        paths = list(result_paths(result[key], key))
        if PATH_KEY.search(key) and not paths:
            report.need(False, "deliverable_path_present:" + key, "路径字段必须含字符串或path对象")
        for path_key, value in paths:
            artifact = resolve_path(value, allowed_root)
            report.need(bool(value.strip()) and within(artifact, allowed_root) and readable_file(artifact), "deliverable_exists:" + path_key, "合同产物路径非空、真实可读且不逃逸", path=str(artifact))


def audit_review(data, report):
    baseline, revised = data["baseline"], data["revised"]
    if not isinstance(baseline, str) or not isinstance(revised, str):
        raise TypeError("baseline and revised must be strings")
    report.need(bool(revised.strip()), "revised_nonempty", "可编辑基线修订稿非空")
    report.need(data.get("scope") == "controlled_baseline_summary", "controlled_scope", "只处理受控基线摘要")
    for key in ("baseline_version", "revised_version"):
        report.need(isinstance(data.get(key), str) and bool(data.get(key).strip()), key + "_present", "明确真实输入/修订版本标识")
    requests = data["subrequests"]
    responses = data["responses"]
    request_ids = [entry["id"] for entry in requests]
    response_ids = [entry["subrequest_id"] for entry in responses]
    comments = {entry["id"]: entry for entry in requests}
    report.need(bool(requests), "subrequests_nonempty", "意见子要求不得为空")
    report.need(len(request_ids) == len(set(request_ids)), "unique_subrequest_ids", "子要求ID唯一")
    report.need(len(response_ids) == len(set(response_ids)), "unique_response_ids", "每个子要求至多一条对应回复")
    report.need(set(comments) == set(response_ids), "subrequest_coverage", "每条子要求均有且只有对应回复")
    for entry in requests:
        tag = entry["id"]
        report.need(entry.get("kind") in ("方法", "数据", "写作", "格式", "伦理"), "kind:" + tag, "子要求类型明确")
        report.need(isinstance(entry.get("severity"), str) and bool(entry.get("severity").strip()), "severity:" + tag, "严重性明确")
        report.need(isinstance(entry.get("dependencies"), list), "dependencies:" + tag, "依赖关系以数组记录")
        report.need(isinstance(entry.get("action"), str) and bool(entry.get("action").strip()), "action:" + tag, "可执行动作明确")

    all_labels = []
    for entry in responses:
        tag = entry["subrequest_id"]
        report.need(entry.get("decision") in ("采纳", "部分采纳", "不采纳"), "decision:" + tag, "处理决策明确")
        report.need(isinstance(entry.get("rationale"), str) and bool(entry.get("rationale").strip()), "rationale:" + tag, "采纳/澄清/反驳均有具体理由")
        report.need(entry.get("tone") in ("接受", "澄清", "反驳", "申诉"), "tone:" + tag, "回复语气类型明确")
        report.need(isinstance(entry.get("reply"), str) and bool(entry.get("reply").strip()), "reply:" + tag, "逐条回复非空")
        status = entry.get("evidence_status")
        report.need(status in ("到位", "未到位", "不适用"), "evidence_status:" + tag, "证据状态明确")
        implementation = entry.get("implementation_status")
        report.need(implementation in ("已修订", "无需改稿", "未完成"), "implementation_status:" + tag, "实际落实状态明确")
        sources = entry.get("evidence_sources", [])
        report.need(isinstance(sources, list) and all(isinstance(x, str) and x.strip() for x in sources), "evidence_sources_type:" + tag, "证据来源为真实定位字符串数组")
        if status == "到位":
            report.need(bool(sources), "evidence_sources:" + tag, "声称证据到位时给出来源定位")
        anchors = entry.get("anchors", [])
        report.need(isinstance(anchors, list), "anchors_type:" + tag, "修改定位为数组")
        if implementation == "已修订":
            report.need(bool(anchors), "anchors_present:" + tag, "声称已修订必须至少有一个真实定位")
        for anchor in anchors:
            before, after = anchor.get("before"), anchor.get("after")
            label = anchor.get("label")
            all_labels.append(label)
            report.need(isinstance(label, str) and bool(label.strip()), "anchor_label:" + tag, "定位标签非空")
            report.need(anchor.get("version") == data.get("revised_version"), "anchor_version:" + tag, "定位版本与当前修订版本一致")
            report.need(isinstance(anchor.get("section"), str) and bool(anchor.get("section").strip()), "anchor_section:" + tag, "定位含真实章节/段落")
            report.need(isinstance(before, str) and baseline.count(before) == 1, "before_anchor:" + tag, "改前锚点在指定基线中恰好出现一次")
            report.need(isinstance(after, str) and revised.count(after) == 1, "after_anchor:" + tag, "声称改动在修订稿中恰好出现一次")
            reply = entry.get("reply", "")
            reply_has_anchor = (isinstance(after, str) and after in reply) or (isinstance(label, str) and label in reply)
            report.need(reply_has_anchor, "reply_anchor:" + tag, "回复定位可反查")
        reply = entry.get("reply", "")
        if status == "未到位":
            report.need("未到位" in reply, "evidence_honesty:" + tag, "未到位证据不虚称已补")
            report.need(isinstance(entry.get("evidence_note"), str) and bool(entry.get("evidence_note").strip()), "evidence_note:" + tag, "说明无法补做原因或替代处理")
        editorial_claim = bool(re.search(r"已收到|已接收|全文返修完成", reply))
        experiment_claim = bool(re.search(r"已补充实验", reply)) and not (status == "到位" and bool(sources))
        report.need(not editorial_claim and not experiment_claim, "no_false_completion:" + tag, "无实验/编辑/全文虚假完成状态")
    report.need(len(all_labels) == len(set(all_labels)), "unique_anchor_labels", "定位标签全局唯一")

    missing = data.get("missing_evidence", [])
    report.need(isinstance(missing, list), "missing_evidence_list", "未到位证据清单为数组")
    missing_ids = []
    if isinstance(missing, list):
        for index, item in enumerate(missing):
            if not isinstance(item, dict):
                report.need(False, "missing_evidence_record:%d" % index, "未到位项必须为对象")
                continue
            tag = item.get("subrequest_id")
            missing_ids.append(tag)
            report.need(tag in comments, "missing_evidence_id:" + str(tag), "未到位项关联真实子要求")
            for key in ("required_evidence", "reason", "alternative"):
                report.need(isinstance(item.get(key), str) and bool(item.get(key).strip()), "missing_evidence_%s:%s" % (key, tag), "未到位证据、原因和替代说明完整")
    expected_missing = {entry["subrequest_id"] for entry in responses if entry.get("evidence_status") == "未到位"}
    report.need(len(missing_ids) == len(set(missing_ids)) and set(missing_ids) == expected_missing, "missing_evidence_exact", "未到位证据清单与回复状态逐项一致")

    conflicts = data.get("conflicts", [])
    report.need(isinstance(conflicts, list), "conflicts_list", "冲突记录为数组")
    for index, conflict in enumerate(conflicts if isinstance(conflicts, list) else []):
        if not isinstance(conflict, dict):
            report.need(False, "conflict_record:%d" % index, "冲突项必须为对象")
            continue
        ids = conflict.get("subrequest_ids", [])
        report.need(isinstance(ids, list) and len(ids) >= 2 and len(ids) == len(set(ids)) and set(ids) <= set(comments), "conflict_ids:%d" % index, "冲突保留至少两个真实子要求")
        report.need(isinstance(conflict.get("resolution"), str) and bool(conflict.get("resolution").strip()), "conflict_resolution:%d" % index, "冲突说明兼容方案或待作者/编辑决策")


def main():
    data, path = load()
    report = Report()
    audit_review(data, report)
    if any(key in data for key in ("case_json", "case_id", "output_contract", "result_json", "output_dir")):
        audit_delivery(data, path.parent.resolve(), report)
    return finish(data, path, report)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        sys.exit(2)
