#!/usr/bin/env python3
"""Non-mutating, allow-listed SVG text/attribute editor; standard library only."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

ALLOWED = {"x", "y", "x1", "x2", "y1", "y2", "width", "height", "fill", "stroke", "stroke-width", "font-size", "data-value"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(source, changes_file, output, log):
    raw = source.read_bytes()
    text = raw.decode("utf-8")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("DTD/entity declarations are not accepted")
    if re.search(r"<image\b", text, re.I):
        raise ValueError("Raster <image> content is outside this editor's preservation contract")
    outcome = "edited" if output.exists() else "new"
    if source.resolve() == output.resolve():
        raise ValueError("Refusing in-place edit; choose a distinct output path")
    ops = json.loads(changes_file.read_text(encoding="utf-8"))
    if not isinstance(ops, list) or not ops or len(ops) > 50:
        raise ValueError("changes must be a non-empty array of at most 50 edits")
    intervals = []
    records = []
    for op in ops:
        if not isinstance(op, dict) or not isinstance(op.get("id"), str) or not re.fullmatch(r"[A-Za-z][\w:.-]*", op["id"]):
            raise ValueError("Every edit needs a safe, unique element id")
        ident = re.escape(op["id"])
        starts = list(re.finditer(r"<([A-Za-z_:][\w:.-]*)\b[^>]*\bid=\"" + ident + r"\"[^>]*>", text))
        if len(starts) != 1:
            raise ValueError("Expected exactly one target element: " + op["id"])
        start = starts[0]
        tag = start.group(1)
        opening = start.group(0)
        if opening.endswith("/>"):
            endpos = start.end()
            whole = opening
        else:
            close = re.search(r"</" + re.escape(tag) + r"\s*>", text[start.end():])
            if not close:
                raise ValueError("Missing close tag: " + op["id"])
            endpos = start.end() + close.end()
            whole = text[start.start():endpos]
        if "text" in op:
            if tag != "text" or opening.endswith("/>"):
                raise ValueError("Text edits require a non-empty <text> target")
            old = text[start.end():start.end() + close.start()]
            if "<" in old or ">" in old:
                raise ValueError("Nested text targets are not supported")
            if "expect_text" in op and op["expect_text"] != old:
                raise ValueError("Stale expected text for " + op["id"])
            if not isinstance(op["text"], str) or len(op["text"]) > 300:
                raise ValueError("Replacement text must be a string <=300 characters")
            newwhole = text[start.start():start.end()] + escape(op["text"]) + text[start.end() + close.start():endpos]
            oldvalue, newvalue = old, op["text"]
        else:
            newwhole = whole
            oldvalue = newvalue = None
        attributes = op.get("attributes", {})
        if not isinstance(attributes, dict) or not attributes and "text" not in op:
            raise ValueError("An edit needs text and/or attributes")
        if set(attributes) - ALLOWED:
            raise ValueError("Non-allow-listed attributes requested for " + op["id"])
        expected = op.get("expect_attributes", {})
        suffix = newwhole[len(opening):]
        newopening = opening
        for key, value in attributes.items():
            if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                raise ValueError("Attribute values must be scalar")
            match = re.search(r"\s" + re.escape(key) + r"=\"([^\"]*)\"", newopening)
            if not match:
                raise ValueError("Target attribute is absent: " + key)
            if key in expected and expected[key] != match.group(1):
                raise ValueError("Stale expected attribute: " + key)
            replacement = " " + key + '="' + escape(str(value), {'"': "&quot;"}) + '"'
            newopening = newopening[:match.start()] + replacement + newopening[match.end():]
        newwhole = newopening + suffix
        intervals.append((start.start(), endpos, whole, newwhole, op["id"]))
        records.append({"id": op["id"], "text_before": oldvalue, "text_after": newvalue, "attributes_after": attributes})
    intervals.sort()
    for a, b in zip(intervals, intervals[1:]):
        if a[1] > b[0]:
            raise ValueError("Overlapping edits are not accepted")
    pieces = []
    reverse = []
    cursor = 0
    for begin, end, before, after, _ in intervals:
        pieces.append(text[cursor:begin])
        pieces.append(after)
        reverse.append(before)
        cursor = end
    pieces.append(text[cursor:])
    updated = "".join(pieces)
    old_cursor = new_cursor = 0
    for begin, end, before, after, _ in intervals:
        prefix_len = begin - old_cursor
        if text[old_cursor:begin] != updated[new_cursor:new_cursor + prefix_len]:
            raise AssertionError("Non-target preservation proof failed before an edit")
        old_cursor = end
        new_cursor += prefix_len + len(after)
    if text[old_cursor:] != updated[new_cursor:]:
        raise AssertionError("Non-target preservation proof failed after the last edit")
    output.parent.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(updated, encoding="utf-8")
    report = {
        "status": "passed", "policy": "byte-preserving outside allow-listed element replacements",
        "source": source.name, "output": output.name, "source_output_before": outcome,
        "source_sha256": digest(raw), "edited_sha256": digest(updated.encode("utf-8")),
        "non_target_preserved": True, "changes": records
    }
    log.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("PASS source-preserving edit: %d target(s), original unchanged" % len(records))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--changes", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--log", type=Path, required=True)
    args = p.parse_args()
    try:
        run(args.source, args.changes, args.output, args.log)
    except Exception as exc:
        print("FAIL edit_svg: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
