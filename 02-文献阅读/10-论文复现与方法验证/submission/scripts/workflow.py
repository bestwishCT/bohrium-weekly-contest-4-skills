#!/usr/bin/env python3
"""Run exactly two supplied numerical candidates with pre-committed tolerances."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

from common import Run, add_arguments, digest, read_json, write_json


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def last_json(text):
    for line in reversed([line for line in text.splitlines() if line.strip()]):
        return json.loads(line)
    raise ValueError("candidate produced no stdout JSON")


def main():
    args = add_arguments(argparse.ArgumentParser(description=__doc__)).parse_args()
    try:
        run = Run(args)
    except Exception as error:
        print(json.dumps({"status": "input_error", "error": str(error)}), file=sys.stderr)
        return 2
    try:
        workorder_path = run.path(run.case.get("repro_workorder", "repro-workorder.json"))
        workorder = read_json(workorder_path)
        methods = workorder.get("methods", [])
        if len(methods) != 2:
            raise ValueError("this task version requires exactly two candidate numerical methods")
        tolerance_contract = {method.get("method_id"): method.get("tolerances", {}) for method in methods}
        if any(not values for values in tolerance_contract.values()):
            raise ValueError("each method requires predeclared metric tolerances")
        precommit = {"case_id": run.case_id, "workorder_sha256": digest(workorder_path),
                     "declared_before_execution_utc": utc_now(), "tolerances": tolerance_contract,
                     "comparison_scope": workorder.get("comparison_scope"),
                     "seed": workorder.get("seed"), "data_rule": workorder.get("data_rule"),
                     "statistic": workorder.get("statistic"),
                     "boundary": "supplied synthetic candidates only; not original-author code, physical experiment, or full PMLB"}
        write_json(run.work / "tolerance-precommit.json", precommit)
        environment = {"captured_utc": utc_now(), "python": sys.version,
                       "python_executable": sys.executable, "platform": platform.platform(),
                       "machine": platform.machine(), "processor": platform.processor(),
                       "environment_variables_selected": {key: os.environ[key] for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS") if key in os.environ}}
        write_json(run.output / "environment.json", environment)
        results = []
        raw_logs = []
        for method in methods:
            method_id = str(method.get("method_id", ""))
            started = time.monotonic()
            stdout = stderr = ""
            returncode = None
            metrics = None
            failure = None
            script_path = run.path(method.get("script", ""))
            method_work = run.work / method_id
            method_work.mkdir(parents=True, exist_ok=True)
            command = [sys.executable, str(script_path)] + [str(value) for value in method.get("args", [])]
            try:
                completed = subprocess.run(command, cwd=str(method_work), text=True,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           timeout=float(method.get("timeout_seconds", 60)), check=False)
                stdout, stderr, returncode = completed.stdout, completed.stderr, completed.returncode
                if returncode != 0:
                    failure = "nonzero exit status"
                else:
                    metrics = last_json(stdout)
            except Exception as error:
                failure = str(error)
                stderr += "\nrunner: " + failure
            stdout_path = run.output / (method_id + ".stdout.log")
            stderr_path = run.output / (method_id + ".stderr.log")
            stdout_path.write_text(stdout, encoding="utf-8")
            stderr_path.write_text(stderr, encoding="utf-8")
            expected = method.get("expected_metrics", {})
            comparisons = []
            if metrics is not None:
                for metric, expected_value in expected.items():
                    tolerances = method["tolerances"].get(metric, {})
                    actual_value = metrics.get(metric)
                    record = {"metric": metric, "actual": actual_value, "expected": expected_value,
                              "absolute_tolerance": tolerances.get("absolute"),
                              "relative_tolerance": tolerances.get("relative")}
                    try:
                        difference = abs(float(actual_value) - float(expected_value))
                        record["absolute_difference"] = difference
                        checks = []
                        if "absolute" in tolerances:
                            checks.append(difference <= float(tolerances["absolute"]))
                        if "relative" in tolerances:
                            denominator = abs(float(expected_value))
                            record["relative_difference"] = difference / denominator if denominator else (0.0 if difference == 0 else None)
                            checks.append(record["relative_difference"] is not None and record["relative_difference"] <= float(tolerances["relative"]))
                        if not checks:
                            raise ValueError("metric has no absolute or relative tolerance")
                        record["within_predeclared_tolerance"] = all(checks)
                    except Exception as error:
                        record["within_predeclared_tolerance"] = False
                        record["reason"] = str(error)
                    comparisons.append(record)
                missing = sorted(set(method["tolerances"]) - set(expected))
                if missing:
                    comparisons.append({"metric": ",".join(missing), "within_predeclared_tolerance": False,
                                        "reason": "toleranced metric is absent from expected contract"})
            passed = failure is None and bool(comparisons) and all(item.get("within_predeclared_tolerance") for item in comparisons)
            result = {"method_id": method_id, "source": method.get("source"),
                      "parameter_provenance": method.get("parameter_provenance", {}),
                      "script": str(script_path), "command": command, "returncode": returncode,
                      "duration_seconds_actual": time.monotonic() - started,
                      "metrics": metrics, "comparisons": comparisons,
                      "status": "reproducible" if passed else "not_reproducible",
                      "failure": failure,
                      "difference_attribution": "not_determined_requires_diagnostic_evidence" if not passed else "within_predeclared_numerical_tolerance"}
            results.append(result)
            raw_logs.extend([{"method_id": method_id, "stream": "stdout", "path": str(stdout_path)},
                             {"method_id": method_id, "stream": "stderr", "path": str(stderr_path)}])
        passed_count = sum(item["status"] == "reproducible" for item in results)
        conclusion = "reproducible" if passed_count == 2 else "partially_reproducible" if passed_count == 1 else "not_reproducible"
        code = 0 if passed_count == 2 else 2
        run.changes.append("executed both candidates without modifying source or tolerances and retained raw logs")
        run.finish({"status": conclusion, "reproduction_conclusion": conclusion,
                    "predeclared_tolerance": precommit, "environment": environment,
                    "methods": results, "raw_logs": raw_logs,
                    "scope_limit": precommit["boundary"], "sources": [{"path": str(workorder_path), "sha256": precommit["workorder_sha256"]}]},
                   aliases={"runs": "methods", "reproductionresults": "methods", "result": "methods"}, code=code,
                   trace_extra={"methods_executed": len(results), "tolerance_precommit": str(run.work / "tolerance-precommit.json")})
    except Exception as error:
        run.uncertainty.append(str(error))
        run.finish({"status": "not_reproducible", "methods": [], "raw_logs": [],
                    "scope_limit": "no execution claim beyond supplied candidates", "failure": str(error)}, code=2)


if __name__ == "__main__":
    sys.exit(main())
