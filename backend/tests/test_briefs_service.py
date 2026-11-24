"""briefs 服务层端到端测试：用内存假库代替 Postgres，用 stub 代替第三方库。

运行：python3 -m unittest -v tests.test_briefs_service
覆盖：首次发布签发、重试幂等、缓发判定（1–4 人）、非小变化不缓发、
并发先发票据进入比较集、payload 无观众记录、CSV 渲染与 404。
"""

import sys
import types
import unittest
from datetime import datetime, timezone

# --- 第三方依赖 stub，必须在 import src.services.briefs 之前注入 ----------

fastapi = types.ModuleType('fastapi')


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


fastapi.HTTPException = HTTPException
sys.modules['fastapi'] = fastapi

psycopg = types.ModuleType('psycopg')
psycopg_types = types.ModuleType('psycopg.types')
psycopg_json = types.ModuleType('psycopg.types.json')


class Jsonb:
    def __init__(self, value):
        self.value = value


psycopg_json.Jsonb = Jsonb
psycopg_types.json = psycopg_json
psycopg.types = psycopg_types
sys.modules['psycopg'] = psycopg
sys.modules['psycopg.types'] = psycopg_types
sys.modules['psycopg.types.json'] = psycopg_json

from src.services.brief_logic import CELL_KEYS  # noqa: E402
from src.services.brief_payload import build_payload, brief_csv  # noqa: E402
from src.services.briefs import get_public_brief, held_orders_for, issue_brief  # noqa: E402


class FakeCursor:
    """支持服务层实际用到的 SQL 形状的内存假库。"""

    def __init__(self, state):
        self.state = state

    def execute(self, sql, params=None):
        self.state.last_sql = ' '.join(sql.split())
        compact = self.state.last_sql
        params = params or ()
        if compact.startswith('SELECT r.id, r.snapshot FROM analysis_runs r'):
            self._result = [{'id': rid, 'snapshot': self.state.snapshots[rid]}
                            for rid in self.state.published_runs
                            if self.state.runs[rid]['study_id'] == params[0]]
        elif compact.startswith('SELECT * FROM public_briefs WHERE run_id'):
            self._result = next((row for row in self.state.briefs
                                 if row['run_id'] == params[0]), None)
        elif compact.startswith('SELECT snapshot FROM analysis_runs WHERE id'):
            self._result = {'snapshot': self.state.snapshots[params[0]]}
        elif compact.startswith('INSERT INTO public_briefs'):
            token, run_id, study_id, revision, held_orders, payload_wrapped, created_by = params
            row = {'token': token, 'run_id': run_id, 'study_id': study_id,
                   'revision': revision, 'held_orders': held_orders,
                   'payload': payload_wrapped.value, 'created_by': created_by}
            self.state.briefs.append(row)
            self._result = row
        elif compact.startswith('INSERT INTO audit_events'):
            self.state.audit.append(params)
            self._result = None
        elif compact.startswith('SELECT payload, created_at FROM public_briefs WHERE token'):
            self._result = next(({'payload': row['payload'],
                                  'created_at': row.get('created_at')}
                                 for row in self.state.briefs if row['token'] == params[0]), None)
        else:
            raise AssertionError(f'未支持的 SQL: {compact}')
        return self

    def fetchone(self):
        return self._result

    def fetchall(self):
        return self._result if isinstance(self._result, list) else [self._result]


class FakeDB:
    def __init__(self):
        self.runs = {}
        self.snapshots = {}
        self.published_runs = []
        self.briefs = []
        self.audit = []
        self.last_sql = ''

    def execute(self, sql, params=None):
        return FakeCursor(self).execute(sql, params)


def make_pair(code, order, old, new):
    return {'visitor_code': code, 'reading_order': order, 'old_ok': old, 'new_ok': new}


def base_pairs():
    rows = []
    seq = 1
    for order in ('old_first', 'new_first'):
        for key in CELL_KEYS:
            count = {'both_ok': 12, 'old_only': 6, 'new_only': 9, 'neither': 5}[key]
            outcome = {
                'both_ok': (True, True), 'old_only': (True, False),
                'new_only': (False, True), 'neither': (False, False),
            }[key]
            for _ in range(count):
                rows.append(make_pair(f'V{seq:03}', order, *outcome))
                seq += 1
    return rows


def make_snapshot(pairs, revision=1):
    return {
        'study_code': 'PD-1121', 'study_title': '青釉执壶展签复验',
        'station': '常设展 · 二厅', 'reading_task': '判定题',
        'old_id': 1, 'new_id': 2,
        'versions': [
            {'id': 1, 'code': 'YA-01', 'title': '原版', 'exhibit': '执壶',
             'type_pt': 16, 'line_height': 1.35},
            {'id': 2, 'code': 'YA-02', 'title': '易读版', 'exhibit': '执壶',
             'type_pt': 24, 'line_height': 1.6},
        ],
        'pairs': pairs,
    }


def make_run(run_id, revision=1):
    return {
        'id': run_id, 'study_id': 7, 'revision': revision, 'confidence': 95,
        'method': 'paired-exact-v1', 'checksum': f'sha256-{run_id}',
        'decision': 'publish', 'note': '同意发布二厅付印方案。',
        'signed_by': 'curator',
        'signed_at': datetime(2026, 11, 24, 6, 0, tzinfo=timezone.utc),
    }


class BriefServiceTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.pairs = base_pairs()

    def _publish(self, run_id, pairs=None, revision=1, published=None):
        pairs = self.pairs if pairs is None else pairs
        self.db.runs[run_id] = make_run(run_id, revision)
        self.db.snapshots[run_id] = make_snapshot(pairs, revision)
        if published is not None:
            self.db.published_runs = list(published)
        if run_id not in self.db.published_runs:
            self.db.published_runs.append(run_id)
        return issue_brief(self.db, self.db.runs[run_id])

    def test_first_publish_issues_brief_without_hold(self):
        row, created = self._publish(1)
        self.assertTrue(created)
        self.assertEqual(len(self.db.briefs), 1)
        self.assertGreaterEqual(len(row['token']), 24)
        self.assertEqual(row['held_orders'], [])
        payload = row['payload']
        # 不含任何观众级字段
        self.assertNotIn('pairs', payload)
        self.assertNotIn('missing_pairs', payload)
        self.assertEqual(set(payload['sections']), {'old_first', 'new_first', 'total'})
        for section in payload['sections'].values():
            self.assertEqual(set(section['cells']), set(CELL_KEYS))
        # 基础数据四格全部 ≥5，不应有遮罩
        for section in payload['sections'].values():
            self.assertFalse(any(item['suppressed'] for item in section['cells'].values()))

    def test_retry_is_idempotent(self):
        first, created = self._publish(1)
        self.assertTrue(created)
        again, created2 = self._publish(1)
        self.assertFalse(created2)
        self.assertEqual(len(self.db.briefs), 1)
        self.assertEqual(first['token'], again['token'])
        # 没有重复审计事件
        self.assertEqual(len([e for e in self.db.audit if e[1] == 'brief.issued']), 1)

    def test_small_change_holds_that_order_only(self):
        # 先发布 R1
        self._publish(1)
        # R2：old_first 里 2 位观众从缺测补录为完整（入组 2 人，落入 1–4）
        r2 = [dict(row) for row in self.pairs]
        r2.append(make_pair('V901', 'old_first', True, True))
        r2.append(make_pair('V902', 'old_first', False, True))
        held, detail = held_orders_for(self.db, 7, r2)
        self.assertEqual(held, ['old_first'])
        self.assertEqual(detail['old_first'][0]['joined'], 2)
        self.assertEqual(detail['new_first'], [])

        row, created = self._publish(2, pairs=r2, revision=2, published=[1])
        self.assertTrue(created)
        payload = row['payload']
        self.assertEqual(payload['held_orders'], ['old_first'])
        # 缓发读序整组遮罩
        group = payload['sections']['old_first']
        self.assertTrue(group['held'])
        self.assertTrue(all(item['suppressed'] for item in group['cells'].values()))
        self.assertTrue(group['n']['suppressed'])
        # 另一读序不受影响，合计表独立保护
        self.assertFalse(payload['sections']['new_first']['held'])
        self.assertFalse(payload['sections']['total']['held'])

    def test_change_of_five_does_not_hold(self):
        self._publish(1)
        r2 = [dict(row) for row in self.pairs]
        for i in range(5):
            r2.append(make_pair(f'V91{i}', 'new_first', True, True))
        held, detail = held_orders_for(self.db, 7, r2)
        self.assertEqual(held, [])
        self.assertEqual(detail['new_first'], [])

    def test_concurrent_earlier_publication_enters_comparison(self):
        # 模拟并发：另一事务已先发布 R1（存在于 published_runs），
        # 当前 R2 与 R1 仅差 3 人换格，必须把先发的 R1 算进比较集并缓发。
        self._publish(1)
        r2 = [dict(row) for row in self.pairs]
        switched = 0
        for row in r2:
            if row['reading_order'] == 'old_first' and row['old_ok'] and row['new_ok'] and switched < 3:
                row['new_ok'] = False  # both_ok -> old_only，换格
                switched += 1
        self.assertEqual(switched, 3)
        held, detail = held_orders_for(self.db, 7, r2)
        self.assertIn('old_first', held)
        self.assertEqual(detail['old_first'][0]['moved'], 3)

    def test_public_get_and_unknown_token_404(self):
        row, _ = self._publish(1)
        payload = get_public_brief(self.db, row['token'])
        self.assertEqual(payload['run_id'], 1)
        with self.assertRaises(HTTPException) as ctx:
            get_public_brief(self.db, 'not-a-real-token')
        self.assertEqual(ctx.exception.status_code, 404)

    def test_csv_renders_masked_blank_and_no_visitor_rows(self):
        self._publish(1)
        # 构造含缓发与遮罩的 payload 验证 CSV 留空规则
        r2 = [dict(row) for row in self.pairs]
        r2.append(make_pair('V903', 'old_first', True, True))
        row, _ = self._publish(2, pairs=r2, revision=2, published=[1])
        text = brief_csv(row['payload'])
        lines = text.strip().splitlines()
        self.assertEqual(lines[0].split(',')[:2], ['section', 'held'])
        self.assertEqual(len(lines), 4)  # 表头 + 两读序 + 合计
        held_line = next(line for line in lines if line.startswith('old_first,'))
        self.assertTrue(held_line.startswith('old_first,True,'))
        cells = held_line.split(',')
        # held=True 后所有数字列为空
        self.assertTrue(all(value == '' for value in cells[2:]))
        self.assertNotIn('V903', text)

    def test_payload_is_frozen_snapshot(self):
        row, _ = self._publish(1)
        token = row['token']
        first = get_public_brief(self.db, token)
        # 底层快照被后续修订替换后，公开链接仍返回冻结的旧 payload
        self.db.snapshots[1] = make_snapshot(base_pairs() + [make_pair('V999', 'old_first', True, True)], 9)
        second = get_public_brief(self.db, token)
        self.assertEqual(first, second)
        self.assertEqual(second['revision'], 1)


if __name__ == '__main__':
    unittest.main()
