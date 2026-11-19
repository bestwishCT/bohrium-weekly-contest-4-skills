#!/usr/bin/env python3
"""Finite-snapshot window differ, atomic state advancement and reminder idempotency."""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from common import digest, first_author, norm_doi, norm_text, norm_title, read_json, records_at, run, write_json


SUCCESS = {"success", "observed", "completed"}


def parse_time(value, timezone_name):
    if not norm_text(value) or not timezone_name:
        raise ValueError("missing timestamp or declared window timezone")
    zone = ZoneInfo(timezone_name)
    parsed = datetime.fromisoformat(norm_text(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def identity(record, index):
    doi = norm_doi(record.get("doi", ""))
    if doi:
        return "doi:" + doi
    if norm_text(record.get("arxiv_id", "")):
        return "arxiv:" + norm_text(record["arxiv_id"]).casefold()
    if norm_text(record.get("id", record.get("record_id", ""))):
        return "id:" + norm_text(record.get("id", record.get("record_id", "")))
    title, author, year = norm_title(record.get("title", "")), first_author(record), norm_text(record.get("year", ""))
    if title and author and year:
        return "title:" + digest([title, author, year])[:24]
    return ""


def scientific_fingerprint(record):
    ignored = {"indexed_at", "observed_at", "source_locator", "response_locator"}
    return digest({k: v for k, v in record.items() if k not in ignored and not k.startswith("_")})


def process(case, data, task, inv):
    subscription = data.get("subscription", {}) if isinstance(data, dict) else {}
    sid = norm_text(subscription.get("id", "default"))
    action = subscription.get("action", data.get("action", "view_increment"))
    state_path = Path(case.get("_runtime_work", "/app/work")) / "tracker_state.json"
    if state_path.exists():
        state = read_json(state_path)
    else:
        state = data.get("persisted_state", {"subscriptions": {}})
    state.setdefault("subscriptions", {})
    previous_state = state["subscriptions"].get(sid, {"watermark": subscription.get("previous_watermark"), "snapshot": {}, "ledger": {}})
    previous_snapshot = previous_state.get("snapshot", {})
    if not previous_snapshot:
        previous_snapshot = {}
        for index, record in enumerate(records_at(data, ["previous_snapshot", "baseline_records"])):
            key = identity(record, index)
            if key:
                previous_snapshot[key] = record
    current_records = records_at(data, ["current_snapshot", "current_records", "records"])
    timezone_name = subscription.get("timezone")
    time_field = subscription.get("time_field")
    start_raw, end_raw = subscription.get("window_start"), subscription.get("window_end")
    bounds = subscription.get("bounds", "[)")
    source_runs = records_at(data, ["source_runs", "sources", "queries"])
    complete_sources = bool(source_runs) and all(x.get("executed") is True and x.get("status") in SUCCESS for x in source_runs)
    if action == "pause" or subscription.get("paused") is True:
        state["subscriptions"][sid] = dict(previous_state, paused=True)
        write_json(state_path, state)
        return {
            "status": "paused", "status_detail": {"completed": ["subscription paused in local state"], "incomplete": ["window retrieval and delivery while paused"]},
            "subscription": subscription, "events": [], "reminders": [], "primary_results": [],
            "state_transition": {"watermark_from": previous_state.get("watermark"), "watermark_to": previous_state.get("watermark"), "advanced": False, "state_file": "tracker_state.json"},
            "uncertainties": ["Pause affects this local state file only; no external scheduler or subscription was changed."], "change_records": ["Paused local subscription without querying or delivering."],
            "_aliases": {"results": "events", "notifications": "reminders"},
        }
    window_errors, current, outside, duplicates = [], {}, [], []
    try:
        start, end = parse_time(start_raw, timezone_name), parse_time(end_raw, timezone_name)
        if start >= end or bounds not in {"[)", "[]", "(]", "()"}:
            raise ValueError("invalid or unsupported window bounds")
        window = {"start": start, "end": end, "start_raw": start_raw, "end_raw": end_raw, "timezone": timezone_name, "bounds": bounds, "time_field": time_field}
    except Exception as exc:
        window = None
        window_errors.append(str(exc))
    for index, record in enumerate(current_records):
        key = identity(record, index)
        if not key:
            window_errors.append("record %d has no stable DOI/arXiv/id/title-author-year identity" % index)
            continue
        if key in current:
            duplicates.append({"identity": key, "record_indices": current[key]["indices"] + [index], "action": "one event per stable identity; source variants retained"})
            current[key]["records"].append(record)
            current[key]["indices"].append(index)
            continue
        current[key] = {"records": [record], "indices": [index]}
    events, review = [], []
    for key in sorted(current):
        records = current[key]["records"]
        record = records[0]
        if window:
            try:
                if not time_field:
                    raise ValueError("time_field is not configured; publication date is not substituted")
                timestamp = parse_time(record.get(time_field), timezone_name)
                left = timestamp > window["start"] if bounds[0] == "(" else timestamp >= window["start"]
                right = timestamp < window["end"] if bounds[1] == ")" else timestamp <= window["end"]
                if not (left and right):
                    outside.append({"identity": key, "timestamp": record.get(time_field), "reason": "outside configured half/open/closed window"})
                    continue
            except Exception as exc:
                review.append({"identity": key, "reason": str(exc), "effect": "window membership uncertain; no reminder and no automatic watermark advancement"})
                continue
        else:
            continue
        previous = previous_snapshot.get(key)
        prior_version = previous.get("version") if isinstance(previous, dict) else None
        explicit_relation = record.get("version_of")
        if previous is None:
            related = next((old for old in previous_snapshot.values() if isinstance(old, dict) and (old.get("id") == explicit_relation or identity(old, 0) == explicit_relation)), None)
            kind = "version_update" if related else "new_to_snapshot"
        else:
            current_status, old_status = record.get("record_status"), previous.get("record_status")
            if current_status != old_status and current_status in {"retracted", "corrected", "withdrawn"}:
                kind = current_status + "_observed"
            elif prior_version != record.get("version"):
                kind = "version_update"
            elif scientific_fingerprint(previous) != scientific_fingerprint(record):
                kind = "metadata_update"
            else:
                kind = "known_record"
        if kind != "known_record":
            events.append({"identity": key, "event_type": kind, "scope": "relative to supplied local snapshot; not global novelty", "metadata": {k: record.get(k) for k in ["title", "authors", "year", "venue", "doi", "arxiv_id", "version", "record_status", time_field] if k}, "previous_identity": key if previous is not None else explicit_relation, "source_locators": sorted({norm_text(r.get("source_locator", "")) for r in records if norm_text(r.get("source_locator", ""))}), "decision_basis": "stable identity plus configured time-field window plus previous snapshot/version/status comparison"})
    previous_ledger = previous_state.get("ledger", {})
    ledger = dict(previous_ledger)
    reminders, suppressed = [], []
    can_advance = bool(window and complete_sources and not review and not window_errors)
    new_watermark = end_raw if can_advance else previous_state.get("watermark")
    combined = dict(previous_snapshot)
    if can_advance:
        for event in events:
            delivery_id = digest([sid, start_raw, end_raw, event["identity"], event["event_type"], event["metadata"]])[:32]
            if delivery_id in ledger:
                suppressed.append({"delivery_id": delivery_id, "reason": "same window event already entered ledger; duplicate preparation suppressed"})
                continue
            entry = {"delivery_id": delivery_id, "event": event, "channel": "none", "delivery_status": "prepared_not_delivered", "window": {"start": start_raw, "end": end_raw, "timezone": timezone_name}}
            ledger[delivery_id] = entry
            reminders.append(entry)
        for key, group in current.items():
            combined[key] = group["records"][0]
        state["subscriptions"][sid] = {"watermark": new_watermark, "snapshot": combined, "ledger": ledger, "paused": False, "last_window": {"start": start_raw, "end": end_raw, "timezone": timezone_name}, "last_window_complete": True}
        write_json(state_path, state)
    status = "partial" if window_errors or review or not complete_sources else "empty" if not current_records or not events else "completed"
    return {
        "status": status, "status_detail": {"completed": ["window-bound snapshot diff", "identity deduplication", "atomic local ledger update"], "incomplete": window_errors + [x["reason"] for x in review] + ([] if complete_sources else ["one or more required sources failed or were unexecuted"])},
        "subscription": subscription, "window": {"start": start_raw, "end": end_raw, "timezone": timezone_name, "bounds": bounds, "time_field": time_field, "semantics": "configured field only; publication time is not implicitly indexing time"},
        "source_runs": source_runs, "events": events, "primary_results": events, "outside_window": outside,
        "duplicate_identities": duplicates, "window_uncertain": review, "reminders": reminders, "suppressed_reminders": suppressed,
        "state_transition": {"subscription_id": sid, "watermark_from": previous_state.get("watermark"), "watermark_to": new_watermark, "advanced": can_advance and new_watermark != previous_state.get("watermark"), "window_complete": can_advance, "ledger_size_before": len(previous_ledger), "ledger_size_after": len(ledger), "snapshot_size_after": len(combined), "state_file": "tracker_state.json"},
        "metrics": {"current_input_records": len(current_records), "unique_current_identities": len(current), "events": len(events), "prepared_not_delivered": len(reminders), "duplicates_suppressed": len(suppressed), "outside_window": len(outside), "window_uncertain": len(review)},
        "uncertainties": ["No real scheduler or reminder channel is configured; prepared_not_delivered is not a delivery receipt.", "New_to_snapshot means new relative to this finite local snapshot, not globally new research or latest publication.", "Failed/uncertain windows never advance the watermark."],
        "change_records": ["Deduplicated current identities and compared versions/status with the previous local snapshot.", "Committed watermark, snapshot and reminder ledger only for complete windows; failed windows leave persisted state untouched."],
        "_aliases": {"results": "events", "new_records": "events", "notifications": "reminders", "state": "state_transition", "metrics_summary": "metrics"},
    }


if __name__ == "__main__":
    raise SystemExit(run("idempotent-snapshot-tracking", process))
