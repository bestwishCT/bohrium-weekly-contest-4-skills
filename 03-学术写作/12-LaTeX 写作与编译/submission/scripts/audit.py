#!/usr/bin/env python3
import json
import re
import shlex
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path


class Report:
    def __init__(self):
        self.checks = []
        self.compilation = {
            "status": "not_executed",
            "verification": "unverified",
            "command": None,
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
    delivery_prefixes = ("case_", "output_", "result_", "delivery_", "contract_")
    partial = report.failures and all(
        c["code"].startswith(delivery_prefixes) for c in report.failures
    )
    status = "pass" if not report.failures else ("partial" if partial else "fail")
    out = {
        "artifact": str(path),
        "status": status,
        "compilation": report.compilation,
        "checks": report.checks,
        "note": "静态源码审计不等于TeX编译或官方环境验证。",
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


def audit_file(text, name, report):
    tokens = list(re.finditer(r"\\(begin|end)\{([^}]+)\}", text))
    begins = [m.group(2) for m in tokens if m.group(1) == "begin"]
    ends = [m.group(2) for m in tokens if m.group(1) == "end"]
    mismatch = next((m for m in tokens if Counter(begins) != Counter(ends)), None)
    report.need(
        Counter(begins) == Counter(ends),
        "environment_balance",
        "每个文件的环境begin/end多重集一致",
        name,
        line_at(text, mismatch.start()) if mismatch else 1,
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
        not stack and nesting_error is None,
        "environment_nesting",
        "每个文件独立闭合环境且嵌套顺序正确",
        name,
        line_at(text, nesting_error.start()) if nesting_error else 1,
    )

    depth = 0
    brace_error = None
    for match in re.finditer(r"(?<!\\)[{}]", text):
        depth += 1 if match.group(0) == "{" else -1
        if depth < 0 and brace_error is None:
            brace_error = match
    if depth != 0 and brace_error is None:
        brace_error = match if "match" in locals() else None
    report.need(
        depth == 0 and brace_error is None,
        "brace_balance",
        "未转义花括号配对",
        name,
        line_at(text, brace_error.start()) if brace_error else 1,
    )

    dollars = list(re.finditer(r"(?<!\\)\$", text))
    report.need(
        len(dollars) % 2 == 0,
        "dollar_math_balance",
        "行内/显示美元定界符配对",
        name,
        line_at(text, dollars[-1].start()) if len(dollars) % 2 else 1,
    )
    slash_ok = text.count("\\[") == text.count("\\]") and text.count("\\(") == text.count("\\)")
    report.need(slash_ok, "slash_math_balance", "LaTeX数学定界符配对", name, 1)


def audit_delivery(data, base, report):
    requested = any(k in data for k in ("case_id", "output_contract", "result_json", "output_dir"))
    if not requested:
        return
    case_id = data.get("case_id")
    contract = data.get("output_contract")
    report.need(isinstance(case_id, str) and bool(case_id.strip()), "case_id_present", "审计输入含真实case_id")
    report.need(isinstance(contract, dict), "output_contract_present", "保留原output_contract对象")
    result_ref = data.get("result_json")
    if not result_ref:
        report.need(False, "result_json_present", "合同适配审计必须指定result_json")
        return
    result_path = Path(result_ref)
    if not result_path.is_absolute():
        result_path = base / result_path
    result_path = result_path.resolve()
    output_dir = Path(data.get("output_dir", result_path.parent))
    if not output_dir.is_absolute():
        output_dir = base / output_dir
    output_dir = output_dir.resolve()
    report.need(within(result_path, output_dir), "output_location", "result.json位于声明的/app/output交付目录", display(result_path, base))
    if not result_path.is_file():
        report.need(False, "result_json_exists", "交付result.json真实存在", display(result_path, base))
        return
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        report.need(False, "result_json_valid", "交付result.json可读取: %s" % exc, display(result_path, base))
        return
    if not isinstance(result, dict):
        report.need(False, "result_json_object", "交付result.json顶层为对象", display(result_path, base))
        return
    report.need(result.get("case_id") == case_id, "case_id_match", "交付case_id与真实输入一致", display(result_path, base))
    report.need(result.get("output_contract") == contract, "output_contract_preserved", "交付保留原output_contract", display(result_path, base))
    missing = sorted(set(contract or {}) - set(result)) if isinstance(contract, dict) else []
    report.need(not missing, "contract_fields_complete", "交付顶层包含原合同全部字段: %s" % ", ".join(missing), display(result_path, base))


def audit_build(data, base, entry, report):
    build = data["build"]
    status = build.get("status")
    allowed = ("success", "failed", "blocked", "not_run", "not_executed", "unverified")
    report.need(status in allowed, "build_status_honest", "构建状态为真实枚举")
    compilation = report.compilation
    compilation["available_compilers"] = {
        name: shutil.which(name) for name in ("pdflatex", "xelatex", "lualatex", "latexmk") if shutil.which(name)
    }
    if status != "success":
        report.need(bool(build.get("reason")), "build_reason", "非成功说明未构建原因")
        compilation["reason"] = build.get("reason", "未执行编译")
        return

    raw_command = build.get("command")
    try:
        command = raw_command if isinstance(raw_command, list) else shlex.split(raw_command or "")
    except ValueError:
        command = []
    report.need(bool(command), "build_command", "成功声明必须有实际命令")
    allowed_executables = {"pdflatex", "xelatex", "lualatex", "latexmk"}
    executable = Path(command[0]).name if command else ""
    if not command or executable not in allowed_executables:
        report.need(False, "build_command_allowed", "仅执行声明的TeX编译器，不执行任意shell命令")
        compilation["reason"] = "编译器命令缺失或不受支持；未执行"
        return
    resolved_executable = shutil.which(command[0])
    if not resolved_executable:
        report.need(False, "build_compiler_unavailable", "声明success但编译器不可用，不能核验成功")
        compilation["reason"] = "编译器不可用；自报success未执行且未核验"
        return

    actual_command = [resolved_executable] + command[1:]
    compilation.update({"status": "executed", "command": actual_command})
    try:
        process = subprocess.run(
            actual_command,
            cwd=str(entry.parent),
            text=True,
            capture_output=True,
            timeout=300,
            check=False,
        )
        compilation.update({
            "exit_code": process.returncode,
            "stdout": process.stdout,
            "stderr": process.stderr,
            "verification": "executed_not_official_tex_validation",
        })
        report.need(process.returncode == 0, "build_exit_code", "实际编译命令退出码为0")
        if build.get("exit_code") is not None:
            report.need(build.get("exit_code") == process.returncode, "build_exit_code_record", "自报退出码与实际执行一致")
    except (OSError, subprocess.TimeoutExpired) as exc:
        compilation["reason"] = str(exc)
        report.need(False, "build_execution", "实际编译执行失败: %s" % exc)
        return

    pdf_ref = build.get("pdf", "")
    pdf = Path(pdf_ref)
    if not pdf.is_absolute():
        pdf = base / pdf
    report.need(pdf.is_file() and pdf.read_bytes().startswith(b"%PDF-"), "real_pdf", "实际成功后对应PDF具有真实签名")
    log_ref = build.get("execution_log", "")
    log = Path(log_ref)
    if not log.is_absolute():
        log = base / log
    report.need(log.is_file() and len(log.read_text(encoding="utf-8", errors="replace").strip()) > 0, "real_build_log", "成功记录附真实非空构建日志")


def main():
    data, p = load()
    report = Report()
    material_root = p.parent.resolve()
    entry = (material_root / data["main"]).resolve()
    if not within(entry, material_root):
        raise ValueError("main escapes material root")
    text = strip_comments(entry.read_text(encoding="utf-8"))
    entry_name = display(entry, material_root)
    report.need(
        "\\documentclass" in text and "\\begin{document}" in text and "\\end{document}" in text,
        "entry_structure",
        "真实主文件结构完整",
        entry_name,
        1,
    )
    report.need(
        text.find("\\begin{document}") >= 0 and text.find("\\begin{document}") < text.find("\\end{document}"),
        "environment_order:document",
        "document环境顺序正确",
        entry_name,
        1,
    )

    sources = {}
    labels = []
    references = []
    citations = []
    active = []

    def visit(path, origin=None, origin_line=None, include_name=None):
        path = path.resolve()
        name = display(path, material_root)
        if path in active:
            report.need(False, "include_cycle:" + (include_name or name), "include/input存在循环", origin, origin_line)
            return
        if path in sources:
            return
        if not within(path, material_root):
            report.need(False, "input_outside_root:" + (include_name or name), "include/input不得越出材料根目录", origin, origin_line)
            return
        if not path.is_file():
            report.need(False, "input_exists:" + (include_name or name), "include/input文件真实存在", origin, origin_line)
            return
        try:
            source_text = strip_comments(path.read_text(encoding="utf-8"))
        except OSError as exc:
            report.need(False, "input_readable:" + name, "include/input文件可读取: %s" % exc, origin or name, origin_line)
            return
        sources[path] = source_text
        active.append(path)
        audit_file(source_text, name, report)
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
            child = resolve_candidate(relative, path, entry, material_root)
            if within(child, material_root) and child.is_file():
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
        report.need(True, "unique_labels", "跨文件label不得重复")
    label_keys = set(seen)
    for key, name, line in references:
        report.need(key in label_keys, "undefined_label:" + key, "跨文件交叉引用有label", name, line)

    bib = (material_root / data["bib"]).resolve()
    keys = set()
    if within(bib, material_root) and bib.is_file():
        keys = set(re.findall(r"@[A-Za-z]+\s*\{\s*([^,\s]+)", bib.read_text(encoding="utf-8")))
        report.need(True, "bib_exists", "bib文件真实存在", display(bib, material_root))
    else:
        report.need(False, "bib_exists", "bib文件存在且不越出材料根目录", data["bib"])
    for key, name, line in citations:
        report.need(key in keys, "undefined_citation:" + key, "引文键存在bib条目", name, line)

    for path, source_text in sources.items():
        name = display(path, material_root)
        for match in re.finditer(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", source_text):
            image = match.group(1).strip()
            line = line_at(source_text, match.start())
            q = resolve_candidate(image, path, entry, material_root)
            candidates = [q] if q.suffix else [q.with_suffix(ext) for ext in (".pdf", ".png", ".jpg", ".jpeg", ".eps")]
            if not within(q, material_root):
                report.need(False, "graphic_outside_root:" + image, "图片不得越出材料根目录", name, line)
            else:
                report.need(any(x.is_file() for x in candidates), "graphic_exists:" + image, "includegraphics图片真实存在", name, line)

    audit_build(data, material_root, entry, report)
    audit_delivery(data, material_root, report)
    return finish(data, p, report)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        sys.exit(2)
