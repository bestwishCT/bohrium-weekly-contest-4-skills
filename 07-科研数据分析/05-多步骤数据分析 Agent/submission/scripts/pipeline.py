#!/usr/bin/env python3
"""Five-stage, file-backed research analysis with gates and resumable lineage."""
import argparse
import csv
import hashlib
import json
import math
import platform
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.dont_write_bytecode = True
import render_chart as chartlib

VERSION = "1.0.0"
STAGES = ["ingest", "clean", "analyze", "visualize", "report"]
ARCHIVABLE_OUTPUTS = {"source_snapshot.csv", "ingest.json", "analysis.csv", "row_audit.csv", "duplicate_groups.json", "cleaning_report.json", "analysis.json", "figure.svg", "figure.png", "plot_data.csv", "render_check.json", "report.md", "report_audit.json", "result.json", "pipeline_state.json"}
UTC = lambda: datetime.now(timezone.utc).isoformat()


def validate_io_paths(work, output, inputs):
    work, output = work.resolve(), output.resolve()
    if work == output or work in output.parents or output in work.parents:
        raise StageError("work and output directories must be disjoint")
    for path in inputs:
        resolved = path.resolve()
        if resolved == output or output in resolved.parents:
            raise StageError("input and plan files must be outside the output directory")


class StageError(Exception):
    pass


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    return sha_bytes(path.read_bytes())


def dump_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise StageError("missing or duplicate CSV header")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise StageError("malformed CSV row")
    return list(reader.fieldnames), rows


def decimal_text(value):
    if value == 0:
        return "0"
    return (format(value, "f").rstrip("0").rstrip(".") if "." in format(value, "f") else format(value, "f")) or "0"


def normalized(value):
    return " ".join(value.strip().split()).casefold()


def output_hashes(stage_dir):
    return {path.name: sha_file(path) for path in sorted(stage_dir.iterdir()) if path.is_file()}


def fmt(value):
    if value is None:
        return "NA"
    if isinstance(value, int):
        return str(value)
    return ("%.10f" % value).rstrip("0").rstrip(".")


class Pipeline:
    def __init__(self, args):
        self.args = args
        self.work, self.output = args.work, args.output
        self.plan = json.loads(args.plan.read_text(encoding="utf-8"))
        self.stage_dirs = {name: self.work / "stages" / name for name in STAGES}
        self.checkpoints = self.work / "checkpoints"
        self.checkpoints.mkdir(parents=True, exist_ok=True)
        for path in self.stage_dirs.values():
            path.mkdir(parents=True, exist_ok=True)
        self.state = {"schema_version": "1.0", "started_at": UTC(), "skill_version": VERSION, "stages": {}, "source_fingerprints": {}}
        self.previous_digest = None
        self.script_hashes = {"pipeline.py": sha_file(Path(__file__)), "render_chart.py": sha_file(Path(chartlib.__file__))}

    def fingerprint(self, stage):
        payload = {"stage": stage, "input_sha256": sha_file(self.args.input), "plan_sha256": sha_file(self.args.plan), "scripts": self.script_hashes, "parent": self.previous_digest}
        if stage == "ingest":
            payload["evidence"] = {label: sha_file(path) for label, path in (("case", self.args.case), ("task", self.args.task), ("resources", self.args.resources)) if path is not None}
        return sha_bytes(json.dumps(payload, sort_keys=True).encode("utf-8"))

    def cached(self, stage, fingerprint):
        path = self.checkpoints / (stage + ".json")
        if not path.is_file():
            return None
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
        if checkpoint.get("source_fingerprint") != fingerprint or checkpoint.get("status") != "done":
            return None
        stage_dir = self.stage_dirs[stage]
        for name, expected in checkpoint.get("output_hashes", {}).items():
            target = stage_dir / name
            if not target.is_file() or sha_file(target) != expected:
                return None
        return checkpoint

    def finish_stage(self, stage, fingerprint):
        checkpoint = {"stage": stage, "status": "done", "source_fingerprint": fingerprint, "finished_at": UTC(), "output_hashes": output_hashes(self.stage_dirs[stage]), "skill_version": VERSION}
        dump_json(self.checkpoints / (stage + ".json"), checkpoint)
        return checkpoint

    def validate_plan(self):
        if self.plan.get("schema_version") != "1.0":
            raise StageError("unsupported plan schema_version")
        dictionary = self.plan.get("dictionary", {})
        if {"id", "group", "value", "unit", "design"} - set(dictionary):
            raise StageError("dictionary id/group/value/unit/design required")
        if self.plan.get("duplicate_action") not in {"quarantine_conflicts", "keep_review"}:
            raise StageError("unsupported duplicate_action")
        if self.plan.get("analysis_scope") != "descriptive_only":
            raise StageError("built-in stage supports descriptive_only; unsupported inferential request must stop")
        if self.plan.get("qc") and self.plan["qc"].get("on_violation") not in {"quarantine", "retain_flag"}:
            raise StageError("QC on_violation must be explicitly quarantine or retain_flag")

    def ingest(self):
        self.validate_plan()
        fields, rows = read_csv(self.args.input)
        required = list(self.plan["dictionary"][key] for key in ("id", "group", "value"))
        missing = [name for name in required if name not in fields]
        if missing:
            raise StageError("required columns missing: %s" % missing)
        if not rows:
            raise StageError("empty dataset")
        snapshot = self.stage_dirs["ingest"] / "source_snapshot.csv"
        shutil.copyfile(self.args.input, snapshot)
        sources = {"input": {"name": self.args.input.name, "bytes": self.args.input.stat().st_size, "sha256": sha_file(self.args.input)}, "plan": {"name": self.args.plan.name, "bytes": self.args.plan.stat().st_size, "sha256": sha_file(self.args.plan)}}
        for label, path in (("case", self.args.case), ("task", self.args.task), ("resources", self.args.resources)):
            if path is not None:
                if not path.is_file():
                    raise StageError("evidence file missing: %s" % path)
                sources[label] = {"name": path.name, "bytes": path.stat().st_size, "sha256": sha_file(path)}
        field_map = {role: self.plan["dictionary"][role] for role in ("id", "group", "value", "unit", "design")}
        dump_json(self.stage_dirs["ingest"] / "ingest.json", {"status": "passed", "raw_rows": len(rows), "columns": fields, "field_map": field_map, "source": self.plan.get("source_description"), "sources": sources, "snapshot": snapshot.name, "grain": self.plan.get("grain", "one row per experimental unit")})

    def clean(self):
        plan = self.plan
        role = plan["dictionary"]
        id_col, group_col, value_col = role["id"], role["group"], role["value"]
        missing_tokens = set(plan.get("missing_tokens", ["", "NA"]))
        fields, rows = read_csv(self.stage_dirs["ingest"] / "source_snapshot.csv")
        audit, exact_seen = [], set()
        for source_row, row in enumerate(rows, 2):
            clean = {key: value.strip() for key, value in row.items()}
            clean[id_col], clean[group_col] = normalized(clean[id_col]), normalized(clean[group_col])
            original = json.dumps(row, sort_keys=True)
            reasons = []
            if original in exact_seen:
                if plan.get("drop_exact_duplicates_authorized"):
                    reasons.append("exact_duplicate_removed")
                else:
                    reasons.append("exact_duplicate_flagged_retained_pending_review")
            exact_seen.add(original)
            text = clean[value_col]
            if not clean[id_col] or not clean[group_col]:
                reasons.append("missing_key")
            if text in missing_tokens:
                reasons.append("missing_or_sentinel_value")
                clean[value_col] = ""
            else:
                try:
                    number = Decimal(text)
                    if not number.is_finite():
                        raise InvalidOperation
                    clean[value_col] = decimal_text(number)
                    qc = plan.get("qc", {})
                    if ("min" in qc and number < Decimal(qc["min"])) or ("max" in qc and number > Decimal(qc["max"])):
                        if qc.get("on_violation") == "quarantine":
                            reasons.append("qc_out_of_range_quarantined")
                        elif qc.get("on_violation") == "retain_flag":
                            reasons.append("qc_out_of_range_retained_flag")
                        else:
                            raise StageError("QC action must be explicitly quarantine or retain_flag")
                except (InvalidOperation, ValueError):
                    reasons.append("invalid_numeric_value")
                    clean[value_col] = ""
            advisory = {"qc_out_of_range_retained_flag", "exact_duplicate_flagged_retained_pending_review"}
            fatal_reasons = [reason for reason in reasons if reason not in advisory]
            audit.append({"source_row": source_row, "key": clean[id_col], "reasons": reasons, "eligible": not fatal_reasons, "clean": clean})
        key_groups = defaultdict(list)
        for pos, record in enumerate(audit):
            if record["key"]:
                key_groups[record["key"]].append(pos)
        duplicate_groups = []
        if plan.get("keys_expected_unique", True):
            for key, positions in sorted(key_groups.items()):
                if len(positions) < 2:
                    continue
                signatures = {json.dumps(audit[pos]["clean"], sort_keys=True) for pos in positions}
                if len(signatures) == 1:
                    kind = "exact_key_duplicate"
                else:
                    kind = "conflicting_key_duplicate"
                    if plan["duplicate_action"] == "quarantine_conflicts":
                        for pos in positions:
                            audit[pos]["eligible"] = False
                            if "conflicting_key_duplicate" not in audit[pos]["reasons"]:
                                audit[pos]["reasons"].append("conflicting_key_duplicate")
                duplicate_groups.append({"key": key, "rows": [audit[pos]["source_row"] for pos in positions], "kind": kind, "action": plan["duplicate_action"]})
        eligible = [record for record in audit if record["eligible"]]
        analysis_rows = [dict(record["clean"], source_row=record["source_row"]) for record in eligible]
        row_output = [dict(record["clean"], source_row=record["source_row"], analysis_eligible=str(record["eligible"]).lower(), reasons=";".join(record["reasons"])) for record in audit]
        write_csv(self.stage_dirs["clean"] / "analysis.csv", ["source_row"] + fields, analysis_rows)
        write_csv(self.stage_dirs["clean"] / "row_audit.csv", ["source_row", "analysis_eligible", "reasons"] + fields, row_output)
        dump_csv_path = self.stage_dirs["clean"] / "duplicate_groups.json"
        dump_json(dump_csv_path, duplicate_groups)
        counts = Counter(reason for record in audit for reason in record["reasons"])
        excluded = len(rows) - len(eligible)
        reloaded_analysis = read_csv(self.stage_dirs["clean"] / "analysis.csv")[1]
        reloaded_audit = read_csv(self.stage_dirs["clean"] / "row_audit.csv")[1]
        reloaded_duplicates = json.loads(dump_csv_path.read_text(encoding="utf-8"))
        report = {"raw_rows": len(rows), "eligible_rows": len(eligible), "excluded_from_analysis_not_deleted": excluded, "issues_by_reason": dict(sorted(counts.items())), "duplicate_groups": len(duplicate_groups), "imputation": False, "reconciliation": {"raw_equals_eligible_plus_excluded": len(rows) == len(eligible) + excluded, "audit_rows_equal_raw": len(reloaded_audit) == len(rows), "eligible_file_rows_match": len(reloaded_analysis) == len(eligible), "duplicate_file_groups_match": len(reloaded_duplicates) == len(duplicate_groups)}}
        if not all(report["reconciliation"].values()):
            raise StageError("clean stage reconciliation failed")
        dump_json(self.stage_dirs["clean"] / "cleaning_report.json", report)
        self.clean_report = report

    def analyze(self):
        _, rows = read_csv(self.stage_dirs["clean"] / "analysis.csv")
        role = self.plan["dictionary"]
        group_col, value_col = role["group"], role["value"]
        grouped = defaultdict(list)
        for row in rows:
            grouped[row[group_col]].append(float(row[value_col]))
        if not grouped:
            raise StageError("no eligible analysis data")
        groups = []
        for name in sorted(grouped):
            values = grouped[name]
            n, avg = len(values), math.fsum(values) / len(values)
            sd = math.sqrt(math.fsum((x - avg) ** 2 for x in values) / (n - 1)) if n > 1 else None
            groups.append({"group": name, "n": n, "mean": avg, "sd": sd, "min": min(values), "max": max(values), "unit": role["unit"]})
        payload = {"scope": "descriptive_only_no_p_values", "design": role["design"], "groups": groups, "total_eligible_n": len(rows), "unit": role["unit"], "assumption_gate": {"independence": "from declared design, not established here", "distribution": "review plot/source; no automatic inferential method switch", "inference": "not requested in this narrow stage; p values intentionally absent", "multiplicity": "not applicable without hypothesis family"}}
        dump_json(self.stage_dirs["analyze"] / "analysis.json", payload)
        self.stats = payload

    def visualize(self):
        stats = json.loads((self.stage_dirs["analyze"] / "analysis.json").read_text(encoding="utf-8"))["groups"]
        if len(stats) > 12 or any(len(row["group"]) > 16 or not row["group"].isascii() for row in stats):
            raise StageError("visualization fallback requires <=12 short ASCII group labels; use verified font/layout tool")
        data = [{"category": row["group"], "series": "all", "center": row["mean"], "n": row["n"], "sd": row["sd"], "error": row["sd"] if self.plan.get("error") == "sd" else None, "error_kind": "sd" if row["sd"] is not None and self.plan.get("error") == "sd" else None, "source_values": []} for row in stats]
        spec = {"chart": "grouped_bar", "purpose": "comparison", "title": self.plan.get("figure_title", "Group means"), "category_label": self.plan["dictionary"]["group"], "value_label": "Mean (%s)" % self.plan["dictionary"]["unit"], "y_label": "Mean", "x_label": "Group", "error": self.plan.get("error")}
        canvas, elements = chartlib.build_canvas(spec, data, ["all"], {})
        dpi = self.plan.get("dpi", 300)
        if dpi < 300:
            raise StageError("pipeline PNG requires dpi >=300")
        width, height = round(85 / 25.4 * dpi), round(55 / 25.4 * dpi)
        (self.stage_dirs["visualize"] / "figure.svg").write_text(canvas.svg(), encoding="utf-8")
        (self.stage_dirs["visualize"] / "figure.png").write_bytes(canvas.png(width, height, dpi))
        write_csv(self.stage_dirs["visualize"] / "plot_data.csv", ["group", "n", "mean", "sd", "error"], [{"group": d["category"], "mean": d["center"], **d} for d in data])
        png_check = chartlib.inspect_png(self.stage_dirs["visualize"] / "figure.png", width, height, dpi)
        audit = {"elements": elements, "stats_groups": len(stats), "same_count": elements == len(stats), "source_stage": "analyze/analysis.json", "not_from_raw_stale_table": True, "svg": __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).parse(self.stage_dirs["visualize"] / "figure.svg").getroot().tag.endswith("svg"), "png": png_check}
        if not audit["same_count"] or not audit["svg"] or not png_check["passed"]:
            raise StageError("visualization source-to-render gate failed")
        dump_json(self.stage_dirs["visualize"] / "render_check.json", audit)

    def report(self):
        stats = json.loads((self.stage_dirs["analyze"] / "analysis.json").read_text(encoding="utf-8"))
        png_check = json.loads((self.stage_dirs["visualize"] / "render_check.json").read_text(encoding="utf-8"))
        lines = ["# 多步骤科研分析摘要（自动回填）", "", "本报告仅作描述统计，不报告或暗示假设检验显著性。数据粒度：%s。" % self.plan.get("grain", "one row per experimental unit"), "", "| Group | Valid n | Mean | Sample SD | Unit |", "|---|---:|---:|---:|---|"]
        for row in stats["groups"]:
            lines.append("| %s | %d | %s | %s | %s |" % (row["group"], row["n"], fmt(row["mean"]), fmt(row["sd"]), row["unit"]))
        lines += ["", "有效样本量总计：%d。误差线：%s。" % (stats["total_eligible_n"], self.plan.get("error", "无")), "", "均值来自 analysis.json，图来自同一分析版本；PNG 结构校验已通过，最终图还需打开逐图检查。设计、剩余不确定性和不适用推断见 analysis.json。", ""]
        text = "\n".join(lines)
        path = self.stage_dirs["report"] / "report.md"
        path.write_text(text, encoding="utf-8")
        reloaded = path.read_text(encoding="utf-8")
        checks = [{"group": row["group"], "n_backfilled": ("| %d |" % row["n"]) in reloaded, "mean_backfilled": fmt(row["mean"]) in reloaded, "sd_backfilled": fmt(row["sd"]) in reloaded} for row in stats["groups"]]
        audit = {"checks": checks, "passed": all(all(value for key, value in check.items() if key != "group") for check in checks), "visual_structural_check_backfilled": png_check["same_count"], "manual_preview_still_required": True}
        if not audit["passed"]:
            raise StageError("report numeric backfill/readback failed")
        dump_json(self.stage_dirs["report"] / "report_audit.json", audit)

    def run(self):
        validate_io_paths(self.work, self.output, [self.args.input, self.args.plan])
        for path in (self.args.input, self.args.plan):
            if not path.is_file():
                raise StageError("required file missing: %s" % path)
        archive = self.work / "recovery" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        archive.mkdir(parents=True, exist_ok=True)
        for old in list(self.output.iterdir()):
            if old.is_file() and old.name in ARCHIVABLE_OUTPUTS:
                shutil.move(str(old), str(archive / old.name))
        functions = {"ingest": self.ingest, "clean": self.clean, "analyze": self.analyze, "visualize": self.visualize, "report": self.report}
        current, failure = None, None
        for stage in STAGES:
            if failure is not None:
                self.state["stages"][stage] = {"status": "blocked", "reason": "upstream failed"}
                continue
            current = stage
            fingerprint = self.fingerprint(stage)
            checkpoint = self.cached(stage, fingerprint)
            if checkpoint:
                self.previous_digest = sha_bytes(json.dumps(checkpoint["output_hashes"], sort_keys=True).encode("utf-8"))
                self.state["stages"][stage] = {"status": "cached", "source_fingerprint": fingerprint, "outputs": sorted(checkpoint["output_hashes"])}
            else:
                try:
                    functions[stage]()
                    checkpoint = self.finish_stage(stage, fingerprint)
                    self.previous_digest = sha_bytes(json.dumps(checkpoint["output_hashes"], sort_keys=True).encode("utf-8"))
                    self.state["stages"][stage] = {"status": "recomputed", "source_fingerprint": fingerprint, "outputs": sorted(output_hashes(self.stage_dirs[stage]))}
                except Exception as exc:
                    failure = {"stage": stage, "type": type(exc).__name__, "message": str(exc)}
                    dump_json(self.checkpoints / (stage + ".json"), {"stage": stage, "status": "failed", "source_fingerprint": fingerprint, "finished_at": UTC(), "error": failure})
                    self.state["stages"][stage] = {"status": "failed", "error": failure}
            self.state["source_fingerprints"][stage] = fingerprint
        if failure is None:
            for stage in STAGES:
                for source in self.stage_dirs[stage].iterdir():
                    if source.is_file():
                        shutil.copyfile(source, self.output / source.name)
            if self.args.contract_values:
                case = json.loads(self.args.case.read_text(encoding="utf-8"))
                values = json.loads(self.args.contract_values.read_text(encoding="utf-8"))
                if not isinstance(case.get("output_contract"), dict) or not isinstance(values, dict) or set(case["output_contract"]) != set(values) or "case_id" not in case or "case_id" in values:
                    failure = {"stage": "contract", "type": "StageError", "message": "contract values keys must exactly match object output_contract"}
                else:
                    result = {"case_id": case["case_id"]}
                    result.update(values)
                    dump_json(self.output / "result.json", result)
        else:
            failed_index = STAGES.index(failure["stage"]) if failure["stage"] in STAGES else len(STAGES)
            for stage in STAGES[:failed_index]:
                for source in self.stage_dirs[stage].iterdir():
                    if source.is_file():
                        shutil.copyfile(source, self.output / source.name)
        self.state["finished_at"] = UTC()
        self.state["status"] = "success" if failure is None else "partial_or_failed"
        self.state["failure"] = failure
        self.state["exit_code"] = 0 if failure is None else 2
        self.state["environment"] = {"python": platform.python_version(), "platform": platform.platform(), "stdlib_only": True, "script_sha256": self.script_hashes, "cache_digest_includes_input_plan_code_parent": True, "randomness": None}
        self.state["archive_of_previous_output"] = str(archive.relative_to(self.work))
        dump_json(self.output / "pipeline_state.json", self.state)
        history = self.work / "run_history.json"
        prior = json.loads(history.read_text(encoding="utf-8")) if history.is_file() else []
        prior.append(self.state)
        dump_json(history, prior)
        print("OK" if failure is None else "PARTIAL stage=%s" % failure["stage"])
        return 0 if failure is None else 2


def main():
    parser = argparse.ArgumentParser(description="Run resume-safe ingest/clean/analyze/visualize/report stages with shared artifacts.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--case", type=Path)
    parser.add_argument("--task", type=Path)
    parser.add_argument("--resources", type=Path)
    parser.add_argument("--contract-values", type=Path)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        pipeline = Pipeline(args)
        return pipeline.run()
    except (StageError, OSError, UnicodeError, ValueError, KeyError, csv.Error, json.JSONDecodeError) as exc:
        failure = {"schema_version": "1.0", "status": "partial_or_failed", "finished_at": UTC(), "exit_code": 2, "failure": {"stage": "startup_or_delivery", "type": type(exc).__name__, "message": str(exc)}, "partial_outputs_retained": True}
        dump_json(args.output / "pipeline_state.json", failure)
        print("ERROR %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
