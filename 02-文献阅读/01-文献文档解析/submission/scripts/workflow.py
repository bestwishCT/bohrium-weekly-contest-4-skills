#!/usr/bin/env python3
"""Auditable, limited PDF text-layer parser; never invents OCR or semantics."""
import argparse
import json
import re
import sys
import zlib
from pathlib import Path

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


def line_blocks(page):
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
        segments = []
        for chunk in chunks:
            if not segments:
                segments.append([chunk])
                continue
            previous = segments[-1][-1]
            previous_end = previous["x"] + len(previous["text"]) * 5
            if chunk["x"] - previous_end > 40:
                segments.append([chunk])
            else:
                segments[-1].append(chunk)
        for segment in segments:
            text = " ".join(item["text"].strip() for item in segment if item["text"].strip()).strip()
            if not text:
                continue
            x0 = min(item["x"] for item in segment)
            x1 = max(item["x"] + len(item["text"]) * 5 for item in segment)
            column = "left" if x1 < 306 else "right" if x0 >= 306 else "wide"
            lines.append({"text": text, "x": x0, "y": group["y"], "column": column})
    top = [line for line in lines if line["column"] == "wide" and line["y"] >= max([item["y"] for item in lines] or [0]) - 2]
    bottom = [line for line in lines if line["column"] == "wide" and line not in top]
    ordered = (sorted(top, key=lambda item: -item["y"])
               + sorted([line for line in lines if line["column"] == "left"], key=lambda item: -item["y"])
               + sorted([line for line in lines if line["column"] == "right"], key=lambda item: -item["y"])
               + sorted(bottom, key=lambda item: -item["y"]))
    return ordered


def classify(text):
    if re.match(r"^(?:\d+(?:\.\d+)*\s+|abstract\b|references\b)", text, re.I):
        return "heading_or_section"
    if re.match(r"^(?:table|figure|fig\.)\s*\d+", text, re.I):
        return "caption"
    if re.search(r"\(\d+\)\s*$", text) and re.search(r"[=+−-]", text):
        return "formula_candidate"
    return "paragraph"


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
        for page in pages:
            for sequence, line in enumerate(line_blocks(page), 1):
                kind = classify(line["text"])
                blocks.append({
                    "block_id": stable_id("b", run.case_id, page["page"], sequence, line["text"]),
                    "page": page["page"], "sequence": sequence, "kind": kind,
                    "text": line["text"], "reading_column": line["column"],
                    "bbox_approx": [line["x"], line["y"]],
                    "extraction": "uncompressed_pdf_text_operator",
                    "correction": None,
                    "structure_note": "caption/formula candidate only; scientific meaning not inferred" if kind != "paragraph" else None,
                })
        status = "partial" if run.uncertainty else "success"
        code = 2 if status == "partial" else 0
        run.changes.append("reconstructed page-local column order without cross-page semantic joining")
        run.finish({
            "status": status,
            "document": {"path": str(path), "bytes": path.stat().st_size, "page_count": len(pages)},
            "pages": [{"page": page["page"], "text_chunks": len(page["chunks"])} for page in pages],
            "blocks": blocks,
            "tables": [block for block in blocks if block["kind"] == "caption" and block["text"].lower().startswith("table")],
            "formulas": [block for block in blocks if block["kind"] == "formula_candidate"],
            "sources": [{"path": str(path), "page": block["page"], "block_id": block["block_id"]} for block in blocks],
        }, aliases={"parseddocument": "blocks", "documentblocks": "blocks", "result": "blocks"}, code=code,
            trace_extra={"pdf_pages_read": len(pages), "fallback_limit": "no OCR, object streams, font CMaps, or image-table recovery"})
    except Exception as error:
        run.uncertainty.append(str(error))
        run.finish({"status": "partial", "pages": [], "blocks": [], "sources": [{"path": str(path)}]},
                   code=2, trace_extra={"failure": str(error)})


if __name__ == "__main__":
    sys.exit(main())
