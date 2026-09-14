# -*- coding: utf-8 -*-
"""
DLG Picker V1.0 —— 高股息 + 低估值 + 高成长 选股策略
================================================================
三条主线交集：能分红（现金流真实）× 便宜（估值不贵）× 还在成长（业绩没熄火）
纯多头基本面中长线选股，不涉及择时；每日收盘后可跑，输出 A/B/C 级候选池。

数据源（全部走项目既有缓存，缓存缺失才按需补接口）：
  ① 估值/股息/市值/换手  stock_cache.daily_basic_market(date) -> daily_basic_cache 表
  ② 行情/成交额/流动性     stock_cache.daily_market(date)       -> daily_cache 表
  ③ 成长与质量(多期)       stock_data.db :: fina_indicator_cache 表（净利/营收同比/ROE/现金流/负债率）
  ④ 扣非同比+单季加速      cache_daily/fin_ind_{年}H1_full.parquet 等全市场 fina_indicator 全字段快照
  ⑤ 股息率历史持续性       stock_data.db :: daily_basic_cache 逐日 dv_ttm 快照，回看 3 年
  ⑥ 行业与上市日           cache_daily/stock_basic.csv

评分模型（统一 0~100，四支柱加权）：
  DLG = 0.30*DIV(股息) + 0.25*VAL(估值) + 0.30*GRO(成长) + 0.15*QUA(质量)
  DIV = 0.60*当期股息率(绝对水平55%+横截面分位45%) + 0.40*历史持续性
         持续性 = 分红未中断比例20% + 3年最低正股息(股息率地板)45% + 分红趋势(近1年/前1年)35%
  等级：A ≥80 / B ≥70 / C ≥60（低于 60 不输出）
  估值支柱采用行业内相对分位（同行业样本≥10 只时），规避行业估值中枢差异；
  股息与质量支柱采用绝对阈值映射，保证横向可比。

无未来函数：仅使用 scan_date 当日可见的行情与 ann_date ≤ scan_date 的财报。

用法:
  python dlg_picker.py                          # 默认取有效交易日
  python dlg_picker.py --date 20260911 --top 30
  python dlg_picker.py --no-api                 # 纯本地缓存，不调用任何接口
  python dlg_picker.py --min-yield 4 --max-pe 20 --min-growth 20
"""
import os
import sys
import re
import argparse
import sqlite3
import time

import numpy as np
import pandas as pd

SOLO_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SOLO_DIR)

from stock_cache import (  # noqa: E402
    daily_market, daily_basic_market, get_effective_date,
    load_stock_basic, DB_PATH,
)

CACHE_DIR = r'D:\mystock\cache_daily'
OUT_DIR = os.path.join(SOLO_DIR, 'report_daily')

# ═══════════════════════════════════════════════════════
# 参数配置
# ═══════════════════════════════════════════════════════
DLG_CONFIG = {
    # ── 硬门槛（不满足直接淘汰）──
    'min_dividend_yield': 3.0,      # 股息率(dv_ttm) 下限 %
    'max_dividend_yield': 15.0,     # 异常高股息剔除（多为特别股息/股价崩塌）
    'min_div_persist_ratio': 0.5,   # 近3年 dv_ttm>0 的交易日占比下限（分红未中断）
    'div_history_years': 3,         # 股息率历史回看年数
    'min_pe_ttm': 0.0,              # 必须为正（剔除亏损）
    'max_pe_ttm': 30.0,
    'max_pb': 8.0,
    'min_netprofit_yoy': 10.0,      # 最新报告期净利同比下限 %
    'min_dt_netprofit_yoy': 0.0,    # 扣非同比下限 %（不许经营利润下滑）
    'min_roe': 6.0,
    'min_gross_margin': 10.0,
    'max_debt_to_assets': 80.0,
    'min_total_mv_yi': 80.0,        # 总市值下限（亿元）
    'min_amount_yi': 0.3,           # 当日成交额下限（亿元），保证可交易
    'min_list_days': 250,           # 上市满 1 年
    'require_latest_period': True,  # 必须已披露最新报告期
    'exclude_st': True,
    'exclude_bj': True,             # 剔除北交所
    'exclude_financial': True,      # 剔除银行/保险/证券等（成长与负债口径不可比）

    # ── 支柱权重 ──
    'w_div': 0.30,
    'w_div_current': 0.60,          # DIV 支柱内：当期股息率
    'w_div_persist': 0.40,          # DIV 支柱内：历史持续性
    'w_val': 0.25,
    'w_gro': 0.30,
    'w_qua': 0.15,

    # ── 等级门槛 ──
    'grade_a': 80.0,
    'grade_b': 70.0,
    'grade_c': 60.0,

    # ── 其它 ──
    'min_industry_n': 10,           # 行业内分位所需最小样本数
    'top_n': 30,
    'make_pdf': True,               # 同时输出 PDF（reportlab + 中文字体）
}

FINANCIAL_INDUSTRIES = {'银行', '保险', '证券', '多元金融', '信托'}

# 绝对阈值 → 分数 映射表（np.interp 线性插值）
_MAP = {
    'dividend': ([-1, 1, 2, 3, 4, 5, 6, 8], [0, 10, 30, 50, 68, 82, 92, 100]),
    'div_trend': ([0, 0.4, 0.7, 1.0, 1.3, 2.0], [0, 25, 55, 75, 90, 100]),
    'netprofit_yoy': ([-30, -10, 0, 10, 20, 30, 50, 100], [0, 0, 20, 50, 70, 85, 95, 100]),
    'or_yoy': ([-30, -10, 0, 5, 10, 20, 30, 50], [0, 10, 25, 45, 60, 80, 90, 100]),
    'accel': ([-40, -20, -5, 0, 10, 20, 40], [0, 10, 35, 55, 75, 90, 100]),
    'mean4': ([-40, -10, 0, 10, 20, 30], [0, 10, 40, 65, 85, 100]),
    'roe': ([-5, 0, 5, 8, 12, 15, 20, 30], [0, 10, 35, 50, 70, 85, 95, 100]),
    'gross_margin': ([0, 5, 10, 20, 30, 40, 60], [0, 15, 35, 60, 80, 92, 100]),
    'debt': ([0, 20, 30, 40, 50, 60, 70, 85], [100, 100, 96, 86, 70, 50, 30, 5]),
}


def _score_by(value, key):
    """按映射表把绝对值线性插值为 0~100 分（支持标量与 Series）"""
    x, y = _MAP[key]
    v = np.asarray(value, dtype=float)
    out = np.interp(np.nan_to_num(v, nan=x[0]), x, y)
    return out


def _rank_score(s, higher_better=True, df_ref=None, ind_col='industry', min_n=10):
    """横截面分位打分（0~100）；同行业内样本≥min_n 时用行业内分位，否则用全市场分位"""
    s = pd.to_numeric(s, errors='coerce')
    mkt = s.rank(pct=True, ascending=higher_better) * 100
    out = mkt
    if df_ref is not None and ind_col in df_ref.columns:
        tmp = pd.DataFrame({ind_col: df_ref[ind_col].values, '_v': s.values}, index=s.index)
        ind = tmp.groupby(ind_col)['_v'].rank(pct=True, ascending=higher_better) * 100
        n = tmp.groupby(ind_col)['_v'].transform('count')
        out = ind.where(n >= min_n, mkt)
    return out.fillna(50.0)


# ═══════════════════════════════════════════════════════
# 数据加载
# ═══════════════════════════════════════════════════════
def load_market_snapshot(date, use_api=True):
    """当日全市场行情 + 估值快照（优先命中缓存，缺失才调接口）"""
    basic = daily_basic_market(date, auto_fill=use_api, silent=True)
    quote = daily_market(date, auto_fill=use_api, silent=True)
    if basic is None or basic.empty:
        raise RuntimeError(f'daily_basic_cache 无 {date} 数据且接口不可用')
    basic = basic.copy()
    basic['trade_date'] = basic['trade_date'].astype(str)
    if quote is not None and not quote.empty:
        q = quote[['ts_code', 'close', 'pct_chg', 'amount', 'vol']].copy()
        basic = basic.merge(q, on='ts_code', how='left')
    else:
        for c in ('close', 'pct_chg', 'amount', 'vol'):
            basic[c] = np.nan
    basic['total_mv_yi'] = pd.to_numeric(basic['total_mv'], errors='coerce') / 1e4
    basic['circ_mv_yi'] = pd.to_numeric(basic['circ_mv'], errors='coerce') / 1e4
    basic['amount_yi'] = pd.to_numeric(basic['amount'], errors='coerce') / 1e5  # 千元 → 亿元
    return basic, (quote if quote is not None else pd.DataFrame())


def load_fin_history(date):
    """多期财报指标（fina_indicator_cache 表）：最新期 + 近 4 期稳定性"""
    cols = ('ts_code, ann_date, end_date, netprofit_yoy, or_yoy, ocf_yoy, '
            'ocf_to_or, roe, grossprofit_margin, debt_to_assets')
    con = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            f'SELECT {cols} FROM fina_indicator_cache WHERE ann_date <= ?',
            con, params=(str(date),))
    finally:
        con.close()
    if df.empty:
        return pd.DataFrame(), None
    df['ann_date'] = df['ann_date'].astype(str)
    df['end_date'] = df['end_date'].astype(str)
    # 同一报告期取 ann_date 最新一条（财报修正以最新为准）
    df = df.sort_values(['ts_code', 'end_date', 'ann_date'])
    df = df.drop_duplicates(['ts_code', 'end_date'], keep='last')
    latest_period = df['end_date'].max()

    df = df.sort_values(['ts_code', 'end_date'], ascending=[True, False])
    df['_rk'] = df.groupby('ts_code').cumcount()
    grp = df[df['_rk'] < 4].groupby('ts_code')
    hist = pd.DataFrame({
        'period': df.groupby('ts_code')['end_date'].first(),
        'npy_latest': grp['netprofit_yoy'].first(),
        'or_yoy_latest': grp['or_yoy'].first(),
        'ocf_yoy_latest': grp['ocf_yoy'].first(),
        'ocf_to_or_latest': grp['ocf_to_or'].first(),
        'roe_latest': grp['roe'].first(),
        'margin_latest': grp['grossprofit_margin'].first(),
        'debt_latest': grp['debt_to_assets'].first(),
        'npy_mean4': grp['netprofit_yoy'].mean(),
        'npy_min4': grp['netprofit_yoy'].min(),
        'npy_pos_ratio': grp['netprofit_yoy'].apply(lambda s: float((s > 0).mean())),
        'n_period': grp['end_date'].count(),
    }).reset_index()
    return hist, latest_period


def load_fin_full(date, hist):
    """最新报告期全字段快照（扣非同比 / 单季加速 / 营收同比），缺失则优雅降级"""
    empty = pd.DataFrame(columns=['ts_code', 'dt_netprofit_yoy', 'tr_yoy', 'q1_profit_yoy', 'q2_profit_yoy'])
    if hist is None or hist.empty:
        return empty
    period = str(hist['period'].max())
    if len(period) != 8:
        return empty
    tag = f"{period[:4]}H1_full" if period[4:] == '0630' else (
        f"{period[:4]}Q1_full" if period[4:] == '0331' else (
            f"{period[:4]}Q3_full" if period[4:] == '0930' else f"{period[:4]}_full"))
    fp = os.path.join(CACHE_DIR, f'fin_ind_{tag}.parquet')
    if not os.path.exists(fp):
        print(f'[提示] 未见 {os.path.basename(fp)}，扣非同比/单季加速项自动跳过')
        return empty
    try:
        df = pd.read_parquet(fp)
    except Exception as e:
        print(f'[提示] 读取 {os.path.basename(fp)} 失败({e})，扣非/加速项跳过')
        return empty
    df = df[df['end_date'].astype(str) == period].copy()
    if 'ann_date' in df.columns:
        df = df[df['ann_date'].astype(str) <= str(date)]
    keep = ['ts_code'] + [c for c in ('dt_netprofit_yoy', 'tr_yoy', 'q1_profit_yoy', 'q2_profit_yoy')
                          if c in df.columns]
    df = df[keep].drop_duplicates('ts_code', keep='last')
    return df


def load_div_history(date, codes, years=3):
    """近 N 年股息率历史（daily_basic_cache 逐日 dv_ttm 快照）：
    返回 分红未中断比例 / 3年均值 / 3年最低正股息 / 分红趋势(近1年÷前1年)
    仅对已通过当期股息率初筛的代码聚合，避免全表扫描。"""
    cols = ['ts_code', 'dv_pos_cnt', 'dv_cnt', 'dv_mean', 'dv_min', 'dv_sum',
            'dv_sum_y1', 'dv_n_y1']
    empty = pd.DataFrame(columns=['ts_code', 'dv_pos_ratio', 'dv_mean3', 'dv_min3',
                                  'dv_trend', 'dv_days'])
    if not codes:
        return empty
    dt = pd.to_datetime(str(date), format='%Y%m%d')
    start = (dt - pd.Timedelta(days=365 * years)).strftime('%Y%m%d')
    y1 = (dt - pd.Timedelta(days=365)).strftime('%Y%m%d')
    sql = f"""
        SELECT ts_code,
               SUM(CASE WHEN dv_ttm > 0 THEN 1 ELSE 0 END) AS dv_pos_cnt,
               COUNT(dv_ttm) AS dv_cnt,
               AVG(dv_ttm) AS dv_mean,
               MIN(CASE WHEN dv_ttm > 0 THEN dv_ttm END) AS dv_min,
               SUM(dv_ttm) AS dv_sum,
               SUM(CASE WHEN trade_date >= ? THEN dv_ttm END) AS dv_sum_y1,
               SUM(CASE WHEN trade_date >= ? THEN 1 ELSE 0 END) AS dv_n_y1
        FROM daily_basic_cache
        WHERE trade_date <= ? AND trade_date >= ? AND ts_code IN ({{ph}})
        GROUP BY ts_code
    """
    frames = []
    con = sqlite3.connect(DB_PATH)
    try:
        for i in range(0, len(codes), 500):
            chunk = codes[i:i + 500]
            ph = ','.join('?' * len(chunk))
            rows = pd.read_sql_query(sql.format(ph=ph), con,
                                     params=[y1, y1, str(date), start] + chunk)
            frames.append(rows)
    finally:
        con.close()
    if not frames:
        return empty
    h = pd.concat(frames, ignore_index=True)[cols]
    h['dv_mean'] = pd.to_numeric(h['dv_mean'], errors='coerce')
    h['dv_sum'] = pd.to_numeric(h['dv_sum'], errors='coerce')
    h['dv_sum_y1'] = pd.to_numeric(h['dv_sum_y1'], errors='coerce')
    h['dv_n_y1'] = pd.to_numeric(h['dv_n_y1'], errors='coerce')
    h['dv_days'] = h['dv_cnt']
    h['dv_pos_ratio'] = (h['dv_pos_cnt'] / h['dv_cnt'].replace(0, np.nan))
    h['dv_mean3'] = h['dv_mean'].round(3)
    h['dv_min3'] = pd.to_numeric(h['dv_min'], errors='coerce').round(3)
    n_y2 = h['dv_cnt'] - h['dv_n_y1']
    mean_y1 = h['dv_sum_y1'] / h['dv_n_y1'].replace(0, np.nan)
    mean_y2 = (h['dv_sum'] - h['dv_sum_y1']) / n_y2.replace(0, np.nan)
    h['dv_trend'] = (mean_y1 / mean_y2.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).round(3)
    return h[['ts_code', 'dv_pos_ratio', 'dv_mean3', 'dv_min3', 'dv_trend', 'dv_days']]


def load_industry():
    """行业 + 上市日（stock_basic.csv 缓存）"""
    sb = load_stock_basic()
    if sb is None or sb.empty:
        return pd.DataFrame(columns=['ts_code', 'name', 'industry', 'list_date'])
    return sb[['ts_code', 'name', 'industry', 'list_date']].copy()


# ═══════════════════════════════════════════════════════
# 硬门槛过滤
# ═══════════════════════════════════════════════════════
def apply_gates(df, cfg, date):
    """逐条门槛过滤，返回 (通过 df, 漏斗统计 list)"""
    funnel = []

    def _step(name, mask):
        before = len(df)
        funnel.append((name, before, int(mask.sum()), before - int(mask.sum())))
        return df[mask]

    df = _step('基础池(当日有估值快照)', df['ts_code'].notna())
    df = _step(f'股息率 dv_ttm ≥ {cfg["min_dividend_yield"]}%',
               df['dv_ttm'].fillna(0) >= cfg['min_dividend_yield'])
    df = _step(f'股息率 ≤ {cfg["max_dividend_yield"]}%(剔除异常高息)',
               df['dv_ttm'].fillna(0) <= cfg['max_dividend_yield'])
    if cfg['min_div_persist_ratio'] > 0:
        df = _step(f'近{cfg["div_history_years"]}年分红未中断(占比≥{cfg["min_div_persist_ratio"]:.0%})',
                   df['dv_pos_ratio'].fillna(0) >= cfg['min_div_persist_ratio'])
    pe = pd.to_numeric(df['pe_ttm'], errors='coerce')
    df = _step(f'{cfg["min_pe_ttm"]} < PE(TTM) ≤ {cfg["max_pe_ttm"]}',
               (pe > cfg['min_pe_ttm']) & (pe <= cfg['max_pe_ttm']))
    pb = pd.to_numeric(df['pb'], errors='coerce')
    df = _step(f'PB ≤ {cfg["max_pb"]} 且 > 0', (pb > 0) & (pb <= cfg['max_pb']))
    df = _step(f'总市值 ≥ {cfg["min_total_mv_yi"]}亿', df['total_mv_yi'] >= cfg['min_total_mv_yi'])
    df = _step(f'成交额 ≥ {cfg["min_amount_yi"]}亿', df['amount_yi'].fillna(0) >= cfg['min_amount_yi'])
    if cfg['exclude_st']:
        df = _step('剔除 ST/*ST/退市', ~df['name'].fillna('').str.contains('ST|退', regex=True))
    if cfg['exclude_bj']:
        df = _step('剔除北交所', ~df['ts_code'].astype(str).str.endswith('.BJ'))
    if cfg['exclude_financial']:
        df = _step('剔除金融行业', ~df['industry'].fillna('').isin(FINANCIAL_INDUSTRIES))
    ld = pd.to_datetime(df['list_date'], format='%Y%m%d', errors='coerce')
    age = (pd.to_datetime(str(date), format='%Y%m%d') - ld).dt.days
    df = _step(f'上市满 {cfg["min_list_days"]} 天', age >= cfg['min_list_days'])
    df = _step(f'净利同比 ≥ {cfg["min_netprofit_yoy"]}%',
               df['npy_latest'] >= cfg['min_netprofit_yoy'])
    if 'dt_netprofit_yoy' in df.columns:
        df = _step(f'扣非同比 ≥ {cfg["min_dt_netprofit_yoy"]}%',
                   df['dt_netprofit_yoy'].fillna(-99) >= cfg['min_dt_netprofit_yoy'])
    df = _step(f'ROE ≥ {cfg["min_roe"]}%', df['roe_latest'] >= cfg['min_roe'])
    df = _step(f'毛利率 ≥ {cfg["min_gross_margin"]}%',
               df['margin_latest'].fillna(0) >= cfg['min_gross_margin'])
    df = _step(f'资产负债率 ≤ {cfg["max_debt_to_assets"]}%',
               df['debt_latest'].fillna(999) <= cfg['max_debt_to_assets'])
    if cfg['require_latest_period']:
        df = _step(f'已披露最新报告期({date[:4]}年最新)', df['is_latest_period'].fillna(False))
    return df, funnel


# ═══════════════════════════════════════════════════════
# 四支柱打分
# ═══════════════════════════════════════════════════════
def score_pillars(df, cfg):
    """DIV / VAL / GRO / QUA 四支柱打分 + DLG 综合分 + 等级"""
    d = df.copy()
    d['dv_ttm'] = pd.to_numeric(d['dv_ttm'], errors='coerce')
    d['pe_ttm'] = pd.to_numeric(d['pe_ttm'], errors='coerce')
    d['pb'] = pd.to_numeric(d['pb'], errors='coerce')
    d['ps'] = pd.to_numeric(d['ps'], errors='coerce')

    # ── DIV 股息：当期水平 60% + 历史持续性 40% ──
    dv_abs = _score_by(d['dv_ttm'].values, 'dividend')
    dv_rank = _rank_score(d['dv_ttm'], True, d, min_n=cfg['min_industry_n']).values
    cur = 0.55 * dv_abs + 0.45 * dv_rank
    # 持续性 = 分红未中断比例 20% + 3年最低正股息(股息率地板) 45% + 分红趋势 35%
    # 实测：高分股息池里"未中断比例"几乎恒为 100%，区分度主要来自地板与趋势
    pos_ratio = pd.to_numeric(d['dv_pos_ratio'], errors='coerce').fillna(0).clip(0, 1).values
    floor_s = _score_by(pd.to_numeric(d['dv_min3'], errors='coerce').fillna(0).values, 'dividend')
    trend = pd.to_numeric(d['dv_trend'], errors='coerce')
    trend_s = _score_by(trend.fillna(1.0).values, 'div_trend')
    trend_s = np.where(trend.isna().values, 50.0, trend_s)          # 历史不足 → 中性
    persist = 0.20 * (pos_ratio * 100) + 0.45 * floor_s + 0.35 * trend_s
    has_hist = pd.to_numeric(d['dv_days'], errors='coerce').notna().values
    persist = np.where(has_hist, persist, 50.0)                      # 无历史 → 中性
    d['S_DIV持久'] = np.round(persist, 1)
    d['S_DIV'] = np.round(cfg['w_div_current'] * cur + cfg['w_div_persist'] * persist, 1)

    # ── VAL 估值（行业中性，越低越好）──
    pe_r = _rank_score(d['pe_ttm'], False, d, min_n=cfg['min_industry_n']).values
    pb_r = _rank_score(d['pb'], False, d, min_n=cfg['min_industry_n']).values
    ps_r = _rank_score(d['ps'], False, d, min_n=cfg['min_industry_n']).values
    # PEG：以净利同比为增速代理（增速 ≤0 已在前置门槛剔除）
    peg = d['pe_ttm'] / d['npy_latest'].clip(lower=1)
    d['PEG'] = np.round(peg, 2)
    peg_score = np.interp(np.nan_to_num(peg.values, nan=9), [0.2, 0.5, 1.0, 1.5, 2.5, 4.0],
                          [100, 95, 75, 55, 30, 8])
    d['S_VAL'] = np.round(0.35 * pe_r + 0.25 * pb_r + 0.15 * ps_r + 0.25 * peg_score, 1)

    # ── GRO 成长（缺失项自动重新归一）──
    parts = {
        'npy': (_score_by(d['npy_latest'].values, 'netprofit_yoy'), 0.30),
        'or': (_score_by(d['or_yoy_latest'].values, 'or_yoy'), 0.20),
        'stab': (None, 0.15),
    }
    if 'dt_netprofit_yoy' in d.columns:
        parts['dt'] = (_score_by(d['dt_netprofit_yoy'].fillna(-99).values, 'netprofit_yoy'), 0.20)
    if 'q1_profit_yoy' in d.columns and 'q2_profit_yoy' in d.columns:
        accel = pd.to_numeric(d['q2_profit_yoy'], errors='coerce') - pd.to_numeric(d['q1_profit_yoy'], errors='coerce')
        d['Q2加速'] = np.round(accel, 1)
        if accel.notna().sum() > 0:
            parts['accel'] = (_score_by(accel.fillna(-40).values, 'accel'), 0.15)
    # 增长稳定性：近 4 期正向比例 + 均值水平
    mean4_score = _score_by(d['npy_mean4'].values, 'mean4')
    parts['stab'] = (0.55 * mean4_score + 0.45 * (d['npy_pos_ratio'].fillna(0).values * 100), 0.15)

    total_w = sum(w for _, w in parts.values())
    gro = np.zeros(len(d))
    for v, w in parts.values():
        gro += np.nan_to_num(np.asarray(v, dtype=float)) * (w / total_w)
    d['S_GRO'] = np.round(gro, 1)

    # ── QUA 质量 ──
    roe_s = _score_by(d['roe_latest'].values, 'roe')
    margin_s = _score_by(d['margin_latest'].fillna(0).values, 'gross_margin')
    debt_s = _score_by(d['debt_latest'].fillna(80).values, 'debt')
    cf_rank = _rank_score(d['ocf_yoy_latest'], True, d, min_n=cfg['min_industry_n']).values
    cf_ocf = _rank_score(d['ocf_to_or_latest'], True, d, min_n=cfg['min_industry_n']).values
    cf_s = 0.5 * cf_rank + 0.5 * cf_ocf
    d['S_QUA'] = np.round(0.35 * roe_s + 0.20 * margin_s + 0.25 * cf_s + 0.20 * debt_s, 1)

    # ── 综合分与等级 ──
    d['DLG'] = np.round(cfg['w_div'] * d['S_DIV'] + cfg['w_val'] * d['S_VAL']
                        + cfg['w_gro'] * d['S_GRO'] + cfg['w_qua'] * d['S_QUA'], 1)
    d['等级'] = np.where(d['DLG'] >= cfg['grade_a'], 'A',
                        np.where(d['DLG'] >= cfg['grade_b'], 'B',
                                 np.where(d['DLG'] >= cfg['grade_c'], 'C', 'D')))
    # 风险标签
    flags = []
    for _, r in d.iterrows():
        f = []
        if r['dv_ttm'] >= 10:
            f.append('股息率异常高(核实是否特别股息)')
        if pd.notna(r.get('dv_mean3')) and r['dv_mean3'] > 0 and r['dv_ttm'] > 1.5 * r['dv_mean3']:
            f.append('当期股息率＞3年均值1.5倍(疑一次性分红)')
        if pd.notna(r.get('dv_pos_ratio')) and r['dv_pos_ratio'] < 0.8:
            f.append(f'近3年分红有中断(占比{float(r["dv_pos_ratio"]):.0%})')
        if pd.notna(r.get('dv_trend')) and r['dv_trend'] < 0.7:
            f.append('分红水平同比下滑')
        if r.get('debt_latest', 0) is not np.nan and pd.notna(r.get('debt_latest')) and r['debt_latest'] > 60:
            f.append('负债偏高')
        if pd.notna(r.get('ocf_yoy_latest')) and r['ocf_yoy_latest'] < 0:
            f.append('经营现金流同比下滑')
        if pd.notna(r.get('npy_min4')) and r['npy_min4'] < 0:
            f.append('近4期有负增长')
        if pd.notna(r.get('PEG')) and r['PEG'] > 2.5:
            f.append('PEG偏高')
        flags.append('；'.join(f) if f else '—')
    d['风险提示'] = flags
    return d.sort_values('DLG', ascending=False).reset_index(drop=True)


# ═══════════════════════════════════════════════════════
# 报告输出
# ═══════════════════════════════════════════════════════
def _fv(v, nd=1, suffix=''):
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return '—'
    try:
        return f'{float(v):.{nd}f}{suffix}'
    except Exception:
        return '—'


def build_report(res, cfg, date, funnel, meta):
    L = []
    L.append(f'# DLG 高股息·低估值·高成长选股 {date}')
    L.append('')
    L.append('════════════════════════════════════════')
    L.append('')
    L.append('## 一、策略与数据')
    L.append('')
    L.append('选股逻辑：三条主线取交集——能分红（股息率与现金流真实）× 便宜（PE/PB/PS 相对同行业不贵）× '
             '还在成长（最新报告期净利/营收/扣非同比为正且加速）。')
    L.append('')
    L.append(f'评分模型：DLG = {cfg["w_div"]:.2f}×股息 + {cfg["w_val"]:.2f}×估值 + '
             f'{cfg["w_gro"]:.2f}×成长 + {cfg["w_qua"]:.2f}×质量（均归一化到 0~100）')
    L.append(f'股息支柱：当期股息率水平 {cfg["w_div_current"]:.0%}（绝对水平55% + 横截面分位45%）'
             f'＋ 历史持续性 {cfg["w_div_persist"]:.0%}'
             f'（近{cfg["div_history_years"]}年分红未中断比例20% + 股息率地板45% + 分红趋势35%）')
    L.append(f'等级划分：A ≥ {cfg["grade_a"]:.0f}｜B ≥ {cfg["grade_b"]:.0f}｜C ≥ {cfg["grade_c"]:.0f}')
    L.append('')
    L.append('数据来源（全部取自项目缓存，缺失才调接口）：')
    L.append(f'- 估值/股息/市值：daily_basic_cache（trade_date={date}）')
    L.append(f'- 股息率历史：daily_basic_cache 逐日 dv_ttm 快照，回看 {cfg["div_history_years"]} 年')
    L.append(f'- 行情/成交额：daily_cache（trade_date={date}）')
    L.append(f'- 成长/质量：fina_indicator_cache 多期财报，最新报告期 {meta.get("period", "—")}，'
             f'财报披露日 ≤ {meta.get("ann_max", "—")}')
    L.append(f'- 扣非同比/单季加速：{meta.get("full_file", "未使用（文件缺失）")}')
    L.append(f'- 行业/上市日：stock_basic.csv')
    L.append('')
    L.append('硬门槛参数：')
    L.append(f'- 股息率 {cfg["min_dividend_yield"]}%~{cfg["max_dividend_yield"]}%｜'
             f'近{cfg["div_history_years"]}年分红未中断占比 ≥ {cfg["min_div_persist_ratio"]:.0%}｜'
             f'PE(TTM) ≤ {cfg["max_pe_ttm"]}｜PB ≤ {cfg["max_pb"]}')
    L.append(f'- 净利同比 ≥ {cfg["min_netprofit_yoy"]}%｜扣非同比 ≥ {cfg["min_dt_netprofit_yoy"]}%｜'
             f'ROE ≥ {cfg["min_roe"]}%｜毛利率 ≥ {cfg["min_gross_margin"]}%')
    L.append(f'- 资产负债率 ≤ {cfg["max_debt_to_assets"]}%｜总市值 ≥ {cfg["min_total_mv_yi"]}亿｜'
             f'成交额 ≥ {cfg["min_amount_yi"]}亿')
    L.append(f'- 剔除 ST/退市、北交所、金融行业；上市满 {cfg["min_list_days"]} 天；'
             f'必须已披露最新报告期：{"是" if cfg["require_latest_period"] else "否"}')
    L.append('')
    L.append('## 二、筛选漏斗')
    L.append('')
    L.append('| 过滤条件 | 剩余只数 | 本步淘汰 |')
    L.append('| --- | --- | --- |')
    for name, _before, after, cut in funnel:
        L.append(f'| {name} | {after} | {cut} |')
    L.append('')
    if not res.empty:
        L.append(f'通过全部门槛 {len(res)} 只：A 级 {int((res["等级"] == "A").sum())} 只、'
                 f'B 级 {int((res["等级"] == "B").sum())} 只、C 级 {int((res["等级"] == "C").sum())} 只'
                 f'（低于 C 级门槛的不输出）。')
    L.append('')
    L.append('## 三、入选结果')
    L.append('')
    if res.empty:
        L.append('当日无股票满足全部门槛，建议放宽股息率或成长门槛。')
        return '\n'.join(L)

    for g in ('A', 'B', 'C'):
        sub = res[res['等级'] == g]
        if sub.empty:
            continue
        L.append(f'### {g} 级候选（{len(sub)} 只，按 DLG 降序）')
        L.append('')
        L.append('| 代码 | 名称 | 行业 | DLG | 股息 | 持久 | 估值 | 成长 | 质量 | 股息率% | 3年低点% | 分红趋势 | PE | PB | PEG | 净利同比% | 扣非同比% | 营收同比% | ROE% |')
        L.append('| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |')
        for _, r in sub.iterrows():
            L.append('| {code} | {name} | {ind} | **{dlg}** | {sd} | {sp} | {sv} | {sg} | {sq} | {dv} | {dmin} | {dtr} | {pe} | {pb} | {peg} | {npy} | {dt} | {tr} | {roe} |'.format(
                code=r['ts_code'], name=r['name'], ind=r.get('industry', '—'), dlg=_fv(r['DLG']),
                sd=_fv(r['S_DIV']), sp=_fv(r.get('S_DIV持久')), sv=_fv(r['S_VAL']),
                sg=_fv(r['S_GRO']), sq=_fv(r['S_QUA']),
                dv=_fv(r['dv_ttm'], 2), dmin=_fv(r.get('dv_min3'), 2), dtr=_fv(r.get('dv_trend'), 2),
                pe=_fv(r['pe_ttm'], 1), pb=_fv(r['pb'], 2), peg=_fv(r.get('PEG'), 2),
                npy=_fv(r.get('npy_latest')), dt=_fv(r.get('dt_netprofit_yoy')),
                tr=_fv(r.get('or_yoy_latest')), roe=_fv(r.get('roe_latest'))))
        L.append('')

    L.append('## 四、重点关注（前 %d 只要点）' % min(cfg['top_n'], len(res)))
    L.append('')
    for _, r in res.head(cfg['top_n']).iterrows():
        acc = f'，单季加速 {_fv(r.get("Q2加速"))}pct' if pd.notna(r.get('Q2加速')) else ''
        L.append(f'**{r["ts_code"]} {r["name"]}**（{r.get("industry", "—")}）DLG {_fv(r["DLG"])} / {r["等级"]} 级')
        L.append('')
        L.append(f'- 股息：股息率 {_fv(r["dv_ttm"], 2)}%，股息得分 {_fv(r["S_DIV"])}'
                 f'（当期 60% + 历史持续性 40%）；近3年分红未中断占比 '
                 f'{_fv(pd.to_numeric(r.get("dv_pos_ratio"), errors="coerce") * 100)}%｜'
                 f'3年最低正股息 {_fv(r.get("dv_min3"), 2)}%｜3年均值 {_fv(r.get("dv_mean3"), 2)}%｜'
                 f'分红趋势(近1年/前1年) {_fv(r.get("dv_trend"), 2)}，持续性得分 {_fv(r.get("S_DIV持久"))}')
        L.append(f'- 估值：PE(TTM) {_fv(r["pe_ttm"], 1)}｜PB {_fv(r["pb"], 2)}｜PEG {_fv(r.get("PEG"), 2)}，'
                 f'估值得分 {_fv(r["S_VAL"])}（行业中性分位）')
        L.append(f'- 成长：净利同比 {_fv(r.get("npy_latest"))}%｜扣非同比 {_fv(r.get("dt_netprofit_yoy"))}%｜'
                 f'营收同比 {_fv(r.get("or_yoy_latest"))}%{acc}，近4期均值 {_fv(r.get("npy_mean4"))}%，成长得分 {_fv(r["S_GRO"])}')
        L.append(f'- 质量：ROE {_fv(r.get("roe_latest"))}%｜毛利率 {_fv(r.get("margin_latest"))}%｜'
                 f'资产负债率 {_fv(r.get("debt_latest"))}%，质量得分 {_fv(r["S_QUA"])}')
        L.append(f'- 风险提示：{r.get("风险提示", "—")}')
        L.append('')

    ind_stat = res.groupby('industry').agg(只数=('ts_code', 'count'), 平均DLG=('DLG', 'mean'))
    ind_stat = ind_stat.sort_values('只数', ascending=False).head(15)
    L.append('## 五、行业分布（前 15）')
    L.append('')
    L.append('| 行业 | 只数 | 平均 DLG |')
    L.append('| --- | --- | --- |')
    for ind, r in ind_stat.iterrows():
        L.append(f'| {ind} | {int(r["只数"])} | {r["平均DLG"]:.1f} |')
    L.append('')
    L.append('════════════════════════════════════════')
    L.append('')
    L.append('## 六、使用说明与风险提示')
    L.append('')
    L.append('- 本策略是基本面横向筛选，不含择时与买点信号；建议结合趋势/量价模块二次确认后再建仓。')
    L.append(f'- 股息持续性来自 daily_basic_cache 逐日 dv_ttm 快照（近{cfg["div_history_years"]}年）：'
             '分红未中断比例体现"年年有分红"，股息率地板体现"最差时点也不低"，分红趋势体现分红意愿方向。')
    L.append('- 高股息需核实分红来源：一次性特别股息、资产处置收益推高的股息率不可持续（报告已自动标记）。')
    L.append('- 低估值可能是“价值陷阱”：需确认行业景气与业绩下滑是否已止住（本策略已要求扣非同比不为负）。')
    L.append('- 单季加速项来自财报拆分，若为 Proxy 口径（q2_proxy=True）则参考权重应降低。')
    L.append('- 数据均来自本地缓存，使用前请确认缓存已更新至最新交易日。')
    return '\n'.join(L)


# ═══════════════════════════════════════════════════════
# Markdown → PDF（reportlab，中文字体，A4 横向）
# ═══════════════════════════════════════════════════════
_FONT_CACHE = {}


def _cjk_font():
    """注册并缓存中文字体，缺失时回退 Helvetica"""
    if _FONT_CACHE:
        return _FONT_CACHE['name']
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for path, name in ((r'C:\Windows\Fonts\msyh.ttc', 'MSYaHei'),
                       (r'C:\Windows\Fonts\simhei.ttf', 'SimHei'),
                       (r'C:\Windows\Fonts\simsun.ttc', 'SimSun')):
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                _FONT_CACHE['name'] = name
                return name
            except Exception:
                continue
    _FONT_CACHE['name'] = 'Helvetica'
    return 'Helvetica'


def _md_inline(text):
    """行内标记：转义 XML 后恢复 **粗体**"""
    t = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    t = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', t)
    return t


def markdown_to_pdf(md_path, pdf_path):
    """把报告 Markdown 渲染为 PDF（支持标题/表格/列表/粗体）"""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle, HRFlowable)

    font = _cjk_font()
    theme = colors.HexColor('#1F3B57')
    st = {
        'h1': ParagraphStyle('h1', fontName=font, fontSize=17, leading=23, textColor=theme, spaceAfter=6),
        'h2': ParagraphStyle('h2', fontName=font, fontSize=13, leading=19, textColor=theme,
                             spaceBefore=10, spaceAfter=4),
        'h3': ParagraphStyle('h3', fontName=font, fontSize=11, leading=16, textColor=colors.HexColor('#2E6DA4'),
                             spaceBefore=8, spaceAfter=3),
        'body': ParagraphStyle('body', fontName=font, fontSize=9, leading=14, spaceAfter=2),
        'bullet': ParagraphStyle('bullet', fontName=font, fontSize=8.5, leading=13, leftIndent=10,
                                 bulletIndent=2, spaceAfter=1),
        'cell': ParagraphStyle('cell', fontName=font, fontSize=7, leading=9),
        'cellh': ParagraphStyle('cellh', fontName=font, fontSize=7, leading=9, textColor=colors.white),
    }

    with open(md_path, 'r', encoding='utf-8') as f:
        lines = f.read().split('\n')

    avail = landscape(A4)[0] - 1.6 * cm
    story, i = [], 0
    while i < len(lines):
        ln = lines[i].rstrip()
        s = ln.strip()
        if not s:
            i += 1
            continue
        if s.startswith('|'):                      # ── 表格块 ──
            block = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                block.append(lines[i].strip())
                i += 1
            rows = [r for r in block if not re.fullmatch(r'\|[\s\-:|]+\|', r)]
            grid = [[c.strip() for c in r.strip('|').split('|')] for r in rows]
            if not grid:
                continue
            ncol = max(len(r) for r in grid)
            grid = [r + [''] * (ncol - len(r)) for r in grid]
            weights = [max(3, max(len(_md_inline(r[j]).replace('<b>', '').replace('</b>', ''))
                                 for r in grid)) for j in range(ncol)]
            total = sum(weights)
            widths = [max(0.85 * cm, avail * w / total) for w in weights]
            k = avail / sum(widths)
            widths = [w * k for w in widths]
            data = [[Paragraph(_md_inline(c), st['cellh'] if ri == 0 else st['cell'])
                     for c in row] for ri, row in enumerate(grid)]
            tbl = Table(data, colWidths=widths, repeatRows=1)
            ts = [('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#B8C4D0')),
                  ('BACKGROUND', (0, 0), (-1, 0), theme),
                  ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                  ('TOPPADDING', (0, 0), (-1, -1), 2),
                  ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                  ('LEFTPADDING', (0, 0), (-1, -1), 2),
                  ('RIGHTPADDING', (0, 0), (-1, -1), 2)]
            for ri in range(1, len(grid)):
                if ri % 2 == 0:
                    ts.append(('BACKGROUND', (0, ri), (-1, ri), colors.HexColor('#F2F6FA')))
            tbl.setStyle(TableStyle(ts))
            story += [Spacer(1, 3), tbl, Spacer(1, 5)]
            continue
        if re.fullmatch(r'[═─=]{3,}', s):           # ── 分隔线 ──
            story.append(HRFlowable(width='100%', thickness=0.8, color=colors.HexColor('#8FA6BC')))
            i += 1
            continue
        if s.startswith('###'):
            story.append(Paragraph(_md_inline(s[3:].strip()), st['h3']))
        elif s.startswith('##'):
            story.append(Paragraph(_md_inline(s[2:].strip()), st['h2']))
        elif s.startswith('#'):
            story.append(Paragraph(_md_inline(s[1:].strip()), st['h1']))
        elif s.startswith('- '):
            story.append(Paragraph(_md_inline(s[2:].strip()), st['bullet'], bulletText='•'))
        else:
            story.append(Paragraph(_md_inline(s), st['body']))
        i += 1

    doc = SimpleDocTemplate(pdf_path, pagesize=landscape(A4),
                            leftMargin=0.8 * cm, rightMargin=0.8 * cm,
                            topMargin=0.8 * cm, bottomMargin=0.8 * cm,
                            title=os.path.basename(pdf_path), author='DLG Picker')
    doc.build(story)
    return pdf_path


# ═══════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════
def run(date='', use_api=True, cfg=None, verbose=True):
    cfg = dict(DLG_CONFIG, **(cfg or {}))
    date = get_effective_date(date) if not date else str(date)
    t0 = time.time()

    snap, _ = load_market_snapshot(date, use_api=use_api)
    hist, latest_period = load_fin_history(date)
    full = load_fin_full(date, hist)
    sb = load_industry()

    df = snap.merge(sb[['ts_code', 'name', 'industry', 'list_date']], on='ts_code', how='left')
    df = df.merge(hist, on='ts_code', how='left')
    if not full.empty:
        df = df.merge(full, on='ts_code', how='left')
    # 股息率历史持续性（仅对通过当期股息率初筛的代码聚合，全表扫描代价太大）
    pre = df.loc[pd.to_numeric(df['dv_ttm'], errors='coerce') >= cfg['min_dividend_yield'], 'ts_code']
    divh = load_div_history(date, pre.tolist(), years=cfg['div_history_years'])
    df = df.merge(divh, on='ts_code', how='left')
    df['is_latest_period'] = df['period'].astype(str) == str(latest_period)

    df_pass, funnel = apply_gates(df, cfg, date)
    scored = score_pillars(df_pass, cfg)
    # 低于 C 级门槛的不输出（保留在漏斗统计中）
    res = scored[scored['等级'] != 'D'].reset_index(drop=True)

    full_file = ''
    if not full.empty and latest_period:
        p = str(latest_period)
        tag = f'{p[:4]}H1_full' if p[4:] == '0630' else (f'{p[:4]}Q1_full' if p[4:] == '0331' else f'{p[:4]}_full')
        full_file = f'fin_ind_{tag}.parquet'
    meta = {'period': latest_period, 'ann_max': date, 'full_file': full_file}

    os.makedirs(OUT_DIR, exist_ok=True)
    md_path = os.path.join(OUT_DIR, f'dlg_picker_{date}.md')
    csv_path = os.path.join(OUT_DIR, f'dlg_picker_{date}.csv')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(build_report(res, cfg, date, funnel, meta))

    out_cols = ['ts_code', 'name', 'industry', '等级', 'DLG', 'S_DIV', 'S_DIV持久', 'S_VAL', 'S_GRO', 'S_QUA',
                'dv_ttm', 'dv_mean3', 'dv_min3', 'dv_trend', 'dv_pos_ratio',
                'pe_ttm', 'pb', 'ps', 'PEG', 'npy_latest', 'dt_netprofit_yoy',
                'or_yoy_latest', 'Q2加速', 'roe_latest', 'margin_latest', 'debt_latest',
                'npy_mean4', 'npy_min4', 'total_mv_yi', 'amount_yi', 'close', 'pct_chg', '风险提示']
    out_cols = [c for c in out_cols if c in res.columns]
    res[out_cols].to_csv(csv_path, index=False, encoding='utf-8-sig')

    pdf_path = ''
    if cfg.get('make_pdf', True):
        pdf_path = os.path.join(OUT_DIR, f'dlg_picker_{date}.pdf')
        try:
            markdown_to_pdf(md_path, pdf_path)
        except Exception as e:
            pdf_path = ''
            print(f'[提示] PDF 生成失败({type(e).__name__}: {e})，已保留 Markdown/CSV')

    if verbose:
        print(f'[DLG] 交易日 {date}｜候选池 {len(df)} 只 → 通过门槛 {len(df_pass)} 只 → '
              f'输出 {len(res)} 只（A {int((res["等级"] == "A").sum())} / B {int((res["等级"] == "B").sum())} / '
              f'C {int((res["等级"] == "C").sum())}）｜耗时 {time.time() - t0:.1f}s')
        if not res.empty:
            show = res.head(cfg['top_n'])
            print(show[['ts_code', 'name', 'industry', '等级', 'DLG', 'dv_ttm', 'pe_ttm', 'pb',
                        'npy_latest', 'or_yoy_latest', 'roe_latest']].to_string(index=False))
        print(f'[DLG] 报告: {md_path}')
        print(f'[DLG] 明细: {csv_path}')
        if pdf_path:
            print(f'[DLG] PDF : {pdf_path}')
    return res, {'md': md_path, 'csv': csv_path, 'pdf': pdf_path, 'funnel': funnel, 'date': date}


def main():
    ap = argparse.ArgumentParser(description='DLG 高股息+低估值+高成长 选股')
    ap.add_argument('--date', default='', help='交易日 YYYYMMDD，默认取有效交易日')
    ap.add_argument('--top', type=int, default=DLG_CONFIG['top_n'], help='终端与报告重点展示只数')
    ap.add_argument('--min-yield', type=float, default=DLG_CONFIG['min_dividend_yield'], help='股息率下限%%')
    ap.add_argument('--min-div-persist', type=float, default=DLG_CONFIG['min_div_persist_ratio'],
                    help='近3年分红未中断占比下限（0~1）')
    ap.add_argument('--keep-dividend-gap', action='store_true',
                    help='放宽分红中断门槛（允许历史有断档）')
    ap.add_argument('--max-pe', type=float, default=DLG_CONFIG['max_pe_ttm'], help='PE(TTM) 上限')
    ap.add_argument('--max-pb', type=float, default=DLG_CONFIG['max_pb'], help='PB 上限')
    ap.add_argument('--min-growth', type=float, default=DLG_CONFIG['min_netprofit_yoy'], help='净利同比下限%%')
    ap.add_argument('--min-mv', type=float, default=DLG_CONFIG['min_total_mv_yi'], help='总市值下限(亿元)')
    ap.add_argument('--no-api', action='store_true', help='纯本地缓存，不调用接口补数')
    ap.add_argument('--no-pdf', action='store_true', help='不生成 PDF，仅输出 Markdown/CSV')
    ap.add_argument('--allow-stale', action='store_true', help='允许使用非最新报告期(放宽)')
    ap.add_argument('--exclude-financial', action='store_true', default=True, help='剔除金融行业(默认开)')
    ap.add_argument('--keep-financial', dest='exclude_financial', action='store_false', help='保留金融行业')
    args = ap.parse_args()

    cfg = {
        'top_n': args.top,
        'min_dividend_yield': args.min_yield,
        'min_div_persist_ratio': 0.0 if args.keep_dividend_gap else args.min_div_persist,
        'max_pe_ttm': args.max_pe,
        'max_pb': args.max_pb,
        'min_netprofit_yoy': args.min_growth,
        'min_total_mv_yi': args.min_mv,
        'require_latest_period': not args.allow_stale,
        'exclude_financial': args.exclude_financial,
        'make_pdf': not args.no_pdf,
    }
    run(date=args.date, use_api=not args.no_api, cfg=cfg)


if __name__ == '__main__':
    main()
