# -*- coding: utf-8 -*-
"""主线龙头首次回踩策略 - 历史信号前瞻胜率统计

对 mainline_pullback/output/pullback_signals_*.csv 存档信号做前瞻验证：
  - 买入口径: 信号日 T+1 开盘价买入（可执行）
  - 统计: T+1/T+2/T+3/T+5/最新 收盘收益、区间最高/最低、止损/止盈命中
  - 分层: 全体 / READY信号 / 评分段 / buy_readiness 段
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

import stock_cache as sc

SIGNAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'mainline_pullback', 'output')
END_DATE = '20260825'


def load_signals():
    frames = []
    for f in sorted(glob.glob(os.path.join(SIGNAL_DIR, 'pullback_signals_*.csv'))):
        d = os.path.basename(f).replace('pullback_signals_', '').replace('.csv', '')
        if d >= END_DATE:
            continue
        df = pd.read_csv(f, dtype={'ts_code': str, 'trade_date': str})
        df['trade_date'] = df['trade_date'].astype(str).str.zfill(8)
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else None


def fwd_returns(row):
    code = row['ts_code']
    sig_d = row['trade_date']
    start = (pd.Timestamp(sig_d) - pd.Timedelta(days=10)).strftime('%Y%m%d')
    df = sc.cached_daily(code, start, END_DATE)
    if df is None or df.empty:
        return None
    df = df.sort_values('trade_date').reset_index(drop=True)
    pos = df.index[df['trade_date'] == sig_d]
    if len(pos) == 0:
        return None
    i = int(pos[0])
    if i + 1 >= len(df):
        return None
    buy = float(df.loc[i + 1, 'open'])

    def close_at(n):
        return float(df.loc[i + n, 'close']) if i + n < len(df) else None

    n_avail = len(df) - i - 1
    out = {
        'name': row['name'], 'ts_code': code, 'trade_date': sig_d,
        'signal': row['buy_signal'], 'score': row['score'],
        'readiness': row['buy_readiness'],
        'entry': buy, 'days_avail': n_avail,
        'stop_hit': 0, 'tp_hit': 0, 'max_gain': None, 'max_dd': None,
    }
    stop = row['stop_loss'] if pd.notna(row['stop_loss']) else None
    tp = row['take_profit'] if pd.notna(row['take_profit']) else None
    hi, lo = buy, buy
    stopped = False
    for j in range(i + 1, len(df)):
        h = float(df.loc[j, 'high'])
        l = float(df.loc[j, 'low'])
        c = float(df.loc[j, 'close'])
        hi = max(hi, h)
        lo = min(lo, l)
        if stop is not None and l <= stop and not stopped:
            # 同日先看止盈再看止损（保守）
            if tp is None or h < tp:
                out['stop_hit'] = 1
                stopped = True
        if tp is not None and h >= tp and out['tp_hit'] == 0:
            out['tp_hit'] = 1
    out['max_gain'] = (hi - buy) / buy * 100 if buy > 0 else None
    out['max_dd'] = (lo - buy) / buy * 100 if buy > 0 else None
    for n in (1, 2, 3, 5):
        c = close_at(n)
        out[f'r{n}'] = (c - buy) / buy * 100 if c else None
    c = float(df.loc[len(df) - 1, 'close'])
    out['r_last'] = (c - buy) / buy * 100
    return out


def seg(x):
    if x >= 95:
        return '95+'
    if x >= 90:
        return '90-95'
    if x >= 85:
        return '85-90'
    if x >= 80:
        return '80-85'
    return '<80'


def ready_seg(x):
    if x >= 85:
        return '85+'
    if x >= 75:
        return '75-85'
    return '<75'


def summarize(df, label):
    n = len(df)
    if n == 0:
        print(f"{label:<28} 样本0")
        return
    def wr(col):
        s = df[col].dropna()
        return f"{(s > 0).mean() * 100:.0f}%({int((s > 0).sum())}/{len(s)})"
    print(f"\n── {label} (n={n}) ──")
    print(f"  T+1收盘胜率: {wr('r1')} | T+3: {wr('r3')} | T+5: {wr('r5')} | 最新: {wr('r_last')}")
    print(f"  T+1均值: {df['r1'].mean():+.2f}% | T+3: {df['r3'].mean():+.2f}% | T+5: {df['r5'].mean():+.2f}% | 最新: {df['r_last'].mean():+.2f}%")
    print(f"  中位数: T+1 {df['r1'].median():+.2f}% | T+5 {df['r5'].median():+.2f}%")
    print(f"  区间最大涨幅均值: {df['max_gain'].mean():+.2f}% | 最大回撤均值: {df['max_dd'].mean():+.2f}%")
    print(f"  止盈命中率: {df['tp_hit'].mean() * 100:.0f}% | 止损命中率: {df['stop_hit'].mean() * 100:.0f}%")
    both = ((df['tp_hit'] == 1) & (df['stop_hit'] == 0)).mean() * 100
    print(f"  纯止盈(未触止损): {both:.0f}%")
    print(f"  盈亏比(均值|盈利均值/亏损均值): {abs(df[df['r_last'] > 0]['r_last'].mean() / df[df['r_last'] <= 0]['r_last'].mean()):.2f}")


def main():
    sig = load_signals()
    if sig is None:
        print("无历史信号存档")
        return
    print(f"信号文件: 20260724~20260821 (不含最新日)")
    print(f"信号总数: {len(sig)} 条, 涉及 {sig['ts_code'].nunique()} 只股票, {sig['trade_date'].nunique()} 个交易日")

    rows = []
    skipped = 0
    for _, r in sig.iterrows():
        try:
            out = fwd_returns(r)
            if out is None:
                skipped += 1
            else:
                rows.append(out)
        except Exception:
            skipped += 1
    if skipped:
        print(f"(跳过 {skipped} 条无数据信号)")
    df = pd.DataFrame(rows)
    df['score_seg'] = df['score'].apply(seg)
    df['ready_seg'] = df['readiness'].apply(ready_seg)

    print("\n" + "═" * 70)
    print("主线龙头首次回踩策略 - 前瞻胜率统计 (T+1开盘买入)")
    print("═" * 70)
    summarize(df, '全体信号')
    summarize(df[df['signal'] == 'READY'], 'READY 可买入信号')
    for s in ['95+', '90-95', '85-90', '80-85', '<80']:
        summarize(df[df['score_seg'] == s], f'评分 {s}')
    for s in ['85+', '75-85', '<75']:
        summarize(df[df['ready_seg'] == s], f'Readiness {s}')

    print("\n── 按信号日汇总 (T+5 胜率) ──")
    g = df.groupby('trade_date').agg(
        n=('ts_code', 'count'),
        wr5=('r5', lambda x: f"{(x.dropna() > 0).mean() * 100:.0f}%"),
        mean5=('r5', lambda x: f"{x.dropna().mean():+.2f}%"),
    )
    print(g.to_string())

    out_path = os.path.join(SIGNAL_DIR, '_winrate_detail.csv')
    df.to_csv(out_path, index=False, encoding='utf-8-sig')
    print(f"\n明细已保存: {out_path}")


if __name__ == '__main__':
    main()
