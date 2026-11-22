#!/usr/bin/env python3
"""Render final PPTX with LibreOffice, rasterize all PDF pages, and audit geometry."""
import argparse
import hashlib
import json
import math
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sync_contract(deck, out, report, expected_hash):
    """Update only existing contract fields; preserve case_id and unrelated deep data."""
    result_path = out / "result.json"
    if not result_path.is_file():
        return
    result = json.loads(result_path.read_text(encoding="utf-8"))
    actual_hash = report.get("pptx_sha256")
    bound = bool(expected_hash) and expected_hash == actual_hash and report.get("pptx_sha256_after") == actual_hash
    passed = report.get("status") == "automated-render-passed-visual-required" and bound
    values = {"status": "partial", "render": report, "validation": report}
    limitations = report.get("limitations", [])
    if "uncertainty" in result and isinstance(result["uncertainty"], list):
        old = [item for item in result["uncertainty"] if item != "final PPTX actual render and per-page visual/font/crop inspection pending"]
        values["uncertainty"] = old + [item for item in limitations if item not in old]
    if "artifacts" in result and isinstance(result["artifacts"], list):
        retained = [item for item in result["artifacts"] if not (isinstance(item, str) and Path(item).parent == out
                    and Path(item).suffix.lower() in (".pdf", ".png"))]
        fresh = []
        if report.get("pdf") and Path(report["pdf"]).is_file():
            fresh.append(report["pdf"])
        fresh.extend(row["png"] for row in report.get("pages", []) if Path(row["png"]).is_file())
        if report.get("contact_sheet") and Path(report["contact_sheet"]).is_file():
            fresh.append(report["contact_sheet"])
        values["artifacts"] = retained + [item for item in fresh if item not in retained]
    for key, value in values.items():
        if key in result:
            if result[key] is not None and not isinstance(value, type(result[key])):
                raise TypeError(f"contract field {key} changed type")
            result[key] = value
    dump(result_path, result)
    record_path = out / "adaptation_record.json"
    if record_path.is_file():
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["render"] = report
        dump(record_path, record)


def remove_derived_previews(out, deck):
    for path in [out / (deck.stem + ".pdf"), out / "contact-sheet.png", *out.glob("page-*.png")]:
        if path.is_file():
            path.unlink()


def run(args):
    deck, out, work = Path(args.pptx).resolve(), Path(args.outdir).resolve(), Path(args.work).resolve()
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    if not deck.is_file():
        raise SystemExit("render-not-passed: final PPTX does not exist")
    before_hash = sha(deck)
    report = {"status": "not-passed", "pptx": str(deck), "pptx_sha256": before_hash,
              "visual_review": "required", "issues": []}
    record_path = out / "adaptation_record.json"
    expected_hash = None
    if record_path.is_file():
        expected_hash = json.loads(record_path.read_text(encoding="utf-8")).get("final_sha256")
    else:
        report["issues"].append("adaptation_record.json is missing; build-to-render hash binding unavailable")
    if expected_hash != before_hash:
        report["issues"].append("current PPTX hash differs from adaptation record; existing previews are stale")
        report["stale_preview"] = True
        report["limitations"] = ["No render was attached to a different PPTX build; regenerate adaptation before rendering."]
        dump(out / "render_report.json", report)
        sync_contract(deck, out, report, expected_hash)
        print("render-not-passed: stale final PPTX", file=sys.stderr)
        return 2
    remove_derived_previews(out, deck)
    try:
        import fitz
        from pptx import Presentation
        from PIL import Image, ImageDraw
    except ImportError as exc:
        report["issues"].append(f"missing optional render dependency: {exc.name}")
        report["pptx_sha256_after"] = sha(deck)
        dump(out / "render_report.json", report)
        sync_contract(deck, out, report, expected_hash)
        print("render-not-passed: install PyMuPDF, Pillow and python-pptx", file=sys.stderr)
        return 3
    office = args.soffice or shutil.which("soffice") or shutil.which("libreoffice")
    if office and not Path(office).is_file() and not shutil.which(office):
        office = None
    if not office:
        report.update({"renderer": None, "limitations": ["LibreOffice/soffice unavailable; no PDF or page PNG was produced"],
                       "python": platform.python_version(), "PyMuPDF": fitz.VersionBind,
                       "pptx_sha256_after": sha(deck)})
        dump(out / "render_report.json", report)
        sync_contract(deck, out, report, expected_hash)
        print("render-not-passed: LibreOffice/soffice unavailable", file=sys.stderr)
        return 3
    try:
        version_proc = subprocess.run([office, "--version"], text=True, capture_output=True, timeout=20)
        version_proc.check_returncode()
        version = version_proc.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        report.update({"renderer": None, "limitations": [f"renderer probe failed: {exc}; no PDF or page PNG was produced"],
                       "pptx_sha256_after": sha(deck)})
        dump(out / "render_report.json", report)
        sync_contract(deck, out, report, expected_hash)
        print("render-not-passed: renderer probe failed", file=sys.stderr)
        return 3
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
        report["pptx_sha256_after"] = sha(deck)
        report["limitations"] = ["final PPTX to PDF conversion failed; no successful render claimed"]
        dump(out / "render_report.json", report)
        sync_contract(deck, out, report, expected_hash)
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
    report["pptx_sha256_after"] = sha(deck)
    if report["pptx_sha256_after"] != before_hash:
        report["stale_preview"] = True
        report["issues"].append("PPTX hash changed during rendering; previews are stale")
    report["status"] = "automated-render-passed-visual-required" if not report["issues"] else "render-check-failed"
    report["limitations"] = ["Automated rendering does not certify scientific content, visual quality, PowerPoint/WPS compatibility, or renderer behavior beyond the recorded command."]
    dump(out / "render_report.json", report)
    sync_contract(deck, out, report, expected_hash)
    print(json.dumps({"status": report["status"], "pages": len(doc), "issues": report["issues"]}, ensure_ascii=False))
    return 0 if not report["issues"] else 2


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pptx", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--soffice")
    args = ap.parse_args()
    try:
        return run(args)
    except Exception as exc:
        deck, out = Path(args.pptx).resolve(), Path(args.outdir).resolve()
        out.mkdir(parents=True, exist_ok=True)
        Path(args.work).resolve().mkdir(parents=True, exist_ok=True)
        before_hash = sha(deck) if deck.is_file() else None
        record_path = out / "adaptation_record.json"
        expected_hash = json.loads(record_path.read_text(encoding="utf-8")).get("final_sha256") if record_path.is_file() else None
        report = {"status": "not-passed", "pptx": str(deck), "pptx_sha256": before_hash,
                  "pptx_sha256_after": before_hash, "visual_review": "required",
                  "issues": [f"{type(exc).__name__}: {exc}"],
                  "limitations": ["Renderer or render checking failed; no successful render or visual pass claimed."]}
        dump(out / "render_report.json", report)
        sync_contract(deck, out, report, expected_hash)
        print(f"render-not-passed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
