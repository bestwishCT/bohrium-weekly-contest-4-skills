#!/usr/bin/env python3
"""Deterministic conservation, terminology and case-contract audit (stdlib only)."""
import json
import re
import sys
from collections import Counter, defaultdict
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


def numbers(text):
    return re.findall(r"(?<![A-Za-z])[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?%?", text or "")


def token_count(text, token):
    if not token:
        return 0
    if re.match(r"^[\w .-]+$", token, re.ASCII):
        return len(re.findall(r"(?<![\w])" + re.escape(token) + r"(?![\w])", text, re.ASCII))
    return text.count(token)


AUTO_PATTERNS = {
    "citations": re.compile(r"\[(?:\d+)(?:\s*(?:,|[-–])\s*\d+)*\]"),
    "figure_table_refs": re.compile(r"(?:\b(?:Fig(?:ure)?|Table)\s*\.?\s*[A-Za-z]?\d+(?:[.-]\d+)*|[图表]\s*[A-Za-z]?\d+(?:[.-]\d+)*)", re.I),
    "math": re.compile(r"\$\$.*?\$\$|\$[^$\n]+\$|\\\(.*?\\\)|\\\[.*?\\\]", re.S),
    "latex_commands": re.compile(r"\\[A-Za-z]+(?:\s*\[[^\]\n]*\])?(?:\s*\{[^{}\n]*\})?"),
    "code": re.compile(r"```.*?```|`[^`\n]+`", re.S),
    "links": re.compile(r"https?://[^\s<>()]+"),
}


def automatic_tokens(text):
    return {kind: Counter(pattern.findall(text or "")) for kind, pattern in AUTO_PATTERNS.items()}


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
    """JSON equality where true and 1 are distinct values."""
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
    """Validate the declared JSON-schema subset without dropping nested rules."""
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


def audit_terminology(data, original, polished, report):
    terms = data.get("terminology", [])
    report.need(isinstance(terms, list), "terminology_list", "术语表为数组")
    if not isinstance(terms, list):
        return
    preferred_by_concept = defaultdict(set)
    for index, term in enumerate(terms):
        if not isinstance(term, dict):
            report.need(False, "terminology_record:%d" % index, "术语记录必须为对象")
            continue
        concept = str(term.get("concept") or "term[%d]" % index)
        preferred = term.get("preferred")
        report.need(isinstance(preferred, str) and bool(preferred.strip()), "terminology_preferred_declared:" + concept, "术语首选译法非空")
        if isinstance(preferred, str):
            preferred_by_concept[concept].add(preferred)
        forbidden = term.get("forbidden", [])
        report.need(isinstance(forbidden, list) and all(isinstance(x, str) for x in forbidden), "terminology_forbidden_declared:" + concept, "禁用/待统一写法为字符串数组")
        if not isinstance(preferred, str) or not preferred or not isinstance(forbidden, list):
            continue
        relevant = token_count(original, preferred) > 0 or any(token_count(original, x) for x in forbidden)
        if relevant:
            report.need(token_count(polished, preferred) > 0, "terminology_preferred:" + concept, "相关概念在润色稿使用首选术语")
        for variant in forbidden:
            report.need(token_count(polished, variant) == 0, "terminology_forbidden:" + concept, "禁用/待统一写法未残留", variant=variant)
        expansion = term.get("expansion")
        abbreviation = term.get("abbreviation")
        if expansion and abbreviation and token_count(polished, abbreviation):
            report.need(token_count(polished, expansion) > 0 and polished.find(expansion) <= polished.find(abbreviation), "terminology_first_expansion:" + concept, "缩写首次出现前已有指定展开形式")
    for concept, choices in preferred_by_concept.items():
        report.need(len(choices) == 1, "terminology_conflict:" + concept, "同一概念的首选译法无冲突", choices=sorted(choices))


def main():
    data, path = load()
    report = Report()
    original, polished = data["original"], data["polished"]
    if not isinstance(original, str) or not isinstance(polished, str):
        raise TypeError("original and polished must be strings")
    report.need(bool(polished.strip()), "complete_text", "完整润色文本非空")
    report.need(Counter(numbers(original)) == Counter(numbers(polished)), "numbers_conserved", "数字多重集保持")
    auto_original, auto_polished = automatic_tokens(original), automatic_tokens(polished)
    for kind in AUTO_PATTERNS:
        report.need(auto_original[kind] == auto_polished[kind], "auto_protected:" + kind, "自动识别的不可变量多重集保持")
    for token in data.get("protected_tokens", []):
        report.need(original.count(token) == polished.count(token), "protected:" + token, "显式引用/公式/图表标记计数保持")
    for token in data.get("semantic_tokens", []):
        report.need(token_count(original, token) == token_count(polished, token), "semantic:" + token, "限定/否定词按完整词计数保持；范围仍须人工回读")
    edits = data["edits"]
    report.need(isinstance(edits, list), "edits_list", "逐句修改为数组")
    ids = [entry["sentence_id"] for entry in edits]
    report.need(len(ids) == len(set(ids)), "unique_edit_ids", "句ID唯一")
    rebuilt = original
    for entry in edits:
        tag = entry["sentence_id"]
        before, after = entry["before"], entry["after"]
        report.need(original.count(before) == 1, "before_unique:" + tag, "改前锚点在原文中恰好出现一次")
        report.need(after in polished, "after:" + tag, "改文进入完整文本")
        report.need(bool(entry["type"] and entry["reason"]), "reason:" + tag, "编辑类型和具体理由存在")
        if before in rebuilt:
            rebuilt = rebuilt.replace(before, after, 1)
    report.need(rebuilt == polished, "complete_text_reconstruction", "完整润色稿可由原稿和已登记编辑精确重建，无漏记差异")
    audit_terminology(data, original, polished, report)
    if any(key in data for key in ("case_json", "case_id", "output_contract", "result_json", "output_dir")):
        audit_delivery(data, path.parent.resolve(), report)
    return finish(data, path, report)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        sys.exit(2)
