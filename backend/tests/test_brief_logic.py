"""brief_logic 的单元测试，只用标准库：python3 -m unittest -v tests.test_brief_logic"""

import unittest
from fractions import Fraction

from src.services.brief_logic import (
    CELL_KEYS,
    _in_span,
    _table_facts,
    build_mask,
    build_sections,
    change_counts,
    is_small_change,
    summarize_cells,
)


def pair(code, order, old, new):
    return {'visitor_code': code, 'reading_order': order, 'old_ok': old, 'new_ok': new}


def make_pairs(cells_by_order, missing_by_order=None):
    """按 {读序: {四格key: 人数}} 构造配对，缺失用 (old,new) 含 None 的列表。"""
    rows = []
    seq = 1
    missing_by_order = missing_by_order or {}
    for order, cells in cells_by_order.items():
        for key in CELL_KEYS:
            outcome = {
                'both_ok': (True, True),
                'old_only': (True, False),
                'new_only': (False, True),
                'neither': (False, False),
            }[key]
            for _ in range(cells[key]):
                rows.append(pair(f'V{seq:03}', order, *outcome))
                seq += 1
        for old, new in missing_by_order.get(order, []):
            rows.append(pair(f'V{seq:03}', order, old, new))
            seq += 1
    return rows


def table_security(section, data, suppressed_global):
    """返回某张表的可见方程与受保护四格，用于最终安全性校验。"""
    facts, width = _table_facts(data)
    n_visible = f'{section}:n' not in suppressed_global
    equations = []
    for fact in facts:
        if f'{section}:{fact["id"]}' in suppressed_global or fact['value'] is None:
            continue
        if fact['rate'] and not n_visible:
            continue
        equations.append(fact['vec'])
    protected = [fact for fact in facts
                 if f'{section}:{fact["id"]}' in suppressed_global
                 and fact['category'] == 'cell']
    return equations, protected, width


class SummarizeTests(unittest.TestCase):
    def test_demo_shaped_split(self):
        cells = {
            'old_first': {'both_ok': 8, 'old_only': 1, 'new_only': 6, 'neither': 2},
            'new_first': {'both_ok': 8, 'old_only': 1, 'new_only': 6, 'neither': 2},
        }
        pairs = make_pairs(cells, {
            'old_first': [(None, True)],
            'new_first': [(True, None)],
        })
        sections = summarize_cells(pairs)
        for order in ('old_first', 'new_first'):
            group = sections[order]
            self.assertEqual(group['n'], 17)
            self.assertEqual(group['missing'], 1)
            self.assertEqual(group['registered'], 18)
            self.assertEqual(group['old_success'], 9)
            self.assertEqual(group['new_success'], 14)
            self.assertAlmostEqual(group['old_rate'], 9 / 17)
            self.assertAlmostEqual(group['new_rate'], 14 / 17)
        total = sections['total']
        self.assertEqual(total['n'], 34)
        self.assertEqual(total['missing'], 2)
        self.assertEqual(total['registered'], 36)
        self.assertEqual(total['cells'], {'both_ok': 16, 'old_only': 2, 'new_only': 12, 'neither': 4})

    def test_empty_group_rates_are_none(self):
        sections = summarize_cells([])
        for section in ('old_first', 'new_first', 'total'):
            self.assertEqual(sections[section]['n'], 0)
            self.assertIsNone(sections[section]['old_rate'])
            self.assertIsNone(sections[section]['new_rate'])


class MaskTests(unittest.TestCase):
    def test_primary_mask_small_cells(self):
        cells = {
            'old_first': {'both_ok': 10, 'old_only': 2, 'new_only': 7, 'neither': 5},
            'new_first': {'both_ok': 9, 'old_only': 3, 'new_only': 8, 'neither': 4},
        }
        sections = summarize_cells(make_pairs(cells))
        suppressed = build_mask(sections, held_groups=[])
        for section in ('old_first', 'new_first', 'total'):
            for key in CELL_KEYS:
                if 1 <= sections[section]['cells'][key] <= 4:
                    self.assertIn(f'{section}:{key}', suppressed)

    def test_margin_that_reveals_hidden_cell_is_suppressed(self):
        # old_only=3（被遮）。old_success = both_ok + old_only，
        # 若 old_success 与 both_ok 同时可见即可反推，连带遮罩必须切断路径。
        cells = {
            'old_first': {'both_ok': 12, 'old_only': 3, 'new_only': 9, 'neither': 6},
            'new_first': {'both_ok': 11, 'old_only': 8, 'new_only': 10, 'neither': 7},
        }
        sections = summarize_cells(make_pairs(cells))
        suppressed = build_mask(sections, held_groups=[])
        self.assertIn('old_first:old_only', suppressed)
        equations, protected, width = table_security('old_first', sections['old_first'], suppressed)
        self.assertTrue(protected)
        for fact in protected:
            self.assertFalse(_in_span(fact['vec'], equations, width))

    def test_rates_are_cheapest_suppression(self):
        # 只有一个小格 old_only=2。可暴露它的方程中，old_rate 的分子含 old_only，
        # 引擎应优先舍弃答对率，而不是先动组人数或其他四格。
        cells = {
            'old_first': {'both_ok': 10, 'old_only': 2, 'new_only': 8, 'neither': 6},
            'new_first': {'both_ok': 12, 'old_only': 7, 'new_only': 6, 'neither': 5},
        }
        sections = summarize_cells(make_pairs(cells))
        suppressed = build_mask(sections, held_groups=[])
        self.assertIn('old_first:old_rate', suppressed)
        # 受影响的率先于任何非率事实被遮：所有被遮非率事实的优先级都不低于 rate
        from src.services.brief_logic import CATEGORY_ORDER
        for name in suppressed:
            section, fid = name.split(':')
            if section != 'old_first':
                continue
            fact = next(item for item in _table_facts(sections['old_first'])[0] if item['id'] == fid)
            self.assertGreaterEqual(CATEGORY_ORDER[fact['category']], CATEGORY_ORDER['rate'])

    def test_convergence_leaves_no_determinable_hidden_cell(self):
        # 多张小格表的随机式组合，逐表验证收敛后无任何被遮四格可被反推。
        cells = {
            'old_first': {'both_ok': 5, 'old_only': 1, 'new_only': 4, 'neither': 2},
            'new_first': {'both_ok': 6, 'old_only': 2, 'new_only': 3, 'neither': 1},
        }
        sections = summarize_cells(make_pairs(cells))
        suppressed = build_mask(sections, held_groups=[])
        for section in ('old_first', 'new_first', 'total'):
            equations, protected, width = table_security(section, sections[section], suppressed)
            for fact in protected:
                self.assertFalse(_in_span(fact['vec'], equations, width),
                                 f'{section}:{fact["id"]} 仍可被反推')

    def test_large_numbers_kept_when_possible(self):
        cells = {
            'old_first': {'both_ok': 40, 'old_only': 12, 'new_only': 20, 'neither': 8},
            'new_first': {'both_ok': 38, 'old_only': 10, 'new_only': 22, 'neither': 9},
        }
        sections = summarize_cells(make_pairs(cells))
        suppressed = build_mask(sections, held_groups=[])
        # 没有 1–4 的四格，主遮罩为空；方程也不暴露任何变量，故无连带遮罩
        self.assertEqual(suppressed, set())

    def test_held_group_full_suppression_but_total_independent(self):
        cells = {
            'old_first': {'both_ok': 20, 'old_only': 10, 'new_only': 15, 'neither': 8},
            'new_first': {'both_ok': 18, 'old_only': 9, 'new_only': 17, 'neither': 7},
        }
        pairs = make_pairs(cells)
        sections = summarize_cells(pairs)
        suppressed = build_mask(sections, held_groups=['old_first'])
        group_facts = {name for name in suppressed if name.startswith('old_first:')}
        # 缓发读序整组遮罩（11 个事实）
        self.assertEqual(len(group_facts), 11)
        # 合计表独立保护：合计四格都 ≥5，无主遮罩，因此合计数字全部保留
        for key in CELL_KEYS:
            self.assertNotIn(f'total:{key}', suppressed)
        self.assertNotIn('total:n', suppressed)
        self.assertNotIn('total:old_rate', suppressed)
        # 另一张读序表不受影响
        self.assertFalse(any(name.startswith('new_first:') for name in suppressed))

    def test_rate_kept_when_n_suppressed(self):
        # n 被连带遮罩后，率无法还原精确分子，应当保留显示。
        cells = {
            'old_first': {'both_ok': 5, 'old_only': 1, 'new_only': 2, 'neither': 1},
            'new_first': {'both_ok': 20, 'old_only': 9, 'new_only': 11, 'neither': 8},
        }
        sections = summarize_cells(make_pairs(cells))
        suppressed = build_mask(sections, held_groups=[])
        if 'old_first:n' in suppressed:
            self.assertNotIn('old_first:old_rate', suppressed)
            self.assertNotIn('old_first:new_rate', suppressed)

    def test_build_sections_shape_and_flags(self):
        cells = {
            'old_first': {'both_ok': 5, 'old_only': 1, 'new_only': 4, 'neither': 2},
            'new_first': {'both_ok': 6, 'old_only': 2, 'new_only': 3, 'neither': 1},
        }
        sections = build_sections(make_pairs(cells), held_groups=['new_first'])
        self.assertTrue(sections['new_first']['held'])
        self.assertIsNone(sections['new_first']['n']['value'])
        self.assertTrue(sections['new_first']['n']['suppressed'])
        self.assertFalse(sections['old_first']['held'])
        self.assertFalse(sections['total']['held'])
        # 视图里每个数字都带 value/suppressed 结构
        for section in sections.values():
            self.assertEqual(set(section['cells']), set(CELL_KEYS))
            for item in list(section['cells'].values()) + [
                    section['n'], section['missing'], section['registered'],
                    section['old_success'], section['new_success'],
                    section['old_rate'], section['new_rate']]:
                self.assertIn('value', item)
                self.assertIn('suppressed', item)


class ChangeCountTests(unittest.TestCase):
    def test_joined_left_moved_and_order_switch(self):
        previous = [
            pair('A', 'old_first', True, True),       # 换格 -> old_only
            pair('B', 'old_first', True, False),      # 退出（当前缺测）
            pair('C', 'old_first', None, True),       # 入组（当前完整）
            pair('D', 'old_first', False, True),      # 读序调整：old_first 计退出
            pair('E', 'new_first', False, False),     # 保持
        ]
        current = [
            pair('A', 'old_first', True, False),
            pair('B', 'old_first', None, False),
            pair('C', 'old_first', False, False),
            pair('D', 'new_first', True, True),       # 在 new_first 计入组
            pair('E', 'new_first', False, False),
        ]
        changes = change_counts(previous, current)
        self.assertEqual(changes['old_first'], {'joined': 1, 'left': 2, 'moved': 1, 'total': 4})
        self.assertEqual(changes['new_first'], {'joined': 1, 'left': 0, 'moved': 0, 'total': 1})

    def test_no_change(self):
        previous = make_pairs({
            'old_first': {'both_ok': 4, 'old_only': 1, 'new_only': 2, 'neither': 1},
            'new_first': {'both_ok': 3, 'old_only': 2, 'new_only': 1, 'neither': 0},
        })
        changes = change_counts(previous, previous)
        for order in ('old_first', 'new_first'):
            self.assertEqual(changes[order]['total'], 0)
            self.assertFalse(is_small_change(changes[order]))

    def test_small_change_threshold(self):
        self.assertTrue(is_small_change({'joined': 1, 'left': 0, 'moved': 0, 'total': 1}))
        self.assertTrue(is_small_change({'joined': 2, 'left': 2, 'moved': 0, 'total': 4}))
        self.assertFalse(is_small_change({'joined': 0, 'left': 0, 'moved': 0, 'total': 0}))
        self.assertFalse(is_small_change({'joined': 3, 'left': 2, 'moved': 0, 'total': 5}))


if __name__ == '__main__':
    unittest.main()
