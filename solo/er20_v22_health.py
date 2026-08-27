# -*- coding: utf-8 -*-
"""ER20 V2.2 批次健康度监控(PEA-Absorption t3: 失效计数器).

从 er20_v22_scores.db 汇总各 scan_date 最新快照的结构性 spread 指标,
用三个独立失效计数器判定策略是否进入降级观察:
  C1 信号枯竭   : 连续批次买入信号(next_day_signal)=0
  C2 质量衰减   : eligible 内 fusion_score 均值连续下行且跌幅超过阈值
  C3 spread 转负 : 头部(eligible top-K)与池内均值的分差跌破地板值
                  (结构代理;待 close{h} 收益回填后可切换为收益性 spread)

任一计数器命中 -> status=DEGRADED(降级观察)
两条及以上同时命中, 或单条命中且已持续过阈值 -> status=HALT(建议停用复核)
否则 NORMAL.

用法:
    python er20_v22_health.py                 # 默认库路径, 打印+落盘 json
    python er20_v22_health.py --db xx.db      # 指定库
"""
import argparse
import json
import os
import sqlite3
import sys

DB_PATH = r'D:\mystock\solo\report_daily\er20_v22_scores.db'
OUT_JSON = r'D:\mystock\solo\report_daily\er20_v22_health.json'

SIGNAL_DRY_STREAK = 2        # C1: 连续多少个有效批次零信号判失效
QUAL_DECLINE_STREAK = 2      # C2: fusion 均值连续下行的批次数
QUAL_DECLINE_MIN_PP = 5.0    # C2: 相对本季滚动基线的最大允许回落(pp)
HEAD_K = 10                  # C3: 头部取样宽度(eligible top-K)
SPREAD_FLOOR = 6.0           # C3: 头部-池均分差的地板值(pp), 跌破判 spread 转负


def load_batches(db):
    """聚合每个 scan_date 的全量记录为批次级指标.
    注: ts 列并非时间戳而是数值分数列, 同一 scan_date 内每股恰一条记录,
    因此直接对整日数据聚合, 不做快照去重."""
    con = sqlite3.connect(db)
    cur = con.cursor()
    dates = [r[0] for r in cur.execute(
        'SELECT DISTINCT scan_date FROM er20_v22_scores ORDER BY scan_date')]
    rows = []
    for d in dates:
        base = 'SELECT * FROM er20_v22_scores WHERE scan_date=?'
        recs = [dict(zip([c[0] for c in cur.description], r))
                for r in cur.execute(base, (d,))]
        elig = [x for x in recs if str(x.get('rank_eligible')) == '1']
        buys = [x for x in recs if str(x.get('next_day_signal')) == '1']
        passes = sum(1 for x in recs if str(x.get('filter_pass')) == '1')

        def f(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None

        fs = sorted(f(x.get('fusion_score')) for x in elig
                    if f(x.get('fusion_score')) is not None)
        pool_avg = sum(fs) / len(fs) if fs else None
        head = fs[-min(HEAD_K, len(fs)):] if fs else []
        head_avg = sum(head) / len(head) if head else None
        rows.append({
            'scan_date': d, 'n_total': len(recs), 'n_eligible': len(elig),
            'n_filter_pass': passes if len(recs) else None,
            'pass_rate': round(passes / len(recs), 3) if recs else None,
            'n_buy': len(buys),
            'elig_fusion_mean': round(pool_avg, 2) if pool_avg is not None else None,
            'head_fusion_mean': round(head_avg, 2) if head_avg is not None else None,
            'head_pool_spread': round(head_avg - pool_avg, 2)
                                if head_avg is not None and pool_avg is not None else None,
            'buy_codes': [x['name'] or x['ts_code'] for x in buys],
        })
    con.close()
    return rows


def run_counters(batches):
    """对最后 N 个批次套用三条失效规则, 返回计数明细与状态."""
    # 有效批次 = 有 rank_eligible 数据的批次(NULL 全空视为该日无快照, 跳过)
    valid = [b for b in batches if b['n_eligible'] and b['n_eligible'] > 0]
    trail = valid[-max(SIGNAL_DRY_STREAK, QUAL_DECLINE_STREAK + 1):]

    c1_dry = 0
    for b in reversed(trail):
        if b['n_buy'] == 0:
            c1_dry += 1
        else:
            break

    c2_decline = 0
    means = [b['elig_fusion_mean'] for b in trail]
    for i in range(len(means) - 1, 0, -1):
        if means[i] is not None and means[i - 1] is not None and means[i] < means[i - 1]:
            c2_decline += 1
        else:
            break

    baseline = [m for m in means if m is not None]
    qual_drop = None
    if baseline:
        ref = sum(baseline[:-c2_decline]) / max(len(baseline) - c2_decline, 1) \
            if c2_decline and len(baseline) > c2_decline else baseline[0]
        last = baseline[-1]
        if last is not None and ref is not None:
            qual_drop = round(ref - last, 2)

    spreads = [b['head_pool_spread'] for b in trail if b['head_pool_spread'] is not None]
    c3_negative = bool(spreads) and spreads[-1] < SPREAD_FLOOR

    hits = {
        'C1_signal_dry': {'streak': c1_dry, 'threshold': SIGNAL_DRY_STREAK,
                          'hit': c1_dry >= SIGNAL_DRY_STREAK},
        'C2_quality_decline': {'streak': c2_decline, 'threshold': QUAL_DECLINE_STREAK,
                               'drop_pp_vs_baseline': qual_drop,
                               'hit': c2_decline >= QUAL_DECLINE_STREAK
                                      and (qual_drop is None or qual_drop >= QUAL_DECLINE_MIN_PP)},
        'C3_spread_negative': {'last_spread': spreads[-1] if spreads else None,
                               'floor': SPREAD_FLOOR, 'hit': c3_negative},
    }
    n_hit = sum(1 for v in hits.values() if v['hit'])
    if n_hit >= 2:
        status = 'HALT'
    elif n_hit == 1:
        status = 'DEGRADED'
    else:
        status = 'NORMAL'
    return hits, status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=DB_PATH)
    ap.add_argument('--json', default=OUT_JSON)
    args = ap.parse_args()

    batches = load_batches(args.db)
    hits, status = run_counters(batches)

    print('=' * 78)
    print('ER20 V2.2 批次健康度 (每 scan_date 全量聚合)')
    print('=' * 78)
    hdr = ['date', 'total', 'elig', 'fpass', 'buys', 'eligFus', 'headFus',
           'spread', 'buys...']
    print('%-9s %5s %5s %6s %5s %8s %8s %7s  %s' % tuple(hdr))
    for b in batches:
        fp = '-' if b['n_filter_pass'] in (None, 0) else b['n_filter_pass']
        bm = ','.join(b['buy_codes']) if b['n_buy'] else ''
        print('%-9s %5s %5s %6s %5s %8s %8s %7s  %s' % (
            b['scan_date'], b['n_total'], b['n_eligible'], fp, b['n_buy'],
            b['elig_fusion_mean'], b['head_fusion_mean'], b['head_pool_spread'], bm))

    print('-' * 78)
    for k, v in hits.items():
        mark = '[X]' if v['hit'] else '[ ]'
        extra = {kk: vv for kk, vv in v.items() if kk not in ('hit',)}
        print(f'{mark} {k}: {extra}')
    print(f'=> STATUS: {status}')
    if status == 'DEGRADED':
        print('   建议: 降级观察 — 新仓位减半/仅保留现有持仓跟踪, 待下一个批次恢复再升级.')
    elif status == 'HALT':
        print('   建议: 停用复核 — 暂停新增信号, 全面复盘因子与过滤池后重启.')

    with open(args.json, 'w', encoding='utf-8') as f:
        json.dump({'status': status, 'counters': hits, 'batches': batches},
                  f, ensure_ascii=False, indent=2)
    print(f'[落盘] {args.json}')


if __name__ == '__main__':
    main()
