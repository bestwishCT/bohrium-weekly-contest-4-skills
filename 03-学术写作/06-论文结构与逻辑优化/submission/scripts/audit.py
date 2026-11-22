#!/usr/bin/env python3
import json
import re
import sys
from collections import Counter
from pathlib import Path


REQUIRED_DIMENSIONS = ['研究问题', '章节结构', '论证链', '段落关系', '重复内容', '结论边界']
ACTION_TEXT_FIELDS = ('action', 'rationale', 'verify', 'impact', 'priority', 'decision_point')


class Report:
    def __init__(self):
        self.checks = []

    def need(self, ok, code, message):
        self.checks.append({"code": code, "ok": bool(ok), "message": message})

    @property
    def failures(self):
        return [check for check in self.checks if not check["ok"]]


def nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


def finish(data, path, report):
    out = {
        "artifact": str(path),
        "status": "pass" if not report.failures else "fail",
        "checks": report.checks,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if not report.failures else 1


def load():
    if len(sys.argv) < 2:
        raise ValueError("usage: audit.py INPUT.json")
    path = Path(sys.argv[1])
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('top-level JSON must be object')
    return data, path


def main():
    data, path = load()
    report = Report()

    paragraphs = data['paragraphs']
    if not isinstance(paragraphs, list):
        raise ValueError('paragraphs must be an array')
    paragraph_ids = [item.get('id') for item in paragraphs if isinstance(item, dict)]
    report.need(
        all(nonempty_string(item_id) for item_id in paragraph_ids)
        and len(paragraph_ids) == len(paragraphs)
        and not any(count > 1 for count in Counter(paragraph_ids).values()),
        'paragraph_ids',
        '段落ID为非空字符串且不重复',
    )
    paras = {
        item['id']: item
        for item in paragraphs
        if isinstance(item, dict) and nonempty_string(item.get('id'))
    }

    dimensions = data['dimensions']
    if not isinstance(dimensions, list):
        raise ValueError('dimensions must be an array')
    dimension_names = [item.get('dimension') for item in dimensions if isinstance(item, dict)]
    report.need(
        len(dimension_names) == len(dimensions)
        and Counter(dimension_names) == Counter(REQUIRED_DIMENSIONS),
        'six_dimensions',
        '六维完整、不重复且不杜撰类别',
    )
    dims = {
        item['dimension']: item
        for item in dimensions
        if isinstance(item, dict) and isinstance(item.get('dimension'), str)
    }

    for name in REQUIRED_DIMENSIONS:
        dimension = dims.get(name, {})
        report.need(
            dimension.get('status') in ('checked_no_issue', 'found', 'unknown'),
            'status:' + name,
            '六维状态明确',
        )
        if dimension.get('status') != 'found':
            continue

        actions = dimension.get('actions')
        report.need(
            isinstance(actions, list) and bool(actions),
            'actions:' + name,
            '发现项有非空编辑动作数组',
        )
        if not isinstance(actions, list):
            continue

        for raw_action in actions:
            action = raw_action if isinstance(raw_action, dict) else {}
            anchor = action.get('anchor')
            excerpt = action.get('excerpt')
            anchor_valid = nonempty_string(anchor) and anchor in paras
            report.need(anchor_valid, 'anchor:' + name, '诊断锚点真实')
            if anchor_valid:
                paragraph_text = paras[anchor].get('text')
                report.need(
                    nonempty_string(excerpt)
                    and isinstance(paragraph_text, str)
                    and excerpt in paragraph_text,
                    'excerpt:' + name,
                    '诊断原文精确',
                )

            for field in ACTION_TEXT_FIELDS:
                report.need(
                    nonempty_string(action.get(field)),
                    field + ':' + name,
                    field + '必须为非空字符串',
                )
            affected_sections = action.get('affected_sections')
            report.need(
                isinstance(affected_sections, list)
                and bool(affected_sections)
                and all(nonempty_string(section) for section in affected_sections),
                'affected_sections:' + name,
                'affected_sections必须为非空字符串数组',
            )

    similarity_candidates = data['similarity_candidates']
    if not isinstance(similarity_candidates, list):
        raise ValueError('similarity_candidates must be an array')
    for raw_pair in similarity_candidates:
        pair = raw_pair if isinstance(raw_pair, dict) else {}
        first_id, second_id = pair.get('a'), pair.get('b')
        report.need(
            first_id in paras and second_id in paras,
            'similarity_ids',
            '相似候选定位有效',
        )
        report.need(
            pair.get('semantic_review') in ('redundant', 'distinct', 'unknown'),
            'semantic_review:' + str(first_id),
            '相似度经语义复核',
        )

    report.need(not data.get('full_rewrite', False), 'no_unsolicited_rewrite', '未主动全文重写')
    return finish(data, path, report)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        sys.exit(2)
