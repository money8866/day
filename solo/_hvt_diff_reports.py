# -*- coding: utf-8 -*-
"""一次性工具：对比两份 hvt_bull_{date}.json（任务5）

跳过已知非确定性/元信息键，逐事件对比业务字段。
usage: python _hvt_diff_reports.py <a.json> <b.json>
"""
import json
import sys

SKIP_TOP = {'generated_at', 'report_time', 'version', 'remark', 'note'}


def _norm(v):
    if isinstance(v, float):
        return round(v, 6)
    return v


def _cmp_event(ev_a, ev_b):
    keys = set(ev_a) | set(ev_b)
    diffs = {}
    for k in keys:
        if k in SKIP_TOP:
            continue
        va, vb = ev_a.get(k), ev_b.get(k)
        if isinstance(va, dict) and isinstance(vb, dict):
            if va != vb:
                diffs[k] = ('dict-diff', va, vb)
            continue
        if isinstance(va, list) and isinstance(vb, list):
            if va != vb:
                diffs[k] = ('list-diff', va, vb)
            continue
        if va != vb:
            diffs[k] = (va, vb)
    return diffs


def main():
    a_path, b_path = sys.argv[1], sys.argv[2]
    a = json.load(open(a_path, encoding='utf-8'))
    b = json.load(open(b_path, encoding='utf-8'))

    print(f"A={a_path}\nB={b_path}")
    for k in ('trade_date', 'universe', 'n_events', 'n_new_today', 'top_n'):
        if k in a or k in b:
            print(f'  top.{k}: A={a.get(k)!r}  B={b.get(k)!r}')

    sa = {e.get('ts_code'): e for e in a.get('events', [])}
    sb = {e.get('ts_code'): e for e in b.get('events', [])}
    only_a = sorted(set(sa) - set(sb))
    only_b = sorted(set(sb) - set(sa))
    print(f'  events: A={len(sa)} B={len(sb)} 仅A={only_a} 仅B={only_b}')

    n_event_diff = 0
    for code in sorted(set(sa) & set(sb)):
        diffs = _cmp_event(sa[code], sb[code])
        if diffs:
            n_event_diff += 1
            print(f'  EVENT {code} 差异字段 {len(diffs)}:')
            for k, v in list(diffs.items())[:12]:
                print(f'    - {k}: A={v[0] if isinstance(v, tuple) else v}')
    print(f'  共同事件中有字段差异的: {n_event_diff}')

    # 聚合分布
    for k in ('all_states',):
        if k in a and k in b:
            print(f'  {k}: A={a[k]}')
            print(f'       B={b[k]}  -> 相等={a[k] == b[k]}')
    if 'te_buy_pool' in a or 'te_buy_pool' in b:
        ta = sorted(x.get('ts_code') for x in a.get('te_buy_pool', []))
        tb = sorted(x.get('ts_code') for x in b.get('te_buy_pool', []))
        print(f'  te_buy_pool: A={ta} B={tb} 相等={ta == tb}')


if __name__ == '__main__':
    main()
