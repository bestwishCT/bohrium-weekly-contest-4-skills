#!/usr/bin/env python3
"""Hash-lock and really render the final PPTX, then update the existing output contract."""
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


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def preview_paths(out, deck):
    paths = [out / f"{deck.stem}.pdf", out / "contact-sheet.png", out / f"{deck.stem}_contact_sheet.png"]
    paths.extend(out.glob("page-*.png"))
    paths.extend(out.glob(f"{deck.stem}_page_*.png"))
    return set(paths)


def remove_previews(out, deck):
    for path in preview_paths(out, deck):
        if path.is_file():
            path.unlink()


def update_existing_contract(out, deck, report, rendered):
    """Fill only fields already present in result.json; never complete visual/scientific review."""
    result_path = out / "result.json"
    record_path = out / "talk_record.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    record = json.loads(record_path.read_text(encoding="utf-8"))
    stale = {str(path.resolve()) for path in preview_paths(out, deck)}
    preview_names = {path.name for path in preview_paths(out, deck)}

    def assign(key, value):
        if key not in result:
            return
        if result[key] is not None and type(value) is not type(result[key]):
            raise TypeError(f"contract field {key} changed type")
        result[key] = value

    assign("status", "partial")
    if "artifacts" in result:
        artifacts = result["artifacts"]
        if not isinstance(artifacts, list):
            raise TypeError("contract field artifacts changed type")
        artifacts = [item for item in artifacts if not (isinstance(item, str) and (str(Path(item).resolve()) in stale or Path(item).name in preview_names))]
        additions = [out / "render_report.json"]
        if rendered:
            additions = [out / report["pdf"]] if not Path(report["pdf"]).is_absolute() else [Path(report["pdf"])]
            additions += [Path(page["png"]) for page in report["pages"]]
            additions += [Path(report["contact_sheet"]), out / "render_report.json"]
        for path in additions:
            value = str(path.resolve())
            if path.is_file() and value not in artifacts:
                artifacts.append(value)
        result["artifacts"] = artifacts
    for key in ("render", "validation"):
        if key in result:
            if result[key] is not None and not isinstance(result[key], dict):
                raise TypeError(f"contract field {key} changed type")
            merged = dict(result[key] or {})
            merged.update(report)
            result[key] = merged
    if "uncertainty" in result:
        if not isinstance(result["uncertainty"], list):
            raise TypeError("contract field uncertainty changed type")
        for limitation in report.get("limitations", []):
            if limitation not in result["uncertainty"]:
                result["uncertainty"].append(limitation)
    record["render"] = report
    dump(record_path, record)
    dump(result_path, result)


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
    report = {"status": "not-passed", "pptx": str(deck), "visual_review": "required", "issues": [],
              "limitations": ["No successful current-PPTX rendering; page-by-page visual review remains pending"]}

    try:
        result = json.loads((out / "result.json").read_text(encoding="utf-8"))
        record = json.loads((out / "talk_record.json").read_text(encoding="utf-8"))
        if result.get("case_id") != record.get("case_id"):
            raise ValueError("result/talk_record case_id mismatch; refusing contract writeback")
        initial_hash = sha(deck)
        if record.get("final_sha256") != initial_hash:
            raise ValueError("current PPTX hash differs from talk_record; stale previews are forbidden")
        source = Path(record["source_material"])
        if not source.is_file() or sha(source) != record.get("source_sha256"):
            raise ValueError("source PDF differs from the composition lock")
        report["pptx_sha256"] = initial_hash
    except Exception as exc:
        report["issues"].append(f"hash/contract preflight failed: {exc}")
        remove_previews(out, deck)
        dump(out / "render_report.json", report)
        if (out / "result.json").is_file() and (out / "talk_record.json").is_file():
            update_existing_contract(out, deck, report, False)
        print(f"render-not-passed: {exc}", file=sys.stderr)
        return 2

    remove_previews(out, deck)
    try:
        import fitz
        from pptx import Presentation
        from PIL import Image, ImageDraw
    except ImportError as exc:
        report["issues"].append(f"missing optional render dependency: {exc.name}")
        dump(out / "render_report.json", report)
        update_existing_contract(out, deck, report, False)
        print("render-not-passed: install PyMuPDF, Pillow and python-pptx", file=sys.stderr)
        return 3

    office = args.soffice or shutil.which("soffice") or shutil.which("libreoffice")
    if args.soffice and not (Path(office).is_file() or shutil.which(office)):
        office = None
    if not office:
        report.update({"renderer": None, "python": platform.python_version(), "PyMuPDF": fitz.VersionBind})
        report["issues"].append("LibreOffice/soffice unavailable; no PDF or page PNG was produced")
        dump(out / "render_report.json", report)
        update_existing_contract(out, deck, report, False)
        print("render-not-passed: LibreOffice/soffice unavailable", file=sys.stderr)
        return 3

    return_code = 2
    try:
        version_proc = subprocess.run([office, "--version"], text=True, capture_output=True, timeout=20)
        version = version_proc.stdout.strip()
        report.update({"renderer": version, "PyMuPDF": fitz.VersionBind, "python": platform.python_version()})
        profile = (work / "lo-profile").as_uri()
        cmd = [office, "--headless", f"-env:UserInstallation={profile}", "--convert-to", "pdf", "--outdir", str(out), str(deck)]
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=120)
        report["command"], report["exit_code"] = cmd, proc.returncode
        (work / "renderer.stdout.txt").write_text(proc.stdout, encoding="utf-8")
        (work / "renderer.stderr.txt").write_text(proc.stderr, encoding="utf-8")
        pdf = out / f"{deck.stem}.pdf"
        if proc.returncode or not pdf.is_file() or pdf.stat().st_size < 500:
            raise RuntimeError("final PPTX to PDF conversion failed")
        if sha(deck) != initial_hash:
            raise RuntimeError("PPTX hash changed during rendering; discarding previews")
        prs, doc = Presentation(str(deck)), fitz.open(str(pdf))
        report["pptx_pages"], report["pdf_pages"] = len(prs.slides), len(doc)
        report["pdf"], report["pdf_sha256"] = str(pdf), sha(pdf)
        if len(prs.slides) != len(doc):
            raise RuntimeError(f"page count {len(prs.slides)} != {len(doc)}")
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
            spans = [span for block in page.get_text("dict")["blocks"] for line in block.get("lines", []) for span in line["spans"]]
            for span in spans:
                x0, y0, x1, y1 = span["bbox"]
                if x0 < -1 or y0 < -1 or x1 > width + 1 or y1 > height + 1:
                    report["issues"].append(f"page {idx+1}: rendered text exceeds media box")
            png = out / f"page-{idx+1:02d}.png"
            page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(str(png))
            im = Image.open(png).convert("RGB")
            if all(lo == hi for lo, hi in im.getextrema()):
                report["issues"].append(f"page {idx+1}: rendered page is blank/single color")
            im.thumbnail((480, 270))
            canvas = Image.new("RGB", (500, 300), "white")
            canvas.paste(im, ((500-im.width)//2, 15))
            ImageDraw.Draw(canvas).text((10, 282), f"Final PPTX / PDF page {idx+1}", fill="black")
            thumbs.append(canvas)
            report["pages"].append({"page": idx+1, "png": str(png), "png_sha256": sha(png),
                                    "fonts": sorted({f[3] for f in page.get_fonts()}),
                                    "text_bbox_count": len(spans), "size": im.size})
        cols, rows = 2, max(1, math.ceil(len(thumbs) / 2))
        contact = Image.new("RGB", (cols*500, rows*300), "#dddddd")
        for n, im in enumerate(thumbs):
            contact.paste(im, ((n % cols)*500, (n // cols)*300))
        contact_path = out / "contact-sheet.png"
        contact.save(contact_path)
        report["contact_sheet"] = str(contact_path)
        if sha(deck) != initial_hash:
            raise RuntimeError("PPTX hash changed before report finalization; discarding previews")
        report["pptx_sha256_after"] = sha(deck)
        report["status"] = "automated-render-passed-visual-required" if not report["issues"] else "render-check-failed"
        report["limitations"] = ["Automated rendering does not certify visual quality, scientific evidence, or PowerPoint/WPS compatibility; actual contact-sheet review remains pending"]
        return_code = 0 if not report["issues"] else 2
    except Exception as exc:
        report["status"] = "not-passed"
        report["issues"].append(f"{type(exc).__name__}: {exc}")
        report["limitations"] = ["No successful current-PPTX rendering; page-by-page visual review remains pending"]
    if report["status"] != "automated-render-passed-visual-required":
        remove_previews(out, deck)
    dump(out / "render_report.json", report)
    update_existing_contract(out, deck, report, report["status"] == "automated-render-passed-visual-required")
    print(json.dumps({"status": report["status"], "issues": report["issues"], "renderer": report.get("renderer")}, ensure_ascii=False))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
