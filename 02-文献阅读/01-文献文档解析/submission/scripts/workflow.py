#!/usr/bin/env python3
"""Auditable, limited PDF text-layer parser; never invents OCR or semantics."""
import argparse
import json
import re
import sys
import zlib
from pathlib import Path
from statistics import median

from common import Run, add_arguments, stable_id

NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
OBJECT = re.compile(r"(\d+)\s+(\d+)\s+obj\b(.*?)endobj", re.S)
STRING = r"\((?:\\.|[^\\)])*\)"
OPERATOR = re.compile(
    r"(?P<matrix>" + NUMBER + r"\s+" + NUMBER + r"\s+" + NUMBER + r"\s+" + NUMBER + r"\s+(" + NUMBER + r")\s+(" + NUMBER + r")\s+Tm)"
    r"|(?P<move>(" + NUMBER + r")\s+(" + NUMBER + r")\s+T[Dd])"
    r"|(?P<show>" + STRING + r"\s*Tj)"
    r"|(?P<array>\[(?:[^\]]|\](?!\s*TJ))*\]\s*TJ)"
    r"|(?P<next>T\*)", re.S)


def pdf_string(token):
    body = token[token.find("(") + 1:token.rfind(")")]
    out = bytearray()
    index = 0
    escapes = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
    while index < len(body):
        char = body[index]
        if char == "\\" and index + 1 < len(body):
            index += 1
            nxt = body[index]
            if nxt.isdigit():
                octal = nxt
                while len(octal) < 3 and index + 1 < len(body) and body[index + 1].isdigit():
                    index += 1
                    octal += body[index]
                out.append(int(octal, 8) & 255)
            elif nxt == "\n":
                pass
            else:
                out.append(escapes.get(ord(nxt), ord(nxt)))
        else:
            out.append(ord(char))
        index += 1
    if out.startswith(b"\xfe\xff"):
        return out[2:].decode("utf-16-be", errors="replace")
    return out.decode("latin-1", errors="replace")


def text_operators(stream):
    try:
        content = stream.decode("latin-1")
    except AttributeError:
        content = stream
    chunks = []
    for block in re.findall(r"BT(.*?)ET", content, re.S):
        x = y = 0.0
        line_y = y
        for match in OPERATOR.finditer(block):
            if match.group("matrix"):
                numbers = re.findall(NUMBER, match.group("matrix"))
                x, y = float(numbers[-2]), float(numbers[-1])
                line_y = y
            elif match.group("move"):
                numbers = re.findall(NUMBER, match.group("move"))
                x += float(numbers[0])
                y += float(numbers[1])
                line_y = y
            elif match.group("next"):
                y -= 12.0
                line_y = y
            elif match.group("show"):
                text = pdf_string(match.group("show"))
                if text:
                    chunks.append({"text": text, "x": x, "y": y})
                    x += max(1, len(text)) * 5.0
            elif match.group("array"):
                pieces = re.findall(STRING, match.group("array"), re.S)
                text = "".join(pdf_string(piece) for piece in pieces)
                if text:
                    chunks.append({"text": text, "x": x, "y": y})
                    x += max(1, len(text)) * 5.0
    return chunks


def parse_pdf(path):
    raw = Path(path).read_bytes()
    if not raw.startswith(b"%PDF-"):
        raise ValueError("not a PDF file")
    if re.search(br"/Encrypt\b", raw):
        raise ValueError("encrypted PDF requires an authorized decryption/OCR tool")
    objects = {int(match.group(1)): match.group(3) for match in OBJECT.finditer(raw.decode("latin-1"))}
    pages = [(number, body) for number, body in objects.items()
             if re.search(r"/Type\s*/Page\b", body) and not re.search(r"/Type\s*/Pages\b", body)]
    if not pages:
        raise ValueError("no directly recoverable page objects; complex xref streams are unsupported")
    pages.sort()
    result = []
    warnings = []
    for page_number, (_, body) in enumerate(pages, 1):
        chunks = []
        references = re.findall(r"/Contents\s+(\d+)\s+\d+\s+R", body)
        if not references:
            references = [str(number) for number, candidate in objects.items() if b"stream" in candidate.encode("latin-1")]
            warnings.append("page content references were ambiguous; conservative stream fallback used")
        for reference in references:
            obj = objects.get(int(reference), "")
            stream_match = re.search(r"stream\r?\n(.*?)\r?\nendstream", obj, re.S)
            if not stream_match:
                continue
            stream = stream_match.group(1).encode("latin-1")
            filters = re.findall(r"/Filter\s*/(\w+)", obj)
            try:
                if "FlateDecode" in filters:
                    stream = zlib.decompress(stream)
                unsupported = [name for name in filters if name != "FlateDecode"]
                if unsupported:
                    warnings.append("unsupported filters on page %d: %s" % (page_number, ",".join(unsupported)))
                    continue
                chunks.extend(text_operators(stream))
            except (zlib.error, UnicodeError) as error:
                warnings.append("page %d stream failed: %s" % (page_number, error))
        result.append({"page": page_number, "chunks": chunks})
        if not chunks:
            warnings.append("page %d has no recovered text layer; OCR was not run" % page_number)
    return result, warnings


def physical_lines(page):
    """Group text operators into lines without discarding same-line segments."""
    groups = []
    for chunk in sorted(page["chunks"], key=lambda item: (-item["y"], item["x"])):
        target = next((group for group in groups if abs(group["y"] - chunk["y"]) <= 2.0), None)
        if target is None:
            target = {"y": chunk["y"], "chunks": []}
            groups.append(target)
        target["chunks"].append(chunk)
    lines = []
    for group in groups:
        chunks = sorted(group["chunks"], key=lambda item: item["x"])
        segment_chunks = []
        for chunk in chunks:
            if not segment_chunks:
                segment_chunks.append([chunk])
                continue
            previous = segment_chunks[-1][-1]
            previous_end = previous["x"] + len(previous["text"]) * 5
            if chunk["x"] - previous_end > 40:
                segment_chunks.append([chunk])
            else:
                segment_chunks[-1].append(chunk)
        segments = []
        for index, items in enumerate(segment_chunks, 1):
            text = " ".join(item["text"].strip() for item in items if item["text"].strip()).strip()
            if not text:
                continue
            segments.append({
                "text": text, "x": min(item["x"] for item in items), "y": group["y"],
                "x1": max(item["x"] + len(item["text"]) * 5 for item in items),
                "segment_index": index,
            })
        if segments:
            lines.append({"y": group["y"], "segments": segments})
    return sorted(lines, key=lambda item: (-item["y"], item["segments"][0]["x"]))


def detect_layout(lines):
    """Detect one page-local two-column gap, or conservatively retain y/x order."""
    segments = [segment for line in lines for segment in line["segments"]]
    base = {
        "mode": "single_column",
        "status": "no_reliable_two_column_boundary",
        "column_boundary": None,
        "reading_order": "top_to_bottom_then_left_to_right",
        "evidence": {"line_count": len(lines), "segment_count": len(segments)},
        "uncertainty": None,
        "scope": "page-local two-column heuristic only; not a universal PDF layout model",
    }
    if not segments:
        base["status"] = "no_text_layer_segments"
        return base, None
    coordinates = sorted({value for segment in segments for value in (segment["x"], segment["x1"])})
    candidates = []
    for gap_start, gap_end in zip(coordinates, coordinates[1:]):
        if gap_end - gap_start < 12:
            continue
        midpoint = (gap_start + gap_end) / 2.0
        # Wide headings/footers may cross the gap; the other lines provide support.
        paired_rows = 0
        left_rows = right_rows = wide_rows = 0
        for line in lines:
            left = any(segment["x1"] <= gap_start for segment in line["segments"])
            right = any(segment["x"] >= gap_end for segment in line["segments"])
            wide = any(segment["x"] < midpoint < segment["x1"] for segment in line["segments"])
            paired_rows += int(left and right)
            left_rows += int(left)
            right_rows += int(right)
            wide_rows += int(wide)
        if not left_rows or not right_rows:
            continue
        if not paired_rows and min(left_rows, right_rows) < 2:
            continue
        candidates.append({
            "gap_start": gap_start, "gap_end": gap_end, "boundary": midpoint,
            "gap_width": gap_end - gap_start, "paired_rows": paired_rows,
            "left_rows": left_rows, "right_rows": right_rows, "wide_rows": wide_rows,
        })
    if not candidates:
        if any(len(line["segments"]) >= 2 for line in lines):
            message = ("same-line segments did not yield a reliable two-column gap; "
                       "conservative single-column y/x order used")
            base["uncertainty"] = message
            return base, message
        base["status"] = "single_column_no_boundary_evidence"
        return base, None

    def support(candidate):
        return (candidate["paired_rows"], min(candidate["left_rows"], candidate["right_rows"]),
                candidate["left_rows"] + candidate["right_rows"])

    best_support = max(support(candidate) for candidate in candidates)
    supported = [candidate for candidate in candidates if support(candidate) == best_support]
    widest = max(candidate["gap_width"] for candidate in supported)
    best = next(candidate for candidate in supported if candidate["gap_width"] == widest)
    comparable = [candidate for candidate in supported
                  if candidate["gap_width"] * 1.25 >= widest]
    base["evidence"]["alternative_gaps"] = [
        {key: candidate[key] for key in ("gap_start", "gap_end", "paired_rows", "left_rows", "right_rows")}
        for candidate in supported[:6]
    ]
    if len(comparable) > 1:
        message = ("multiple similarly supported page-local column gaps were detected; "
                   "conservative single-column y/x order used")
        base["status"] = "ambiguous_column_boundaries"
        base["uncertainty"] = message
        return base, message
    base.update({
        "mode": "two_column", "status": "two_column_detected",
        "column_boundary": best["boundary"],
        "reading_order": "wide_top_then_left_then_right_then_wide_bottom",
        "uncertainty": None,
    })
    base["evidence"].update({
        "method": "midpoint of a gap between actual line-segment x intervals; wide lines may cross it",
        "gap_start": best["gap_start"], "gap_end": best["gap_end"],
        "gap_width": best["gap_width"], "paired_rows": best["paired_rows"],
        "left_rows": best["left_rows"], "right_rows": best["right_rows"],
        "wide_rows": best["wide_rows"],
    })
    return base, None


def layout_page(page):
    """Return ordered segment blocks, preserved physical lines, and layout evidence."""
    lines = physical_lines(page)
    layout, uncertainty = detect_layout(lines)
    boundary = layout["column_boundary"]
    segments = []
    for line_index, line in enumerate(lines, 1):
        line["line_index"] = line_index
        for segment in line["segments"]:
            segment["line_index"] = line_index
            if boundary is None:
                segment["column"] = "wide"
            elif segment["x1"] <= boundary:
                segment["column"] = "left"
            elif segment["x"] >= boundary:
                segment["column"] = "right"
            else:
                segment["column"] = "wide"
            segments.append(segment)
    if boundary is None:
        ordered = sorted(segments, key=lambda item: (-item["y"], item["x"], item["segment_index"]))
    else:
        highest = max(segment["y"] for segment in segments)
        top = [segment for segment in segments if segment["column"] == "wide" and segment["y"] >= highest - 2]
        bottom = [segment for segment in segments if segment["column"] == "wide" and segment not in top]
        ordered = (sorted(top, key=lambda item: (-item["y"], item["x"]))
                   + sorted([item for item in segments if item["column"] == "left"],
                            key=lambda item: (-item["y"], item["x"]))
                   + sorted([item for item in segments if item["column"] == "right"],
                            key=lambda item: (-item["y"], item["x"]))
                   + sorted(bottom, key=lambda item: (-item["y"], item["x"])))
    return ordered, lines, layout, uncertainty


def line_blocks(page):
    """Compatibility wrapper for callers that only need ordered segment blocks."""
    return layout_page(page)[0]


SECTION_HEADING = re.compile(
    r"^(?P<number>\d{1,2}(?:\.\d{1,2}){0,5})\.?(?:\s+(?P<title>\S.*?))?\s*$"
)


def section_candidate(text):
    """Recognize short numbered heading shapes, not arbitrary numeric paragraphs."""
    match = SECTION_HEADING.fullmatch(text.strip())
    if not match:
        return None
    title = (match.group("title") or "").strip()
    if len(title) > 160 or len(title.split()) > 12:
        return None
    if re.search(r"[.!?;]$", title):
        return None
    number = match.group("number")
    return {
        "section_number": number,
        "section_level": number.count(".") + 1,
        "title": title or number,
    }


def classify(text):
    if section_candidate(text) or re.match(r"^(?:abstract|references)\s*$", text, re.I):
        return "heading_or_section"
    if re.match(r"^(?:table|figure|fig\.)\s*\d+", text, re.I):
        return "caption"
    if re.search(r"\(\d+\)\s*$", text) and re.search(r"[=+−-]", text):
        return "formula_candidate"
    return "paragraph"


def find_table_candidates(lines, page_number, case_id, source_path, block_ids):
    """Find adjacent, aligned multi-segment lines; never assert semantic table truth."""
    spacings = [abs(lines[index]["y"] - lines[index + 1]["y"])
                for index in range(len(lines) - 1)
                if lines[index]["y"] != lines[index + 1]["y"]]
    typical_spacing = median(spacings) if spacings else 24.0
    max_row_gap = min(96.0, max(12.0, typical_spacing * 2.5))
    start_tolerance = min(36.0, max(12.0, typical_spacing))
    candidates = []
    index = 0
    while index < len(lines):
        cell_count = len(lines[index]["segments"])
        if cell_count < 2:
            index += 1
            continue
        run = [lines[index]]
        next_index = index + 1
        while next_index < len(lines):
            previous = run[-1]
            current = lines[next_index]
            if len(current["segments"]) != cell_count:
                break
            if current["y"] == previous["y"] or abs(current["y"] - previous["y"]) > max_row_gap:
                break
            aligned = all(
                abs(current["segments"][cell]["x"] - run[0]["segments"][cell]["x"]) <= start_tolerance
                for cell in range(cell_count)
            )
            if not aligned:
                break
            run.append(current)
            next_index += 1
        if len(run) < 2:
            index += 1
            continue
        rows = []
        source_block_ids = []
        for row_index, line in enumerate(run, 1):
            cells = []
            for cell_index, segment in enumerate(line["segments"], 1):
                block_id = block_ids[(line["line_index"], segment["segment_index"])]
                source_block_ids.append(block_id)
                cells.append({
                    "cell_index": cell_index, "block_id": block_id, "text": segment["text"],
                    "bbox_approx": [segment["x"], segment["y"]],
                    "bbox_extent_approx": [segment["x"], segment["y"], segment["x1"], segment["y"]],
                })
            rows.append({
                "row_index": row_index, "page": page_number, "line_id": line["line_id"],
                "y": line["y"], "cells": cells,
            })
        candidate_id = stable_id("tc", case_id, page_number, *source_block_ids)
        candidates.append({
            "candidate_id": candidate_id, "page": page_number,
            "row_count": len(rows), "column_count": cell_count, "rows": rows,
            "source": {
                "path": str(source_path), "page": page_number,
                "block_ids": source_block_ids, "line_ids": [line["line_id"] for line in run],
            },
            "evidence": {
                "minimum_adjacent_rows": 2, "observed_adjacent_rows": len(run),
                "consistent_segment_count": cell_count,
                "cell_start_tolerance_points": start_tolerance,
                "maximum_row_gap_points": max_row_gap,
            },
            "structure_note": ("row/column structure candidate only; merged cells and cross-page "
                               "continuation are not established; scientific meaning not inferred"),
            "merged_cells": "not_evaluated",
            "cross_page_continuation": "not_evaluated",
        })
        index = next_index
    return candidates


def main():
    args = add_arguments(argparse.ArgumentParser(description=__doc__)).parse_args()
    try:
        run = Run(args)
    except Exception as error:
        print(json.dumps({"status": "input_error", "error": str(error)}), file=sys.stderr)
        return 2
    filename = run.case.get("pdf_path") or run.case.get("pdf") or run.case.get("document")
    if not filename:
        run.uncertainty.append("case.json does not identify an actual PDF")
        run.finish({"pages": [], "blocks": [], "status": "input_error"}, code=2)
    path = run.path(filename)
    try:
        pages, warnings = parse_pdf(path)
        run.uncertainty.extend(sorted(set(warnings)))
        blocks = []
        pages_output = []
        table_candidates = []
        sections = []
        page_layouts = []
        section_stack = {}
        for page in pages:
            ordered, lines, layout, layout_uncertainty = layout_page(page)
            if layout_uncertainty:
                run.uncertainty.append("page %d: %s" % (page["page"], layout_uncertainty))
            for line in lines:
                line["line_id"] = stable_id(
                    "l", run.case_id, page["page"], line["line_index"], line["y"],
                    "|".join(segment["text"] for segment in line["segments"]),
                )
            block_ids = {}
            for sequence, segment in enumerate(ordered, 1):
                kind = classify(segment["text"])
                block_id = stable_id("b", run.case_id, page["page"], sequence, segment["text"])
                block_ids[(segment["line_index"], segment["segment_index"])] = block_id
                section = section_candidate(segment["text"])
                block = {
                    "block_id": block_id,
                    "page": page["page"], "sequence": sequence, "kind": kind,
                    "text": segment["text"], "reading_column": segment["column"],
                    "line_id": lines[segment["line_index"] - 1]["line_id"],
                    "source_line_index": segment["line_index"],
                    "segment_index": segment["segment_index"],
                    "bbox_approx": [segment["x"], segment["y"]],
                    "bbox_extent_approx": [segment["x"], segment["y"], segment["x1"], segment["y"]],
                    "extraction": "uncompressed_pdf_text_operator",
                    "correction": None,
                    "section_level": None,
                    "structure_note": "caption/formula candidate only; scientific meaning not inferred" if kind != "paragraph" else None,
                }
                if section:
                    parent_level = max((level for level in section_stack
                                        if level < section["section_level"]), default=None)
                    parent = section_stack.get(parent_level)
                    path_ids = (list(parent["path_ids"]) if parent else []) + [block_id]
                    block.update({
                        "section_id": block_id, "section_level": section["section_level"],
                        "parent_section_id": parent["section_id"] if parent else None,
                        "section_path_ids": path_ids,
                        "structure_note": ("numbered section structural candidate only; hierarchy follows "
                                           "extracted order and is not a verified table of contents"),
                    })
                    sections.append({
                        "section_id": block_id, "block_id": block_id, "page": page["page"],
                        "section_number": section["section_number"], "title": section["title"],
                        "section_level": section["section_level"],
                        "parent_section_id": parent["section_id"] if parent else None,
                        "section_path_ids": path_ids,
                        "source": {"path": str(path), "page": page["page"], "block_id": block_id},
                        "structure_note": block["structure_note"],
                    })
                    section_stack = {level: value for level, value in section_stack.items()
                                     if level < section["section_level"]}
                    section_stack[section["section_level"]] = {
                        "section_id": block_id, "path_ids": path_ids,
                    }
                blocks.append(block)
            page_lines = []
            for line in lines:
                page_lines.append({
                    "line_id": line["line_id"], "page": page["page"], "y": line["y"],
                    "segments": [{
                        "segment_index": segment["segment_index"],
                        "block_id": block_ids[(line["line_index"], segment["segment_index"])],
                        "text": segment["text"], "reading_column": segment["column"],
                        "bbox_approx": [segment["x"], segment["y"]],
                        "bbox_extent_approx": [segment["x"], segment["y"], segment["x1"], segment["y"]],
                    } for segment in line["segments"]],
                })
            table_candidates.extend(find_table_candidates(
                lines, page["page"], run.case_id, path, block_ids,
            ))
            pages_output.append({
                "page": page["page"], "text_chunks": len(page["chunks"]),
                "layout": layout, "lines": page_lines,
            })
            page_layouts.append(dict(layout, page=page["page"]))
        status = "partial" if run.uncertainty else "success"
        code = 2 if status == "partial" else 0
        run.changes.extend([
            "detected page-local column gaps from actual x intervals; ambiguous pages use conservative single-column order",
            "retained same-line segments and added row/cell table candidates without merged-cell or cross-page claims",
            "added linked numbered section-level structural candidates and conservative heading filters",
        ])
        limitations = [
            "OCR was not run; scanned or image-only text is unsupported",
            "only directly recoverable page objects and supported text operators are covered; font CMaps and object streams are not recovered",
            "column detection is a page-local two-column heuristic with single-column fallback, not universal PDF layout support",
            "table rows/cells and section levels are structural candidates; merged cells, cross-page continuation, TOC truth, and scientific meaning are not inferred",
        ]
        run.finish({
            "status": status,
            "document": {"path": str(path), "bytes": path.stat().st_size, "page_count": len(pages)},
            "pages": pages_output,
            "blocks": blocks,
            "tables": [block for block in blocks if block["kind"] == "caption" and block["text"].lower().startswith("table")],
            "table_structure_candidates": table_candidates,
            "sections": sections,
            "formulas": [block for block in blocks if block["kind"] == "formula_candidate"],
            "sources": [{"path": str(path), "page": block["page"], "block_id": block["block_id"]} for block in blocks],
            "limitations": limitations,
        }, aliases={"parseddocument": "blocks", "documentblocks": "blocks", "result": "blocks"}, code=code,
            trace_extra={
                "pdf_pages_read": len(pages), "page_layouts": page_layouts,
                "table_structure_candidate_count": len(table_candidates),
                "section_candidate_count": len(sections),
                "capabilities": {"ocr": False, "image_table_recovery": False,
                                 "merged_cell_detection": False, "cross_page_table_joining": False},
                "fallback_limit": "; ".join(limitations),
            })
    except Exception as error:
        run.uncertainty.append(str(error))
        run.finish({"status": "partial", "pages": [], "blocks": [], "tables": [],
                    "table_structure_candidates": [], "sections": [], "formulas": [],
                    "sources": [{"path": str(path)}],
                    "limitations": ["OCR and unsupported PDF recovery were not performed"]},
                   code=2, trace_extra={"failure": str(error),
                                        "capabilities": {"ocr": False, "image_table_recovery": False}})


if __name__ == "__main__":
    sys.exit(main())
