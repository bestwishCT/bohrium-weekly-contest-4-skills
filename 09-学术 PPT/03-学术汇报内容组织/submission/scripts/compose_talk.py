#!/usr/bin/env python3
"""Compose an audience/timing/Q&A driven editable talk from verified candidates."""
import argparse
import hashlib
import json
import math
import platform
import shutil
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

try:
    import pptx
    from pptx import Presentation
    from pptx.chart.data import ChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Pt
except ImportError:
    raise SystemExit("dependency-blocked: install python-pptx>=1.0 from resources.yaml")


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{field} must be numeric")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


class PDFEvidence:
    """Read actual PDF pages and enforce exact citation/chart field bindings."""

    def __init__(self, path):
        if path.suffix.lower() != ".pdf":
            raise ValueError("source material must be the actual PDF")
        try:
            import fitz
        except ImportError:
            raise ValueError("dependency-blocked: PyMuPDF is required to read source PDF pages")
        self.path = path
        self.sha256 = sha(path)
        self.page_lines, self.page_ledger = [], []
        with fitz.open(str(path)) as document:
            for page in document:
                lines = [self.canonical(line) for line in page.get_text("text", sort=True).splitlines() if self.canonical(line)]
                self.page_lines.append(lines)
                has_nontext_content = bool(page.get_images(full=True) or page.get_drawings())
                self.page_ledger.append({
                    "pdf_page": page.number + 1,
                    "text_lines": len(lines),
                    "read_status": "text-extracted" if lines else ("ocr-pending" if has_nontext_content else "empty-page"),
                    "ocr_status": "not-run",
                    "ocr_pending": not lines and has_nontext_content,
                })
        if not self.page_lines:
            raise ValueError("source PDF has no pages")

    @staticmethod
    def canonical(value):
        return " ".join(value.split())

    def validate_source(self, source, context):
        page, quote = source.get("pdf_page"), source.get("quote")
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= len(self.page_lines):
            raise ValueError(f"{context}: PDF page is outside the actual document")
        if not isinstance(quote, str) or not self.canonical(quote):
            raise ValueError(f"{context}: exact source quote required")
        quote = self.canonical(quote)
        lines, matches = self.page_lines[page-1], 0
        for start in range(len(lines)):
            parts = []
            for end in range(start, len(lines)):
                parts.append(lines[end])
                candidate = " ".join(parts)
                if candidate == quote:
                    matches += 1
                    break
                if len(candidate) >= len(quote):
                    break
        if matches != 1:
            raise ValueError(f"{context}: exact quote must equal one complete extracted line or consecutive lines on PDF p.{page}")
        section = source.get("section")
        if section is not None and lines.count(self.canonical(section)) != 1:
            raise ValueError(f"{context}: section heading does not exactly match PDF p.{page}")

    def validate_chart(self, visual, citations, context):
        source = visual.get("source")
        if not isinstance(source, dict):
            raise ValueError(f"{context}: chart requires a PDF structured-record source")
        page, prefix = source.get("pdf_page"), source.get("record_prefix")
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= len(self.page_lines) or not isinstance(prefix, str) or not prefix:
            raise ValueError(f"{context}: invalid chart PDF page or record prefix")
        if not any(s.get("pdf_page") == page and s.get("figure") and prefix == s["figure"] + " data:" for s in citations):
            raise ValueError(f"{context}: chart source must match a cited figure on the same physical PDF page")
        records = []
        for line in self.page_lines[page-1]:
            if not line.startswith(prefix):
                continue
            fields = {}
            for part in line[len(prefix):].split(";"):
                key, separator, value = part.strip().partition("=")
                if not separator or key in fields:
                    raise ValueError(f"{context}: malformed or duplicate chart source field")
                fields[key] = value.strip()
            if set(fields) != {"series", "unit", "category", "value"} or any(not fields[k] for k in fields):
                raise ValueError(f"{context}: each PDF chart record requires exactly series/unit/category/value")
            try:
                fields["value"] = Decimal(fields["value"])
            except InvalidOperation:
                raise ValueError(f"{context}: PDF chart value is not numeric")
            if not fields["value"].is_finite():
                raise ValueError(f"{context}: PDF chart value must be finite")
            records.append(fields)
        if not records:
            raise ValueError(f"{context}: no structured chart records on PDF p.{page}")
        units = {record["unit"] for record in records}
        if len(units) != 1:
            raise ValueError(f"{context}: PDF chart records must use one exact unit")
        unit = units.pop()
        if visual.get("unit") != unit:
            raise ValueError(f"{context}: chart unit differs from PDF structured records")
        categories, expected = [], {}
        for record in records:
            series, category = record["series"], record["category"]
            expected.setdefault(series, {})
            if category in expected[series]:
                raise ValueError(f"{context}: duplicate PDF series/category record")
            if category not in categories:
                categories.append(category)
            expected[series][category] = record["value"]
        if visual.get("categories") != categories:
            raise ValueError(f"{context}: chart categories differ from PDF structured records")
        actual_series = visual.get("series")
        if not isinstance(actual_series, list) or [item.get("name") for item in actual_series] != list(expected):
            raise ValueError(f"{context}: chart series names differ from PDF structured records")
        for item in actual_series:
            name = item["name"]
            if set(expected[name]) != set(categories):
                raise ValueError(f"{context}: PDF series {name} lacks a value for every category")
            if [number(value, f"{context} chart value") for value in item.get("values", [])] != [expected[name][category] for category in categories]:
                raise ValueError(f"{context}: chart values differ from PDF structured records for series {name}")
        expected_axis = f"{list(expected)[0]} ({unit})" if len(expected) == 1 else f"Value ({unit})"
        if visual.get("y_title") != expected_axis:
            raise ValueError(f"{context}: value-axis title must exactly bind the source measure and unit")

    def validate_image(self, visual, citations, root, context):
        """Require a byte-reproducible PDF crop; an unrelated PNG is not evidence."""
        import fitz

        source = visual.get("source")
        image_path = (root / visual.get("path", "")).resolve()
        if not isinstance(source, dict) or not image_path.is_file():
            raise ValueError(f"{context}: PDF-derived image requires an actual PNG and source record")
        page = source.get("pdf_page")
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= len(self.page_lines):
            raise ValueError(f"{context}: image physical PDF page is invalid")
        if not any(s.get("pdf_page") == page and s.get("figure") and visual.get("figure", "").startswith(s["figure"]) for s in citations):
            raise ValueError(f"{context}: image needs a citation to the same figure on the same physical PDF page")
        clip, matrix = source.get("clip"), source.get("matrix")
        if not isinstance(clip, list) or len(clip) != 4 or not isinstance(matrix, list) or len(matrix) != 2:
            raise ValueError(f"{context}: PDF image requires clip=[x0,y0,x1,y1] and matrix=[sx,sy]")
        try:
            coordinates = [number(v, f"{context} clip") for v in clip]
            scales = [number(v, f"{context} matrix") for v in matrix]
        except ValueError:
            raise
        if any(v <= 0 for v in scales) or coordinates[0] >= coordinates[2] or coordinates[1] >= coordinates[3]:
            raise ValueError(f"{context}: invalid PDF crop or raster scale")
        if source.get("source_sha256") is not None and source["source_sha256"] != self.sha256:
            raise ValueError(f"{context}: source PDF version differs from the image provenance lock")
        actual_hash = sha(image_path)
        if source.get("recrop_sha256") != actual_hash:
            raise ValueError(f"{context}: recrop_sha256 must lock the exact supplied PNG")
        with fitz.open(str(self.path)) as document:
            pdf_page = document[page - 1]
            rectangle = fitz.Rect(*(float(v) for v in coordinates))
            if not pdf_page.rect.contains(rectangle):
                raise ValueError(f"{context}: PDF crop exceeds the physical page")
            pixmap = pdf_page.get_pixmap(matrix=fitz.Matrix(*(float(v) for v in scales)), clip=rectangle, alpha=False)
            regenerated = pixmap.tobytes("png")
        if hashlib.sha256(regenerated).hexdigest() != actual_hash or regenerated != image_path.read_bytes():
            raise ValueError(f"{context}: PNG is not the declared byte-reproducible crop of the locked PDF page")
        return image_path


def assert_saved_chart(chart, visual, context):
    if [category.label for category in chart.plots[0].categories] != visual["categories"]:
        raise AssertionError(f"{context}: saved chart categories differ from validated source fields")
    actual = list(chart.series)
    if [series.name for series in actual] != [item["name"] for item in visual["series"]]:
        raise AssertionError(f"{context}: saved chart series differ from validated source fields")
    for saved, expected in zip(actual, visual["series"]):
        if [number(value, "saved chart value") for value in saved.values] != [number(value, "source chart value") for value in expected["values"]]:
            raise AssertionError(f"{context}: saved chart values differ from validated source fields")
    if not chart.value_axis.has_title or chart.value_axis.axis_title.text_frame.text != visual["y_title"]:
        raise AssertionError(f"{context}: saved value-axis unit/title differs from validated source fields")
    if not chart.has_title or chart.chart_title.text_frame.text != visual["figure"]:
        raise AssertionError(f"{context}: saved chart title differs from its cited figure")


def placeholder(slide, idx):
    return next((s for s in slide.placeholders if s.placeholder_format.idx == idx), None)


def layout_identity(layout):
    return str(layout.part.partname), str(layout.slide_master.part.partname)


def target_layout(prs, name):
    matches = [layout for master in prs.slide_masters for layout in master.slide_layouts if layout.name == name]
    identities = {layout_identity(layout) for layout in matches}
    if not matches:
        raise ValueError(f"actual template lacks layout: {name}")
    if len(identities) != 1:
        raise ValueError(f"layout name is ambiguous across layout parts/masters: {name}")
    return matches[0]


def inherited_title_font_pt(title, slide):
    """Read explicit OOXML inheritance levels; never invent a 24 pt default."""
    from pptx.oxml.ns import qn

    candidates = [title.text_frame.paragraphs[0]] if title.text_frame.paragraphs else []
    for paragraph in candidates:
        for run in paragraph.runs:
            if run.font.size is not None:
                return run.font.size.pt
        if paragraph.font.size is not None:
            return paragraph.font.size.pt
    idx = title.placeholder_format.idx
    owners = [title]
    layout_placeholder = placeholder(slide.slide_layout, idx)
    if layout_placeholder is not None:
        owners.append(layout_placeholder)
    for owner in owners:
        for level in owner._element.xpath("./p:txBody/a:lstStyle/a:lvl1pPr/a:defRPr"):
            size = level.get("sz")
            if size:
                return int(size) / 100
    for level in slide.slide_layout.slide_master._element.xpath("./p:txStyles/p:titleStyle/a:lvl1pPr/a:defRPr"):
        size = level.get("sz")
        if size:
            return int(size) / 100
    return None


def native_chart(slide, visual):
    target = placeholder(slide, 2)
    if target is None:
        raise ValueError("QA/evidence visual requires real placeholder idx=2")
    cats, series = visual.get("categories", []), visual.get("series", [])
    if not cats or not series or any(len(s.get("values", [])) != len(cats) for s in series):
        raise ValueError("chart source data are not aligned")
    data = ChartData()
    data.categories = cats
    for item in series:
        data.add_series(item["name"], item["values"])
    box = target.left, target.top, target.width, target.height
    target._element.getparent().remove(target._element)
    chart = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, *box, data).chart
    chart.has_legend = len(series) > 1
    chart.has_title = bool(visual.get("figure"))
    if chart.has_title:
        chart.chart_title.text_frame.text = visual["figure"]
    for axis, key in ((chart.category_axis, "x_title"), (chart.value_axis, "y_title")):
        axis.has_title = bool(visual.get(key))
        if axis.has_title:
            axis.axis_title.text_frame.text = visual[key]
        axis.tick_labels.font.size = Pt(12)


def native_picture(slide, visual, image_path):
    from pptx.parts.image import Image

    target = placeholder(slide, 2)
    if target is None:
        raise ValueError("PDF evidence image requires real placeholder idx=2")
    pixel_width, pixel_height = Image.from_file(str(image_path)).size
    scale = min(target.width / pixel_width, target.height / pixel_height)
    width, height = int(pixel_width * scale), int(pixel_height * scale)
    left, top = int(target.left + (target.width - width) / 2), int(target.top + (target.height - height) / 2)
    target._element.getparent().remove(target._element)
    picture = slide.shapes.add_picture(str(image_path), left, top, width=width, height=height)
    if any(abs(getattr(picture, f"crop_{side}")) > 0.001 for side in ("left", "right", "top", "bottom")):
        raise AssertionError("PDF evidence image must remain uncropped inside its content slot")


def master_theme_hash(path):
    import posixpath
    import zipfile
    from xml.etree import ElementTree as ET
    result = hashlib.sha256()
    with zipfile.ZipFile(path) as zf:
        names, themes = set(zf.namelist()), set()
        for rel_name in names:
            if rel_name.startswith("ppt/slideMasters/_rels/") and rel_name.endswith(".rels"):
                for relation in ET.fromstring(zf.read(rel_name)):
                    if relation.attrib.get("Type", "").endswith("/theme"):
                        target = posixpath.normpath(posixpath.join("ppt/slideMasters", relation.attrib["Target"]))
                        if target in names:
                            themes.add(target)
        for name in sorted(n for n in names if (n.startswith(("ppt/slideMasters/", "ppt/slideLayouts/")) and n.endswith((".xml", ".rels"))) or n in themes):
            result.update(name.encode())
            result.update(zf.read(name))
    return result.hexdigest()


def validate_content(spec):
    profile = spec.get("audience_profile", {})
    if not all(profile.get(k) for k in ("role", "prior_knowledge", "goal")):
        raise ValueError("case-derived audience role, prior knowledge and goal are required")
    pages, candidates = spec["pages"], spec["candidates"]
    if len(pages) != spec["slide_count"]:
        raise ValueError("organized pages must equal fixed slide_count")
    duration = number(spec["time_minutes"], "time_minutes") * 60
    if duration <= 0:
        raise ValueError("time_minutes must be positive")
    main_seconds = Decimal(0)
    for index, page in enumerate(pages, 1):
        seconds = number(page.get("seconds"), f"slide {index} seconds")
        if seconds <= 0:
            raise ValueError(f"slide {index}: seconds must be positive and finite")
        if page.get("role") != "qa-backup":
            main_seconds += seconds
    if main_seconds != duration:
        raise ValueError(f"non-backup presentation time must equal fixed duration: planned {main_seconds}s, required {duration}s")
    catalog = {c["id"]: c for c in candidates}
    if len(catalog) != len(candidates):
        raise ValueError("duplicate candidate ids")
    for candidate in candidates:
        src = candidate.get("source", {})
        if candidate.get("disposition") not in ("main", "notes", "backup", "drop") or not candidate.get("reason"):
            raise ValueError("every candidate needs an explicit audience-based disposition")
        if not src.get("quote") or not isinstance(src.get("pdf_page"), int) or src["pdf_page"] < 1:
            raise ValueError("candidate lacks exact source or physical PDF page")
        if not candidate.get("evidence_strength") or not candidate.get("text"):
            raise ValueError("candidate lacks text or calibrated evidence strength")
    roles, claims = set(), set()
    for index, page in enumerate(pages, 1):
        for field in ("role", "claim", "audience_question", "seconds", "layout"):
            if not page.get(field):
                raise ValueError(f"slide {index}: missing {field}")
        if page["claim"] in claims:
            raise ValueError("multiple pages carry the same claim")
        if page["role"] in roles and page["role"] in ("opening", "synthesis", "qa-backup"):
            raise ValueError("duplicate primary narrative role")
        roles, claims = roles | {page["role"]}, claims | {page["claim"]}
        if not page.get("candidate_ids"):
            raise ValueError(f"slide {index}: no source candidate selected")
        for cid in page["candidate_ids"]:
            if cid not in catalog or catalog[cid]["disposition"] == "drop":
                raise ValueError(f"slide {index}: selected dropped/unknown candidate {cid}")
        if page["role"] == "qa-backup" and not page.get("qa_ids"):
            raise ValueError("allowed backup pages must connect to prepared Q&A")
        notes = page.get("notes", {})
        if not all(notes.get(k) for k in ("transition", "emphasis", "backup")):
            raise ValueError("speaker notes must add transition, emphasis and backup evidence")
        for visual in ([page["visual"]] if page.get("visual") else []):
            if visual.get("kind") not in ("chart", "image") or not visual.get("figure"):
                raise ValueError("only source-bound native charts and reproducible PDF-page images are accepted")
    qas = {qa["id"]: qa for qa in spec.get("qa", [])}
    if not qas:
        raise ValueError("at least one sourced expected question is required")
    for qa in spec["qa"]:
        if qa.get("page") not in range(1, len(pages)+1) or not qa.get("question") or not qa.get("answer") or not qa.get("answer_boundary"):
            raise ValueError("Q&A needs question, scoped answer, existing page and answer boundary")
        src = qa.get("source", {})
        if not src.get("quote") or not isinstance(src.get("pdf_page"), int):
            raise ValueError("Q&A needs source PDF page and exact quote")
        if qa["id"] not in pages[qa["page"]-1].get("qa_ids", []):
            raise ValueError("Q&A is not linked from its named response page")
    valid_roles = {"opening", "question", "method", "evidence", "synthesis", "qa-backup"}
    if not valid_roles.issuperset(roles) or not {"opening", "evidence", "synthesis"}.issubset(roles):
        raise ValueError("narrative must include opening, evidence and synthesis; roles not recognized")
    return catalog, qas


def compose(spec_path, outdir):
    spec_path = spec_path.resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    root = spec_path.parent
    for key in ("case_id", "source_material", "deck", "mode", "time_minutes", "slide_count", "output_contract"):
        if key not in spec:
            raise ValueError(f"missing case field: {key}")
    catalog, qas = validate_content(spec)
    source, deck = (root / spec["source_material"]).resolve(), (root / spec["deck"]).resolve()
    if not source.is_file() or not deck.is_file():
        raise FileNotFoundError("actual source paper/material and template/starting deck are required")
    pdf_evidence = PDFEvidence(source)
    for candidate in spec["candidates"]:
        pdf_evidence.validate_source(candidate["source"], f"candidate {candidate['id']}")
    for qa in spec["qa"]:
        pdf_evidence.validate_source(qa["source"], f"Q&A {qa['id']}")
    resolved_images = {}
    for number, page in enumerate(spec["pages"], 1):
        visual = page.get("visual")
        if visual:
            citations = [catalog[cid]["source"] for cid in page["candidate_ids"]]
            if visual.get("kind") == "chart":
                pdf_evidence.validate_chart(visual, citations, f"slide {number}")
            elif visual.get("kind") == "image":
                resolved_images[number] = pdf_evidence.validate_image(visual, citations, root, f"slide {number}")
    outdir.mkdir(parents=True, exist_ok=True)
    baseline = outdir / "organization_baseline.pptx"
    shutil.copyfile(deck, baseline)
    master_before = master_theme_hash(deck)
    prs = Presentation(str(deck))
    if spec["mode"] == "from-template":
        if len(prs.slides) != 0:
            raise ValueError("from-template requires a zero-slide actual template")
    elif spec["mode"] == "existing-deck-edit":
        if len(prs.slides) != spec["slide_count"]:
            raise ValueError("actual starting deck count differs from fixed slide_count")
        if spec.get("original_sha256") != sha(deck):
            raise ValueError("actual starting deck changed since organization planning")
    else:
        raise ValueError("mode must be from-template or existing-deck-edit")
    title_fonts = []
    for number, page in enumerate(spec["pages"], 1):
        planned_layout = target_layout(prs, page["layout"])
        slide = prs.slides[number-1] if spec["mode"] == "existing-deck-edit" else prs.slides.add_slide(planned_layout)
        if layout_identity(slide.slide_layout) != layout_identity(planned_layout):
            raise ValueError(f"slide {number}: planned layout part/master differs from actual starting-deck layout")
        title, body = placeholder(slide, 0), placeholder(slide, 1)
        if title is None:
            raise ValueError(f"slide {number}: actual title placeholder missing")
        if page.get("points") and body is None:
            raise ValueError(f"slide {number}: body points exist but actual body placeholder is missing")
        title.text = page["claim"]
        if "title_font_pt" in spec:
            title_size = number(spec["title_font_pt"], "title_font_pt")
            if not 24 <= title_size <= 54:
                raise ValueError("title_font_pt must follow actual template readable policy")
            for para in title.text_frame.paragraphs:
                for run in para.runs:
                    run.font.size = Pt(title_size)
            resolved_size = float(title_size)
            font_review = "explicit"
        else:
            resolved_size = inherited_title_font_pt(title, slide)
            font_review = "resolved-inherited" if resolved_size is not None else "pending-inherited-font-size"
        if resolved_size is not None and title.height < Pt(100) and resolved_size > 36:
            # Compact slots use a smaller font cap without changing the template's geometry or larger title styles.
            declared_size = resolved_size
            for para in title.text_frame.paragraphs:
                for run in para.runs:
                    run.font.size = Pt(36)
            resolved_size = 36.0
            font_review += f"; compact-slot cap {declared_size:g}pt->36pt"
        title_fonts.append({"slide": number, "font_pt": resolved_size, "review": font_review})
        if body is not None:
            body.text = "\n".join(page.get("points", []))
        if page.get("visual", {}).get("kind") == "chart":
            native_chart(slide, page["visual"])
        elif page.get("visual", {}).get("kind") == "image":
            native_picture(slide, page["visual"], resolved_images[number])
        qa_text = []
        for qa_id in page.get("qa_ids", []):
            qa = qas[qa_id]
            qa_text.append(f"Q&A {qa_id}: {qa['question']} / {qa['answer']} / Boundary: {qa['answer_boundary']} / PDF p.{qa['source']['pdf_page']}")
        notes = page["notes"]
        note = "Audience question: {}\nTransition: {}\nEmphasis: {}\nExtra evidence: {}\nCandidate dispositions: {}\n{}".format(
            page["audience_question"], notes["transition"], notes["emphasis"], notes["backup"],
            ", ".join(f"{cid}={catalog[cid]['disposition']}" for cid in page["candidate_ids"]), "\n".join(qa_text))
        slide.notes_slide.notes_text_frame.text = note
        page["resolved_sources"] = [catalog[cid]["source"] for cid in page["candidate_ids"]]
    if sha(source) != pdf_evidence.sha256:
        raise ValueError("source PDF changed during composition; rebuild from one locked source version")
    temp = outdir / "organized.tmp.pptx"
    final = outdir / "organized_talk.pptx"
    prs.save(str(temp))
    if len(Presentation(str(temp)).slides) != spec["slide_count"] or master_theme_hash(temp) != master_before:
        raise AssertionError("fixed slide count or actual master/theme changed")
    temp.replace(final)
    reopened = Presentation(str(final))
    for number, slide in enumerate(reopened.slides, 1):
        if not slide.notes_slide.notes_text_frame.text.strip():
            raise AssertionError(f"slide {number}: missing native notes")
        if not any(s.has_text_frame or s.has_chart or s.shape_type == 13 for s in slide.shapes):
            raise AssertionError(f"slide {number}: no editable object")
        visual = spec["pages"][number-1].get("visual")
        if visual and visual.get("kind") == "chart":
            charts = [shape.chart for shape in slide.shapes if shape.has_chart]
            if len(charts) != 1:
                raise AssertionError(f"slide {number}: expected one source-bound native chart")
            assert_saved_chart(charts[0], visual, f"slide {number}")
    pending_fonts = [item["slide"] for item in title_fonts if item["review"] == "pending-inherited-font-size"]
    limitations = ["actual final-PPTX rendering and page-by-page visual review pending"]
    if pending_fonts:
        limitations.append(f"inherited title font size unresolved on slides {pending_fonts}; manual review required")
    render = {"status": "pending", "limitations": limitations}
    main_seconds = sum(p["seconds"] for p in spec["pages"] if p["role"] != "qa-backup")
    backup_seconds = sum(p["seconds"] for p in spec["pages"] if p["role"] == "qa-backup")
    report = {"case_id": spec["case_id"], "source_material": str(source), "source_sha256": sha(source),
              "baseline_sha256": sha(deck), "final_sha256": sha(final),
              "audience_profile": spec["audience_profile"], "slide_count": len(reopened.slides),
              "layout_names": [s.slide_layout.name for s in reopened.slides],
              "layout_bindings": [{"slide": n, "layout_part": layout_identity(s.slide_layout)[0], "master_part": layout_identity(s.slide_layout)[1]}
                                  for n, s in enumerate(reopened.slides, 1)],
              "master_theme_preserved": True, "title_fonts": title_fonts,
              "seconds_total": main_seconds, "backup_seconds_excluded": backup_seconds, "time_limit_seconds": spec["time_minutes"] * 60,
              "source_page_ledger": pdf_evidence.page_ledger,
              "python": platform.python_version(), "python-pptx": pptx.__version__, "render": render}
    dump(outdir / "source_page_ledger.json", {"source_sha256": pdf_evidence.sha256, "pages": pdf_evidence.page_ledger})
    dump(outdir / "content_decisions.json", {"candidates": spec["candidates"], "pages": spec["pages"]})
    dump(outdir / "qa_map.json", {"qa": spec["qa"]})
    dump(outdir / "talk_record.json", report)
    result = dict(spec["output_contract"])
    if "case_id" not in result or result["case_id"] != spec["case_id"]:
        raise ValueError("materialized output_contract case_id must match and remain unchanged")
    values = {"status": "partial", "artifacts": [str(final)], "primary_result": str(final), "output": str(final),
              "sources": [spec["source_material"]], "uncertainty": spec.get("uncertainty", []) + render["limitations"],
              "render": render, "validation": render, "qa": spec["qa"], "decisions": spec["candidates"]}
    for key, value in values.items():
        if key not in result:
            continue
        if result[key] is not None and type(value) is not type(result[key]):
            raise TypeError(f"contract field {key} changed type")
        if key in ("render", "validation") and isinstance(result[key], dict):
            merged = dict(result[key])
            merged.update(value)
            result[key] = merged
        elif key == "uncertainty" and isinstance(result[key], list):
            result[key] = result[key] + [item for item in value if item not in result[key]]
        else:
            result[key] = value
    dump(outdir / "result.json", result)
    print(json.dumps({"pptx": str(final), "slides": len(reopened.slides), "qa": len(qas), "status": "structure-passed-render-pending"}, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()
    try:
        compose(Path(args.spec), Path(args.outdir))
    except (OSError, ValueError, TypeError, KeyError, AssertionError) as exc:
        print(f"compose-blocked: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
