#!/usr/bin/env python3
"""Initialize runtime files without inventing case results or public contract fields."""

import argparse
import hashlib
import json
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


def public_fields(output_contract):
    """Support a direct field map or an object JSON Schema without merging the two."""
    if (
        isinstance(output_contract.get('properties'), dict)
        and output_contract.get('type') == 'object'
    ):
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
    if value_type == 'object':
        return {}
    if value_type == 'string':
        return ''
    return None


def initial_public_value(field, spec, gaps):
    """Map partial state to existing public fields only; never add audit fields."""
    normalized = field.lower()
    value_type = spec.get('type') if isinstance(spec, dict) else None
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

    materials = catalog_materials(case, case_path, args.material)
    gaps = [PARTIAL_REASON]
    if not materials:
        gaps.append('case未使用已支持的materials路径声明，且未提供--material；材料范围待实际读取确认。')
    missing = [item['material_id'] for item in materials if item['status'] != 'readable']
    if missing:
        gaps.append('以下已声明材料不可读：' + '、'.join(missing) + '。')

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
