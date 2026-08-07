# -*- coding: utf-8 -*-
"""
Buy Score 交易价值评分 - TDX 历史回测
================================================
验证 eld/buy_score.py 的 Buy Score（30%买点质量 + 25%乖离 + 20%机构 + 15%连板 + 10%风险收益比）
是否具备排序价值：分数越高未来收益越好（单调性 / 十分位 / 分级阈值 / ELD场景内）。

数据源:
  - eld_buy_backtest_tdx.db 的 eld_buy_signals 表（300k 信号，含 quality/bias/t1/t5/t10/in_eld_window）
  - 通达信 .day 文件 (C:/new_tdx) - 复算信号日连续涨停数
  - forecast_*.parquet - 预增公告 p_change_min 映射事件强度

近似说明（回测无法完全复现的维度，报告如实标注）:
  - 机构状态: 历史无快照 → 全部按"未知"40分（20%权重=8分，所有信号统一，不影响排序）
  - 事件强度: 用预增幅度映射（≥100%→90分 / ≥60%→75分 / ≥30%→55分 / 无公告→35分）

用法:
  python eld_buy_score_backtest_tdx.py
  python eld_buy_score_backtest_tdx.py --full   # 重建信号库后回测（默认用现有库）
"""
import os
import sys
import glob
import sqlite3
import argparse
import time

import numpy as np
import pandas as pd
from scipy import stats

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
CACHE_DIR = r'D:\mystock\cache_daily'
BT_DB = os.path.join(CACHE_DIR, 'eld_buy_backtest_tdx.db')
TDX_PATH = r"C:\new_tdx"

from tail_backtest_tdx import parse_tdx_day_file, ts_code_to_tdx_file  # noqa: E402
from eld.buy_score import _bias_score, _cons_score, _rr_score, buy_score_level  # noqa: E402

INST_UNKNOWN_SCORE = 40.0  # 回测无机构快照，未知状态给中间分(不影响排序)


def calc_cons_count(ts_code, df):
    """向量化计算每交易日连续涨停数（含当日）"""
    close = df['close'].values
    pre = df['pre_close'].values
    limit = 19.5 if ts_code.startswith(('300', '301', '688', '689')) else 9.5
    chg = np.full(len(df), np.nan)
    m = (pre > 0) & np.isfinite(pre)
    chg[m] = (close[m] / pre[m] - 1) * 100
    is_limit = (chg >= limit - 0.5)
    cons = np.zeros(len(df), dtype=np.int32)
    run = 0
    for i in range(len(df) - 1, -1, -1):
        run = run + 1 if is_limit[i] else 0
        cons[i] = run
    return cons


def load_forecast_announce():
    """历史预增公告 {ts_code: {ann_date: p_change_min}}（type含预增 且 p_change_min>=30）"""
    result = {}
    files = glob.glob(os.path.join(CACHE_DIR, 'forecast_*.parquet'))
    for f in files:
        try:
            df = pd.read_parquet(f, columns=['ts_code', 'ann_date', 'type', 'p_change_min'])
        except Exception:
            continue
        if df.empty or 'ts_code' not in df.columns:
            continue
        for _, r in df.iterrows():
            t = str(r.get('type', '') or '')
            if '预增' in t:
                pmin = r.get('p_change_min')
                if pmin is not None and pmin >= 30:
                    result.setdefault(r['ts_code'], {})[str(r['ann_date'])] = float(pmin)
    return result


def event_quality_from_pmin(pmin):
    """预增幅度 → 事件强度分（上行空间）"""
    if pmin >= 100:
        return 90.0
    if pmin >= 60:
        return 75.0
    return 55.0


def build_buy_score(sig, forecast_map):
    """给信号表补连板数 + Buy Score 各分量 + 分级"""
    codes = sig['ts_code'].unique()
    cons_map = {}
    t0 = time.time()
    for i, ts_code in enumerate(codes):
        tdx_file = ts_code_to_tdx_file(ts_code)
        if not tdx_file or not os.path.exists(tdx_file):
            continue
        df = parse_tdx_day_file(tdx_file)
        if df is None or df.empty:
            continue
        cons_map[ts_code] = dict(zip(df['trade_date'], calc_cons_count(ts_code, df)))
        if (i + 1) % 1000 == 0:
            print(f"  连板计算进度: {i+1}/{len(codes)} ({time.time()-t0:.0f}s)")

    sig = sig.copy()
    sig['cons_count'] = sig.apply(
        lambda r: cons_map.get(r['ts_code'], {}).get(r['trade_date'], 0), axis=1)
    sig['inst_score'] = INST_UNKNOWN_SCORE

    # 事件强度: ELD窗口内按预增幅度映射, 非窗口35
    ann_map = {ts: sorted(anns.items()) for ts, anns in forecast_map.items()}
    ev = []
    for _, r in sig.iterrows():
        if r['in_eld_window']:
            anns = ann_map.get(r['ts_code'], [])
            p = 35.0
            for ad, pmin in anns:
                if ad <= r['trade_date']:
                    p = event_quality_from_pmin(pmin)
                else:
                    break
            ev.append(p)
        else:
            ev.append(35.0)
    sig['event_quality'] = ev

    q = np.clip(sig['quality'].values, 0, 100)
    b = sig['bias'].values
    inst = sig['inst_score'].values
    cs = np.array([_cons_score(int(c)) for c in sig['cons_count'].values])
    rr = np.array([_rr_score(e, bi) for e, bi in zip(sig['event_quality'].values, b)])
    bs = 0.30 * q + 0.25 * np.array([_bias_score(x) for x in b]) + 0.20 * inst \
        + 0.15 * cs + 0.10 * rr
    sig['buy_score'] = np.minimum(100.0, np.round(bs, 1))
    sig['buy_level'] = [buy_score_level(x) for x in sig['buy_score']]
    return sig


# ════════════════════════════════════════════════════════════════
# 统计
# ════════════════════════════════════════════════════════════════
def fmt(g):
    if g is None or len(g) == 0:
        return None
    n = len(g)
    return (n,
            (g['t1'] > 0).mean() * 100, g['t1'].mean() * 100, g['t1'].median() * 100,
            g['t5'].mean() * 100, (g['t5'] > 0).mean() * 100,
            g['t10'].mean() * 100, (g['t10'] > 0).mean() * 100)


def print_table(title, groups, rank_key=None):
    print(f"\n{'═' * 92}")
    print(f"  {title}")
    print(f"{'═' * 92}")
    hdr = (f"  {'分组':<16}{'信号':>7}{'T+1胜率':>8}{'T+1均':>8}{'T+1中位':>8}"
           f"{'T+5均':>8}{'T+5胜率':>8}{'T+10均':>8}{'T+10胜率':>9}")
    print(hdr)
    print('  ' + '─' * 90)
    if rank_key:
        groups = sorted(groups, key=lambda x: -rank_key(x[1]))
    for k, g in groups:
        s = fmt(g)
        if s is None:
            continue
        n, w1, m1, med1, m5, w5, m10, w10 = s
        print(f"  {str(k):<16}{n:>7}{w1:>7.1f}%{m1:>+7.3f}%{med1:>+7.3f}%"
              f"{m5:>+7.3f}%{w5:>7.1f}%{m10:>+7.3f}%{w10:>8.1f}%")


def decile_report(df, title):
    """按 Buy Score 十分位分层"""
    df = df.dropna(subset=['t1', 't5', 't10']).copy()
    if len(df) < 100:
        print(f"\n  {title}: 样本不足({len(df)})")
        return
    df['dec'] = pd.qcut(df['buy_score'], 10, labels=False, duplicates='drop')
    groups = []
    for d in sorted(df['dec'].unique()):
        g = df[df['dec'] == d]
        lo, hi = g['buy_score'].min(), g['buy_score'].max()
        groups.append((f"D{d+1} [{lo:.0f}~{hi:.0f}]", g))
    print_table(f"{title} - Buy Score 十分位", groups)
    # 单调性
    means = [g['t1'].mean() for _, g in groups]
    corr = np.corrcoef(range(len(groups)), means)[0, 1]
    print(f"  → 十分位序号 vs T+1均收益 线性相关: {corr:+.3f}")


def spearman_report(df, title):
    df = df.dropna(subset=['t1', 't5', 't10']).copy()
    print(f"\n  [{title}] Spearman 秩相关:")
    for col in ['buy_score', 'quality']:
        r1 = stats.spearmanr(df[col], df['t1'])[0]
        r5 = stats.spearmanr(df[col], df['t5'])[0]
        r10 = stats.spearmanr(df[col], df['t10'])[0]
        print(f"    {col:<12} vs T+1: {r1:+.4f}   vs T+5: {r5:+.4f}   vs T+10: {r10:+.4f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--full', action='store_true', help='重建信号库（重新扫描全部股票）')
    args = parser.parse_args()

    if args.full:
        from eld_buy_backtest_tdx import run_backtest
        run_backtest('20260101', '20260803')

    if not os.path.exists(BT_DB):
        print(f"回测库不存在: {BT_DB}，先运行 eld_buy_backtest_tdx.py")
        return

    sig = pd.read_sql_query('SELECT * FROM eld_buy_signals', sqlite3.connect(BT_DB))
    print(f"信号: {len(sig)} | ELD窗口: {int(sig['in_eld_window'].sum())}")
    forecast_map = load_forecast_announce()

    t0 = time.time()
    sig = build_buy_score(sig, forecast_map)
    print(f"Buy Score 计算完成, 耗时{time.time()-t0:.0f}s")

    out = os.path.join(CACHE_DIR, 'eld_buy_score_backtest.csv')
    sig.to_csv(out, index=False, encoding='utf-8-sig')
    print(f"明细已导出: {out}")

    print(f"\n{'#' * 92}")
    print("  Buy Score 交易价值评分 - 回测统计")
    print(f"{'#' * 92}")

    # 0. 总体
    print_table("总体", [('全部信号', sig)])

    # 1. 十分位
    decile_report(sig, "全量信号")

    # 2. Spearman 对比（Buy Score vs quality）
    spearman_report(sig, "全量信号")

    # 3. 分级阈值验证（用户分级: >80推荐买/60-80观察/40-60等回踩/<40禁止追高）
    order = ['推荐买', '观察', '等回踩', '禁止追高']
    lv_groups = []
    for lv in order:
        g = sig[sig['buy_level'] == lv]
        if len(g) > 0:
            lv_groups.append((lv, g))
    print_table("按 Buy Score 分级（用户阈值）", lv_groups)

    # 4. 连板维度单独验证（15%权重核心输入）
    def cbucket(c):
        if c >= 4: return '≥4连板'
        return f'{c}连板' if c > 0 else '0连板'
    cg = sig.groupby(sig['cons_count'].apply(cbucket))
    cg = sorted([(k, v) for k, v in cg], key=lambda x: -fmt(x[1])[2])
    print_table("按连板数（T+1均降序）", cg)

    # 5. ELD 场景内
    eld = sig[sig['in_eld_window'] == True]  # noqa: E712
    non = sig[sig['in_eld_window'] == False]  # noqa: E712
    print_table("ELD 场景对比", [('预增公告后5-20日', eld), ('非公告窗口', non)])
    if len(eld) >= 200:
        decile_report(eld, "ELD 场景内")
        spearman_report(eld, "ELD 场景内")
        lv_g = []
        for lv in order:
            g = eld[eld['buy_level'] == lv]
            if len(g) > 0:
                lv_g.append((lv, g))
        print_table("ELD 场景内 按分级", lv_g)

    # 6. 月度
    sigm = sig.copy()
    sigm['month'] = sigm['trade_date'].str[:6]
    mg = sorted([(k, v) for k, v in sigm.groupby('month')], key=lambda x: x[0])
    print_table("按月统计", mg)


if __name__ == '__main__':
    main()
