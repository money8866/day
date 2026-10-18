#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
盘后仓位建议准确度评估
- 数据源：overall_analysis（每日盘后建议）+ cache.db index_kline（指数行情）
- 语义：T日盘后出建议 position_T -> T+1 日执行，收益按 T+1 close-to-close
"""
import sqlite3
import pandas as pd
import numpy as np
from io import StringIO

CACHE_DB = 'D:/mystock/solo/cache_backbone_tushare/cache.db'
MA_DB = 'D:/mystock/solo/cache_backbone_tushare/market_analysis.db'
INDEX_MAP = {
    '上证指数': '000001.SH',
    '沪深300': '000300.SH',
    '中证2000': '932000.CSI',
}

# ---------- 1. 还原指数日线序列（每个 trade_date 缓存取最后一行=当日） ----------
def load_index_series():
    conn = sqlite3.connect(CACHE_DB)
    cur = conn.cursor()
    cur.execute("SELECT key, data FROM cache_data WHERE key LIKE 'tsc_index_kline%'")
    rows = cur.fetchall()
    conn.close()
    out = {}
    for key, data in rows:
        code = key.split('ts_code_')[1].split('_')[0]
        df = pd.read_csv(StringIO(data))
        if df.empty or 'trade_date' not in df.columns:
            continue
        df['trade_date'] = df['trade_date'].astype(str)
        last = df.iloc[-1]
        out.setdefault(code, {})[last['trade_date']] = {
            'close': float(last['close']),
            'pct_chg': float(last.get('pct_chg', 0)),
        }
    series = {}
    for code, d in out.items():
        s = pd.DataFrame.from_dict(d, orient='index').sort_index()
        series[code] = s
    return series

# ---------- 2. 读取盘后建议 ----------
def load_advice():
    conn = sqlite3.connect(MA_DB)
    df = pd.read_sql_query(
        "SELECT trade_date, total_position, trend_score, market_status, position_reason "
        "FROM overall_analysis ORDER BY trade_date", conn)
    conn.close()
    df['trade_date'] = df['trade_date'].astype(str)
    return df

def main():
    series = load_index_series()
    advice = load_advice()

    for name, code in INDEX_MAP.items():
        s = series.get(code)
        print(f"{name} ({code}): {len(s) if s is not None else 0} 个交易日 "
              f"{s.index.min() if s is not None else '-'} ~ {s.index.max() if s is not None else '-'}")

    print(f"\n建议记录: {len(advice)} 天  {advice['trade_date'].min()} ~ {advice['trade_date'].max()}")
    print(f"仓位分布: {advice['total_position'].value_counts().sort_index().to_dict()}")

    # ---------- 3. 对齐：建议日 T -> 次日 T+1 收益 ----------
    results = []
    advice_dates = set(advice['trade_date'])
    for bm_name, code in INDEX_MAP.items():
        s = series.get(code)
        if s is None:
            continue
        closes = s['close']
        dates = list(closes.index)
        for i, d in enumerate(dates[:-1]):
            if d not in advice_dates:
                continue
            nd = dates[i + 1]  # 下一交易日
            ret = closes.iloc[i + 1] / closes.iloc[i] - 1
            row = advice[advice['trade_date'] == d].iloc[0]
            results.append({
                'bm': bm_name, 'advice_date': d, 'exec_date': nd,
                'position': float(row['total_position']),
                'trend_score': float(row['trend_score']) if pd.notna(row['trend_score']) else np.nan,
                'regime': row['market_status'], 'next_ret': ret * 100,
            })
    r = pd.DataFrame(results)

    if r.empty:
        print("无对齐样本")
        return

    print(f"\n对齐样本: {len(r)} 条 (3个基准x建议天数)")
    print(f"建议日有次日行情的覆盖: {r['advice_date'].nunique()} 天")

    # ---------- 4. 分层检验：仓位档 vs 次日收益 ----------
    def tier(p):
        if p <= 10:
            return '0-10% 空仓/试探'
        if p <= 30:
            return '15-30% 轻仓'
        if p < 50:
            return '35-45% 中仓'
        return '>=50% 重仓'
    r['tier'] = r['position'].apply(tier)

    print("\n" + "=" * 78)
    print("分层检验：建议仓位档 -> 次日收益（3基准平均，单位%）")
    print("=" * 78)
    print(f"{'仓位档':<16}{'样本':>6}{'次日均收益':>12}{'胜率':>8}{'最好':>8}{'最差':>8}")
    for t in ['0-10% 空仓/试探', '15-30% 轻仓', '35-45% 中仓', '>=50% 重仓']:
        sub = r[r['tier'] == t]
        if len(sub) == 0:
            continue
        print(f"{t:<16}{len(sub):>6}{sub['next_ret'].mean():>12.3f}"
              f"{(sub['next_ret'] > 0).mean() * 100:>7.1f}%{sub['next_ret'].max():>8.2f}{sub['next_ret'].min():>8.2f}")

    # ---------- 5. 高仓位日 vs 低仓位日：方向判断准确度 ----------
    print("\n" + "=" * 78)
    print("方向判断准确度")
    print("=" * 78)
    hi = r[r['position'] >= 50]
    lo = r[r['position'] <= 10]
    print(f"高仓位建议(>=50%) 共{len(hi)}次: 次日上涨概率 {(hi['next_ret'] > 0).mean() * 100:.1f}%  "
          f"均收益 {hi['next_ret'].mean():+.3f}%")
    print(f"低仓位建议(<=10%) 共{len(lo)}次: 次日下跌概率 {(lo['next_ret'] < 0).mean() * 100:.1f}%  "
          f"均收益 {lo['next_ret'].mean():+.3f}%")
    # 低仓位日的"避险收益"= 市场均收益 - 低仓位日收益（正=成功避开下跌）
    mkt_all = r['next_ret'].mean()
    print(f"全样本次日均收益: {mkt_all:+.3f}%   低仓位日相对优势: {mkt_all - lo['next_ret'].mean():+.3f}%")

    # ---------- 6. IC：趋势分/仓位 与 次日收益的相关性 ----------
    print("\n" + "=" * 78)
    print("信息系数 IC（Spearman，越正越有预测力）")
    print("=" * 78)
    for col in ['position', 'trend_score']:
        sub = r.dropna(subset=[col])
        ic = sub[col].corr(sub['next_ret'], method='spearman')
        print(f"  {col:<12} vs 次日收益: IC = {ic:+.4f}  (n={len(sub)})")

    # ---------- 7. 模拟策略：按建议仓位吃次日指数收益 ----------
    print("\n" + "=" * 78)
    print("模拟组合（建议仓位 x 次日指数收益，无成本）")
    print("=" * 78)
    for bm_name in INDEX_MAP:
        sub = r[r['bm'] == bm_name].sort_values('advice_date').reset_index(drop=True)
        if len(sub) < 10:
            continue
        strat_ret = (sub['position'] / 100 * sub['next_ret'] / 100)
        bench_ret = sub['next_ret'] / 100
        # 策略收益要递延：T日建议的仓位作用于T+1，从T+1开始计入净值
        cum_strat = (1 + strat_ret).cumprod()
        cum_bench = (1 + bench_ret).cumprod()
        # 满仓基准（每日100%）
        cum_full = (1 + bench_ret).cumprod()
        # 最大回撤
        dd_s = (cum_strat / cum_strat.cummax() - 1).min()
        dd_b = (cum_full / cum_full.cummax() - 1).min()
        print(f"\n  [{bm_name}] 样本 {len(sub)} 天")
        print(f"    建议仓位策略: 累计 {(cum_strat.iloc[-1] - 1) * 100:+.2f}%  最大回撤 {dd_s * 100:.2f}%")
        print(f"    满仓持有基准: 累计 {(cum_full.iloc[-1] - 1) * 100:+.2f}%  最大回撤 {dd_b * 100:.2f}%")
        print(f"    超额: {((cum_strat.iloc[-1] - 1) - (cum_full.iloc[-1] - 1)) * 100:+.2f}%")

    # ---------- 8. 分市场状态：次日实际表现 ----------
    print("\n" + "=" * 78)
    print("市场状态分类 -> 次日实际表现（3基准平均）")
    print("=" * 78)
    print(f"{'状态':<12}{'天数':>6}{'均仓位':>8}{'次日均收益':>12}{'胜率':>8}")
    for reg, sub in r.groupby('regime'):
        print(f"{reg:<12}{sub['advice_date'].nunique():>6}{sub['position'].mean():>8.1f}"
              f"{sub['next_ret'].mean():>12.3f}{(sub['next_ret'] > 0).mean() * 100:>7.1f}%")

    # ---------- 9. 信号稳定性：换手 ----------
    print("\n" + "=" * 78)
    print("信号稳定性")
    print("=" * 78)
    a = advice.set_index('trade_date')['total_position'].sort_index()
    chg = a.diff().abs()
    print(f"仓位调整次数(|diff|>0): {(chg > 0).sum()} / {len(a) - 1} 天 "
          f"({(chg > 0).mean() * 100:.0f}% 的交易日发生变动)")
    print(f"平均单次变动: {chg[chg > 0].mean():.1f} 个百分点, 最大单次: {chg.max():.0f} 个百分点")

if __name__ == '__main__':
    main()
