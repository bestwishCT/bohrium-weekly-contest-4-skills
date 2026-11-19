#!/usr/bin/env python3
"""Probe actual PPTX masters/layouts, batch-adapt every page and audit style drift."""
import argparse
import hashlib
import json
import math
import platform
import re
import shutil
import subprocess
import sys
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET

try:
    import pptx
    from pptx import Presentation
    from pptx.chart.data import ChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.enum.text import MSO_AUTO_SIZE
    from pptx.util import Pt
except ImportError:
    raise SystemExit("dependency-blocked: install python-pptx>=1.0 from resources.yaml")

from pdf_evidence import validate_chart, validate_citation

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ph(slide, idx):
    return next((s for s in slide.placeholders if s.placeholder_format.idx == idx), None)


def template_internals(path):
    import posixpath
    digest = hashlib.sha256()
    masters, themes, slide_theme_parts = [], [], set()
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        for rel_name in names:
            if rel_name.startswith("ppt/slideMasters/_rels/") and rel_name.endswith(".rels"):
                root = ET.fromstring(zf.read(rel_name))
                for relation in root:
                    if relation.attrib.get("Type", "").endswith("/theme"):
                        target = posixpath.normpath(posixpath.join("ppt/slideMasters", relation.attrib["Target"]))
                        if target in names:
                            slide_theme_parts.add(target)
        for name in sorted(names):
            structural = name.startswith(("ppt/slideMasters/", "ppt/slideLayouts/")) and name.endswith((".xml", ".rels"))
            if structural or name in slide_theme_parts:
                digest.update(name.encode())
                digest.update(zf.read(name))
            if name.startswith("ppt/slideMasters/slideMaster") and name.endswith(".xml"):
                masters.append(name)
            if name in slide_theme_parts:
                root = ET.fromstring(zf.read(name))
                fonts = sorted({el.attrib.get("typeface") for el in root.iter() if el.tag in (A+"latin", A+"ea") and el.attrib.get("typeface")})
                palette = []
                for color in root.iter():
                    if color.tag.endswith("}srgbClr") and color.attrib.get("val"):
                        palette.append(color.attrib["val"])
                    elif color.tag.endswith("}sysClr") and color.attrib.get("lastClr"):
                        palette.append(color.attrib["lastClr"])
                themes.append({"part": name, "typefaces": fonts, "theme_colors": sorted(set(palette)),
                               "color_validation": "inherit target only; contrast/grayscale visual review required"})
        return {"master_theme_sha256": digest.hexdigest(), "master_parts": masters, "slide_theme_parts": sorted(slide_theme_parts),
                "themes": themes, "scope": "slide masters/layouts and their linked themes; native notesMaster excluded"}


def slide_master_hash(path):
    return template_internals(path)["master_theme_sha256"]


def font_environment():
    if shutil.which("fc-list"):
        proc = subprocess.run(["fc-list", "--format=%{family}\t%{style}\t%{file}\n"], text=True, capture_output=True)
        rows = []
        for line in proc.stdout.splitlines():
            family, style, path = (line.split("\t") + ["", ""])[:3]
            rows.append({"family": family, "style": style, "path": path, "version": "not-exposed-by-fc-list"})
        return {"probe": "fc-list", "fonts": rows}
    if sys.platform == "darwin" and shutil.which("system_profiler"):
        proc = subprocess.run(["system_profiler", "SPFontsDataType", "-json"], text=True, capture_output=True, timeout=60)
        data = json.loads(proc.stdout).get("SPFontsDataType", [])
        rows = []
        for entry in data:
            for face in entry.get("typefaces", []):
                rows.append({"family": face.get("family"), "fullname": face.get("fullname"), "path": entry.get("path"),
                             "version": face.get("unique", face.get("version", "unknown")), "enabled": face.get("enabled")})
        return {"probe": "macOS system_profiler SPFontsDataType", "fonts": rows}
    return {"probe": None, "fonts": [], "limit": "no supported actual font inventory tool"}


def font_report(required):
    inventory = font_environment()
    available, matches = set(), {}
    for row in inventory["fonts"]:
        if row.get("enabled", "yes") not in ("no", "false"):
            available.update((row.get("family") or "").split(","))
    for family in required:
        found = family in available
        matches[family] = {"available": found, "versions": sorted({r.get("version") for r in inventory["fonts"] if family in (r.get("family") or "").split(",")}),
                           "fallback_observed": None if found else "unresolved-before-render"}
    return {"required": required, "matches": matches, "probe": inventory["probe"], "font_count": len(inventory["fonts"]),
            "embedding_claimed": False}, matches


def text_units(text):
    return sum(1 if ord(ch) > 255 else 0.56 for ch in text)


def audit_presentation(path, pages, style):
    prs = Presentation(str(path))
    rows, issues = [], []
    for number, slide in enumerate(prs.slides, 1):
        planned = pages[number-1]
        row = {"slide": number, "layout": slide.slide_layout.name, "planned_layout": planned["layout"], "shapes": [], "overflow_estimates": [], "issues": []}
        if slide.slide_layout.name != planned["layout"]:
            issues.append(f"slide {number}: wrong actual layout inheritance")
        for shape in slide.shapes:
            entry = {"name": shape.name, "box": [shape.left, shape.top, shape.width, shape.height],
                     "editable_text": shape.has_text_frame, "native_chart": shape.has_chart,
                     "placeholder_idx": shape.placeholder_format.idx if shape.is_placeholder else None}
            if shape.left < 0 or shape.top < 0 or shape.left + shape.width > prs.slide_width or shape.top + shape.height > prs.slide_height:
                issues.append(f"slide {number}: {shape.name} crosses slide boundary")
            if shape.shape_type == 13:
                entry["crop"] = [shape.crop_left, shape.crop_right, shape.crop_top, shape.crop_bottom]
                entry["source_size"] = list(shape.image.size)
                entry["display_ratio"] = shape.width / shape.height
                entry["source_ratio"] = shape.image.size[0] / shape.image.size[1]
                if any(abs(v) > 0.001 for v in entry["crop"]):
                    issues.append(f"slide {number}: image crop requires explicit authorization")
                if abs(entry["display_ratio"] / entry["source_ratio"] - 1) > 0.01:
                    issues.append(f"slide {number}: picture aspect ratio distorted")
            if shape.has_text_frame and shape.text.strip():
                frame = shape.text_frame
                width = max(1, shape.width/12700 - 20)
                height = max(1, shape.height/12700 - 12)
                used, sizes = 0, []
                for para in frame.paragraphs:
                    points = [r.font.size.pt for r in para.runs if r.font.size]
                    size = max(points, default=24)
                    sizes.extend(points)
                    rows_needed = max(1, math.ceil(text_units(para.text) / max(1, width / (size*0.58))))
                    used += rows_needed * size * 1.25 + 6
                entry["font_sizes_pt"] = sizes
                if any(v < style["min_font_pt"] for v in sizes):
                    issues.append(f"slide {number}: explicit font below case style minimum")
                if used > height:
                    row["overflow_estimates"].append({"shape": shape.name, "estimated_pt": round(used), "available_pt": round(height)})
                    issues.append(f"slide {number}: estimated overflow in {shape.name}")
            row["shapes"].append(entry)
        if not slide.notes_slide.notes_text_frame.text.strip():
            issues.append(f"slide {number}: native speaker notes missing")
        rows.append(row)
    return rows, issues


def fill_chart(slide, visual):
    destination = ph(slide, 2)
    if destination is None:
        raise ValueError("no actual chart placeholder idx=2; do not improvise over footer/master")
    cats, series = visual.get("categories", []), visual.get("series", [])
    if not cats or not series or any(len(s["values"]) != len(cats) for s in series):
        raise ValueError("chart source data are misaligned")
    data = ChartData()
    data.categories = cats
    for item in series:
        data.add_series(item["name"], item["values"])
    box = destination.left, destination.top, destination.width, destination.height
    destination._element.getparent().remove(destination._element)
    chart = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, *box, data).chart
    chart.has_title = bool(visual.get("figure"))
    if chart.has_title:
        chart.chart_title.text_frame.text = visual["figure"]
    chart.has_legend = len(series) > 1
    for axis, key in ((chart.category_axis, "x_title"), (chart.value_axis, "y_title")):
        axis.has_title = bool(visual.get(key))
        if axis.has_title:
            axis.axis_title.text_frame.text = visual[key]
        axis.tick_labels.font.size = Pt(style_safe(12, 11))


def style_safe(value, minimum):
    if value < minimum:
        raise ValueError("requested chart axis font below declared readable min")
    return value


def fill_source_figure(slide, visual, root):
    from PIL import Image
    target = ph(slide, 2)
    if target is None:
        raise ValueError("no actual figure placeholder idx=2; do not cover axis or footer")
    path = (root / visual["path"]).resolve()
    if root.resolve() not in path.parents or not path.is_file():
        raise ValueError("source figure must be an actual extracted file inside the case workspace")
    with Image.open(path) as image:
        ratio = min(target.width / image.width, target.height / image.height)
        width, height = int(image.width * ratio), int(image.height * ratio)
    picture = slide.shapes.add_picture(str(path), target.left + (target.width-width)//2,
                                       target.top + (target.height-height)//2, width=width, height=height)
    picture.name = f"PDF p.{visual['source_pdf_page']} {visual.get('figure', 'source figure')}"
    picture.crop_left = picture.crop_right = picture.crop_top = picture.crop_bottom = 0
    return picture


def adapt(spec_path, outdir, work):
    spec_path = spec_path.resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    root = spec_path.parent
    required = ("case_id", "source_material", "template", "starting_deck", "original_sha256", "mode", "audience", "time_minutes", "slide_count", "pages", "required_fonts", "style_rules", "output_contract")
    if any(k not in spec for k in required):
        raise ValueError("template adaptation spec lacks case-derived fields")
    source, template, start = [(root / spec[k]).resolve() for k in ("source_material", "template", "starting_deck")]
    if not all(p.is_file() for p in (source, template, start)):
        raise FileNotFoundError("actual source, editable template and starting deck are required")
    if sha(start) != spec["original_sha256"]:
        raise ValueError("starting deck changed after template probe")
    original_theme = template_internals(template)
    starting_theme = template_internals(start)
    if original_theme["master_theme_sha256"] != starting_theme["master_theme_sha256"] and spec["mode"] != "migrate-to-template":
        raise ValueError("template and starting deck have different master/theme; explicit migrate-to-template mode is required")
    if len(spec["pages"]) != spec["slide_count"]:
        raise ValueError("page plan must cover every fixed slide")
    style = spec["style_rules"]
    for key in ("title_font_pt", "body_font_pt", "min_font_pt"):
        if key not in style:
            raise ValueError(f"style_rules.{key} required from actual template policy")
    if not 14 <= style["min_font_pt"] <= style["body_font_pt"] <= style["title_font_pt"] <= 54:
        raise ValueError("inconsistent readable type ladder")
    fonts, _ = font_report(spec["required_fonts"])
    missing = [name for name, item in fonts["matches"].items() if not item["available"]]
    outdir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    dump(outdir / "font_report.json", fonts)
    if missing or not fonts["probe"]:
        raise ValueError("required font availability unresolved: " + ", ".join(missing + ([] if fonts["probe"] else ["no font probe"])))
    source_prs = Presentation(str(start))
    if spec["mode"] in ("existing-deck-edit", "migrate-to-template") and len(source_prs.slides) != spec["slide_count"]:
        raise ValueError("actual starting deck has wrong fixed page count")
    if spec["mode"] == "migrate-to-template":
        for number, page in enumerate(spec["pages"], 1):
            origin = page.get("origin", {})
            if origin.get("source_slide") != number or not origin.get("source_excerpt"):
                raise ValueError(f"slide {number}: cross-template migration needs actual source slide and verbatim excerpt")
            start_slide = source_prs.slides[number-1]
            actual_text = "\n".join(s.text for s in start_slide.shapes if s.has_text_frame) + "\n" + start_slide.notes_slide.notes_text_frame.text
            if origin["source_excerpt"] not in actual_text:
                raise ValueError(f"slide {number}: migration excerpt is absent from actual starting deck")
        prs = Presentation(str(template))
        if len(prs.slides) != 0:
            raise ValueError("migrate-to-template requires zero-slide target template")
    elif spec["mode"] == "from-template":
        if start != template or len(source_prs.slides) != 0:
            raise ValueError("from-template requires actual zero-slide template as starting_deck")
        prs = source_prs
    elif spec["mode"] == "existing-deck-edit":
        prs = source_prs
    else:
        raise ValueError("mode must be existing-deck-edit, from-template or migrate-to-template")
    shutil.copyfile(start, work / "starting_baseline.pptx")
    template_probe = {"template_sha256": sha(template), "starting_sha256": sha(start), "mode": spec["mode"],
                      "starting_internals": starting_theme, "slide_dimensions": [prs.slide_width, prs.slide_height],
                      **template_internals(template), "layouts": [{"name": l.name, "part": l.part.partname, "master": l.slide_master.part.partname,
                      "placeholders": [{"idx": p.placeholder_format.idx, "type": str(p.placeholder_format.type), "box": [p.left, p.top, p.width, p.height]} for p in l.placeholders]} for l in prs.slide_layouts]}
    dump(outdir / "template_probe.json", template_probe)
    for number, page in enumerate(spec["pages"], 1):
        for src in page.get("sources", []):
            validate_citation(source, src, f"slide {number}")
        notes = page.get("notes", {})
        if not all(notes.get(k) for k in ("transition", "emphasis", "backup")):
            raise ValueError(f"slide {number}: complementary notes required")
        slide = prs.slides[number-1] if spec["mode"] == "existing-deck-edit" else prs.slides.add_slide(next(l for l in prs.slide_layouts if l.name == page["layout"]))
        if slide.slide_layout.name != page["layout"]:
            raise ValueError(f"slide {number}: actual layout not the content-mapped layout")
        title, body = ph(slide, 0), ph(slide, 1)
        if title is None:
            raise ValueError(f"slide {number}: title slot unavailable")
        title.text = page["claim"]
        for para in title.text_frame.paragraphs:
            for run in para.runs:
                run.font.size = Pt(style["title_font_pt"])
        if body is not None:
            body.text = "\n".join(page.get("points", []))
            body.text_frame.word_wrap = True
            body.text_frame.auto_size = MSO_AUTO_SIZE.NONE
            for para in body.text_frame.paragraphs:
                for run in para.runs:
                    run.font.size = Pt(style["body_font_pt"])
        if page.get("visual"):
            if page["visual"].get("kind") == "chart":
                validate_chart(source, page["visual"], page.get("sources", []), f"slide {number}")
                fill_chart(slide, page["visual"])
            elif page["visual"].get("kind") == "figure" and isinstance(page["visual"].get("source_pdf_page"), int):
                fill_source_figure(slide, page["visual"], root)
            else:
                raise ValueError("visual must be source-bound native chart or actual extracted figure with PDF page")
        slide.notes_slide.notes_text_frame.text = "Transition: {}\nEmphasis: {}\nExtra evidence: {}".format(notes["transition"], notes["emphasis"], notes["backup"])
    if sum(p.get("seconds", 0) for p in spec["pages"]) > spec["time_minutes"] * 60:
        raise ValueError("organized deck exceeds fixed audience time")
    candidate = work / "adapted.candidate.pptx"
    final = outdir / "adapted_deck.pptx"
    prs.save(str(candidate))
    reopened = Presentation(str(candidate))
    if len(reopened.slides) != spec["slide_count"]:
        raise AssertionError("PPTX page count changed")
    if template_internals(candidate)["master_theme_sha256"] != original_theme["master_theme_sha256"]:
        raise AssertionError("saved deck does not inherit the designated target master/theme")
    report, issues = audit_presentation(candidate, spec["pages"], style)
    dump(outdir / "page_deviations.json", {"slides": report, "issues": issues, "overflow_method": "geometry/font estimate; visual inspection still required"})
    if issues:
        raise ValueError("batch adaptation blocked; see page_deviations.json: " + issues[0])
    candidate.replace(final)
    render = {"status": "not-run", "limitations": ["final PPTX actual render and per-page visual/font/crop inspection pending"]}
    dump(outdir / "adaptation_record.json", {"case_id": spec["case_id"], "source_sha256": sha(source), "template_sha256": sha(template),
         "starting_sha256": sha(start), "final_sha256": sha(final), "slides": len(reopened.slides), "python": platform.python_version(),
         "python-pptx": pptx.__version__, "fonts": fonts, "all_pages_processed": report, "render": render})
    result = dict(spec["output_contract"])
    if "case_id" not in result:
        raise ValueError("materialized output_contract must include case_id")
    result["case_id"] = spec["case_id"]
    values = {"status": "partial", "artifacts": [str(final)], "primary_result": str(final), "output": str(final),
              "sources": [spec["source_material"]], "uncertainty": spec.get("uncertainty", []) + render["limitations"],
              "render": render, "validation": render, "template_report": "template_probe.json", "deviations": report}
    for key, value in values.items():
        if key in result:
            if result[key] is not None and not isinstance(value, type(result[key])):
                raise TypeError(f"contract field {key} changed type")
            result[key] = value
    dump(outdir / "result.json", result)
    print(json.dumps({"pptx": str(final), "slides": len(reopened.slides), "pages_checked": len(report), "status": "structure-passed-render-pending"}, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--work", required=True)
    args = ap.parse_args()
    try:
        adapt(Path(args.spec), Path(args.outdir), Path(args.work))
    except (OSError, ValueError, TypeError, KeyError, AssertionError, subprocess.SubprocessError) as exc:
        print(f"adapt-blocked: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
