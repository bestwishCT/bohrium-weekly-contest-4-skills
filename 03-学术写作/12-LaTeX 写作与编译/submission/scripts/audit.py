#!/usr/bin/env python3
"""Offline LaTeX source, build-record and delivery auditor.

Static source checks do not prove TeX compilation or official-environment validity.
Only explicitly declared successful builds are executed, without a shell.
"""
import json
import re
import shlex
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path


COMPILERS = ("pdflatex", "xelatex", "lualatex", "latexmk", "tectonic", "bibtex", "biber")
ALLOWED_EXECUTABLES = set(COMPILERS)
PATH_KEY = re.compile(
    r"(?:^|_)(?:deliverables?|artifacts?|sources?|source_files?|files?|pdf|previews?|"
    r"tex|bib|editable|documents?|outputs?)(?:$|_)", re.I
)


class Report:
    def __init__(self):
        self.checks = []
        self.compilation = {
            "status": "not_executed",
            "verification": "unverified",
            "command": None,
            "commands": [],
            "rounds": [],
            "exit_code": None,
        }

    def need(self, ok, code, message, file=None, line=None, **details):
        check = {"code": code, "ok": bool(ok), "message": message}
        if file is not None:
            check["file"] = str(file)
        if line is not None:
            check["line"] = int(line)
        check.update(details)
        self.checks.append(check)

    @property
    def failures(self):
        return [c for c in self.checks if not c["ok"]]


def finish(data, path, report):
    delivery_prefixes = (
        "case_", "output_", "result_", "delivery_", "contract_", "deliverable_",
        "required_", "main_", "bib_", "build_", "real_pdf", "pdf_", "real_build_log",
    )
    partial = report.failures and all(c["code"].startswith(delivery_prefixes) for c in report.failures)
    status = "pass" if not report.failures else ("partial" if partial else "fail")
    out = {
        "artifact": str(path),
        "case_id": data.get("case_id"),
        "status": status,
        "compilation": report.compilation,
        "checks": report.checks,
        "note": "静态源码与文件结构审计不等于TeX编译、PDF渲染或官方环境验证。",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if not report.failures else 1


def load():
    if len(sys.argv) != 2:
        raise ValueError("usage: audit.py INPUT.json")
    p = Path(sys.argv[1]).resolve()
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("top-level JSON must be object")
    return data, p


def strip_comments(text):
    return "\n".join(re.sub(r"(?<!\\)%.*", "", x) for x in text.splitlines())


def line_at(text, offset):
    return text.count("\n", 0, offset) + 1


def within(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def display(path, root):
    return str(path.relative_to(root)) if within(path, root) else str(path)


def resolve_path(value, base):
    path = Path(str(value))
    return (path if path.is_absolute() else base / path).resolve()


def resolve_candidate(name, current, entry, root):
    candidate = Path(name)
    if candidate.is_absolute():
        return candidate.resolve()
    primary = (current.parent / candidate).resolve()
    alternate = (entry.parent / candidate).resolve()
    if not within(primary, root):
        return primary
    if primary.exists() or not within(alternate, root) or not alternate.exists():
        return primary
    return alternate


def pdf_info(path):
    """Return (signature_ok, page_count). This is not a rendering check."""
    try:
        raw = path.read_bytes()
    except OSError:
        return False, 0
    signature = raw.startswith(b"%PDF-") and b"%%EOF" in raw[-2048:]
    pages = len(re.findall(rb"/Type\s*/Page(?![sA-Za-z])", raw))
    return signature, pages


def audit_pdf(path, name, report, code_prefix="real_pdf"):
    signature, pages = pdf_info(path) if path.is_file() else (False, 0)
    report.need(path.is_file() and signature, code_prefix, "PDF真实存在、可读且具有完整签名/EOF", name)
    report.need(signature and pages > 0, "pdf_pages_readable", "PDF至少含一个可机械识别的页面对象", name, page_count=pages)


def audit_file(text, name, report):
    tokens = list(re.finditer(r"\\(begin|end)\{([^}]+)\}", text))
    begins = [m.group(2) for m in tokens if m.group(1) == "begin"]
    ends = [m.group(2) for m in tokens if m.group(1) == "end"]
    mismatch = next((m for m in tokens if Counter(begins) != Counter(ends)), None)
    report.need(
        Counter(begins) == Counter(ends), "environment_balance", "每个文件的环境begin/end多重集一致",
        name, line_at(text, mismatch.start()) if mismatch else 1,
    )
    stack = []
    nesting_error = None
    for match in tokens:
        kind, env = match.group(1), match.group(2)
        if kind == "begin":
            stack.append((env, match))
        elif not stack or stack[-1][0] != env:
            nesting_error = nesting_error or match
            if stack:
                stack.pop()
        else:
            stack.pop()
    if stack and nesting_error is None:
        nesting_error = stack[-1][1]
    report.need(
        not stack and nesting_error is None, "environment_nesting", "每个文件独立闭合环境且嵌套顺序正确",
        name, line_at(text, nesting_error.start()) if nesting_error else 1,
    )

    depth = 0
    brace_error = None
    last_brace = None
    for match in re.finditer(r"(?<!\\)[{}]", text):
        last_brace = match
        depth += 1 if match.group(0) == "{" else -1
        if depth < 0 and brace_error is None:
            brace_error = match
    if depth != 0 and brace_error is None:
        brace_error = last_brace
    report.need(
        depth == 0 and brace_error is None, "brace_balance", "未转义花括号配对",
        name, line_at(text, brace_error.start()) if brace_error else 1,
    )

    dollars = list(re.finditer(r"(?<!\\)\$", text))
    report.need(
        len(dollars) % 2 == 0, "dollar_math_balance", "行内/显示美元定界符配对",
        name, line_at(text, dollars[-1].start()) if len(dollars) % 2 else 1,
    )
    slash_ok = text.count("\\[") == text.count("\\]") and text.count("\\(") == text.count("\\)")
    report.need(slash_ok, "slash_math_balance", "LaTeX数学定界符配对", name, 1)


def original_case(data, base, report):
    """Load an optional immutable case record and reject conflicting copies."""
    case = data
    ref = data.get("case_json")
    if ref:
        path = resolve_path(ref, base)
        if not within(path, base):
            report.need(False, "case_json_outside_root", "原case文件不得越出材料根目录", display(path, base))
        elif not path.is_file():
            report.need(False, "case_json_exists", "原case文件真实存在", display(path, base))
        else:
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise ValueError("case top-level must be object")
                case = loaded
            except (OSError, ValueError) as exc:
                report.need(False, "case_json_valid", "原case可读取: %s" % exc, display(path, base))
    for key in ("case_id", "output_contract"):
        if ref and key in data and key in case:
            report.need(data[key] == case[key], "case_%s_consistent" % key, "审计副本与原case一致")
    return case


def iter_contract_paths(value, key=""):
    """Yield declared path values, retaining nested contract fields."""
    if isinstance(value, dict):
        if "path" in value and isinstance(value["path"], str):
            yield key or "path", value["path"]
        for child_key, child in value.items():
            if child_key != "path":
                yield from iter_contract_paths(child, child_key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_contract_paths(child, "%s[%d]" % (key, index))
    elif isinstance(value, str) and PATH_KEY.search(key):
        yield key, value


def audit_delivery(data, case, base, report):
    requested = data.get("synthetic") is not True or any(k in data for k in ("case_json", "case_id", "output_contract", "result_json", "output_dir"))
    if not requested:
        return None
    case_id = case.get("case_id")
    contract = case.get("output_contract")
    report.need(isinstance(case_id, str) and bool(case_id.strip()), "case_id_present", "审计输入含真实case_id")
    report.need(isinstance(contract, dict), "output_contract_present", "保留原output_contract对象")

    allowed_root = resolve_path(data.get("allowed_output_dir", "/app/output"), base)
    if "allowed_output_dir" in data:
        report.need(
            data.get("synthetic") is True and isinstance(data.get("allowed_output_dir"), str) and bool(data.get("allowed_output_dir").strip()) and within(allowed_root, base),
            "output_root_override", "非默认输出根仅允许synthetic测试显式覆盖，且必须位于审计材料根目录内",
        )
    output_dir = resolve_path(data.get("output_dir", allowed_root), base)
    report.need(output_dir == allowed_root, "output_root", "输出目录必须等于允许根目录（默认/app/output）", str(output_dir))
    result_ref = data.get("result_json")
    report.need(bool(result_ref), "result_json_present", "合同适配审计必须指定result_json")
    if not result_ref:
        return allowed_root
    result_path = resolve_path(result_ref, base)
    report.need(within(result_path, allowed_root), "output_location", "result.json位于允许输出根目录", display(result_path, base))
    if not within(result_path, allowed_root) or not result_path.is_file():
        report.need(False, "result_json_exists", "交付result.json真实存在且路径不逃逸", display(result_path, base))
        return allowed_root
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        report.need(False, "result_json_valid", "交付result.json可读取: %s" % exc, display(result_path, base))
        return allowed_root
    if not isinstance(result, dict):
        report.need(False, "result_json_object", "交付result.json顶层为对象", display(result_path, base))
        return allowed_root
    report.need(result.get("case_id") == case_id, "case_id_match", "交付case_id与真实输入一致", display(result_path, base))
    report.need(result.get("output_contract") == contract, "output_contract_preserved", "交付保留原output_contract", display(result_path, base))
    missing = sorted(set(contract or {}) - set(result)) if isinstance(contract, dict) else []
    report.need(not missing, "contract_fields_complete", "交付顶层包含原合同全部字段: %s" % ", ".join(missing), display(result_path, base))

    if isinstance(contract, dict):
        for key in contract:
            if key not in result:
                report.need(False, "deliverable_field_missing:" + key, "合同字段缺失，无法核验产物", key)
                continue
            paths = list(iter_contract_paths(result[key], key))
            if PATH_KEY.search(key) and not paths:
                report.need(False, "deliverable_path_present:" + key, "路径字段必须含非空字符串或path对象", key)
            for path_key, ref_value in paths:
                if not ref_value.strip():
                    report.need(False, "deliverable_path_present:" + path_key, "产物路径不得为空", path_key)
                    continue
                artifact = resolve_path(ref_value, allowed_root)
                safe = within(artifact, allowed_root)
                readable = safe and artifact.is_file()
                if readable:
                    try:
                        with artifact.open("rb") as stream:
                            stream.read(1)
                    except OSError:
                        readable = False
                report.need(readable, "deliverable_exists:" + path_key, "合同源文件/成品真实存在可读且不逃逸", path_key, path=str(artifact))
                if readable and (artifact.suffix.lower() == ".pdf" or "pdf" in path_key.lower()):
                    audit_pdf(artifact, path_key, report, "deliverable_pdf:" + path_key)
    return allowed_root


def parse_command(raw):
    if isinstance(raw, dict):
        raw = raw.get("command")
    if isinstance(raw, list):
        if not all(isinstance(x, str) for x in raw):
            return []
        return list(raw)
    if isinstance(raw, str):
        try:
            return shlex.split(raw)
        except ValueError:
            return []
    return []


def audit_build_artifacts(build, base, allowed_output, report):
    """Validate claimed products even when their commands cannot be executed."""
    roots = [base] + ([allowed_output] if allowed_output else [])
    pdf_ref = build.get("pdf")
    report.need(bool(pdf_ref), "real_pdf_path", "成功声明必须提供实际PDF路径")
    if pdf_ref:
        pdf = resolve_path(pdf_ref, base)
        if not any(within(pdf, root) for root in roots):
            report.need(False, "real_pdf_outside_root", "PDF路径不得越出材料或允许输出根目录", str(pdf))
        else:
            audit_pdf(pdf, display(pdf, base), report)
    log_ref = build.get("execution_log")
    report.need(bool(log_ref), "real_build_log_path", "成功声明必须提供实际构建日志路径")
    if log_ref:
        log = resolve_path(log_ref, base)
        try:
            log_ok = any(within(log, root) for root in roots) and log.is_file() and bool(log.read_text(encoding="utf-8", errors="replace").strip())
        except OSError:
            log_ok = False
        report.need(log_ok, "real_build_log", "成功记录附路径安全、真实非空构建日志")


def audit_build(data, base, entry, allowed_output, report):
    build = data.get("build")
    compilation = report.compilation
    compilation["available_compilers"] = {name: shutil.which(name) for name in COMPILERS if shutil.which(name)}
    if not isinstance(build, dict):
        report.need(False, "build_present", "build对象缺失或类型错误；未执行编译")
        compilation["reason"] = "build对象缺失或类型错误"
        return
    status = build.get("status")
    allowed = ("success", "failed", "blocked", "not_run", "not_executed", "unverified")
    report.need(status in allowed, "build_status_honest", "构建状态为真实枚举")
    if status != "success":
        report.need(bool(build.get("reason")), "build_reason", "非成功说明未构建原因")
        if allowed_output is not None:
            report.need(False, "build_delivery_unverified", "合同交付必须真实执行并编译通过；当前状态只能partial", declared_status=status)
        compilation["reason"] = build.get("reason", "未执行编译")
        return
    if not within(entry, base) or not entry.is_file():
        report.need(False, "build_entry_valid", "主文件缺失或越界时不得执行编译")
        compilation["reason"] = "真实主文件缺失或越界；未执行"
        return

    raw_commands = build.get("commands")
    if raw_commands is None:
        raw_commands = [build.get("command")]
    if not isinstance(raw_commands, list) or not raw_commands:
        raw_commands = []
    commands = [parse_command(raw) for raw in raw_commands]
    report.need(bool(commands) and all(commands), "build_command", "成功声明必须有一轮或多轮结构化实际命令")
    invalid = [
        cmd[0] if cmd else "<empty>" for cmd in commands
        if not cmd or Path(cmd[0]).name not in ALLOWED_EXECUTABLES or Path(cmd[0]).name != cmd[0]
    ]
    report.need(not invalid, "build_command_allowed", "仅允许TeX/BibTeX/Biber/Tectonic等白名单工具，shell=False", invalid=invalid)
    if invalid or not commands or not all(commands):
        compilation["reason"] = "编译命令缺失或不受支持；未执行"
        return

    planned = []
    unavailable = []
    for command in commands:
        resolved = shutil.which(command[0])
        if not resolved:
            unavailable.append(command[0])
        planned.append([resolved] + command[1:] if resolved else command)
    compilation["commands"] = planned
    compilation["command"] = planned[0] if planned else None
    if unavailable:
        report.need(False, "build_compiler_unavailable", "声明success但工具不可用，不能核验成功", unavailable=sorted(set(unavailable)))
        compilation["reason"] = "一个或多个编译工具不可用；自报success未执行且未核验"
        compilation["rounds"] = [
            {"round": i + 1, "command": cmd, "status": "not_executed", "exit_code": None, "stdout": "", "stderr": ""}
            for i, cmd in enumerate(planned)
        ]
        return

    compilation["status"] = "executed"
    compilation["verification"] = "executed_not_official_tex_validation"
    all_zero = True
    for index, command in enumerate(planned):
        round_record = {"round": index + 1, "command": command, "status": "not_executed", "exit_code": None}
        if not all_zero:
            round_record["reason"] = "前一轮失败，本轮未执行"
            compilation["rounds"].append(round_record)
            continue
        try:
            process = subprocess.run(
                command, cwd=str(entry.parent), text=True, capture_output=True,
                timeout=300, check=False, shell=False,
            )
            round_record.update({
                "status": "executed", "exit_code": process.returncode,
                "stdout": process.stdout, "stderr": process.stderr,
            })
            compilation["exit_code"] = process.returncode
            report.need(process.returncode == 0, "build_exit_code:%d" % (index + 1), "实际编译轮次退出码为0")
            all_zero = process.returncode == 0
        except (OSError, subprocess.TimeoutExpired) as exc:
            round_record.update({"status": "execution_failed", "reason": str(exc), "stdout": "", "stderr": ""})
            report.need(False, "build_execution:%d" % (index + 1), "实际编译执行失败: %s" % exc)
            all_zero = False
        compilation["rounds"].append(round_record)
    declared_rounds = build.get("exit_codes")
    if declared_rounds is not None:
        actual = [item.get("exit_code") for item in compilation["rounds"]]
        report.need(declared_rounds == actual, "build_exit_codes_record", "自报每轮退出码与实际执行一致")
    elif build.get("exit_code") is not None:
        report.need(build.get("exit_code") == compilation.get("exit_code"), "build_exit_code_record", "自报退出码与实际最后执行轮一致")


def audit_bib_without_main(data, base, report):
    """Keep missing-main failures from hiding the independent bib checks."""
    bib_ref = data.get("bib")
    report.need(isinstance(bib_ref, str) and bool(bib_ref.strip()), "bib_present", "bib字段必须明确提供；无参考文献时仍须按case说明")
    bib = resolve_path(bib_ref or "refs.bib", base)
    exists = bool(bib_ref) and within(bib, base) and bib.is_file()
    report.need(exists, "bib_exists", "bib文件存在且不越出材料根目录", str(bib_ref))


def audit_source(data, base, report):
    main_ref = data.get("main")
    report.need(isinstance(main_ref, str) and bool(main_ref.strip()), "main_present", "main字段必须指向真实TeX入口")
    entry = resolve_path(main_ref or "main.tex", base)
    if not main_ref or not within(entry, base) or not entry.is_file():
        report.need(False, "main_exists", "真实主文件存在、可读且不越出材料根目录", display(entry, base))
        audit_bib_without_main(data, base, report)
        return entry
    try:
        text = strip_comments(entry.read_text(encoding="utf-8"))
    except OSError as exc:
        report.need(False, "main_readable", "主文件可读取: %s" % exc, display(entry, base))
        audit_bib_without_main(data, base, report)
        return entry
    entry_name = display(entry, base)
    report.need(
        "\\documentclass" in text and "\\begin{document}" in text and "\\end{document}" in text,
        "entry_structure", "真实主文件结构完整", entry_name, 1,
    )
    report.need(
        0 <= text.find("\\begin{document}") < text.find("\\end{document}"),
        "environment_order:document", "document环境顺序正确", entry_name, 1,
    )

    sources = {}
    labels, references, citations = [], [], []
    active = []

    def visit(path, origin=None, origin_line=None, include_name=None):
        path = path.resolve()
        name = display(path, base)
        if path in active:
            report.need(False, "include_cycle:" + (include_name or name), "include/input存在循环", origin, origin_line)
            return
        if not within(path, base):
            report.need(False, "input_outside_root:" + (include_name or name), "include/input不得越出材料根目录", origin, origin_line)
            return
        if not path.is_file():
            report.need(False, "input_exists:" + (include_name or name), "include/input文件真实存在", origin, origin_line)
            return
        if path not in sources:
            try:
                sources[path] = strip_comments(path.read_text(encoding="utf-8"))
            except OSError as exc:
                report.need(False, "input_readable:" + name, "include/input文件可读取: %s" % exc, origin or name, origin_line)
                return
            audit_file(sources[path], name, report)
        source_text = sources[path]
        active.append(path)
        # Deliberately collect every inclusion: repeated includes can repeat labels.
        for match in re.finditer(r"\\label\{([^}]+)\}", source_text):
            labels.append((match.group(1), name, line_at(source_text, match.start())))
        for match in re.finditer(r"\\(?:ref|eqref|pageref|autoref|[Cc]ref)\{([^}]+)\}", source_text):
            references.append((match.group(1), name, line_at(source_text, match.start())))
        for match in re.finditer(r"\\cite\w*\{([^}]+)\}", source_text):
            for key in match.group(1).split(","):
                citations.append((key.strip(), name, line_at(source_text, match.start())))
        for match in re.finditer(r"\\(?:input|include)\{([^}]+)\}", source_text):
            inc = match.group(1).strip()
            line = line_at(source_text, match.start())
            relative = inc if inc.endswith(".tex") else inc + ".tex"
            child = resolve_candidate(relative, path, entry, base)
            if within(child, base) and child.is_file():
                report.need(True, "input_exists:" + inc, "include/input文件真实存在", name, line)
            visit(child, name, line, inc)
        active.pop()

    visit(entry)
    seen = {}
    duplicates = []
    for key, name, line in labels:
        if key in seen:
            duplicates.append((key, name, line))
        else:
            seen[key] = (name, line)
    if duplicates:
        for key, name, line in duplicates:
            report.need(False, "unique_labels", "重复label %s；首次位置%s:%s" % ((key,) + seen[key]), name, line)
    else:
        report.need(True, "unique_labels", "跨文件及重复include的每次label不得重复")
    label_keys = set(seen)
    for key, name, line in references:
        report.need(key in label_keys, "undefined_label:" + key, "跨文件交叉引用有label", name, line)

    bib_ref = data.get("bib")
    report.need(isinstance(bib_ref, str) and bool(bib_ref.strip()), "bib_present", "bib字段必须明确提供；无参考文献时仍须按case说明")
    keys = set()
    bib = resolve_path(bib_ref or "refs.bib", base)
    if bib_ref and within(bib, base) and bib.is_file():
        try:
            keys = set(re.findall(r"@[A-Za-z]+\s*\{\s*([^,\s]+)", bib.read_text(encoding="utf-8")))
            report.need(True, "bib_exists", "bib文件真实存在", display(bib, base))
        except OSError as exc:
            report.need(False, "bib_readable", "bib文件可读取: %s" % exc, display(bib, base))
    else:
        report.need(False, "bib_exists", "bib文件存在且不越出材料根目录", str(bib_ref))
    for key, name, line in citations:
        report.need(key in keys, "undefined_citation:" + key, "引文键存在bib条目", name, line)

    for path, source_text in sources.items():
        name = display(path, base)
        for match in re.finditer(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", source_text):
            image = match.group(1).strip()
            line = line_at(source_text, match.start())
            q = resolve_candidate(image, path, entry, base)
            candidates = [q] if q.suffix else [q.with_suffix(ext) for ext in (".pdf", ".png", ".jpg", ".jpeg", ".eps")]
            if not within(q, base):
                report.need(False, "graphic_outside_root:" + image, "图片不得越出材料根目录", name, line)
            else:
                report.need(any(x.is_file() for x in candidates), "graphic_exists:" + image, "includegraphics图片真实存在", name, line)
    return entry


def main():
    data, p = load()
    report = Report()
    material_root = p.parent.resolve()
    case = original_case(data, material_root, report)
    entry = audit_source(data, material_root, report)
    allowed_output = audit_delivery(data, case, material_root, report)
    # Delivery is audited even when source/build fields are absent or broken.
    audit_build(data, material_root, entry, allowed_output, report)
    if isinstance(data.get("build"), dict) and data["build"].get("status") == "success":
        audit_build_artifacts(data["build"], material_root, allowed_output, report)
    output_data = dict(data)
    output_data["case_id"] = case.get("case_id", data.get("case_id"))
    return finish(output_data, p, report)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        # Only malformed top-level invocation/JSON is an invocation error. Missing
        # case fields are handled above so prior checks and partial results survive.
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        sys.exit(2)
