# -*- coding: utf-8 -*-
"""
中报猎手 × EGPT 回踩 - TDX 历史回测
================================================
验证"中报实际业绩正(扣非增速≥30% 且 扣非≥0.2亿) + EGPT 回踩择时
(回踩中 1-2 日 且 回踩买点分≥60 → 次日可买入)"在 2023-2026 四个中报季
是否跑赢"业绩正就直接买"的基线。

回测规则（与 eld_buy_backtest_tdx.py 框架一致）:
  1. 中报池: fina_indicator 各年 0630 实际披露(ann_date 为锚), 硬过滤
     (dt_netprofit_yoy>=30 且 profit_dedt>=0.2亿) 或 (netprofit_yoy>=30 且 profit_dedt>=0.2亿)
  2. 基线信号: 中报披露后第一个交易日收盘买入 → "业绩正就买"
  3. EGPT 信号: 披露后 60 交易日内, 逐日快照 analyze_shape(与线上 EGPT 同逻辑),
     仅取 decision=="✅ 次日可买入"(回踩中×分≥60) → 信号日收盘买入
  4. 收益: T+1/T+3/T+5/T+10/T+20 (信号日收盘买入, 对应日收盘卖出)
  5. 分组: 形态分档 / 回踩天数 / 年度 / 月度 / 增速段 / 市值段

数据源:
  - 通达信 .day 文件 (C:/new_tdx/vipdoc/sh|sz/lday/*.day)
  - cache_daily/treasure_fin_ind_*.parquet (fina_indicator 全历史, 含 ann_date)

用法:
  python zhongbao_egpt_backtest_tdx.py --start 20230101 --end 20260818
  python zhongbao_egpt_backtest_tdx.py --status
"""
import os
import sys
import glob
import time
import json
import argparse
import sqlite3
import datetime
from collections import defaultdict

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
CACHE_DIR = r'D:\mystock\cache_daily'
BT_DB = os.path.join(CACHE_DIR, 'zhongbao_egpt_backtest_tdx.db')

from tail_backtest_tdx import parse_tdx_day_file, ts_code_to_tdx_file  # noqa: E402
from pullback_buy import analyze_shape  # noqa: E402
from enhanced_timing_analysis import _calc_vwap, _calc_atr, _calc_chip_concentration_peak  # noqa: E402
from zhongbao_egpt_timing import buy_point_type  # noqa: E402

WINDOW_DAYS = 60          # 中报披露后扫描窗口(交易日)
MIN_YEAR = '2023'         # 回测起始中报年
THEME_MAP_FILE = os.path.join(BASE_DIR, 'report_daily', 'theme_stock_map_latest_v2.json')
THEME_CFG_FILE = os.path.join(BASE_DIR, 'theme_config.json')


def _num(v):
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return None
        return float(v)
    except Exception:
        return None


# ════════════════════════════════════════════════════════════════
# 主题热度（历史可算, 无前视: 用主题主ETF近20日涨幅）
# ════════════════════════════════════════════════════════════════
def load_theme_heat_map():
    """加载 股票→主题 映射 + 主题→主ETF + ETF日线索引。
    返回 (stock2theme, theme2etf, etf_series)
      stock2theme: {ts_code: [主题名, ...]}
      theme2etf:   {主题名: 主ETF代码}
      etf_series:  {ETF代码: pd.Series(index=trade_date, values=close)}
    """
    import json
    stock2theme, theme2etf, etf_series = {}, {}, {}

    # 主题→成分股
    try:
        with open(THEME_MAP_FILE, encoding='utf-8') as f:
            tm = json.load(f)
        themes = tm.get('themes', {})
        for name, members in themes.items():
            for m in members:
                code = m.get('code', '')
                if code:
                    stock2theme.setdefault(code, []).append(name)
    except Exception as e:
        print(f"[警告] 主题映射加载失败: {e}")

    # 主题→主ETF
    try:
        with open(THEME_CFG_FILE, encoding='utf-8') as f:
            cfg = json.load(f)
        for _, v in cfg.items():
            name = v.get('name_cn', '')
            if name and v.get('main_etf'):
                theme2etf[name] = v['main_etf']
    except Exception as e:
        print(f"[警告] theme_config 加载失败: {e}")

    # ETF 日线索引
    etfs = set(theme2etf.values())
    for etf in etfs:
        f = ts_code_to_tdx_file(etf)
        df = parse_tdx_day_file(f) if f and os.path.exists(f) else None
        if df is not None and len(df) > 200:
            etf_series[etf] = pd.Series(df['close'].values, index=df['trade_date'].values)
    print(f"主题热度: 主题映射 {len(stock2theme)} 只 | ETF {len(etf_series)}/{len(etfs)}")
    return stock2theme, theme2etf, etf_series


def theme_heat_at(theme, date, theme2etf, etf_series):
    """主题近20交易日涨幅: ETF[date]/ETF[date-20交易日]-1；无ETF返回None"""
    etf = theme2etf.get(theme)
    if not etf or etf not in etf_series:
        return None
    s = etf_series[etf]
    pos = s.index.searchsorted(date, side='right') - 1
    if pos < 20:
        return None
    c0 = float(s.iloc[pos])
    c20 = float(s.iloc[pos - 20])
    if c20 <= 0:
        return None
    return c0 / c20 - 1.0


def enrich_theme_heat(df, stock2theme, theme2etf, etf_series):
    """给信号/基线 DataFrame 补 theme + theme_heat 列（取股票所属主题中热度最高者）"""
    df = df.copy()
    themes, heats = [], []
    for _, r in df.iterrows():
        code = r['ts_code']
        ts = stock2theme.get(code, [])
        if not ts:
            themes.append(None)
            heats.append(None)
            continue
        best_t, best_h = None, None
        for t in ts:
            h = theme_heat_at(t, r['sig_date'], theme2etf, etf_series)
            if best_h is None or (h is not None and h > best_h):
                best_h, best_t = h, t
        themes.append(best_t)
        heats.append(best_h)
    df['theme'] = themes
    df['theme_heat'] = heats
    return df


def load_zhongbao_pool():
    """读缓存 fina_indicator parquet → {year: {ts_code: {'ann':..,'dty':..,'ny':..,'dedt':..}}}"""
    pool = defaultdict(dict)
    files = glob.glob(os.path.join(CACHE_DIR, 'treasure_fin_ind_*.parquet'))
    for f in files:
        code = os.path.basename(f).replace('treasure_fin_ind_', '').replace('.parquet', '').replace('_', '.')
        try:
            df = pd.read_parquet(f)
        except Exception:
            continue
        if 'ann_date' not in df.columns or 'end_date' not in df.columns:
            continue
        sub = df[df['end_date'].astype(str).str.endswith('0630')]
        for _, r in sub.iterrows():
            year = str(r['end_date'])[:4]
            if year < MIN_YEAR:
                continue
            ann = str(r.get('ann_date') or '')[:8]
            if not ann or ann == 'nan':
                continue
            dty = _num(r.get('dt_netprofit_yoy'))
            ny = _num(r.get('netprofit_yoy'))
            dedt = _num(r.get('profit_dedt')) or 0.0
            ok = (dty is not None and dty >= 30 and dedt >= 2e7) \
                or (ny is not None and ny >= 30 and dedt >= 2e7)
            if ok:
                pool[year][code] = {'ann': ann, 'dty': dty, 'ny': ny, 'dedt': dedt}
    return pool


def _future_returns(df, idx):
    """信号日收盘买入 → T+1/3/5/10/20 收益"""
    closes = df['close'].values
    n = len(closes)
    c0 = closes[idx]
    out = {}
    for h, col in ((1, 't1'), (3, 't3'), (5, 't5'), (10, 't10'), (20, 't20')):
        j = idx + h
        out[col] = closes[j] / c0 - 1 if j < n else np.nan
    return out


def run_backtest(start_date, end_date):
    pool = load_zhongbao_pool()
    total_codes = len(set(c for y in pool for c in pool[y]))
    print(f"中报池: 4年 {total_codes} 只 | 各年: " +
          ", ".join(f"{y}:{len(pool[y])}" for y in sorted(pool)))
    print(f"回测区间: {start_date} ~ {end_date}")

    start_dt = datetime.datetime.strptime(start_date, '%Y%m%d')
    ext_start = (start_dt - datetime.timedelta(days=200)).strftime('%Y%m%d')

    base_rows, sig_rows = [], []
    t0 = time.time()
    codes = sorted(set(c for y in pool for c in pool[y]))

    for i, ts_code in enumerate(codes):
        tdx_file = ts_code_to_tdx_file(ts_code)
        if not tdx_file or not os.path.exists(tdx_file):
            continue
        df = parse_tdx_day_file(tdx_file)
        if df is None or len(df) < 80:
            continue
        df = df[(df['trade_date'] >= ext_start) & (df['trade_date'] <= end_date)].reset_index(drop=True)
        if len(df) < 60:
            continue
        dates = df['trade_date'].tolist()
        n = len(dates)
        opens_all = df['open'].astype(float).values
        lows_all = df['low'].astype(float).values
        closes_all = df['close'].astype(float).values
        vols_all = df['vol'].astype(float).values

        for year, year_map in pool.items():
            if ts_code not in year_map:
                continue
            info = year_map[ts_code]
            ann = info['ann']
            # 披露后第一个交易日
            a_idx = next((j for j, d in enumerate(dates) if d > ann), None)
            if a_idx is None:
                continue

            # ── 基线: 业绩正就直接买(披露后首个交易日收盘) ──
            if a_idx + 1 < n:
                ret = _future_returns(df, a_idx)
                base_rows.append({'ts_code': ts_code, 'year': year,
                                  'ann_date': ann, 'sig_date': dates[a_idx],
                                  'dty': info['dty'], 'ny': info['ny'],
                                  **ret})

            # ── EGPT 择时: 披露后 WINDOW_DAYS 内逐日快照, 取"✅次日可买入" ──
            last_idx = min(a_idx + WINDOW_DAYS, n - 1)
            for k in range(a_idx, last_idx + 1):
                if k < 20:
                    continue
                sub = df.iloc[:k + 1]
                try:
                    shape = analyze_shape(sub)
                except Exception:
                    shape = None
                if not shape or shape.get('decision') != '✅ 次日可买入':
                    continue
                # ── 买点字段: 买点类型/VWAP/筹码峰/ATR止损/次日开盘/信号后21日路径 ──
                vwap = _calc_vwap(sub, 20)
                atr = _calc_atr(sub, 14)
                peak_low, peak_high, _ = _calc_chip_concentration_peak(sub, 60)
                price_v = float(closes_all[k])
                ma20_v = float(np.mean(closes_all[k - 19:k + 1])) if k >= 19 else None
                bp, confirm = buy_point_type(sub, vwap, peak_high, peak_low, price_v, ma20_v,
                                             vols_all[:k + 1], closes_all[:k + 1])
                atr_stop = round(price_v - 2.0 * atr, 3) if atr and atr > 0 else None
                nxt_open = float(opens_all[k + 1]) if k + 1 < n else None
                path_json = json.dumps({
                    'closes': [round(float(x), 3) for x in closes_all[k:k + 21]],
                    'lows': [round(float(x), 3) for x in lows_all[k:k + 21]],
                    'opens': [round(float(x), 3) for x in opens_all[k:k + 21]],
                    'dates': dates[k:k + 21],
                })
                ret = _future_returns(df, k)
                sig_rows.append({
                    'ts_code': ts_code, 'year': year,
                    'ann_date': ann, 'sig_date': dates[k],
                    'stage': shape.get('stage', ''),
                    'pullback_score': shape.get('pullback_score'),
                    'pullback_days': shape.get('pullback_days'),
                    'max_dd10': shape.get('max_dd10'),
                    'dty': info['dty'], 'ny': info['ny'],
                    'buy_point': bp, 'buy_confirm': int(bool(confirm)),
                    'vwap': round(vwap, 3) if vwap else None,
                    'peak_high': round(peak_high, 3) if peak_high else None,
                    'atr_stop': atr_stop, 'next_open': nxt_open, 'path': path_json,
                    **ret})

        if (i + 1) % 500 == 0:
            print(f"  进度: {i+1}/{total_codes} 基线{len(base_rows)} 信号{len(sig_rows)} | {time.time()-t0:.0f}s")

    # ── 入库 ──
    conn = sqlite3.connect(BT_DB, timeout=10.0)
    pd.DataFrame(base_rows).to_sql('base', conn, if_exists='replace', index=False)
    pd.DataFrame(sig_rows).to_sql('egpt_sig', conn, if_exists='replace', index=False)
    conn.commit()
    conn.close()
    print(f"\n基线 {len(base_rows)} 笔, EGPT 信号 {len(sig_rows)} 笔 → {BT_DB} | 总耗时 {time.time()-t0:.0f}s")
    return base_rows, sig_rows


# ════════════════════════════════════════════════════════════════
# 统计展示
# ════════════════════════════════════════════════════════════════
def _fmt(df):
    if df is None or len(df) == 0:
        return None
    n = len(df)
    w1 = (df['t1'] > 0).mean() * 100
    m1 = df['t1'].mean() * 100
    m3 = df['t3'].mean() * 100
    m5 = df['t5'].mean() * 100
    w5 = (df['t5'] > 0).mean() * 100
    m10 = df['t10'].mean() * 100
    m20 = df['t20'].mean() * 100
    return n, w1, m1, m3, m5, w5, m10, m20


def _table(title, groups):
    print(f"\n{'═' * 96}")
    print(f"  {title}")
    print(f"{'═' * 96}")
    hdr = f"  {'分组':<26} {'信号':>6} {'T+1胜':>7} {'T+1均':>8} {'T+3均':>8} {'T+5均':>8} {'T+5胜':>7} {'T+10均':>8} {'T+20均':>8}"
    print(hdr)
    print('  ' + '─' * 94)
    for key, g in groups:
        s = _fmt(g)
        if s is None:
            continue
        n, w1, m1, m3, m5, w5, m10, m20 = s
        print(f"  {str(key):<26} {n:>6} {w1:>6.1f}% {m1:>+7.2f}% {m3:>+7.2f}% {m5:>+7.2f}% {w5:>6.1f}% {m10:>+7.2f}% {m20:>+7.2f}%")


def show_stats(with_heat=False):
    if not os.path.exists(BT_DB):
        print(f"回测库不存在: {BT_DB}")
        return
    conn = sqlite3.connect(BT_DB)
    base = pd.read_sql_query('SELECT * FROM base', conn)
    sig = pd.read_sql_query('SELECT * FROM egpt_sig', conn)
    conn.close()

    if with_heat and 'theme_heat' not in sig.columns:
        print("\n[补算主题热度] ...")
        stock2theme, theme2etf, etf_series = load_theme_heat_map()
        base = enrich_theme_heat(base, stock2theme, theme2etf, etf_series)
        sig = enrich_theme_heat(sig, stock2theme, theme2etf, etf_series)

    print(f"\n{'#' * 96}")
    print(f"  中报猎手 × EGPT 回踩择时 - 回测统计（信号日收盘买入）")
    print(f"  基线: 业绩正就直接买(披露后首日) vs 择时: 业绩正+EGPT回踩(回踩中分≥60)")
    print(f"{'#' * 96}")

    _table("总体对比", [('中报池基线(业绩正就买)', base), ('中报池+EGPT回踩择时', sig)])

    _table("EGPT 按回踩买点分档", [
        ('分≥70 (最优)', sig[sig['pullback_score'] >= 70]),
        ('分60-70', sig[(sig['pullback_score'] >= 60) & (sig['pullback_score'] < 70)]),
    ])

    _table("EGPT 按回踩天数", [
        (f'回踩{n}日', sig[sig['pullback_days'] == n]) for n in (1, 2)
    ])

    _table("EGPT 按年度", [(y, sig[sig['year'] == y]) for y in sorted(sig['year'].unique())])

    if 'month' in sig.columns:
        sig = sig.copy()
        sig['month'] = sig['sig_date'].str[:6]
        _table("EGPT 按月", [(m, sig[sig['month'] == m]) for m in sorted(sig['month'].unique())])

    def _dty_bucket(x):
        if x is None: return '--'
        if x >= 100: return '扣非增速≥100%'
        if x >= 50: return '50~100%'
        return '30~50%'
    _table("EGPT 按扣非增速段", [
        (b, sig[sig['dty'].apply(_dty_bucket) == b]) for b in ('扣非增速≥100%', '50~100%', '30~50%')
    ])

    # ── 主题热度分组（结合主题热度） ──
    if with_heat or 'theme_heat' in sig.columns:
        def _hb(x):
            if x is None: return '无主题/无ETF'
            if x >= 0.10: return '高热度(ETF20日≥10%)'
            if x >= 0.0: return '中热度(0~10%)'
            return '低热度(ETF20日<0)'
        sig_h = sig.copy()
        base_h = base.copy()
        sig_h['hb'] = sig_h['theme_heat'].apply(_hb)
        base_h['hb'] = base_h['theme_heat'].apply(_hb)
        _table("基线 按主题热度", [(b, base_h[base_h['hb'] == b]) for b in
               ('高热度(ETF20日≥10%)', '中热度(0~10%)', '低热度(ETF20日<0)', '无主题/无ETF')])
        _table("EGPT 按主题热度", [(b, sig_h[sig_h['hb'] == b]) for b in
               ('高热度(ETF20日≥10%)', '中热度(0~10%)', '低热度(ETF20日<0)', '无主题/无ETF')])

        # EGPT 高热度 内部再按分档
        hot = sig_h[sig_h['hb'] == '高热度(ETF20日≥10%)']
        _table("EGPT 高热度内 按分档", [
            ('分≥70', hot[hot['pullback_score'] >= 70]),
            ('分60-70', hot[(hot['pullback_score'] >= 60) & (hot['pullback_score'] < 70)]),
        ])
        # 按主题明细（EGPT 信号）
        thg = sig.groupby('theme')
        thg = sorted([(k, v) for k, v in thg if len(v) >= 3], key=lambda x: -_fmt(x[1])[3])
        _table("EGPT 按主题明细(≥3笔,按T+3均降序)", thg)

        # ── 组合过滤验证: EGPT + 主题热度甜区(5~15%) + 主题白名单 ──
        WHITELIST = {"智能驾驶", "信创", "新能源车", "消费电子", "半导体", "创新药",
                     "机器人", "游戏", "建筑装饰", "传媒", "能源金属", "商业航天"}
        sw = (sig['theme_heat'] >= 0.05) & (sig['theme_heat'] <= 0.15)
        wl = sig['theme'].isin(WHITELIST)
        _table("EGPT 组合过滤验证(甜区5~15%)", [
            ('EGPT 全信号', sig),
            ('EGPT + 甜区热度(5~15%)', sig[sw]),
            ('EGPT + 白名单主题', sig[wl]),
            ('EGPT + 甜区×白名单', sig[sw & wl]),
            ('EGPT + 甜区×白名单×扣非≥50%', sig[sw & wl & (sig['dty'] >= 50)]),
        ])

    # 导出CSV
    out = os.path.join(CACHE_DIR, 'zhongbao_egpt_backtest_detail.csv')
    sig.to_csv(out, index=False, encoding='utf-8-sig')
    print(f"\n  信号明细已导出: {out}")


def grid_search():
    """网格搜索: 以 T+5 胜率最高为目标, 精调 热度区间×白名单×回踩分×回踩天数×增速 组合"""
    if not os.path.exists(BT_DB):
        print(f"回测库不存在: {BT_DB}")
        return
    conn = sqlite3.connect(BT_DB)
    sig = pd.read_sql_query('SELECT * FROM egpt_sig', conn)
    conn.close()
    print("\n[加载主题热度] ...")
    stock2theme, theme2etf, etf_series = load_theme_heat_map()
    sig = enrich_theme_heat(sig, stock2theme, theme2etf, etf_series)

    WL_FULL = {"智能驾驶", "信创", "新能源车", "消费电子", "半导体", "创新药",
               "机器人", "游戏", "建筑装饰", "传媒", "能源金属", "商业航天"}
    WL_CORE = {"智能驾驶", "信创", "新能源车", "消费电子", "半导体", "创新药",
               "机器人", "游戏"}

    print(f"\n{'═' * 96}")
    print(f"  T+5 胜率网格搜索（样本{len(sig)}笔, 仅列样本≥20）")
    print(f"{'═' * 96}")
    print(f"  {'组合':<30} {'信号':>5} {'T+1胜':>7} {'T+5均':>8} {'T+5胜':>7} {'T+10均':>8} {'T+20均':>8}")
    print('  ' + '─' * 94)

    combos = []
    for heat_rng in [(None, None), (0, 0.05), (0, 0.10), (0, 0.15), (0, 0.20),
                     (0.05, 0.15), (-0.05, 0.15), (0.05, 0.20)]:
        for wl in [None, 'full', 'core']:
            for sc in [None, 70]:
                for pb in [None, (1, 2)]:
                    for dty in [None, 50]:
                        s = sig
                        name = []
                        if heat_rng[0] is not None:
                            s = s[(s['theme_heat'] >= heat_rng[0]) & (s['theme_heat'] <= heat_rng[1])]
                            name.append(f"热度{heat_rng[0]}-{heat_rng[1]}")
                        if wl == 'full':
                            s = s[s['theme'].isin(WL_FULL)]
                            name.append("白名单全")
                        elif wl == 'core':
                            s = s[s['theme'].isin(WL_CORE)]
                            name.append("白名单核心")
                        if sc == 70:
                            s = s[s['pullback_score'] >= 70]
                            name.append("分≥70")
                        if pb == (1, 2):
                            s = s[s['pullback_days'].isin([1, 2])]
                            name.append("回踩1-2日")
                        if dty == 50:
                            s = s[s['dty'] >= 50]
                            name.append("扣非≥50%")
                        if len(s) < 20:
                            continue
                        n = len(s)
                        w5 = (s['t5'] > 0).mean() * 100
                        m1 = s['t1'].mean() * 100
                        m5 = s['t5'].mean() * 100
                        m10 = s['t10'].mean() * 100
                        m20 = s['t20'].mean() * 100
                        combos.append((name, n, w5, m1, m5, m10, m20))

    combos.sort(key=lambda x: -x[2])
    for name, n, w5, m1, m5, m10, m20 in combos[:25]:
        tag = " · ".join(name) if name else "全信号"
        print(f"  {tag:<30} {n:>5} {m1:>6.1f}% {m5:>+7.2f}% {w5:>6.1f}% {m10:>+7.2f}% {m20:>+7.2f}%")

    print('  ' + '─' * 94)
    print("  说明: T+1胜=T+1上涨率; 组合为空名=仅基础EGPT信号")
    # 保存 CSV
    out = os.path.join(CACHE_DIR, 'zhongbao_egpt_grid_result.csv')
    pd.DataFrame([{'组合': ' · '.join(c[0]) or '全信号', '信号': c[1], 'T+1胜率': c[3],
                   'T+5均': c[4], 'T+5胜率': c[2], 'T+10均': c[5], 'T+20均': c[6]}
                  for c in combos[:50]]).to_csv(out, index=False, encoding='utf-8-sig')
    print(f"  网格结果已导出: {out}")


def buy_point_opt():
    """买点优化: 买点类型 × 买入时点 × 止损 三重维度
    （在最优组合 甜区5~15%×白名单×扣非≥50% 内分析，需先重跑回测重建含买点字段的信号库）"""
    if not os.path.exists(BT_DB):
        print(f"回测库不存在: {BT_DB}")
        return
    conn = sqlite3.connect(BT_DB)
    sig = pd.read_sql_query('SELECT * FROM egpt_sig', conn)
    conn.close()
    if 'buy_point' not in sig.columns:
        print("[错误] 信号库无买点字段，请先重跑回测重建(会全量重算，约数分钟)")
        return

    print("\n[加载主题热度] ...")
    stock2theme, theme2etf, etf_series = load_theme_heat_map()
    sig = enrich_theme_heat(sig, stock2theme, theme2etf, etf_series)

    WHITELIST = {"智能驾驶", "信创", "新能源车", "消费电子", "半导体", "创新药",
                 "机器人", "游戏", "建筑装饰", "传媒", "能源金属", "商业航天"}
    sw = (sig['theme_heat'] >= 0.05) & (sig['theme_heat'] <= 0.15)
    best = sig[sw & sig['theme'].isin(WHITELIST) & (sig['dty'] >= 50)].copy()

    print(f"\n{'#' * 96}")
    print(f"  买点优化分析（最优组合: 甜区5~15%×白名单×扣非≥50%，{len(best)} 笔）")
    print(f"{'#' * 96}")

    # ── A. 买点类型分层 ──
    _table("A. 最优组合内 按买点类型(信号日收盘买入)", [
        ('买点2(缩量回踩VWAP确认)', best[best['buy_point'] == '买点2(缩量回踩VWAP确认)']),
        ('买点1(放量突破VWAP+筹码峰)', best[best['buy_point'] == '买点1(放量突破VWAP+筹码峰)']),
        ('未突破', best[best['buy_point'] == '未突破']),
    ])

    # ── B. 买入时点: 信号日收盘 vs 次日开盘（真实可执行口径） ──
    # 路径: path['closes'/'opens'][0]=信号日, [1]=次日...
    def _entry_ret(r, entry_idx):
        p = json.loads(r['path'])
        closes, opens = p['closes'], p['opens']
        if len(closes) < 21 or opens[entry_idx] <= 0:
            return None
        entry = opens[entry_idx]
        out = {}
        for h, col in ((1, 't1'), (3, 't3'), (5, 't5'), (10, 't10'), (20, 't20')):
            out[col] = closes[h] / entry - 1
        return out

    next_open_rows = []
    for _, r in best.iterrows():
        v = _entry_ret(r, 1)
        if v is not None:
            next_open_rows.append(v)
    no_df = pd.DataFrame(next_open_rows)
    _table("B. 买入时点对比(最优组合)", [
        ('信号日收盘买入', best[['t1', 't3', 't5', 't10', 't20']]),
        ('次日开盘买入(真实可执行)', no_df[['t1', 't3', 't5', 't10', 't20']]),
    ])

    # ── C. 止损模拟（从次日开盘买入; 触及止损按止损价卖出, 否则持有至T+20） ──
    def _stop_sim(r, stop_mode):
        p = json.loads(r['path'])
        closes, lows, opens = p['closes'], p['lows'], p['opens']
        if len(closes) < 21 or opens[1] <= 0:
            return None
        entry = opens[1]
        if stop_mode == 'none':
            return closes[20] / entry - 1
        if stop_mode == 'atr':
            stop = r['atr_stop']
            if not stop or stop <= 0:
                return None
        else:
            stop = entry * (1.0 - stop_mode)
        for i in range(1, len(closes)):
            if lows[i] <= stop:
                return stop / entry - 1
        return closes[20] / entry - 1

    print(f"\n{'═' * 96}")
    print("  C. 止损对比（次日开盘买入, 触及止损按止损价卖, 否则持有至T+20）")
    print(f"{'═' * 96}")
    print(f"  {'止损方案':<30} {'信号':>5} {'均值':>8} {'胜率':>7} {'最大亏损':>9} {'超-10%占比':>9}")
    print('  ' + '─' * 94)
    for scope_name, scope in (("全部(买点1+2)", best[best['buy_point'].astype(str) != '未突破']),
                              ("仅买点2(回踩VWAP确认)", best[best['buy_point'] == '买点2(缩量回踩VWAP确认)'])):
        for label, mode in (("无止损", 'none'), ("ATR止损(信号日-2ATR)", 'atr'),
                            ("固定-5%", 0.05), ("固定-8%", 0.08)):
            vals = [v for v in (_stop_sim(r, mode) for _, r in scope.iterrows()) if v is not None]
            if not vals:
                continue
            arr = np.array(vals)
            win = (arr > 0).mean() * 100
            worst = arr.min() * 100
            big = (arr < -0.10).mean() * 100
            print(f"  {scope_name}·{label:<12} {len(arr):>5} {arr.mean()*100:>+7.2f}% {win:>8.1f}% {worst:>+8.2f}% {big:>8.1f}%")


def main():
    parser = argparse.ArgumentParser(description='中报猎手×EGPT回踩 TDX回测')
    parser.add_argument('--start', default='20230101')
    parser.add_argument('--end', default='20260818')
    parser.add_argument('--status', action='store_true', help='查看历史回测统计')
    parser.add_argument('--heat', action='store_true', help='查看统计并补算主题热度')
    parser.add_argument('--grid', action='store_true', help='T+5胜率网格搜索')
    parser.add_argument('--buypt', action='store_true', help='买点优化分析(买点类型×买入时点×止损)')
    args = parser.parse_args()

    if args.buypt:
        buy_point_opt()
        return
    if args.grid:
        grid_search()
        return
    if args.status or args.heat:
        show_stats(with_heat=args.heat)
        return
    base_rows, sig_rows = run_backtest(args.start, args.end)
    if sig_rows:
        show_stats()


if __name__ == '__main__':
    main()
