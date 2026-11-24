"""免登录简报的签发与读取（数据库会话相关）。

签发在会签发布的同一事务内完成：
- 与该批次所有已发布运行的冻结快照逐读序比较，1–4 人的入组/退出/换格触发缓发；
- 缓发读序整组遮罩，其余数字经小格遮罩引擎处理；
- payload 一次性冻结，公开接口只凭不透明 token 读取，不含任何观众级记录；
- run_id 上的唯一约束与先查后插保证发布重试不重复签发。
"""

import secrets

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from src.services.audit import record
from src.services.brief_logic import ORDER_GROUPS, change_counts, is_small_change
from src.services.brief_payload import build_payload, brief_csv

__all__ = ('held_orders_for', 'issue_brief', 'get_public_brief', 'brief_csv')


def held_orders_for(db, study_id, current_pairs, exclude_run_id=None):
    """与全部已发布版本比较，返回缓发读序及逐版本变化明细。"""
    published = db.execute('''SELECT r.id, r.snapshot
                              FROM analysis_runs r
                              JOIN signoffs s ON s.run_id = r.id
                              WHERE r.study_id = %s AND s.decision = 'publish'
                                AND r.status = 'succeeded'
                                AND (%s IS NULL OR r.id <> %s)
                              ORDER BY r.id''',
                          (study_id, exclude_run_id, exclude_run_id)).fetchall()
    detail = {order: [] for order in ORDER_GROUPS}
    for row in published:
        changes = change_counts(row['snapshot']['pairs'], current_pairs)
        for order in ORDER_GROUPS:
            if is_small_change(changes[order]):
                detail[order].append({'run_id': row['id'], **changes[order]})
    held_orders = [order for order in ORDER_GROUPS if detail[order]]
    return held_orders, detail


def issue_brief(db, run):
    """在发布事务内签发简报；已签发时原样返回，保证重试幂等。"""
    existing = db.execute('SELECT * FROM public_briefs WHERE run_id = %s',
                          (run['id'],)).fetchone()
    if existing:
        return existing, False
    snapshot = db.execute('SELECT snapshot FROM analysis_runs WHERE id = %s',
                          (run['id'],)).fetchone()['snapshot']
    held_orders, held_detail = held_orders_for(db, run['study_id'], snapshot['pairs'],
                                               exclude_run_id=run['id'])
    payload = build_payload(run, snapshot, held_orders, held_detail)
    token = secrets.token_urlsafe(24)
    row = db.execute('''INSERT INTO public_briefs(token, run_id, study_id, revision,
                                                  held_orders, payload, created_by)
                        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *''',
                     (token, run['id'], run['study_id'], run['revision'],
                      held_orders, Jsonb(payload), run['signed_by'])).fetchone()
    record(db, run['signed_by'], 'brief.issued', str(run['id']),
           {'token': token, 'held_orders': held_orders})
    return row, True


def get_public_brief(db, token):
    row = db.execute('SELECT payload, created_at FROM public_briefs WHERE token = %s',
                     (token,)).fetchone()
    if not row:
        raise HTTPException(404, '简报不存在或链接已失效')
    return row['payload']
