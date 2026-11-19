#!/usr/bin/env python3
"""Render final PPTX with LibreOffice, rasterize all PDF pages, and audit geometry."""
import argparse
import json
import math
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pptx", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--soffice")
    args = ap.parse_args()
    deck, out, work = Path(args.pptx).resolve(), Path(args.outdir).resolve(), Path(args.work).resolve()
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    report = {"status": "not-passed", "pptx": str(deck), "visual_review": "required", "issues": []}
    try:
        import fitz
        from pptx import Presentation
        from PIL import Image, ImageOps, ImageDraw
    except ImportError as exc:
        report["issues"].append(f"missing optional render dependency: {exc.name}")
        (out / "render_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("render-not-passed: install PyMuPDF, Pillow and python-pptx", file=sys.stderr)
        return 3
    office = args.soffice or shutil.which("soffice") or shutil.which("libreoffice")
    if not office:
        report.update({"renderer": None, "limitations": ["LibreOffice/soffice unavailable; no PDF or page PNG was produced"],
                       "python": platform.python_version(), "PyMuPDF": fitz.VersionBind})
        (out / "render_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("render-not-passed: LibreOffice/soffice unavailable", file=sys.stderr)
        return 3
    version = subprocess.run([office, "--version"], text=True, capture_output=True).stdout.strip()
    report.update({"renderer": version, "PyMuPDF": fitz.VersionBind, "python": platform.python_version()})
    profile = (work / "lo-profile").as_uri()
    cmd = [office, "--headless", f"-env:UserInstallation={profile}", "--convert-to", "pdf", "--outdir", str(out), str(deck)]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=120)
    report["command"] = cmd
    report["exit_code"] = proc.returncode
    (work / "renderer.stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (work / "renderer.stderr.txt").write_text(proc.stderr, encoding="utf-8")
    pdf = out / (deck.stem + ".pdf")
    if proc.returncode or not pdf.is_file():
        report["issues"].append("final PPTX to PDF conversion failed")
        (out / "render_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("render-not-passed: PDF missing", file=sys.stderr)
        return 2
    prs, doc = Presentation(str(deck)), fitz.open(str(pdf))
    if len(prs.slides) != len(doc):
        report["issues"].append(f"page count {len(prs.slides)} != {len(doc)}")
    report["pptx_pages"], report["pdf_pages"] = len(prs.slides), len(doc)
    report["pdf"] = str(pdf)
    report["pages"] = []
    thumbs = []
    for idx, page in enumerate(doc):
        width, height = page.rect.width, page.rect.height
        expected = prs.slide_width / prs.slide_height
        if abs(width / height - expected) > 0.01:
            report["issues"].append(f"page {idx+1}: aspect ratio mismatch")
        text = re.sub(r"\s+", "", page.get_text())
        for shape in prs.slides[idx].shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    token = re.sub(r"\s+", "", para.text)
                    if len(token) > 3 and token not in text:
                        report["issues"].append(f"page {idx+1}: text absent or clipped in rendered PDF: {para.text[:60]}")
            if shape.shape_type == 13 and any(abs(getattr(shape, f"crop_{side}", 0)) > 0.001 for side in ("left", "right", "top", "bottom")):
                report["issues"].append(f"page {idx+1}: cropped source picture needs case authorization and visual review")
        spans = [b for b in page.get_text("dict")["blocks"] if "lines" in b for line in b["lines"] for b in line["spans"]]
        for span in spans:
            x0, y0, x1, y1 = span["bbox"]
            if x0 < -1 or y0 < -1 or x1 > width + 1 or y1 > height + 1:
                report["issues"].append(f"page {idx+1}: rendered text exceeds media box")
        png = out / f"page-{idx+1:02d}.png"
        page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(str(png))
        im = Image.open(png).convert("RGB")
        im.thumbnail((480, 270))
        canvas = Image.new("RGB", (500, 300), "white")
        canvas.paste(im, ((500-im.width)//2, 15))
        ImageDraw.Draw(canvas).text((10, 282), f"Final PPTX / PDF page {idx+1}", fill="black")
        thumbs.append(canvas)
        report["pages"].append({"page": idx+1, "png": str(png), "fonts": sorted({f[3] for f in page.get_fonts()}),
                                "text_bbox_count": len(spans), "size": im.size})
    cols, rows = 2, max(1, math.ceil(len(thumbs) / 2))
    contact = Image.new("RGB", (cols*500, rows*300), "#dddddd")
    for n, im in enumerate(thumbs):
        contact.paste(im, ((n % cols)*500, (n // cols)*300))
    contact_path = out / "contact-sheet.png"
    contact.save(contact_path)
    report["contact_sheet"] = str(contact_path)
    report["status"] = "automated-render-passed-visual-required" if not report["issues"] else "render-check-failed"
    (out / "render_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "pages": len(doc), "issues": report["issues"]}, ensure_ascii=False))
    return 0 if not report["issues"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
