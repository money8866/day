#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地选股库 -> 飞书多维表格「选股跟踪」单向同步。

本地 SQLite 是唯一数据源，飞书仅用于展示与协作，批注/修改不回写。
每次同步最近 N 天（默认 7 天）滚动窗口；按业务键幂等 upsert：
先拉取飞书已有记录建立 (键 -> record_id) 映射，存在则批量更新，不存在则批量创建。

用法:
    python feishu_sync.py               # 同步最近 7 天
    python feishu_sync.py --days 30     # 指定窗口天数
    python feishu_sync.py --dry-run     # 只读取并打印计划，不写飞书
    python feishu_sync.py --only picks  # 只同步指定表: picks | daily
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta

import pandas as pd

import stock_pick_db as spdb

BASE_TOKEN = 'FzqWbuzEGarcXUsBTAYc5l3bnUf'
BASE_URL = 'https://ucnj0s2qu471.feishu.cn/base/' + BASE_TOKEN
TBL_PICKS = 'tblZKY73Tzg2GSuA'
TBL_DAILY = 'tblrhqifjoEdjuMA'
BATCH_SIZE = 200
RETRY_SNIPPETS = ('1254291', 'TooManyRequest', 'too many request', 'rate limit')

PICK_KEY = [('选股日期', 'date'), ('策略', 'plain'), ('股票代码', 'plain')]
DAILY_KEY = [('日期', 'date'), ('策略', 'plain')]


def _isnull(v):
    try:
        return v is None or bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _iso(d):
    d = str(d)[:8]
    return f'{d[:4]}-{d[4:6]}-{d[6:8]}'


def _num(v, nd=2):
    if _isnull(v):
        return None
    try:
        return round(float(v), nd)
    except (TypeError, ValueError):
        return None


def _txt(v):
    if _isnull(v):
        return None
    s = str(v).strip()
    return s or None


def _sel(v):
    s = _txt(v)
    return [s] if s else None


def pick_fields(row):
    f = {
        '股票名称': _txt(row.get('stock_name')) or _txt(row.get('ts_code')),
        '选股日期': _iso(row.get('pick_date')),
        '策略': _sel(row.get('strategy_name') or row.get('strategy_id')),
        '股票代码': _txt(row.get('ts_code')),
        '收盘价': _num(row.get('close')),
        '当日涨跌幅%': _num(row.get('pct_chg')),
        '信号': _sel(row.get('signal')),
        '操作建议': _sel(row.get('action')),
        '评分': _num(row.get('score'), 1),
        '排名': _num(row.get('rank_no'), 0),
        '行业': _txt(row.get('industry')),
        '入选理由': _txt(row.get('reason')),
        '止损价': _num(row.get('stop_price')),
        '目标价': _num(row.get('target_price')),
        '最大涨幅%': _num(row.get('max_gain')),
        '最大回撤%': _num(row.get('max_drawdown')),
        '状态': _sel(row.get('status')),
    }
    for h in (1, 3, 5, 10, 20):
        f[f'{h}日收益%'] = _num(row.get(f'ret_{h}d'))
    return {k: v for k, v in f.items() if v is not None}


def daily_fields(row, date_iso):
    f = {
        '策略': _txt(row.get('strategy_name') or row.get('strategy_id')),
        '日期': date_iso,
        '入选数': _num(row.get('total'), 0),
        '平均最大涨幅%': _num(row.get('avg_max_gain')),
        '平均最大回撤%': _num(row.get('avg_max_dd')),
    }
    for h in (1, 3, 5, 10, 20):
        f[f'{h}日胜率%'] = _num(row.get(f'ret_{h}d_win'), 1)
        f[f'{h}日均收益%'] = _num(row.get(f'ret_{h}d_avg'), 2)
    return {k: v for k, v in f.items() if v is not None}


def _unwrap(v):
    if isinstance(v, list):
        v = v[0] if v else None
    if isinstance(v, dict):
        v = v.get('text') or v.get('name') or v.get('value')
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _epoch_to_date(v):
    n = int(v)
    ts = n / (1000 if n > 10**11 else 1)
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d')


def key_from_vals(vals, spec):
    out = []
    for name, kind in spec:
        v = _unwrap(vals.get(name))
        if v is not None and kind == 'date':
            v = v[:10]
        out.append(v)
    return tuple(out)


def key_from_remote(fields, spec):
    out = []
    for name, kind in spec:
        v = _unwrap(fields.get(name))
        if v is not None and kind == 'date':
            if v.isdigit():
                v = _epoch_to_date(v)
            v = v[:10]
        out.append(v)
    return tuple(out)


def _lark(base_args, payload=None):
    cmd = ['lark-cli', 'base'] + base_args + ['--as', 'user', '--format', 'json']
    if payload is not None:
        cmd += ['--json', json.dumps(payload, ensure_ascii=False)]
    last = ''
    for attempt in range(4):
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=180)
        out = (p.stdout or '').strip() or (p.stderr or '').strip()
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            raise RuntimeError(f'lark-cli 输出解析失败: {out[:300]}')
        if data.get('ok'):
            return data.get('data')
        last = json.dumps(data, ensure_ascii=False)
        if any(s in out for s in RETRY_SNIPPETS) and attempt < 3:
            time.sleep(2 * (attempt + 1))
            continue
        raise RuntimeError(f'lark-cli 调用失败: {last[:400]}')
    raise RuntimeError(f'lark-cli 重试耗尽: {last[:400]}')


def list_records(table_id, spec):
    """拉取表中全部记录，返回 {业务键: (record_id, fields)}。"""
    remote, offset = {}, 0
    while True:
        data = _lark(['+record-list', '--base-token', BASE_TOKEN,
                      '--table-id', table_id, '--offset', str(offset), '--limit', '200'])
        recs = data.get('data') or []
        rids = data.get('record_id_list') or []
        names = data.get('fields') or []
        for i, row in enumerate(recs):
            if not isinstance(row, list):
                continue
            fields = dict(zip(names, row))
            rid = rids[i] if i < len(rids) else None
            k = key_from_remote(fields, spec)
            if rid and all(x is not None for x in k):
                remote[k] = (rid, fields)
        if data.get('has_more') and recs:
            offset += len(recs)
        else:
            break
    return remote


def _local_select_values():
    df = spdb.get_picks(limit=1000000)
    if df.empty:
        return {}
    sv = df['strategy_name'].where(df['strategy_name'].notna(), df['strategy_id'])

    def uniq(col):
        return sorted({str(x).strip() for x in col.dropna() if str(x).strip()})

    return {'策略': uniq(sv), '信号': uniq(df['signal']),
            '操作建议': uniq(df['action']), '状态': uniq(df['status'])}


def ensure_select_options(table_id, dry=False):
    vals = _local_select_values()
    if not vals:
        return
    data = _lark(['+field-list', '--base-token', BASE_TOKEN, '--table-id', table_id])
    for fld in data.get('fields') or []:
        name = fld.get('name')
        if fld.get('type') != 'select' or name not in vals:
            continue
        opts = fld.get('options') or []
        have = {o.get('name') for o in opts}
        new = [v for v in vals[name] if v not in have]
        if not new:
            continue
        print(f'  选项补充 [{name}]: +{", ".join(new)}')
        if dry:
            continue
        payload = {'name': name, 'type': 'select',
                   'multiple': bool(fld.get('multiple')),
                   'options': opts + [{'name': v} for v in new]}
        _lark(['+field-update', '--base-token', BASE_TOKEN, '--table-id', table_id,
               '--field-id', name, '--yes'], payload)
        time.sleep(3)


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def sync_table(table_id, local_vals, spec, label, dry=False):
    print(f'\n== {label} ({table_id}) ==')
    print(f'本地待同步行数: {len(local_vals)}')
    remote = list_records(table_id, spec)
    print(f'飞书已有记录: {len(remote)}')
    to_create, to_update = [], []
    for vals in local_vals:
        k = key_from_vals(vals, spec)
        if not all(x is not None for x in k):
            print(f'[跳过] 业务键不完整: {vals}')
            continue
        if k in remote:
            to_update.append((remote[k][0], vals))
        else:
            to_create.append(vals)
    print(f'计划新建 {len(to_create)} 条 / 更新 {len(to_update)} 条')
    if dry:
        print('[dry-run] 不执行写入')
        return
    for i, chunk in enumerate(_chunks(to_create, BATCH_SIZE), 1):
        _lark(['+record-batch-create', '--base-token', BASE_TOKEN, '--table-id', table_id],
              {'create_records': chunk})
        print(f'  创建批次 {i}: {len(chunk)} 条')
    for i, chunk in enumerate(_chunks(to_update, BATCH_SIZE), 1):
        payload = {'update_records': {rid: vals for rid, vals in chunk}}
        _lark(['+record-batch-update', '--base-token', BASE_TOKEN, '--table-id', table_id], payload)
        print(f'  更新批次 {i}: {len(chunk)} 条')
    print(f'[{label}] 同步完成')


def load_pick_rows(days):
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=days - 1)).strftime('%Y%m%d')
    df = spdb.get_picks(start_date=start, end_date=end, limit=1000000)
    return [pick_fields(r) for _, r in df.iterrows()]


def load_daily_rows(days):
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=days - 1)).strftime('%Y%m%d')
    df = spdb.get_picks(start_date=start, end_date=end, limit=1000000)
    rows = []
    for d in sorted(df['pick_date'].astype(str).unique()):
        d8 = str(d)[:8]
        st = spdb.get_strategy_stats(start_date=d8, end_date=d8)
        iso = _iso(d8)
        for _, r in st.iterrows():
            rows.append(daily_fields(r, iso))
    return rows


def main():
    ap = argparse.ArgumentParser(description='同步本地选股库到飞书多维表格')
    ap.add_argument('--days', type=int, default=7, help='同步最近 N 天窗口，默认 7')
    ap.add_argument('--dry-run', action='store_true', help='只读取并打印计划，不写飞书')
    ap.add_argument('--only', choices=['picks', 'daily'], default=None, help='只同步指定表')
    a = ap.parse_args()

    print(f'Base: {BASE_URL}')
    print(f'窗口: 最近 {a.days} 天 (含今天 {datetime.now():%Y-%m-%d})')
    if a.only in (None, 'picks'):
        ensure_select_options(TBL_PICKS, a.dry_run)
        sync_table(TBL_PICKS, load_pick_rows(a.days), PICK_KEY, '选股记录', a.dry_run)
    if a.only in (None, 'daily'):
        sync_table(TBL_DAILY, load_daily_rows(a.days), DAILY_KEY, '策略日报', a.dry_run)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'[错误] {e}', file=sys.stderr)
        sys.exit(1)
