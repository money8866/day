# -*- coding: utf-8 -*-
"""
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃  中报猎手 V2.0 — ER20 六模块翻倍潜力扫描                          ┃
┃                                                                ┃
┃  以 2026 中报【实际披露业绩】为核心，按 ER20 六模块打分：         ┃
┃    A 业绩 40 / B 持续 20 / C 弹性 15 / D 估值 15 / E 技术 10     ┃
┃    F 主题+行业景气+机构预测 加成 ±10                             ┃
┃    再叠加 EQ 盈利质量风险惩罚（0 ~ -15）                         ┃
┃                                                                ┃
┃  配套能力：翻倍发动机分类 / 预期差(公告反应) / 成长×估值四象限    ┃
┃  输出：TOP30 + 三大榜单（翻倍发动机榜/预期差榜/回调上车榜）       ┃
┃         + 每只交易档案（买点/止损/逻辑/风险）                    ┃
┃                                                                ┃
┃  数据源：全本地缓存（D:\\mystock\\cache_daily），运行无需联网     ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
"""

import os
import sys
import json
import time
import glob
import argparse
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import treasure_hunter as th
from zhongbao_hunter import _num, _calc_q2_from_cache

SOLO_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SOLO_DIR)

CACHE_DIR = r'D:\mystock\cache_daily'
REPORT_DIR = os.path.join(SOLO_DIR, 'report_daily')
FIN_IND_H1 = os.path.join(CACHE_DIR, 'fin_ind_2026H1_full.parquet')
THEME_MAP_DIR = CACHE_DIR
THEME_SCORE_DIR = REPORT_DIR


# ============================================================
# ZHConfig — 全部阈值与权重（单点可调，零 magic numbers）
# ============================================================
class ZHConfig:
    # ── 池 / 硬过滤 ──
    MV_MIN = 20.0          # 市值下界(亿)
    MV_MAX = 300.0         # 市值上界(亿) —— 翻倍弹性空间
    GROWTH_MIN = 30.0      # 硬过滤: 扣非或净利增速 ≥30%
    NI_MIN = 0.30          # 硬过滤: 中报归母净利 ≥0.30亿(防微基数)
    ST_BLOCK = True        # 过滤 ST / *ST / 退市整理

    # ── ER20 六模块权重 ──
    W = {'a': 40.0, 'b': 20.0, 'c': 15.0, 'd': 15.0, 'e': 10.0}
    F_MAX = 10.0           # F 主题+行业+机构 加成上限
    EQ_MIN = -15.0         # EQ 惩罚下限

    # ── A 业绩(40) 档位 ──
    A_DT = [(300, 15.0), (200, 13.5), (150, 12.0), (100, 10.5),
            (60, 8.5), (40, 7.0), (30, 5.5), (20, 4.0), (0, 2.0)]
    A_NP = [(300, 8.0), (200, 7.2), (150, 6.5), (100, 5.5),
            (60, 4.5), (30, 3.5), (0, 2.0)]
    A_TR = [(100, 8.0), (60, 7.0), (40, 6.0), (30, 5.0),
            (20, 4.0), (10, 2.5), (0, 1.0)]
    A_NI = [(5.0, 5.0), (3.0, 4.5), (2.0, 4.0), (1.0, 3.3),
            (0.5, 2.5), (0.3, 1.8), (0.2, 1.2)]
    A_GM = [(50.0, 4.0), (40.0, 3.4), (30.0, 2.6), (20.0, 1.8)]
    A_DT_MISS_DOWN = 0.85  # 缺扣非时净利增速打折系数

    # ── B 持续(20) ──
    B_Q1 = [(200, 4.0), (100, 3.4), (60, 2.8), (30, 2.2), (0, 1.4)]
    B_QS = [(60, 7.0), (40, 6.0), (25, 5.0), (10, 3.5), (0, 2.0)]
    B_QS_NEUTRAL = 3.0    # Q2营收同比缺失中性分
    B_ACC = 4.0            # 加速度满分
    B_CSH = 2.0            # 现金流满分

    # ── C 弹性(15) ──
    C_MV = [(300, 4.5), (200, 6.0), (120, 7.5), (80, 9.0), (60, 10.0), (40, 11.0)]
    C_TO_SWEET = (1.0, 8.0)   # 换手率最优区间(%)

    # ── D 估值(15) ──
    D_PEG = [(3.0, 2.5), (2.0, 4.0), (1.5, 5.5), (1.0, 7.0), (0.8, 8.0),
             (0.5, 9.0), (0.3, 10.0)]
    D_PE = [(100, 1.8), (70, 2.6), (50, 3.5), (35, 4.3), (25, 5.0)]
    D_PE_NEUTRAL = 2.0    # pe_ttm 缺失中性分
    D_PEG_MISS = 3.0       # PEG 无法计算时中性分

    # ── E 技术(10) ──
    E_H120 = [(30, 5.0), (20, 4.2), (10, 3.4), (5, 2.6), (0, 1.8)]
    E_RS = [(70, 3.0), (50, 2.2), (30, 1.4)]
    E_VR_SWEET = (0.8, 1.8)
    E_NEUTRAL = {'h120': 2.0, 'rs': 1.2, 'vr': 1.0}

    # ── F 主题+行业+机构(±10) ──
    F_THEME = [(75, 5.0), (65, 4.0), (55, 3.0), (45, 2.0)]
    F_THEME_NEUTRAL = 2.0
    F_THEME_DECAY = {'退潮': -1.5, '分歧': -1.0, '高潮': -0.5}
    F_IND = [(50, 3.0), (30, 2.4), (15, 1.8), (0, 1.2)]
    F_IND_NEUTRAL = 1.2
    F_RC = {'have_buy': 2.0, 'have': 1.2, 'none': 0.5}   # 机构预测

    # ── 预期差 ──
    GAP_SURPRISE = [(200, 65.0), (100, 55.0), (60, 40.0), (30, 25.0)]
    GAP_SPACE_K = 35.0      # 价格空间分系数
    GAP_SPACE_CAP = 0.30    # 公告后涨幅超过30%视为已定价
    GAP_PRE = [(0.30, -15.0), (0.15, -8.0), (0.05, -3.0)]
    GAP_POST_RETREAT = 5.0  # 公告后回调反而加预期差

    # ── 四象限 / 分类 ──
    QUAD_B = 11.0           # 成长性高阈值(B分)
    QUAD_D = 10.0           # 估值优势阈值(D分)

    # ── 技术回踩口径（回调上车榜） ──
    PULLBACK_WIN = (-4.0, 4.0)   # 距MA20 区间
    PULLBACK_MIN_DAYS = 0

    # ── 交易档案 ──
    STOP_LOSS_PCT = 0.92    # 止损 = max(现价×92%, MA60)


# ============================================================
# 数据层 — 全本地缓存，缺失统一 _num → None（绝不编造）
# ============================================================
def _num_safe(v):
    return _num(v)


def load_h1() -> pd.DataFrame:
    """读取 2026H1 fina_indicator 全字段缓存（含 q1/q2 拆分行）"""
    if not os.path.exists(FIN_IND_H1):
        print(f'  [错误] H1 缓存不存在: {FIN_IND_H1}')
        sys.exit(1)
    df = pd.read_parquet(FIN_IND_H1)
    df = df[df['end_date'].astype(str).str.startswith('20260630')].copy()
    df = df.drop_duplicates('ts_code', keep='last')
    return df


def load_pool(trade_date: str, h1: pd.DataFrame) -> pd.DataFrame:
    """市值池 20~300亿 + 非北交所 + 非ST + 已披露中报"""
    stocks = th.get_stock_list()
    basic = th.get_daily_basic(trade_date)
    if basic is None or len(basic) == 0:
        print('  [错误] daily_basic 无数据，检查交易日')
        sys.exit(1)
    df = stocks.merge(
        basic[['ts_code', 'total_mv', 'circ_mv', 'pe_ttm', 'pe', 'pb',
               'close', 'turnover_rate', 'volume_ratio']],
        on='ts_code', how='inner')
    df['市值(亿)'] = df['total_mv'] / 10000
    # 排除北交所 + 老八股占位
    df = df[~df['ts_code'].str.endswith('.BJ')].copy()
    df = df[~df['ts_code'].str.match(r'^(8\d{5}|4\d{5}|92\d{4})\.SZ$')].copy()
    # 非 ST
    if ZHConfig.ST_BLOCK:
        df = df[~df['name'].str.contains(r'ST|退', na=False)].copy()
    pool = df[(df['市值(亿)'] >= ZHConfig.MV_MIN)
              & (df['市值(亿)'] <= ZHConfig.MV_MAX)].copy()
    # 与中报缓存合并（h1 自带 name 列会冲突，丢弃保留 stock_basic 的 name）
    h1_use = h1.drop(columns=['name'], errors='ignore')
    pool = pool.merge(h1_use, on='ts_code', how='inner')
    # 净利规模(元): 归母 ≈ 扣非 + 非经常
    pool['归母净利(亿)'] = (pool['profit_dedt'].fillna(0) + pool['extra_item'].fillna(0)) / 1e8
    return pool


def build_industry_prosperity(h1: pd.DataFrame) -> dict:
    """行业景气：H1 全市场按申万一级行业(来自 stock_basic)的中位净利增速"""
    stocks = th.get_stock_list()
    ind_map = dict(zip(stocks['ts_code'], stocks['industry']))
    h1 = h1.copy()
    h1['_ind'] = h1['ts_code'].map(ind_map)
    out = {}
    for ind, g in h1.groupby('_ind'):
        vals = pd.to_numeric(g['netprofit_yoy'], errors='coerce').dropna()
        if len(vals) >= 3:
            out[ind] = float(vals.median())
    return out


def load_theme_ctx(trade_date: str) -> tuple:
    """返回 (stock2theme, theme_heat, theme_lifecycle)"""
    stock2theme, heat, lifecycle = {}, {}, {}
    # 1) theme_stock_map_v2 成份股归属
    cands = [
        os.path.join(THEME_MAP_DIR, f'theme_stock_map_v2_{trade_date}.json'),
        os.path.join(THEME_MAP_DIR, f'theme_stock_map_{trade_date}.json'),
        os.path.join(THEME_MAP_DIR, 'theme_stock_map_v2_latest.json'),
        os.path.join(THEME_MAP_DIR, 'theme_stock_map_latest.json'),
    ]
    for p in cands:
        if os.path.exists(p):
            try:
                data = json.load(open(p, encoding='utf-8'))
                stocks = data.get('stocks', {})
                for code, info in stocks.items():
                    ths = info.get('themes') if isinstance(info, dict) else None
                    if ths:
                        stock2theme[code] = ths[0]
                break
            except Exception:
                continue
    # 2) theme_scores_v2 主题热度
    tc = os.path.join(THEME_SCORE_DIR, f'theme_scores_v2_{trade_date}.csv')
    if not os.path.exists(tc):
        gl = sorted(glob.glob(os.path.join(THEME_SCORE_DIR, 'theme_scores_v2_*.csv')))
        if gl:
            tc = gl[-1]
    if os.path.exists(tc):
        try:
            ts = pd.read_csv(tc, encoding='utf-8-sig')
            for _, r in ts.iterrows():
                t = r['theme']
                heat[t] = float(r.get('composite_score', 50)) if pd.notna(r.get('composite_score')) else 50.0
                lifecycle[t] = str(r.get('lifecycle', ''))
        except Exception:
            pass
    return stock2theme, heat, lifecycle


def load_report_rc(pool: pd.DataFrame, trade_date: str) -> dict:
    """机构预测（可选，best-effort）：读取已落库的 report_rc 汇总缓存，失败返回 {}"""
    out = {}
    cands = [os.path.join(CACHE_DIR, f'report_rc_all_{trade_date}.parquet')]
    for p in cands:
        if not os.path.exists(p):
            continue
        try:
            raw = pd.read_parquet(p)
            for _, r in raw.iterrows():
                out[r['ts_code']] = r
            return out
        except Exception:
            continue
    return out


def _load_daily_local(code: str):
    """仅读本地日线缓存（绝不联网）；无缓存或太短返回 None → 技术面用中性分"""
    key = f"daily_{code.replace('.', '_')}"
    # 1) 精确 key（与 treasure_hunter 同口径：end=最近交易日, days=230）
    try:
        end_date = th.get_last_trade_date()
        start_dt = datetime.strptime(end_date, '%Y%m%d') - timedelta(days=230)
        start_date = start_dt.strftime('%Y%m%d')
        exact = os.path.join(CACHE_DIR, f'treasure_{key}_{start_date}_{end_date}.parquet')
        if os.path.exists(exact):
            df = pd.read_parquet(exact)
            if len(df) >= 60:
                return df
    except Exception:
        pass
    # 2) 兜底：从最新缓存往前找
    files = sorted(glob.glob(os.path.join(CACHE_DIR, f'treasure_{key}_*.parquet')))
    if not files:
        return None
    for p in reversed(files[-4:]):
        try:
            df = pd.read_parquet(p)
        except Exception:
            continue
        if len(df) >= 60:
            return df
    return None


def _tech_and_gap(code: str, ann_date) -> dict:
    """本地日线 → 技术指标 + 预期差（公告前20日/后涨幅）"""
    res = {}
    try:
        daily = _load_daily_local(code)
        if daily is None or len(daily) <= 60:
            return res
        daily = daily.sort_values('trade_date').reset_index(drop=True)
        closes = daily['close'].astype(float).values
        highs = daily['high'].astype(float).values
        lows = daily['low'].astype(float).values
        volumes = daily['vol'].astype(float).values
        dates = daily['trade_date'].astype(str).values
        cur = float(closes[-1])

        # ── 技术面 ──
        high_120 = float(daily.tail(120)['high'].max())
        res['pct_from_120d_high'] = round((high_120 - cur) / high_120 * 100, 2) if high_120 > 0 else 999.0
        ma20 = pd.Series(closes).rolling(20).mean()
        ma60 = pd.Series(closes).rolling(60).mean()
        ma20v = float(ma20.dropna().iloc[-1]) if len(ma20.dropna()) > 0 else cur
        ma60v = float(ma60.dropna().iloc[-1]) if len(ma60.dropna()) > 0 else cur
        res['pct_below_ma20'] = round((cur - ma20v) / ma20v * 100, 2) if ma20v > 0 else 0.0
        ma20_series = ma20.values
        days_above = 0
        for j in range(len(closes) - 1, -1, -1):
            if np.isnan(ma20_series[j]) or closes[j] <= ma20_series[j]:
                break
            days_above += 1
        res['days_above_ma20'] = days_above
        # MA20 斜率(5日)
        ma20_ok = ma20.dropna()
        if len(ma20_ok) >= 6:
            m_prev = float(ma20_ok.iloc[-6])
            res['ma20_slope'] = round((ma20v - m_prev) / m_prev * 100, 2) if m_prev > 0 else 0.0
        else:
            res['ma20_slope'] = 0.0
        res['ma20'] = round(ma20v, 2)
        res['ma60'] = round(ma60v, 2)
        res['cur'] = cur
        if len(volumes) >= 20:
            v5, v20 = float(np.mean(volumes[-5:])), float(np.mean(volumes[-20:]))
            res['volume_ratio'] = round(v5 / v20, 2) if v20 > 0 else 1.0
        else:
            res['volume_ratio'] = 1.0
        # 右侧强度（复用 treasure 口径）
        srow = {'days_above_ma20': days_above, 'pct_below_ma20': res['pct_below_ma20'],
                'ma20_slope': res['ma20_slope'], 'ma20': ma20v, 'ma60': ma60v,
                'volume_ratio': res['volume_ratio'],
                'pct_from_120d_high': res['pct_from_120d_high']}
        try:
            res['rightside'] = th._compute_rightside_strength(srow).get('右侧强度总分', 0.0)
        except Exception:
            res['rightside'] = 0.0

        # ── 预期差 ──
        if ann_date:
            ann = str(ann_date)
            idx = [i for i, d in enumerate(dates) if d >= ann]
            if idx:
                i0 = idx[0]                      # 公告后首个交易日
                ann_close = float(closes[i0])
                # 公告前20交易日涨幅
                j0 = max(0, i0 - 20)
                pre_base = float(closes[j0])
                pre_ret = (ann_close / pre_base - 1.0) if pre_base > 0 else 0.0
                post_ret = (cur / ann_close - 1.0) if ann_close > 0 else 0.0
                res['ann_close'] = round(ann_close, 2)
                res['pre_ret20'] = round(pre_ret * 100, 1)
                res['post_ret'] = round(post_ret * 100, 1)
    except Exception:
        pass
    return res


# ============================================================
# ER20 六模块评分
# ============================================================
def _tier(v, table, default):
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return default
    for thre, score in table:
        if v >= thre:
            return score
    return default


def score_a(r) -> float:
    """A 业绩(40): 扣非15 + 净利8 + 营收8 + 规模5 + 壁垒4"""
    dt = r.get('dt_netprofit_yoy')
    npg = r.get('netprofit_yoy')
    tr = r.get('tr_yoy')
    if tr is None:
        tr = r.get('or_yoy')
    ni = r.get('归母净利(亿)')
    gm = r.get('grossprofit_margin')

    if dt is None:
        a1 = _tier(npg, ZHConfig.A_DT, 0.0) * ZHConfig.A_DT_MISS_DOWN
    else:
        a1 = _tier(dt, ZHConfig.A_DT, 0.0)
    a2 = _tier(npg, ZHConfig.A_NP, 0.0)
    a3 = _tier(tr, ZHConfig.A_TR, 0.0)
    a4 = _tier(ni, ZHConfig.A_NI, 0.5)
    a5 = _tier(gm, ZHConfig.A_GM, 0.5)
    return round(a1 + a2 + a3 + a4 + a5, 2)


def score_b(r) -> float:
    """B 持续(20): Q1净利同比4 + Q2动能7 + 加速度4 + 连续性3 + 现金流2"""
    q1 = r.get('q1_profit_yoy')      # Q1净利同比
    qs = r.get('q_sales_yoy')        # Q2单季营收同比
    qo = r.get('q_op_qoq')           # Q2营业利润环比
    npg = r.get('netprofit_yoy')     # H1净利同比
    ocf = r.get('ocf_yoy')           # 现金流同比
    ocfps = r.get('ocfps')

    b1 = _tier(q1, ZHConfig.B_Q1, 0.5)
    # Q2 动能
    if qs is None:
        b2 = ZHConfig.B_QS_NEUTRAL
    else:
        b2 = _tier(qs, ZHConfig.B_QS, 0.5)
        if qo is not None:
            if qo >= 20:
                b2 = min(7.0, b2 + 1.0)
            elif qo < -30:
                b2 = max(0.0, b2 - 1.0)
    # 加速度: H1 vs Q1
    if npg is not None and q1 is not None:
        if npg >= 0 and q1 >= 0 and npg > q1:
            b3 = ZHConfig.B_ACC
        elif npg >= 0 and q1 >= 0 and npg >= q1 * 0.7:
            b3 = 2.5
        elif npg >= 30 and q1 >= 30:
            b3 = 1.5
        else:
            b3 = 0.5
    else:
        b3 = 1.0
    # 连续性: H1>0 & Q1>0 & Q2营收>0
    cont = sum(1 for x in (npg, q1, qs) if x is not None and x > 0)
    b4 = {3: 3.0, 2: 2.0, 1: 1.0}.get(cont, 0.0)
    # 现金流
    csh = 0.0
    if ocfps is not None and ocfps > 0:
        csh += 1.0
    if ocf is not None and ocf > 0:
        csh += 1.0
    b5 = csh
    return round(b1 + b2 + b3 + b4 + b5, 2)


def score_c(r) -> float:
    """C 弹性(15): 市值弹性11 + 换手活跃4"""
    mv = r.get('市值(亿)')
    c1 = _tier(mv, ZHConfig.C_MV, 3.0)
    to = r.get('turnover_rate')
    if to is None:
        c2 = 2.0
    else:
        lo, hi = ZHConfig.C_TO_SWEET
        if lo <= to <= hi:
            c2 = 4.0
        elif hi < to <= 15:
            c2 = 3.0
        elif 0.5 <= to < lo:
            c2 = 2.5
        elif to > 15:
            c2 = 2.0
        else:
            c2 = 1.5
    return round(c1 + c2, 2)


def score_d(r) -> float:
    """D 估值(15): PEG 10 + PE 5"""
    pe = r.get('pe_ttm')
    dt = r.get('dt_netprofit_yoy')
    npg = r.get('netprofit_yoy')
    g = dt if dt is not None else npg
    peg = None
    if pe is not None and pe > 0 and g is not None and g > 0:
        peg = pe / g
    if peg is None:
        d1 = ZHConfig.D_PEG_MISS
    else:
        d1 = _tier(peg, ZHConfig.D_PEG, 1.0)
    if pe is None:
        d2 = ZHConfig.D_PE_NEUTRAL
    else:
        d2 = _tier(pe, ZHConfig.D_PE, 1.0)
    return round(d1 + d2, 2)


def score_e(r) -> float:
    """E 技术(10): 距120日高5 + 右侧强度3 + 量能2"""
    ph = r.get('pct_from_120d_high')
    e1 = _tier(ph, ZHConfig.E_H120, ZHConfig.E_NEUTRAL['h120']) if ph is not None else ZHConfig.E_NEUTRAL['h120']
    rs = r.get('rightside')
    e2 = _tier(rs, ZHConfig.E_RS, ZHConfig.E_NEUTRAL['rs']) if rs is not None else ZHConfig.E_NEUTRAL['rs']
    vr = r.get('volume_ratio')
    if vr is None:
        e3 = ZHConfig.E_NEUTRAL['vr']
    else:
        lo, hi = ZHConfig.E_VR_SWEET
        if lo <= vr <= hi:
            e3 = 2.0
        elif hi < vr <= 3.0 or 0.5 <= vr < lo:
            e3 = 1.4
        else:
            e3 = 0.8
    return round(e1 + e2 + e3, 2)


def score_f(r, theme_heat, theme_life, ind_pros, rc) -> float:
    """F 主题+行业+机构(±10): 主题热度5 + 行业景气3 + 机构预测2"""
    # 主题热度
    theme = r.get('theme') or ''
    if theme and theme in theme_heat:
        f1 = _tier(theme_heat[theme], ZHConfig.F_THEME, 1.0)
        f1 += ZHConfig.F_THEME_DECAY.get(theme_life.get(theme, ''), 0.0)
    elif theme:
        f1 = 1.0
    else:
        f1 = ZHConfig.F_THEME_NEUTRAL
    # 行业景气
    ind = r.get('industry') or ''
    if ind and ind in ind_pros:
        f2 = _tier(ind_pros[ind], ZHConfig.F_IND, 0.5)
    else:
        f2 = ZHConfig.F_IND_NEUTRAL
    # 机构预测
    rc_info = rc.get(r['ts_code']) if rc else None
    if rc_info is not None:
        buy = float(rc_info.get('buy_ratio', 0) or 0)
        f3 = ZHConfig.F_RC['have_buy'] if buy >= 0.5 else ZHConfig.F_RC['have']
    else:
        f3 = ZHConfig.F_RC['none']
    total = f1 + f2 + f3
    return round(max(-ZHConfig.F_MAX, min(ZHConfig.F_MAX, total)), 2)


def eq_penalty(r) -> float:
    """EQ 盈利质量风险惩罚(0 ~ -15)"""
    dt = r.get('dt_netprofit_yoy')
    npg = r.get('netprofit_yoy')
    tr = r.get('tr_yoy')
    ocf = r.get('ocf_yoy')
    qo = r.get('q_op_qoq')
    pen = 0.0
    # eq1 扣非/归母背离 → 一次性收益主导
    if dt is not None and npg is not None and npg >= 30:
        if dt < 0 and npg > 0:
            pen = min(pen, -8.0)
        elif dt < npg * 0.5:
            pen = min(pen, -5.0)
    # eq2 现金流恶化
    if ocf is not None and ocf < -30:
        pen = min(pen, -4.0)
    elif ocf is not None and ocf < 0:
        pen = min(pen, -2.0)
    # eq3 营收利润背离（利润暴增营收滞涨）
    if npg is not None and npg >= 60 and tr is not None and tr < 10:
        pen = min(pen, -3.0)
    # eq4 环比动能骤转差
    if qo is not None and qo < -50 and npg is not None and npg >= 30:
        pen = min(pen, -2.0)
    return round(max(ZHConfig.EQ_MIN, pen), 2)


def er20_total(r, f_score, eq) -> float:
    """ER20 = A+B+C+D+E+F+EQ，钳制 [0,100]"""
    base = (score_a(r) + score_b(r) + score_c(r) + score_d(r) + score_e(r))
    return round(max(0.0, min(100.0, base + f_score + eq)), 2)


# ============================================================
# 分类 / 四象限
# ============================================================
def classify_driver(r) -> str:
    """翻倍发动机分类（优先顺序）"""
    dt = r.get('dt_netprofit_yoy')
    npg = r.get('netprofit_yoy')
    tr = r.get('tr_yoy')
    qs = r.get('q_sales_yoy')
    gm = r.get('grossprofit_margin')
    g = dt if dt is not None else npg
    g = g if g is not None else 0
    tr = tr if tr is not None else 0
    qs = qs if qs is not None else tr

    if qs >= 40 and g >= 60:
        return '需求爆发'
    if g >= 100 and tr < 25:
        return '困境反转'
    if gm is not None and gm >= 40 and g >= 30:
        return '高壁垒成长'
    return '稳健增长'


def quadrant(r, b_score, d_score) -> str:
    """成长×估值四象限"""
    grow = b_score >= ZHConfig.QUAD_B
    cheap = d_score >= ZHConfig.QUAD_D
    if grow and cheap:
        return '主攻区'
    if grow and not cheap:
        return '成长确认'
    if not grow and cheap:
        return '深度价值'
    return '回避区'


def expectation_gap(r) -> float:
    """预期差 0~100：惊喜分 + 价格空间分 + 公告前定价修正"""
    dt = r.get('dt_netprofit_yoy')
    npg = r.get('netprofit_yoy')
    g = dt if dt is not None else npg
    if g is None or r.get('ann_close') is None:
        return None
    surprise = _tier(g, ZHConfig.GAP_SURPRISE, 10.0)
    post = r.get('post_ret', 0) / 100.0
    pre = r.get('pre_ret20', 0) / 100.0
    space = ZHConfig.GAP_SPACE_K * max(0.0, 1.0 - post / ZHConfig.GAP_SPACE_CAP)
    adj = 0.0
    for thre, p in ZHConfig.GAP_PRE:
        if pre > thre:
            adj = p
            break
    if post < 0:
        adj += ZHConfig.GAP_POST_RETREAT
    return round(max(0.0, min(100.0, surprise + space + adj)), 1)


# ============================================================
# 交易档案
# ============================================================
def trade_profile(r) -> dict:
    """买点 / 止损 / 目标 / 逻辑 / 风险"""
    cur = r.get('cur')
    ma20 = r.get('ma20')
    ma60 = r.get('ma60')
    h120 = r.get('pct_from_120d_high')
    pbm = r.get('pct_below_ma20')

    buy = '—'
    if cur and ma20:
        if pbm is not None and -3.0 <= pbm <= 3.0:
            buy = f'现价附近分批，回踩MA20({ma20:.2f})加仓'
        elif pbm is not None and pbm > 3.0:
            buy = f'强势站上MA20，回踩MA20({ma20:.2f})不破低吸'
        else:
            buy = f'待企稳：放量阳线收复MA20({ma20:.2f})再介入'
    stop = '—'
    if cur and ma60 and ma60 > 0:
        stop_pct = cur * ZHConfig.STOP_LOSS_PCT
        stop = round(max(stop_pct, ma60) if ma60 <= cur else stop_pct, 2)
    target = '—'
    if cur and h120 is not None and h120 != 999.0:
        target = round(cur / (1 - h120 / 100), 2) if h120 > 0 else round(cur * 1.2, 2)

    # 逻辑
    dt = r.get('dt_netprofit_yoy')
    npg = r.get('netprofit_yoy')
    g = dt if dt is not None else npg
    pe = r.get('pe_ttm')
    logic = (f'{r.get("发动机", "—")}：中报净利增速{g:+.0f}%'
             f'{"/扣非" + f"{dt:+.0f}%" if dt is not None else ""}，'
             f'营收{_fmt(r.get("tr_yoy"), "%", 0)}，PE={_fmt(pe, nd=0)}')
    # 风险
    risks = []
    if r.get('eq_pen') is not None and r['eq_pen'] < -4:
        risks.append('盈利质量存疑(扣非/现金流背离)')
    if h120 is not None and h120 < 8:
        risks.append(f'距120日高仅{h120:.0f}%，上行空间有限')
    if r.get('post_ret', 0) > 25:
        risks.append(f'公告后已涨{r["post_ret"]:.0f}%，预期差部分兑现')
    if r.get('days_above_ma20', 0) == 0:
        risks.append('当前位于MA20下方，趋势未转强')
    risk_txt = '；'.join(risks) if risks else '无明显即时风险'

    return {'买点': buy, '止损': stop, '目标': target, '逻辑': logic, '风险': risk_txt}


# ============================================================
# 主流程
# ============================================================
def run(trade_date: str, with_forecast: bool = False, top: int = 30):
    t0 = time.time()
    print('━' * 70)
    print('  中报猎手 V2.0 — ER20 六模块翻倍潜力扫描')
    print(f'  数据日期: {trade_date}')
    print('━' * 70)

    # ── Phase 1: 数据层 ──
    print('\n[Phase 1] 加载中报缓存 + 构建市值池...')
    h1 = load_h1()
    print(f'  H1 已披露: {len(h1)} 只')
    pool = load_pool(trade_date, h1)
    print(f'  市值池(20~300亿, 非ST/北交所, 有中报): {len(pool)} 只')

    # ── Phase 2: 硬过滤 ──
    print('\n[Phase 2] 硬过滤...')
    g_col = pool['dt_netprofit_yoy'].fillna(pool['netprofit_yoy'])
    passed = pool[
        (g_col >= ZHConfig.GROWTH_MIN)
        & (pool['归母净利(亿)'] >= ZHConfig.NI_MIN)
    ].copy()
    passed['增速基准'] = g_col[passed.index]
    print(f'  增速≥{ZHConfig.GROWTH_MIN:.0f}% 且 净利≥{ZHConfig.NI_MIN:.2f}亿: {len(passed)} 只')

    if len(passed) == 0:
        print('  [错误] 无通过硬过滤的股票')
        return

    # ── Phase 3: 上下文数据（主题/行业/机构） ──
    print('\n[Phase 3] 主题热度 / 行业景气 / 机构预测...')
    stock2theme, theme_heat, theme_life = load_theme_ctx(trade_date)
    ind_pros = build_industry_prosperity(h1)
    stocks = th.get_stock_list()
    ind_map = dict(zip(stocks['ts_code'], stocks['industry']))
    passed['theme'] = passed['ts_code'].map(stock2theme)
    passed['industry'] = passed['ts_code'].map(ind_map)
    rc = {}
    if with_forecast:
        print('  [机构预测] 读取 report_rc 缓存...')
        rc = load_report_rc(passed, trade_date)
        print(f'  机构预测覆盖: {len(rc)} 只')
    print(f'  主题覆盖: {passed["theme"].notna().sum()}/{len(passed)}  行业覆盖: {passed["industry"].notna().sum()}/{len(passed)}')

    # ── Phase 4: 技术面 + 预期差（单次日线拉取） ──
    print(f'\n[Phase 4] 技术面 + 预期差({len(passed)}只)...')
    tech = {}
    for i, code in enumerate(passed['ts_code']):
        ann = None
        row = passed[passed['ts_code'] == code].iloc[0]
        if pd.notna(row.get('ann_date')):
            ann = str(int(row['ann_date']))
        tech[code] = _tech_and_gap(code, ann)
        if (i + 1) % 50 == 0 or i == 0:
            print(f'  [{i+1}/{len(passed)}] {row["name"]}({code})')

    for k, v in tech.items():
        for fk, fv in v.items():
            passed.loc[passed['ts_code'] == k, fk] = fv
    passed['rightside'] = pd.to_numeric(passed.get('rightside'), errors='coerce').fillna(0)
    passed['pct_from_120d_high'] = pd.to_numeric(passed.get('pct_from_120d_high'), errors='coerce').fillna(999.0)

    # ── Phase 5: ER20 六模块评分 ──
    print('\n[Phase 5] ER20 六模块评分...')
    rows = []
    for _, r in passed.iterrows():
        d = r.to_dict()
        b_s = score_b(d)
        d_s = score_d(d)
        f_s = score_f(d, theme_heat, theme_life, ind_pros, rc)
        eq = eq_penalty(d)
        d['A业绩'] = score_a(d)
        d['B持续'] = b_s
        d['C弹性'] = score_c(d)
        d['D估值'] = d_s
        d['E技术'] = score_e(d)
        d['F加成'] = f_s
        d['EQ惩罚'] = eq
        d['ER20'] = er20_total(d, f_s, eq)
        d['发动机'] = classify_driver(d)
        d['象限'] = quadrant(d, b_s, d_s)
        d['预期差'] = expectation_gap(d)
        d['tech'] = tech[d['ts_code']]
        rows.append(d)
    df = pd.DataFrame(rows)
    df = df.sort_values('ER20', ascending=False).reset_index(drop=True)
    df['排名'] = df.index + 1
    print(f'  评分完成: {len(df)} 只, NaN in ER20: {df["ER20"].isna().sum()}')

    # ── Phase 6: 交易档案 ──
    print('\n[Phase 6] 交易档案...')
    profiles = {}
    for _, r in df.iterrows():
        profiles[r['ts_code']] = trade_profile(r.to_dict())

    # ── Phase 7: 输出 ──
    write_csv(df, trade_date)
    write_md(df, profiles, trade_date, top)
    print(f'\n  完成, 耗时 {time.time()-t0:.0f}s')
    print(f'  输出: {os.path.join(REPORT_DIR, f"zhongbao_v2_report_{trade_date}.md")}')


def write_csv(df: pd.DataFrame, trade_date: str):
    out_cols = ['排名', 'ts_code', 'name', 'ER20', 'A业绩', 'B持续', 'C弹性', 'D估值',
                'E技术', 'F加成', 'EQ惩罚', '发动机', '象限', '预期差',
                'dt_netprofit_yoy', 'netprofit_yoy', 'tr_yoy', '归母净利(亿)',
                'grossprofit_margin', '市值(亿)', 'pe_ttm', 'q1_profit_yoy',
                'q_sales_yoy', 'pct_from_120d_high', 'pct_below_ma20',
                'days_above_ma20', 'rightside', 'pre_ret20', 'post_ret',
                'theme', 'industry']
    keep = [c for c in out_cols if c in df.columns]
    df[keep].to_csv(os.path.join(REPORT_DIR, f'zhongbao_v2_{trade_date}.csv'),
                    index=False, encoding='utf-8-sig')
    print(f'  完整CSV已保存: report_daily/zhongbao_v2_{trade_date}.csv')


def _fmt(x, unit='', nd=0):
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return 'N/A'
    return f'{x:.{nd}f}{unit}'


def write_md(df: pd.DataFrame, profiles: dict, trade_date: str, top: int):
    L = []
    A = L.append
    A(f'# 中报猎手 V2.0 — ER20 翻倍潜力榜（{trade_date}）')
    A('')
    A('> 评分口径：A业绩40 + B持续20 + C弹性15 + D估值15 + E技术10 + F主题/行业/机构±10 + EQ风险惩罚')
    A('')
    A(f'池 {len(df)} 只（市值20~300亿 / 非ST / 非北交所 / 已披露2026中报 / 增速≥30% / 净利≥0.3亿）')
    A('')

    # ── TOP30 ──
    A(f'## 一、翻倍发动机 TOP{min(top, len(df))}')
    A('')
    A('| # | 名称 | ER20 | A/B/C/D/E | F | EQ | 发动机 | 象限 | 净利增速 | 营收 | 净利(亿) | 市值(亿) | PE | 预期差 |')
    A('|---|---|---|---|---|---|---|---|---|---|---|---|---|---|')
    for _, r in df.head(top).iterrows():
        dt = r['dt_netprofit_yoy'] if pd.notna(r['dt_netprofit_yoy']) else r['netprofit_yoy']
        A(f"| {r['排名']} | {r['name']}({r['ts_code'][:6]}) | {r['ER20']:.1f} | "
          f"{r['A业绩']:.0f}/{r['B持续']:.0f}/{r['C弹性']:.0f}/{r['D估值']:.0f}/{r['E技术']:.0f} | "
          f"{r['F加成']:+.0f} | {r['EQ惩罚']:.0f} | {r['发动机']} | {r['象限']} | "
          f"{_fmt(dt, '%', 0)} | {_fmt(r.get('tr_yoy'), '%', 0)} | {r['归母净利(亿)']:.2f} | {r['市值(亿)']:.0f} | "
          f"{_fmt(r.get('pe_ttm'), nd=0)} | {_fmt(r.get('预期差'), nd=0)} |")
    A('')

    # ── 交易档案 ──
    A(f'## 二、TOP{min(top, len(df))} 交易档案')
    A('')
    for _, r in df.head(top).iterrows():
        p = profiles[r['ts_code']]
        dt = r['dt_netprofit_yoy'] if pd.notna(r['dt_netprofit_yoy']) else r['netprofit_yoy']
        A(f"### {r['排名']}. {r['name']}({r['ts_code']}) — ER20 {r['ER20']:.1f} | {r['发动机']} | {r['象限']}")
        A('')
        A(f"- 现价 {_fmt(r.get('cur'), nd=2)} | MA20 {_fmt(r.get('ma20'), nd=2)} | MA60 {_fmt(r.get('ma60'), nd=2)} | 距120日高 {_fmt(r.get('pct_from_120d_high'), '%', 0)} | 距MA20 {_fmt(r.get('pct_below_ma20'), '%', 1)}")
        A(f"- 业绩: 净利 {_fmt(dt, '%', 0)} | 营收 {_fmt(r.get('tr_yoy'), '%', 0)} | Q1净利 {_fmt(r.get('q1_profit_yoy'), '%', 0)} | Q2营收 {_fmt(r.get('q_sales_yoy'), '%', 0)} | 毛利率 {_fmt(r.get('grossprofit_margin'), '%', 0)}")
        A(f"- 预期差: {_fmt(r.get('预期差'), nd=0)} | 公告前20日 {_fmt(r.get('pre_ret20'), '%', 0)} | 公告后 {_fmt(r.get('post_ret'), '%', 0)} | 主题 {r.get('theme') or '—'} | 行业 {r.get('industry') or '—'}")
        A(f"- 买点: {p['买点']}")
        A(f"- 止损: {_fmt(p['止损'], nd=2)} | 目标: {_fmt(p['目标'], nd=2)}")
        A(f"- 逻辑: {p['逻辑']}")
        A(f"- 风险: {p['风险']}")
        A('')

    # ── 三大榜单 ──
    A('## 三、预期差榜（惊喜大 + 未被充分定价）')
    A('')
    gap_df = df[df['预期差'].notna()].sort_values('预期差', ascending=False).head(10)
    if len(gap_df):
        A('| # | 名称 | 预期差 | ER20 | 净利增速 | 公告前20日 | 公告后 |')
        A('|---|---|---|---|---|---|---|')
        for _, r in gap_df.iterrows():
            dt = r['dt_netprofit_yoy'] if pd.notna(r['dt_netprofit_yoy']) else r['netprofit_yoy']
            A(f"| {r['排名']} | {r['name']}({r['ts_code'][:6]}) | {r['预期差']:.0f} | {r['ER20']:.1f} | {_fmt(dt, '%', 0)} | "
              f"{_fmt(r.get('pre_ret20'), '%', 0)} | {_fmt(r.get('post_ret'), '%', 0)} |")
    else:
        A('无（公告日数据缺失）')
    A('')

    A('## 四、回调上车榜（业绩强 + 回踩MA20企稳）')
    A('')
    lo, hi = ZHConfig.PULLBACK_WIN
    pb = df[(df['pct_below_ma20'] >= lo) & (df['pct_below_ma20'] <= hi)
            & (df['ER20'] >= 60)].sort_values('ER20', ascending=False).head(10)
    if len(pb):
        A('| # | 名称 | ER20 | 距MA20 | 站上MA20天数 | 右侧强度 | 净利增速 |')
        A('|---|---|---|---|---|---|---|')
        for _, r in pb.iterrows():
            dt = r['dt_netprofit_yoy'] if pd.notna(r['dt_netprofit_yoy']) else r['netprofit_yoy']
            A(f"| {r['排名']} | {r['name']}({r['ts_code'][:6]}) | {r['ER20']:.1f} | "
              f"{_fmt(r.get('pct_below_ma20'), '%', 1)} | {r.get('days_above_ma20', 0)} | "
              f"{_fmt(r.get('rightside'), nd=0)} | {_fmt(dt, '%', 0)} |")
    else:
        A('无（今日无符合回踩条件的标的）')
    A('')

    A('## 五、数据说明')
    A('')
    A('- 数据源：全部本地缓存（fin_ind_2026H1 全字段 + daily_basic + 日线），未联网')
    A('- 增速为 2026H1 vs 2025H1 同比；Q2 营收同比为单季口径(q_sales_yoy)')
    A('- 预期差：业绩惊喜与公告后价格反应的匹配度，高=未被充分定价')
    A('- 风险提示：本报告仅供研究，不构成投资建议')

    path = os.path.join(REPORT_DIR, f'zhongbao_v2_report_{trade_date}.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    print(f'  报告已保存: {path}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None, help='交易日 YYYYMMDD，默认最近交易日')
    ap.add_argument('--top', type=int, default=30, help='TOP N')
    ap.add_argument('--with-forecast', action='store_true', help='启用机构预测加成(读取 report_rc 缓存)')
    args = ap.parse_args()
    trade_date = args.date or th.get_last_trade_date()
    run(trade_date, with_forecast=args.with_forecast, top=args.top)


if __name__ == '__main__':
    main()
