#!/usr/bin/env python3
"""Design-first descriptive and focused inferential statistics using stdlib only."""
import argparse
import csv
import hashlib
import json
import math
import platform
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path

VERSION = "1.0.0"
UTC = lambda: datetime.now(timezone.utc).isoformat()


class AnalysisError(Exception):
    pass


def dump_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def betacf(a, b, x):
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < 1e-300:
        d = 1e-300
    d = 1.0 / d
    h = d
    for m in range(1, 301):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-300:
            d = 1e-300
        c = 1.0 + aa / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-300:
            d = 1e-300
        c = 1.0 + aa / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-14:
            return h
    raise AnalysisError("incomplete beta did not converge")


def betai(a, b, x):
    if x < 0.0 or x > 1.0:
        raise AnalysisError("beta domain error")
    if x == 0.0 or x == 1.0:
        return x
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * betacf(a, b, x) / a
    return 1.0 - bt * betacf(b, a, 1.0 - x) / b


def t_two_sided_p(t, df):
    if df <= 0 or not math.isfinite(df):
        return None
    if not math.isfinite(t):
        return 0.0
    return min(1.0, max(0.0, betai(df / 2.0, 0.5, df / (df + t * t))))


def t_critical(df, confidence):
    if not 0 < confidence < 1 or df <= 0:
        raise AnalysisError("invalid t confidence or df")
    target = 1.0 - confidence
    lo, hi = 0.0, 1.0
    while t_two_sided_p(hi, df) > target:
        hi *= 2
        if hi > 1e6:
            raise AnalysisError("t quantile search failed")
    for _ in range(200):
        mid = (lo + hi) / 2
        if t_two_sided_p(mid, df) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def mean(values):
    return math.fsum(values) / len(values)


def variance(values):
    avg = mean(values)
    return math.fsum((x - avg) ** 2 for x in values) / (len(values) - 1)


def quantiles(values):
    if len(values) < 2:
        return (None, None)
    q = statistics.quantiles(values, n=4, method="inclusive")
    return q[0], q[2]


def describe(values, missing, unit):
    avg = mean(values) if values else None
    q1, q3 = quantiles(values)
    fences = None
    if q1 is not None:
        iqr = q3 - q1
        fences = [q1 - 1.5 * iqr, q3 + 1.5 * iqr]
    outliers = sum(1 for x in values if fences and (x < fences[0] or x > fences[1]))
    jb = {"available": False, "statistic": None, "asymptotic_p": None, "reason": "n<8; no automatic method switch"}
    if len(values) >= 8 and variance(values) > 0:
        n = len(values)
        m2 = math.fsum((x - avg) ** 2 for x in values) / n
        m3 = math.fsum((x - avg) ** 3 for x in values) / n
        m4 = math.fsum((x - avg) ** 4 for x in values) / n
        skew = m3 / (m2 ** 1.5)
        excess = m4 / (m2 * m2) - 3.0
        stat = n / 6.0 * (skew * skew + excess * excess / 4.0)
        jb = {"available": True, "statistic": stat, "asymptotic_p": math.exp(-stat / 2.0), "reason": "Jarque-Bera diagnostic; chi-square df=2 tail, not a mechanical decision switch"}
    return {
        "n": len(values), "missing_or_invalid": missing, "unit": unit,
        "mean": avg, "sample_sd": math.sqrt(variance(values)) if len(values) >= 2 else None,
        "median": statistics.median(values) if values else None, "q1": q1, "q3": q3,
        "minimum": min(values) if values else None, "maximum": max(values) if values else None,
        "tukey_diagnostic_outliers_not_removed": outliers, "normality_diagnostic": jb
    }


def missing_fraction(missing, total):
    return 1.0 if total == 0 else missing / total


def bh_adjust(items):
    valid = sorted((item for item in items if item["p_raw"] is not None), key=lambda x: (x["p_raw"], x["analysis_id"]))
    running = 1.0
    ranked = {}
    for rank in range(len(valid), 0, -1):
        item = valid[rank - 1]
        running = min(running, item["p_raw"] * len(valid) / rank)
        ranked[item["analysis_id"]] = min(1.0, running)
    for item in items:
        item["p_adjusted"] = ranked.get(item["analysis_id"])
        item["adjustment"] = "Benjamini-Hochberg within configured family" if item["p_raw"] is not None else "not_available"


def independent_analysis(values_a, values_b, label_a, label_b, analysis, confidence):
    if len(values_a) < 2 or len(values_b) < 2:
        return {"status": "unavailable_small_n", "reason": "two values per independently sampled group required"}
    ma, mb = mean(values_a), mean(values_b)
    va, vb = variance(values_a), variance(values_b)
    na, nb = len(values_a), len(values_b)
    diff = ma - mb
    se2 = va / na + vb / nb
    df = se2 * se2 / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1)) if se2 > 0 else None
    pooled_df = na + nb - 2
    pooled = math.sqrt(((na - 1) * va + (nb - 1) * vb) / pooled_df)
    correction = 1.0 - 3.0 / (4.0 * pooled_df - 1.0)
    effect = correction * diff / pooled if pooled > 0 else None
    base = {"design": "independent", "contrast": "%s - %s" % (label_a, label_b), "mean_difference": diff, "effect_name": "Hedges g using pooled SD and small-sample correction", "effect": effect, "confidence_level": confidence}
    if se2 <= 0 or df is None or df <= 0:
        base.update(status="unavailable_zero_variance", reason="Welch SE/df undefined; do not report p or CI", statistic=None, df=None, p_raw=None, ci=None)
        return base
    se = math.sqrt(se2)
    stat = diff / se
    critique = t_critical(df, confidence)
    base.update(status="computed", statistic=stat, statistic_name="Welch t", df=df, p_raw=t_two_sided_p(stat, df), ci=[diff - critique * se, diff + critique * se], se=se, formula="diff=mean(A)-mean(B); SE=sqrt(vA/nA+vB/nB); Welch-Satterthwaite df and t CI", reason=None)
    base["independent_numeric_recheck"] = {
        "mean_difference_max_abs_error": abs(diff - ((statistics.mean(values_a) - statistics.mean(values_b)))),
        "variance_a_max_abs_error": abs(va - statistics.variance(values_a)),
        "variance_b_max_abs_error": abs(vb - statistics.variance(values_b))
    }
    return base


def paired_analysis(rows, spec, confidence):
    group_col, value_col, subject_col = spec["group"], spec["outcome"], spec["subject"]
    a, b = spec["group_levels"]
    pairs = {}
    conflicted_subjects = set()
    unmatched = Counter()
    for row in rows:
        if row.get(group_col) not in {a, b}:
            continue
        subject = row.get(subject_col, "").strip()
        text = row.get(value_col, "").strip()
        if not subject:
            unmatched["missing_subject"] += 1
            continue
        if not text:
            unmatched["missing_value"] += 1
            continue
        try:
            value = float(text)
        except ValueError:
            unmatched["invalid_value"] += 1
            continue
        if not math.isfinite(value):
            unmatched["nonfinite_value"] += 1
            continue
        cell = pairs.setdefault(subject, {})
        if row[group_col] in cell:
            conflicted_subjects.add(subject)
            unmatched["duplicate_subject_condition_cells"] += 1
        else:
            cell[row[group_col]] = value
    complete = [(values[a], values[b]) for subject, values in pairs.items() if subject not in conflicted_subjects and a in values and b in values]
    differences = [x - y for x, y in complete]
    unmatched["conflicted_subjects_excluded"] = len(conflicted_subjects)
    unmatched["unmatched_subjects"] = sum(subject not in conflicted_subjects and not (a in values and b in values) for subject, values in pairs.items())
    unmatched["candidate_pairs"] = len(pairs) + unmatched["missing_subject"]
    unmatched["complete_pairs"] = len(differences)
    unmatched["missing_pairs"] = unmatched["candidate_pairs"] - unmatched["complete_pairs"]
    desc = describe(differences, sum(unmatched.values()), spec.get("unit"))
    if len(differences) < 2:
        inference = {"status": "unavailable_small_n", "reason": "at least two complete pairs required"}
    else:
        avg, sd = mean(differences), math.sqrt(variance(differences))
        se = sd / math.sqrt(len(differences))
        df = len(differences) - 1
        critique = t_critical(df, confidence)
        if se == 0:
            inference = {"status": "unavailable_zero_variance", "reason": "paired difference SD is zero; do not report p or CI", "mean_difference": avg, "effect": None, "p_raw": None, "ci": None}
        else:
            stat = avg / se
            inference = {"status": "computed", "design": "paired", "mean_difference": avg, "effect_name": "dz=mean within-pair difference / SD of differences", "effect": avg / sd, "statistic_name": "paired t", "statistic": stat, "df": df, "se": se, "p_raw": t_two_sided_p(stat, df), "ci": [avg - critique * se, avg + critique * se], "confidence_level": confidence, "contrast": "%s - %s within subject" % (a, b)}
    return desc, inference, dict(unmatched)


def correlation_analysis(rows, spec, confidence):
    pairs, missing = [], 0
    for row in rows:
        try:
            x, y = float(row[spec["x"]]), float(row[spec["y"]])
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError("nonfinite")
            pairs.append((x, y))
        except (ValueError, KeyError):
            missing += 1
    desc = {"complete_pairs": len(pairs), "missing_or_invalid_pairs": missing}
    if len(pairs) < 3:
        return desc, {"status": "unavailable_small_n", "reason": "at least three complete pairs required"}
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    mx, my = mean(xs), mean(ys)
    sxx = math.fsum((x - mx) ** 2 for x in xs)
    sxy = math.fsum((x - mx) * (y - my) for x, y in pairs)
    syy = math.fsum((y - my) ** 2 for y in ys)
    if sxx <= 0:
        return desc, {"status": "unavailable_zero_variance", "reason": "x variance is zero; slope undefined"}
    slope = sxy / sxx
    intercept = my - slope * mx
    residuals = [y - (intercept + slope * x) for x, y in pairs]
    df = len(pairs) - 2
    sse = math.fsum(r * r for r in residuals)
    r = sxy / math.sqrt(sxx * syy) if syy > 0 else None
    if sse == 0:
        return desc, {"status": "unavailable_zero_residual_variance", "reason": "slope SE undefined for zero residual variance", "slope": slope, "r": r, "p_raw": None, "ci": None}
    se = math.sqrt((sse / df) / sxx)
    critique = t_critical(df, confidence)
    stat = slope / se
    return desc, {"status": "computed", "design": "correlation_and_ols_slope", "slope": slope, "intercept": intercept, "r": r, "r_squared": r * r if r is not None else None, "effect_name": "unstandardized OLS slope and Pearson r", "effect": slope, "statistic_name": "slope t", "statistic": stat, "df": df, "se": se, "p_raw": t_two_sided_p(stat, df), "ci": [slope - critique * se, slope + critique * se], "confidence_level": confidence, "residual_diagnostic": {"sse": sse, "residual_mean_abs": mean([abs(x) for x in residuals])}, "method": "single prespecified linear slope; linearity, independent observations and homoscedastic residual variance require design/review"}


def build_result(case_path, values_path, output):
    case = json.loads(case_path.read_text(encoding="utf-8"))
    values = json.loads(values_path.read_text(encoding="utf-8"))
    if not isinstance(case.get("output_contract"), dict) or not isinstance(values, dict) or set(case["output_contract"]) != set(values) or "case_id" not in case or "case_id" in values:
        raise AnalysisError("contract adapter requires exact object keys and case_id")
    result = {"case_id": case["case_id"]}
    result.update(values)
    dump_json(output / "result.json", result)


def main():
    parser = argparse.ArgumentParser(description="Focused independent, paired and correlation analyses with recalculation evidence.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--case", type=Path)
    parser.add_argument("--task", type=Path)
    parser.add_argument("--resources", type=Path)
    parser.add_argument("--contract-values", type=Path)
    parser.add_argument("--confidence", type=float, default=0.95)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    args.work.mkdir(parents=True, exist_ok=True)
    started = UTC()
    try:
        if not 0 < args.confidence < 1:
            raise AnalysisError("confidence must be between 0 and 1")
        for path in (args.input, args.plan):
            if not path.is_file():
                raise AnalysisError("required file missing: %s" % path)
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        if plan.get("schema_version") != "1.0" or not isinstance(plan.get("analyses"), list) or not plan["analyses"]:
            raise AnalysisError("plan requires schema_version 1.0 and non-empty analyses")
        if not 0 <= float(plan.get("max_missing_fraction", 1.0)) <= 1:
            raise AnalysisError("max_missing_fraction must be in [0,1]")
        with args.input.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise AnalysisError("CSV header absent or duplicated")
            all_rows = list(reader)
        if not all_rows:
            raise AnalysisError("empty input")
        if any(None in row or any(value is None for value in row.values()) for row in all_rows):
            raise AnalysisError("malformed CSV row")
        missing_tokens = set(plan.get("missing_tokens", ["", "NA"]))
        rows, duplicate_removed = [], 0
        seen_exact = set()
        for source_row, row in enumerate(all_rows, 2):
            if plan.get("drop_exact_duplicates_authorized"):
                signature = json.dumps(row, ensure_ascii=False, sort_keys=True)
                if signature in seen_exact:
                    duplicate_removed += 1
                    continue
                seen_exact.add(signature)
            clean = {key: ("" if value.strip() in missing_tokens else value.strip()) for key, value in row.items()}
            clean["__source_row"] = str(source_row)
            rows.append(clean)
        output_rows, descriptive, inferences = [], [], []
        for spec in plan["analyses"]:
            design = spec.get("design")
            if design not in {"independent", "paired", "correlation"}:
                raise AnalysisError("unsupported or ambiguous design: %s" % design)
            analysis_id = spec.get("analysis_id")
            if not analysis_id or any(x["analysis_id"] == analysis_id for x in inferences):
                raise AnalysisError("analysis_id must be non-empty and unique")
            if design == "correlation":
                required = {"x", "y"}
            else:
                required = {"outcome", "group", "group_levels"}
                if design == "paired":
                    required.add("subject")
            if not required <= set(spec):
                raise AnalysisError("analysis %s missing plan fields %s" % (analysis_id, sorted(required - set(spec))))
            columns = set(spec.get(key, "") for key in ("outcome", "group", "subject", "x", "y"))
            if any(column and column not in reader.fieldnames for column in columns):
                raise AnalysisError("analysis %s references missing column" % analysis_id)
            if design == "paired":
                group_desc, inference, pair_audit = paired_analysis(rows, spec, args.confidence)
                descriptive.append({"analysis_id": analysis_id, "scope": "within_pair_differences", **group_desc})
                fraction = missing_fraction(pair_audit["missing_pairs"], pair_audit["candidate_pairs"])
                pair_audit["missing_fraction"] = fraction
                if fraction > float(plan.get("max_missing_fraction", 1.0)):
                    inference = {"status": "blocked_missing_threshold", "p_raw": None, "missing_fraction": fraction}
                inference["pair_audit"] = pair_audit
            elif design == "correlation":
                desc, inference = correlation_analysis(rows, spec, args.confidence)
                fraction = missing_fraction(desc["missing_or_invalid_pairs"], len(rows))
                desc["missing_fraction"] = fraction
                descriptive.append({"analysis_id": analysis_id, "scope": "%s and %s pairs" % (spec["x"], spec["y"]), **desc})
                if fraction > float(plan.get("max_missing_fraction", 1.0)):
                    inference = {"status": "blocked_missing_threshold", "p_raw": None, "missing_fraction": fraction}
            else:
                levels = spec["group_levels"]
                if not isinstance(levels, list) or len(levels) != 2 or levels[0] == levels[1]:
                    raise AnalysisError("independent analysis requires exactly two distinct prespecified levels")
                values, missing = {levels[0]: [], levels[1]: []}, {levels[0]: 0, levels[1]: 0}
                unexpected = 0
                for row in rows:
                    level = row.get(spec["group"], "")
                    if level not in values:
                        unexpected += 1
                        continue
                    try:
                        value = float(row[spec["outcome"]])
                        if not math.isfinite(value):
                            raise ValueError("nonfinite")
                        values[level].append(value)
                    except (ValueError, TypeError):
                        missing[level] += 1
                for level in levels:
                    descriptive.append({"analysis_id": analysis_id, "scope": level, **describe(values[level], missing[level], spec.get("unit"))})
                missing_rates = {level: missing_fraction(missing[level], missing[level] + len(values[level])) for level in levels}
                if any(rate > float(plan.get("max_missing_fraction", 1.0)) for rate in missing_rates.values()):
                    inference = {"analysis_id": analysis_id, "design": design, "status": "blocked_missing_threshold", "p_raw": None, "missing_fraction_by_group": missing_rates}
                    inferences.append(inference)
                    continue
                inference = independent_analysis(values[levels[0]], values[levels[1]], levels[0], levels[1], spec, args.confidence)
                inference["unexpected_or_out_of_scope_rows"] = unexpected
            inference["analysis_id"] = analysis_id
            inference["design_evidence"] = spec.get("design_evidence")
            inference["target_estimand"] = spec.get("target_estimand")
            inference["practical_significance"] = "requires domain threshold from task; not inferred from p"
            inference["assumptions"] = {
                "independence": "inferred only from declared design, not proven by a test",
                "distribution": "see descriptive diagnostics; no automatic test-based switching",
                "paired_structure" if design == "paired" else "variance": "paired complete cases" if design == "paired" else ("residual review required" if design == "correlation" else "Welch does not assume equal variances"),
                "multiplicity": "adjusted after all configured family p values are available"
            }
            inferences.append(inference)
        bh_adjust(inferences)
        statuses = Counter(x["status"] for x in inferences)
        status = "complete" if all(x["status"] == "computed" for x in inferences) else "complete_with_limitations"
        payload = {"schema_version": "1.0", "status": status, "descriptive": descriptive, "inference": inferences, "multiplicity": {"family": plan.get("family", "all analyses in this plan"), "method": "Benjamini-Hochberg"}, "known_unsupported_designs": ["clustered/repeated beyond two-condition pairing", "multi-group omnibus models", "causal estimation without design"]}
        dump_json(args.output / "analysis.json", payload)
        fields = sorted({key for row in descriptive for key in row})
        with (args.output / "descriptive.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
            writer.writeheader()
            for row in descriptive:
                writer.writerow({k: json.dumps(row.get(k), ensure_ascii=False) if isinstance(row.get(k), (dict, list)) else row.get(k) for k in fields})
        checks = {"raw_rows": len(all_rows), "duplicate_rows_removed": duplicate_removed, "analysis_rows": len(rows), "row_reconciliation": len(all_rows) == len(rows) + duplicate_removed, "numeric_rechecks": [{"analysis_id": x["analysis_id"], **x["independent_numeric_recheck"]} for x in inferences if "independent_numeric_recheck" in x], "distributed_units_preserved": True}
        checks["numeric_rechecks_passed"] = all(all(value < 1e-12 for key, value in row.items() if key != "analysis_id") for row in checks["numeric_rechecks"])
        if not checks["row_reconciliation"] or not checks["numeric_rechecks_passed"]:
            raise AnalysisError("independent numeric recheck failed")
        dump_json(args.output / "reconciliation.json", checks)
        lines = ["# 统计分析结果（自动同源生成）", "", "状态：%s。" % status, "", "## 设计与数据口径", "", "原始 %d 行；授权完全重复移除 %d 行；进入分析 %d 行。缺失不作零或均值填补；Tukey 异常仅诊断不删除。" % (len(all_rows), duplicate_removed, len(rows)), "", "设计证据和目标估计来自 plan；独立、配对或相关结构不由检验事后猜测。", "", "## 结果", ""]
        for item in inferences:
            lines.append("### %s" % item["analysis_id"])
            lines.append("")
            if item["status"] == "computed":
                estimate = item.get("mean_difference") if "mean_difference" in item else item.get("slope")
                estimand = "均值差/配对均值差" if "mean_difference" in item else "OLS 斜率"
                lines.append("区间所对应估计（%s）=%s；置信水平=%s；区间=%s；p_raw=%s；p_BH=%s。" % (estimand, estimate, item.get("confidence_level"), item.get("ci"), item.get("p_raw"), item.get("p_adjusted")))
                lines.append("")
                lines.append("效应量%s=%s。置信区间对应上述未标准化估计，不冒充标准化效应量区间；描述估计不确定性而非个体范围。BH 控制配置族内 FDR，不证明因果、实际意义或‘有效’。" % (item.get("effect_name"), item.get("effect")))
            else:
                lines.append("未产生推断结果：%s（%s）。" % (item["status"], item.get("reason", "design or data limitation")))
            lines.append("")
        lines += ["## 剩余不确定性", "", "前提检查、缺失审计及方法状态见 analysis.json；不支持的设计和未决异常不得改写成显著结论。", ""]
        (args.output / "report.md").write_text("\n".join(lines), encoding="utf-8")
        sources = {}
        for label, path in (("input", args.input), ("plan", args.plan), ("case", args.case), ("task", args.task), ("resources", args.resources)):
            if path is not None:
                if not path.is_file():
                    raise AnalysisError("evidence file missing: %s" % path)
                sources[label] = {"name": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)}
        dump_json(args.output / "run_manifest.json", {"skill_version": VERSION, "status": "success", "started_at": started, "finished_at": UTC(), "exit_code": 0, "sources": sources, "environment": {"python": platform.python_version(), "stdlib_only": True}, "no_randomness": True, "changes": ["missing tokens normalized only; optional exact duplicate removal only when plan authorizes"], "uncertainties": ["unsupported inference remains null", "independence and practical meaning require study-design/domain evidence"]})
        if args.contract_values:
            if not args.case or not args.case.is_file():
                raise AnalysisError("--case required for result contract")
            build_result(args.case, args.contract_values, args.output)
        print("OK analyses=%d computed=%d limited=%d raw_rows=%d" % (len(inferences), statuses["computed"], len(inferences) - statuses["computed"], len(all_rows)))
        return 0
    except (AnalysisError, OSError, ValueError, KeyError, csv.Error, json.JSONDecodeError) as exc:
        dump_json(args.output / "run_failure.json", {"status": "failed", "started_at": started, "finished_at": UTC(), "exit_code": 2, "error_type": type(exc).__name__, "message": str(exc), "partial_outputs_retained": True})
        print("ERROR %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
