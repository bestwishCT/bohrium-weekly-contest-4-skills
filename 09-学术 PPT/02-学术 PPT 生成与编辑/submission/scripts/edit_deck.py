#!/usr/bin/env python3
"""Guarded native-PPTX edits with per-slide fingerprints, charts, diff and rollback."""
import argparse
import copy
import hashlib
import json
import platform
import shutil
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET

try:
    import pptx
    from pptx import Presentation
    from pptx.chart.data import ChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    from pptx.util import Pt
except ImportError:
    raise SystemExit("dependency-blocked: install python-pptx>=1.0 from resources.yaml")


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
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
        with fitz.open(str(path)) as document:
            self.page_lines = [[self.canonical(line) for line in page.get_text("text", sort=True).splitlines() if self.canonical(line)]
                               for page in document]
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


def ph(slide, idx):
    return next((s for s in slide.placeholders if s.placeholder_format.idx == idx), None)


def slide_state(slide):
    shapes = []
    for shape in slide.shapes:
        item = {"name": shape.name, "type": int(shape.shape_type), "box": [shape.left, shape.top, shape.width, shape.height]}
        if shape.has_text_frame:
            item["text"] = shape.text
        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            item["image_sha256"] = hashlib.sha256(shape.image.blob).hexdigest()
        if shape.has_chart:
            item["chart_type"] = int(shape.chart.chart_type)
        shapes.append(item)
    return {"layout": slide.slide_layout.name, "shapes": shapes,
            "notes": slide.notes_slide.notes_text_frame.text}


def fingerprint(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def deck_state(path):
    prs = Presentation(str(path))
    with path.open("rb") as _:
        pass
    import zipfile
    import posixpath
    master = hashlib.sha256()
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
            master.update(name.encode())
            master.update(zf.read(name))
    pages = [{"slide": n, "fingerprint": fingerprint(slide_state(s)), **slide_state(s)} for n, s in enumerate(prs.slides, 1)]
    return {"slides": len(prs.slides), "master_theme_sha256": master.hexdigest(), "pages": pages}


def clear_shape_text(shape):
    if shape.has_text_frame:
        shape.text_frame.clear()


def add_chart(slide, target, visual):
    series = visual.get("series", [])
    cats = visual.get("categories", [])
    if not cats or not series or any(len(item["values"]) != len(cats) for item in series):
        raise ValueError("chart values must exactly align with source categories")
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
    return chart


def apply_edit(prs, before, op):
    number = op.get("slide")
    if not isinstance(number, int) or not 1 <= number <= len(prs.slides):
        raise ValueError("edit slide is outside fixed deck")
    slide = prs.slides[number-1]
    if fingerprint(slide_state(slide)) != op.get("expected_slide_fingerprint"):
        raise ValueError(f"slide {number}: optimistic-lock fingerprint mismatch; reread deck")
    action = op.get("action")
    if action == "replace_title":
        target = ph(slide, 0)
        if target is None:
            raise ValueError("actual slide has no title placeholder")
        target.text = op["title"]
        title_size = op.get("title_font_pt", 28)
        if not 24 <= title_size <= 54:
            raise ValueError("title_font_pt must be within the actual template's 24-54pt ladder")
        for para in target.text_frame.paragraphs:
            for run in para.runs:
                run.font.size = Pt(title_size)
    elif action == "replace_body":
        target = ph(slide, op.get("placeholder_idx", 1))
        if target is None or not target.has_text_frame:
            raise ValueError("target body placeholder is missing")
        target.text = "\n".join(op["points"])
    elif action == "replace_notes":
        if not all(op.get("notes", {}).get(k) for k in ("transition", "emphasis", "backup")):
            raise ValueError("notes require transition, emphasis and backup evidence")
        slide.notes_slide.notes_text_frame.text = "Transition: {}\nEmphasis: {}\nExtra evidence: {}".format(
            op["notes"]["transition"], op["notes"]["emphasis"], op["notes"]["backup"])
    elif action == "replace_page":
        title, body = ph(slide, 0), ph(slide, op.get("placeholder_idx", 1))
        if title is None:
            raise ValueError("target layout has no title placeholder")
        title.text = op.get("claim", "")
        title_size = op.get("title_font_pt", 28)
        if not 24 <= title_size <= 54:
            raise ValueError("title_font_pt must be within the actual template's 24-54pt ladder")
        for para in title.text_frame.paragraphs:
            for run in para.runs:
                run.font.size = Pt(title_size)
        if body is not None:
            body.text = "\n".join(op.get("points", []))
        if op.get("visual"):
            target = ph(slide, op.get("visual_placeholder_idx", 2))
            if target is None:
                raise ValueError("declared chart destination placeholder missing")
            visual = op["visual"]
            if visual.get("kind") != "chart":
                raise ValueError("editor inserts data-bound native charts; cite source figures instead of fake curves")
            add_chart(slide, target, visual)
        if not all(op.get("notes", {}).get(k) for k in ("transition", "emphasis", "backup")):
            raise ValueError("replace_page requires full complementary notes")
        slide.notes_slide.notes_text_frame.text = "Transition: {}\nEmphasis: {}\nExtra evidence: {}".format(
            op["notes"]["transition"], op["notes"]["emphasis"], op["notes"]["backup"])
    elif action == "set_text_style":
        shape = next((s for s in slide.shapes if s.name == op.get("shape_name")), None)
        if shape is None or not shape.has_text_frame:
            raise ValueError("named editable text shape missing")
        for para in shape.text_frame.paragraphs:
            for run in para.runs:
                if "font_size_pt" in op:
                    if not 14 <= op["font_size_pt"] <= 54:
                        raise ValueError("font size outside declared readable range")
                    run.font.size = Pt(op["font_size_pt"])
                if "font_name" in op:
                    run.font.name = op["font_name"]
    else:
        raise ValueError(f"unsupported scoped action: {action}")
    if not op.get("reason"):
        raise ValueError("every edit needs an auditable reason")
    after = slide_state(slide)
    return {"slide": number, "operation_id": op.get("operation_id"), "reason": op["reason"],
            "before": before, "after": after,
            "changed": fingerprint(before) != fingerprint(after)}


def result_from_contract(spec, final, render, changed):
    out = dict(spec["output_contract"])
    if "case_id" not in out:
        raise ValueError("materialized output_contract must include case_id")
    out["case_id"] = spec["case_id"]
    values = {"status": "completed" if render.get("status", "").startswith("automated-render-passed") else "partial",
              "artifacts": [str(final)], "primary_result": str(final), "output": str(final),
              "sources": [spec["source_material"]], "uncertainty": spec.get("uncertainty", []) + render.get("limitations", []),
              "diff": "slide_diff.json", "render": render, "validation": render,
              "changes": changed}
    for key, value in values.items():
        if key not in out:
            continue
        if out[key] is not None and not isinstance(value, type(out[key])):
            raise TypeError(f"contract field {key} changed type")
        out[key] = value
    return out


def apply(spec_path, outdir):
    spec_path = spec_path.resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    root = spec_path.parent
    required = ("case_id", "mode", "source_material", "starting_deck", "original_sha256", "audience", "time_minutes", "slide_count", "edits", "page_evidence", "output_contract")
    if any(k not in spec for k in required):
        raise ValueError("case-derived edit specification is incomplete")
    source, start = (root / spec["source_material"]).resolve(), (root / spec["starting_deck"]).resolve()
    if not source.is_file() or not start.is_file() or digest(start) != spec["original_sha256"]:
        raise ValueError("actual material/deck missing or starting-deck hash changed")
    pdf_evidence = PDFEvidence(source)
    outdir.mkdir(parents=True, exist_ok=True)
    backup = outdir / "previous_verified.pptx"
    candidate = outdir / "candidate.pptx"
    final = outdir / "edited_deck.pptx"
    shutil.copyfile(start, backup)
    shutil.copyfile(start, candidate)
    initial = deck_state(start)
    if spec["mode"] == "existing-deck-edit" and initial["slides"] != spec["slide_count"]:
        raise ValueError("starting deck does not equal fixed case slide_count")
    if spec["mode"] == "from-template" and initial["slides"] != 0:
        raise ValueError("from-template requires the actual template with zero slides; do not delete an unapproved starting deck")
    if spec["mode"] not in ("from-template", "existing-deck-edit"):
        raise ValueError("mode must be from-template or existing-deck-edit")
    if len(spec["page_evidence"]) != spec["slide_count"]:
        raise ValueError("final page_evidence must include every fixed page, including unchanged pages")
    for index, page in enumerate(spec["page_evidence"], 1):
        sources = page.get("sources", [])
        if not sources:
            raise ValueError(f"final page {index}: at least one actual PDF source required")
        for source_index, src in enumerate(sources, 1):
            pdf_evidence.validate_source(src, f"final page {index} source {source_index}")
    for op_index, op in enumerate(spec["edits"], 1):
        visual = op.get("visual")
        if visual and visual.get("kind") == "chart":
            number = op.get("slide")
            if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= spec["slide_count"]:
                raise ValueError(f"edit {op_index}: chart slide is outside the fixed deck")
            pdf_evidence.validate_chart(visual, spec["page_evidence"][number-1]["sources"], f"edit {op_index} slide {number}")
    prs = Presentation(str(candidate))
    changes = []
    for op in spec["edits"]:
        if op.get("action") == "append_page":
            if spec["mode"] != "from-template" or op.get("slide") != len(prs.slides) + 1:
                raise ValueError("append_page is only sequential generation in from-template mode")
            matches = [layout for layout in prs.slide_layouts if layout.name == op.get("layout")]
            if not matches:
                raise ValueError("declared layout does not exist in the actual template")
            before = slide_state(prs.slides.add_slide(matches[0]))
            op = dict(op, action="replace_page", expected_slide_fingerprint=fingerprint(before))
        else:
            if spec["mode"] != "existing-deck-edit":
                raise ValueError("from-template accepts only append_page operations")
            before = slide_state(prs.slides[op["slide"]-1]) if isinstance(op.get("slide"), int) and 1 <= op["slide"] <= len(prs.slides) else {}
        changes.append(apply_edit(prs, before, op))
    if sum(p.get("seconds", 0) for p in spec["page_evidence"]) > spec["time_minutes"] * 60:
        raise ValueError("final page timings exceed case time_minutes")
    temp = outdir / "candidate.tmp.pptx"
    prs.save(str(temp))
    temp.replace(candidate)
    final_state = deck_state(candidate)
    untouched = [] if spec["mode"] == "from-template" else sorted(set(range(1, initial["slides"]+1)) - {op["slide"] for op in spec["edits"]})
    for number in untouched:
        old = initial["pages"][number-1]["fingerprint"]
        new = final_state["pages"][number-1]["fingerprint"]
        if old != new:
            raise AssertionError(f"unauthorized change on slide {number}; candidate rejected")
    if final_state["master_theme_sha256"] != initial["master_theme_sha256"]:
        raise AssertionError("master/theme changed; candidate rejected")
    if final_state["slides"] != spec["slide_count"]:
        raise AssertionError("page count changed")
    candidate.replace(final)
    reopened = Presentation(str(final))
    visual_pages = {op["slide"]: op["visual"] for op in spec["edits"] if op.get("visual", {}).get("kind") == "chart"}
    for number, slide in enumerate(reopened.slides, 1):
        if not any(s.has_text_frame or s.has_chart or s.shape_type == MSO_SHAPE_TYPE.PICTURE for s in slide.shapes):
            raise AssertionError(f"slide {number}: no editable content")
        if number in visual_pages:
            charts = [shape.chart for shape in slide.shapes if shape.has_chart]
            if len(charts) != 1:
                raise AssertionError(f"slide {number}: expected one source-bound native chart")
            assert_saved_chart(charts[0], visual_pages[number], f"slide {number}")
    render = {"status": "not-run", "limitations": ["final PPTX needs real office rendering and page-by-page visual inspection"]}
    save_json(outdir / "slide_diff.json", {"case_id": spec["case_id"], "before_sha256": spec["original_sha256"],
                                          "after_sha256": digest(final), "unchanged_pages_verified": untouched,
                                          "master_theme_unchanged": True, "changes": changes})
    save_json(outdir / "evidence_map.json", {"pages": spec["page_evidence"]})
    save_json(outdir / "edit_report.json", {"status": "edit-structure-passed-render-pending", "slide_count": final_state["slides"],
                                           "python": platform.python_version(), "python-pptx": pptx.__version__,
                                           "backup": str(backup), "rollback_verified_by": "edit_deck.py rollback with current hash guard"})
    save_json(outdir / "result.json", result_from_contract(spec, final, render, changes))
    print(json.dumps({"pptx": str(final), "slides": final_state["slides"], "edited": [c["slide"] for c in changes], "backup": str(backup)}, ensure_ascii=False))


def inspect(path, out):
    state = deck_state(path)
    state["file_sha256"] = digest(path)
    if out:
        save_json(out, state)
    print(json.dumps(state, ensure_ascii=False))


def rollback(current, backup, expected, outdir):
    if digest(current) != expected or not backup.is_file():
        raise ValueError("rollback blocked: current hash mismatches or backup missing")
    baseline = deck_state(backup)
    outdir.mkdir(parents=True, exist_ok=True)
    temp = outdir / "rollback.tmp.pptx"
    shutil.copyfile(backup, temp)
    if deck_state(temp) != baseline:
        raise AssertionError("rollback candidate differs from backup")
    destination = outdir / "rolled_back.pptx"
    temp.replace(destination)
    save_json(outdir / "rollback_report.json", {"status": "rolled-back", "destination": str(destination),
                                               "source_backup": str(backup), "sha256": digest(destination)})
    print(json.dumps({"status": "rolled-back", "pptx": str(destination)}))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    a = sub.add_parser("apply")
    a.add_argument("--spec", required=True)
    a.add_argument("--outdir", required=True)
    i = sub.add_parser("inspect")
    i.add_argument("--deck", required=True)
    i.add_argument("--out")
    r = sub.add_parser("rollback")
    r.add_argument("--current", required=True)
    r.add_argument("--backup", required=True)
    r.add_argument("--expected-current-sha256", required=True)
    r.add_argument("--outdir", required=True)
    args = ap.parse_args()
    try:
        if args.command == "apply":
            apply(Path(args.spec), Path(args.outdir))
        elif args.command == "inspect":
            inspect(Path(args.deck).resolve(), Path(args.out).resolve() if args.out else None)
        else:
            rollback(Path(args.current).resolve(), Path(args.backup).resolve(), args.expected_current_sha256, Path(args.outdir).resolve())
    except (OSError, ValueError, TypeError, KeyError, AssertionError) as exc:
        print(f"edit-blocked: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
