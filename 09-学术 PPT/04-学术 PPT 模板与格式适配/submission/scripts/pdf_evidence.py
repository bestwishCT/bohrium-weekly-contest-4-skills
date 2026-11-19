from decimal import Decimal, InvalidOperation


def canonical(value):
    return " ".join(value.split())


def page_lines(path):
    if path.suffix.lower() != ".pdf":
        raise ValueError("source material must be the actual PDF")
    try:
        import fitz
    except ImportError:
        raise ValueError("dependency-blocked: PyMuPDF is required to read source PDF pages")
    with fitz.open(str(path)) as document:
        pages = [[canonical(line) for line in page.get_text("text", sort=True).splitlines() if canonical(line)] for page in document]
    if not pages:
        raise ValueError("source PDF has no pages")
    return pages


def validate_citation(pdf_path, source, context, pages=None):
    pages = pages if pages is not None else page_lines(pdf_path)
    page = source.get("pdf_page")
    quote = source.get("quote")
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= len(pages):
        raise ValueError(f"{context}: PDF page is outside the actual document")
    if not isinstance(quote, str) or not canonical(quote):
        raise ValueError(f"{context}: exact source quote required")
    quote = canonical(quote)
    lines = pages[page - 1]
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
    if section is not None and lines.count(canonical(section)) != 1:
        raise ValueError(f"{context}: section heading does not exactly match PDF p.{page}")


def validate_chart(pdf_path, visual, citations, context):
    pages = page_lines(pdf_path)
    source = visual.get("source")
    if not isinstance(source, dict):
        raise ValueError(f"{context}: chart requires a PDF structured-record source")
    page, prefix = source.get("pdf_page"), source.get("record_prefix")
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= len(pages) or not isinstance(prefix, str) or not prefix:
        raise ValueError(f"{context}: invalid chart PDF page or record prefix")
    if not any(item.get("pdf_page") == page and item.get("figure") and prefix == item["figure"] + " data:" for item in citations):
        raise ValueError(f"{context}: chart source must match a cited figure on the same physical PDF page")
    records = []
    for line in pages[page - 1]:
        if not line.startswith(prefix):
            continue
        fields = {}
        for part in line[len(prefix):].split(";"):
            key, separator, value = part.strip().partition("=")
            if not separator or key in fields:
                raise ValueError(f"{context}: malformed or duplicate chart source field")
            fields[key] = value.strip()
        if set(fields) != {"series", "unit", "category", "value"} or any(not fields[key] for key in fields):
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
        try:
            actual_values = [Decimal(str(value)) for value in item.get("values", [])]
        except InvalidOperation:
            raise ValueError(f"{context}: chart values must be numeric")
        if any(not value.is_finite() for value in actual_values):
            raise ValueError(f"{context}: chart values must be finite")
        if actual_values != [expected[name][category] for category in categories]:
            raise ValueError(f"{context}: chart values differ from PDF structured records for series {name}")
    first_series = list(expected)[0]
    expected_axis = f"{first_series} ({unit})" if len(expected) == 1 else f"Value ({unit})"
    if visual.get("y_title") != expected_axis:
        raise ValueError(f"{context}: value-axis title must exactly bind the source measure and unit")
