#!/usr/bin/env python3
"""Mechanical academic-format and OOXML preservation auditor.

This script does not render Office documents and does not claim visual fidelity.
It checks real files, package/XML structure, selected inherited values and object
retention. Unresolved styles/fonts are pending failures, never assumed defaults.
"""
import hashlib
import json
import re
import struct
import sys
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"w": W, "m": M, "r": R, "a": A}
PATH_KEY = re.compile(
    r"(?:^|_)(?:deliverables?|artifacts?|source_files?|files?|pdf|previews?|"
    r"png|editable|documents?|outputs?|source_maps?)(?:$|_)", re.I
)
FORMAT_RELATIONSHIPS = {"styles", "settings", "theme", "fontTable", "numbering", "webSettings"}


def q(ns, name):
    return "{%s}%s" % (ns, name)


class Report:
    def __init__(self):
        self.checks = []

    def need(self, ok, code, message, pending=False, **details):
        check = {"code": code, "ok": bool(ok), "message": message}
        if pending:
            check["pending"] = True
        check.update(details)
        self.checks.append(check)

    @property
    def failures(self):
        return [c for c in self.checks if not c["ok"]]


def finish(data, path, report, real_mode=False):
    pending = any(c.get("pending") for c in report.failures)
    status = "pass" if not report.failures else ("partial" if real_mode or pending else "fail")
    out = {
        "artifact": str(path),
        "case_id": data.get("case_id"),
        "status": status,
        "pending": pending,
        "checks": report.checks,
        "note": "仅机械核验OOXML/PDF/PNG结构、继承值与对象保留；未执行Office渲染或官方评分。",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if not report.failures else 1


def load():
    if len(sys.argv) != 2:
        raise ValueError("usage: audit.py INPUT.json")
    p = Path(sys.argv[1]).resolve()
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("top-level JSON must be object")
    return data, p


def within(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_path(value, base):
    path = Path(str(value))
    return (path if path.is_absolute() else base / path).resolve()


def readable_file(path):
    if not path.is_file():
        return False
    try:
        with path.open("rb") as stream:
            stream.read(1)
        return True
    except OSError:
        return False


def pdf_info(path):
    try:
        raw = path.read_bytes()
    except OSError:
        return False, 0
    signature = raw.startswith(b"%PDF-") and b"%%EOF" in raw[-2048:]
    pages = len(re.findall(rb"/Type\s*/Page(?![sA-Za-z])", raw))
    return signature, pages


def png_ok(path):
    try:
        raw = path.read_bytes()
    except OSError:
        return False
    return raw.startswith(b"\x89PNG\r\n\x1a\n") and b"IHDR" in raw[:64] and b"IEND" in raw[-32:]


def sfnt_declares_family(path, family):
    """Read the SFNT name table; a signature alone is not font availability."""
    try:
        raw = path.read_bytes()
        if raw[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"):
            return False
        table_count = struct.unpack(">H", raw[4:6])[0]
        name_table = None
        for index in range(table_count):
            record = raw[12 + index * 16:28 + index * 16]
            if len(record) == 16 and record[:4] == b"name":
                _, offset, length = struct.unpack(">III", record[4:])
                name_table = raw[offset:offset + length]
                break
        if not name_table or len(name_table) < 6:
            return False
        _, count, strings = struct.unpack(">HHH", name_table[:6])
        wanted = family.strip().casefold()
        for index in range(count):
            record = name_table[6 + index * 12:18 + index * 12]
            if len(record) != 12:
                return False
            platform_id, encoding_id, _, name_id, length, offset = struct.unpack(">HHHHHH", record)
            if name_id not in (1, 16):
                continue
            value = name_table[strings + offset:strings + offset + length]
            encoding = "utf-16-be" if platform_id in (0, 3) else "mac_roman"
            try:
                decoded = value.decode(encoding).strip().casefold()
            except (UnicodeDecodeError, LookupError):
                continue
            if decoded == wanted:
                return True
    except (OSError, struct.error, ValueError):
        return False
    return False


def unit_index(units, label, report):
    result = {}
    if not isinstance(units, list):
        report.need(False, label + "_present", label + "必须为数组")
        return result
    ids = [u.get("id") for u in units if isinstance(u, dict) and isinstance(u.get("id"), str)]
    duplicates = sorted(k for k, count in Counter(ids).items() if k is not None and count > 1)
    report.need(not duplicates, "duplicate_" + label + "_ids", "源/转换单元ID不得重复", duplicates=duplicates)
    for unit in units:
        if not isinstance(unit, dict) or not isinstance(unit.get("id"), str) or not unit["id"]:
            report.need(False, label + "_id_valid", "每个内容单元必须有非空字符串ID")
            continue
        if unit["id"] not in result:
            result[unit["id"]] = unit
    return result


def audit_declared_units(data, report):
    src = unit_index(data.get("source_units"), "source", report)
    out = unit_index(data.get("converted_units"), "converted", report)
    report.need(set(src) == set(out), "content_unit_ids", "内容单元ID无遗漏")
    for key, value in src.items():
        if key in out:
            source_text = value.get("text")
            converted_text = out[key].get("text")
            if not isinstance(source_text, str) or not isinstance(converted_text, str):
                report.need(False, "content_text:" + key, "内容单元text必须为字符串")
            else:
                digest = lambda x: hashlib.sha256(x.encode()).hexdigest()
                report.need(digest(source_text) == digest(converted_text), "content_hash:" + key, "转换前后文字不变")
    return src, out


def audit_legacy_specs(data, report):
    specs = data.get("specs")
    applied = data.get("applied")
    if not isinstance(specs, list) or not isinstance(applied, dict):
        report.need(False, "specs_present", "旧映射模式需要specs数组和applied对象")
    else:
        for spec in specs:
            if not isinstance(spec, dict) or "key" not in spec:
                report.need(False, "spec_valid", "规格必须为含key的对象")
                continue
            tag = str(spec["key"])
            report.need(bool(spec.get("source")), "spec_source:" + tag, "规格有模板来源")
            report.need(applied.get(tag) == spec.get("value"), "spec_applied:" + tag, "转换应用值与规格一致")
    font = data.get("font")
    if not isinstance(font, dict):
        report.need(False, "required_font", "revision3使用Liberation Serif")
        report.need(False, "font_revision", "字体需求为revision3")
    else:
        report.need(font.get("family") == "Liberation Serif", "required_font", "revision3使用Liberation Serif")
        report.need(font.get("revision") == 3, "font_revision", "字体需求为revision3")


class Package:
    def __init__(self, path, label, report):
        self.path = path
        self.label = label
        self.report = report
        self.names = []
        self.xml = {}
        self.raw = {}
        self.valid = False
        try:
            with zipfile.ZipFile(str(path)) as archive:
                bad = archive.testzip()
                if bad:
                    report.need(False, "ooxml_zip_crc:" + label, "Office ZIP CRC校验失败", member=bad)
                    return
                self.names = archive.namelist()
                if len(self.names) != len(set(self.names)):
                    report.need(False, "ooxml_duplicate_member:" + label, "Office ZIP不得含重复成员")
                for name in self.names:
                    self.raw[name] = archive.read(name)
                required = {"[Content_Types].xml", "_rels/.rels"}
                report.need(required <= set(self.names), "ooxml_package:" + label, "Office ZIP包含内容类型与根关系")
                for name, raw in self.raw.items():
                    if name.endswith(".xml") or name.endswith(".rels"):
                        try:
                            self.xml[name] = ET.fromstring(raw)
                        except ET.ParseError as exc:
                            report.need(False, "ooxml_xml:" + label, "XML可解析: %s" % exc, member=name)
                if path.suffix.lower() == ".docx":
                    report.need("word/document.xml" in self.xml, "ooxml_main_document:" + label, "DOCX含可解析word/document.xml")
                self.valid = required <= set(self.names) and "word/document.xml" in self.xml
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            report.need(False, "ooxml_readable:" + label, "Office文件为可读ZIP/XML包: %s" % exc)

    def story_text(self):
        stories = []
        pattern = re.compile(r"word/(?:document|header\d+|footer\d+|footnotes|endnotes|comments)\.xml$")
        for name in sorted(n for n in self.xml if pattern.match(n)):
            root = self.xml[name]
            paragraphs = []
            for paragraph in root.iter(q(W, "p")):
                pieces = []
                for node in paragraph.iter():
                    if node.tag in (q(W, "t"), q(M, "t")):
                        pieces.append(node.text or "")
                    elif node.tag == q(W, "tab"):
                        pieces.append("\t")
                    elif node.tag in (q(W, "br"), q(W, "cr")):
                        pieces.append("\n")
                paragraphs.append("".join(pieces))
            stories.append((name, paragraphs))
        return stories

    def objects(self):
        document = self.xml.get("word/document.xml")
        maths = []
        fields = []
        if document is not None:
            for node in document.iter(q(M, "oMath")):
                maths.append("".join(node.itertext()))
            for node in document.iter(q(W, "instrText")):
                fields.append(re.sub(r"\s+", " ", node.text or "").strip())
            for node in document.iter(q(W, "fldSimple")):
                fields.append(re.sub(r"\s+", " ", node.get(q(W, "instr"), "")).strip())
        comments = []
        comment_root = self.xml.get("word/comments.xml")
        if comment_root is not None:
            for node in comment_root.iter(q(W, "comment")):
                comments.append((node.get(q(W, "id"), ""), "".join(node.itertext())))
        media = []
        for name, raw in self.raw.items():
            if name.startswith(("word/media/", "word/embeddings/", "word/activeX/", "word/charts/")) and not name.endswith("/"):
                media.append((name.rsplit("/", 1)[-1].split(".")[-1].lower(), hashlib.sha256(raw).hexdigest()))
        relationships = []
        for name, root in self.xml.items():
            if not name.endswith(".rels"):
                continue
            for rel in root:
                kind = rel.get("Type", "").rsplit("/", 1)[-1]
                if kind not in FORMAT_RELATIONSHIPS:
                    relationships.append((kind, rel.get("Target", ""), rel.get("TargetMode", "Internal")))
        return {
            "math": Counter(maths),
            "comments": Counter(comments),
            "fields": Counter(fields),
            "media": Counter(media),
            "relationships": Counter(relationships),
        }


class StyleModel:
    def __init__(self, package, report):
        self.package = package
        self.report = report
        self.styles = {}
        self.default_rpr = None
        self.default_paragraph = None
        self.theme = {"major": {}, "minor": {}}
        root = package.xml.get("word/styles.xml")
        if root is not None:
            defaults = root.find("w:docDefaults/w:rPrDefault/w:rPr", NS)
            self.default_rpr = defaults
            for style in root.findall("w:style", NS):
                style_id = style.get(q(W, "styleId"), "")
                based = style.find("w:basedOn", NS)
                self.styles[style_id] = {
                    "type": style.get(q(W, "type")),
                    "default": style.get(q(W, "default")) in ("1", "true", "on"),
                    "basedOn": based.get(q(W, "val")) if based is not None else None,
                    "rPr": style.find("w:rPr", NS),
                    "pPr": style.find("w:pPr", NS),
                }
                if style.get(q(W, "type")) == "paragraph" and style.get(q(W, "default")) in ("1", "true", "on"):
                    self.default_paragraph = style_id
        for name, theme in package.xml.items():
            if not name.startswith("word/theme/"):
                continue
            for kind in ("major", "minor"):
                scheme = theme.find(".//a:themeElements/a:fontScheme/a:%sFont" % kind, NS)
                if scheme is not None:
                    latin = scheme.find("a:latin", NS)
                    if latin is not None:
                        self.theme[kind]["latin"] = latin.get("typeface")
                    for font in scheme.findall("a:font", NS):
                        self.theme[kind][font.get("script", "")] = font.get("typeface")

    def chain(self, style_id):
        self.chain_error = None
        seen = set()
        while style_id:
            if style_id in seen:
                self.chain_error = "basedOn cycle at %s" % style_id
                return
            if style_id not in self.styles:
                self.chain_error = "missing basedOn style %s" % style_id
                return
            seen.add(style_id)
            yield style_id, self.styles[style_id]
            style_id = self.styles[style_id]["basedOn"]

    def _value(self, rpr, prop):
        if rpr is None:
            return None
        if prop == "font_size_pt":
            node = rpr.find("w:sz", NS)
            if node is None:
                return None
            try:
                return float(node.get(q(W, "val"))) / 2.0
            except (TypeError, ValueError):
                return None
        if prop == "font_family":
            node = rpr.find("w:rFonts", NS)
            if node is None:
                return None
            direct = node.get(q(W, "ascii")) or node.get(q(W, "hAnsi")) or node.get(q(W, "eastAsia"))
            if direct:
                return direct
            theme_ref = node.get(q(W, "asciiTheme")) or node.get(q(W, "hAnsiTheme")) or node.get(q(W, "eastAsiaTheme"))
            if theme_ref:
                if "EastAsia" in theme_ref:
                    return None  # script/language specific; do not substitute Latin theme text
                kind = "major" if theme_ref.startswith("major") else "minor"
                return self.theme[kind].get("latin")
        return None

    def style_value(self, style_id, prop):
        for _, style in self.chain(style_id):
            value = self._value(style["rPr"], prop)
            if value is not None:
                return value
        if self.chain_error:
            return None
        return self._value(self.default_rpr, prop)

    def effective_run_value(self, run, paragraph_style, prop):
        value = self._value(run.find("w:rPr", NS), prop)
        if value is not None:
            return value
        char_style = run.find("w:rPr/w:rStyle", NS)
        if char_style is not None:
            value = self.style_value(char_style.get(q(W, "val")), prop)
            if value is not None:
                return value
        return self.style_value(paragraph_style or self.default_paragraph, prop)

    def paragraph_style(self, paragraph):
        node = paragraph.find("w:pPr/w:pStyle", NS)
        return node.get(q(W, "val")) if node is not None else self.default_paragraph

    def paragraph_num(self, paragraph):
        direct = paragraph.find("w:pPr/w:numPr", NS)
        if direct is not None:
            return direct
        style_id = self.paragraph_style(paragraph)
        for _, style in self.chain(style_id):
            num = style["pPr"].find("w:numPr", NS) if style["pPr"] is not None else None
            if num is not None:
                return num
        return None

    def runs(self):
        document = self.package.xml.get("word/document.xml")
        if document is None:
            return
        for paragraph in document.iter(q(W, "p")):
            style_id = self.paragraph_style(paragraph)
            for run in paragraph.findall("w:r", NS):
                yield paragraph, run, style_id


def audit_styles(converted, case, data, base, report):
    model = StyleModel(converted, report)
    requirements = data.get("style_requirements", case.get("style_requirements", {}))
    if not isinstance(requirements, dict):
        report.need(False, "style_requirements_valid", "style_requirements必须为对象")
        requirements = {}
    required_families = set()
    for style_id, expected in requirements.items():
        if not isinstance(expected, dict):
            report.need(False, "style_requirement_valid:" + str(style_id), "样式要求必须为对象")
            continue
        for prop, wanted in expected.items():
            if prop not in ("font_size_pt", "font_family"):
                continue
            for _ in model.chain(style_id):
                pass
            if model.chain_error:
                report.need(False, "style_unresolved:%s:%s" % (style_id, prop), "basedOn链不完整或循环；不猜默认值", pending=True, style=style_id, reason=model.chain_error)
                continue
            actual = model.style_value(style_id, prop)
            if actual is None:
                report.need(False, "style_unresolved:%s:%s" % (style_id, prop), "无法从direct/basedOn/docDefaults/theme可靠解析；不猜默认值", pending=True, style=style_id)
                continue
            report.need(actual == wanted, "style_value:%s:%s" % (style_id, prop), "实际继承样式值符合模板", expected=wanted, actual=actual)
            if prop == "font_family":
                required_families.add(str(wanted))

    # Check actual runs too: a correct style definition must not be bypassed by
    # direct/character formatting in same-text converted content.
    for paragraph, run, style_id in model.runs():
        text = "".join((n.text or "") for n in run.iter() if n.tag in (q(W, "t"), q(M, "t")))
        expected = requirements.get(style_id, {}) if isinstance(style_id, str) else {}
        if not isinstance(expected, dict) or not text:
            continue
        for prop in ("font_size_pt", "font_family"):
            if prop not in expected:
                continue
            actual = model.effective_run_value(run, style_id, prop)
            if actual is None:
                report.need(False, "run_style_unresolved:%s" % prop, "实际文本运行继承值无法可靠解析", pending=True, text=text[:80])
            else:
                report.need(actual == expected[prop], "run_style_value:%s" % prop, "实际文本运行未覆盖错误继承值", expected=expected[prop], actual=actual, text=text[:80])

    required_fonts = case.get("required_fonts", data.get("required_fonts", []))
    if not isinstance(required_fonts, list):
        report.need(False, "required_fonts_valid", "required_fonts必须为数组", pending=True)
        required_fonts = []
    for family in required_fonts:
        required_families.add(str(family))
    resources = data.get("font_resources", case.get("font_resources", {}))
    if not isinstance(resources, dict):
        resources = {}
    for family in sorted(required_families):
        ref = resources.get(family)
        if not ref:
            report.need(False, "font_availability:" + family, "未提供可实测字体资源；字体状态pending", pending=True)
            continue
        path = resolve_path(ref, base)
        available = within(path, base) and readable_file(path) and sfnt_declares_family(path, family)
        report.need(available, "font_resource:" + family, "随题字体资源真实可读，SFNT name表声明所需family", pending=True, path=str(path))

    audit_numbering(converted, model, case, data, report)
    return model


def audit_numbering(package, model, case, data, report):
    root = package.xml.get("word/numbering.xml")
    nums = {}
    abstracts = set()
    if root is not None:
        for node in root.findall("w:abstractNum", NS):
            abstracts.add(node.get(q(W, "abstractNumId")))
        for node in root.findall("w:num", NS):
            abstract = node.find("w:abstractNumId", NS)
            nums[node.get(q(W, "numId"))] = abstract.get(q(W, "val")) if abstract is not None else None
    references = []
    document = package.xml.get("word/document.xml")
    if document is not None:
        for paragraph in document.iter(q(W, "p")):
            num = model.paragraph_num(paragraph)
            if num is None:
                continue
            num_id = num.find("w:numId", NS)
            level = num.find("w:ilvl", NS)
            references.append((num_id.get(q(W, "val")) if num_id is not None else None, level.get(q(W, "val")) if level is not None else "0"))
    required = bool(data.get("numbering_required", case.get("numbering_required", False)))
    if required:
        report.need(bool(references), "numbering_present", "合同要求的实际列表编号存在（含样式继承）")
    for num_id, level in references:
        valid = num_id in nums and nums[num_id] in abstracts
        if valid:
            abstract = next((n for n in root.findall("w:abstractNum", NS) if n.get(q(W, "abstractNumId")) == nums[num_id]), None)
            valid = abstract is not None and any(n.get(q(W, "ilvl")) == str(level) for n in abstract.findall("w:lvl", NS))
        report.need(valid, "numbering_resolved:%s:%s" % (num_id, level), "numId/ilvl实际解析到abstractNum与lvl")


def allowed_difference(case, data, kind):
    allowed = data.get("allowed_differences", case.get("allowed_differences", []))
    if isinstance(allowed, dict):
        return bool(allowed.get(kind))
    return isinstance(allowed, list) and kind in allowed


def compare_packages(source, converted, case, data, report):
    if not source.valid or not converted.valid:
        report.need(False, "ooxml_compare_pending", "源或成品结构不可可靠解析，保真检查pending", pending=True)
        return
    source_text = source.story_text()
    converted_text = converted.story_text()
    if allowed_difference(case, data, "text"):
        report.need(True, "content_text_allowed_difference", "原case明确允许文本差异")
    else:
        report.need(source_text == converted_text, "content_text_preserved", "正文、页眉脚、脚注尾注和批注文本保持完整")
    source_objects = source.objects()
    converted_objects = converted.objects()
    for kind in ("math", "comments", "fields", "media", "relationships"):
        missing = source_objects[kind] - converted_objects[kind]
        allowed = allowed_difference(case, data, kind)
        report.need(not missing or allowed, "object_preserved:" + kind, "转换未丢公式/批注/域/媒体或关系；格式变化不构成豁免", missing=[str(x) for x in missing], allowed=bool(allowed))


def load_case(data, base, report):
    case = data
    ref = data.get("case_json")
    if not ref:
        return case
    path = resolve_path(ref, base)
    if not within(path, base) or not readable_file(path):
        report.need(False, "case_json_exists", "原case真实存在可读且不越出材料根目录", path=str(path))
        return case
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("case top-level must be object")
        case = loaded
    except (OSError, ValueError) as exc:
        report.need(False, "case_json_valid", "原case JSON可读取: %s" % exc)
    for key in ("case_id", "output_contract"):
        if key in data and key in case:
            report.need(data[key] == case[key], "case_%s_consistent" % key, "审计副本与原case一致")
    return case


def iter_contract_paths(value, key=""):
    if isinstance(value, dict):
        if isinstance(value.get("path"), str):
            yield key or "path", value["path"]
        for child_key, child in value.items():
            if child_key != "path":
                yield from iter_contract_paths(child, child_key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_contract_paths(child, "%s[%d]" % (key, index))
    elif isinstance(value, str) and PATH_KEY.search(key):
        yield key, value


def audit_contract(data, case, base, report):
    case_id = case.get("case_id")
    contract = case.get("output_contract")
    report.need(isinstance(case_id, str) and bool(case_id.strip()), "case_id_present", "真实case_id存在")
    report.need(isinstance(contract, dict), "output_contract_present", "原output_contract对象存在")
    allowed_root = resolve_path(data.get("allowed_output_dir", "/app/output"), base)
    if "allowed_output_dir" in data:
        report.need(
            data.get("synthetic") is True and isinstance(data.get("allowed_output_dir"), str) and bool(data.get("allowed_output_dir").strip()) and within(allowed_root, base),
            "output_root_override", "非默认输出根仅允许synthetic测试显式覆盖，且必须位于审计材料根目录内",
        )
    output_dir = resolve_path(data.get("output_dir", allowed_root), base)
    report.need(output_dir == allowed_root, "output_root", "实际输出目录等于允许根目录（默认/app/output）")
    ref = data.get("result_json")
    report.need(bool(ref), "result_json_present", "真实交付审计必须提供result_json")
    if not ref:
        return allowed_root, None
    result_path = resolve_path(ref, base)
    if not within(result_path, allowed_root) or not readable_file(result_path):
        report.need(False, "result_json_exists", "result.json真实存在可读且位于允许输出根目录", path=str(result_path))
        return allowed_root, None
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise ValueError("result top-level must be object")
    except (OSError, ValueError) as exc:
        report.need(False, "result_json_valid", "result JSON可读取: %s" % exc)
        return allowed_root, None
    report.need(result.get("case_id") == case_id, "case_id_match", "result保留真实case_id")
    report.need(result.get("output_contract") == contract, "output_contract_preserved", "result逐字段保留原嵌套合同")
    missing = sorted(set(contract or {}) - set(result)) if isinstance(contract, dict) else []
    report.need(not missing, "contract_fields_complete", "result包含原合同全部顶层字段", missing=missing)
    first_deliverable = None
    if isinstance(contract, dict):
        for key in contract:
            if key not in result:
                report.need(False, "deliverable_field_missing:" + key, "合同字段缺失，无法验证对应成品", field=key)
                continue
            paths = list(iter_contract_paths(result[key], key))
            if PATH_KEY.search(key) and not paths:
                report.need(False, "deliverable_path_present:" + key, "路径字段必须包含实际字符串/path对象")
            for path_key, value in paths:
                artifact = resolve_path(value, allowed_root)
                safe = within(artifact, allowed_root)
                exists = safe and readable_file(artifact)
                report.need(exists, "deliverable_exists:" + path_key, "合同成品存在可读且不逃逸", path=str(artifact))
                if key == "deliverable" and first_deliverable is None:
                    first_deliverable = artifact
                if not exists:
                    continue
                lower = artifact.suffix.lower()
                if lower == ".pdf" or "pdf" in path_key.lower():
                    signature, pages = pdf_info(artifact)
                    report.need(signature, "preview_pdf_signature:" + path_key, "要求PDF时具有真实签名/EOF")
                    report.need(signature and pages > 0, "preview_pdf_pages:" + path_key, "PDF至少含可机械识别页面对象", page_count=pages)
                elif lower == ".png" or "png" in path_key.lower():
                    report.need(png_ok(artifact), "preview_png_structure:" + path_key, "要求PNG时具有真实签名/IHDR/IEND")
    return allowed_root, first_deliverable


def real_file(value, base, root, label, report):
    report.need(isinstance(value, str) and bool(value.strip()), label + "_present", label + "必须明确指向真实文件")
    if not isinstance(value, str) or not value.strip():
        return None
    path = resolve_path(value, base)
    report.need(within(path, root) and readable_file(path), label + "_exists", "真实文件存在可读且路径不逃逸", path=str(path))
    return path if within(path, root) and readable_file(path) else None


def audit_real(data, case, base, report):
    audit_declared_units(data, report) if "source_units" in data or "converted_units" in data else None
    allowed_root, contract_deliverable = audit_contract(data, case, base, report)
    source_path = real_file(data.get("source_file", case.get("source_file")), base, base, "source_file", report)
    template_path = real_file(data.get("template_file", case.get("template_file")), base, base, "template_file", report)
    converted_ref = data.get("converted_file", case.get("converted_file"))
    if converted_ref:
        converted_path = real_file(converted_ref, allowed_root, allowed_root, "converted_file", report)
    else:
        converted_path = contract_deliverable if contract_deliverable and readable_file(contract_deliverable) else None
        report.need(converted_path is not None, "converted_file_exists", "可编辑成品来自真实合同deliverable")
    if not source_path or not template_path or not converted_path:
        report.need(False, "ooxml_compare_pending", "源/模板/成品任一缺失时保留partial且不作保真通过", pending=True)
        return
    source = Package(source_path, "source", report)
    template = Package(template_path, "template", report)
    converted = Package(converted_path, "converted", report)
    if not template.valid:
        report.need(False, "template_ooxml_valid", "真实模板Office ZIP/XML可解析")
    compare_packages(source, converted, case, data, report)
    if converted.valid:
        audit_styles(converted, case, data, base, report)
    else:
        report.need(False, "style_parse_pending", "成品不可可靠解析，样式/字体检查pending", pending=True)


def main():
    data, p = load()
    report = Report()
    base = p.parent.resolve()
    real_keys = {"case_json", "case_id", "output_contract", "result_json", "source_file", "template_file", "converted_file"}
    real_mode = data.get("synthetic") is not True or bool(real_keys & set(data))
    if real_mode:
        case = load_case(data, base, report)
        audit_real(data, case, base, report)
        output_data = dict(data)
        output_data["case_id"] = case.get("case_id", data.get("case_id"))
    else:
        audit_declared_units(data, report)
        audit_legacy_specs(data, report)
        output_data = data
    return finish(output_data, p, report, real_mode)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        sys.exit(2)
