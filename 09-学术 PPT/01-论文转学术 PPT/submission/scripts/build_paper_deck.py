#!/usr/bin/env python3
"""Render an evidence-audited paper deck. Requires python-pptx >=1.0."""
import argparse
import hashlib
import json
import math
import platform
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

try:
    import pptx
    from pptx import Presentation
    from pptx.chart.data import ChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    from pptx.enum.text import MSO_AUTO_SIZE
    from pptx.util import Pt
except ImportError:
    raise SystemExit("dependency-blocked: install python-pptx>=1.0 from resources.yaml")


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{field} must be numeric")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


class PDFEvidence:
    """Read the actual PDF; quotes and chart records never come from a sidecar text file."""

    def __init__(self, path):
        if path.suffix.lower() != ".pdf":
            raise ValueError("source material must be the actual PDF")
        try:
            import fitz
        except ImportError:
            raise ValueError("dependency-blocked: PyMuPDF is required to read source PDF pages")
        with fitz.open(str(path)) as document:
            self.page_lines = [[self.canonical(line) for line in page.get_text("text", sort=True).splitlines() if self.canonical(line)]
                               for page in document]
        if not self.page_lines:
            raise ValueError("source PDF has no pages")

    @staticmethod
    def canonical(value):
        return " ".join(value.split())

    def validate_source(self, source, context):
        page = source.get("pdf_page")
        quote = source.get("quote")
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= len(self.page_lines):
            raise ValueError(f"{context}: PDF page is outside the actual document")
        if not isinstance(quote, str) or not self.canonical(quote):
            raise ValueError(f"{context}: exact source quote required")
        quote = self.canonical(quote)
        lines = self.page_lines[page-1]
        matches = 0
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
        return {"pdf_page": page, "quote": quote}

    def validate_chart(self, visual, citations, context):
        source = visual.get("source")
        if not isinstance(source, dict):
            raise ValueError(f"{context}: chart requires a PDF structured-record source")
        page, prefix = source.get("pdf_page"), source.get("record_prefix")
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= len(self.page_lines) or not isinstance(prefix, str) or not prefix:
            raise ValueError(f"{context}: invalid chart PDF page or record prefix")
        linked = [s for s in citations if s.get("pdf_page") == page and s.get("figure") and prefix == s["figure"] + " data:"]
        if not linked:
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
        units = {r["unit"] for r in records}
        if len(units) != 1:
            raise ValueError(f"{context}: PDF chart records must use one exact unit")
        unit = units.pop()
        if visual.get("unit") != unit:
            raise ValueError(f"{context}: chart unit differs from PDF structured records")
        categories = []
        expected = {}
        for record in records:
            series, category = record["series"], record["category"]
            if series not in expected:
                expected[series] = {}
            if category in expected[series]:
                raise ValueError(f"{context}: duplicate PDF series/category record")
            if category not in categories:
                categories.append(category)
            expected[series][category] = record["value"]
        if visual.get("categories") != categories:
            raise ValueError(f"{context}: chart categories differ from PDF structured records")
        actual_series = visual.get("series")
        if not isinstance(actual_series, list) or [s.get("name") for s in actual_series] != list(expected):
            raise ValueError(f"{context}: chart series names differ from PDF structured records")
        for item in actual_series:
            name = item["name"]
            if set(expected[name]) != set(categories):
                raise ValueError(f"{context}: PDF series {name} lacks a value for every category")
            actual_values = [number(v, f"{context} chart value") for v in item.get("values", [])]
            expected_values = [expected[name][category] for category in categories]
            if actual_values != expected_values:
                raise ValueError(f"{context}: chart values differ from PDF structured records for series {name}")
        expected_axis = f"{list(expected)[0]} ({unit})" if len(expected) == 1 else f"Value ({unit})"
        if visual.get("y_title") != expected_axis:
            raise ValueError(f"{context}: value-axis title must exactly bind the source measure and unit")


def assert_saved_chart(chart, visual, context):
    plot = chart.plots[0]
    if [category.label for category in plot.categories] != visual["categories"]:
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


def placeholder(slide, kinds):
    for shape in slide.placeholders:
        if str(shape.placeholder_format.type).split()[0] in kinds or shape.placeholder_format.idx in kinds:
            return shape
    return None


def text_width(text):
    return sum(1.0 if ord(ch) > 255 else 0.56 for ch in text)


def fit_check(shape, slide_no, issues):
    if not shape.has_text_frame or not shape.text.strip():
        return
    frame = shape.text_frame
    width = max(1, shape.width / 12700 - 20)
    height = max(1, shape.height / 12700 - 12)
    used = 0.0
    for para in frame.paragraphs:
        size = max((r.font.size.pt for r in para.runs if r.font.size), default=24.0)
        if size < 14:
            issues.append(f"slide {slide_no}: font below 14pt")
        capacity = max(1.0, width / (size * 0.58))
        lines = max(1, math.ceil(text_width(para.text) / capacity))
        used += lines * size * 1.25 + 6
    if used > height:
        issues.append(f"slide {slide_no}: estimated text overflow ({used:.0f}>{height:.0f}pt)")


def add_native_chart(slide, box, visual):
    series = visual.get("series", [])
    if not series or not visual.get("categories"):
        raise ValueError("chart requires source-derived categories and series")
    if any(len(s["values"]) != len(visual["categories"]) for s in series):
        raise ValueError("chart series length differs from categories")
    data = ChartData()
    data.categories = visual["categories"]
    for item in series:
        data.add_series(item["name"], item["values"])
    chart = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, *box, data).chart
    chart.has_legend = len(series) > 1
    chart.has_title = bool(visual.get("figure"))
    if chart.has_title:
        chart.chart_title.text_frame.text = visual["figure"]
    for axis, key in ((chart.category_axis, "x_title"), (chart.value_axis, "y_title")):
        axis.has_title = bool(visual.get(key))
        if axis.has_title:
            axis.axis_title.text_frame.text = visual[key]
        axis.tick_labels.font.size = Pt(max(11, visual.get("axis_font_pt", 12)))
    chart.chart_style = 10
    return chart


def add_figure(slide, box, visual, root):
    path = (root / visual["path"]).resolve()
    if root.resolve() not in path.parents or not path.is_file():
        raise ValueError("source figure must be a regular file inside the case workspace")
    from PIL import Image
    x, y, w, h = box
    with Image.open(path) as im:
        ratio = min(w / im.width, h / im.height)
        disp_w, disp_h = int(im.width * ratio), int(im.height * ratio)
    pic = slide.shapes.add_picture(str(path), x + (w - disp_w) // 2, y + (h - disp_h) // 2,
                                   width=disp_w, height=disp_h)
    pic.name = f"Source figure: {visual.get('figure', 'unnumbered')}"
    return pic


def render_result(spec, deck, pdf, render):
    contract = spec["output_contract"]
    if not isinstance(contract, dict) or "case_id" not in contract:
        raise ValueError("materialized output_contract must retain case_id and all case fields")
    result = dict(contract)
    result["case_id"] = spec["case_id"]
    values = {
        "status": "partial" if render.get("status") != "passed" else "completed",
        "artifacts": [str(deck)],
        "primary_result": str(deck),
        "output": str(deck),
        "sources": [spec["source_pdf"]],
        "uncertainty": spec.get("uncertainty", []) + render.get("limitations", []),
        "evidence": "evidence_map.json",
        "render": render,
        "validation": render,
    }
    for key, value in values.items():
        if key not in result:
            continue
        expected = type(result[key])
        if result[key] is not None and not isinstance(value, expected):
            raise TypeError(f"output_contract field {key} must remain {expected.__name__}")
        result[key] = value
    return result


def build(spec_path, outdir):
    spec_path = spec_path.resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    root = spec_path.parent
    for key in ("case_id", "source_pdf", "template", "audience", "time_minutes", "slide_count", "pages", "output_contract"):
        if key not in spec:
            raise ValueError(f"missing case-derived field: {key}")
    source_pdf, template = (root / spec["source_pdf"]).resolve(), (root / spec["template"]).resolve()
    if not source_pdf.is_file() or not template.is_file():
        raise FileNotFoundError("actual source paper and editable template are required")
    pdf_evidence = PDFEvidence(source_pdf)
    if len(spec["pages"]) != spec["slide_count"] or not isinstance(spec["slide_count"], int) or spec["slide_count"] < 1:
        raise ValueError("pages must exactly equal the fixed case slide_count")
    if sum(p.get("seconds", 0) for p in spec["pages"]) > spec["time_minutes"] * 60:
        raise ValueError("page timings exceed case time_minutes")
    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / "paper_talk.pptx"
    prs = Presentation(str(template))
    for old in list(prs.slides._sldIdLst):
        r_id = old.rId
        prs.part.drop_rel(r_id)
        prs.slides._sldIdLst.remove(old)
    layouts = {layout.name: layout for layout in prs.slide_layouts}
    evidence, issues = [], []
    for index, page in enumerate(spec["pages"], 1):
        sources = page.get("sources", [])
        if not page.get("claim") or not sources or not page.get("role"):
            raise ValueError(f"slide {index}: claim, role and sources required")
        for source_index, src in enumerate(sources, 1):
            pdf_evidence.validate_source(src, f"slide {index} source {source_index}")
        visual = page.get("visual")
        if visual and visual.get("kind") == "chart":
            pdf_evidence.validate_chart(visual, sources, f"slide {index}")
        if page.get("layout") not in layouts:
            raise ValueError(f"slide {index}: unknown actual layout {page.get('layout')}")
        slide = prs.slides.add_slide(layouts[page["layout"]])
        title = placeholder(slide, ("TITLE", "CENTER_TITLE", 0))
        if title is None:
            raise ValueError(f"slide {index}: selected layout has no title placeholder")
        title.text = page["claim"]
        title_size = spec.get("title_font_pt", 28)
        if not isinstance(title_size, (int, float)) or not 24 <= title_size <= 54:
            raise ValueError("title_font_pt must follow the actual template's readable 24-54pt policy")
        for para in title.text_frame.paragraphs:
            for run in para.runs:
                run.font.size = Pt(title_size)
        body = placeholder(slide, ("BODY", "OBJECT", 1))
        if body is not None:
            body.text = "\n".join(page.get("points", []))
            body.text_frame.word_wrap = True
            body.text_frame.auto_size = MSO_AUTO_SIZE.NONE
        if visual:
            target = placeholder(slide, (2,))
            if target is None:
                raise ValueError("visual pages require actual placeholder idx=2; no invented overlay")
            box = target.left, target.top, target.width, target.height
            target._element.getparent().remove(target._element)
            if visual.get("kind") == "chart":
                add_native_chart(slide, box, visual)
            elif visual.get("kind") == "figure":
                add_figure(slide, box, visual, root)
            else:
                raise ValueError("visual kind must be source-derived chart or figure")
        notes = page.get("notes", {})
        if not notes.get("transition") or not notes.get("emphasis") or not notes.get("backup"):
            raise ValueError(f"slide {index}: transition, emphasis and non-duplicative backup notes required")
        note_text = "Transition: {}\nEmphasis: {}\nExtra evidence: {}\nSources: {}".format(
            notes["transition"], notes["emphasis"], notes["backup"],
            "; ".join(f"p.{s['pdf_page']} {s.get('section', '')} {s.get('figure', '')}".strip() for s in sources))
        slide.notes_slide.notes_text_frame.text = note_text
        evidence.append({"slide": index, "role": page["role"], "claim": page["claim"],
                         "points": page.get("points", []), "sources": sources,
                         "figure": visual.get("figure") if visual else None,
                         "seconds": page["seconds"], "layout": page["layout"]})
        for shape in slide.shapes:
            if shape.left < 0 or shape.top < 0 or shape.left + shape.width > prs.slide_width or shape.top + shape.height > prs.slide_height:
                issues.append(f"slide {index}: shape crosses slide bounds")
            fit_check(shape, index, issues)
    if issues:
        save_json(outdir / "validation.json", {"status": "blocked", "issues": issues})
        raise ValueError("; ".join(issues))
    prs.save(str(dest))
    reopened = Presentation(str(dest))
    if len(reopened.slides) != spec["slide_count"]:
        raise AssertionError("reopen page count mismatch")
    for i, slide in enumerate(reopened.slides, 1):
        editable = sum(s.has_text_frame or s.has_chart or s.shape_type == MSO_SHAPE_TYPE.PICTURE for s in slide.shapes)
        if editable < 1 or not slide.notes_slide.notes_text_frame.text.strip():
            raise AssertionError(f"slide {i}: editable objects or notes missing")
        visual = spec["pages"][i-1].get("visual")
        if visual and visual.get("kind") == "chart":
            charts = [shape.chart for shape in slide.shapes if shape.has_chart]
            if len(charts) != 1:
                raise AssertionError(f"slide {i}: expected one source-bound native chart")
            assert_saved_chart(charts[0], visual, f"slide {i}")
    manifest = {"case_id": spec["case_id"], "audience": spec["audience"], "slide_count": len(reopened.slides),
                "source_pdf_sha256": sha256(source_pdf), "template_sha256": sha256(template),
                "pptx_sha256": sha256(dest), "python": platform.python_version(), "python-pptx": pptx.__version__,
                "layout_names": [s.slide_layout.name for s in reopened.slides], "status": "editable-reopen-passed"}
    render = {"status": "not-run", "limitations": ["final PPTX must be rendered by the case office renderer and inspected page by page"]}
    save_json(outdir / "evidence_map.json", {"pages": evidence})
    save_json(outdir / "technical_record.json", manifest)
    save_json(outdir / "validation.json", {"status": "structure-passed-render-pending", "issues": [], "overflow_method": "conservative estimate, not visual proof"})
    save_json(outdir / "result.json", render_result(spec, dest, source_pdf, render))
    print(json.dumps({"pptx": str(dest), "slides": len(reopened.slides), "status": "structure-passed-render-pending"}, ensure_ascii=False))
    return dest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--outdir", required=True)
    args = parser.parse_args()
    try:
        build(Path(args.spec), Path(args.outdir))
    except (ValueError, OSError, KeyError, TypeError, AssertionError) as exc:
        print(f"build-blocked: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
