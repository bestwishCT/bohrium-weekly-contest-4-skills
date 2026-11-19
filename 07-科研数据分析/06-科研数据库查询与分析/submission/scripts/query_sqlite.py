#!/usr/bin/env python3
"""Read-only, schema-grounded SQLite querying with alternative-query recalculation."""
import argparse
import csv
import hashlib
import json
import platform
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

VERSION = "1.0.0"
UTC = lambda: datetime.now(timezone.utc).isoformat()


class QueryError(Exception):
    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def dump_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def quote_identifier(name):
    return '"' + name.replace('"', '""') + '"'


def json_value(value):
    if isinstance(value, bytes):
        return {"$blob_hex": value.hex()}
    if isinstance(value, float) and (value != value or abs(value) == float("inf")):
        raise QueryError("non-finite query result cannot be represented", 4)
    return value


def json_row(row):
    return [json_value(value) for value in row]


def validate_sql(sql):
    if not isinstance(sql, str) or not sql.strip() or "\x00" in sql:
        raise QueryError("SQL must be non-empty text without NUL", 3)
    stripped = sql.strip()
    if stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()
    if ";" in stripped:
        raise QueryError("only one SQL statement is allowed", 3)
    if not re.match(r"(?is)^(?:SELECT|WITH)\b", stripped):
        raise QueryError("only SELECT or WITH query is allowed", 3)
    forbidden = [r"(?i)\bATTACH\b", r"(?i)\bDETACH\b", r"(?i)\bPRAGMA\b", r"(?i)\bload_extension\s*\(", r"(?i)\breadfile\s*\(", r"(?i)\bwritefile\s*\("]
    if any(re.search(pattern, stripped) for pattern in forbidden):
        raise QueryError("attachment, pragma, extension and filesystem SQL functions are forbidden", 3)
    if re.search(r"(?is)\b(?:INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP|TRUNCATE|VACUUM|REINDEX)\b", re.sub(r"'(?:''|[^'])*'", "''", stripped)):
        raise QueryError("write-like keywords forbidden in query text", 3)
    return stripped


def authorizer(action, arg1, arg2, db_name, source):
    denied_actions = []
    for name in ("INSERT", "UPDATE", "DELETE", "ALTER_TABLE", "DROP_INDEX", "DROP_TABLE", "DROP_TRIGGER", "DROP_VIEW", "CREATE_INDEX", "CREATE_TABLE", "CREATE_TEMP_INDEX", "CREATE_TEMP_TABLE", "CREATE_TEMP_TRIGGER", "CREATE_TEMP_VIEW", "CREATE_TRIGGER", "CREATE_VIEW", "ATTACH", "DETACH", "REINDEX", "ANALYZE"):
        if hasattr(sqlite3, "SQLITE_" + name):
            denied_actions.append(getattr(sqlite3, "SQLITE_" + name))
    if action in denied_actions:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or arg1 or "").lower() in {"load_extension", "readfile", "writefile"}:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def parameters(spec):
    values = spec.get("parameters", {})
    if not isinstance(values, (dict, list)):
        raise QueryError("SQL parameters must be object or list")
    return values


def schema_card(conn):
    objects = conn.execute("SELECT name,type,sql FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
    cards = []
    for name, kind, sql in objects:
        columns = [{"cid": row[0], "name": row[1], "declared_type": row[2], "not_null": bool(row[3]), "default": row[4], "primary_key_position": row[5]} for row in conn.execute("PRAGMA table_info(%s)" % quote_identifier(name))]
        foreign_keys = [{"id": row[0], "seq": row[1], "target_table": row[2], "from": row[3], "to": row[4], "on_update": row[5], "on_delete": row[6], "match": row[7]} for row in conn.execute("PRAGMA foreign_key_list(%s)" % quote_identifier(name))]
        count = conn.execute("SELECT COUNT(*) FROM %s" % quote_identifier(name)).fetchone()[0] if kind == "table" else None
        cards.append({"name": name, "kind": kind, "sql": sql, "row_count": count, "columns": columns, "foreign_keys": foreign_keys})
    return cards


def run_query(conn, spec, limit, unordered_allowed, role):
    sql = validate_sql(spec.get("sql"))
    params = parameters(spec)
    plan = [list(row) for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params)]
    cursor = conn.execute(sql, params)
    names = [item[0] for item in cursor.description]
    if not names or len(set(names)) != len(names):
        raise QueryError("result columns must be non-empty and unique: %s" % role, 4)
    records = cursor.fetchmany(limit + 1)
    if len(records) > limit:
        raise QueryError("result exceeds limit %d; no silent truncation" % limit, 4)
    if len(records) > 1 and not unordered_allowed and not re.search(r"(?is)\bORDER\s+BY\b", sql):
        raise QueryError("multi-row output requires ORDER BY or explicit allow_unordered", 2)
    rows = [json_row(row) for row in records]
    null_counts = {names[index]: sum(row[index] is None for row in rows) for index in range(len(names))}
    canonical = [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows]
    quality = {"row_count": len(rows), "columns": names, "null_counts": null_counts, "exact_duplicate_rows": len(canonical) - len(set(canonical)), "empty_is_not_filled": True}
    return {"role": role, "sql": sql, "parameters": params, "columns": names, "rows": rows, "query_plan": plan, "quality": quality}


def compare(main, alternative, tolerance):
    if main["columns"] != alternative["columns"]:
        return {"passed": False, "reason": "column order/name mismatch", "main_columns": main["columns"], "alternative_columns": alternative["columns"]}
    if len(main["rows"]) != len(alternative["rows"]):
        return {"passed": False, "reason": "row count mismatch", "main": len(main["rows"]), "alternative": len(alternative["rows"])}
    checks = []
    for index, (left, right) in enumerate(zip(main["rows"], alternative["rows"]), 1):
        for column, a, b in zip(main["columns"], left, right):
            same = False
            difference = None
            if isinstance(a, (int, float)) and not isinstance(a, bool) and isinstance(b, (int, float)) and not isinstance(b, bool):
                try:
                    difference = abs(Decimal(str(a)) - Decimal(str(b)))
                    same = difference <= Decimal(str(tolerance))
                except InvalidOperation:
                    same = False
            else:
                same = a == b
            checks.append({"row": index, "column": column, "main": a, "alternative": b, "absolute_difference": str(difference) if difference is not None else None, "passed": same})
    return {"passed": all(item["passed"] for item in checks), "method": "separate real alternative SQL against same immutable snapshot; ordered numeric Decimal comparison", "tolerance": tolerance, "checks": checks, "independent_of_official_gold": True}


def build_result(case_path, values_path, output):
    case = json.loads(case_path.read_text(encoding="utf-8"))
    values = json.loads(values_path.read_text(encoding="utf-8"))
    if not isinstance(case.get("output_contract"), dict) or not isinstance(values, dict) or set(case["output_contract"]) != set(values) or "case_id" not in case or "case_id" in values:
        raise QueryError("contract adapter requires exact object keys and case_id")
    result = {"case_id": case["case_id"]}
    result.update(values)
    dump_json(output / "result.json", result)


def main():
    parser = argparse.ArgumentParser(description="Execute read-only schema-grounded SQLite queries and real alternative SQL checks.")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--job", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--case", type=Path)
    parser.add_argument("--task", type=Path)
    parser.add_argument("--resources", type=Path)
    parser.add_argument("--contract-values", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    args.work.mkdir(parents=True, exist_ok=True)
    started, clock = UTC(), time.monotonic()
    source_hash = None
    conn = None
    try:
        if not args.db.is_file() or args.db.stat().st_size < 100:
            raise QueryError("authorized SQLite snapshot file missing or too small; never create a substitute", 2)
        header = args.db.read_bytes()[:16]
        if header != b"SQLite format 3\x00":
            raise QueryError("not a SQLite 3 snapshot", 2)
        source_hash = sha256(args.db)
        if not args.job.is_file():
            raise QueryError("job missing")
        for path in (args.case, args.task, args.resources):
            if path is not None and not path.is_file():
                raise QueryError("evidence file missing: %s" % path)
        job = json.loads(args.job.read_text(encoding="utf-8"))
        if job.get("schema_version") != "1.0" or not isinstance(job.get("query"), dict) or not isinstance(job.get("verification"), dict):
            raise QueryError("job requires query and independent verification specifications")
        limit = int(job.get("row_limit", 100000))
        if limit < 1 or limit > 1000000:
            raise QueryError("row_limit must be 1..1000000")
        # mode=ro prevents a missing or altered path from silently creating a new database.
        uri = args.db.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)
        conn.execute("PRAGMA query_only = ON")
        pragmas = {"query_only": True, "mode": "ro"}
        try:
            conn.execute("PRAGMA trusted_schema = OFF")
            pragmas["trusted_schema"] = False
        except sqlite3.Error as exc:
            pragmas["trusted_schema"] = "unavailable: " + str(exc)
        conn.set_authorizer(authorizer)
        steps = {"remaining": int(job.get("step_limit", 2000000))}
        if steps["remaining"] < 1000 or steps["remaining"] > 100000000:
            raise QueryError("step_limit must be 1000..100000000")
        def progress():
            steps["remaining"] -= 1000
            return 1 if steps["remaining"] <= 0 else 0
        conn.set_progress_handler(progress, 1000)
        cards = schema_card(conn)
        if not cards:
            raise QueryError("database schema has no user tables/views", 4)
        dump_json(args.output / "schema_card.json", {"source": {"name": args.db.name, "bytes": args.db.stat().st_size, "sha256": source_hash}, "sqlite_version": sqlite3.sqlite_version, "tables": cards, "provided_dictionary": job.get("dictionary"), "dictionary_conflicts": job.get("dictionary_conflicts", [])})
        step_limit_initial = int(job.get("step_limit", 2000000))
        steps["remaining"] = step_limit_initial
        primary = run_query(conn, job["query"], limit, bool(job.get("allow_unordered")), "main")
        if job.get("expected_columns") is not None and primary["columns"] != job["expected_columns"]:
            raise QueryError("main result schema differs from expected_columns", 4)
        (args.output / "query.sql").write_text(primary["sql"] + ";\n", encoding="utf-8")
        (args.output / "query_result.json").write_text(json.dumps({"status": "verification_pending", "columns": primary["columns"], "rows": primary["rows"], "quality": primary["quality"]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        diagnostics = []
        for diagnostic in job.get("diagnostics", []):
            if not isinstance(diagnostic, dict) or "name" not in diagnostic:
                raise QueryError("diagnostics require names")
            steps["remaining"] = step_limit_initial
            result = run_query(conn, diagnostic, min(limit, 10000), True, "diagnostic:" + diagnostic["name"])
            diagnostics.append(result)
        steps["remaining"] = step_limit_initial
        alternative = run_query(conn, job["verification"], limit, bool(job.get("allow_unordered")), "alternative")
        if re.sub(r"\s+", " ", primary["sql"]).strip() == re.sub(r"\s+", " ", alternative["sql"]).strip():
            raise QueryError("verification SQL must differ from main SQL to provide an alternative calculation", 2)
        (args.output / "verification.sql").write_text(alternative["sql"] + ";\n", encoding="utf-8")
        comparison = compare(primary, alternative, job.get("numeric_tolerance", 1e-12))
        dump_json(args.output / "quality_checks.json", {"main": primary["quality"], "alternative": alternative["quality"], "comparison": comparison, "diagnostics": diagnostics, "step_budget": {"configured": step_limit_initial, "remaining_after_alternative": steps["remaining"]}, "input_hash_after_queries": sha256(args.db), "input_unchanged": sha256(args.db) == source_hash})
        if not comparison["passed"]:
            raise QueryError("independent alternative SQL mismatch", 5)
        if sha256(args.db) != source_hash:
            raise QueryError("source snapshot changed during read-only execution", 4)
        with (args.output / "query_result.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(primary["columns"])
            for row in primary["rows"]:
                writer.writerow([json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else "" if value is None else value for value in row])
        dump_json(args.output / "query_result.json", {"status": "verified" if primary["rows"] else "verified_empty", "columns": primary["columns"], "rows": primary["rows"], "quality": primary["quality"], "field_units": job.get("result_units", {})})
        dump_json(args.output / "query_plan.json", {"main": primary["query_plan"], "alternative": alternative["query_plan"], "diagnostics": [{"name": x["role"], "plan": x["query_plan"]} for x in diagnostics]})
        field_notes = {"provided_dictionary": job.get("dictionary"), "provided_semantics": job.get("semantics"), "result_units": job.get("result_units", {}), "filters_and_grain": job.get("question_spec"), "join_path": job.get("join_path"), "uncertainties": job.get("uncertainties", ["Snapshot cannot support claims beyond recorded rows or external populations."]), "empty_result_guidance": "No rows are synthesized; verify filters, key matching and authorization."}
        dump_json(args.output / "field_notes.json", field_notes)
        receipt = {"skill_version": VERSION, "status": "success_with_rows" if primary["rows"] else "success_empty", "started_at": started, "finished_at": UTC(), "duration_seconds": time.monotonic() - clock, "exit_code": 0, "database": {"engine": "SQLite", "version": sqlite3.sqlite_version, "python_sqlite3_module": sqlite3.version, "snapshot_name": args.db.name, "snapshot_bytes": args.db.stat().st_size, "snapshot_sha256_before": source_hash, "snapshot_sha256_after": sha256(args.db), "pragmas": pragmas, "authorizer": True, "credential_values_logged": False}, "commands": {"entry": "query_sqlite.py (actual invocation recorded by caller)", "read_only_uri_mode": "ro", "main_sql_sha256": hashlib.sha256(primary["sql"].encode()).hexdigest(), "verification_sql_sha256": hashlib.sha256(alternative["sql"].encode()).hexdigest()}, "rows": primary["quality"]["row_count"], "uncertainties": field_notes["uncertainties"], "environment": {"python": platform.python_version(), "platform": platform.platform(), "stdlib_only": True}}
        receipt["evidence"] = {}
        for label, path in (("job", args.job), ("case", args.case), ("task", args.task), ("resources", args.resources)):
            if path is not None and path.is_file():
                receipt["evidence"][label] = {"name": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)}
        dump_json(args.output / "execution_receipt.json", receipt)
        if args.contract_values:
            if not args.case:
                raise QueryError("--case required for result contract")
            build_result(args.case, args.contract_values, args.output)
        print("OK sqlite=%s rows=%d alternatives_match=%s source_unchanged=%s" % (sqlite3.sqlite_version, len(primary["rows"]), comparison["passed"], source_hash == sha256(args.db)))
        return 0
    except (QueryError, OSError, sqlite3.Error, ValueError, KeyError, json.JSONDecodeError) as exc:
        code = exc.code if isinstance(exc, QueryError) else (4 if isinstance(exc, sqlite3.Error) else 2)
        failure = {"skill_version": VERSION, "status": "failed", "started_at": started, "finished_at": UTC(), "duration_seconds": time.monotonic() - clock, "exit_code": code, "error": {"type": type(exc).__name__, "message": str(exc)}, "source_sha256": source_hash, "source_sha256_after": sha256(args.db) if source_hash and args.db.is_file() else None, "no_query_result_fabricated": True, "partial_outputs_retained": True}
        dump_json(args.output / "run_failure.json", failure)
        print("ERROR %s" % exc, file=sys.stderr)
        return code
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
