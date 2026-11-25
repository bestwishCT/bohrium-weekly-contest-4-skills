#!/usr/bin/env python3
"""Initialize runtime files without inventing case results or public contract fields."""

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path


PARTIAL_REASON = "仅完成运行初始化；六维诊断和完整论文分析尚未执行。"


def parse_args():
    parser = argparse.ArgumentParser(
        description="读取真实case并尽早写入部分结果；不执行论文语义诊断。"
    )
    parser.add_argument('--case', default='/input/case.json', help='实际case.json路径')
    parser.add_argument('--task', default='/input/task.md', help='实际task.md路径')
    parser.add_argument('--work-dir', default='/app/work', help='中间产物目录')
    parser.add_argument('--output-dir', default='/app/output', help='最终产物目录')
    parser.add_argument(
        '--material',
        action='append',
        default=[],
        help='补充实际材料路径；可重复。相对路径以case.json目录为基准。',
    )
    parser.add_argument(
        '--finalize',
        action='store_true',
        help='校验已有result.json的嵌套required/type/enum/items；失败时保留内容并标记partial。',
    )
    return parser.parse_args()


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def declared_materials(case, extra_paths):
    """Read only explicit paths; unknown case-specific material layouts stay unguessed."""
    entries = []
    raw_materials = case.get('materials', [])
    if isinstance(raw_materials, dict):
        raw_materials = list(raw_materials.values())
    if isinstance(raw_materials, list):
        for index, item in enumerate(raw_materials):
            if isinstance(item, str):
                entries.append({'material_id': 'material-' + str(index + 1), 'path': item})
            elif isinstance(item, dict):
                material_path = next(
                    (item[key] for key in ('path', 'file_path', 'source_path') if isinstance(item.get(key), str)),
                    None,
                )
                if material_path is not None:
                    entries.append({
                        'material_id': item.get('material_id') or item.get('id') or 'material-' + str(index + 1),
                        'path': material_path,
                    })
    for index, material_path in enumerate(extra_paths):
        entries.append({'material_id': 'cli-material-' + str(index + 1), 'path': material_path})
    return entries


def catalog_materials(case, case_path, extra_paths):
    catalog = []
    for entry in declared_materials(case, extra_paths):
        raw_path = Path(entry['path'])
        material_path = raw_path if raw_path.is_absolute() else case_path.parent / raw_path
        record = {
            'material_id': str(entry['material_id']),
            'declared_path': entry['path'],
            'resolved_path': str(material_path.resolve()),
        }
        try:
            if not material_path.is_file():
                record['status'] = 'missing'
            else:
                record.update({
                    'status': 'readable',
                    'size_bytes': material_path.stat().st_size,
                    'sha256': sha256(material_path),
                })
        except OSError as exc:
            record.update({'status': 'unreadable', 'error': str(exc)})
        catalog.append(record)
    return catalog


def index_manuscript(text):
    """Build exact character anchors for UTF-8 text; sentence splits are candidates only."""
    sections, paragraphs, sentences = [], [], []
    current = None
    buffer = []
    buffer_start = None
    offset = 0

    def new_section(title, level):
        nonlocal current
        current = {
            'id': 'S' + str(len(sections) + 1),
            'title': title,
            'heading_level': level,
            'paragraph_count': 0,
        }
        sections.append(current)

    def flush():
        nonlocal buffer, buffer_start
        if not buffer or current is None:
            buffer, buffer_start = [], None
            return
        raw = ''.join(buffer)
        left = len(raw) - len(raw.lstrip())
        right = len(raw.rstrip())
        paragraph_text = raw[left:right]
        if paragraph_text:
            current['paragraph_count'] += 1
            paragraph_id = current['id'] + '.P' + str(current['paragraph_count'])
            start = buffer_start + left
            end = buffer_start + right
            paragraphs.append({
                'id': paragraph_id,
                'section_id': current['id'],
                'section_title': current['title'],
                'text': paragraph_text,
                'start_char': start,
                'end_char': end,
            })
            candidate_pattern = re.compile(r'.+?(?:[.!?。！？]+(?=\s|$)|\Z)', re.DOTALL)
            count = 0
            for match in candidate_pattern.finditer(paragraph_text):
                candidate = match.group(0)
                candidate_left = len(candidate) - len(candidate.lstrip())
                candidate_right = len(candidate.rstrip())
                candidate = candidate[candidate_left:candidate_right]
                if not candidate:
                    continue
                count += 1
                sentences.append({
                    'id': paragraph_id + '.Z' + str(count),
                    'paragraph_id': paragraph_id,
                    'text': candidate,
                    'start_char': start + match.start() + candidate_left,
                    'end_char': start + match.start() + candidate_right,
                    'review_required': True,
                })
        buffer, buffer_start = [], None

    for line in text.splitlines(keepends=True):
        heading = re.match(r'^\s*(#{1,6})\s+(.+?)\s*#*\s*(?:\r?\n)?$', line)
        numbered = re.match(r'^\s*(\d+(?:\.\d+){0,5})\s+(\S.*?)\s*(?:\r?\n)?$', line)
        if heading or numbered:
            flush()
            if heading:
                new_section(heading.group(2), len(heading.group(1)))
            else:
                new_section(numbered.group(1) + ' ' + numbered.group(2), numbered.group(1).count('.') + 1)
        elif not line.strip():
            flush()
        else:
            if current is None:
                new_section('未命名起始部分', None)
            if buffer_start is None:
                buffer_start = offset
            buffer.append(line)
        offset += len(line)
    flush()
    return {
        'sections': sections,
        'paragraphs': paragraphs,
        'sentence_candidates': sentences,
        'character_count': len(text),
        'sentence_note': '句子为确定性候选切分，缩写、小数和跨行情况须由Agent回读；字符区间与段落原文可机械复核。',
        'possible_truncation_markers': [
            marker for marker in ('[truncated]', '[...]', '……', '…') if marker in text
        ],
    }


def public_fields(output_contract):
    """Support a direct field map or an object JSON Schema without merging the two."""
    if isinstance(output_contract.get('properties'), dict):
        return output_contract['properties']
    return output_contract


def empty_value(spec):
    if isinstance(spec, list):
        return []
    if not isinstance(spec, dict):
        return None
    value_type = spec.get('type')
    if value_type == 'array':
        return []
    if value_type == 'object' or isinstance(spec.get('properties'), dict):
        return {
            field: empty_value(child)
            for field, child in spec.get('properties', {}).items()
        }
    if value_type is None and not {'enum', 'items', 'required'} & set(spec):
        return {field: empty_value(child) for field, child in spec.items()}
    if value_type == 'string':
        allowed = spec.get('enum')
        if isinstance(allowed, list) and 'not_reported' in allowed:
            return 'not_reported'
        return ''
    if value_type == 'boolean':
        return False
    return None


def initial_public_value(field, spec, gaps):
    """Map partial state recursively to declared fields; never add audit fields."""
    normalized = field.lower()
    value_type = spec.get('type') if isinstance(spec, dict) else None
    if isinstance(spec, dict) and (value_type == 'object' or isinstance(spec.get('properties'), dict)):
        return {
            child_field: initial_public_value(child_field, child_spec, gaps)
            for child_field, child_spec in spec.get('properties', {}).items()
        }
    if isinstance(spec, dict) and value_type is None and not {'enum', 'items', 'required'} & set(spec):
        return {
            child_field: initial_public_value(child_field, child_spec, gaps)
            for child_field, child_spec in spec.items()
        }
    if 'status' in normalized:
        enum = spec.get('enum') if isinstance(spec, dict) else None
        if isinstance(enum, list):
            return 'partial' if 'partial' in enum else empty_value(spec)
        return 'partial' if value_type in (None, 'string') else empty_value(spec)
    if any(token in normalized for token in ('uncert', 'gap', 'limit')) and gaps:
        reason = '；'.join(gaps)
        if value_type == 'array' or isinstance(spec, list):
            return [reason]
        if value_type in (None, 'string'):
            return reason
    return empty_value(spec)


def matches_type(value, expected):
    checks = {
        'object': lambda item: isinstance(item, dict),
        'array': lambda item: isinstance(item, list),
        'string': lambda item: isinstance(item, str),
        'boolean': lambda item: isinstance(item, bool),
        'integer': lambda item: isinstance(item, int) and not isinstance(item, bool),
        'number': lambda item: isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item),
        'null': lambda item: item is None,
    }
    return expected not in checks or checks[expected](value)


def validate_contract(value, spec, path='result'):
    """Recursive JSON-Schema subset plus legacy direct-field-map validation."""
    errors = []
    if isinstance(spec, dict) and (
        'type' in spec or 'properties' in spec or 'required' in spec or 'enum' in spec or 'items' in spec
    ):
        declared = spec.get('type', 'object' if 'properties' in spec else None)
        kinds = declared if isinstance(declared, list) else [declared]
        if declared is not None and not any(matches_type(value, kind) for kind in kinds):
            return [path + ' has incorrect type for ' + str(declared)]
        if 'enum' in spec and value not in spec['enum']:
            errors.append(path + ' is outside declared enum ' + repr(spec['enum']))
        if isinstance(value, dict):
            properties = spec.get('properties', {})
            for field in spec.get('required', []):
                if field not in value:
                    errors.append(path + '.' + field + ' is required')
            for field, child in properties.items():
                if field in value:
                    errors.extend(validate_contract(value[field], child, path + '.' + field))
        if isinstance(value, list) and isinstance(spec.get('items'), dict):
            for index, item in enumerate(value):
                errors.extend(validate_contract(item, spec['items'], '%s[%d]' % (path, index)))
        return errors
    if isinstance(spec, dict):
        if not isinstance(value, dict):
            return [path + ' has incorrect type for object']
        for field, child in spec.items():
            if field not in value:
                errors.append(path + '.' + field + ' is required')
            else:
                errors.extend(validate_contract(value[field], child, path + '.' + field))
        return errors
    if isinstance(spec, list):
        if not isinstance(value, list):
            return [path + ' has incorrect type for array']
        if spec:
            for index, item in enumerate(value):
                errors.extend(validate_contract(item, spec[0], '%s[%d]' % (path, index)))
        return errors
    expected = type(spec)
    if value is None or not isinstance(value, expected) or (expected is not bool and isinstance(value, bool)):
        errors.append(path + ' has incorrect type for ' + expected.__name__)
    return errors


def validate_result(result, output_contract):
    if isinstance(output_contract.get('properties'), dict):
        return validate_contract(result, output_contract)
    if not isinstance(result, dict):
        return ['result has incorrect type for object']
    errors = []
    for field, spec in output_contract.items():
        if field not in result:
            errors.append('result.' + field + ' is required')
        else:
            errors.extend(validate_contract(result[field], spec, 'result.' + field))
    return errors


def mark_partial(result, output_contract):
    if not isinstance(result, dict) or 'status' not in result:
        return
    spec = public_fields(output_contract).get('status')
    allowed = spec.get('enum') if isinstance(spec, dict) else None
    if isinstance(allowed, list):
        if result.get('status') == 'partial' and 'partial' in allowed:
            return
        if 'partial' in allowed:
            result['status'] = 'partial'
        return
    if spec is None or isinstance(spec, str) or (isinstance(spec, dict) and spec.get('type') == 'string'):
        result['status'] = 'partial'


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    args = parse_args()
    case_path = Path(args.case)
    task_path = Path(args.task)
    case = json.loads(case_path.read_text(encoding='utf-8'))
    if not isinstance(case, dict):
        raise ValueError('case.json top level must be an object')
    case_id = case.get('case_id')
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError('case.json requires a non-empty case_id string')
    output_contract = case.get('output_contract')
    if not isinstance(output_contract, dict):
        raise ValueError('case.json requires an output_contract object; its actual fields cannot be guessed')
    task_text = task_path.read_text(encoding='utf-8')
    if not task_text.strip():
        raise ValueError('task.md must not be empty')

    work_dir = Path(args.work_dir)
    output_dir = Path(args.output_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.finalize:
        result_path = output_dir / 'result.json'
        result = json.loads(result_path.read_text(encoding='utf-8'))
        errors = []
        if not isinstance(result, dict) or result.get('case_id') != case_id:
            errors.append('result.case_id must exactly match the original case_id')
        errors.extend(validate_result(result, output_contract))
        if errors:
            mark_partial(result, output_contract)
            write_json(result_path, result)
        report = {
            'case_id': case_id,
            'status': 'pass' if not errors else 'partial',
            'errors': errors,
            'validation': 'recursive required/type/enum/items; original case_id checked',
        }
        write_json(work_dir / 'finalization.json', report)
        print(json.dumps(report, ensure_ascii=False))
        return 0 if not errors else 2

    materials = catalog_materials(case, case_path, args.material)
    gaps = [PARTIAL_REASON]
    if not materials:
        gaps.append('case未使用已支持的materials路径声明，且未提供--material；材料范围待实际读取确认。')
    missing = [item['material_id'] for item in materials if item['status'] != 'readable']
    if missing:
        gaps.append('以下已声明材料不可读：' + '、'.join(missing) + '。')

    manuscript_indexes = []
    for material in materials:
        path = Path(material['resolved_path'])
        if material['status'] != 'readable' or path.suffix.lower() not in {'.md', '.txt', '.text', '.tex'}:
            continue
        try:
            text = path.read_text(encoding='utf-8')
            index = index_manuscript(text)
            index.update({'material_id': material['material_id'], 'source_path': str(path), 'sha256': material['sha256']})
            manuscript_indexes.append(index)
        except (OSError, UnicodeError) as exc:
            gaps.append('文本材料 %s 无法按UTF-8建立句段索引：%s。' % (material['material_id'], exc))
    if manuscript_indexes:
        write_json(work_dir / 'manuscript-index.json', {'documents': manuscript_indexes})

    fields = public_fields(output_contract)
    result = {'case_id': case_id}
    for field, spec in fields.items():
        if field != 'case_id':
            result[field] = initial_public_value(field, spec, gaps)

    snapshot = {
        'case_id': case_id,
        'case_path': str(case_path.resolve()),
        'task_path': str(task_path.resolve()),
        'task_sha256': hashlib.sha256(task_text.encode('utf-8')).hexdigest(),
        'original_output_contract': output_contract,
        'materials': materials,
        'manuscript_index_path': str(work_dir / 'manuscript-index.json') if manuscript_indexes else None,
        'indexed_text_materials': len(manuscript_indexes),
    }
    state = {
        'case_id': case_id,
        'status': 'partial',
        'gaps': gaps,
        'public_result_path': str(output_dir / 'result.json'),
        'note': '公开result仅含case_id和原output_contract字段；审计内部字段不得自动映射进去。',
    }
    write_json(work_dir / 'input_snapshot.json', snapshot)
    write_json(work_dir / 'run_state.json', state)
    write_json(output_dir / 'result.json', result)
    print(json.dumps({
        'case_id': case_id,
        'status': 'partial',
        'result': str(output_dir / 'result.json'),
        'gaps': gaps,
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}, ensure_ascii=False))
        sys.exit(2)
