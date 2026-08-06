# -*- coding: utf-8 -*-
"""
ELD 最佳买点算法 - TDX 历史回测
================================================
基于通达信本地 .day 文件，对 earnings_buy_point.py 的 V3 买点算法
（买点类型分类 + 质量评分 + 乖离控制）做全市场历史验证。

回测规则（与 tail_backtest_tdx.py 框架一致）:
  1. 信号日收盘价作为信号触发价
  2. 统计 T+1/T+3/T+5/T+10 收益（信号日收盘买入，对应日收盘卖出）
  3. 分组统计: 买点类型 / 质量分段 / 乖离区间 / 量比区间 / 月度 / 是否业绩预增池

数据源:
  - 通达信 .day 文件 (C:/new_tdx/vipdoc/sh|sz/lday/*.day) - 日线OHLC
  - stock_basic.csv - 名称/行业（过滤ST、北交所）
  - forecast_*.parquet - 历史业绩预告（ELD场景限定: 预增≥30%公告后5-20交易日）

用法:
  python eld_buy_backtest_tdx.py --start 20260101 --end 20260803
  python eld_buy_backtest_tdx.py --status
"""
import os
import sys
import glob
import json
import time
import argparse
import sqlite3
import datetime

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
CACHE_DIR = r'D:\mystock\cache_daily'
TDX_PATH = r"C:\new_tdx"
BT_DB = os.path.join(CACHE_DIR, 'eld_buy_backtest_tdx.db')

from tail_backtest_tdx import parse_tdx_day_file, ts_code_to_tdx_file  # noqa: E402

# ════════════════════════════════════════════════════════════════
# 买点算法复现（与 eld/earnings_buy_point.py 一致，机构维度置未知）
# ════════════════════════════════════════════════════════════════
CHASE_BIAS = 15.0          # 乖离>15% 追高
OPT_MIN, OPT_MAX = -2.0, 5.0   # 乖离最佳区
OK_MAX = 10.0              # 乖离可接受上限
QUALITY_BUY = 80.0         # ≥80 BUY
QUALITY_WATCH = 50.0       # ≥50 WATCH
INST_UNKNOWN_SCORE = 6.0   # 回测无机构数据，给"未知"基准分


def compute_signals_df(df):
    """对单只股票计算买点信号, 返回信号行 DataFrame（纯向量化）"""
    if df is None or len(df) < 60:
        return None
    df = df.copy()
    close = df['close'].values
    high = df['high'].values
    vol = df['vol'].values

    ma5 = pd.Series(close).rolling(5).mean().values
    ma10 = pd.Series(close).rolling(10).mean().values
    ma20 = pd.Series(close).rolling(20).mean().values
    vol_ma20 = pd.Series(vol).rolling(20).mean().shift(1).values
    recent_high = pd.Series(high).rolling(20).max().values
    chg = pd.Series(close).pct_change().values * 100

    with np.errstate(divide='ignore', invalid='ignore'):
        vol_ratio = np.where(vol_ma20 > 0, vol / vol_ma20, np.nan)
        bias = np.where(ma20 > 0, (close / ma20 - 1) * 100, np.nan)
        dist_ma10 = np.where(ma10 > 0, np.abs(close / ma10 - 1) * 100, 99)
        dist_ma20 = np.where(ma20 > 0, np.abs(close / ma20 - 1) * 100, 99)
    above_ma20 = close > ma20

    # MACD 绿柱收敛
    ema12 = pd.Series(close).ewm(span=12, adjust=False).mean()
    ema26 = pd.Series(close).ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd_bar = (dif - dea) * 2
    macd_green_conv = ((macd_bar < 0) & (macd_bar.shift(1) > macd_bar)).values

    # ── 买点类型（向量化） ──
    btype = np.where(bias > CHASE_BIAS, 'CHASE_HIGH',
             np.where((close >= recent_high) & (vol_ratio >= 1.2), 'BREAKOUT',
             np.where(((dist_ma20 <= 3) | (dist_ma10 <= 2)) & (vol_ratio <= 0.9), 'VCP_PULLBACK',
             np.where((dist_ma20 <= 5) & above_ma20, 'MA20_BOUNCE',
             np.where((dist_ma10 <= 3) & above_ma20, 'MA10_BOUNCE',
             np.where(above_ma20 & (bias > 5), 'TREND_FOLLOW', 'UNKNOWN'))))))

    # ── 质量分（向量化, 机构=未知6分基准） ──
    q = np.zeros(len(df))
    q += np.where((bias >= OPT_MIN) & (bias <= OPT_MAX), 25,
         np.where(bias <= OK_MAX, 18, np.where(bias <= CHASE_BIAS, 8, 0)))
    q += np.where(dist_ma20 <= 2, 25,
         np.where(dist_ma10 <= 2, 22,
         np.where(dist_ma20 <= 5, 18,
         np.where(dist_ma10 <= 4, 12, 5))))
    q += np.where(vol_ratio < 0.6, 20,
         np.where(vol_ratio < 0.8, 16,
         np.where(vol_ratio < 1.0, 10, 4)))
    q += np.where((chg >= -3) & (chg <= 3), 8, 0)
    q += np.where(macd_green_conv, 7, 0)
    q += INST_UNKNOWN_SCORE
    q = np.minimum(100.0, np.round(q, 1))

    # 有效行: 指标齐全 + 有明确买点类型 + 质量≥WATCH门槛
    valid = (~np.isnan(ma20)) & (~np.isnan(ma10)) & (~np.isnan(vol_ratio)) \
        & (ma20 > 0) & (close > 0) & (btype != 'UNKNOWN') & (q >= QUALITY_WATCH)

    idxs = np.where(valid)[0]
    if len(idxs) == 0:
        return None

    t1 = np.full(len(df), np.nan)
    t3 = np.full(len(df), np.nan)
    t5 = np.full(len(df), np.nan)
    t10 = np.full(len(df), np.nan)
    t1[:-1] = close[1:] / close[:-1] - 1
    t3[:-3] = close[3:] / close[:-3] - 1
    t5[:-5] = close[5:] / close[:-5] - 1
    t10[:-10] = close[10:] / close[:-10] - 1

    rows = pd.DataFrame({
        'trade_date': df['trade_date'].values[idxs],
        'ts_code': df['ts_code'].values[idxs] if 'ts_code' in df.columns else '',
        'buy_type': btype[idxs],
        'quality': q[idxs],
        'bias': np.round(bias[idxs], 2),
        'vol_ratio': np.round(vol_ratio[idxs], 2),
        'chg': np.round(chg[idxs], 2),
        't1': t1[idxs], 't3': t3[idxs], 't5': t5[idxs], 't10': t10[idxs],
    })
    return rows


# ════════════════════════════════════════════════════════════════
# 数据加载
# ════════════════════════════════════════════════════════════════
def load_stock_basic():
    """加载股票基本信息, 过滤ST/北交所, 返回 {ts_code: name}"""
    path = os.path.join(CACHE_DIR, 'stock_basic.csv')
    df = pd.read_csv(path)
    df = df[~df['name'].str.contains('ST', na=False)]
    df = df[~df['ts_code'].str.startswith(('4', '8', '9'))]
    return dict(zip(df['ts_code'], df['name']))


def load_forecast_announce():
    """加载历史业绩预增公告 {ts_code: [(ann_date, p_change_min)]}
    仅保留 type 含'预增' 且 p_change_min>=30 的公告
    """
    result = {}
    files = glob.glob(os.path.join(CACHE_DIR, 'forecast_*.parquet'))
    for f in files:
        try:
            df = pd.read_parquet(f, columns=['ts_code', 'ann_date', 'type', 'p_change_min'])
        except Exception:
            continue
        if df.empty or 'ts_code' not in df.columns:
            continue
        ts = df['ts_code'].iloc[0]
        for _, r in df.iterrows():
            t = str(r.get('type', '') or '')
            if '预增' in t:
                pmin = r.get('p_change_min')
                if pmin is not None and pmin >= 30:
                    result.setdefault(ts, []).append((str(r['ann_date']), float(pmin)))
    for k in result:
        result[k].sort()
    return result


# ════════════════════════════════════════════════════════════════
# 回测主流程
# ════════════════════════════════════════════════════════════════
def run_backtest(start_date, end_date):
    stock_basic = load_stock_basic()
    forecast_map = load_forecast_announce()
    print(f"股票池(去ST/北交所): {len(stock_basic)} 只; 预增池: {len(forecast_map)} 只")

    # 交易日序列（用于公告窗口计算）
    all_signals = []
    t0 = time.time()
    codes = sorted(stock_basic.keys())
    for i, ts_code in enumerate(codes):
        tdx_file = ts_code_to_tdx_file(ts_code)
        if not tdx_file or not os.path.exists(tdx_file):
            continue
        df = parse_tdx_day_file(tdx_file)
        if df is None or df.empty:
            continue
        df['ts_code'] = ts_code
        # 扩展区间: 前推180天保证MA60/MACD充分预热
        start_dt = datetime.datetime.strptime(start_date, '%Y%m%d')
        ext_start = (start_dt - datetime.timedelta(days=200)).strftime('%Y%m%d')
        df = df[(df['trade_date'] >= ext_start) & (df['trade_date'] <= end_date)].copy()
        if len(df) < 60:
            continue

        sig = compute_signals_df(df)
        if sig is not None and len(sig) > 0:
            sig = sig[sig['trade_date'] >= start_date]
            if len(sig) > 0:
                # 标记是否 ELD 场景（预增公告后5-20交易日窗口）
                sig['in_eld_window'] = False
                anns = forecast_map.get(ts_code, [])
                if anns:
                    all_dates = sorted(df['trade_date'].unique().tolist())
                    date_idx = {d: n for n, d in enumerate(all_dates)}
                    for j, row in sig.iterrows():
                        sig_date = row['trade_date']
                        for ann_date, _ in anns:
                            if ann_date > sig_date:
                                break
                            a_idx = date_idx.get(ann_date)
                            if a_idx is None:
                                continue
                            days = date_idx[sig_date] - a_idx
                            if 5 <= days <= 20:
                                sig.at[j, 'in_eld_window'] = True
                                break
                all_signals.append(sig)

        if (i + 1) % 1000 == 0:
            print(f"  进度: {i+1}/{len(codes)} ({time.time()-t0:.0f}s)")

    if not all_signals:
        print("无信号")
        return None

    full = pd.concat(all_signals, ignore_index=True)
    print(f"信号总数: {len(full)}, 耗时{time.time()-t0:.0f}s")

    # 入库
    conn = sqlite3.connect(BT_DB, timeout=10.0)
    full.to_sql('eld_buy_signals', conn, if_exists='replace', index=False)
    conn.commit()
    conn.close()
    print(f"已入库: {BT_DB}")
    return full


# ════════════════════════════════════════════════════════════════
# 统计展示
# ════════════════════════════════════════════════════════════════
def _fmt_stats(df):
    """计算统计字段: n, t1胜率, t1均收益, t5均, t10均, 中位t1"""
    if df is None or len(df) == 0:
        return None
    n = len(df)
    t1_wr = (df['t1'] > 0).mean() * 100
    t1_mean = df['t1'].mean() * 100
    t1_med = df['t1'].median() * 100
    t5_mean = df['t5'].mean() * 100
    t10_mean = df['t10'].mean() * 100
    t10_wr = (df['t10'] > 0).mean() * 100
    return n, t1_wr, t1_mean, t1_med, t5_mean, t10_mean, t10_wr


def _print_table(title, grouped_df, group_cols):
    print(f"\n{'═' * 88}")
    print(f"  {title}")
    print(f"{'═' * 88}")
    hdr = f"  {'分组':<24} {'信号':>6} {'T+1胜率':>8} {'T+1均':>8} {'T+1中位':>8} {'T+5均':>8} {'T+10均':>8} {'T+10胜率':>9}"
    print(hdr)
    print('  ' + '─' * 86)
    for key, g in grouped_df:
        s = _fmt_stats(g)
        if s is None:
            continue
        n, w1, m1, med1, m5, m10, w10 = s
        print(f"  {str(key):<24} {n:>6} {w1:>7.1f}% {m1:>+7.2f}% {med1:>+7.2f}% {m5:>+7.2f}% {m10:>+7.2f}% {w10:>8.1f}%")


def show_stats(df, forecast_map):
    print(f"\n{'#' * 88}")
    print(f"  ELD 最佳买点算法 - 回测统计（信号日收盘买入）")
    print(f"{'#' * 88}")

    # 0. 总体
    _print_table("总体", [('全部信号', df)], None)

    # 1. 按买点类型
    order = ['VCP_PULLBACK', 'MA20_BOUNCE', 'MA10_BOUNCE', 'BREAKOUT', 'TREND_FOLLOW', 'CHASE_HIGH']
    type_groups = []
    for t in order:
        g = df[df['buy_type'] == t]
        if len(g) > 0:
            type_groups.append((t, g))
    _print_table("按买点类型", type_groups, None)

    # 2. 按质量分段
    def qbucket(q):
        if q >= 90: return '90+'
        if q >= 80: return '80-90'
        if q >= 70: return '70-80'
        if q >= 60: return '60-70'
        return '50-60'
    qg = df.groupby(df['quality'].apply(qbucket))
    qg = sorted([(k, v) for k, v in qg],
                key=lambda x: 100 if x[0] == '90+' else int(x[0].split('-')[0]), reverse=True)
    _print_table("按质量分段", qg, None)

    # 3. 按乖离区间
    def bbucket(b):
        if b > 15: return '>15 追高'
        if b > 10: return '10~15'
        if b > 5: return '5~10'
        if b > 0: return '0~5 (最佳区)'
        if b > -2: return '-2~0 (最佳区)'
        if b > -5: return '-5~-2'
        return '<-5'
    bg = df.groupby(df['bias'].apply(bbucket))
    bg = sorted([(k, v) for k, v in bg], key=lambda x: -_fmt_stats(x[1])[2])
    _print_table("按乖离区间 (按T+1均降序)", bg, None)

    # 4. 按量比区间
    def vbucket(v):
        if v < 0.6: return '<0.6 极度缩量'
        if v < 0.8: return '0.6~0.8 显著缩量'
        if v < 1.0: return '0.8~1.0 温和'
        if v < 1.2: return '1.0~1.2 放量'
        return '>1.2 显著放量'
    vg = df.groupby(df['vol_ratio'].apply(vbucket))
    vg = sorted([(k, v) for k, v in vg], key=lambda x: -_fmt_stats(x[1])[2])
    _print_table("按量比区间 (按T+1均降序)", vg, None)

    # 5. 信号分级（模拟V3决策）
    def signal_level(row):
        if row['buy_type'] == 'CHASE_HIGH' or row['bias'] > CHASE_BIAS:
            return '追高(WATCH)'
        if row['quality'] >= QUALITY_BUY:
            return 'BUY (质量≥80)'
        return 'WATCH'
    sl = df.copy()
    sl['level'] = sl.apply(signal_level, axis=1)
    sg = sorted(sl.groupby('level'), key=lambda x: -_fmt_stats(x[1])[2])
    _print_table("按信号分级", sg, None)

    # 6. ELD场景 vs 非ELD
    if 'in_eld_window' in df.columns:
        eg = []
        eld = df[df['in_eld_window'] == True]  # noqa: E712
        non_eld = df[df['in_eld_window'] == False]  # noqa: E712
        if len(eld) > 0:
            eg.append(('业绩预增公告后5-20日', eld))
        eg.append(('非公告窗口', non_eld))
        _print_table("ELD场景对比", eg, None)

        # ELD场景内按类型
        if len(eld) > 0:
            etype = []
            for t in order:
                g = eld[eld['buy_type'] == t]
                if len(g) > 0:
                    etype.append((t, g))
            _print_table("ELD场景内 按买点类型", etype, None)

            # ELD场景内 BUY vs WATCH
            eld_sig = eld.copy()
            eld_sig['level'] = eld_sig.apply(signal_level, axis=1)
            esg = sorted(eld_sig.groupby('level'), key=lambda x: -_fmt_stats(x[1])[2])
            _print_table("ELD场景内 按信号分级", esg, None)

    # 7. 月度
    dfm = df.copy()
    dfm['month'] = dfm['trade_date'].str[:6]
    mg = sorted(dfm.groupby('month'), key=lambda x: x[0])
    _print_table("按月统计", mg, None)

    # 导出CSV
    out = os.path.join(CACHE_DIR, f'eld_buy_backtest_{df["trade_date"].min()}_{df["trade_date"].max()}.csv')
    df.to_csv(out, index=False, encoding='utf-8-sig')
    print(f"\n  信号明细已导出: {out}")


def main():
    parser = argparse.ArgumentParser(description='ELD最佳买点算法 TDX回测')
    parser.add_argument('--start', default='20260101')
    parser.add_argument('--end', default='20260803')
    parser.add_argument('--status', action='store_true', help='查看历史回测统计')
    args = parser.parse_args()

    if args.status:
        if not os.path.exists(BT_DB):
            print(f"回测库不存在: {BT_DB}")
            return
        df = pd.read_sql_query('SELECT * FROM eld_buy_signals', sqlite3.connect(BT_DB))
        forecast_map = load_forecast_announce()
        show_stats(df, forecast_map)
        return

    df = run_backtest(args.start, args.end)
    if df is not None:
        forecast_map = load_forecast_announce()
        show_stats(df, forecast_map)


if __name__ == '__main__':
    main()
