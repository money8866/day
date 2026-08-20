# -*- coding: utf-8 -*-
"""
ER20 (Earnings Repricing 20 Days) v1.0 - 中报公告后20交易日 Alpha 选股模块
============================================================
半年报正式披露后 → 等待市场对业绩重新定价 → 公告后2~10交易日右侧确认 → 买入 → 计划持有20交易日

策略A (ER20_A_CONFIRMATION): 业绩确认后的缩量回踩再突破
  基本面改善 × 公告前未过度透支 × 公告后温和认可 × 缩量回踩不破趋势 × 放量再突破
策略B (ER20_B_REVERSAL): 业绩反转 + 公告后不跌反涨 + MA60突破
  基本面仍在改善(即便净利同比为负) × 市场不杀跌(公告后不跌反涨) × MA60右侧突破

数据复用(不重新下载):
  - 中报池: cache_daily/treasure_fin_ind_*.parquet (fina_indicator全历史, ann_date=披露日)
  - Q2拆分: cache_daily/income_*.parquet 四期相减(H1-Q1); 缺失用 Proxy=中报累计同比 vs Q1累计同比加速差
  - 日线:   bts/data.py load_daily (SQLite前复权→TDX补历史, 防未来数据)
  - 技术:   bts/indicators.py add_ma (MA5/10/20/60); 自算 ATR14; etf_midterm_rating.calc_ret_from_pct
  - 主题:   report_daily/theme_stock_map_latest_v2.json + theme_config.json
评分(统一0~100):
  A = 0.30*FundamentalQuality + 0.20*ExpectationGap + 0.20*AnnouncementReaction + 0.25*TechnicalConfirmation + 0.05*MarketTheme - CrowdingPenalty
  B = 0.35*FundamentalReversal + 0.25*PositivePriceReaction + 0.30*MA60BreakoutQuality + 0.10*MarketTheme
无未来函数: 评分只用 公告内容 + 截至当前交易日行情; 未来收益仅用于回测。
用法: python er20_strategy.py --date 20260819 [--backtest]
"""
import os
import sys
import glob
import json
import time
import argparse

import numpy as np
import pandas as pd

SOLO_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SOLO_DIR)
sys.path.insert(0, os.path.join(SOLO_DIR, 'multi_factor_picker'))
sys.path.insert(0, os.path.join(SOLO_DIR, 'etf_alpha_ranking'))

CACHE_DIR = r'D:\mystock\cache_daily'
REPORT_DIR = r'D:\mystock\solo\report_daily'
TDX_PATH = r'C:\new_tdx\vipdoc'

from bts.data import load_daily, get_trade_dates, load_stock_basic, parse_tdx_day_file
from bts.indicators import add_ma

# ---------------- ER20_CONFIG 集中参数 ----------------
ER20_CONFIG = {
    'announcement_window': 10,          # 公告后观察窗口(交易日), 超过则不再触发
    'min_gap_days': 2,                  # 公告后最早可确认
    'strategy_a': {
        'min_score': 70,
        'pullback_days_min': 2,
        'pullback_days_max': 7,
        'pullback_volume_ratio': 0.75,  # 回踩均量 < 公告/突破日量×0.75
        'breakout_volume_ratio': 1.2,   # 突破日量 > 前3日均量×1.2
        'max_pullback': 0.10,           # 回踩幅度 < 10%
        'chase_limit': 0.09,            # 单日>9% 不追
    },
    'strategy_b': {
        'min_score': 70,
        'min_profit_acceleration': 15,  # Q2 vs Q1 加速≥15pct
        'announcement_max_drop': -0.03, # 公告首日跌幅容忍
        'ma60_breakout_volume': 1.2,
        'chase_limit': 0.08,            # 单日>8% 等回踩
    },
    'risk': {
        'atr_stop_multiple': 1.5,
        'min_stop': 0.03,
        'max_stop': 0.08,
        'max_hold_days': 20,
    },
    'premium_penalty': {                # 公告前预期透支惩罚
        'r20_gt': 0.50,  'r20_pen': 20,
        'r20_mid': 0.35, 'r20_mid_pen': 12,
        'r20_lo': 0.20,  'r20_lo_pen': 6,
        'r60_gt': 0.60,  'r60_pen': 15,
    },
    'reaction': {                       # 公告后首日理想反应
        'ret_min': -0.02, 'ret_max': 0.08,
        'vr_min': 1.1, 'vr_max': 2.8,
        'close_pos_min': 0.60,
    },
}

# ---------------- 基础工具 ----------------

def _p(t=None):
    """限速: 默认 0.13s, 可覆盖"""
    time.sleep(t if t is not None else 0.13)


def calc_atr14(df):
    """ATR14 (Wilder), 返回最新值(元)"""
    h, l, c = df['high'].astype(float), df['low'].astype(float), df['close'].astype(float)
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / 14, adjust=False).mean()
    return float(atr.iloc[-1]) if len(atr) else np.nan


def calc_ret_pct(df, window):
    """N日复利涨幅%"""
    pct = df['pct_chg'].astype(float).tail(window)
    if len(pct) < window:
        return np.nan
    return float((np.prod(1 + pct.values / 100.0) - 1) * 100.0)


def theme_map_stock2theme():
    """股票->主题列表"""
    fp = os.path.join(REPORT_DIR, 'theme_stock_map_latest_v2.json')
    if not os.path.exists(fp):
        return {}
    with open(fp, encoding='utf-8') as f:
        data = json.load(f)
    out = {}
    for theme, stocks in data.get('themes', {}).items():
        for s in stocks:
            code = s.get('code') or s.get('ts_code')
            if code:
                out.setdefault(code, []).append(theme)
    return out


def load_theme_config():
    fp = os.path.join(SOLO_DIR, 'theme_config.json')
    if os.path.exists(fp):
        with open(fp, encoding='utf-8') as f:
            return json.load(f)
    return {}


# ---------------- 数据加载 ----------------

def load_zhongbao_pool(period='20260630', scan_date=''):
    """
    全市场中报池(多源合并, 全部来自已有缓存, 不新增接口):
      S1 treasure_fin_ind_*.parquet   fina_indicator全历史(140只有本中报, 字段全: ann_date/净利/扣非/营收/ROE/现金流)
      S2 report_daily/zhongbao_hunt_*.csv  中报猎手(净利/扣非/营收同比+市值)
      S3 parquet/express_vip_*.parquet     中报快报(ann_date+营收+净利)
    合并去重(优先级 S1>S2>S3), Q1 来自 S1 的 20260331 记录, Q2 拆分用 income 缓存, 缺失用 Proxy。
    """
    # ---- S1: treasure fina_indicator ----
    year = period[:4]
    q1_period = f'{year}0331'
    files = sorted(glob.glob(os.path.join(CACHE_DIR, 'treasure_fin_ind_*.parquet')))
    rows = []
    for fp in files:
        try:
            df = pd.read_parquet(fp, columns=['ts_code', 'ann_date', 'end_date',
                                              'netprofit_yoy', 'dt_netprofit_yoy', 'tr_yoy',
                                              'op_yoy', 'roe', 'grossprofit_margin',
                                              'netprofit_margin', 'ocf_yoy', 'profit_dedt'])
        except Exception:
            continue
        h1 = df[df['end_date'] == period]
        if h1.empty:
            continue
        rec = h1.sort_values('ann_date').iloc[-1].to_dict()
        if scan_date and str(rec.get('ann_date', '')) > scan_date:
            continue
        q1 = df[df['end_date'] == q1_period]
        rec['q1_profit_yoy'] = q1.sort_values('ann_date')['netprofit_yoy'].iloc[-1] if not q1.empty else np.nan
        rec['src'] = 'fina_indicator'
        rows.append(rec)
    pool = pd.DataFrame(rows)
    # ---- S2: zhongbao_hunt ----
    hunts = sorted(glob.glob(os.path.join(REPORT_DIR, 'zhongbao_hunt_*.csv')))
    hunt = None
    if hunts:
        hunt = pd.read_csv(hunts[-1], dtype={'ts_code': str})
        hunt = hunt[['ts_code', 'netprofit_yoy', 'dt_netprofit_yoy', 'tr_yoy']].copy()
        hunt['src'] = 'zhongbao_hunt'
    # ---- S3: express 快报 ----
    exps = sorted(glob.glob(os.path.join(CACHE_DIR, 'parquet', 'express_vip_*.parquet')))
    exp = None
    if exps:
        exp = pd.read_parquet(exps[-1])
        if 'netprofit_yoy' not in exp.columns and 'yoy_net_profit' in exp.columns:
            # 快报无同比%, 用 ann_date/revenue/n_income 补披露日与规模
            pass
        exp = exp[['ts_code', 'ann_date', 'revenue', 'n_income']].copy()
        exp['src'] = 'express'
    # ---- 合并 ----
    frames = [pool]
    extras = [(hunt, ('netprofit_yoy', 'dt_netprofit_yoy', 'tr_yoy')),
              (exp, ('revenue', 'n_income'))]
    # ---- S4: 全量中报回填 (fin_ind_2026H1_full.parquet, 全字段 fina_indicator) ----
    s4_path = os.path.join(CACHE_DIR, 'fin_ind_2026H1_full.parquet')
    if os.path.exists(s4_path):
        try:
            s4 = pd.read_parquet(s4_path)
            s4 = s4[s4['end_date'] == period].copy()
            if scan_date:
                s4 = s4[s4['ann_date'].astype(str) <= scan_date]
            if not s4.empty:
                keep = ['ts_code', 'ann_date', 'end_date', 'netprofit_yoy',
                        'dt_netprofit_yoy', 'tr_yoy', 'op_yoy', 'roe',
                        'grossprofit_margin', 'netprofit_margin', 'ocf_yoy',
                        'profit_dedt', 'name']
                for qc in ('q_sales_yoy', 'q_op_qoq', 'q_roe'):
                    if qc in s4.columns:
                        keep.append(qc)
                s4 = s4[keep].drop_duplicates('ts_code', keep='last').copy()
                s4['src'] = 'fin_ind_full'
                extras.append((s4, ('netprofit_yoy', 'dt_netprofit_yoy', 'tr_yoy')))
        except Exception as e:
            print('S4 fin_ind_full 读取失败:', e)
    for extra, key_cols in extras:
        if extra is None or extra.empty:
            continue
        # 只补 pool 未覆盖的股票
        known = set(pool['ts_code']) if not pool.empty else set()
        add = extra[~extra['ts_code'].isin(known)]
        for k in key_cols:
            add = add.rename(columns={k: k})
        if add.empty:
            continue
        # 对齐列
        for c in pool.columns:
            if c not in add.columns:
                add[c] = np.nan
        add = add[[c for c in pool.columns if c in add.columns]]
        pool = pd.concat([pool, add], ignore_index=True) if not pool.empty else add
    if pool.empty:
        return pool
    # Q2 拆分 + Q1(非 S1 来源)
    pool['q2_proxy'] = False
    # 全量 Q1 缓存(fin_ind_2026Q1_full.parquet): ts_code -> netprofit_yoy(20260331 累计同比)
    q1_map = {}
    q1_full = os.path.join(CACHE_DIR, 'fin_ind_2026Q1_full.parquet')
    if os.path.exists(q1_full):
        try:
            q1f = pd.read_parquet(q1_full, columns=['ts_code', 'end_date', 'netprofit_yoy'])
            q1f = q1f[q1f['end_date'] == q1_period].drop_duplicates('ts_code', keep='last')
            q1_map = dict(zip(q1f['ts_code'], q1f['netprofit_yoy']))
        except Exception:
            pass
    for idx, r in pool.iterrows():
        if pd.isna(r.get('q2_profit_yoy')):
            q2, proxy = _calc_q2_single(r['ts_code'], period, r)
            pool.at[idx, 'q2_profit_yoy'] = q2
            pool.at[idx, 'q2_proxy'] = proxy
        if pd.isna(r.get('q1_profit_yoy')):
            v = q1_map.get(r['ts_code'], np.nan)
            if pd.isna(v):
                v = _load_q1_yoy(r['ts_code'], q1_period)
            pool.at[idx, 'q1_profit_yoy'] = v
    # 补名称
    basic = load_stock_basic()
    nm = dict(zip(basic['ts_code'], basic['name'])) if basic is not None and len(basic) else {}
    pool['name'] = pool['ts_code'].map(nm)
    return pool


def _load_q1_yoy(ts_code, q1_period):
    """从 treasure 缓存读 Q1 净利同比(补非 S1 来源)"""
    sym, mkt = ts_code.split('.')
    fp = os.path.join(CACHE_DIR, f'treasure_fin_ind_{sym}_{mkt}.parquet')
    if not os.path.exists(fp):
        return np.nan
    try:
        df = pd.read_parquet(fp, columns=['end_date', 'netprofit_yoy'])
        q1 = df[df['end_date'] == q1_period]
        return float(q1.sort_values('ann_date')['netprofit_yoy'].iloc[-1]) if not q1.empty else np.nan
    except Exception:
        return np.nan


def _calc_q2_single(ts_code, period, rec):
    """
    Q2单季同比 = (H1_26 - Q1_26) / (H1_25 - Q1_25) - 1
    income 缓存有四期则精确; 否则 Proxy = 中报累计 netprofit_yoy (近似单季同比, 标注 proxy)
    """
    fp = os.path.join(CACHE_DIR, f'income_{ts_code.split(".")[0]}_{ts_code.split(".")[1]}.parquet')
    alt = os.path.join(CACHE_DIR, f'income_{ts_code}.parquet')
    for cand in (fp, alt):
        if not os.path.exists(cand):
            continue
        try:
            inc = pd.read_parquet(cand, columns=['ts_code', 'end_date', 'n_income_attr_p', 'revenue'])
        except Exception:
            continue
        y = period[:4]
        map4 = {
            f'{y}0630': 'h1_c', f'{y}0331': 'q1_c',
            f'{int(y) - 1}0630': 'h1_p', f'{int(y) - 1}0331': 'q1_p',
        }
        vals = {}
        for end, key in map4.items():
            sub = inc[inc['end_date'] == end]
            if not sub.empty:
                vals[key] = float(sub['n_income_attr_p'].iloc[0])
        if len(vals) == 4:
            q2_c = vals['h1_c'] - vals['q1_c']
            q2_p = vals['h1_p'] - vals['q1_p']
            if q2_p:
                return round((q2_c / q2_p - 1) * 100.0, 1), False
        break
    v = rec.get('netprofit_yoy', np.nan)
    return (round(float(v), 1) if pd.notna(v) else np.nan), True


# ---------------- 基本面评分 ----------------

def fundamental_quality_score(r):
    """
    Strategy A 基本面质量 100分
    A1 Q2单季加速 30 | A2 收入利润共振 15 | A3 扣非质量 10 | A4 现金流 ±
    """
    s = 0.0
    # A1: Q2同比 vs Q1同比 (精确拆分优先, proxy兜底)
    q2 = r.get('q2_profit_yoy', np.nan)
    q1 = r.get('q1_profit_yoy', np.nan)
    accel = (q2 - q1) if pd.notna(q2) and pd.notna(q1) else np.nan
    if pd.notna(accel):
        if accel >= 30: s += 30
        elif accel >= 15: s += 22
        elif accel > 0: s += 15
        elif accel >= -5: s += 8
        else: s += 0
    elif pd.notna(q2):
        s += 12 if q2 > 0 else 5
    # A2: 收入利润共振
    tr, ni = r.get('tr_yoy', np.nan), r.get('netprofit_yoy', np.nan)
    if pd.notna(tr) and pd.notna(ni):
        if tr > 0 and ni > 0:
            s += 15 + (10 if ni > tr else 0)
        elif ni > 0:
            s += 5
        elif tr > 0:
            s += 3
        else:
            s += 0
    elif pd.notna(ni) and ni > 0:
        s += 8
    # A3: 扣非质量 (扣非同比 vs 归母同比)
    dt, ni2 = r.get('dt_netprofit_yoy', np.nan), r.get('netprofit_yoy', np.nan)
    if pd.notna(dt) and pd.notna(ni2) and ni2 > 0:
        if dt >= ni2 * 0.8: s += 10
        elif dt > 0: s += 7
        elif dt > -0.5: s += 3
        else: s += 0
    elif pd.notna(dt) and dt > 0:
        s += 8
    # A4: 现金流风险
    ocf = r.get('ocf_yoy', np.nan)
    if pd.notna(ocf):
        if ocf > 0: s += 5
        elif ocf < -50: s -= 10
        elif ocf < -20: s -= 5
    return round(min(100, max(0, s)), 1)


def fundamental_reversal_score(r):
    """
    Strategy B 基本面反转 100分
    B1 利润边际改善 35 | B2 收入改善 15 | B3 盈利能力改善 10
    """
    s = 0.0
    q2 = r.get('q2_profit_yoy', np.nan)
    q1 = r.get('q1_profit_yoy', np.nan)
    accel = (q2 - q1) if pd.notna(q2) and pd.notna(q1) else np.nan
    if pd.notna(accel):
        if accel >= 50: s += 35
        elif accel >= 30: s += 28
        elif accel >= 15: s += 18
        elif accel > 0: s += 10
        else: s += 0
    elif pd.notna(q2):
        s += 20 if q2 > 0 else 5
    tr = r.get('tr_yoy', np.nan)
    if pd.notna(tr):
        if tr > 10: s += 15
        elif tr > 0: s += 8
    # 盈利能力: ROE 参考
    roe = r.get('roe', np.nan)
    if pd.notna(roe) and roe > 0:
        s += min(10, roe)
    return round(min(100, max(0, s)), 1)


# ---------------- 公告前预期透支 ----------------

def pre_run_and_penalty(daily, ann_idx, cur_idx):
    """
    公告日前 R20/R60 复利涨幅 + 透支惩罚(0~20)
    只用 ann_idx 之前的数据
    """
    pre = daily.iloc[:ann_idx]
    if len(pre) < 65:
        return 0.0, 0.0
    r20 = calc_ret_pct(pre, 20)
    r60 = calc_ret_pct(pre, 60)
    c = ER20_CONFIG['premium_penalty']
    pen = 0.0
    if pd.notna(r20):
        if r20 > c['r20_gt'] * 100: pen += c['r20_pen']
        elif r20 > c['r20_mid'] * 100: pen += c['r20_mid_pen']
        elif r20 > c['r20_lo'] * 100: pen += c['r20_lo_pen']
    if pd.notna(r60) and r60 > c['r60_gt'] * 100:
        pen += c['r60_pen']
    # 公告前横盘加分(温和) 转 expectation_gap 分数
    gap_score = 50.0
    if pd.notna(r20):
        if -10 <= r20 <= 20: gap_score = 75
        elif r20 < -10: gap_score = 60
        elif 20 < r20 <= 35: gap_score = 55
        elif r20 <= 50: gap_score = 40
        else: gap_score = 25
    return gap_score, min(pen, 35.0)


# ---------------- 公告后市场反应 ----------------

def announcement_reaction(daily, ann_idx):
    """
    公告后首日反应: 涨幅/量比/收盘位置/利好兑现识别
    返回 (reaction_score 0~100, sell_news bool)
    """
    if ann_idx >= len(daily) or ann_idx < 1:
        return 50.0, False
    d0 = daily.iloc[ann_idx - 1]   # 公告前一日(锚)
    d1 = daily.iloc[ann_idx]       # 公告后首个交易日(严格在公告日之后)
    if ann_idx < 20:
        return 50.0, False
    ma20_v = float(daily['vol'].iloc[ann_idx - 20:ann_idx].mean())
    if not ma20_v:
        return 50.0, False
    ret = float(d1['close']) / float(d0['close']) - 1.0
    vr = float(d1['vol']) / ma20_v
    rng = float(d1['high']) - float(d1['low'])
    close_pos = (float(d1['close']) - float(d1['low'])) / rng if rng > 0 else 0.5
    body = abs(float(d1['close']) - float(d1['open']))
    upper_shadow = float(d1['high']) - max(float(d1['close']), float(d1['open']))
    rc = ER20_CONFIG['reaction']
    # 利好兑现识别
    sell_news = False
    if (float(d1['open']) / float(d0['close']) - 1.0 > 0.07 and vr > 3
            and float(d1['close']) < float(d1['open']) and upper_shadow > body):
        sell_news = True
    if vr > 4 and close_pos < 0.35:
        sell_news = True
    # 理想反应打分
    score = 50.0
    if rc['ret_min'] <= ret <= rc['ret_max']:
        score = 75.0
    if rc['vr_min'] <= vr <= rc['vr_max']:
        score += 10
    if close_pos >= rc['close_pos_min']:
        score += 10
    elif close_pos < 0.35:
        score -= 15
    if sell_news:
        score = max(0.0, score - 25)
    return round(min(100, max(0, score)), 1), sell_news


def positive_price_reaction(daily, ann_idx, cur_idx):
    """Strategy B: 公告后不跌反涨 AR1/AR3"""
    if ann_idx + 3 >= len(daily):
        return 50.0
    ar1 = float(daily.iloc[ann_idx]['close']) / float(daily.iloc[ann_idx - 1]['close']) - 1.0
    ar3 = float(daily.iloc[min(ann_idx + 2, cur_idx)]['close']) / float(daily.iloc[ann_idx - 1]['close']) - 1.0
    s = 50.0
    if ar1 > -0.03: s += 25
    d1 = daily.iloc[ann_idx]
    if float(d1['close']) >= float(d1['open']): s += 10
    if ar3 > 0: s += 15
    elif ar3 > -0.05: s += 5
    return round(min(100, max(0, s)), 1)


# ---------------- Strategy A 回踩再突破 ----------------

def detect_pattern_a(daily, ann_idx, cur_idx, sell_news):
    """
    公告后观察 T+2~T+10: 缩量回踩(2~7日, 不破关键位) → 放量再突破
    返回 (status, buy_signal, technical_score, extra)
    status: WATCH/WAIT_PULLBACK/WAIT_BREAKOUT/BUY/NO_PATTERN
    """
    cfg = ER20_CONFIG['strategy_a']
    technical_score = 40.0
    if sell_news:
        return 'REJECT', False, 15.0, '利好兑现'
    if cur_idx - ann_idx < cfg['pullback_days_min']:
        return 'WATCH', False, 45.0, '等待确认'
    # 取公告后窗口数据
    win = daily.iloc[ann_idx:cur_idx + 1]
    if len(win) < 2:
        return 'WATCH', False, 45.0, '数据不足'
    base_ret = float(win['close'].iloc[-1]) / float(daily.iloc[ann_idx - 1]['close']) - 1.0
    # 回踩识别: 最近2~7日缩量整理且未破趋势
    n = len(win)
    pull_days = 0
    pull_vol = None
    for i in range(max(1, n - 7), n):
        seg = win.iloc[max(0, i - pull_days):i]
        if i - max(0, i - pull_days) < 2:
            continue
        avg_vol = float(seg['vol'].mean())
        peak_vol = float(win['vol'].iloc[0]) if len(win) else np.nan
        ret_seg = float(win['close'].iloc[i - 1]) / float(win['close'].iloc[max(0, i - pull_days)]) - 1.0
        if avg_vol <= (peak_vol * cfg['pullback_volume_ratio'] if pd.notna(peak_vol) else np.nan) and ret_seg > -cfg['max_pullback']:
            pull_days = i - max(0, i - pull_days)
            pull_vol = avg_vol
    if 2 <= pull_days <= 7 and pull_vol is not None:
        technical_score = 65.0
        status = 'WAIT_BREAKOUT'
        note = f'缩量回踩{pull_days}日'
    else:
        status = 'WAIT_PULLBACK'
        note = '未现缩量回踩'
        technical_score = 50.0
    # 突破判定(今日)
    cur = win.iloc[-1]
    prev3 = win.iloc[-4:-1]['vol'].mean() if len(win) >= 4 else float(win['vol'].iloc[0])
    today_vol = float(cur['vol'])
    last = daily.iloc[cur_idx]
    ma5 = float(last['ma5']) if pd.notna(last.get('ma5')) else np.nan
    ma10 = float(last['ma10']) if pd.notna(last.get('ma10')) else np.nan
    hi3 = float(win['high'].iloc[-4:-1].max()) if len(win) >= 4 else float(win['high'].max())
    ret_today = float(last['close']) / float(win['close'].iloc[-2]) - 1.0
    if ret_today > cfg['chase_limit']:
        return status, False, min(technical_score + 5, 80), note + '/当日大涨不追'
    cond = (pd.notna(ma5) and pd.notna(ma10) and float(last['close']) > ma5 and ma5 >= ma10
            and today_vol > prev3 * cfg['breakout_volume_ratio']
            and float(last['close']) > hi3)
    if cond:
        technical_score = 88.0
        return 'BUY', True, technical_score, note + '/放量再突破'
    return status, False, technical_score, note


# ---------------- Strategy B MA60 突破 ----------------

def detect_pattern_b(daily, ann_idx, cur_idx):
    """MA60 右侧突破: close>MA60 且前20日贴近(≤8%) + 放量≥1.2"""
    cfg = ER20_CONFIG['strategy_b']
    last = daily.iloc[cur_idx]
    ma60 = float(last['ma60']) if pd.notna(last.get('ma60')) else np.nan
    if pd.isna(ma60):
        return 'WATCH', False, 40.0, 'MA60缺失'
    if cur_idx < 20:
        return 'WATCH', False, 40.0, '数据不足'
    prev20_hi = float(daily.iloc[cur_idx - 20:cur_idx]['close'].max())
    close = float(last['close'])
    # 前20日曾在MA60附近或下方
    near = prev20_hi <= ma60 * 1.08 or float(daily.iloc[cur_idx - 20]['close']) <= ma60 * 1.08
    prev3 = float(daily['vol'].iloc[cur_idx - 4:cur_idx].mean())
    vol_ok = float(last['vol']) >= prev3 * cfg['ma60_breakout_volume']
    ma5 = float(last['ma5']) if pd.notna(last.get('ma5')) else np.nan
    ma10 = float(last['ma10']) if pd.notna(last.get('ma10')) else np.nan
    ret_today = float(last['close']) / float(daily.iloc[cur_idx - 1]['close']) - 1.0
    score = 40.0
    if close > ma60 and near:
        score += 30
    if ma5 >= ma10:
        score += 15
    if vol_ok:
        score += 10
    if ret_today > cfg['chase_limit']:
        return 'WAIT_PULLBACK', False, min(score + 5, 80), '单日大涨等回踩'
    if close > ma60 and near and ma5 >= ma10 and vol_ok:
        return 'BUY', True, min(score + 5, 92), 'MA60右侧突破'
    return 'WATCH', False, score, 'MA60未突破'


# ---------------- 主题因子 ----------------

def market_theme_score(code, stock2theme):
    th = stock2theme.get(code, [])
    if not th:
        return 50.0
    # 主题白名单(EGPT 同款) 加分
    whitelist = {'智能驾驶', '信创', '新能源车', '消费电子', '半导体', '创新药',
                 '机器人', '游戏', '建筑装饰', '传媒', '能源金属', '商业航天'}
    hit = set(th) & whitelist
    return 70.0 if hit else 60.0


# ---------------- 评分/评级 ----------------

def build_signal(r, daily, ann_idx, cur_idx, stock2theme, mode):
    """
    组装单股: 评分(含中间因子), 模式, 状态机, 买点, 止损
    mode: 'A' / 'B'
    """
    cfg = ER20_CONFIG
    code = r['ts_code']
    # 公告前
    gap_score, pen = pre_run_and_penalty(daily, ann_idx, cur_idx)
    # 公告后反应
    react_score, sell_news = announcement_reaction(daily, ann_idx)
    ppr = positive_price_reaction(daily, ann_idx, cur_idx)
    if mode == 'A':
        fq = fundamental_quality_score(r)
        status, buy, tech, note = detect_pattern_a(daily, ann_idx, cur_idx, sell_news)
        theme = market_theme_score(code, stock2theme)
        er_score = (0.30 * fq + 0.20 * gap_score + 0.20 * react_score
                    + 0.25 * tech + 0.05 * theme - pen)
        er_score = round(min(100, max(0, er_score)), 1)
        # BUY 硬门槛: 评分(Score) + 形态(Pattern) + 触发(Trigger) 三者同时满足
        min_sc = cfg['strategy_a']['min_score']
        if buy and er_score < min_sc:
            buy = False
            status = 'WAIT_CONFIRM'
            note = f'{note}/评分{er_score}<{min_sc} 观察'
        return {
            'strategy': 'A_CONFIRMATION', 'fundamental_quality_score': fq,
            'expectation_gap_score': gap_score, 'announcement_reaction_score': react_score,
            'technical_confirmation_score': tech, 'market_theme_score': theme,
            'crowding_penalty': round(pen, 1), 'er20_a_score': er_score,
            'er20_b_score': None, 'final_score': er_score,
            'status': status, 'buy_signal': buy, 'note': note,
        }
    else:
        fr = fundamental_reversal_score(r)
        status, buy, tech, note = detect_pattern_b(daily, ann_idx, cur_idx)
        # 买点纪律: MA60 突破后仅允许 0~3% 偏离; 已远离则等回踩(规格§19)
        if buy:
            ma60_now = float(daily.iloc[cur_idx]['ma60'])
            if float(daily.iloc[cur_idx]['close']) > ma60_now * 1.03:
                buy = False
                status = 'WAIT_PULLBACK'
                note = '已偏离MA60>3%, 等回踩'
        theme = market_theme_score(code, stock2theme)
        er_score = (0.35 * fr + 0.25 * ppr + 0.30 * tech + 0.10 * theme)
        er_score = round(min(100, max(0, er_score)), 1)
        # BUY 硬门槛: 评分 + 形态 + 触发 三者同时满足
        min_sc = cfg['strategy_b']['min_score']
        if buy and er_score < min_sc:
            buy = False
            status = 'WAIT_CONFIRM'
            note = f'{note}/评分{er_score}<{min_sc} 观察'
        return {
            'strategy': 'B_REVERSAL', 'fundamental_reversal_score': fr,
            'expectation_gap_score': None, 'announcement_reaction_score': ppr,
            'technical_confirmation_score': tech, 'market_theme_score': theme,
            'crowding_penalty': 0.0, 'er20_a_score': None, 'er20_b_score': er_score,
            'final_score': er_score,
            'status': status, 'buy_signal': buy, 'note': note,
        }


def build_trade_plan(r, daily, cur_idx, mode, q2_proxy):
    """买点区间/止损(ATR clip 3~8%, 基于理想入场价)/20日计划"""
    cfg = ER20_CONFIG['risk']
    close = float(daily.iloc[cur_idx]['close'])
    atr = calc_atr14(daily)
    if pd.isna(atr):
        atr = close * 0.04
    if mode == 'A':
        # 买点: 突破整理平台高点(±2%追价)
        hi = float(daily.iloc[cur_idx - 4:cur_idx + 1]['high'].max())
        lo = round(hi * 0.98, 2)
        hi_zone = round(hi * 1.02, 2)
        ma10 = float(daily.iloc[cur_idx]['ma10']) if pd.notna(daily.iloc[cur_idx].get('ma10')) else close
        if close / ma10 > 1.08:
            lo, hi_zone = round(ma10 * 1.01, 2), round(ma10 * 1.06, 2)
            note = '距MA10>8% 禁止追高, 等回踩MA10'
        else:
            note = '突破平台承接'
    else:
        ma60 = float(daily.iloc[cur_idx]['ma60'])
        lo, hi_zone = round(ma60 * 1.00, 2), round(ma60 * 1.03, 2)
        note = 'MA60突破后0~3%介入'
    ideal = round((lo + hi_zone) / 2, 2)
    # 止损基于入场价(规格: initial_stop = entry - 1.5*ATR, clip 3~8%), 保证低于买点区
    stop_pct = min(cfg['max_stop'], max(cfg['min_stop'], cfg['atr_stop_multiple'] * atr / ideal))
    stop = round(ideal * (1 - stop_pct), 2)
    return {
        'buy_zone_low': lo, 'buy_zone_high': hi_zone, 'ideal_entry': ideal,
        'stop': stop, 'stop_pct': round(stop_pct * 100, 1), 'note': note,
        'max_hold_days': cfg['max_hold_days'], 'atr': round(atr, 3), 'q2_proxy': q2_proxy,
    }


def market_env_summary(scan_date):
    """市场环境: 本地通达信上证指数近20/60日涨跌幅(无网络依赖)"""
    idx = None
    for p in (os.path.join(TDX_PATH, 'sh', 'lday', 'sh000001.day'),
              os.path.join(TDX_PATH, 'vipdoc', 'sh', 'lday', 'sh000001.day')):
        idx = parse_tdx_day_file(p)
        if idx is not None:
            break
    if idx is None:
        return '上证指数: 本地无数据(跳过)'
    idx = idx[idx['trade_date'] <= scan_date].reset_index(drop=True)
    if len(idx) < 60:
        return '上证指数: 数据不足(跳过)'
    c = idx['close'].astype(float)
    r20 = (c.iloc[-1] / c.iloc[-21] - 1) * 100
    r60 = (c.iloc[-1] / c.iloc[-61] - 1) * 100
    return f'上证指数近20日 {r20:+.1f}% / 近60日 {r60:+.1f}%'


# ---------------- 主流程 ----------------

def scan(scan_date='20260819'):
    print('== ER20 v1.0 中报公告后20日Alpha | 扫描日', scan_date, '==')
    period = f'{scan_date[:4]}0630'
    pool = load_zhongbao_pool(period, scan_date)
    if pool.empty:
        print('中报池为空'); return None
    print(f'已披露中报: {len(pool)}')

    stock2theme = theme_map_stock2theme()
    dates = sorted(get_trade_dates(f'{int(scan_date[:4])-1}1101', scan_date))
    cur_date = max(d for d in dates if d <= scan_date)
    cur_idx_all = {d: i for i, d in enumerate(dates)}
    if cur_date not in cur_idx_all:
        print('交易日历缺失', cur_date); return None
    cur_i = cur_idx_all[cur_date]

    # 粗筛: 公告后 2~10 交易日窗口内 且 有改善(净利>0 或 Q2加速>0)
    cands = []
    for _, r in pool.iterrows():
        ann = str(r.get('ann_date', ''))
        if not ann or len(ann) != 8 or ann < f'{int(scan_date[:4]) - 1}0101':
            continue
        # 历史约束: 排除 ST/*ST/退市 与 北交所(.BJ)
        nm = str(r.get('name', ''))
        if nm and ('ST' in nm.upper() or nm.endswith('退')):
            continue
        if str(r['ts_code']).endswith('.BJ'):
            continue
        # 公告后第一个交易日(周末/假期公告取下个交易日作为锚)
        tds = [d for d in dates if d >= ann]
        if not tds:
            continue
        ann_td = tds[0]
        gap = cur_i - cur_idx_all[ann_td]
        if not (ER20_CONFIG['min_gap_days'] <= gap <= ER20_CONFIG['announcement_window']):
            continue
        q2 = r.get('q2_profit_yoy', np.nan)
        q1 = r.get('q1_profit_yoy', np.nan)
        ni = r.get('netprofit_yoy', np.nan)
        accel = (q2 - q1) if pd.notna(q2) and pd.notna(q1) else np.nan
        if pd.isna(accel) and pd.isna(ni):
            continue
        if pd.notna(ni) and ni <= -30 and pd.isna(accel):
            continue
        cands.append((r, ann, gap))
    print(f'公告窗口内候选: {len(cands)}')

    results = []
    for r, ann, gap in cands:
        try:
            daily = load_daily(r['ts_code'], scan_date, lookback_bars=300)
        except Exception:
            continue
        if daily is None or len(daily) < 120:
            continue
        daily = daily.reset_index(drop=True)
        daily = add_ma(daily)
        if 'pct_chg' not in daily.columns:
            daily['pct_chg'] = daily['close'].pct_change() * 100.0
        daily['pct_chg'] = daily['pct_chg'].fillna(0.0)
        # 反应锚 = 公告后第一个交易日(严格在公告日之后; 中报盘后披露, 公告日当天行情不属于"反应", 防未来函数)
        nxt = daily[daily['trade_date'] > ann]
        if nxt.empty:
            continue
        ann_idx = nxt.index[0]
        if ann_idx >= len(daily) - 1:
            continue
        cur_idx = len(daily) - 1
        # 默认跑 A, B 作为次优先
        sig = None
        for mode in ('A', 'B'):
            s = build_signal(r, daily, ann_idx, cur_idx, stock2theme, mode)
            if mode == 'A' and s['buy_signal']:
                sig = s
                sig['chosen'] = 'A'
                break
            if mode == 'B' and s['buy_signal'] and sig is None:
                sig = s
                sig['chosen'] = 'B'
        if sig is None:
            # 即使未 BUY 也记录观察(评级>=60 或 A/B 有分数)
            sA = build_signal(r, daily, ann_idx, cur_idx, stock2theme, 'A')
            sB = build_signal(r, daily, ann_idx, cur_idx, stock2theme, 'B')
            best = max([sA['final_score'], sB['final_score']])
            sig = sA if sA['final_score'] >= sB['final_score'] else sB
            sig['chosen'] = 'A' if sig is sA else 'B'
        plan = build_trade_plan(r, daily, cur_idx, 'A' if sig['chosen'] == 'A' else 'B', sig.get('q2_proxy', False))
        rec = {
            '代码': r['ts_code'], '名称': r.get('name', ''), '公告日': ann,
            '公告间隔日': gap, '策略': sig['strategy'],
            '状态': sig['status'], 'buy_signal': sig['buy_signal'],
            'ER20评分': sig['final_score'],
            'Q1净利同比': round(r.get('q1_profit_yoy', np.nan), 1) if pd.notna(r.get('q1_profit_yoy')) else '',
            'Q2净利同比': round(r.get('q2_profit_yoy', np.nan), 1) if pd.notna(r.get('q2_profit_yoy')) else '',
            '业绩加速度': round((r.get('q2_profit_yoy', np.nan) - r.get('q1_profit_yoy', np.nan)), 1)
                          if pd.notna(r.get('q2_profit_yoy')) and pd.notna(r.get('q1_profit_yoy')) else '',
            '营收同比': round(r.get('tr_yoy', np.nan), 1) if pd.notna(r.get('tr_yoy')) else '',
            '扣非同比': round(r.get('dt_netprofit_yoy', np.nan), 1) if pd.notna(r.get('dt_netprofit_yoy')) else '',
            'Q2_proxy': sig.get('q2_proxy', r.get('q2_proxy', False)),
            '基本面分': sig.get('fundamental_quality_score') or sig.get('fundamental_reversal_score'),
            '预期差分': sig.get('expectation_gap_score', ''),
            '公告反应分': sig.get('announcement_reaction_score'),
            '技术确认分': sig.get('technical_confirmation_score'),
            '主题分': sig.get('market_theme_score', ''),
            '透支惩罚': sig.get('crowding_penalty', 0),
            '状态备注': sig.get('note', ''),
            'buy_zone_low': plan['buy_zone_low'], 'buy_zone_high': plan['buy_zone_high'],
            'ideal_entry': plan['ideal_entry'], '止损': plan['stop'],
            '止损%': plan['stop_pct'], 'ATR': plan['atr'],
            '买点说明': plan['note'], '持仓上限日': plan['max_hold_days'],
        }
        results.append(rec)
    out = pd.DataFrame(results)
    if not out.empty:
        out = out.sort_values(['buy_signal', 'ER20评分'], ascending=[False, False]).reset_index(drop=True)
        csv = os.path.join(REPORT_DIR, f'er20_scan_{scan_date}.csv')
        out.to_csv(csv, index=False, encoding='utf-8-sig')
        print(f'扫描明细已存: {csv}')
    return out


def write_report(out, scan_date):
    if out is None or out.empty:
        print('无候选'); return
    # 规格§17: <60 不输出(报告与评级), CSV 扫描明细保留全部候选供因子研究
    out = out[out['ER20评分'] >= 60].copy()
    buys = out[out['buy_signal']]
    wa = out[~out['buy_signal']]
    lines = []
    lines.append('══════════════════════════════════════════════')
    lines.append('          ER20 中报公告后20日Alpha')
    lines.append('══════════════════════════════════════════════')
    lines.append('')
    lines.append(f'扫描日期: {scan_date}')
    lines.append(f'市场环境: {market_env_summary(scan_date)}')
    lines.append(f'窗口内候选(≥60分输出): {len(out)} 只')
    lines.append(f'符合Strategy A: {(out["策略"].str.startswith("A")).sum()} 只')
    lines.append(f'符合Strategy B: {(out["策略"].str.startswith("B")).sum()} 只')
    lines.append(f'最终BUY: {len(buys)} 只')
    lines.append('')
    lines.append('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    # S/A/B 分级 (S≥80且BUY; A 70~80; B 60~70只观察)
    grade_map = {'S': [], 'A': [], 'B': []}
    for _, r in out.iterrows():
        sc = r['ER20评分']
        if r['buy_signal'] and sc >= 80:
            grade_map['S'].append(r)
        elif sc >= 70:
            grade_map['A'].append(r)
        elif sc >= 60:
            grade_map['B'].append(r)
    for g in ('S', 'A', 'B'):
        if not grade_map[g]:
            continue
        lines.append(f'【{g}级】({len(grade_map[g])}只)')
        lines.append('')
        for r in grade_map[g]:
            code = r['代码']
            lines.append(f'1. {r["名称"]}（{code}）')
            lines.append(f'   策略: ER20_{r["策略"]}')
            lines.append(f'   ER20 Score: {r["ER20评分"]}')
            lines.append(f'   基本面: Q1利润同比 {r["Q1净利同比"]}% → Q2 {r["Q2净利同比"]}% (加速度 {r["业绩加速度"]}pct, {"Proxy" if r["Q2_proxy"] else "精确"}); 营收同比 {r["营收同比"]}%; 扣非同比 {r["扣非同比"]}%')
            lines.append(f'   公告前: 20日涨幅见明细; 透支惩罚 {r["透支惩罚"]}')
            lines.append(f'   公告反应: 反应分 {r["公告反应分"]}')
            lines.append(f'   技术形态: {r["状态备注"]} (技术确认分 {r["技术确认分"]})')
            lines.append(f'   买入: BUY_ZONE {r["buy_zone_low"]}~{r["buy_zone_high"]} (理想 {r["ideal_entry"]}) | 状态 {r["状态"]}')
            lines.append(f'   止损: {r["止损"]} ({r["止损%"]}%) | 持仓上限 {r["持仓上限日"]}日')
            lines.append(f'   核心逻辑: {r["买点说明"]}')
            lines.append('')
    # 对比表
    lines.append('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    lines.append('| 排名 | 股票 | 策略 | ER20 | 基本面 | 预期差/反转 | 公告反应 | 技术确认 | 当前状态 | 买入 |')
    lines.append('| -- | -- | -- | ---: | --: | -----: | ---: | ---: | ---- | -- |')
    for i, (_, r) in enumerate(out.iterrows(), 1):
        st = 'BUY' if r['buy_signal'] else r['状态']
        buy = '✅' if r['buy_signal'] else '—'
        lines.append(f"| {i} | {r['名称']} | {r['策略']} | {r['ER20评分']:.0f} | {r['基本面分']:.0f} | {r['预期差分'] or '-'} | {r['公告反应分']:.0f} | {r['技术确认分']:.0f} | {st} | {buy} |")
    txt = '\n'.join(lines)
    fp = os.path.join(REPORT_DIR, f'er20_report_{scan_date}.md')
    with open(fp, 'w', encoding='utf-8') as f:
        f.write(txt)
    print(f'报告已存: {fp}')
    return txt


# ---------------- 回测 ----------------

def backtest(period_list=('20250831', '20260819')):
    """
    最小回测: 优先复用已存 er20_scan_{date}.csv(缺失才重扫, 避免全量重算),
    对信号日之后计算 T+5/10/20 收益, 按 评分档×策略 分层统计。
    未来收益仅用于回测结果, 不参与评分。
    """
    print('\n== ER20 回测(仅 T+5/10/20 统计) ==')
    out_all = []
    for scan_date in period_list:
        csvp = os.path.join(REPORT_DIR, f'er20_scan_{scan_date}.csv')
        if os.path.exists(csvp):
            out = pd.read_csv(csvp, dtype={'代码': str, '公告日': str})
            print(f'扫描日 {scan_date}: 复用已存扫描 {len(out)} 候选')
        else:
            out = scan(scan_date)
            print(f'扫描日 {scan_date}: 无缓存, 重扫 {0 if out is None else len(out)} 候选')
        if out is None or out.empty:
            continue
        if 'buy_signal' in out.columns:
            out['buy_signal'] = out['buy_signal'].astype(str).str.strip().isin(['True', 'true', '1'])
        for _, r in out.iterrows():
            code, ann = str(r['代码']), str(r['公告日'])
            try:
                # 回测需信号日之后的 T+20 行情: 取截至最新的远期 end_date, 仅切片不参与评分
                daily = load_daily(code, '20991231', lookback_bars=400)
            except Exception:
                continue
            if daily is None or len(daily) < 120:
                continue
            daily = daily.reset_index(drop=True)
            if 'pct_chg' not in daily.columns:
                daily['pct_chg'] = daily['close'].pct_change() * 100.0
            daily['pct_chg'] = daily['pct_chg'].fillna(0.0)
            # 从信号日(scan_date)之后持有
            idx = daily.index[daily['trade_date'] > scan_date]
            if idx.empty:
                continue
            base = float(daily.loc[idx[0] - 1, 'close']) if idx[0] > 0 else float(daily.iloc[idx[0]]['close'])
            row = {'代码': code, '名称': r['名称'], '策略': r['策略'], '评分': r['ER20评分'],
                   'buy_signal': r['buy_signal'], '公告日': ann, '信号日': scan_date}
            for h in (5, 10, 20):
                if len(idx) >= h:
                    px = float(daily.iloc[idx[0] + h - 1]['close'])
                    row[f'T+{h}'] = round((px / base - 1) * 100, 2)
                else:
                    row[f'T+{h}'] = np.nan
            out_all.append(row)
    bt = pd.DataFrame(out_all)
    if bt.empty:
        print('无回测样本'); return
    csv = os.path.join(REPORT_DIR, 'er20_backtest.csv')
    bt.to_csv(csv, index=False, encoding='utf-8-sig')
    print(f'回测明细: {csv}')
    bands = [('≥80', 80, 999), ('70~80', 70, 80), ('60~70', 60, 70)]
    for strat in ('A', 'B'):
        sub = bt[bt['策略'].str.startswith(strat)]
        if sub.empty:
            continue
        print(f'\n[Strategy {strat}] n={len(sub)} BUY={int(sub["buy_signal"].sum())}')
        rows = [('全部', 0, 999)] + bands
        for label, lo, hi in rows:
            sband = sub[(sub['评分'] >= lo) & (sub['评分'] < hi)]
            if sband.empty:
                continue
            parts = [f'  档位{label}: n={len(sband)}']
            for h in (5, 10, 20):
                s = sband[f'T+{h}'].dropna()
                if s.empty:
                    continue
                parts.append(f'T+{h}均值{s.mean():+.2f}% 胜率{(s > 0).mean() * 100:.0f}% 中位{s.median():+.2f}%')
            print(' | '.join(parts))
        sbu = sub[sub['buy_signal']]
        if not sbu.empty:
            parts = [f'  BUY档: n={len(sbu)}']
            for h in (5, 10, 20):
                s = sbu[f'T+{h}'].dropna()
                if s.empty:
                    continue
                parts.append(f'T+{h}均值{s.mean():+.2f}% 胜率{(s > 0).mean() * 100:.0f}%')
            print(' | '.join(parts))
    return bt


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='20260819', help='扫描日 YYYYMMDD')
    ap.add_argument('--backtest', action='store_true', help='运行历史回测')
    args = ap.parse_args()
    if args.backtest:
        backtest(('20250831', '20260819'))
    else:
        out = scan(args.date)
        write_report(out, args.date)
