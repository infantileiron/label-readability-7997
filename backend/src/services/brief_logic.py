"""免登录简报的纯计算逻辑。

本模块只依赖 Python 标准库，便于在没有第三方依赖的环境下直接单元测试。
数据库与会话相关的封装放在 ``src.services.briefs``。

遮罩口径（小格保护）：
- 四格中人数为 1–4 的格子首先遮住（主遮罩）；
- 任何能由当前可见数字唯一反推出被遮四格的数字也要遮住（连带遮罩），
  连带遮罩按「答对率 → 边距（两版成功人数）→ 登记人数 → 组人数 → 其他四格」
  的顺序取舍，尽量保留信息量大的原始计数；
- 两张读序表与合计表分别独立做线性保护：缓发读序整组遮住后，合计表仍按
  自身四格独立判断，不会因为一个读序缓发就把全部合计数字抹掉；
- 判定使用精确有理数（Fraction）上的高斯消元，把每个可见计数视为一条
  关于底层变量的线性方程，通过行空间归属判断被遮变量是否被唯一确定；
- 答对率只在组人数可见时才按精确分子进入线性系统；组人数被遮后，
  四舍五入的百分比无法还原精确计数，率本身保留显示。
"""

from fractions import Fraction

CELL_KEYS = ('both_ok', 'old_only', 'new_only', 'neither')
CELL_LABELS = {
    'both_ok': '两版均成功',
    'old_only': '旧版独胜',
    'new_only': '样张独胜',
    'neither': '两版均未成功',
}
ORDER_GROUPS = ('old_first', 'new_first')
TOTAL = 'total'
SECTIONS = (*ORDER_GROUPS, TOTAL)
SMALL_MIN, SMALL_MAX = 1, 4

# 连带遮罩的取舍优先级：数值越小越先被遮（率最容易舍弃，四格最后动）。
CATEGORY_ORDER = {'rate': 0, 'margin': 1, 'registered': 2, 'n': 3, 'cell': 4}
RATE_KEYS = ('old_rate', 'new_rate')
RATE_NUMERATOR = {
    'old_rate': ('both_ok', 'old_only'),
    'new_rate': ('both_ok', 'new_only'),
}
MARGIN_VEC = {
    'old_success': ('both_ok', 'old_only'),
    'new_success': ('both_ok', 'new_only'),
}


def summarize_cells(pairs):
    """按读序汇总四格、完整人数、缺失与登记人数。"""
    sections = {}
    for order in ORDER_GROUPS:
        rows = [row for row in pairs if row['reading_order'] == order]
        cells = {key: 0 for key in CELL_KEYS}
        for row in rows:
            old, new = row['old_ok'], row['new_ok']
            if old is None or new is None:
                continue
            if old and new:
                cells['both_ok'] += 1
            elif old:
                cells['old_only'] += 1
            elif new:
                cells['new_only'] += 1
            else:
                cells['neither'] += 1
        complete = sum(cells.values())
        missing = len(rows) - complete
        sections[order] = {
            'cells': cells,
            'n': complete,
            'missing': missing,
            'registered': len(rows),
            'old_success': cells['both_ok'] + cells['old_only'],
            'new_success': cells['both_ok'] + cells['new_only'],
            'old_rate': (cells['both_ok'] + cells['old_only']) / complete if complete else None,
            'new_rate': (cells['both_ok'] + cells['new_only']) / complete if complete else None,
        }
    total_cells = {key: sum(sections[order]['cells'][key] for order in ORDER_GROUPS)
                   for key in CELL_KEYS}
    total_n = sum(sections[order]['n'] for order in ORDER_GROUPS)
    total_missing = sum(sections[order]['missing'] for order in ORDER_GROUPS)
    sections[TOTAL] = {
        'cells': total_cells,
        'n': total_n,
        'missing': total_missing,
        'registered': total_n + total_missing,
        'old_success': total_cells['both_ok'] + total_cells['old_only'],
        'new_success': total_cells['both_ok'] + total_cells['new_only'],
        'old_rate': (total_cells['both_ok'] + total_cells['old_only']) / total_n if total_n else None,
        'new_rate': (total_cells['both_ok'] + total_cells['new_only']) / total_n if total_n else None,
    }
    return sections


def _cell_map(visitor):
    old, new = visitor['old_ok'], visitor['new_ok']
    if old is None or new is None:
        return None
    if old and new:
        return 'both_ok'
    if old:
        return 'old_only'
    if new:
        return 'new_only'
    return 'neither'


def change_counts(previous_pairs, current_pairs):
    """统计每个读序相对一个历史版本的入组、退出、换格人数。

    返回 ``{读序: {'joined': 入组, 'left': 退出, 'moved': 换格, 'total': 合计}}``。

    - 入组：历史版缺测、当前版完整的观众；
    - 退出：历史版完整、当前版缺测的观众；
    - 换格：两版均完整但四格归属不同的观众；
    - 读序本身发生调整的观众，在两个读序里各自按入组/退出计一次。
    """
    result = {}
    for order in ORDER_GROUPS:
        old_rows = {row['visitor_code']: row for row in previous_pairs
                    if row['reading_order'] == order}
        new_rows = {row['visitor_code']: row for row in current_pairs
                    if row['reading_order'] == order}
        joined = left = moved = 0
        for code in set(old_rows) | set(new_rows):
            old_cell = _cell_map(old_rows[code]) if code in old_rows else None
            new_cell = _cell_map(new_rows[code]) if code in new_rows else None
            if old_cell is None and new_cell is not None:
                joined += 1
            elif old_cell is not None and new_cell is None:
                left += 1
            elif old_cell is not None and new_cell is not None and old_cell != new_cell:
                moved += 1
        result[order] = {'joined': joined, 'left': left, 'moved': moved,
                         'total': joined + left + moved}
    return result


def is_small_change(change):
    """1–4 人的入组、退出或换格需要缓发该读序的新数。"""
    return SMALL_MIN <= change['total'] <= SMALL_MAX


# --- 遮罩引擎（每个读序表与合计表独立保护） -------------------------------

def _rank(rows, width):
    """有理数矩阵的秩（就地消元）。"""
    rank = 0
    work = [row[:] for row in rows]
    for col in range(width):
        pivot = next((i for i in range(rank, len(work)) if work[i][col] != 0), None)
        if pivot is None:
            continue
        work[rank], work[pivot] = work[pivot], work[rank]
        pivot_value = work[rank][col]
        work[rank] = [value / pivot_value for value in work[rank]]
        for i in range(len(work)):
            if i != rank and work[i][col] != 0:
                factor = work[i][col]
                work[i] = [a - factor * b for a, b in zip(work[i], work[rank])]
        rank += 1
    return rank


def _in_span(vector, rows, width):
    """向量是否属于行空间（即可由可见方程线性表出，等于被唯一确定）。"""
    if not any(value != 0 for value in vector):
        return True
    return _rank(rows + [vector], width) == _rank(rows, width)


def _table_facts(data):
    """构造一张表的事实清单。变量为四个四格 + 一个缺失计数，共 5 维。"""
    variables = (*CELL_KEYS, 'missing')
    width = len(variables)
    index = {name: i for i, name in enumerate(variables)}

    def vec(keys):
        out = [Fraction(0)] * width
        for key in keys:
            out[index[key]] += Fraction(1)
        return out

    facts = []

    def add(fid, category, value, equation_keys, rate=False):
        facts.append({'id': fid, 'category': category, 'value': value,
                      'vec': vec(equation_keys), 'rate': rate})

    for key in CELL_KEYS:
        add(key, 'cell', data['cells'][key], (key,))
    add('n', 'n', data['n'], CELL_KEYS)
    add('missing', 'missing', data['missing'], ('missing',))
    add('registered', 'registered', data['registered'], (*CELL_KEYS, 'missing'))
    add('old_success', 'margin', data['old_success'], MARGIN_VEC['old_success'])
    add('new_success', 'margin', data['new_success'], MARGIN_VEC['new_success'])
    for rate_key in RATE_KEYS:
        # 率的方程向量就是其分子（两格之和）；仅当 n 可见时才进入线性系统。
        add(rate_key, 'rate', data[rate_key], RATE_NUMERATOR[rate_key], rate=True)
    return facts, width


def mask_table(data, held):
    """对单张表执行遮罩，返回被遮事实 id 集合。"""
    facts, width = _table_facts(data)
    suppressed = set()
    if held:
        # 缓发读序：整表所有数字遮住。
        return {fact['id'] for fact in facts}
    for fact in facts:
        if fact['category'] == 'cell' and SMALL_MIN <= fact['value'] <= SMALL_MAX:
            suppressed.add(fact['id'])

    def visible_equations():
        equations = []
        n_visible = 'n' not in suppressed
        for fact in facts:
            if fact['id'] in suppressed or fact['value'] is None:
                continue
            if fact['rate'] and not n_visible:
                # n 被遮后，四舍五入的率无法还原精确分子，不进入精确方程系统。
                continue
            equations.append(fact['vec'])
        return equations

    protected = [fact for fact in facts
                 if fact['id'] in suppressed and fact['category'] == 'cell']
    while True:
        equations = visible_equations()
        exposed = next((fact for fact in protected
                        if _in_span(fact['vec'], equations, width)), None)
        if exposed is None:
            break
        n_visible = 'n' not in suppressed
        candidates = [fact for fact in facts
                      if fact['id'] not in suppressed and fact['value'] is not None
                      and fact['category'] != 'missing'
                      and (not fact['rate'] or n_visible)]
        # 优先舍弃率，其次边距、登记、组人数，最后才动其他四格；
        # 同类里先遮数值小的，降低对整体读数的影响。
        candidates.sort(key=lambda fact: (CATEGORY_ORDER[fact['category']],
                                          abs(Fraction(fact['value'])), fact['id']))
        if not candidates:
            break
        suppressed.add(candidates[0]['id'])
    return suppressed


def build_mask(sections, held_groups):
    """对三张表分别遮罩，返回 ``section:fact`` 形式的全局标识集合。"""
    suppressed = set()
    for section in SECTIONS:
        held = section in held_groups
        for fid in mask_table(sections[section], held):
            suppressed.add(f'{section}:{fid}')
    return suppressed


def build_sections(pairs, held_groups):
    """汇总并应用遮罩，返回可直接序列化的简报分组数据。"""
    sections = summarize_cells(pairs)
    suppressed = build_mask(sections, held_groups)

    def view(section, key, value):
        return {'value': None if f'{section}:{key}' in suppressed else value,
                'suppressed': f'{section}:{key}' in suppressed}

    result = {}
    for section in SECTIONS:
        data = sections[section]
        held = section in held_groups
        result[section] = {
            'held': held,
            'cells': {key: view(section, key, data['cells'][key]) for key in CELL_KEYS},
            'n': view(section, 'n', data['n']),
            'missing': view(section, 'missing', data['missing']),
            'registered': view(section, 'registered', data['registered']),
            'old_success': view(section, 'old_success', data['old_success']),
            'new_success': view(section, 'new_success', data['new_success']),
            'old_rate': view(section, 'old_rate', data['old_rate']),
            'new_rate': view(section, 'new_rate', data['new_rate']),
        }
    return result
