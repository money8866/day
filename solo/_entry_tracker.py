#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
每日入场信号记分卡（供盘后复盘跟踪「0仓位后何时能入场」）

口径与 2026-10-18 晚间实证一致（_eval_entry_timing.py v1）：
  1. breakdown 状态机 = position_engine_v95._detect_key_low_breakdown（滚动锚点，无未来函数）
  2. 三道闸：① 收复关键低点/底分型 ② level1 加仓确认（3选2）③ level2（4选3）+regime升级
  3. 历史节奏：2024-01~2026-10 全样本 breakdown 区间统计

用法：
  python _entry_tracker.py                  # 人类可读输出（默认取最近交易日）
  python _entry_tracker.py --date 20261016  # 指定交易日
  python _entry_tracker.py --json           # 输出当日快照 JSON（供上层解析）
  python _entry_tracker.py --save           # 记分卡写入 report_daily/（md + json 快照）

输出文件（--save）：
  report_daily/entry_tracker_{date}.md    人类可读记分卡（含与昨日对比）
  report_daily/entry_tracker_{date}.json  当日快照（明日对比的基线）
"""
import argparse
import glob
import json
import os
import sqlite3
import sys
from io import StringIO

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from position_engine_v95 import _detect_key_low_breakdown  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOLO = os.path.dirname(os.path.abspath(__file__))
CACHE_DB = os.path.join(SOLO, 'cache_backbone_tushare', 'cache.db')
MA_DB = os.path.join(SOLO, 'cache_backbone_tushare', 'market_analysis.db')
REPORT_DIR = os.path.join(ROOT, 'report_daily')
WINDOW = 120  # breakdown 检测滚动窗口（与 v1 实证一致）

# 三道闸触发线（与 position_engine_v95._check_level1/2_add 一致）
L1_LINES = {'up': 55, 'zb': 25, 'mainline_top1': 60}
L2_LINES = {'up': 60, 'zb': 20, 'zt': 60, 'top1': 80}


# ---------------------------------------------------------------- 数据加载
def load_sh_index():
    """上证指数全历史（hist_* + 2026 kline），返回 DataFrame(index=trade_date)"""
    conn = sqlite3.connect(CACHE_DB)
    sh = {}
    df = pd.read_sql_query("SELECT trade_date, open, high, low, close FROM hist_000001_SH", conn)
    for _, r in df.iterrows():
        sh[str(r['trade_date'])] = (float(r['open']), float(r['high']), float(r['low']), float(r['close']))
    cur = conn.cursor()
    cur.execute("SELECT key, data FROM cache_data WHERE key LIKE 'tsc_index_kline_ts_code_000001.SH_%'")
    for key, data in cur.fetchall():
        df2 = pd.read_csv(StringIO(data))
        for _, r in df2.iterrows():
            sh[str(r['trade_date'])] = (float(r['open']), float(r['high']), float(r['low']), float(r['close']))
    conn.close()
    dates = sorted(sh.keys())
    return pd.DataFrame([sh[d] for d in dates], index=pd.Index(dates, name='trade_date'),
                        columns=['open', 'high', 'low', 'close']).sort_index()


def load_limit_stats():
    """全市场涨跌停统计 {date: dict}"""
    stats = {}
    for f in glob.glob(os.path.join(SOLO, 'cache_daily', 'full_market_stats_*.json')):
        d = os.path.basename(f).replace('full_market_stats_', '').replace('.json', '')
        try:
            j = json.load(open(f, encoding='utf-8'))
            stats[d] = {'zb': j.get('broken_rate', 0), 'zt': j.get('zt_count', 0),
                        'up': j.get('up_ratio', 50), 'h': j.get('max_limit_height', 0)}
        except Exception:
            pass
    return stats


def breakdown_history(SH):
    """全样本逐日 breakdown 状态（扩展窗口滚动判定）→ (active Series, key_low Series)"""
    active = pd.Series(False, index=SH.index)
    key_low_s = pd.Series(np.nan, index=SH.index)
    for t in range(WINDOW, len(SH)):
        win = SH.iloc[t - WINDOW + 1:t + 1].reset_index()
        res = _detect_key_low_breakdown({'上证指数': win})
        active.iloc[t] = res['active']
        key_low_s.iloc[t] = res['key_low'] if res['key_low'] else np.nan
    return active, key_low_s


# ---------------------------------------------------------------- 记分卡
def build_scorecard(date):
    """构建当日入场记分卡 dict"""
    SH = load_sh_index()
    stats = load_limit_stats()
    active, key_low_s = breakdown_history(SH)

    if date not in SH.index:
        raise SystemExit('日期 {} 不在行情样本内（最新 {}）'.format(date, SH.index[-1]))
    di = SH.index.get_loc(date)

    # --- 闸一：breakdown 状态 ---
    seg_start = None
    for t in range(di, -1, -1):
        if active.iloc[t]:
            seg_start = SH.index[t]
        else:
            break
    dur = (di - SH.index.get_loc(seg_start) + 1) if seg_start else 0
    brk = {
        'active': bool(active.iloc[di]),
        'key_low': None if pd.isna(key_low_s.iloc[di]) else round(float(key_low_s.iloc[di]), 2),
        'close': round(float(SH['close'].iloc[di]), 2),
        'seg_start': seg_start,
        'seg_days': dur,
        'need_pct': None,
    }
    if brk['active'] and brk['key_low']:
        brk['need_pct'] = round((brk['key_low'] / brk['close'] - 1) * 100, 2)

    # --- 历史节奏（全样本区间统计，含进行中的当前段） ---
    segs, in_seg = [], False
    for t in range(1, len(SH)):
        d = SH.index[t]
        if active.iloc[t] and not in_seg:
            s, in_seg = d, True
        elif not active.iloc[t] and in_seg:
            segs.append((s, SH.index[t - 1])); in_seg = False
    if in_seg:
        segs.append((s, SH.index[-1]))
    done = [(a, b) for a, b in segs if b != SH.index[-1]]
    hist = {
        'segments': len(segs),
        'done_segments': len(done),
        'dur_median': int(np.median([SH.index.get_loc(b) - SH.index.get_loc(a) + 1 for a, b in done])) if done else None,
        'dur_min': min(SH.index.get_loc(b) - SH.index.get_loc(a) + 1 for a, b in done) if done else None,
        'dur_max': max(SH.index.get_loc(b) - SH.index.get_loc(a) + 1 for a, b in done) if done else None,
        'relief_r5_mean': None, 'relief_r5_win': None, 'relief_n': 0,
    }
    r5s = []
    for a, b in done:
        bi = SH.index.get_loc(b)
        if bi + 5 < len(SH):
            r5s.append((SH['close'].iloc[bi + 5] / SH['close'].iloc[bi] - 1) * 100)
    if r5s:
        hist['relief_r5_mean'] = round(float(np.mean(r5s)), 2)
        hist['relief_r5_win'] = round(float((np.array(r5s) > 0).mean() * 100), 0)
        hist['relief_n'] = len(r5s)

    # --- 闸二/三：情绪指标 ---
    s = stats.get(date, {})
    up = s.get('up', 0); zb = s.get('zb', 0); zt = s.get('zt', 0)
    l1 = {
        'up_gt55': up > L1_LINES['up'],
        'zb_lt25': zb < L1_LINES['zb'],
        'mainline_strong': False,  # 主线强度需主题数据，盘后记分卡以 top1 缺失记为未满足
        'l1_pass': False,
    }
    l1['l1_pass'] = sum([l1['up_gt55'], l1['zb_lt25'], l1['mainline_strong']]) >= 2
    l2 = {
        'up_gt60': up > L2_LINES['up'],
        'zb_lt20': zb < L2_LINES['zb'],
        'zt_gt60': zt > L2_LINES['zt'],
        'top1_gt80': False,
        'l2_pass': False,
    }
    l2['l2_pass'] = sum([l2['up_gt60'], l2['zb_lt20'], l2['zt_gt60'], l2['top1_gt80']]) >= 3

    return {
        'date': date,
        'sh_close': brk['close'],
        'breakdown': brk,
        'history': hist,
        'sentiment': {'up': up, 'zb': zb, 'zt': zt, 'height': s.get('h', 0)},
        'level1': l1,
        'level2': l2,
        'gate1_clear': not brk['active'],
        'gate2_clear': l1['l1_pass'],
        'gate3_clear': l2['l2_pass'],
    }


def load_prev_card(date):
    """读最近一个早于 date 的快照，用于 Δ 对比"""
    files = sorted(glob.glob(os.path.join(REPORT_DIR, 'entry_tracker_*.json')))
    prev = None
    for f in files:
        d = os.path.basename(f).replace('entry_tracker_', '').replace('.json', '')
        if d < date:
            try:
                prev = json.load(open(f, encoding='utf-8'))
            except Exception:
                pass
    return prev


def render_markdown(card, prev):
    """人类可读记分卡（含与昨日 Δ）"""
    L = []
    brk, hist, s = card['breakdown'], card['history'], card['sentiment']
    L.append('')
    L.append('=' * 46)
    L.append('📊 入场信号记分卡（{}）'.format(card['date']))
    L.append('=' * 46)

    # 闸一
    if brk['active']:
        need = brk['need_pct']
        d_need = ''
        if prev and prev.get('breakdown', {}).get('need_pct') is not None:
            d_need = '（昨日 {:+.2f}%，{}）'.format(
                prev['breakdown']['need_pct'],
                '近 {:.2f}pp'.format(prev['breakdown']['need_pct'] - need) if need < prev['breakdown']['need_pct'] else '远 {:.2f}pp'.format(need - prev['breakdown']['need_pct']))
        L.append('闸一 breakdown：🔴 空仓中（{} 跌破关键低点 {} 未收复，已 {} 天）'.format(
            brk['seg_start'], brk['key_low'], brk['seg_days']))
        L.append('  解锁：收盘 > {}（当前 {}，需 {:+.2f}%）{}'.format(
            brk['key_low'], brk['close'], need, d_need))
        if hist['dur_median'] is not None:
            L.append('  历史节奏：中位 {} 天解除（{}-{} 天，n={} 段）；本次已 {} 天'.format(
                hist['dur_median'], hist['dur_min'], hist['dur_max'],
                hist['done_segments'], brk['seg_days']))
    else:
        L.append('闸一 breakdown：🟢 已解除（可脱离强制空仓）')

    # 闸二
    l1 = card['level1']
    n1 = sum([l1['up_gt55'], l1['zb_lt25'], l1['mainline_strong']])
    L.append('闸二 一级加仓（3选2，当前 {}/3）：{}'.format(n1, '🟢 已满足' if l1['l1_pass'] else '🔴 未满足'))
    d_zb = ''
    if prev and prev.get('sentiment', {}).get('zb') is not None:
        d = s['zb'] - prev['sentiment']['zb']
        d_zb = '（昨日 {:.1f}%，{:+.1f}pp）'.format(prev['sentiment']['zb'], d)
    L.append('  ① 涨家>55%：{}（当前 {:.0f}%）  ② 炸板率<25%：{}（当前 {:.1f}%{}）  ③ 主线强+top1>60：❌（弱）'.format(
        '✓' if l1['up_gt55'] else '✗', s['up'],
        '✓' if l1['zb_lt25'] else '✗', s['zb'], d_zb))

    # 闸三
    l2 = card['level2']
    n2 = sum([l2['up_gt60'], l2['zb_lt20'], l2['zt_gt60'], l2['top1_gt80']])
    L.append('闸三 二级加仓（4选3，当前 {}/4）：{}'.format(n2, '🟢 已满足' if l2['l2_pass'] else '🔴 未满足'))
    L.append('  ① 涨家>60%：{}（{:.0f}%）② 炸板<20%：{}（{:.1f}%）③ 涨停>60：{}（{:.0f}家）④ top1>80：❌'.format(
        '✓' if l2['up_gt60'] else '✗', s['up'],
        '✓' if l2['zb_lt20'] else '✗', s['zb'],
        '✓' if l2['zt_gt60'] else '✗', s['zt']))

    # 结论
    if card['gate1_clear'] and card['gate2_clear']:
        concl = '🟢 闸一+闸二已过 → 可试错仓（15%~25%），按 +8/日限速爬升'
    elif card['gate1_clear']:
        concl = '🟡 闸一已过，闸二未过 → 维持低仓/空仓，盯炸板率与主线'
    else:
        concl = '🔴 闸一未过 → 强制空仓等待（收复关键低点或底分型）'
    L.append('结论：{}'.format(concl))
    if hist['relief_r5_mean'] is not None:
        L.append('参考：历史 breakdown 解除后未来5日 {:+.2f}%、胜率 {:.0f}%（n={}）'.format(
            hist['relief_r5_mean'], hist['relief_r5_win'], hist['relief_n']))
    L.append('=' * 46)
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None, help='交易日 YYYYMMDD，默认样本最后一日')
    ap.add_argument('--json', action='store_true', help='输出当日快照 JSON')
    ap.add_argument('--save', action='store_true', help='写入 report_daily/（md+json）')
    a = ap.parse_args()

    SH = load_sh_index()
    date = a.date or SH.index[-1]
    card = build_scorecard(date)
    prev = load_prev_card(date)

    if a.json:
        print(json.dumps(card, ensure_ascii=False, default=str))
    elif a.save:
        os.makedirs(REPORT_DIR, exist_ok=True)
        md = os.path.join(REPORT_DIR, 'entry_tracker_{}.md'.format(date))
        with open(md, 'w', encoding='utf-8') as f:
            f.write(render_markdown(card, prev))
        json.dump(card, open(os.path.join(REPORT_DIR, 'entry_tracker_{}.json'.format(date)),
                             'w', encoding='utf-8'), ensure_ascii=False, default=str, indent=1)
        print('saved:', md)
        print(render_markdown(card, prev))
    else:
        print(render_markdown(card, prev))


if __name__ == '__main__':
    main()
