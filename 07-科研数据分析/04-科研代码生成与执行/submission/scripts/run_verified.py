#!/usr/bin/env python3
"""Reviewed-code execution gate with timeout, audit and independent validators."""
import argparse
import ast
import csv
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

VERSION = "1.0.0"
UTC = lambda: datetime.now(timezone.utc).isoformat()
ALLOWED_MODULES = {"math", "statistics", "json", "decimal", "collections", "itertools", "functools", "datetime"}
DENIED_CALLS = {"open", "eval", "exec", "compile", "__import__", "input", "breakpoint", "exit", "quit", "globals", "locals", "vars", "dir", "getattr", "setattr", "delattr", "memoryview", "help"}
DENIED_ATTRIBUTES = {"system", "popen", "spawn", "fork", "remove", "unlink", "rmdir", "write", "write_text", "write_bytes", "read_text", "read_bytes", "open", "connect", "urlopen", "request", "socket"}
MAX_CODE_BYTES = 200000
MAX_RESULT_BYTES = 256000
MAX_LOG_BYTES = 65536
DRIVER = r'''
import json, sys
code_path, payload_path, result_path = sys.argv[1:4]
with open(payload_path, "r", encoding="utf-8") as f:
    payload = json.load(f)
with open(code_path, "r", encoding="utf-8") as f:
    code = f.read()
namespace = {"ROWS": payload["rows"], "CONFIG": payload.get("config", {})}
try:
    exec(compile(code, code_path, "exec"), namespace)
    if "RESULT" not in namespace:
        raise ValueError("generated code must set RESULT")
    encoded = json.dumps(namespace["RESULT"], ensure_ascii=False, allow_nan=False)
    if len(encoded.encode("utf-8")) > 256000:
        raise ValueError("RESULT exceeds 256000 bytes")
    with open(result_path, "w", encoding="utf-8") as f:
        f.write(encoded)
except Exception as exc:
    sys.stderr.write("GENERATED_CODE_ERROR %s: %s\n" % (type(exc).__name__, str(exc)))
    sys.exit(20)
'''


class GateError(Exception):
    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    return sha256_bytes(path.read_bytes())


def dump_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def static_gate(source):
    if len(source.encode("utf-8")) > MAX_CODE_BYTES:
        raise GateError("generated code exceeds size limit", 3)
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise GateError("generated code syntax error: %s" % exc, 3)
    nodes = list(ast.walk(tree))
    if len(nodes) > 5000:
        raise GateError("generated code AST too large", 3)
    problems = []
    for node in nodes:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name.split(".")[0] for alias in node.names]
            if isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            if any(name not in ALLOWED_MODULES for name in names) or getattr(node, "level", 0):
                problems.append("line %s unauthorized import" % node.lineno)
        if isinstance(node, ast.Name) and (node.id.startswith("__") or node.id.endswith("__")):
            problems.append("line %s dunder name %s" % (node.lineno, node.id))
        if isinstance(node, ast.Attribute) and (node.attr.startswith("__") or node.attr.endswith("__") or node.attr in DENIED_ATTRIBUTES):
            problems.append("line %s forbidden attribute %s" % (node.lineno, node.attr))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in DENIED_CALLS:
            problems.append("line %s forbidden call %s" % (node.lineno, node.func.id))
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            problems.append("line %s global/nonlocal not allowed" % node.lineno)
        if isinstance(node, ast.Str) and len(node.s) > 50000:
            problems.append("line %s string literal too large" % node.lineno)
    if problems:
        raise GateError("static safety review failed: " + "; ".join(problems[:5]), 3)
    return {"ast_nodes": len(nodes), "allowed_imports": sorted(ALLOWED_MODULES), "no_filesystem_or_process_api": True}


def load_csv(path, required):
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise GateError("CSV header absent or duplicated")
        missing = [name for name in required if name not in reader.fieldnames]
        if missing:
            raise GateError("required columns missing: %s" % missing)
        rows = list(reader)
    if not rows or any(None in row or any(value is None for value in row.values()) for row in rows):
        raise GateError("empty or malformed CSV rows")
    return rows


def numeric_validators(rows, validations, missing_tokens):
    evidence = []
    all_ok = True
    for rule in validations:
        result_key, op = rule.get("result_key"), rule.get("op")
        selected = rows
        if "where_column" in rule:
            selected = [row for row in selected if row.get(rule["where_column"]) == rule.get("equals")]
        column = rule.get("column")
        texts = [row.get(column, "") for row in selected] if column else []
        valid = []
        invalid = 0
        for text in texts:
            if text in missing_tokens:
                continue
            if len(text) > 128:
                invalid += 1
                continue
            try:
                value = Decimal(text)
                if not value.is_finite():
                    raise InvalidOperation
                valid.append(value)
            except (InvalidOperation, ValueError):
                invalid += 1
        if op == "count_rows":
            expected = Decimal(len(selected))
        elif op == "count_missing":
            expected = Decimal(sum(text in missing_tokens for text in texts))
        elif op == "count_valid":
            expected = Decimal(len(valid))
        elif op == "mean":
            if not valid:
                raise GateError("validator mean has no valid values: %s" % result_key, 2)
            with localcontext() as ctx:
                ctx.prec = 45
                expected = sum(valid, Decimal(0)) / Decimal(len(valid))
        elif op == "sum":
            expected = sum(valid, Decimal(0))
        elif op == "min":
            expected = min(valid) if valid else None
        elif op == "max":
            expected = max(valid) if valid else None
        elif op == "unique_count":
            expected = Decimal(len(set(texts)))
        else:
            raise GateError("unsupported independent validator: %s" % op, 2)
        evidence.append({"result_key": result_key, "op": op, "expected": str(expected) if expected is not None else None, "valid_values": len(valid), "invalid_nonmissing_values": invalid})
    return evidence


def compare(actual, evidence, tolerance):
    checks, ok = [], True
    for item in evidence:
        key = item["result_key"]
        actual_value = actual.get(key, "__MISSING_RESULT_KEY__") if isinstance(actual, dict) else "__RESULT_NOT_OBJECT__"
        passed, difference = False, None
        if item["expected"] is None:
            passed = actual_value is None
        elif not isinstance(actual_value, bool) and isinstance(actual_value, (int, float)):
            try:
                difference = abs(Decimal(str(actual_value)) - Decimal(item["expected"]))
                passed = math.isfinite(float(actual_value)) and difference <= Decimal(str(tolerance))
            except (InvalidOperation, ValueError, OverflowError):
                passed = False
        if not passed:
            ok = False
        checks.append({**item, "actual": actual_value, "decimal_absolute_difference": str(difference) if difference is not None else None, "passed": passed, "tolerance": tolerance})
    return checks, ok


def build_result(case_path, values_path, output):
    case = json.loads(case_path.read_text(encoding="utf-8"))
    values = json.loads(values_path.read_text(encoding="utf-8"))
    if not isinstance(case.get("output_contract"), dict) or not isinstance(values, dict) or set(case["output_contract"]) != set(values) or "case_id" not in case or "case_id" in values:
        raise GateError("contract adapter requires exact object keys and case_id")
    result = {"case_id": case["case_id"]}
    result.update(values)
    dump_json(output / "result.json", result)


def main():
    parser = argparse.ArgumentParser(description="Run previously reviewed generated code with static limits and independent numeric checks.")
    parser.add_argument("--code", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--job", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--case", type=Path)
    parser.add_argument("--task", type=Path)
    parser.add_argument("--resources", type=Path)
    parser.add_argument("--contract-values", type=Path)
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--memory-mb", type=int, default=256)
    parser.add_argument("--strict-memory-limit", action="store_true", help="Fail instead of continuing when OS rejects RLIMIT_AS")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    args.work.mkdir(parents=True, exist_ok=True)
    started, start_clock = UTC(), time.monotonic()
    receipt = {"skill_version": VERSION, "status": "started", "started_at": started, "actual_commands": [], "attempts": 1}
    exit_code = 2
    try:
        if args.timeout < 1 or args.timeout > 60 or args.memory_mb < 32 or args.memory_mb > 2048:
            raise GateError("timeout must be 1..60s and memory 32..2048MB")
        for path in (args.code, args.input, args.job):
            if not path.is_file():
                raise GateError("required file missing: %s" % path)
        for path in (args.case, args.task, args.resources):
            if path is not None and not path.is_file():
                raise GateError("evidence file missing: %s" % path)
        job = json.loads(args.job.read_text(encoding="utf-8"))
        if job.get("schema_version") != "1.0" or not isinstance(job.get("required_columns"), list) or not isinstance(job.get("validations"), list) or not job["validations"]:
            raise GateError("job schema/validators invalid")
        if not job.get("human_review_confirmed"):
            raise GateError("generated code must be human/agent reviewed before this non-adversarial sandbox", 3)
        code = args.code.read_text(encoding="utf-8")
        rows = load_csv(args.input, job["required_columns"])
        receipt["static_gate"] = static_gate(code)
        sandbox = args.work / "sandbox"
        sandbox.mkdir(exist_ok=True)
        payload = {"rows": rows, "config": job.get("public_config", {})}
        payload_path = sandbox / "payload.json"
        result_path = sandbox / "generated_result.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        for old in (result_path, sandbox / "stdout.log", sandbox / "stderr.log"):
            if old.exists():
                old.unlink()
        command = [sys.executable, "-I", "-c", DRIVER, str(args.code.resolve()), str(payload_path.resolve()), str(result_path.resolve())]
        stdout_path, stderr_path = sandbox / "stdout.log", sandbox / "stderr.log"
        env = {"PATH": "", "PYTHONDONTWRITEBYTECODE": "1", "LC_ALL": "C", "LANG": "C"}
        preexec = None
        resource_status_path = (sandbox / "resource_limits.json").resolve()
        if resource_status_path.exists():
            resource_status_path.unlink()
        if os.name == "posix":
            def preexec():
                import resource
                resource.setrlimit(resource.RLIMIT_CPU, (args.timeout, args.timeout + 1))
                resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_RESULT_BYTES, MAX_RESULT_BYTES))
                resource.setrlimit(resource.RLIMIT_NOFILE, (16, 16))
                limits = {"cpu_limit_applied": True, "file_size_limit_applied": True, "nofile_limit_applied": True, "memory_limit_applied": False, "strict_memory_limit": args.strict_memory_limit}
                try:
                    resource.setrlimit(resource.RLIMIT_AS, (args.memory_mb * 1024 * 1024, args.memory_mb * 1024 * 1024))
                    limits["memory_limit_applied"] = True
                    limits["memory_limit_error"] = None
                except (ValueError, OSError) as exc:
                    limits["memory_limit_error"] = "%s: %s" % (type(exc).__name__, str(exc))
                    if args.strict_memory_limit:
                        raise
                resource_status_path.write_text(json.dumps(limits), encoding="utf-8")
        with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
            try:
                child = subprocess.run(command, cwd=sandbox, env=env, stdout=stdout_file, stderr=stderr_file, timeout=args.timeout, preexec_fn=preexec, check=False)
                child_code, timed_out = child.returncode, False
            except subprocess.TimeoutExpired:
                child_code, timed_out = None, True
        resource_status = json.loads(resource_status_path.read_text(encoding="utf-8")) if resource_status_path.is_file() else {"status": "unavailable_or_not_posix", "memory_limit_applied": False}
        actual = {"command": command, "command_display": ["python3", "-I", "-c", "<trusted-driver>", "<reviewed-code>", "<payload>", "<generated-result>"], "cwd": str(sandbox), "child_exit_code": child_code, "timed_out": timed_out, "resource_limits": resource_status, "stdout": stdout_path.read_text(encoding="utf-8", errors="replace")[-MAX_LOG_BYTES:], "stderr": stderr_path.read_text(encoding="utf-8", errors="replace")[-MAX_LOG_BYTES:]}
        receipt["actual_commands"].append(actual)
        if timed_out or child_code != 0 or not result_path.is_file():
            receipt["status"] = "execution_failed"
            raise GateError("generated execution failed: timeout=%s child_exit=%s" % (timed_out, child_code), 4)
        result_bytes = result_path.read_bytes()
        if len(result_bytes) > MAX_RESULT_BYTES:
            raise GateError("generated result too large", 4)
        generated = json.loads(result_bytes.decode("utf-8"))
        if not isinstance(generated, dict):
            raise GateError("RESULT must be a JSON object", 4)
        evidence = numeric_validators(rows, job["validations"], set(job.get("missing_tokens", ["", "NA"])))
        comparisons, valid = compare(generated, evidence, job.get("numeric_tolerance", 1e-12))
        dump_json(args.output / "validation.json", {"status": "passed" if valid else "failed", "checks": comparisons, "boundary": "independently computed from source CSV, not from child RESULT or official gold"})
        shutil.copyfile(args.code, args.output / "generated_analysis.py")
        dump_json(args.output / "result_data.json", generated)
        shutil.copyfile(stdout_path, args.output / "stdout.log")
        shutil.copyfile(stderr_path, args.output / "stderr.log")
        if not valid:
            receipt["status"] = "validation_failed"
            raise GateError("silent numerical mismatch detected; see validation.json", 5)
        receipt["status"] = "success"
        if args.contract_values:
            if not args.case or not args.case.is_file():
                raise GateError("--case required for result contract")
            build_result(args.case, args.contract_values, args.output)
        exit_code = 0
        print("OK independent_validators=%d child_exit=0" % len(comparisons))
    except (GateError, OSError, UnicodeError, csv.Error, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        exit_code = exc.code if isinstance(exc, GateError) else (4 if isinstance(exc, subprocess.SubprocessError) else 2)
        if receipt["status"] == "started":
            receipt["status"] = "failed_before_or_during_execution"
        elif receipt["status"] == "success":
            receipt["status"] = "delivery_failed"
        print("ERROR %s" % exc, file=sys.stderr)
        receipt["error"] = {"type": type(exc).__name__, "message": str(exc)}
    receipt["finished_at"] = UTC()
    receipt["duration_seconds"] = time.monotonic() - start_clock
    receipt["exit_code"] = exit_code
    receipt["environment"] = {"python": platform.python_version(), "platform": platform.platform(), "stdlib_only": True, "os_posix_limits": os.name == "posix", "isolated_interpreter": True, "not_a_security_boundary_for_unreviewed_hostile_code": True}
    receipt["inputs"] = {}
    for label, path in (("code", args.code), ("input", args.input), ("job", args.job), ("case", args.case), ("task", args.task), ("resources", args.resources)):
        if path is not None and path.is_file():
            receipt["inputs"][label] = {"name": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
    dump_json(args.output / "run_receipt.json", receipt)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
