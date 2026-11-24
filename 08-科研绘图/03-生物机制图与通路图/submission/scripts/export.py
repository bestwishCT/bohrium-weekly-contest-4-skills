#!/usr/bin/env python3
"""Renderer-detecting SVG export to verified PNG/PDF with real provenance; no fabricated renders."""
import argparse
import hashlib
import json
import re
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from xml.etree import ElementTree as ET

TIMEOUT = 180


def run_command(name, args, cwd=None):
    try:
        cp = subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=TIMEOUT)
        return {"name": name, "command": args, "returncode": cp.returncode,
                "stdout": cp.stdout[-4000:], "stderr": cp.stderr[-4000:], "timed_out": False}
    except subprocess.TimeoutExpired as exc:
        return {"name": name, "command": args, "returncode": None, "stdout": "",
                "stderr": str(exc), "timed_out": True}


def version_of(path, args):
    result = run_command("version", [path] + args)
    text = (result["stdout"] or result["stderr"] or "").strip().splitlines()
    return text[0] if text else "version-not-reported"


def discover(names):
    found = []
    for name in names:
        path = shutil.which(name)
        if path:
            try:
                version = version_of(path, ["--version"])
            except Exception:
                version = "version-not-reported"
            found.append({"name": name, "path": path, "version": version[:200]})
    return found


def svg_size(path):
    root = ET.parse(str(path)).getroot()
    def number(value):
        if value is None:
            return None
        text = str(value).strip()
        for suffix, factor in (("px", 1.0), ("pt", 1.0), ("", 1.0)):
            if text.endswith(suffix):
                try:
                    return float(text[: len(text) - len(suffix)] if suffix else text)
                except ValueError:
                    return None
        return None
    width, height = number(root.attrib.get("width")), number(root.attrib.get("height"))
    if width is None or height is None:
        view = root.attrib.get("viewBox", "").replace(",", " ").split()
        if len(view) == 4:
            try:
                width = float(view[2])
                height = float(view[3])
            except ValueError:
                pass
    if width is None or height is None or width <= 0 or height <= 0:
        raise ValueError("cannot determine finite SVG width/height")
    return width, height


def png_info(path):
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos, width, height, depth, color, interlace = 8, None, None, None, None, None
    idat = b""
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        pos += length + 12
        if tag == b"IHDR":
            width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", payload)
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"IEND":
            break
    return {"width": width, "height": height, "bit_depth": depth,
            "color_type": color, "interlace": interlace, "idat": idat}


def png_pixels(path):
    info = png_info(path)
    if not info or info["bit_depth"] != 8 or info["color_type"] not in (2, 6) or info["interlace"] != 0:
        raise ValueError("unsupported PNG encoding for pixel audit")
    width, height, color = info["width"], info["height"], info["color_type"]
    bpp = 3 if color == 2 else 4
    raw = zlib.decompress(info["idat"])
    stride = width * bpp
    if len(raw) != (stride + 1) * height:
        raise ValueError("PNG data length mismatch")
    rows = bytearray()
    previous = bytearray(stride)
    off = 0
    for _ in range(height):
        mode = raw[off]
        off += 1
        row = bytearray(raw[off:off + stride])
        off += stride
        for i in range(stride):
            left = row[i - bpp] if i >= bpp else 0
            up = previous[i]
            upper_left = previous[i - bpp] if i >= bpp else 0
            if mode == 1:
                row[i] = (row[i] + left) & 255
            elif mode == 2:
                row[i] = (row[i] + up) & 255
            elif mode == 3:
                row[i] = (row[i] + (left + up) // 2) & 255
            elif mode == 4:
                guess = left + up - upper_left
                pa, pb, pc = abs(guess - left), abs(guess - up), abs(guess - upper_left)
                pick = left if pa <= pb and pa <= pc else up if pb <= pc else upper_left
                row[i] = (row[i] + pick) & 255
            elif mode != 0:
                raise ValueError("unknown PNG filter mode")
        rows.extend(row)
        previous = row
    return width, height, bpp, rows


def pdf_from_png(png_path, pdf_path, width, height):
    """Wrap the real rendered pixels into a valid single-page PDF; method stays labeled raster."""
    pw, ph, bpp, rows = png_pixels(png_path)
    rgb = bytearray()
    for i in range(0, len(rows), bpp):
        if bpp == 3:
            rgb.extend(rows[i:i + 3])
        else:
            alpha = rows[i + 3]
            rgb.extend(bytes(int(c * alpha / 255 + 255 * (255 - alpha) / 255) for c in rows[i:i + 3]))
    stream = zlib.compress(bytes(rgb), 9)
    content = "q %d 0 0 %d 0 0 cm /Im0 Do Q" % (int(width), int(height))
    bodies = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>"
         % (int(width), int(height))).encode(),
        b"",
        ("<< /Length %d >>\nstream\n" % len(content)).encode() + content.encode() + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(bodies, 1):
        offsets.append(len(out))
        if number == 4:
            out.extend(b"4 0 obj << /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\nstream\n" % (pw, ph, len(stream)))
            out.extend(stream)
            out.extend(b"\nendstream\nendobj\n")
        else:
            out.extend(("%d 0 obj " % number).encode() + body + b"\nendobj\n")
    xref_at = len(out)
    out.extend(("xref\n0 %d\n" % (len(bodies) + 1)).encode())
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        out.extend(("%010d 00000 n \n" % offset).encode())
    out.extend(("trailer << /Root 1 0 R /Size %d >>\nstartxref\n%d\n%%%%EOF\n" % (len(bodies) + 1, xref_at)).encode())
    Path(pdf_path).write_bytes(bytes(out))
    return {"method": "png_wrapped_raster", "embedded_png_pixels": [pw, ph],
            "note": "single-page PDF embeds the renderer's true raster pixels; not vector"}


def pdf_info(path):
    data = Path(path).read_bytes()
    if not data.startswith(b"%PDF") or b"%%EOF" not in data[-2048:] or len(data) < 200:
        raise ValueError("invalid or truncated PDF")
    pages = len([m for m in re.finditer(rb"/Type\s*/Page\b", data)
                 if not re.search(rb"/Type\s*/Pages\b", data[m.start():m.start() + 40])])
    media = re.search(rb"/MediaBox\s*\[\s*([-0-9.]+)\s+([-0-9.]+)\s+([-0-9.]+)\s+([-0-9.]+)\s*\]", data)
    box = [float(v) for v in media.groups()] if media else None
    return {"page_count": pages, "media_box_points": box, "bytes": len(data)}


def first_success(candidates, verify):
    attempts = []
    for render in candidates:
        if not render["available"]:
            continue
        result = run_command(render["name"], render["command"])
        attempts.append({"renderer": render["name"], "command": result["command"],
                         "returncode": result["returncode"], "timed_out": result["timed_out"],
                         "stdout_tail": result["stdout"][-600:], "stderr_tail": result["stderr"][-600:]})
        if result["returncode"] == 0 and not result["timed_out"]:
            check = verify()
            if check.get("ok"):
                return render, attempts, check
    return None, attempts, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="source SVG path")
    parser.add_argument("--png", help="output PNG path")
    parser.add_argument("--pdf", help="output PDF path")
    parser.add_argument("--pdf-preview", help="output PDF preview PNG path")
    parser.add_argument("--report", required=True, help="output JSON report path")
    parser.add_argument("--width", type=float, help="expected canvas width")
    parser.add_argument("--height", type=float, help="expected canvas height")
    parser.add_argument("--scale", type=float, default=1.0)
    args = parser.parse_args()

    svg = Path(args.input)
    if not svg.is_file():
        print(json.dumps({"status": "input_error", "error": "missing SVG: " + args.input}))
        return 2
    try:
        svg_w, svg_h = svg_size(svg)
    except Exception as exc:
        print(json.dumps({"status": "input_error", "error": str(exc)}))
        return 2
    expected_w = args.width or svg_w
    expected_h = args.height or svg_h
    report = {
        "status": "failed",
        "source_file": svg.name,
        "source_svg_sha256": hashlib.sha256(svg.read_bytes()).hexdigest(),
        "svg_size": [svg_w, svg_h],
        "expected_canvas": [expected_w, expected_h],
        "renderers_discovered": discover(["rsvg-convert", "inkscape", "magick", "soffice", "sips", "pdftoppm", "gs"]),
        "renderer": None,
        "renderer_version": None,
        "font_resolution": "text rendered by the discovered renderer with its real font stack; pixels inspected only for signature/size",
        "png": None,
        "pdf": None,
        "pdf_preview": None,
        "commands": [],
    }
    png_path = Path(args.png) if args.png else None
    if png_path is None and not args.pdf and not args.pdf_preview:
        report["error"] = "no output requested"
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 2

    available = {item["name"]: item for item in report["renderers_discovered"]}

    def png_candidates():
        out = str(png_path)
        return [
            {"name": "rsvg-convert", "available": "rsvg-convert" in available,
             "command": ["rsvg-convert", "-w", str(int(round(expected_w))), "-h", str(int(round(expected_h))), "-b", "white", "-o", out, str(svg)]},
            {"name": "inkscape", "available": "inkscape" in available,
             "command": ["inkscape", str(svg), "--export-type=png", "--export-filename=" + out,
                         "--export-width=" + str(int(round(expected_w))), "--export-height=" + str(int(round(expected_h)))]},
            {"name": "magick", "available": "magick" in available,
             "command": ["magick", "-background", "white", str(svg), out]},
            {"name": "sips", "available": "sips" in available,
             "command": ["sips", "-s", "format", "png", str(svg), "--out", out]},
        ]

    def png_verify():
        try:
            info = png_info(png_path)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if not info:
            return {"ok": False, "error": "PNG signature missing"}
        ok = info["width"] == int(round(expected_w)) and info["height"] == int(round(expected_h))
        return {"ok": ok, "png_pixels": [info["width"], info["height"]],
                "error": None if ok else "rendered size %dx%d differs from expected %dx%d" % (
                    info["width"], info["height"], int(round(expected_w)), int(round(expected_h)))}

    png_result = None
    if png_path is not None or args.pdf:
        png_target = png_path or svg.with_suffix(".render.png")
        if png_path is None:
            png_path = png_target
            report["png_path_intermediate"] = str(png_target)
        chosen, attempts, check = first_success(png_candidates(), png_verify)
        report["commands"].extend(attempts)
        if not chosen:
            report["png"] = {"status": "not_executed_or_failed", "attempts": len(attempts)}
        else:
            report["renderer"] = chosen["name"]
            report["renderer_version"] = available[chosen["name"]]["version"]
            info = png_info(png_path)
            report["png"] = {"status": "rendered_and_verified", "path": str(png_path),
                             "png_pixels": [info["width"], info["height"]],
                             "sha256": hashlib.sha256(png_path.read_bytes()).hexdigest()}
            png_result = info

    pdf_path = Path(args.pdf) if args.pdf else None
    if pdf_path is not None:
        if report["png"] and report["png"]["status"] == "rendered_and_verified":
            vector = [
                {"name": "rsvg-convert", "available": "rsvg-convert" in available,
                 "command": ["rsvg-convert", "-f", "pdf", "-o", str(pdf_path), str(svg)]},
                {"name": "inkscape", "available": "inkscape" in available,
                 "command": ["inkscape", str(svg), "--export-type=pdf", "--export-filename=" + str(pdf_path)]},
                {"name": "magick", "available": "magick" in available,
                 "command": ["magick", "-background", "white", str(svg), str(pdf_path)]},
            ]

            def pdf_verify():
                try:
                    info = pdf_info(pdf_path)
                except Exception as exc:
                    return {"ok": False, "error": str(exc)}
                return {"ok": info["page_count"] == 1 and info["media_box_points"] is not None, "info": info}

            chosen, attempts, check = first_success(vector, pdf_verify)
            report["commands"].extend(attempts)
            if chosen:
                info = pdf_info(pdf_path)
                report["pdf"] = {"status": "rendered_and_verified", "method": "vector_renderer",
                                 "renderer": chosen["name"], "path": str(pdf_path),
                                 "pdf_page_count": info["page_count"], "pdf_media_box_points": info["media_box_points"],
                                 "sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest()}
            else:
                try:
                    wrap = pdf_from_png(png_path, pdf_path, expected_w, expected_h)
                    info = pdf_info(pdf_path)
                    report["commands"].append({"renderer": "stdlib-png-wrapped-pdf",
                                               "command": ["python3-stdlib:embed rendered PNG into single-page PDF"],
                                               "returncode": 0, "timed_out": False})
                    report["pdf"] = {"status": "rendered_and_verified", "method": wrap["method"],
                                     "embedded_png_pixels": wrap["embedded_png_pixels"], "note": wrap["note"],
                                     "path": str(pdf_path), "pdf_page_count": info["page_count"],
                                     "pdf_media_box_points": info["media_box_points"],
                                     "sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest()}
                except Exception as exc:
                    report["pdf"] = {"status": "failed", "error": str(exc)}
        else:
            report["pdf"] = {"status": "not_executed", "reason": "no verified PNG render available to embed or forward"}

    preview_path = Path(args.pdf_preview) if args.pdf_preview else None
    if preview_path is not None:
        if report.get("pdf", {}).get("status") == "rendered_and_verified":
            preview = [
                {"name": "pdftoppm", "available": "pdftoppm" in available,
                 "command": ["pdftoppm", "-png", "-singlefile", "-r", "72", str(pdf_path), str(preview_path.with_suffix(""))]},
                {"name": "gs", "available": "gs" in available,
                 "command": ["gs", "-sDEVICE=png16m", "-o", str(preview_path), "-r72", str(pdf_path)]},
                {"name": "sips", "available": "sips" in available,
                 "command": ["sips", "-s", "format", "png", str(pdf_path), "--out", str(preview_path)]},
            ]

            def preview_verify():
                try:
                    info = png_info(preview_path)
                except Exception as exc:
                    return {"ok": False, "error": str(exc)}
                return {"ok": bool(info and info["width"] and info["height"]), "info": info}

            chosen, attempts, check = first_success(preview, preview_verify)
            report["commands"].extend(attempts)
            if chosen:
                info = png_info(preview_path)
                report["pdf_preview"] = {"status": "rendered_and_verified", "renderer": chosen["name"],
                                         "path": str(preview_path), "pdf_preview_pixels": [info["width"], info["height"]],
                                         "sha256": hashlib.sha256(preview_path.read_bytes()).hexdigest()}
            else:
                report["pdf_preview"] = {"status": "not_executed", "reason": "no PDF rasterizer discovered"}
        else:
            report["pdf_preview"] = {"status": "not_executed", "reason": "PDF was not rendered and verified"}

    requests = {"png": png_path is not None, "pdf": pdf_path is not None, "pdf_preview": preview_path is not None}
    outcomes = {
        "png": bool(png_path is None or (report["png"] and report["png"]["status"] == "rendered_and_verified")),
        "pdf": bool(pdf_path is None or (report["pdf"] and report["pdf"]["status"] == "rendered_and_verified")),
        "pdf_preview": bool(preview_path is None or (report["pdf_preview"] and report["pdf_preview"]["status"] == "rendered_and_verified")),
    }
    ok = all(outcomes[key] for key in requests if requests[key])
    if png_path is not None and report["png"]:
        report["png_pixels"] = report["png"].get("png_pixels")
    if pdf_path is not None and report["pdf"]:
        report["pdf_page_count"] = report["pdf"].get("pdf_page_count")
        report["pdf_media_box_points"] = report["pdf"].get("pdf_media_box_points")
    if preview_path is not None and report["pdf_preview"]:
        report["pdf_preview_pixels"] = report["pdf_preview"].get("pdf_preview_pixels")
    report["status"] = "passed" if ok else "partial_or_failed"
    report["no_fabricated_renders"] = True
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "renderer": report["renderer"],
                      "pdf_method": (report.get("pdf") or {}).get("method")}, ensure_ascii=False))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
