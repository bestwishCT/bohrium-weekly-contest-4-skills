#!/usr/bin/env python3
"""Structure-preserving MathML inspection and guarded numeric substitution."""
import argparse
import ast
import json
import math
import operator
import sys
import xml.etree.ElementTree as ET

from common import Run, add_arguments, digest, evidence_ok, paragraph_map, paragraphs, read_json, stable_id

GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "θ": "theta", "λ": "lambda", "μ": "mu", "π": "pi", "ρ": "rho", "σ": "sigma", "τ": "tau", "φ": "phi", "ω": "omega",
         "Γ": "Gamma", "Δ": "Delta", "Θ": "Theta", "Λ": "Lambda", "Π": "Pi", "Σ": "Sigma", "Φ": "Phi", "Ω": "Omega"}


def local(element):
    return element.tag.rsplit("}", 1)[-1]


def latex(element, warnings):
    tag = local(element)
    children = list(element)
    text = (element.text or "").strip()
    if tag in {"mi", "mn", "mo", "mtext"}:
        return "\\" + GREEK[text] + " " if text in GREEK else text
    if tag in {"math", "mrow", "mstyle", "semantics"}:
        return " ".join(filter(None, (latex(child, warnings) for child in children))) or text
    if tag in {"annotation", "annotation-xml"}:
        return ""
    if tag == "mfrac" and len(children) == 2:
        return "\\frac{%s}{%s}" % (latex(children[0], warnings), latex(children[1], warnings))
    if tag in {"msub", "msup", "msubsup"}:
        expected = {"msub": 2, "msup": 2, "msubsup": 3}[tag]
        if len(children) != expected:
            warnings.append("invalid child count for " + tag)
            return ""
        base = latex(children[0], warnings)
        if tag == "msub":
            return "%s_{%s}" % (base, latex(children[1], warnings))
        if tag == "msup":
            return "%s^{%s}" % (base, latex(children[1], warnings))
        return "%s_{%s}^{%s}" % (base, latex(children[1], warnings), latex(children[2], warnings))
    if tag == "msqrt":
        return "\\sqrt{%s}" % " ".join(latex(child, warnings) for child in children)
    if tag == "mroot" and len(children) == 2:
        return "\\sqrt[%s]{%s}" % (latex(children[1], warnings), latex(children[0], warnings))
    if tag == "mfenced":
        return "%s%s%s" % (element.get("open", "("),
                            (element.get("separators", ",") + " ").join(latex(child, warnings) for child in children),
                            element.get("close", ")"))
    if tag == "mtable":
        rows = []
        for row in children:
            if local(row) != "mtr":
                continue
            rows.append(" & ".join(latex(cell, warnings) for cell in row if local(cell) == "mtd"))
        return "\\begin{matrix}%s\\end{matrix}" % " \\\\ ".join(rows)
    if tag in {"mover", "munder", "munderover"}:
        warnings.append("accent/limit tag retained by generic LaTeX grouping: " + tag)
        return " ".join(latex(child, warnings) for child in children)
    if list(element):
        warnings.append("unmodeled MathML container preserved through recursive descent: " + tag)
        return text + " ".join(latex(child, warnings) for child in children)
    return text


def formula_records(root, origin):
    records = []

    def walk(element, inherited_id=None, inherited_label=None):
        identifier = element.get("id", inherited_id)
        label = inherited_label
        if local(element) == "label" and element.text:
            label = element.text.strip()
        if local(element) == "math":
            records.append((identifier or element.get("id"), label, element))
            return
        for child in element:
            walk(child, identifier, label)

    walk(root)
    result = []
    for position, (source_id, label, element) in enumerate(records, 1):
        warnings = []
        result.append({"formula_id": source_id or stable_id("formula", origin, position),
                       "source_id": source_id, "formula_number": label,
                       "mathml": ET.tostring(element, encoding="unicode"),
                       "latex": latex(element, warnings), "origin": origin,
                       "source_position": position, "warnings": warnings})
    return result


def safe_eval(expression, values):
    operations = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                  ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg,
                  ast.UAdd: operator.pos}

    def evaluate(node):
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in values:
                raise ValueError("missing numeric input: " + node.id)
            return values[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in operations:
            if isinstance(node.op, ast.Pow) and isinstance(node.right, ast.Constant) and abs(node.right.value) > 12:
                raise ValueError("exponent exceeds guarded limit")
            return operations[type(node.op)](evaluate(node.left), evaluate(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in operations:
            return operations[type(node.op)](evaluate(node.operand))
        raise ValueError("only literal arithmetic with supplied variables is permitted")

    return evaluate(ast.parse(expression, mode="eval"))


def main():
    args = add_arguments(argparse.ArgumentParser(description=__doc__)).parse_args()
    try:
        run = Run(args)
    except Exception as error:
        print(json.dumps({"status": "input_error", "error": str(error)}), file=sys.stderr)
        return 2
    try:
        formula_path = run.path(run.case.get("formula_xml", "artifacts/source-formulas.xml"))
        publisher_path = run.path(run.case.get("publisher_xml", "artifacts/publisher-source.xml"))
        formula_tree = ET.parse(str(formula_path))
        publisher_tree = ET.parse(str(publisher_path))
        formulas = formula_records(formula_tree.getroot(), str(formula_path))
        publisher_formulas = formula_records(publisher_tree.getroot(), str(publisher_path))
        publisher_by_id = {item["source_id"]: item for item in publisher_formulas if item["source_id"]}
        missing_on_publisher = []
        for formula in formulas:
            match = publisher_by_id.get(formula["source_id"])
            if match is None:
                missing_on_publisher.append(formula["formula_id"])
            elif match["mathml"] != formula["mathml"]:
                missing_on_publisher.append(formula["formula_id"] + " (same ID but different MathML structure)")
            else:
                formula["publisher_crosscheck"] = "same_source_id_and_mathml_structure"
                formula["publisher_source_sha256"] = digest(publisher_path)
        candidate = run.candidate("formula-analysis.json")
        variable_rows = candidate.get("variables", [])
        material_index = {}
        if run.case.get("material_path"):
            material_index = paragraph_map(paragraphs(read_json(run.path(run.case["material_path"]))))
        for variable in variable_rows:
            if variable.get("definition_kind") == "reported" and not evidence_ok(material_index, variable):
                raise ValueError("reported variable definition lacks exact evidence: " + str(variable.get("symbol")))
            if variable.get("definition_kind") not in {"reported", "context_inference", "common_knowledge", "not_reported"}:
                raise ValueError("variable definition kind is invalid")
        evaluations = []
        evaluation_failures = []
        for item in candidate.get("evaluations", []):
            record = dict(item)
            try:
                values = {key: float(value) for key, value in item.get("inputs", {}).items()}
                if not values:
                    raise ValueError("no actual numeric values supplied; unknown values were not set to zero")
                record["computed"] = safe_eval(item["expression"], values)
                record["numeric_status"] = "computed_from_supplied_values"
                if "expected" in item and "absolute_tolerance" in item:
                    record["absolute_difference"] = abs(record["computed"] - float(item["expected"]))
                    record["within_predeclared_tolerance"] = record["absolute_difference"] <= float(item["absolute_tolerance"])
            except Exception as error:
                record["numeric_status"] = "insufficient_evidence"
                record["reason"] = str(error)
                evaluation_failures.append(str(error))
            evaluations.append(record)
        c02_missing = run.case.get("case_id") == "C02" or not formulas
        failures = evaluation_failures + (["source formula IDs absent from publisher XML"] if missing_on_publisher else [])
        if c02_missing:
            failures.append("formula representation/derivation/raw predictions unavailable; stopped at actual evidence boundary")
        status = "partial" if failures or run.uncertainty else "success"
        run.changes.append("retained MathML structure and source IDs; no formula catalog was treated as a requested proof")
        run.finish({"status": "insufficient_evidence" if c02_missing else status,
                    "formulas": formulas, "variables": variable_rows, "evaluations": evaluations,
                    "publisher_crosscheck_failures": missing_on_publisher, "failures": failures,
                    "sources": [{"path": str(formula_path), "sha256": digest(formula_path)},
                                {"path": str(publisher_path), "sha256": digest(publisher_path)}]},
                   aliases={"math": "formulas", "mathmlformulas": "formulas", "result": "formulas"},
                   code=2 if status == "partial" else 0,
                   trace_extra={"c02_missing_branch": c02_missing,
                                "mathml_scope": "deterministic common MathML subset; unmodeled tags explicitly warned"})
    except Exception as error:
        run.uncertainty.append(str(error))
        run.finish({"status": "partial", "formulas": [], "variables": [], "evaluations": [],
                    "failures": [str(error)]}, code=2)


if __name__ == "__main__":
    sys.exit(main())
