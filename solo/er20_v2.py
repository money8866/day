# -*- coding: utf-8 -*-
"""
ER20 V2.0 — Earnings Event → Repricing → Entry → 20D Alpha
============================================================
中报事件驱动选股升级版。在半年报披露期寻找「真实基本面变化 × 预期差 ×
公告反应健康 × 技术位置合理 × 尚未透支」的标的，严格区分 ALPHA(值不值得持有)
与 ENTRY(今天能不能买)，只允许 CORE_BUY 成为重点候选。

ER20 V1 → V2 模块映射
─────────────────────
V1 旧模块                                   → V2 新模块
─────────────────────────────────────────────────────────────
fundamental_quality_score (硬编码档位)      → fundamental_quality()（纯数据计算，缺失返 None）
fundamental_reversal_score (30/15/10 凑分)  → calc_rqs()（RQS 七维反转质量）
pre_run_and_penalty (gap 50/75/60... 默认)  → expect_gap_score()（预期差代理模型）
                                          + overheat_penalty()（透支检测，独立）
announcement_reaction (50 基线)             → calc_ars()（T0/T+1/T+3/T+5 相对收益+量+收盘位置+高开低走）
positive_price_reaction (50 基线)           → 并入 calc_ars()
detect_pattern_a/b (tech 40/50/88 硬编码)   → technical_engine：TQS = 趋势0.25+量0.20+回踩0.20+突破0.15+动量0.10+支撑0.10
build_signal 统一加权、A/B max 混排          → classify_event → 策略专属公式 → 策略内 percentile 归一化
build_trade_plan                            → risk_engine + entry_engine（独立 ENTRY_SCORE）
market_theme_score (60/70 固定)              → theme_score()（白名单+强度，最终 ±5 封顶）
market_env_summary (仅报告文字)              → market_multiplier()（复用 bts.data.market_regime，±10% 封顶）
无 Confidence                               → data_confidence() + missing_factors 全程追踪
无事件分类                                  → classify_event()（A/B/C/D 四类）
无独立 Entry                                → entry_engine()（Location/Trigger/Volume/RR/Market）
无因子落库                                  → save_sqlite()（供 Forward 5/10/20D 回测）

设计原则
────────
1. 任何核心因子缺失：score=None + confidence 扣减，绝不偷偷用高默认分；
   也不允许 nan 进入加权平均（safe_score 统一拦截）。
2. 所有权重/阈值/风控参数集中 ER20Config，禁止 magic numbers。
3. 数据获取 100% 复用现有缓存（fina_indicator/income/daily/主题/市场），
   不新增任何下载接口，不修改旧 er20_strategy.py。
4. 无未来函数：评分只用 公告内容 + 截至扫描日行情。

用法
────
python -X utf8 er20_v2.py --date 20260820 [--backtest]
"""
import os
import sys
import glob
import json
import time
import sqlite3
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
DB_PATH = os.path.join(REPORT_DIR, 'er20_v2_scores.db')

from bts.data import load_daily, get_trade_dates, market_regime, parse_tdx_day_file
from bts.indicators import add_ma, add_rsi, ma_slope
from er20_strategy import (calc_atr14, calc_ret_pct, _calc_q2_single,
                           theme_map_stock2theme)  # 复用既有实现


# ============================================================
# ER20Config — 全部参数集中配置（禁止 magic numbers）
# ============================================================
class ER20Config:
    # ── 事件窗口 ──
    ANN_WINDOW_MIN = 2        # 公告后最早可确认
    ANN_WINDOW_MAX = 10       # 公告后观察窗口(交易日)
    # ── 策略专属权重（Alpha 层，0~100 加权） ──
    W_A = {'fq': 0.25, 'gap': 0.20, 'ars': 0.15, 'pqs': 0.20, 'trend': 0.10, 'risk': 0.10}
    W_B = {'rqs': 0.25, 'fq': 0.15, 'gap': 0.15, 'ars': 0.15, 'tqs': 0.20, 'risk': 0.10}
    # ── RQS 反转质量权重 ──
    W_RQS = {'profit': 0.25, 'revenue': 0.20, 'margin': 0.15, 'cash': 0.15,
             'balance': 0.10, 'persist': 0.10, 'cycle': 0.05}
    # ── TQS 技术质量权重 ──
    W_TQS = {'trend': 0.25, 'volume': 0.20, 'pullback': 0.20, 'breakout': 0.15,
             'momentum': 0.10, 'support': 0.10}
    # ── ENTRY_SCORE 权重 ──
    W_ENTRY = {'location': 0.30, 'trigger': 0.25, 'volume': 0.20, 'rr': 0.15, 'market': 0.10}
    # ── 归一化后调整 ──
    THEME_ADJ_MAX = 5.0       # 主题最多 ±5 分
    # ── 归一化乘数（作用于 0~100 归一化后的 ALPHA） ──
    CONF_MULT = 1.00          # Confidence 直接乘（conf/100）
    RISK_PEN = 0.40           # Risk 每 100 分最多扣 40%
    # ── 等级门槛 ──
    GATE = {
        'core_alpha': 75.0, 'core_entry': 80.0, 'core_risk': 40.0, 'core_conf': 80.0,
        'test_alpha': 68.0, 'test_entry': 70.0,
        'watch_alpha': 60.0,
        'fq_floor_core': 35.0,   # 正常股 Fundamental < 35 不能进 CORE_BUY
        'fq_floor_reject': 25.0, # < 25 默认 REJECT（B_REVERSAL 除外，改看 RQS）
        'rqs_high_grade': 70.0,  # B_REVERSAL 需 RQS>=70 才允许高等级
        'conf_watch': 50.0,      # DataConfidence < 50 只能 WATCH
    }
    # ── 透支检测阈值 ──
    OVERHEAT = {'r20_hi': 0.30, 'r20_mid': 0.20, 'r60_hi': 0.50,
                'ann_ret_hi': 0.09, 'vr_hi': 3.0, 'rsi_hi': 80,
                'pen_max': 40.0}
    # ── 公告反应理想区间 ──
    REACT = {'ret_min': -0.02, 'ret_max': 0.08, 'vr_min': 1.1, 'vr_max': 2.8,
             'close_pos_min': 0.60}
    # ── 市场乘数（规格 6 档；由 _market_regime_6 判定） ──
    MARKET_MULT = {'strong': 1.15, 'bull': 1.05, 'neutral': 1.00,
                   'recovery': 0.95, 'weak': 0.85, 'bear': 0.70}
    # ── 风控 ──
    RISK = {'atr_mult': 1.5, 'stop_min': 0.03, 'stop_max': 0.08,
            'max_hold': 20, 'event_pos_cap': 0.03}
    # ── 主题白名单（复用 EGPT 同款） ──
    THEME_WHITELIST = {'智能驾驶', '信创', '新能源车', '消费电子', '半导体', '创新药',
                       '机器人', '游戏', '建筑装饰', '传媒', '能源金属', '商业航天'}
    # ── 事件类关键词（C_EVENT_SPEC 启发式） ──
    EVENT_KEYWORDS = ('重组', '重整', '摘帽', '国改', '混改', '借壳', '并购', '资产置换')
    # ── 评分缺失处理 ──
    MISSING_FALLBACK = {'gap': None, 'ars': None, 'trend': None, 'pqs': None,
                        'breakout': None, 'momentum': None, 'support': None}


# ============================================================
# 基础工具
# ============================================================

def _valid(v):
    """有效数值判定（排除 nan/None/inf）"""
    if v is None:
        return False
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return np.isfinite(f)


def safe_score(value, name, missing, fallback=None):
    """
    核心评分统一入口：valid 直接返回；可重算走 fallback；
    均不可用则记录缺失并返回 None（绝不偷偷给默认分）。
    """
    if _valid(value):
        return round(float(value), 1)
    if fallback is not None and _valid(fallback):
        return round(float(fallback), 1)
    if name not in missing:
        missing.append(name)
    return None


def _pct(a, b):
    """相对涨幅 %，分母非正或无效返回 None"""
    if not (_valid(a) and _valid(b)) or float(b) <= 0:
        return None
    return round((float(a) / float(b) - 1.0) * 100.0, 2)


def calc_macd(df, fast=12, slow=26, signal=9):
    """MACD：返回 (dif, dea, hist) 最新值（标准 EMA 计算）"""
    c = df['close'].astype(float)
    if len(c) < slow + signal + 2:
        return np.nan, np.nan, np.nan
    ema_f = c.ewm(span=fast, adjust=False).mean()
    ema_s = c.ewm(span=slow, adjust=False).mean()
    dif = ema_f - ema_s
    dea = dif.ewm(span=signal, adjust=False).mean()
    return float(dif.iloc[-1]), float(dea.iloc[-1]), float((dif - dea).iloc[-1])


_BENCH_CACHE = {}


def load_bench(scan_date):
    """上证指数日线（本地 TDX，无网络），供相对收益"""
    key = str(scan_date)
    if key in _BENCH_CACHE:
        return _BENCH_CACHE[key]
    idx = None
    for p in (os.path.join(TDX_PATH, 'sh', 'lday', 'sh000001.day'),
              os.path.join(TDX_PATH, 'vipdoc', 'sh', 'lday', 'sh000001.day')):
        idx = parse_tdx_day_file(p)
        if idx is not None:
            break
    if idx is None or len(idx) == 0:
        _BENCH_CACHE[key] = None
        return None
    idx = idx[idx['trade_date'] <= str(scan_date)].reset_index(drop=True)
    idx['pct_chg'] = idx['close'].astype(float).pct_change() * 100.0
    idx['date'] = idx['trade_date'].astype(str)
    _BENCH_CACHE[key] = idx
    return idx


# ============================================================
# 数据加载（复用现有缓存，保留全字段）
# ============================================================

_FULL_COLS = None


def _s4_columns():
    global _FULL_COLS
    if _FULL_COLS is None:
        fp = os.path.join(CACHE_DIR, 'fin_ind_2026H1_full.parquet')
        if os.path.exists(fp):
            try:
                _FULL_COLS = pd.read_parquet(fp, columns=None).columns.tolist()
            except Exception:
                _FULL_COLS = []
        else:
            _FULL_COLS = []
    return _FULL_COLS


def load_pool_v2(period='20260630', scan_date=''):
    """
    全市场中报池（多源合并，与 V1 相同缓存，但保留全字段供 RQS/ARS 使用）。
    返回列：
      ts_code/name/ann_date/src + 利润/收入/盈利/现金流/资产/单季 全部指标
      + q1_profit_yoy/q2_profit_yoy/q2_proxy
    """
    year = period[:4]
    q1_period = f'{year}0331'
    # ── 主源：全量中报回填（S4，886+只，全字段） ──
    pool = pd.DataFrame()
    s4_path = os.path.join(CACHE_DIR, 'fin_ind_2026H1_full.parquet')
    if os.path.exists(s4_path):
        try:
            s4 = pd.read_parquet(s4_path)
            s4 = s4[s4['end_date'] == period].copy()
            if scan_date:
                s4 = s4[s4['ann_date'].astype(str) <= str(scan_date)]
            s4 = s4.drop_duplicates('ts_code', keep='last').copy()
            s4['src'] = 'fin_ind_full'
            pool = s4
        except Exception as e:
            print('S4 读取失败:', e)
    # ── S1 treasure 补字段（若 S4 缺失某列） ──
    files = sorted(glob.glob(os.path.join(CACHE_DIR, 'treasure_fin_ind_*.parquet')))
    rows = []
    for fp in files:
        try:
            df = pd.read_parquet(fp)
        except Exception:
            continue
        h1 = df[df['end_date'] == period]
        if h1.empty:
            continue
        rec = h1.sort_values('ann_date').iloc[-1].to_dict()
        if scan_date and str(rec.get('ann_date', '')) > str(scan_date):
            continue
        q1 = df[df['end_date'] == q1_period]
        rec['q1_profit_yoy'] = q1.sort_values('ann_date')['netprofit_yoy'].iloc[-1] if not q1.empty else np.nan
        rec['src'] = 'fina_indicator'
        rows.append(rec)
    if rows:
        s1 = pd.DataFrame(rows)
        if pool.empty:
            pool = s1
        else:
            add = s1[~s1['ts_code'].isin(pool['ts_code'])]
            if not add.empty:
                pool = pd.concat([pool, add], ignore_index=True)
    # ── S2/S3 补充（与 V1 相同的兜底来源） ──
    hunts = sorted(glob.glob(os.path.join(REPORT_DIR, 'zhongbao_hunt_*.csv')))
    if hunts:
        try:
            h = pd.read_csv(hunts[-1], dtype={'ts_code': str})
            h = h[~h['ts_code'].isin(pool['ts_code'])][['ts_code', 'netprofit_yoy', 'dt_netprofit_yoy', 'tr_yoy']].copy()
            if not h.empty:
                h['src'] = 'zhongbao_hunt'
                for c in pool.columns:
                    if c not in h.columns:
                        h[c] = np.nan
                pool = pd.concat([pool, h[[c for c in pool.columns if c in h.columns]]], ignore_index=True)
        except Exception:
            pass
    if pool.empty:
        return pool
    # ── Q1 全量缓存合并 q1_profit_yoy ──
    q1_map = {}
    q1_full = os.path.join(CACHE_DIR, 'fin_ind_2026Q1_full.parquet')
    if os.path.exists(q1_full):
        try:
            q1f = pd.read_parquet(q1_full, columns=['ts_code', 'end_date', 'netprofit_yoy', 'q_roe', 'q_sales_yoy'])
            q1f = q1f[q1f['end_date'] == q1_period].drop_duplicates('ts_code', keep='last')
            q1_map = {r['ts_code']: r for _, r in q1f.iterrows()}
        except Exception:
            pass
    # ── Q2 拆分 + 补 Q1 ──
    pool['q2_proxy'] = False
    q2s, q1s, prox = [], [], []
    for _, r in pool.iterrows():
        q2, proxy = _calc_q2_single(r['ts_code'], period, r)
        q2s.append(q2)
        prox.append(proxy)
        v = r.get('q1_profit_yoy', np.nan)
        if not _valid(v) and r['ts_code'] in q1_map:
            v = q1_map[r['ts_code']].get('netprofit_yoy', np.nan)
        q1s.append(v if _valid(v) else np.nan)
    pool['q2_profit_yoy'] = q2s
    pool['q1_profit_yoy'] = q1s
    pool['q2_proxy'] = prox
    # ── 补名称 ──
    if 'name' not in pool.columns or pool['name'].isna().all():
        from bts.data import load_stock_basic
        basic = load_stock_basic()
        if basic is not None and len(basic):
            nm = dict(zip(basic['ts_code'], basic['name']))
            pool['name'] = pool['ts_code'].map(nm)
    return pool


def load_daily_for(code, scan_date):
    """复用 load_daily + add_ma + add_rsi；pct_chg 补零"""
    try:
        daily = load_daily(code, scan_date, lookback_bars=300)
    except Exception:
        return None
    if daily is None or len(daily) < 120:
        return None
    daily = daily.reset_index(drop=True)
    daily = add_ma(daily)
    if 'rsi' not in daily.columns:
        daily = add_rsi(daily)
    if 'pct_chg' not in daily.columns:
        daily['pct_chg'] = daily['close'].pct_change() * 100.0
    daily['pct_chg'] = daily['pct_chg'].fillna(0.0)
    return daily


# ============================================================
# 第一层：事件分类（A/B/C/D）
# ============================================================

def classify_event(r, daily, ann_idx, cur_idx):
    """
    自动识别事件类型。返回 (strategy, reason)。
      A_CONFIRMATION: 持续成长验证（质量好 + 中报继续验证）
      B_REVERSAL:     真实拐点（利润大幅改善/加速/转正 + 扣非同步）
      C_EVENT_SPEC:   重组/摘帽等事件驱动（名称启发式，默认小仓位隔离）
      D_FALSE_SIGNAL: 假信号（一次性收益/营收降利润增/现金流恶化/低基数背离/公告后连续暴涨）
    """
    nm = str(r.get('name', ''))
    # ── C: 事件类（名称含关键词） ──
    if any(k in nm for k in ER20Config.EVENT_KEYWORDS):
        # 仅当财务已验证改善才放行 A/B，否则隔离为 C_EVENT_SPEC
        ni, dt = r.get('netprofit_yoy', np.nan), r.get('dt_netprofit_yoy', np.nan)
        if _valid(ni) and _valid(dt) and ni > 0 and dt > 0:
            return ('A_CONFIRMATION' if _valid(r.get('tr_yoy', np.nan)) and r.get('tr_yoy', 0) > 0
                    else 'B_REVERSAL'), '事件股但财务已验证改善'
        return 'C_EVENT_SPEC', '事件驱动(名称关键词)'
    ni = r.get('netprofit_yoy', np.nan)
    dt = r.get('dt_netprofit_yoy', np.nan)
    tr = r.get('tr_yoy', np.nan)
    q1 = r.get('q1_profit_yoy', np.nan)
    q2 = r.get('q2_profit_yoy', np.nan)
    accel = (q2 - q1) if (_valid(q2) and _valid(q1)) else np.nan
    ocf = r.get('ocf_yoy', np.nan)
    # ── D1: 一次性收益主导（扣非与归母严重背离） ──
    if _valid(ni) and _valid(dt) and ni > 30 and dt < 0:
        return 'D_FALSE_SIGNAL', '扣非负但归母大增=一次性收益'
    # ── D2: 营收下降但利润异常增长 ──
    if _valid(tr) and _valid(ni) and tr < -10 and ni > 50:
        return 'D_FALSE_SIGNAL', '营收降但利润暴增=质量存疑'
    # ── D3: 低基数虚高 + 增长已见顶（Q2 加速度为负且 Q1 增速>200） ──
    if _valid(ni) and ni > 300 and _valid(accel) and accel < -100:
        return 'D_FALSE_SIGNAL', '低基数虚高且增长见顶'
    # ── D4: 现金流严重恶化且利润为正 ──
    if _valid(ni) and ni > 0 and _valid(ocf) and ocf < -80:
        return 'D_FALSE_SIGNAL', '利润正但现金流严重恶化'
    # ── D5: 公告后连续暴涨（透支） ──
    if cur_idx > ann_idx + 1:
        win = daily.iloc[ann_idx:cur_idx + 1]
        if len(win) >= 3:
            cum = float(win['close'].iloc[-1]) / float(win['close'].iloc[0]) - 1.0
            if cum > 0.30:
                return 'D_FALSE_SIGNAL', '公告后连续暴涨已透支'
    # ── A vs B ──
    # 持续成长确认度（A 特征）：营收+净利+扣非共振、ROE 正常
    conf_cnt = 0
    if _valid(tr) and tr > 0: conf_cnt += 1
    if _valid(ni) and ni > 0: conf_cnt += 1
    if _valid(dt) and dt > 0: conf_cnt += 1
    roe = r.get('roe', np.nan)
    if _valid(roe) and roe > 3: conf_cnt += 1
    # 反转拐点度（B 特征）：加速显著 / Q1负Q2正 / 扣非大幅改善
    rev_cnt = 0
    if _valid(accel) and accel >= 15: rev_cnt += 1
    if _valid(q1) and _valid(q2) and q1 <= 0 and q2 > 0: rev_cnt += 1
    if _valid(dt) and dt >= 30: rev_cnt += 1
    if _valid(ni) and ni >= 50: rev_cnt += 1
    if rev_cnt >= 2 and rev_cnt > conf_cnt:
        return 'B_REVERSAL', f'真实拐点(加速{accel:.0f}pct/扣非{dt if _valid(dt) else 0:.0f}%)'
    if conf_cnt >= 2:
        return 'A_CONFIRMATION', '持续成长验证'
    # 兜底：有数据但两不靠 → 看加速度
    if _valid(accel) and accel > 0:
        return 'B_REVERSAL', '边际改善'
    if _valid(ni) and ni > 0:
        return 'A_CONFIRMATION', '净利为正(数据有限)'
    return 'D_FALSE_SIGNAL', '基本面证据不足'


# ============================================================
# 基本面引擎
# ============================================================

def fundamental_quality(r, missing):
    """
    A 类基本面质量 0~100，纯数据计算（无默认分）。
    维度：利润加速/共振/扣非质量/现金流/盈利水平。
    核心字段缺失 → 该维度不计分并入 missing。
    """
    s, core = 0.0, 0
    q2 = r.get('q2_profit_yoy', np.nan)
    q1 = r.get('q1_profit_yoy', np.nan)
    accel = (q2 - q1) if (_valid(q2) and _valid(q1)) else np.nan
    if _valid(accel):
        core += 1
        if accel >= 30: s += 25
        elif accel >= 15: s += 20
        elif accel > 0: s += 14
        elif accel >= -5: s += 6
        else: s += 0
    elif _valid(q2):
        core += 1
        s += 10 if q2 > 0 else 2
    else:
        missing.append('q2_accel')
    tr, ni = r.get('tr_yoy', np.nan), r.get('netprofit_yoy', np.nan)
    if _valid(tr) and _valid(ni):
        core += 1
        if tr > 0 and ni > 0:
            s += 20 + (8 if ni > tr else 0)
        elif ni > 0:
            s += 8
        elif tr > 0:
            s += 4
    elif _valid(ni):
        core += 1
        s += 10 if ni > 0 else 0
    else:
        missing.append('rev_profit')
    dt, ni2 = r.get('dt_netprofit_yoy', np.nan), r.get('netprofit_yoy', np.nan)
    if _valid(dt) and _valid(ni2) and ni2 > 0:
        core += 1
        if dt >= ni2 * 0.8: s += 15
        elif dt > 0: s += 10
        elif dt > -30: s += 3
    elif _valid(dt) and dt > 0:
        core += 1
        s += 12
    else:
        missing.append('dt_profit')
    ocf = r.get('ocf_yoy', np.nan)
    if _valid(ocf):
        core += 1
        if ocf > 0: s += 10
        elif ocf < -50: s -= 10
        elif ocf < -20: s -= 5
    else:
        missing.append('ocf')
    roe = r.get('roe', np.nan)
    if _valid(roe):
        core += 1
        s += min(10, max(0, roe)) if roe > 0 else 0
    else:
        missing.append('roe')
    if core == 0:
        return None
    return round(min(100, max(0, s)), 1)


def calc_rqs(r, missing):
    """
    Reversal Quality Score 反转质量 0~100（B 类核心）。
    RQS = 0.25利润反转 + 0.20收入加速 + 0.15毛利率 + 0.15现金流
        + 0.10资产负债 + 0.10多季连续 + 0.05行业周期
    """
    w = ER20Config.W_RQS
    parts = {}
    # ── 1. 利润反转（亏损→盈利最优先） ──
    q1 = r.get('q1_profit_yoy', np.nan)
    q2 = r.get('q2_profit_yoy', np.nan)
    dt = r.get('dt_netprofit_yoy', np.nan)
    ni = r.get('netprofit_yoy', np.nan)
    accel = (q2 - q1) if (_valid(q2) and _valid(q1)) else np.nan
    p_rev = None
    if _valid(q1) and _valid(q2) and q1 <= 0 and q2 > 0:
        p_rev = 80.0
        if _valid(dt) and dt > 0:
            p_rev += 10
        if _valid(q2) and q2 >= 100:
            p_rev += 5
        p_rev = min(100, p_rev)
    elif _valid(accel):
        if accel >= 80: p_rev = 88
        elif accel >= 50: p_rev = 78
        elif accel >= 30: p_rev = 66
        elif accel >= 15: p_rev = 52
        elif accel > 0: p_rev = 40
        elif accel >= -10: p_rev = 25
        else: p_rev = 10
    elif _valid(ni) and ni > 0:
        p_rev = 55
    if p_rev is not None and _valid(dt) and _valid(dt) and dt < -20:
        p_rev = max(0, p_rev - 15)
    parts['profit'] = p_rev
    # ── 2. 收入加速（单季 vs 累计） ──
    tr = r.get('tr_yoy', np.nan)
    qs = r.get('q_sales_yoy', np.nan)
    r_acc = None
    if _valid(qs) and _valid(tr):
        if qs > tr + 20: r_acc = 88          # 爆发加速
        elif qs > tr + 5: r_acc = 70         # 加速
        elif abs(qs - tr) <= 5: r_acc = 55   # 稳定
        elif qs < tr - 20: r_acc = 25        # 明显减速
        else: r_acc = 40                     # 温和减速
    elif _valid(tr):
        if tr > 30: r_acc = 70
        elif tr > 10: r_acc = 55
        elif tr > 0: r_acc = 40
        else: r_acc = 15
    parts['revenue'] = r_acc
    # ── 3. 毛利率改善（水平 + 单季盈利动量代理） ──
    gm = r.get('grossprofit_margin', np.nan)
    qroe = r.get('q_roe', np.nan)
    roe = r.get('roe', np.nan)
    m_score = None
    gm_score = 40.0 if _valid(gm) else None
    if _valid(gm):
        if gm >= 40: gm_score = 75
        elif gm >= 25: gm_score = 62
        elif gm >= 15: gm_score = 50
        elif gm >= 5: gm_score = 35
        else: gm_score = 20
    if _valid(qroe) and _valid(roe):
        # 单季 ROE 明显高于累计 → 盈利质量在改善
        qroe_s = 70 if qroe > max(roe, 2) else (50 if qroe > 0 else 25)
        m_score = (gm_score if gm_score is not None else 50) * 0.6 + qroe_s * 0.4
    elif gm_score is not None:
        m_score = gm_score
    parts['margin'] = m_score
    # ── 4. 现金流改善 ──
    ocf = r.get('ocf_yoy', np.nan)
    qocf = r.get('q_ocf_to_sales', np.nan)
    c_score = None
    if _valid(ocf):
        if ocf > 30: c_score = 80
        elif ocf > 0: c_score = 62
        elif ocf > -30: c_score = 40
        elif ocf > -60: c_score = 22
        else: c_score = 8
    if c_score is not None and _valid(ni) and _valid(ocf) and ni > 30 and ocf < 0:
        c_score = max(0, c_score - 20)  # 利润好现金流差 → 质量打折
    parts['cash'] = c_score if c_score is not None else (55 if _valid(qocf) and qocf > 0 else None)
    # ── 5. 资产负债改善 ──
    da = r.get('debt_to_assets', np.nan)
    cr = r.get('current_ratio', np.nan)
    b_score = 50.0
    if _valid(da):
        if da < 40: b_score = 70
        elif da < 60: b_score = 55
        else: b_score = 35
    if _valid(cr) and cr >= 1.5:
        b_score = min(85, b_score + 10)
    elif _valid(cr) and cr < 1.0:
        b_score = max(10, b_score - 15)
    parts['balance'] = b_score if (_valid(da) or _valid(cr)) else None
    # ── 6. 多季连续（Q1/Q2 双正或双改善 → 连续） ──
    persist = None
    if _valid(q1) and _valid(q2):
        if q1 > 0 and q2 > 0:
            persist = 75 if q2 >= q1 else 60
        elif q1 <= 0 < q2:
            persist = 68
        elif q1 > 0 >= q2:
            persist = 25
        else:
            persist = 30
    elif _valid(q2):
        persist = 50
    parts['persist'] = persist
    # ── 7. 行业周期（主题热度代理，中性默认不给分也不惩罚） ──
    parts['cycle'] = 50.0
    # ── 加权汇总：缺失维度按权重比例折算，缺>=2 个核心维度则 None ──
    wsum, ssum = 0.0, 0.0
    for k, wgt in w.items():
        v = parts.get(k)
        if _valid(v):
            wsum += wgt
            ssum += wgt * float(v)
        else:
            missing.append(f'rqs_{k}')
    if wsum == 0 or wsum < 0.75:   # 缺 >25% 权重维度 → 不可靠
        missing.append('rqs_core')
        return None
    return round(ssum / wsum, 1)


# ============================================================
# 预期差引擎（公告前透支 → 预期差衰减）
# ============================================================

def expect_gap_score(r, daily, ann_idx):
    """
    预期差代理模型 0~100：
      基准 = 增长水平与加速度（实际 vs 历史趋势）
      修正 = 公告前 20/60 日涨幅（提前涨越多 → 预期差越低）
    全部基于实际数据；数据不足返 None（进 missing）。
    """
    pre = daily.iloc[:ann_idx]
    if len(pre) < 65:
        return None, 'history<65'
    r20 = calc_ret_pct(pre, 20)
    r60 = calc_ret_pct(pre, 60)
    q2 = r.get('q2_profit_yoy', np.nan)
    q1 = r.get('q1_profit_yoy', np.nan)
    ni = r.get('netprofit_yoy', np.nan)
    accel = (q2 - q1) if (_valid(q2) and _valid(q1)) else np.nan
    # 实际增长惊喜基准
    base = 50.0
    if _valid(accel):
        if accel >= 30: base = 75
        elif accel >= 15: base = 66
        elif accel > 0: base = 56
        elif accel >= -10: base = 42
        else: base = 28
    elif _valid(ni):
        base = 60 if ni > 30 else (50 if ni > 0 else 30)
    # 公告前涨幅修正
    pen = 0.0
    if _valid(r20):
        if r20 > 30: pen = 35
        elif r20 > 20: pen = 22
        elif r20 > 10: pen = 10
        elif r20 < -10: pen = -5   # 超跌 → 预期差反而抬升
    if _valid(r60) and r60 > 50:
        pen += 12
    score = base - pen
    if score < 0:
        return 0.0, 'fully_priced'
    return round(min(100, score), 1), None


def overheat_penalty(r, daily, ann_idx, cur_idx):
    """
    透支检测 0~40（惩罚分，越高越透支）。
    R20/R60 涨幅 + 距历史高 + 公告日涨幅量能 + RSI。
    """
    cfg = ER20Config.OVERHEAT
    pen = 0.0
    pre = daily.iloc[:ann_idx]
    if len(pre) >= 65:
        r20 = calc_ret_pct(pre, 20)
        r60 = calc_ret_pct(pre, 60)
        if _valid(r20):
            if r20 > cfg['r20_hi'] * 100: pen += 20
            elif r20 > cfg['r20_mid'] * 100: pen += 12
        if _valid(r60) and r60 > cfg['r60_hi'] * 100: pen += 10
    # 距历史高点（近 120 日）
    if cur_idx >= 60:
        hi = float(daily['close'].iloc[max(0, cur_idx - 120):cur_idx + 1].max())
        c = float(daily.iloc[cur_idx]['close'])
        if _valid(hi) and hi > 0 and c >= hi * 0.97:
            pen += 5
    # 公告日涨幅 + 巨量
    if ann_idx < len(daily) and ann_idx >= 1:
        d0, d1 = daily.iloc[ann_idx - 1], daily.iloc[ann_idx]
        ret = float(d1['close']) / float(d0['close']) - 1.0
        ma20v = float(daily['vol'].iloc[max(0, ann_idx - 20):ann_idx].mean())
        vr = float(d1['vol']) / ma20v if ma20v > 0 else 0.0
        if ret > cfg['ann_ret_hi'] and vr > cfg['vr_hi']:
            pen += 8
    # RSI 超买
    if 'rsi' in daily.columns and _valid(daily.iloc[cur_idx].get('rsi')):
        if float(daily.iloc[cur_idx]['rsi']) > cfg['rsi_hi']:
            pen += 5
    return round(min(cfg['pen_max'], pen), 1)


# ============================================================
# 公告反应引擎（ARS）
# ============================================================

def calc_ars(daily, ann_idx, cur_idx, bench=None):
    """
    Announcement Reaction Score 0~100（无默认分，数据不足返 None）。
    观察 T0/T+1/T+3/T+5：
      相对基准收益 / 量能扩张 / 收盘位置 / 高开低走·冲高回落 / sell_the_news
    """
    if ann_idx < 1 or ann_idx >= len(daily):
        return None, ''
    if ann_idx < 20:
        return None, 'window<20'
    d0 = daily.iloc[ann_idx - 1]
    d1 = daily.iloc[ann_idx]
    ma20v = float(daily['vol'].iloc[ann_idx - 20:ann_idx].mean())
    if not ma20v:
        return None, 'no_vol'
    ret = float(d1['close']) / float(d0['close']) - 1.0
    vr = float(d1['vol']) / ma20v
    rng = float(d1['high']) - float(d1['low'])
    close_pos = (float(d1['close']) - float(d1['low'])) / rng if rng > 0 else 0.5
    body = abs(float(d1['close']) - float(d1['open']))
    upper_shadow = float(d1['high']) - max(float(d1['close']), float(d1['open']))
    open_gap = float(d1['open']) / float(d0['close']) - 1.0
    # 相对基准（上证指数同日）
    rel = None
    if bench is not None and not bench.empty:
        brow = bench[bench['date'] == str(d1.get('trade_date', ''))]
        if not brow.empty:
            bret = float(brow['pct_chg'].iloc[0])
            rel = ret * 100.0 - bret
    rc = ER20Config.REACT
    sell_news = False
    if open_gap > 0.07 and vr > 3 and float(d1['close']) < float(d1['open']) and upper_shadow > body:
        sell_news = True
    if vr > 4 and close_pos < 0.35:
        sell_news = True
    score = 50.0
    # 相对行业/基准：跑赢大盘加分
    if rel is not None:
        if rel > 3: score += 12
        elif rel > 0: score += 6
        elif rel < -5: score -= 10
    if rc['ret_min'] <= ret <= rc['ret_max']:
        score += 8
    if rc['vr_min'] <= vr <= rc['vr_max']:
        score += 6
    elif vr > 4:
        score -= 8            # 天量 = 分歧
    if close_pos >= rc['close_pos_min']:
        score += 8
    elif close_pos < 0.35:
        score -= 10
    # 高开低走/冲高回落
    if float(d1['close']) < float(d1['open']) and upper_shadow > body:
        score -= 12
    # T+3/T+5 缩量横盘（利好不兑现但也不回吐 → 健康）
    if cur_idx >= ann_idx + 2:
        seg = daily.iloc[ann_idx + 1:min(ann_idx + 6, cur_idx + 1)]
        if len(seg) >= 2:
            seg_ret = float(seg['close'].iloc[-1]) / float(seg['close'].iloc[0]) - 1.0
            seg_vr = float(seg['vol'].mean()) / ma20v if ma20v > 0 else 0.0
            if -0.04 <= seg_ret <= 0.04 and seg_vr <= 1.0:
                score += 6       # 缩量横盘确认
    if sell_news:
        score -= 25
    return round(min(100, max(0, score)), 1), ('SELL_THE_NEWS' if sell_news else '')


# ============================================================
# 技术引擎（TQS 六维）
# ============================================================

def trend_structure(daily, cur_idx, missing):
    """趋势结构：多头排列 + MA20/60 斜率 + 站上 MA20"""
    if cur_idx < 61:
        missing.append('trend_history')
        return None
    last = daily.iloc[cur_idx]
    ma5, ma10, ma20, ma60 = (float(last.get(f'ma{w}', np.nan)) for w in (5, 10, 20, 60))
    c = float(last['close'])
    if not all(_valid(x) for x in (ma5, ma10, ma20, ma60)):
        missing.append('trend_ma')
        return None
    s = 40.0
    if c > ma5 > ma10 > ma20: s += 20          # 多头排列
    elif ma5 > ma10: s += 10
    if c > ma20: s += 15
    if c > ma60: s += 15
    s20 = ma_slope(daily['ma20'], cur_idx, 5)
    s60 = ma_slope(daily['ma60'], cur_idx, 10)
    if _valid(s20) and s20 > 0: s += 5
    if _valid(s60) and s60 > 0: s += 5
    return round(min(100, s), 1)


def volume_structure(daily, cur_idx, missing):
    """量能结构：上涨放量 / 下跌缩量（近 20 日）"""
    if cur_idx < 21:
        missing.append('vol_history')
        return None
    seg = daily.iloc[cur_idx - 20:cur_idx + 1]
    up = seg[seg['pct_chg'] > 0]['vol']
    dn = seg[seg['pct_chg'] <= 0]['vol']
    if up.empty and dn.empty:
        missing.append('vol_both')
        return None
    if up.empty:
        return 15.0
    if dn.empty:
        return 85.0
    ratio = float(up.mean()) / float(dn.mean())
    if ratio >= 1.6: s = 85
    elif ratio >= 1.2: s = 68
    elif ratio >= 0.9: s = 50
    elif ratio >= 0.6: s = 32
    else: s = 18
    return s


def pullback_quality(daily, cur_idx, ann_idx, missing):
    """
    PQS 回踩质量：距 MA10/MA20、回调幅度/天数、缩量、不破关键位。
    最佳：强趋势回踩 MA10/MA20 缩量不破 + 重新出现阳线。
    """
    if cur_idx < 21:
        missing.append('pb_history')
        return None
    last = daily.iloc[cur_idx]
    c = float(last['close'])
    ma10 = float(last.get('ma10', np.nan))
    ma20 = float(last.get('ma20', np.nan))
    ma60 = float(last.get('ma60', np.nan))
    if not (_valid(ma10) and _valid(ma20)):
        missing.append('pb_ma')
        return None
    s = 45.0
    # 距均线位置（回踩到 MA10/MA20 为佳）
    d10 = (c / ma10 - 1) if ma10 > 0 else 0
    d20 = (c / ma20 - 1) if ma20 > 0 else 0
    if _valid(d20):
        if 0 <= d20 <= 0.03: s += 20          # 贴 MA20 回踩
        elif -0.02 <= d20 < 0: s += 15        # 略破 MA20 收回
        elif 0.03 < d20 <= 0.08: s += 8
        elif d20 > 0.15: s -= 15              # 严重偏离（追高区）
        elif d20 < -0.06: s -= 12             # 有效破位
    if _valid(d10) and 0 <= d10 <= 0.03: s += 8
    # 回调幅度与天数（公告后窗口内回踩）
    if cur_idx > ann_idx:
        seg = daily.iloc[ann_idx:cur_idx + 1]
        if len(seg) >= 3:
            hi = float(seg['high'].max())
            ret = c / hi - 1
            if _valid(ret):
                if -0.08 <= ret <= 0: s += 15      # 温和回踩
                elif -0.15 <= ret < -0.08: s += 6
                elif ret < -0.20: s -= 12
    # 缩量（回踩期量能低于前期）
    if cur_idx >= 20:
        cur_v = float(daily['vol'].iloc[cur_idx - 5:cur_idx + 1].mean())
        pre_v = float(daily['vol'].iloc[cur_idx - 20:cur_idx - 5].mean())
        if pre_v > 0 and cur_v < pre_v * 0.8: s += 10
        elif pre_v > 0 and cur_v > pre_v * 1.4: s -= 8
    # 破 MA60 重罚
    if _valid(ma60) and c < ma60 and _valid(d20) and d20 < -0.03:
        s -= 15
    # 重新出现阳线
    if float(last['close']) > float(last['open']):
        s += 5
    return round(min(100, max(0, s)), 1)


def breakout_quality(daily, cur_idx, missing):
    """BQS 突破质量：站上前高/平台/MA60 + 放量 + 收盘位置"""
    if cur_idx < 25:
        missing.append('bo_history')
        return None
    last = daily.iloc[cur_idx]
    c = float(last['close'])
    hi_20 = float(daily['high'].iloc[max(0, cur_idx - 20):cur_idx].max())
    ma60 = float(last.get('ma60', np.nan))
    ma5v = float(daily['vol'].iloc[max(0, cur_idx - 5):cur_idx].mean())
    s = 40.0
    bo = 0
    if _valid(hi_20) and c > hi_20:
        s += 20; bo += 1
    if _valid(ma60) and c > ma60:
        s += 15; bo += 1
    # 放量（>1.5× 5日均量）
    if _valid(ma5v) and ma5v > 0:
        vr = float(last['vol']) / ma5v
        if vr >= 1.5: s += 18
        elif vr >= 1.2: s += 10
        elif vr < 0.8: s -= 8
    # 收盘位置
    rng = float(last['high']) - float(last['low'])
    if rng > 0:
        cp = (c - float(last['low'])) / rng
        if cp >= 0.7: s += 12
        elif cp < 0.3: s -= 8
    # 突破后次日不跌回（若有次日数据）
    if cur_idx + 1 < len(daily) and bo > 0:
        nx = daily.iloc[cur_idx + 1]
        if float(nx['close']) >= c * 0.99:
            s += 5
    return round(min(100, max(0, s)), 1)


def momentum_score(daily, cur_idx, missing):
    """动量：RSI14 位置 + MACD 状态"""
    s = 50.0
    rsi = daily.iloc[cur_idx].get('rsi', np.nan)
    if _valid(rsi):
        if 50 <= rsi <= 70: s += 20
        elif 40 <= rsi < 50: s += 8
        elif 70 < rsi <= 80: s += 10
        elif rsi > 80: s += 2          # 超买边缘
        elif 30 <= rsi < 40: s -= 5
        else: s -= 12
    else:
        missing.append('rsi')
    dif, dea, hist = calc_macd(daily)
    if _valid(hist):
        if hist > 0 and dif > dea: s += 15
        elif hist > 0: s += 8
        elif hist < 0 and dif < dea: s -= 12
    else:
        missing.append('macd')
    return round(min(100, max(0, s)), 1)


def support_structure(daily, cur_idx, missing):
    """支撑结构：距前高距离（低=离高点近，支撑薄） + MA20 承接"""
    if cur_idx < 61:
        missing.append('sup_history')
        return None
    last = daily.iloc[cur_idx]
    c = float(last['close'])
    hi = float(daily['high'].iloc[max(0, cur_idx - 120):cur_idx].max())
    ma20 = float(last.get('ma20', np.nan))
    s = 50.0
    if _valid(hi) and hi > 0:
        dist = c / hi
        if dist >= 0.97: s -= 15         # 贴前高，追高风险
        elif dist >= 0.88: s += 5
        elif dist >= 0.75: s += 12
        else: s += 18                    # 距高点远，上方空间足
    if _valid(ma20) and c > ma20:
        s += 10
    return round(min(100, max(0, s)), 1)


def calc_tqs(daily, cur_idx, ann_idx, missing):
    """Technical Quality Score（TQS）：六维加权，核心维度缺失返 None"""
    w = ER20Config.W_TQS
    trend = trend_structure(daily, cur_idx, missing)
    vol = volume_structure(daily, cur_idx, missing)
    pb = pullback_quality(daily, cur_idx, ann_idx, missing)
    bo = breakout_quality(daily, cur_idx, missing)
    mom = momentum_score(daily, cur_idx, missing)
    sup = support_structure(daily, cur_idx, missing)
    parts = {'trend': trend, 'volume': vol, 'pullback': pb,
             'breakout': bo, 'momentum': mom, 'support': sup}
    wsum, ssum = 0.0, 0.0
    for k, wgt in w.items():
        v = parts.get(k)
        if _valid(v):
            wsum += wgt
            ssum += wgt * float(v)
        else:
            missing.append(f'tqs_{k}')
    if wsum < 0.75:
        missing.append('tqs_core')
        return None
    return round(ssum / wsum, 1)


# ============================================================
# 风险引擎
# ============================================================

def risk_score(r, daily, cur_idx, ann_idx):
    """风险分 0~100（低=风险小）：波动 + 透支 + 偏离 + 公告健康"""
    close = float(daily.iloc[cur_idx]['close'])
    atr = calc_atr14(daily)
    if not _valid(atr) or close <= 0:
        atr = close * 0.04 if close > 0 else 0.0
    vol_risk = min(40, max(0, atr / close * 100 / 4.0 * 40))   # 4% 日波幅 → 40 分
    overheat = overheat_penalty(r, daily, ann_idx, cur_idx)
    ma20 = float(daily.iloc[cur_idx].get('ma20', np.nan))
    dev = 0.0
    if _valid(ma20) and ma20 > 0:
        dev = close / ma20 - 1
    dev_risk = 0.0
    if dev > 0.15: dev_risk = 20
    elif dev > 0.08: dev_risk = 10
    elif dev < -0.08: dev_risk = 12
    return round(min(100, vol_risk + overheat * 0.8 + dev_risk), 1)


# ============================================================
# 数据置信度
# ============================================================

def data_confidence(r, daily, ann_idx, missing):
    """
    DATA_CONFIDENCE 0~100。
    金融完整性 0.35 / 技术完整性 0.20 / 公告完整性 0.25 / 历史完整性 0.10 / 数据新鲜度 0.10
    """
    # 金融字段完整性（核心 8 项）
    fin_cols = ['netprofit_yoy', 'dt_netprofit_yoy', 'tr_yoy', 'q1_profit_yoy',
                'q2_profit_yoy', 'roe', 'ocf_yoy', 'grossprofit_margin']
    fin_ok = sum(1 for c in fin_cols if _valid(r.get(c, np.nan)))
    fin = fin_ok / len(fin_cols)
    # 技术完整性（行情 + 均线）
    tech = 1.0 if (daily is not None and len(daily) >= 120) else 0.3
    # 公告完整性
    ann = str(r.get('ann_date', ''))
    ann_ok = 1.0 if (len(ann) == 8 and ann[:4] == '2026') else 0.4
    # 历史完整性（60 日以上）
    hist = 1.0 if len(daily) >= 65 else 0.5
    # 数据新鲜度（ann_date 距今 ≤10 交易日）
    fresh = 0.7
    if len(daily) > 0:
        last_d = str(daily.iloc[-1]['trade_date'])
        fresh = 1.0 if ann <= last_d else 0.5
    conf = round(100 * (0.35 * fin + 0.20 * tech + 0.25 * ann_ok + 0.10 * hist + 0.10 * fresh), 1)
    return conf


# ============================================================
# 市场环境 + 主题
# ============================================================

def market_multiplier(scan_date):
    """市场环境 6 档 + 乘数（指数 MA20/MA60 位置 + 20 日涨幅，无未来函数）"""
    try:
        idx = load_bench(scan_date)
    except Exception:
        idx = None
    if idx is None or len(idx) < 65:
        regime, mult = 'neutral', 1.00
    else:
        regime = _market_regime_6(idx)
        mult = ER20Config.MARKET_MULT.get(regime, 1.00)
    return regime, mult


def _market_regime_6(idx):
    """
    6 档市场状态：
      strong   = 指数 > MA20 > MA60 且 20 日涨幅 > 2%
      bull     = 指数 > MA20 且 20 日涨幅 > 0（强度略弱）
      neutral  = 其余（多空拉锯）
      recovery = 指数 < MA20 但 20 日跌幅 < 2%（超跌回升初期）
      weak     = 指数 < MA20 且 20 日跌幅 >= 2%
      bear     = 指数 < MA20 < MA60 且 20 日跌幅 >= 5%
    """
    c = idx['close'].astype(float)
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    last = idx.iloc[-1]
    price = float(last['close'])
    m20, m60 = float(ma20.iloc[-1]), float(ma60.iloc[-1])
    r20 = float(c.iloc[-1] / c.iloc[-21] - 1) if len(c) > 21 else 0.0
    if price > m20 > m60 and r20 > 0.02:
        return 'strong'
    if price > m20 and r20 > 0:
        return 'bull'
    if price > m20:
        return 'neutral'
    if m20 > m60 and r20 >= -0.02:
        return 'recovery'
    if m20 > m60:
        return 'weak'
    if r20 <= -0.05:
        return 'bear'
    return 'weak'


def theme_score(code, stock2theme):
    """主题确认：白名单命中 70，冷门 60 → 映射到 ±5 分贡献"""
    th = stock2theme.get(code, [])
    if not th:
        return 0.0
    hit = set(th) & ER20Config.THEME_WHITELIST
    if hit:
        return ER20Config.THEME_ADJ_MAX
    return 0.0


# ============================================================
# Entry Engine（Alpha ≠ Entry）
# ============================================================

def entry_engine(daily, cur_idx, ann_idx, tqs, risk, market_mult):
    """
    ENTRY_SCORE 0~100 = 0.30位置 + 0.25触发 + 0.20量能 + 0.15盈亏比 + 0.10市场。
    返回 (entry_score, trigger_desc, entry_note)。
    """
    w = ER20Config.W_ENTRY
    last = daily.iloc[cur_idx]
    c = float(last['close'])
    ma10 = float(last.get('ma10', np.nan))
    ma20 = float(last.get('ma20', np.nan))
    ma60 = float(last.get('ma60', np.nan))
    # ── Location 0.30：贴近关键均线 + 距前高有空间 ──
    loc = 45.0
    if _valid(ma20) and ma20 > 0:
        d20 = c / ma20 - 1
        if 0 <= d20 <= 0.03: loc = 85
        elif -0.02 <= d20 < 0: loc = 78
        elif 0.03 < d20 <= 0.08: loc = 62
        elif d20 > 0.15: loc = 20
        elif d20 < -0.06: loc = 25
    if _valid(ma10) and ma10 > 0 and 0 <= c / ma10 - 1 <= 0.03:
        loc = min(90, loc + 8)
    # ── Trigger 0.25：当日触发信号 ──
    trig, desc = 0.0, '无触发'
    if cur_idx > ann_idx:
        prev3 = float(daily['vol'].iloc[cur_idx - 4:cur_idx].mean())
        hi3 = float(daily['high'].iloc[max(0, cur_idx - 3):cur_idx].max())
        vol_ratio = float(last['vol']) / prev3 if prev3 > 0 else 0.0
        ret_today = float(last['close']) / float(daily.iloc[cur_idx - 1]['close']) - 1.0
        # 放量突破（阳线站上前 3 日高点 + 量比 ≥1.2）
        if (c > hi3 and vol_ratio >= 1.2 and ret_today > 0
                and not (_valid(ma5 := float(last.get('ma5', np.nan))) and ret_today > 0.09)):
            trig, desc = 88.0, '放量突破前高'
        # 回踩后阳线（贴近 MA20 + 阳线 + 温和量）
        elif _valid(ma20) and ma20 > 0 and 0 <= c / ma20 - 1 <= 0.04 \
                and float(last['close']) > float(last['open']):
            trig, desc = 72.0, '回踩MA20后阳线'
        # MA60 突破
        elif _valid(ma60) and ma60 > 0 and c > ma60 and vol_ratio >= 1.2:
            trig, desc = 80.0, '放量突破MA60'
        elif ret_today > 0.05:
            trig, desc = 55.0, '温和上涨'
    # ── Volume 0.20 ──
    vol = 50.0
    if cur_idx >= 5:
        vr5 = float(last['vol']) / float(daily['vol'].iloc[cur_idx - 5:cur_idx].mean()) if daily['vol'].iloc[cur_idx - 5:cur_idx].mean() > 0 else 0.0
        if 1.2 <= vr5 <= 2.5: vol = 80
        elif 0.9 <= vr5 < 1.2: vol = 60
        elif vr5 > 3: vol = 40
        elif vr5 < 0.7: vol = 35
    # ── R/R 0.15：止损距离 vs 上方空间 ──
    atr = calc_atr14(daily)
    atr = atr if _valid(atr) else c * 0.04
    stop_dist = atr * 1.5 / c
    hi_60 = float(daily['high'].iloc[max(0, cur_idx - 60):cur_idx].max())
    up_space = (hi_60 / c - 1) if hi_60 > c > 0 else 0.0
    rr = 50.0
    if up_space > 0:
        ratio = up_space / max(stop_dist, 0.02)
        rr = 80 if ratio >= 2.5 else (65 if ratio >= 1.5 else (50 if ratio >= 1.0 else 32))
    # ── Market 0.10 ──
    mkt = 50.0 + (market_mult - 1.0) * 150
    mkt = min(100, max(0, mkt))
    entry = w['location'] * loc + w['trigger'] * trig + w['volume'] * vol + w['rr'] * rr + w['market'] * mkt
    return round(entry, 1), desc, loc, trig, vol, rr


# ============================================================
# Position Engine
# ============================================================

def position_engine(strategy, status, risk, market_mult, market_cap=None):
    """建议仓位（0~1）：事件股 ≤3%；按风险+市场档位递减"""
    if strategy == 'C_EVENT_SPEC':
        return round(ER20Config.RISK['event_pos_cap'], 3)
    base = 0.10
    if status == 'CORE_BUY':
        base = 0.20
    elif status == 'TEST_BUY':
        base = 0.12
    elif status in ('WAIT_CONFIRM', 'WAIT_PULLBACK'):
        base = 0.05
    elif status == 'WATCH':
        base = 0.03
    if market_mult < 1.0:
        base *= market_mult
    if risk > 60:
        base *= 0.5
    if market_cap and market_cap < 80e8:
        base *= 0.7
    return round(min(0.20, max(0.01, base)), 3)


# ============================================================
# 等级判定（CORE_BUY / TEST_BUY / WAIT_* / WATCH / REJECT）
# ============================================================

def grade_v2(alpha, entry, risk, conf, fq, rqs, strategy, trigger, missing):
    """
    按规格门槛分级：
      CORE_BUY:  Alpha≥75 + Entry≥80 + Risk≤40 + Conf≥80 + 有触发
      TEST_BUY:  Alpha≥68 + Entry≥70
      WAIT_CONFIRM/WAIT_PULLBACK: Alpha≥60 但今日无触发/需等回踩
      WATCH:     Conf<70 的高分股或低 Alpha 观察
      REJECT:    Fundamental Floor / 基本面证据不足
    Fundamental Floor: FQ<25 默认 REJECT（B_REVERSAL 若 RQS≥70 可放行）。
    Data Confidence:  <50 只能 WATCH；<70 不能 CORE_BUY。
    """
    g = ER20Config.GATE
    # ── 假信号硬剔除（D_FALSE_SIGNAL 不入任何买入/观察榜） ──
    if strategy == 'D_FALSE_SIGNAL':
        return 'REJECT', '假信号'
    # ── 事件股隔离 ──
    if strategy == 'C_EVENT_SPEC':
        return 'WATCH', '事件驱动仅观察'
    # ── Fundamental Floor ──
    if fq is not None and fq < g['fq_floor_reject']:
        if strategy == 'B_REVERSAL' and rqs is not None and rqs >= g['rqs_high_grade']:
            pass  # 反转股用 RQS 放行
        else:
            return 'REJECT', f'基本面{fq:.0f}<{g["fq_floor_reject"]}'
    # ── 数据置信度封顶 ──
    if conf < g['conf_watch']:
        return 'WATCH', f'数据置信{conf:.0f}<{g["conf_watch"]}'
    if conf < g['core_conf'] and alpha >= g['core_alpha']:
        return 'WATCH', f'置信{conf:.0f}<{g["core_conf"]}不可CORE_BUY'
    # ── 无触发的高分股 ──
    if not trigger or trigger == '无触发':
        if alpha >= g['watch_alpha']:
            return 'WAIT_CONFIRM', '高分但今日无触发'
        return 'WATCH', '等待触发'
    # ── CORE_BUY ──
    if (alpha >= g['core_alpha'] and entry >= g['core_entry']
            and risk <= g['core_risk'] and conf >= g['core_conf']):
        return 'CORE_BUY', ''
    # ── TEST_BUY ──
    if alpha >= g['test_alpha'] and entry >= g['test_entry']:
        return 'TEST_BUY', ''
    # ── 回踩型触发 → WAIT_PULLBACK ──
    if trigger in ('回踩MA20后阳线', '温和上涨'):
        if alpha >= g['watch_alpha']:
            return 'WAIT_PULLBACK', '触发偏弱，等放量确认'
    if alpha >= g['watch_alpha']:
        return 'WAIT_CONFIRM', '观察等触发'
    return 'REJECT', f'Alpha{alpha:.0f}<{g["watch_alpha"]}'


# ============================================================
# 主流程 scan_v2
# ============================================================

def scan_v2(scan_date='20260820'):
    """
    STEP1 取池 → STEP2 粗筛(公告窗口/数据量) → STEP3 事件分类
    → STEP4 策略专属加权 → STEP5 策略内 percentile 归一化
    → STEP6 Confidence/Risk/Market/Theme 调整 → STEP7 分级
    → STEP8 落库 + 报告。
    """
    t0 = time.time()
    period = '20260630'
    g = ER20Config.GATE
    print(f'[scan_v2] 扫描日 {scan_date}  报告期 {period}')
    # ── 市场环境 ──
    regime, market_mult = market_multiplier(scan_date)
    print(f'  市场环境: {regime}  x{market_mult}')
    bench = load_bench(scan_date)
    stock2theme = theme_map_stock2theme()
    # ── 取池 + 粗筛 ──
    pool = load_pool_v2(period, scan_date)
    if pool.empty:
        print('  池为空，退出')
        return None
    print(f'  池规模: {len(pool)}')
    dates = get_trade_dates('20250101', str(scan_date))
    cur_i = len(dates) - 1
    ann_idx_all = {d: i for i, d in enumerate(dates)}
    cands = []
    for _, r in pool.iterrows():
        if str(r['ts_code']).endswith('.BJ'):
            continue
        ann = str(r.get('ann_date', ''))
        if len(ann) != 8:
            continue
        tds = [d for d in dates if d >= ann]
        if not tds or tds[0] not in ann_idx_all:
            continue
        gap = cur_i - ann_idx_all[tds[0]]
        if not (ER20Config.ANN_WINDOW_MIN <= gap <= ER20Config.ANN_WINDOW_MAX):
            continue
        q2 = r.get('q2_profit_yoy', np.nan)
        ni = r.get('netprofit_yoy', np.nan)
        if not (_valid(q2) or _valid(ni)):
            continue
        cands.append((r, ann, gap))
    print(f'  公告窗口内候选: {len(cands)}')
    # ── 逐股评分 ──
    rows = []
    for i, (r, ann, gap) in enumerate(cands):
        code = r['ts_code']
        daily = load_daily_for(code, scan_date)
        if daily is None:
            continue
        nxt = daily[daily['trade_date'] > ann]
        if nxt.empty:
            continue
        ann_idx = nxt.index[0]
        cur_idx = len(daily) - 1
        if ann_idx >= cur_idx:
            continue
        missing = []
        strategy, cls_reason = classify_event(r, daily, ann_idx, cur_idx)
        # ── 公共因子 ──
        fq = fundamental_quality(r, missing)
        gap_s, gap_note = expect_gap_score(r, daily, ann_idx)
        ars, ars_note = calc_ars(daily, ann_idx, cur_idx, bench)
        risk = risk_score(r, daily, cur_idx, ann_idx)
        overheat = overheat_penalty(r, daily, ann_idx, cur_idx)
        conf = data_confidence(r, daily, ann_idx, missing)
        th = theme_score(code, stock2theme)
        # ── 策略专属 ──
        if strategy == 'B_REVERSAL':
            rqs = calc_rqs(r, missing)
            tqs = calc_tqs(daily, cur_idx, ann_idx, missing)
            pqs = trend = None
        else:
            rqs = tqs = None
            pqs = pullback_quality(daily, cur_idx, ann_idx, missing)
            trend = trend_structure(daily, cur_idx, missing)
        # ── 策略专属加权（有效维度权重归一） ──
        wmap = ER20Config.W_B if strategy == 'B_REVERSAL' else ER20Config.W_A
        parts = {}
        if strategy == 'B_REVERSAL':
            parts = {'rqs': rqs, 'fq': fq, 'gap': gap_s, 'ars': ars, 'tqs': tqs, 'risk': risk}
        else:
            parts = {'fq': fq, 'gap': gap_s, 'ars': ars, 'pqs': pqs, 'trend': trend, 'risk': risk}
        wsum, ssum = 0.0, 0.0
        for k, wgt in wmap.items():
            v = parts.get(k)
            if _valid(v):
                wsum += wgt
                ssum += wgt * float(v)
            else:
                missing.append(k)
        if wsum == 0:
            continue
        raw = ssum / wsum
        rows.append({
            'ts_code': code, 'name': r.get('name', ''), 'ann_date': ann, 'gap': gap,
            'strategy': strategy, 'cls_reason': cls_reason, 'raw': round(raw, 1),
            'fq': fq, 'rqs': rqs, 'gap_s': gap_s, 'ars': ars,
            'pqs': pqs, 'trend': trend, 'tqs': tqs, 'risk': risk,
            'overheat': overheat, 'conf': conf, 'theme_adj': th,
            'missing': '|'.join(sorted(set(missing))),
        })
        if (i + 1) % 200 == 0:
            print(f'  已评分 {i + 1}/{len(cands)}')
    if not rows:
        print('  无可评分候选')
        return None
    df = pd.DataFrame(rows)
    # ── 策略内 percentile 归一化（raw → 0~100） ──
    for strat, grp in df.groupby('strategy'):
        if len(grp) >= 3:
            df.loc[df['strategy'] == strat, 'norm'] = (
                grp['raw'].rank(pct=True).reindex(df[df['strategy'] == strat].index) * 100.0)
        else:
            df.loc[df['strategy'] == strat, 'norm'] = grp['raw'].clip(0, 100)
    # ── 最终 ER20_ALPHA = norm × Conf × Risk × Market + Theme ──
    conf_mult = df['conf'].astype(float) / 100.0
    risk_mult = 1.0 - (df['risk'].astype(float) / 100.0) * ER20Config.RISK_PEN
    df['alpha'] = (df['norm'] * conf_mult * risk_mult * market_mult + df['theme_adj']).round(1)
    # ── Entry 引擎（只对高分股计算，省时） ──
    entry_map = {}
    for _, row in df[df['alpha'] >= 45].iterrows():
        daily = load_daily_for(row['ts_code'], scan_date)
        if daily is None:
            continue
        nxt = daily[daily['trade_date'] > row['ann_date']]
        if nxt.empty:
            continue
        ann_idx = nxt.index[0]
        cur_idx = len(daily) - 1
        e = entry_engine(daily, cur_idx, ann_idx, None, row['risk'], market_mult)
        entry_map[row['ts_code']] = e
    df['entry'] = df['ts_code'].map(lambda c: entry_map.get(c, (None, '未计算', None, None, None, None))[0])
    df['trigger'] = df['ts_code'].map(lambda c: entry_map.get(c, (None, '', None, None, None, None))[1])
    # ── 分级 ──
    grades, reasons = [], []
    for _, row in df.iterrows():
        st, rs = grade_v2(row['alpha'], row['entry'], row['risk'], row['conf'],
                          row['fq'], row['rqs'], row['strategy'], row['trigger'], [])
        grades.append(st)
        reasons.append(rs)
    df['grade'] = grades
    df['grade_reason'] = reasons
    # ── 排序 ──
    df = df.sort_values('alpha', ascending=False).reset_index(drop=True)
    # ── 落库 + 报告 ──
    save_sqlite(df, scan_date)
    print(f'[scan_v2] 完成 {len(df)} 只，耗时 {time.time() - t0:.0f}s')
    return df


# ============================================================
# SQLite 因子落库（供 Forward 5/10/20D 回测）
# ============================================================

def save_sqlite(df, scan_date):
    cols = ['ts_code', 'name', 'ann_date', 'gap', 'strategy', 'cls_reason', 'raw', 'norm',
            'fq', 'rqs', 'gap_s', 'ars', 'pqs', 'trend', 'tqs', 'risk',
            'overheat', 'conf', 'theme_adj', 'alpha', 'entry', 'trigger', 'grade', 'grade_reason']
    save = df[[c for c in cols if c in df.columns]].copy()
    save.insert(0, 'scan_date', str(scan_date))
    save['missing'] = df.get('missing', '')
    conn = sqlite3.connect(DB_PATH)
    try:
        # 同日期重跑先清旧行，避免重复追加
        conn.execute('DELETE FROM er20_v2_scores WHERE scan_date = ?', (str(scan_date),))
        conn.commit()
        # 旧表缺列则补（列名变更后的兼容）
        exist = {r[1] for r in conn.execute('PRAGMA table_info(er20_v2_scores)').fetchall()}
        for c in save.columns:
            if c not in exist:
                conn.execute(f'ALTER TABLE er20_v2_scores ADD COLUMN "{c}" TEXT')
        conn.commit()
        save.to_sql('er20_v2_scores', conn, if_exists='append', index=False)
        conn.commit()
    finally:
        conn.close()
    print(f'  已落库 {len(save)} 行 -> {DB_PATH}')


# ============================================================
# DATA QUALITY REPORT（数据质量审计）
# ============================================================

def validate_er20_scores(df):
    print('\n===== DATA QUALITY REPORT =====')
    if df is None or df.empty:
        print('  无数据')
        return
    n = len(df)
    print(f'  样本: {n} 只')
    # 各因子缺失率
    core_factors = ['fq', 'rqs', 'gap_s', 'ars', 'pqs', 'tqs', 'trend']
    print('  核心因子缺失率:')
    for c in core_factors:
        miss = df[c].isna().mean() * 100 if c in df.columns else np.nan
        if pd.notna(miss):
            print(f'    {c:<8s} 缺失 {miss:5.1f}%')
    # Confidence 分布
    if 'conf' in df.columns:
        print(f'  Confidence: 均值{df["conf"].mean():.0f}  中位{df["conf"].median():.0f}  '
              f'<50分 {int((df["conf"] < 50).sum())}只  <70分 {int((df["conf"] < 70).sum())}只')
    # 策略分布
    if 'strategy' in df.columns:
        vc = df['strategy'].value_counts()
        print('  事件分类: ' + '  '.join(f'{k}={v}' for k, v in vc.items()))
    # 高频缺失因子 Top10
    miss_pool = []
    for m in df.get('missing', '').tolist():
        if m:
            miss_pool += m.split('|')
    if miss_pool:
        from collections import Counter
        print('  高频缺失因子 Top8: ' + ' '.join(f'{k}x{v}' for k, v in Counter(miss_pool).most_common(8)))
    # 等级分布
    if 'grade' in df.columns:
        vc = df['grade'].value_counts()
        print('  等级分布: ' + '  '.join(f'{k}={v}' for k, v in vc.items()))
    # 指标口径审计（ARS 无默认分、gap 无默认分）
    if 'ars' in df.columns and 'gap_s' in df.columns:
        ars_nan = df['ars'].isna().sum()
        gap_nan = df['gap_s'].isna().sum()
        print(f'  防默认分审计: ARS None={ars_nan}只  GAP None={gap_nan}只  (规格: 缺失不得给默认分)')
    print('==============================')


# ============================================================
# 报告输出（4 榜单 + 个股 20 字段报告）
# ============================================================

def build_report(df, scan_date, regime, market_mult):
    lines = []
    lines.append(f'# ER20 V2.0 中报事件驱动扫描 — {scan_date}')
    lines.append(f'市场环境: {regime} x{market_mult}  |  样本 {len(df)} 只  |  评分=归一化×置信×风险×市场+主题')
    lines.append('')
    buy = df[df['grade'].isin(['CORE_BUY', 'TEST_BUY'])]
    watch = df[df['grade'].isin(['WAIT_CONFIRM', 'WAIT_PULLBACK', 'WATCH'])]
    rej = df[df['grade'] == 'REJECT']
    shown = df[df['grade'] != 'REJECT']   # 榜单/个股报告只展示非 REJECT
    # ── 1. ALPHA TOP20 ──
    top = shown.head(20) if len(shown) else shown
    lines.append(f'## 一、ALPHA TOP {len(top)}（值不值得持有，与入场无关）')
    lines.append('| 排名 | 代码 | 名称 | 事件 | Alpha | 置信 | 风险 | Entry | 触发 | 等级 |')
    lines.append('|---:|---|---|---|---:|---:|---:|---:|---|---|')
    for i, (_, r) in enumerate(top.iterrows()):
        lines.append(f"| {i+1} | {r['ts_code']} | {r['name']} | {r['strategy'][:10]} | {r['alpha']:.1f} | "
                     f"{r['conf']:.0f} | {r['risk']:.0f} | {r['entry'] if pd.notna(r['entry']) else '-'} | "
                     f"{r['trigger']} | {r['grade']} |")
    lines.append('')
    # ── 2. TODAY BUY LIST ──
    lines.append('## 二、TODAY BUY LIST（今日可买）')
    if buy.empty:
        lines.append('无 CORE_BUY / TEST_BUY（纪律：宁可 0~5 只，不降门槛）')
    else:
        for _, r in buy.iterrows():
            lines.append(f"- **{r['name']}** ({r['ts_code']}) {r['strategy']}  Alpha={r['alpha']:.1f}  "
                         f"Entry={r['entry']:.0f}  {r['trigger']}  仓位建议 {position_engine(r['strategy'], r['grade'], r['risk'], 1.0)}")
    lines.append('')
    # ── 3. WATCH ──
    lines.append('## 三、WATCH / WAIT（观察等触发或回踩）')
    if watch.empty:
        lines.append('无')
    else:
        for _, r in watch.head(15).iterrows():
            lines.append(f"- **{r['name']}** ({r['ts_code']}) {r['strategy']}  Alpha={r['alpha']:.1f}  "
                         f"Entry={r['entry'] if pd.notna(r['entry']) else '-'}  {r['grade']}  {r['grade_reason']}")
    lines.append('')
    # ── 4. REJECT 摘要 ──
    lines.append(f'## 四、REJECT（{len(rej)} 只）')
    if not rej.empty:
        vc = rej['grade_reason'].str.replace(r'\d+\.?\d*', 'N', regex=True).value_counts().head(5)
        for k, v in vc.items():
            lines.append(f'- {k} × {v}')
    lines.append('')
    # ── 5. 个股报告（Top 候选 20 字段） ──
    lines.append('## 五、Top 候选个股报告')
    for _, r in shown.head(10).iterrows():
        lines.append(f'\n### {r["name"]} ({r["ts_code"]}) — {r["grade"]}  {r["grade_reason"]}')
        lines.append(f'- **20D Thesis**: {r["strategy"]} 事件，Alpha={r["alpha"]:.1f}（{r["raw"]:.0f} 归一化 {r["norm"]:.0f}），'
                     f'主题调整 {r["theme_adj"]:+.0f}')
        lines.append(f'- **Buy Trigger**: {r["trigger"]}（Entry={r["entry"] if pd.notna(r["entry"]) else "-"}）')
        lines.append(f'- **Invalidation**: 基本面 FQ={r["fq"] if pd.notna(r["fq"]) else "-"} / RQS={r["rqs"] if pd.notna(r["rqs"]) else "-"}；'
                     f'跌破止损或置信 {r["conf"]:.0f} 不足时放弃')
        lines.append(f'- **Risk**: 风险 {r["risk"]:.0f}（透支 {r["overheat"]:.0f}）')
        lines.append(f'- **Position**: {position_engine(r["strategy"], r["grade"], r["risk"], 1.0) * 100:.0f}%')
        lines.append(f'- **Data**: 缺失因子 {r["missing"] if r.get("missing") else "无"}')
    txt = '\n'.join(lines)
    fp = os.path.join(REPORT_DIR, f'er20_v2_report_{scan_date}.md')
    with open(fp, 'w', encoding='utf-8') as f:
        f.write(txt)
    print(f'  报告已写 {fp}')
    return txt


# ============================================================
# 新旧排名对比
# ============================================================

def compare_v1_v2(scan_date, df_new):
    """旧 vs 新排名对比（优先复用 V1 已产出的 CSV，缺失才重跑 V1 扫描）"""
    v1_csv = os.path.join(REPORT_DIR, f'er20_scan_{scan_date}.csv')
    if os.path.exists(v1_csv):
        print(f'[compare] 复用 V1 旧 CSV: {v1_csv}')
        old = pd.read_csv(v1_csv, dtype={'ts_code': str})
    else:
        import er20_strategy as v1
        print(f'\n[compare] V1 CSV 缺失，运行 V1 旧版扫描 {scan_date} ...')
        try:
            v1.scan(scan_date=str(scan_date))
        except Exception as e:
            print(f'  V1 运行失败: {e}')
        old = pd.read_csv(v1_csv, dtype={'ts_code': str}) if os.path.exists(v1_csv) else None
    print('\n===== 旧 vs 新排名对比 =====')
    focus = [('688309.SH', '恒誉环保'), ('603236.SH', '移远通信'), ('688469.SH', '芯联集成'),
             ('689009.SH', '九号公司'), ('688082.SH', '盛美上海'), ('002648.SZ', '卫星化学'),
             ('301308.SZ', '江波龙'), ('300191.SZ', '潜能恒信'), ('688083.SH', '中望软件'),
             ('600183.SH', '生益科技')]
    # V1 CSV 索引（兼容中文/英文列名）
    code_col = '代码' if old is not None and '代码' in old.columns else ('ts_code' if old is not None else None)
    if old is not None and code_col:
        old_map = {str(r[code_col]).split('.')[0]: r for _, r in old.iterrows()}
    else:
        old_map = {}
    for ts, nm in focus:
        code6 = ts.split('.')[0]
        rn = df_new[df_new['ts_code'] == ts]
        ro = old_map.get(code6)
        nv = f"Alpha={rn.iloc[0]['alpha']:.1f}/{rn.iloc[0]['grade']}" if not rn.empty else '未入选'
        if ro is not None:
            sc = ro.get('ER20评分', ro.get('total_score', ro.get('score', np.nan)))
            st = ro.get('状态', ro.get('grade', ro.get('status', '')))
            ov = f"总分={sc}/{st}"
        else:
            ov = '未入选'
        print(f'  {nm:<6s} 旧[{ov}]  vs  新[{nv}]')
    return old


# ============================================================
# 主入口
# ============================================================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='20260820', help='扫描日 YYYYMMDD')
    ap.add_argument('--compare', action='store_true', help='与 V1 旧版对比 + 重点验证 10 只股票')
    ap.add_argument('--validate', action='store_true', help='输出 DATA QUALITY REPORT')
    args = ap.parse_args()
    scan_date = args.date
    df = scan_v2(scan_date)
    if df is None:
        return
    regime, market_mult = market_multiplier(scan_date)
    build_report(df, scan_date, regime, market_mult)
    if args.validate:
        validate_er20_scores(df)
    if args.compare:
        compare_v1_v2(scan_date, df)
    print('\n完成')


if __name__ == '__main__':
    main()
