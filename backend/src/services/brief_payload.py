"""免登录简报 payload 的组装与 CSV 渲染（仅标准库，无数据库依赖）。"""

import csv
import io
from datetime import datetime, timezone

from src.services.brief_logic import CELL_KEYS, ORDER_GROUPS, build_sections

VERSION_FIELDS = ('id', 'code', 'title', 'exhibit', 'type_pt', 'line_height')
SUPPRESSION_RULE = ('四格表中 1–4 人的小格予以遮罩，可由可见数字唯一反推小格的计数一并遮罩；'
                    '相对任一已发布版本仅有 1–4 人入组、退出或换格的读序整组缓发。')


def _version_desc(versions, version_id):
    matched = next((row for row in versions if row['id'] == version_id), None)
    if not matched:
        return None
    return {field: matched[field] for field in VERSION_FIELDS if field in matched}


def build_payload(run, snapshot, held_orders, held_detail):
    sections = build_sections(snapshot['pairs'], held_orders)
    return {
        'run_id': run['id'],
        'study_id': run['study_id'],
        'study_code': snapshot['study_code'],
        'study_title': snapshot['study_title'],
        'station': snapshot['station'],
        'reading_task': snapshot['reading_task'],
        'revision': run['revision'],
        'confidence': run['confidence'],
        'method': run['method'],
        'checksum': run['checksum'],
        'versions': {
            'old': _version_desc(snapshot['versions'], snapshot['old_id']),
            'new': _version_desc(snapshot['versions'], snapshot['new_id']),
        },
        'signoff': {
            'decision': run['decision'],
            'note': run['note'],
            'signed_by': run['signed_by'],
            'signed_at': run['signed_at'].isoformat(),
        },
        'held_orders': held_orders,
        'held_detail': held_detail,
        'sections': sections,
        'suppression_rule': SUPPRESSION_RULE,
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }


def brief_csv(payload):
    """聚合口径 CSV：每读序一行 + 合计一行，被遮数字留空，不含观众记录。"""
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['section', 'held', *CELL_KEYS, 'n', 'missing', 'registered',
                     'old_success', 'new_success', 'old_rate', 'new_rate'])

    def number(item):
        if item['suppressed'] or item['value'] is None:
            return ''
        return item['value']

    def rate(item):
        if item['suppressed'] or item['value'] is None:
            return ''
        return f"{item['value']:.4f}"

    for section in (*ORDER_GROUPS, 'total'):
        data = payload['sections'][section]
        writer.writerow([
            section, data['held'],
            *[number(data['cells'][key]) for key in CELL_KEYS],
            number(data['n']), number(data['missing']), number(data['registered']),
            number(data['old_success']), number(data['new_success']),
            rate(data['old_rate']), rate(data['new_rate']),
        ])
    return output.getvalue()
