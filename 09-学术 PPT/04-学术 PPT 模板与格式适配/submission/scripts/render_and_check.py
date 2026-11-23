#!/usr/bin/env python3
"""Hash-lock a PPTX, render or validate an external PDF, and audit every PDF page."""
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

SUCCESS = "automated-render-passed-visual-required"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def deep_merge(old, new):
    """Recursively merge contract objects without discarding unrelated deep fields."""
    merged = dict(old or {})
    for key, value in new.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def preview_paths(out, deck):
    paths = [out / f"{deck.stem}.pdf", out / "contact-sheet.png", out / f"{deck.stem}_contact_sheet.png"]
    paths.extend(out.glob("page-*.png"))
    paths.extend(out.glob(f"{deck.stem}_page_*.png"))
    return set(paths)


def remove_previews(out, deck, protected_pdf=None):
    protected = str(protected_pdf.resolve()) if protected_pdf else None
    for path in preview_paths(out, deck):
        if path.is_file() and str(path.resolve()) != protected:
            path.unlink()


def is_render_stage_limitation(value):
    text = value.lower()
    return any(token in text for token in ("actual final-pptx rendering", "final pptx actual render",
               "no successful current-pptx", "automated pdf/page", "automated rendering",
               "renderer or render checking", "final pptx to pdf conversion"))


def has_pending(value):
    if isinstance(value, dict):
        for key, item in value.items():
            marker = key.lower().replace("_", "-")
            if item and any(token in marker for token in ("pending", "unresolved")):
                return True
            if has_pending(item):
                return True
        return False
    if isinstance(value, list):
        return any(has_pending(item) for item in value)
    if isinstance(value, str):
        text = value.lower().replace("_", "-")
        return any(token in text for token in ("pending", "review-required", "review required", "visual-required",
                                               "unresolved", "awaiting review", "not-run"))
    return False


def update_existing_contract(out, deck, report, rendered, protected_pdf=None, visual_review=None):
    """Fill existing fields only; completion needs a bound, nonempty actual-image review."""
    result_path, record_path = out / "result.json", out / "adaptation_record.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if not result.get("case_id") or result.get("case_id") != record.get("case_id"):
        raise ValueError("result.case_id must equal the actual adaptation_record case_id")
    stale = {str(path.resolve()) for path in preview_paths(out, deck)}
    preview_names = {path.name for path in preview_paths(out, deck)}
    old_limitations = []
    for key in ("render", "validation"):
        if isinstance(result.get(key), dict):
            previous = result[key]
            old_limitations.extend(previous.get("limitations", []))
            for field in ("pdf", "contact_sheet"):
                if previous.get(field):
                    stale.add(str(Path(previous[field]).resolve()))
                    preview_names.add(Path(previous[field]).name)
            for page in previous.get("pages", []):
                if page.get("png"):
                    stale.add(str(Path(page["png"]).resolve()))
                    preview_names.add(Path(page["png"]).name)
    result["status"] = "partial"
    artifact_keys = {"pdf", "pdf_sha256", "pages", "contact_sheet", "contact_sheet_sha256",
                     "pptx_sha256_after", "pptx_pages", "pdf_pages", "visual_review_record"}
    contract_report = dict(report) if rendered else {key: value for key, value in report.items() if key not in artifact_keys}
    if "artifacts" in result:
        artifacts = result["artifacts"]
        if not isinstance(artifacts, list):
            raise TypeError("contract field artifacts changed type")
        artifacts = [item for item in artifacts if not (isinstance(item, str) and
                     (str(Path(item).resolve()) in stale or Path(item).name in preview_names))]
        additions = [out / "render_report.json"]
        if rendered:
            additions = [Path(report["pdf"])] + [Path(page["png"]) for page in report["pages"]]
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
            base = dict(result[key] or {})
            stage_owned = artifact_keys | {"renderer", "renderer_name", "renderer_version", "command", "exit_code",
                                           "execution_log", "external_receipt", "external_receipt_sha256", "render_mode",
                                           "PyMuPDF", "python", "stale_preview", "visual_review_record"}
            for field in stage_owned:
                base.pop(field, None)
            result[key] = deep_merge(base, contract_report)
    if "uncertainty" in result:
        if not isinstance(result["uncertainty"], list):
            raise TypeError("contract field uncertainty changed type")
        retained = [item for item in result["uncertainty"] if item not in old_limitations or not is_render_stage_limitation(item)]
        result["uncertainty"] = retained + [item for item in report.get("limitations", []) if item not in retained]
    if rendered and visual_review is not None:
        candidate = deep_merge(result, {"status": "completed"})
        non_render = {key: value for key, value in candidate.items() if key not in ("render", "validation", "status")}
        report_pending = has_pending({key: value for key, value in report.items() if key not in ("status", "visual_review")})
        if not has_pending(non_render) and not report_pending:
            result["status"] = "completed"
    record["render"] = contract_report
    dump(record_path, record)
    dump(result_path, result)


def external_provenance(receipt_path, pdf, deck_hash):
    receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
    if not isinstance(receipt, dict):
        raise ValueError("external renderer receipt must be a JSON object")
    provenance = receipt.get("provenance", {})
    if not isinstance(provenance, dict):
        raise ValueError("external provenance must be an object")
    renderer = receipt.get("renderer")
    if isinstance(renderer, dict):
        name, version = renderer.get("name"), renderer.get("version")
    else:
        name, version = renderer, receipt.get("renderer_version")
    if not isinstance(name, str) or not name.strip() or not isinstance(version, str) or not version.strip():
        raise ValueError("external receipt requires a real renderer name and obtainable version")
    invocation = receipt.get("command", receipt.get("actual_tool_invocation"))
    if isinstance(invocation, list):
        invocation_valid = len(invocation) >= 2 and all(isinstance(item, str) and item for item in invocation)
    elif isinstance(invocation, str):
        invocation_valid = bool(invocation.strip())
    elif isinstance(invocation, dict):
        invocation_valid = bool(invocation)
    else:
        invocation_valid = False
    if not invocation_valid:
        raise ValueError("external receipt requires the complete command or actual tool invocation")
    if isinstance(receipt.get("exit_code"), bool) or receipt.get("exit_code") != 0:
        raise ValueError("external renderer exit_code must be numeric zero")
    execution_log = receipt.get("execution_log", receipt.get("log", receipt.get("receipt")))
    if not execution_log or not isinstance(execution_log, (str, list, dict)):
        raise ValueError("external receipt requires a nonempty actual execution log/receipt")
    pptx_hash = receipt.get("pptx_sha256", provenance.get("pptx_sha256"))
    pdf_hash = receipt.get("pdf_sha256", provenance.get("pdf_sha256"))
    if not isinstance(pptx_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", pptx_hash) or pptx_hash != deck_hash:
        raise ValueError("external receipt PPTX provenance SHA-256 does not match the current PPTX")
    if not isinstance(pdf_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", pdf_hash) or pdf_hash != sha(pdf):
        raise ValueError("external receipt PDF provenance SHA-256 does not match the supplied PDF")
    return {"renderer": f"{name.strip()} {version.strip()}", "renderer_name": name.strip(),
            "renderer_version": version.strip(), "command": invocation, "exit_code": 0,
            "execution_log": execution_log, "external_receipt": str(Path(receipt_path).resolve()),
            "external_receipt_sha256": sha(receipt_path), "pdf_sha256": pdf_hash,
            "render_mode": "external-pdf"}


def rect_for_shape(shape, prs, page):
    return [shape.left / prs.slide_width * page.rect.width,
            shape.top / prs.slide_height * page.rect.height,
            (shape.left + shape.width) / prs.slide_width * page.rect.width,
            (shape.top + shape.height) / prs.slide_height * page.rect.height]


def intersects(box, slot):
    return min(box[2], slot[2]) > max(box[0], slot[0]) and min(box[3], slot[3]) > max(box[1], slot[1])


def outside(box, slot, tolerance=2):
    return (box[0] < slot[0] - tolerance or box[1] < slot[1] - tolerance or
            box[2] > slot[2] + tolerance or box[3] > slot[3] + tolerance)


def audit_visual_slots(prs, doc):
    """Check actual PDF content in picture/chart slots; this is not full visual certification."""
    import fitz
    issues, rows = [], []
    for index, (slide, page) in enumerate(zip(prs.slides, doc), 1):
        text_dict = page.get_text("dict")
        spans = [dict(span, bbox=list(span["bbox"])) for block in text_dict["blocks"]
                 for line in block.get("lines", []) for span in line.get("spans", [])]
        images = [dict(item, bbox=list(item["bbox"])) for item in page.get_image_info()]
        drawings = [item for item in page.get_drawings() if not item["rect"].is_empty]
        page_row = {"page": index, "pictures": [], "charts": []}
        for shape in slide.shapes:
            slot = rect_for_shape(shape, prs, page)
            if shape.shape_type == 13:
                matches = [item for item in images if intersects(item["bbox"], slot)]
                check = {"shape": shape.name, "slot_pdf_rect": slot, "matched_images": len(matches)}
                page_row["pictures"].append(check)
                if not matches:
                    issues.append(f"page {index}: picture {shape.name} has no actual PDF image in its slot")
                for item in matches:
                    if outside(item["bbox"], slot):
                        issues.append(f"page {index}: picture {shape.name} visibly overflows its PDF slot")
            if not shape.has_chart:
                continue
            chart = shape.chart
            tokens = []
            if chart.has_title:
                tokens.append(chart.chart_title.text_frame.text)
            try:
                tokens.extend(str(category.label) for category in chart.plots[0].categories)
                tokens.extend(series.name for series in chart.series)
            except (AttributeError, IndexError):
                pass
            tokens = [re.sub(r"\s+", "", token) for token in tokens if token and re.sub(r"\s+", "", token)]
            slot_spans = [span for span in spans if intersects(span["bbox"], slot)]
            slot_text = re.sub(r"\s+", "", " ".join(span["text"] for span in slot_spans))
            missing = [token for token in tokens if token not in slot_text]
            marks = [item for item in drawings if intersects(list(item["rect"]), slot)]
            marks += [item for item in images if intersects(item["bbox"], slot)]
            check = {"shape": shape.name, "slot_pdf_rect": slot, "expected_text_tokens": tokens,
                     "missing_slot_text": missing, "graphic_marks": len(marks)}
            page_row["charts"].append(check)
            if missing:
                issues.append(f"page {index}: chart {shape.name} text is absent from its PDF slot: {missing}")
            if not marks:
                issues.append(f"page {index}: chart {shape.name} has no actual PDF vector/image marks in its slot")
            for span in slot_spans:
                if outside(span["bbox"], slot):
                    issues.append(f"page {index}: chart {shape.name} text visibly overflows its PDF slot")
            for drawing in marks:
                box = list(drawing.get("bbox", drawing["rect"] if "rect" in drawing else fitz.Rect()))
                center_inside = slot[0] <= (box[0] + box[2]) / 2 <= slot[2] and slot[1] <= (box[1] + box[3]) / 2 <= slot[3]
                if center_inside and outside(box, slot):
                    issues.append(f"page {index}: chart {shape.name} graphic visibly overflows its PDF slot")
        rows.append(page_row)
    return issues, rows


def validate_visual_review(path, report):
    from PIL import Image
    review = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(review, dict) or review.get("pptx_sha256") != report["pptx_sha256"] or review.get("pdf_sha256") != report["pdf_sha256"]:
        raise ValueError("visual review is not bound to the current PPTX/PDF hashes")
    pages = review.get("pages")
    if not isinstance(pages, list) or len(pages) != len(report["pages"]):
        raise ValueError("visual review must contain one nonempty record per rendered page")
    for expected, actual in zip(report["pages"], pages):
        notes = actual.get("notes", actual.get("review"))
        if actual.get("page") != expected["page"] or actual.get("png_sha256") != expected["png_sha256"] or not isinstance(notes, str) or not notes.strip():
            raise ValueError("visual review page hash binding or review notes are invalid")
        if actual.get("actual_image_read") is not True:
            raise ValueError("visual review must attest actual image reading for every page")
        image_path = Path(expected["png"])
        if sha(image_path) != expected["png_sha256"]:
            raise ValueError("reviewed PNG no longer matches the render")
        with Image.open(image_path) as image:
            image.load()
            if not image.width or not image.height:
                raise ValueError("reviewed PNG is not a readable nonempty image")
    return {"path": str(Path(path).resolve()), "sha256": sha(path), "page_reviews": len(pages)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pptx", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--soffice")
    ap.add_argument("--external-pdf", help="Already rendered PDF; requires --external-receipt and is never pre-deleted")
    ap.add_argument("--external-receipt", help="JSON renderer execution receipt with command, exit code, log and provenance hashes")
    ap.add_argument("--visual-review", help="Optional hash-bound per-page actual-image review JSON")
    args = ap.parse_args()
    if bool(args.external_pdf) != bool(args.external_receipt):
        ap.error("--external-pdf and --external-receipt must be supplied together")
    if args.external_pdf and args.soffice:
        ap.error("--external-pdf and --soffice are mutually exclusive")
    deck, out, work = Path(args.pptx).resolve(), Path(args.outdir).resolve(), Path(args.work).resolve()
    external_pdf = Path(args.external_pdf).resolve() if args.external_pdf else None
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    report = {"status": "not-passed", "pptx": str(deck), "visual_review": "required", "issues": [],
              "limitations": ["No successful current-PPTX rendering; page-by-page visual review remains pending"]}

    try:
        result = json.loads((out / "result.json").read_text(encoding="utf-8"))
        record = json.loads((out / "adaptation_record.json").read_text(encoding="utf-8"))
        if not result.get("case_id") or result.get("case_id") != record.get("case_id"):
            raise ValueError("result.case_id must equal the actual adaptation_record case_id")
        initial_hash = sha(deck)
        report["pptx_sha256"] = initial_hash
        if record.get("final_sha256") != initial_hash:
            report["stale_preview"] = True
            raise ValueError("current PPTX hash differs from adaptation_record; stale previews are forbidden")
        report["pptx_sha256"] = initial_hash
    except Exception as exc:
        report["issues"].append(f"hash/contract preflight failed: {exc}")
        remove_previews(out, deck, external_pdf)
        dump(out / "render_report.json", report)
        try:
            update_existing_contract(out, deck, report, False, external_pdf)
        except Exception as write_exc:
            print(f"render-contract-write-blocked: {write_exc}", file=sys.stderr)
        print(f"render-not-passed: {exc}", file=sys.stderr)
        return 2

    remove_previews(out, deck, external_pdf)
    try:
        import fitz
        from pptx import Presentation
        from PIL import Image, ImageDraw
    except ImportError as exc:
        report["issues"].append(f"missing optional render dependency: {exc.name}")
        dump(out / "render_report.json", report)
        update_existing_contract(out, deck, report, False, external_pdf)
        print("render-not-passed: install PyMuPDF, Pillow and python-pptx", file=sys.stderr)
        return 3

    pdf = external_pdf or out / f"{deck.stem}.pdf"
    return_code = 2
    try:
        if external_pdf:
            if not pdf.is_file() or pdf.stat().st_size < 500:
                raise ValueError("external PDF does not exist or is too small")
            report.update(external_provenance(args.external_receipt, pdf, initial_hash))
        else:
            office = args.soffice or shutil.which("soffice") or shutil.which("libreoffice")
            if args.soffice and not (Path(office).is_file() or shutil.which(office)):
                office = None
            if not office:
                report.update({"renderer": None, "python": platform.python_version(), "PyMuPDF": fitz.VersionBind})
                report["issues"].append("LibreOffice/soffice unavailable; no PDF or page PNG was produced")
                dump(out / "render_report.json", report)
                update_existing_contract(out, deck, report, False, external_pdf)
                print("render-not-passed: LibreOffice/soffice unavailable", file=sys.stderr)
                return 3
            version_proc = subprocess.run([office, "--version"], text=True, capture_output=True, timeout=20)
            version_proc.check_returncode()
            version = version_proc.stdout.strip()
            if not version:
                raise RuntimeError("renderer version probe returned no version")
            profile = (work / "lo-profile").as_uri()
            cmd = [office, "--headless", f"-env:UserInstallation={profile}", "--convert-to", "pdf", "--outdir", str(out), str(deck)]
            proc = subprocess.run(cmd, text=True, capture_output=True, timeout=120)
            report.update({"renderer": version, "command": cmd, "exit_code": proc.returncode,
                           "execution_log": {"stdout": proc.stdout, "stderr": proc.stderr},
                           "PyMuPDF": fitz.VersionBind, "python": platform.python_version(), "render_mode": "soffice"})
            (work / "renderer.stdout.txt").write_text(proc.stdout, encoding="utf-8")
            (work / "renderer.stderr.txt").write_text(proc.stderr, encoding="utf-8")
            if proc.returncode or not pdf.is_file() or pdf.stat().st_size < 500:
                raise RuntimeError("final PPTX to PDF conversion failed")
        if external_pdf:
            report.update({"PyMuPDF": fitz.VersionBind, "python": platform.python_version()})
        if sha(deck) != initial_hash:
            raise RuntimeError("PPTX hash changed during rendering; previews are stale")
        prs, doc = Presentation(str(deck)), fitz.open(str(pdf))
        report["pptx_pages"], report["pdf_pages"] = len(prs.slides), len(doc)
        report["pdf"], report["pdf_sha256"] = str(pdf), sha(pdf)
        if len(prs.slides) != len(doc):
            raise RuntimeError(f"page count {len(prs.slides)} != {len(doc)}")
        report["pages"], thumbs = [], []
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
            with Image.open(png) as source_image:
                im = source_image.convert("RGB")
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
        visual_issues, visual_rows = audit_visual_slots(prs, doc)
        report["issues"].extend(visual_issues)
        for row, checks in zip(report["pages"], visual_rows):
            row["visual_slot_checks"] = checks
        cols, rows = 2, max(1, math.ceil(len(thumbs) / 2))
        contact = Image.new("RGB", (cols*500, rows*300), "#dddddd")
        for number, image in enumerate(thumbs):
            contact.paste(image, ((number % cols)*500, (number // cols)*300))
        contact_path = out / "contact-sheet.png"
        contact.save(contact_path)
        report["contact_sheet"], report["contact_sheet_sha256"] = str(contact_path), sha(contact_path)
        if sha(deck) != initial_hash or sha(pdf) != report["pdf_sha256"]:
            raise RuntimeError("PPTX or PDF hash changed before report finalization; previews are stale")
        report["pptx_sha256_after"] = sha(deck)
        report["status"] = SUCCESS if not report["issues"] else "render-check-failed"
        report["limitations"] = ["Automated PDF/page checks do not certify visual quality, scientific evidence, or PowerPoint/WPS compatibility; actual contact-sheet review remains pending"]
        review_record = None
        if args.visual_review and report["status"] == SUCCESS:
            review_record = validate_visual_review(args.visual_review, report)
            report["visual_review"] = "completed-with-bound-actual-image-review"
            report["visual_review_record"] = review_record
            report["limitations"] = ["Automated PDF/page checks and recorded image review do not certify scientific evidence or PowerPoint/WPS compatibility"]
        return_code = 0 if not report["issues"] else 2
    except Exception as exc:
        report["status"] = "not-passed"
        report["issues"].append(f"{type(exc).__name__}: {exc}")
        report["limitations"] = ["No successful current-PPTX rendering/checking; page-by-page visual review remains pending"]
        review_record = None
    if report["status"] != SUCCESS:
        remove_previews(out, deck, external_pdf)
        report.pop("visual_review_record", None)
    dump(out / "render_report.json", report)
    update_existing_contract(out, deck, report, report["status"] == SUCCESS, external_pdf, review_record)
    print(json.dumps({"status": report["status"], "issues": report["issues"], "renderer": report.get("renderer")}, ensure_ascii=False))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
