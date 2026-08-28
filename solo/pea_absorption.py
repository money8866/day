# -*- coding: utf-8 -*-
"""
pea_absorption.py — PEA-Absorption 价格-事件吸收独立策略 V1.0
=============================================================

范式：Price-Event Absorption（价格-事件吸收）
核心命题：中报事件后，价格对信息的"吸收程度"决定后续超额收益的方向。
  - NOT_ABSORBED      事件未被吸收（相对强势 ≤3%）→ 仍有漂移空间，最优先
  - SECONDARY_CONFIRM 事件正被次级资金确认（3%~15%）→ 趋势延续，次优先
  - PRICED_IN         事件已被完全定价（≥15% 或 pre_priced）→ 硬排除

硬规则（实证内嵌，不可配置绕过）：
  1. T+15 持有期（15 个交易日收盘离场）
  2. -8% 收盘价止损
  3. T3_RECLAIM 触发类型硬屏蔽（实证 fwd20 -3.05% / win 31% / n=77）
  4. PRICED_IN 吸收态硬排除
  5. 次日开盘 > 前收×1.08 → 放弃追高

实证锚点：er20 基线 close15 Top2 净超额 +0.40%（唯一净超额为正的退出方案）；
spike5 净 -1.24%、mix15 净 -0.45% 已证伪，故本策略仅保留 close15 路径。

依赖：仅 bts 公共库（bts.data / bts.indicators），零 er20 系列依赖。
数据：SQLite/TDX 本地优先；财报池 fin_ind parquet，缺失时 treasure 回退。

用法：
  python pea_absorption.py --period 2026H1 --date 20260905
  python pea_absorption.py --validate 600XXX --period 2026H1
"""

import os
import re
import glob
import json
import math
import argparse
import warnings
from collections import defaultdict

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

from bts.data import (
    load_daily,
    get_trade_dates,
    parse_tdx_day_file,
    load_stock_basic,
    market_regime,
    to_ts_code,
)
from bts.indicators import add_ma, add_rsi, add_vol_ma, ma_slope

# ============================================================
# 路径
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DAILY = r'D:\mystock\cache_daily'
TDX_VIPDOC = r'D:\new_tdx\vipdoc'
REPORT_DIR = os.path.join(BASE_DIR, 'report_daily')
DB_PATH = os.path.join(BASE_DIR, 'pea_absorption.db')

TDX_DAY_CANDIDATES = [
    os.path.join(TDX_VIPDOC, 'sh', 'lday'),
    os.path.join(TDX_VIPDOC, 'sz', 'lday'),
    r'D:\new_tdx\vipdoc\sh\lday',
    r'D:\new_tdx\vipdoc\sz\lday',
]

BENCH_CACHE = {}
_TRADE_DATES_CACHE = {}
_INDUSTRY_MAP_CACHE = {}


# ============================================================
# 配置
# ============================================================
class PeaConfig:
    """PEA-Absorption 全参数。数值与实证回测口径严格一致。"""

    # ---------- 权重 ----------
    # A 类事件（景气/成长共振）：基本面驱动
    W_A = {'fq': 0.25, 'gap': 0.20, 'ars': 0.15, 'pqs': 0.20, 'trend': 0.10, 'risk': 0.10}
    # B 类事件（预期差/反转）：质量+技术驱动
    W_B = {'rqs': 0.25, 'fq': 0.15, 'gap': 0.15, 'ars': 0.15, 'tqs': 0.20, 'risk': 0.10}

    # RQS 七维
    W_RQS = {'profit': 0.25, 'revenue': 0.20, 'margin': 0.15,
             'cash': 0.15, 'balance': 0.10, 'persist': 0.10, 'cycle': 0.05}

    # TQS 六维
    W_TQS = {'trend': 0.25, 'volume': 0.20, 'pullback': 0.20,
             'breakout': 0.15, 'momentum': 0.10, 'support': 0.10}

    # ---------- 吸收三态（范式核心） ----------
    ABSORPTION = {
        'not_absorbed_max': 0.03,     # rel_str ≤ 3% → NOT_ABSORBED
        'secondary_max': 0.08,        # 3% < rel_str ≤ 8% 且未超 → SECONDARY_CONFIRM 上界参考
        'priced_in_min': 0.15,        # rel_str ≥ 15% → PRICED_IN（硬排除）
        'pre_priced_ret': 0.20,       # 事前涨幅 >20% → 事前已定价
        'pre_priced_gap': 0.05,       # 事前 >12% 且跳空 >5% → 事前已定价
    }

    # ---------- Decay 分层（event_age → 折减系数） ----------
    DECAY_TIERS = [(2, 0.00), (5, 0.03), (10, 0.08), (15, 0.15), (20, 0.25)]

    # ---------- Refresh（吸收重启）4 条件加分 ----------
    REFRESH = {
        'cond_full': 8.0,        # 4/4 条件
        'cond_strong': 6.0,      # 3 条件
        'cond_partial': 4.0,     # ≥2 条件
        'vol_ratio': 1.30,       # 量比阈值
        'pullback_vol_max': 0.90,  # 回踩缩量
        'ma20_hold': 0.97,       # 收盘 > ma20×0.97
    }

    # ---------- 触发分类 ----------
    TRIGGER = {
        'min_history': 25,          # cur_idx < 25 → NO_TRIGGER
        't1_base': 80,              # T1_BREAKOUT 基础分
        't3_base': 72,              # T3_RECLAIM 基础分（被硬屏蔽，仅诊断）
        't2_base': 70,              # T2_PULLBACK 基础分
        't1_close_pos': 0.70,       # 收盘位于日内区间位置
        't1_vr_lo': 1.5,
        't1_vr_hi': 2.5,
        't2_vol_max': 1.00,
    }

    # ---------- EES（事件执行分） ----------
    EES_W = {'trend': 0.25, 'trigger': 0.30, 'volume': 0.15,
             'pqs': 0.20, 'overheat': -0.10}
    EES_DEFAULT = {'trend_v': 40, 'vol_v': 50, 'pqs_v': 50, 'oh_v': 0}

    # ---------- 门控阈值 ----------
    GATE = {
        'core_alpha': 85, 'core_ees': 80, 'core_ts': 80, 'core_risk': 35,
        'core_conf': 80, 'core_fq': 35, 'core_overheat': 20,
        'test_alpha': 80, 'test_ees': 72, 'test_ts': 72, 'test_risk': 50,
        'probe_alpha': 72, 'probe_ees': 60,
        'watch_alpha': 60,          # alpha ≥60 → WAIT_CONFIRM
        'fq_floor_reject': 25,      # fq <25 → REJECT（B_REVERSAL+RQS≥70 豁免）
        'conf_watch': 50,           # conf <50 → WATCH
        'overheat_pullback': 25,    # overheat >25 → WAIT_PULLBACK
        'ts_watch': 70,             # ts <70 → WAIT_PULLBACK/WATCH
        'rqs_high_grade': 70,       # B_REVERSAL 豁免线
    }

    # ---------- 执行硬规则 ----------
    EXEC = {
        'max_hold': 15,          # T+15 收盘离场
        'stop': -0.08,           # -8% 收盘止损
        'gap_cap': 1.08,         # 次日开盘 > 前收×1.08 放弃
        't3_block': True,        # T3_RECLAIM 硬屏蔽
        'priced_in_block': True, # PRICED_IN 硬排除
    }

    # ---------- 组合控制 ----------
    PORTFOLIO = {
        'core_pos': 0.15, 'test_pos': 0.12, 'probe_pos': 0.05,
        'max_hold_min': 5, 'max_hold_max': 8,
        'theme_cap': 0.30,
    }

    # ---------- 数据置信 ----------
    EXPECTED_ANN_YEAR = '2026'   # 2025H1 回测时 ann_ok=0（与基线口径一致）
    CONF_MULT = 1.00
    RISK_PEN = 0.40

    # ---------- 风险 ----------
    RISK_PEN_W = 0.40

    # ---------- 主题 ----------
    THEME_ADJ_MAX = 5.0
    THEME_WHITELIST = [
        ('算力', '算力/AI'), ('AI', '算力/AI'), ('人工智能', '算力/AI'),
        ('机器人', '机器人'), ('减速器', '机器人'), ('伺服', '机器人'),
        ('半导体', '半导体'), ('芯片', '半导体'), ('集成电路', '半导体'),
        ('光模块', '光模块/光通信'), ('光通信', '光模块/光通信'), ('CPO', '光模块/光通信'),
        ('创新药', '创新药'), ('医药', '创新药'),
        ('出海', '出海制造'), ('出口', '出海制造'),
        ('低空', '低空经济'), ('eVTOL', '低空经济'),
        ('固态电池', '固态电池'), ('锂电', '锂电/新能源'),
        ('电网', '电力设备'), ('特高压', '电力设备'),
        ('黄金', '贵金属'),
    ]

    # ---------- 事件关键词 ----------
    EVENT_KEYWORDS = ('业绩预告', '业绩快报', '年度报告', '半年度报告',
                      '季度报告', '分红', '回购', '重大合同')

    # ---------- 行业周期性 ----------
    CYCLICAL_KEYWORDS = {'半导体', '存储', '面板', '显示屏', '化学', '化工',
                         '光伏', '太阳能', '新能源', '设备', '材料', '电子',
                         '芯片', '集成电路', '内存', '闪存', '显示', '锂电'}

    # ---------- 现金流季节性 ----------
    SEASONAL_KEYWORDS = {'白酒', '食品', '饮料', '啤酒', '乳业', '农业', '养殖',
                         '旅游', '餐饮', '零售', '服装', '服饰', '家电', '纺服',
                         '商业', '消费', '日化', '美容'}

    # ---------- EQ 惩罚 ----------
    EQ_PENALTY = {'LOW': 15.0, 'MIXED': 8.0, 'ONE_OFF_DOMINATED': 20.0}

    # ---------- 现金流六分类分值 ----------
    CFCS_SCORE = {'cfcs_healthy': 85, 'cfcs_seasonal': 60, 'cfcs_inventory': 70,
                  'cfcs_wcap': 65, 'cfcs_receivable': 35, 'cfcs_structural': 18}

    # ---------- 市场状态乘数 ----------
    MARKET_MULT = {'strong': 1.15, 'bull': 1.05, 'neutral': 1.00,
                   'recovery': 0.95, 'weak': 0.85, 'bear': 0.70}


# ============================================================
# 工具函数
# ============================================================
def _valid(x):
    try:
        v = float(x)
        return np.isfinite(v)
    except (TypeError, ValueError):
        return False


def safe_score(x, default=50.0):
    return float(x) if _valid(x) else default


def _pct(a, b):
    """a/b - 1，安全除法。"""
    try:
        if b is None or abs(float(b)) < 1e-9:
            return np.nan
        return float(a) / float(b) - 1.0
    except (TypeError, ValueError):
        return np.nan


def calc_atr14(df):
    """Wilder ATR(14)，返回最后一日值。"""
    h, l, c = df['high'], df['low'], df['close']
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    return float(atr.iloc[-1])


def calc_ret_pct(df, window):
    """近 window 日复利涨幅%。"""
    if len(df) < window + 1:
        return np.nan
    pct = df['pct_chg'].iloc[-(window + 1):].iloc[1:]
    return float((np.prod(1 + pct / 100.0) - 1) * 100.0)


def calc_macd(df, fast=12, slow=26, signal=9):
    ema_f = df['close'].ewm(span=fast, adjust=False).mean()
    ema_s = df['close'].ewm(span=slow, adjust=False).mean()
    dif = ema_f - ema_s
    dea = dif.ewm(span=signal, adjust=False).mean()
    return float(dif.iloc[-1]), float(dea.iloc[-1])


# ============================================================
# 数据层
# ============================================================
def load_bench(end_date=None, lookback=120):
    """指数基准（sh000001 优先 sh999999 兜底），带缓存。"""
    key = (end_date, lookback)
    if key in BENCH_CACHE:
        return BENCH_CACHE[key]
    df = None
    for name in ('sh999999', 'sh000001'):
        for d in TDX_DAY_CANDIDATES:
            fp = os.path.join(d, name + '.day')
            if os.path.exists(fp):
                try:
                    df = parse_tdx_day_file(fp)
                except Exception:
                    df = None
                break
        if df is not None and len(df) > 0:
            break
    if df is None or len(df) == 0:
        BENCH_CACHE[key] = pd.DataFrame()
        return BENCH_CACHE[key]
    df = df.sort_values('trade_date').reset_index(drop=True)
    if end_date:
        df = df[df['trade_date'] <= str(end_date)].reset_index(drop=True)
    if len(df) > lookback:
        df = df.iloc[-lookback:].reset_index(drop=True)
    df = add_ma(df, windows=(5, 10, 20, 60))
    BENCH_CACHE[key] = df
    return df


def load_daily_for(code6, end_date, lookback=300):
    """单票日线（bts.load_daily 封装），len<120 → None。"""
    ts = to_ts_code(code6)
    df = load_daily(ts, end_date=end_date, lookback_bars=lookback)
    if df is None or len(df) < 120:
        return None
    df = df.sort_values('trade_date').reset_index(drop=True)
    df = add_ma(df, windows=(5, 10, 20, 60))
    df = add_vol_ma(df, windows=(5, 10, 20))
    df = add_rsi(df, n=14)
    df['pct_chg'] = df['close'].pct_change() * 100.0
    return df


def _treasure_pool(period):
    """treasure 财报缓存回退：fin_ind_{period}_full.parquet 缺失时合并 treasure_fin_ind_*.parquet。"""
    rows = []
    for fp in glob.glob(os.path.join(CACHE_DAILY, 'treasure_fin_ind_*.parquet')):
        try:
            dft = pd.read_parquet(fp)
        except Exception:
            continue
        if dft is None or len(dft) == 0:
            continue
        if 'end_date' in dft.columns:
            sem = {'2025H1': ('20250630', '202506'), '2026H1': ('20260630', '202606')}.get(period)
            if sem:
                dft = dft[dft['end_date'].astype(str).str.startswith(sem[1])]
        if len(dft) > 0:
            rows.append(dft)
    if not rows:
        return pd.DataFrame()
    pool = pd.concat(rows, ignore_index=True).drop_duplicates(subset=['ts_code'], keep='last')
    return pool


def load_pool(period='2026H1'):
    """
    财报池加载：
      1) fin_ind_{period}_full.parquet（主路径）
      2) treasure_fin_ind_{sym}_{mkt}.parquet 合并回退
      3) zhongbao_hunt / express 兜底
    """
    # 主路径
    fp = os.path.join(CACHE_DAILY, f'fin_ind_{period}_full.parquet')
    if os.path.exists(fp):
        try:
            pool = pd.read_parquet(fp)
            if pool is not None and len(pool) > 0:
                return pool
        except Exception:
            pass
    # treasure 回退
    pool = _treasure_pool(period)
    if len(pool) > 0:
        return pool
    # 兜底：zhongbao_hunt / express
    for name in (f'zhongbao_hunt_{period}.parquet', f'express_{period}.parquet'):
        fp2 = os.path.join(CACHE_DAILY, name)
        if os.path.exists(fp2):
            try:
                dft = pd.read_parquet(fp2)
                if dft is not None and len(dft) > 0:
                    return dft
            except Exception:
                pass
    return pd.DataFrame()


# ============================================================
# 事件分类
# ============================================================
def classify_event(fin_row, stock_basic=None):
    """
    D1~D5 事件类型 + A/B 侧面。
      D1 业绩预告类 / D2 业绩快报 / D3 正式财报 / D4 分红送转 / D5 其他
      A 侧 = 景气成长共振；B 侧 = 预期差/反转
    返回 (etype, side, desc)
    """
    ann = str(fin_row.get('ann_date', '') or '')
    np_yoy = fin_row.get('netprofit_yoy', np.nan)
    rev_yoy = fin_row.get('revenue_yoy', np.nan)
    ded_yoy = fin_row.get('deduct_np_yoy', np.nan)

    # 事件类型：优先 ann_type 文本，缺失按期报推断
    etype = 'D3'
    ann_type = str(fin_row.get('ann_type', '') or '')
    for kw in PeaConfig.EVENT_KEYWORDS:
        if kw in ann_type:
            if '预告' in kw:
                etype = 'D1'
            elif '快报' in kw:
                etype = 'D2'
            elif '报告' in kw:
                etype = 'D3'
            elif kw in ('分红', '回购', '重大合同'):
                etype = 'D5'
            break
    else:
        # 无文本时按 end_date 与 ann_date 间隔推断（≤20 自然日 → 预告/快报）
        end = str(fin_row.get('end_date', '') or '')
        if _valid(end) and len(ann) == 8:
            try:
                gap = (pd.Timestamp(ann) - pd.Timestamp(end)).days
                if 0 <= gap <= 20:
                    etype = 'D2' if gap <= 12 else 'D1'
            except Exception:
                pass

    # A/B 侧面：共振/反转计数
    signals_up = 0
    signals_down = 0
    if _valid(np_yoy):
        if np_yoy >= 30:
            signals_up += 1
        elif np_yoy < -10:
            signals_down += 1
    if _valid(rev_yoy):
        if rev_yoy >= 20:
            signals_up += 1
        elif rev_yoy < 0:
            signals_down += 1
    if _valid(ded_yoy):
        if ded_yoy >= 30:
            signals_up += 1
        elif ded_yoy < -10:
            signals_down += 1

    if signals_up >= 2:
        side = 'A'
        desc = '景气成长共振'
    elif signals_down >= 2 and signals_up == 0:
        side = 'B'
        desc = '困境反转候选'
    elif signals_up == 1 and signals_down == 0:
        side = 'A'
        desc = '单维成长确认'
    else:
        side = 'B'
        desc = '预期差博弈'

    return etype, side, desc


# ============================================================
# 基本面信号
# ============================================================
def fundamental_quality(fin_row):
    """FQ 基本面质量 0~100（净利/营收/ROE 加权）。"""
    parts = []
    np_yoy = fin_row.get('netprofit_yoy', np.nan)
    rev_yoy = fin_row.get('revenue_yoy', np.nan)
    roe = fin_row.get('roe', np.nan)

    if _valid(np_yoy):
        if np_yoy >= 100:
            parts.append(95)
        elif np_yoy >= 50:
            parts.append(88)
        elif np_yoy >= 30:
            parts.append(80)
        elif np_yoy >= 15:
            parts.append(70)
        elif np_yoy >= 0:
            parts.append(58)
        elif np_yoy >= -20:
            parts.append(40)
        else:
            parts.append(18)
    if _valid(rev_yoy):
        if rev_yoy >= 50:
            parts.append(90)
        elif rev_yoy >= 25:
            parts.append(80)
        elif rev_yoy >= 10:
            parts.append(68)
        elif rev_yoy >= 0:
            parts.append(55)
        else:
            parts.append(35)
    if _valid(roe):
        if roe >= 15:
            parts.append(88)
        elif roe >= 10:
            parts.append(75)
        elif roe >= 6:
            parts.append(62)
        elif roe >= 0:
            parts.append(45)
        else:
            parts.append(25)

    if not parts:
        return 50.0
    return float(np.mean(parts))


def calc_rqs(fin_row, industry=''):
    """RQS 报告质量七维加权 0~100。"""
    c = PeaConfig.W_RQS
    np_yoy = fin_row.get('netprofit_yoy', np.nan)
    rev_yoy = fin_row.get('revenue_yoy', np.nan)
    gm = fin_row.get('gross_margin', np.nan)
    gm_yoy = fin_row.get('gross_margin_yoy', np.nan)
    ocf = fin_row.get('ocf', np.nan)
    np_v = fin_row.get('np', np.nan)
    debt = fin_row.get('debt_ratio', np.nan)
    q1_yoy = fin_row.get('q1_np_yoy', np.nan)

    # profit
    if np_yoy >= 80:
        v = 92
    elif np_yoy >= 50:
        v = 84
    elif np_yoy >= 30:
        v = 74
    elif np_yoy >= 15:
        v = 62
    elif np_yoy >= 0:
        v = 50
    elif np_yoy >= -20:
        v = 32
    else:
        v = 12
    s_profit = v

    # revenue
    if rev_yoy >= 40:
        v = 90
    elif rev_yoy >= 20:
        v = 78
    elif rev_yoy >= 10:
        v = 65
    elif rev_yoy >= 0:
        v = 50
    elif rev_yoy >= -10:
        v = 35
    else:
        v = 15
    s_rev = v

    # margin
    if _valid(gm_yoy) and _valid(gm):
        if gm_yoy >= 3 and gm >= 25:
            v = 88
        elif gm_yoy >= 1:
            v = 72
        elif gm_yoy >= 0:
            v = 58
        elif gm_yoy >= -2:
            v = 42
        else:
            v = 22
    else:
        v = 50
    s_margin = v

    # cash
    if _valid(ocf) and _valid(np_v) and np_v and abs(float(np_v)) > 1e-6:
        ratio = float(ocf) / float(np_v)
        if ratio >= 1.2:
            v = 90
        elif ratio >= 0.8:
            v = 75
        elif ratio >= 0.5:
            v = 60
        elif ratio >= 0:
            v = 40
        else:
            v = 15
    else:
        v = 50
    s_cash = v

    # balance
    if _valid(debt):
        if debt <= 30:
            v = 85
        elif debt <= 50:
            v = 70
        elif debt <= 65:
            v = 55
        else:
            v = 30
    else:
        v = 50
    s_bal = v

    # persist（持续性：本期 vs Q1 方向一致）
    if _valid(np_yoy) and _valid(q1_yoy):
        if np_yoy > 0 and q1_yoy > 0 and np_yoy >= q1_yoy:
            v = 90
        elif np_yoy > 0 and q1_yoy > 0:
            v = 72
        elif np_yoy > 0:
            v = 62
        elif np_yoy <= 0 and q1_yoy <= 0:
            v = 28
        else:
            v = 42
    else:
        v = 50
    s_persist = v

    # cycle
    is_cyc = any(kw in (industry or '') for kw in PeaConfig.CYCLICAL_KEYWORDS)
    s_cycle = 50 if is_cyc else 80

    total = (s_profit * c['profit'] + s_rev * c['revenue'] + s_margin * c['margin'] +
             s_cash * c['cash'] + s_bal * c['balance'] + s_persist * c['persist'] +
             s_cycle * c['cycle'])
    return float(total)


def expect_gap_score(fin_row, bench_df=None):
    """GAP 预期缺口 0~100：业绩幅度 × 首次披露溢价。"""
    np_yoy = fin_row.get('netprofit_yoy', np.nan)
    rev_yoy = fin_row.get('revenue_yoy', np.nan)
    etype, _, _ = classify_event(fin_row)

    base = 50.0
    if _valid(np_yoy):
        if np_yoy >= 100:
            base = 92
        elif np_yoy >= 60:
            base = 84
        elif np_yoy >= 35:
            base = 74
        elif np_yoy >= 20:
            base = 64
        elif np_yoy >= 8:
            base = 54
        elif np_yoy >= 0:
            base = 45
        else:
            base = 28
    # 预告/快报有信息溢价，正式财报已被预期部分消化
    if etype in ('D1', 'D2'):
        base = min(96.0, base + 8.0)
    elif etype == 'D3':
        base = min(96.0, base + 2.0)
    # 营收与利润同向增强
    if _valid(rev_yoy) and _valid(np_yoy) and rev_yoy > 10 and np_yoy > 0 and rev_yoy <= np_yoy:
        base = min(96.0, base + 5.0)
    return float(base)


def overheat_penalty(df):
    """过热惩罚 0~40：近5日涨幅 / RSI / ma20 乖离。"""
    if df is None or len(df) < 20:
        return 0.0
    oh = 0.0
    r5 = calc_ret_pct(df, 5)
    if _valid(r5):
        if r5 > 25:
            oh += 18
        elif r5 > 15:
            oh += 12
        elif r5 > 10:
            oh += 6
    rsi = df['rsi14'].iloc[-1] if 'rsi14' in df.columns else np.nan
    if _valid(rsi):
        if rsi > 85:
            oh += 12
        elif rsi > 78:
            oh += 7
    ma20 = df['ma20'].iloc[-1] if 'ma20' in df.columns else np.nan
    c = df['close'].iloc[-1]
    bias = _pct(c, ma20)
    if _valid(bias):
        if bias > 0.20:
            oh += 10
        elif bias > 0.15:
            oh += 6
    return float(min(40.0, oh))


def calc_ars(daily, ann_idx, cur_idx):
    """ARS 公告反应评分 0~100：T0 强度 + T+3 持续 + T+5 缩量横盘 + 利空不跌。"""
    if daily is None or ann_idx is None or ann_idx >= len(daily) or ann_idx < 1:
        return 50.0
    d = daily
    ann_ret = float(d['pct_chg'].iloc[ann_idx]) if _valid(d['pct_chg'].iloc[ann_idx]) else 0.0

    # T0 反应
    if ann_ret > 7:
        s0 = 92
    elif ann_ret > 5:
        s0 = 82
    elif ann_ret > 3:
        s0 = 70
    elif ann_ret > 1:
        s0 = 58
    elif ann_ret > -2:
        s0 = 45
    else:
        s0 = 25

    # T+3 持续
    s3 = 50.0
    if ann_idx + 3 < len(d):
        r3 = (np.prod(1 + d['pct_chg'].iloc[ann_idx + 1: ann_idx + 4] / 100.0) - 1) * 100
        if _valid(r3):
            if r3 > 8:
                s3 = 88
            elif r3 > 4:
                s3 = 76
            elif r3 > 0:
                s3 = 62
            elif r3 > -4:
                s3 = 45
            else:
                s3 = 25

    # T+5 缩量横盘（回撤 <3% 且缩量）
    s5 = 50.0
    if ann_idx + 5 < len(d):
        seg = d.iloc[ann_idx + 1: ann_idx + 6]
        r5 = (np.prod(1 + seg['pct_chg'] / 100.0) - 1) * 100
        vol_ratio = float(seg['vol'].mean() / max(d['vol_ma5'].iloc[ann_idx], 1e-9)) if 'vol_ma5' in d.columns else np.nan
        if _valid(r5) and abs(r5) < 3.0:
            if _valid(vol_ratio) and vol_ratio < 0.85:
                s5 = 85
            else:
                s5 = 68

    # 利空不跌（B 类反转加分）
    s_sn = 50.0
    if ann_ret < -2 and ann_idx + 3 < len(d):
        r3 = (np.prod(1 + d['pct_chg'].iloc[ann_idx + 1: ann_idx + 4] / 100.0) - 1) * 100
        if _valid(r3) and r3 > 0:
            s_sn = 82

    total = s0 * 0.40 + s3 * 0.30 + s5 * 0.20 + s_sn * 0.10
    return float(total)


# ============================================================
# 技术信号（TQS 六维）
# ============================================================
def trend_structure(df):
    """趋势结构 0~100。"""
    if df is None or len(df) < 60:
        return 50.0
    c = df['close'].iloc[-1]
    ma5, ma10, ma20, ma60 = df['ma5'].iloc[-1], df['ma10'].iloc[-1], df['ma20'].iloc[-1], df['ma60'].iloc[-1]
    s = 40.0
    if c > ma5:
        s += 8
    if ma5 > ma10:
        s += 10
    if ma10 > ma20:
        s += 12
    if c > ma60:
        s += 10
    slope20 = df['ma20_slope'].iloc[-1] if 'ma20_slope' in df.columns else np.nan
    if _valid(slope20):
        if slope20 > 0.02:
            s += 12
        elif slope20 > 0:
            s += 6
        elif slope20 < -0.02:
            s -= 10
    if _valid(ma60) and c > ma60 * 1.05:
        s += 5
    return float(min(100.0, max(0.0, s)))


def volume_structure(df):
    """量能结构 0~100。"""
    if df is None or len(df) < 25:
        return 50.0
    vol = df['vol']
    v5, v20 = df['vol_ma5'].iloc[-1], df['vol_ma20'].iloc[-1]
    s = 50.0
    if _valid(v5) and _valid(v20) and v20 > 0:
        r = v5 / v20
        if 1.1 <= r <= 2.0:
            s += 22          # 温和放量
        elif 2.0 < r <= 3.0:
            s += 10          # 大幅放量（存疑）
        elif 0.7 <= r < 1.1:
            s += 8           # 平量
        elif r < 0.5:
            s += 2           # 极度缩量
        elif r > 3.5:
            s -= 8           # 天量
    # 近5日量价配合：涨日放量跌日缩量
    seg = df.iloc[-5:]
    up_v = seg.loc[seg['pct_chg'] > 0, 'vol'].mean() if (seg['pct_chg'] > 0).any() else np.nan
    dn_v = seg.loc[seg['pct_chg'] < 0, 'vol'].mean() if (seg['pct_chg'] < 0).any() else np.nan
    if _valid(up_v) and _valid(dn_v) and dn_v > 0:
        r = up_v / dn_v
        if r > 1.15:
            s += 18
        elif r > 1.0:
            s += 8
        elif r < 0.8:
            s -= 6
    return float(min(100.0, max(0.0, s)))


def pullback_quality(df):
    """回踩质量 0~100：深度/缩量/企稳。"""
    if df is None or len(df) < 25:
        return 50.0
    c = df['close'].iloc[-1]
    hi20 = df['high'].iloc[-20:].max()
    drawdown = _pct(c, hi20)
    s = 50.0
    if _valid(drawdown):
        if -0.08 <= drawdown <= -0.02:
            s += 20          # 合理回踩
        elif 0 < drawdown <= 0.03:
            s += 15          # 高位横盘
        elif -0.15 <= drawdown < -0.08:
            s += 5
        elif drawdown < -0.15:
            s -= 10          # 深跌
        else:
            s += 3
    # 回踩缩量
    vol5 = df['vol_ma5'].iloc[-1]
    vol20 = df['vol_ma20'].iloc[-1]
    if _valid(vol5) and _valid(vol20) and vol20 > 0 and vol5 / vol20 < 0.85:
        s += 15
    # 企稳：不破 ma20×0.97
    ma20 = df['ma20'].iloc[-1]
    if _valid(ma20) and c > ma20 * 0.97:
        s += 15
    return float(min(100.0, max(0.0, s)))


def breakout_quality(df):
    """突破质量 0~100：20日新高 + 量能确认。"""
    if df is None or len(df) < 25:
        return 50.0
    c = df['close'].iloc[-1]
    hi20 = df['high'].iloc[-21:-1].max()
    s = 50.0
    if _valid(hi20) and c > hi20:
        s += 25
        vr = df['vol'].iloc[-1] / max(df['vol_ma5'].iloc[-2], 1e-9) if len(df) > 5 else np.nan
        if _valid(vr):
            if 1.5 <= vr <= 3.0:
                s += 20      # 有效放量
            elif vr > 4.0:
                s += 5       # 天量存疑
            elif vr > 1.0:
                s += 12
    # 平台紧凑度：近20日振幅
    seg = df.iloc[-20:]
    amp = (seg['high'].max() - seg['low'].min()) / max(seg['low'].min(), 1e-9)
    if _valid(amp):
        if amp < 0.10:
            s += 15          # 高度紧凑
        elif amp < 0.18:
            s += 8
    return float(min(100.0, max(0.0, s)))


def momentum_score(df):
    """动量 0~100：RSI + 近10日涨幅。"""
    if df is None or len(df) < 15:
        return 50.0
    s = 50.0
    rsi = df['rsi14'].iloc[-1] if 'rsi14' in df.columns else np.nan
    if _valid(rsi):
        if 55 <= rsi <= 72:
            s += 20          # 健康强势区
        elif 72 < rsi <= 80:
            s += 10
        elif rsi > 85:
            s -= 5
        elif 45 <= rsi < 55:
            s += 5
        elif rsi < 35:
            s -= 5
    r10 = calc_ret_pct(df, 10)
    if _valid(r10):
        if 5 <= r10 <= 20:
            s += 18
        elif 0 < r10 < 5:
            s += 8
        elif r10 > 25:
            s -= 5
        elif -10 <= r10 <= 0:
            s += 2
    return float(min(100.0, max(0.0, s)))


def support_structure(df):
    """支撑结构 0~100。"""
    if df is None or len(df) < 60:
        return 50.0
    c = df['close'].iloc[-1]
    ma20, ma60 = df['ma20'].iloc[-1], df['ma60'].iloc[-1]
    s = 50.0
    if _valid(ma20):
        bias = _pct(c, ma20)
        if -0.02 <= bias <= 0.06:
            s += 20          # 贴线蓄势
        elif 0.06 < bias <= 0.12:
            s += 10
        elif bias < -0.05:
            s -= 8
    if _valid(ma60) and c > ma60:
        s += 15
    # 近60日低点抬升
    lo30 = df['low'].iloc[-30:].min()
    lo60 = df['low'].iloc[-60:].min()
    if _valid(lo30) and _valid(lo60) and lo30 >= lo60:
        s += 12
    return float(min(100.0, max(0.0, s)))


def calc_tqs(df):
    """TQS 六维加权 0~100。"""
    w = PeaConfig.W_TQS
    parts = {
        'trend': trend_structure(df),
        'volume': volume_structure(df),
        'pullback': pullback_quality(df),
        'breakout': breakout_quality(df),
        'momentum': momentum_score(df),
        'support': support_structure(df),
    }
    return float(sum(parts[k] * w[k] for k in w))


def risk_score(df):
    """风险分 0~100（低分=高风险）：ATR/振幅/乖离。"""
    if df is None or len(df) < 30:
        return 50.0
    s = 75.0
    atr = calc_atr14(df)
    c = df['close'].iloc[-1]
    if _valid(atr) and c > 0:
        atr_pct = atr / c * 100
        if atr_pct < 2.5:
            s += 12
        elif atr_pct > 5.0:
            s -= 15
        elif atr_pct > 4.0:
            s -= 8
    # 近20日振幅
    seg = df.iloc[-20:]
    amp = (seg['high'].max() - seg['low'].min()) / max(seg['low'].min(), 1e-9)
    if _valid(amp):
        if amp > 0.30:
            s -= 15
        elif amp > 0.20:
            s -= 8
        elif amp < 0.12:
            s += 8
    # ma20 乖离
    ma20 = df['ma20'].iloc[-1]
    if _valid(ma20) and ma20 > 0:
        bias = abs(_pct(c, ma20))
        if bias > 0.18:
            s -= 12
        elif bias > 0.12:
            s -= 6
    return float(min(100.0, max(0.0, s)))


# ============================================================
# 现金流语境（v22 六分类）
# ============================================================
def _load_industry_map():
    if 'map' in _INDUSTRY_MAP_CACHE:
        return _INDUSTRY_MAP_CACHE['map']
    m = {}
    try:
        basic = load_stock_basic()
        if basic is not None and len(basic) > 0:
            code_col = 'ts_code' if 'ts_code' in basic.columns else ('code' if 'code' in basic.columns else None)
            ind_col = 'industry' if 'industry' in basic.columns else None
            if code_col and ind_col:
                for _, r in basic.iterrows():
                    code6 = str(r[code_col]).split('.')[0]
                    m[code6] = str(r[ind_col])
    except Exception:
        pass
    _INDUSTRY_MAP_CACHE['map'] = m
    return m


def _detect_cyclical(industry):
    return any(kw in (industry or '') for kw in PeaConfig.CYCLICAL_KEYWORDS)


def _detect_seasonal(industry):
    return any(kw in (industry or '') for kw in PeaConfig.SEASONAL_KEYWORDS)


def cashflow_context_engine(fin_row, industry=''):
    """
    现金流六分类 → (cfcs_key, score, adj, desc)
      healthy 85 / inventory 70 / wcap 65 / seasonal 60 / receivable 35 / structural 18
    """
    ocf = fin_row.get('ocf', np.nan)
    np_v = fin_row.get('np', np.nan)
    rev_yoy = fin_row.get('revenue_yoy', np.nan)
    ded_yoy = fin_row.get('deduct_np_yoy', np.nan)

    ratio = np.nan
    if _valid(ocf) and _valid(np_v) and abs(float(np_v)) > 1e-6:
        ratio = float(ocf) / float(np_v)

    acc_ratio = np.nan
    np_yoy = fin_row.get('netprofit_yoy', np.nan)
    if _valid(np_yoy) and _valid(ded_yoy):
        # 非经常损益占比近似：净利与扣非增速差
        acc_ratio = max(0.0, float(np_yoy) - float(ded_yoy))

    if _valid(ratio) and ratio >= 0.8:
        key, desc = 'cfcs_healthy', '经营现金流覆盖充分'
    elif _valid(ratio) and 0.3 <= ratio < 0.8:
        if _detect_seasonal(industry):
            key, desc = 'cfcs_seasonal', '季节性现金流（消费/周期节律）'
        elif _valid(rev_yoy) and rev_yoy > 25:
            key, desc = 'cfcs_inventory', '高增长备货占用（存货驱动）'
        else:
            key, desc = 'cfcs_wcap', '营运资本波动'
    elif _valid(ratio) and 0 <= ratio < 0.3:
        if _valid(acc_ratio) and acc_ratio > 20:
            key, desc = 'cfcs_receivable', '应收占比偏高（含一次性损益）'
        else:
            key, desc = 'cfcs_receivable', '现金回款偏弱'
    else:
        key, desc = 'cfcs_structural', '结构性现金流缺陷或数据缺失'

    score = float(PeaConfig.CFCS_SCORE[key])

    # adj：扣非增速与净利增速方向一致性
    adj = 0.0
    if _valid(ded_yoy) and _valid(fin_row.get('netprofit_yoy', np.nan)):
        np_yoy = float(fin_row['netprofit_yoy'])
        if ded_yoy > 0 and np_yoy > 0 and abs(ded_yoy - np_yoy) < 15:
            adj += 6.0
        elif ded_yoy < -10 < np_yoy:
            adj -= 8.0
    return key, score, adj, desc


def earnings_quality_context(fin_row):
    """盈利质量 → 'HIGH'/'MIXED'/'LOW'/'ONE_OFF_DOMINATED'。"""
    ocf = fin_row.get('ocf', np.nan)
    np_v = fin_row.get('np', np.nan)
    ded_yoy = fin_row.get('deduct_np_yoy', np.nan)
    np_yoy = fin_row.get('netprofit_yoy', np.nan)

    ratio = np.nan
    if _valid(ocf) and _valid(np_v) and abs(float(np_v)) > 1e-6:
        ratio = float(ocf) / float(np_v)

    one_off = False
    if _valid(ded_yoy) and _valid(np_yoy):
        if np_yoy > 30 and ded_yoy < 0:
            one_off = True
        elif np_yoy - float(ded_yoy) > 40:
            one_off = True

    if one_off:
        return 'ONE_OFF_DOMINATED'
    if _valid(ratio):
        if ratio >= 0.8:
            return 'HIGH'
        if ratio >= 0.3:
            return 'MIXED'
        return 'LOW'
    return 'MIXED'


# ============================================================
# 评分链：置信 / 市场 / 主题 / 风险 / PEA 总分
# ============================================================
def data_confidence(fin_row, ann_date, expected_year=None):
    """数据置信 0~100。ann_ok 与基线口径一致（EXPECTED_ANN_YEAR 参数化）；
    expected_year 可显式指定预期季年（回测预期季口径对照列用）。"""
    conf = 60.0
    fields = ['netprofit_yoy', 'revenue_yoy', 'roe', 'gross_margin', 'ocf', 'np', 'deduct_np_yoy']
    missing = sum(1 for f in fields if not _valid(fin_row.get(f, np.nan)))
    if missing >= 5:
        conf = min(conf, 80.0)
    elif missing >= 3:
        conf = min(conf, 90.0)
    ann = str(ann_date or '')[:8]
    ey = str(expected_year) if expected_year else PeaConfig.EXPECTED_ANN_YEAR
    ann_ok = ann[:4] == ey
    if ann_ok:
        conf = min(100.0, conf + 25.0)
    else:
        conf = min(conf, 60.0)
    # 现金流缺失降档
    if not _valid(fin_row.get('ocf', np.nan)):
        conf = min(conf, 85.0)
    return float(conf)


def _market_regime_6(bench_df):
    """6 档市场状态：strong/bull/neutral/recovery/weak/bear。"""
    if bench_df is None or len(bench_df) < 60:
        return 'neutral'
    c = float(bench_df['close'].iloc[-1])
    ma20 = float(bench_df['ma20'].iloc[-1])
    ma60 = float(bench_df['ma60'].iloc[-1])
    r20 = calc_ret_pct(bench_df, 20)
    above = (c > ma20) + (ma20 > ma60)
    if above == 2 and _valid(r20) and r20 > 5:
        return 'strong'
    if above == 2:
        return 'bull'
    if above == 0 and _valid(r20) and r20 < -5:
        return 'bear'
    if above == 0:
        return 'weak'
    if _valid(r20) and -3 <= r20 <= 3:
        return 'neutral'
    return 'recovery'


def market_multiplier(bench_df):
    return PeaConfig.MARKET_MULT.get(_market_regime_6(bench_df), 1.00)


def theme_score(code6, stock_basic=None):
    """主题 adj ±5：名称/行业命中白名单。"""
    name = ''
    ind = ''
    try:
        basic = load_stock_basic()
        if basic is not None and len(basic) > 0:
            hit = basic[basic['ts_code'].astype(str).str.split('.').str[0] == str(code6)]
            if len(hit) > 0:
                r = hit.iloc[0]
                name = str(r.get('name', ''))
                ind = str(r.get('industry', ''))
    except Exception:
        pass
    text = name + ind
    for kw, theme in PeaConfig.THEME_WHITELIST:
        if kw.lower() in text.lower():
            return PeaConfig.THEME_ADJ_MAX, theme
    return 0.0, ''


def relative_risk_score(vol_ratio20):
    """相对风险 0~40：vol_ratio20 <1.2 → max(vol×0.7,10)；>1.5 → min(40, vol×1.3)；else 25。"""
    vol_score = safe_score(vol_ratio20 * 20, 25)
    if not _valid(vol_ratio20):
        return 25.0
    if vol_ratio20 < 1.2:
        return float(max(vol_score * 0.7, 10.0))
    if vol_ratio20 > 1.5:
        return float(min(40.0, vol_score * 1.3))
    return 25.0


def calc_pea_score(norm_score, conf, rel_risk, mkt_mult, theme_adj,
                   decay, refresh, eq_penalty):
    """
    PEA 评分链（与实证口径一致）：
      raw = Σ 权重×维度（A: W_A / B: W_B，scan 层完成）
      norm = 组内 pct rank×100（len≥3 才启用，scan 层完成）
      base = norm×(conf/100)×(1−rel_risk/100×0.40)×mkt_mult + theme_adj
      alpha = base×max(0.60, 1−decay) + refresh − eq_penalty   （clip 0~100）
    """
    base = float(norm_score) * (conf / 100.0) * (1.0 - rel_risk / 100.0 * PeaConfig.RISK_PEN) * mkt_mult
    base += theme_adj
    alpha = base * max(0.60, 1.0 - decay) + refresh - eq_penalty
    return float(min(100.0, max(0.0, alpha)))


# ============================================================
# 状态机（硬规则内嵌）
# ============================================================
def grade_pea(alpha, ees, ts, risk, conf, fq, overheat, trigger_type,
              absorption_state, side, rqs, event_age):
    """
    分级状态机。硬规则判定顺序：
      ① PRICED_IN            → REJECT（吸收门控硬排除）
      ② T3_RECLAIM           → REJECT（实证硬屏蔽 fwd20 -3.05%/win31%/n=77）
      ③ fq<25                → REJECT（B_REVERSAL & rqs≥70 豁免）
      ④ conf<50              → WATCH
      ⑤ ts==0                → alpha≥60 WAIT_CONFIRM else WATCH
      ⑥ overheat>25          → WAIT_PULLBACK / WATCH
      ⑦ T1_BREAKOUT          → WAIT_PULLBACK / WATCH（追高控制）
      ⑧ CORE / TEST / PROBE 门槛
      ⑨ ees<60 → WATCH；ts<70 → WAIT_PULLBACK/WATCH；alpha≥60 → WAIT_CONFIRM；WATCH
    """
    g = PeaConfig.GATE

    # ① 吸收门控（PEA 核心硬规则）
    if absorption_state == 'PRICED_IN':
        return 'REJECT', 'PRICED_IN（价格已完全吸收事件）'

    # ② T3_RECLAIM 硬屏蔽
    if trigger_type == 'T3_RECLAIM':
        if alpha >= g['watch_alpha']:
            return 'WAIT_CONFIRM', 'T3_RECLAIM 硬屏蔽（实证负超额），观察确认'
        return 'WATCH', 'T3_RECLAIM 硬屏蔽（实证负超额）'

    # ③ 基本面下限
    if _valid(fq) and fq < g['fq_floor_reject']:
        if not (side == 'B' and _valid(rqs) and rqs >= g['rqs_high_grade']):
            return 'REJECT', f'FQ={fq:.0f} 低于下限'

    # ④ 数据置信
    if _valid(conf) and conf < g['conf_watch']:
        return 'WATCH', f'置信度 {conf:.0f} 不足'

    # ⑤ 无触发
    if trigger_type == 'NO_TRIGGER':
        if alpha >= g['watch_alpha']:
            return 'WAIT_CONFIRM', '无触发但 alpha 达标'
        return 'WATCH', '无触发信号'

    # ⑥ 过热
    if _valid(overheat) and overheat > g['overheat_pullback']:
        if alpha >= g['watch_alpha']:
            return 'WAIT_PULLBACK', f'过热 {overheat:.0f}，等待回踩'
        return 'WATCH', f'过热 {overheat:.0f}'

    # ⑦ T1 突破追高控制
    if trigger_type == 'T1_BREAKOUT':
        if alpha >= g['watch_alpha'] and ees >= g['probe_ees']:
            return 'WAIT_PULLBACK', 'T1 突破后等回踩确认'
        return 'WATCH', 'T1 突破但条件不足'

    # ⑧ CORE / TEST / PROBE（仅 T2_PULLBACK 放行入场）
    if trigger_type == 'T2_PULLBACK':
        if (alpha >= g['core_alpha'] and ees >= g['core_ees'] and ts >= g['core_ts'] and
                risk <= g['core_risk'] and conf >= g['core_conf'] and fq >= g['core_fq'] and
                overheat <= g['core_overheat']):
            return 'CORE', '四门全过：核心仓'
        if alpha >= g['test_alpha'] and ees >= g['test_ees'] and ts >= g['test_ts'] and risk <= g['test_risk']:
            return 'TEST', '三门过：测试仓'
        if alpha >= g['probe_alpha'] and ees >= g['probe_ees']:
            return 'PROBE', '双门过：探针仓'

    # ⑨ 兜底
    if _valid(ees) and ees < 60:
        return 'WATCH', f'执行分 {ees:.0f} 不足'
    if _valid(ts) and ts < g['ts_watch']:
        if alpha >= g['watch_alpha']:
            return 'WAIT_PULLBACK', '触发分不足，等结构完善'
        return 'WATCH', '触发分不足'
    if _valid(alpha) and alpha >= g['watch_alpha']:
        return 'WAIT_CONFIRM', 'alpha 达标等确认'
    return 'WATCH', '综合条件不足'


def apply_portfolio_cap(df):
    """
    组合控制：PROBE(0)/TEST(1)/CORE(2) 降序取前 N；
    N = max(5, min(8, 候选数))；同主题 ≤30%。
    """
    p = PeaConfig.PORTFOLIO
    if df is None or len(df) == 0:
        return df
    d = df.copy()
    rank_map = {'PROBE': 0, 'TEST': 1, 'CORE': 2}
    d['_rank'] = d['grade'].map(rank_map).fillna(-1)
    d = d[d['_rank'] >= 0].sort_values(['_rank', 'alpha'], ascending=[False, False])
    max_hold = max(p['max_hold_min'], min(p['max_hold_max'], len(d)))
    picked, theme_cnt = [], defaultdict(int)
    for _, r in d.iterrows():
        if len(picked) >= max_hold:
            break
        theme = r.get('theme', '') or 'NONE'
        if theme != 'NONE' and theme_cnt[theme] + 1 / max(len(picked) + 1, 1) > p['theme_cap'] and \
           sum(1 for x in picked if x.get('theme', '') == theme) / max(len(picked) + 1, 1) >= p['theme_cap']:
            continue
        picked.append(r.to_dict())
        theme_cnt[theme] += 1
    out = pd.DataFrame(picked)
    if len(out) > 0:
        out = out.sort_values(['_rank', 'alpha'], ascending=[False, False]).reset_index(drop=True)
    return out


def position_suggest(grade):
    """仓位建议：CORE 15% / TEST 12% / PROBE 5%。"""
    p = PeaConfig.PORTFOLIO
    return {'CORE': p['core_pos'], 'TEST': p['test_pos'], 'PROBE': p['probe_pos']}.get(grade, 0.0)


# ============================================================
# 范式核心：事件龄 / 价格吸收 / 吸收态 / 触发 / EES
# ============================================================
def calc_event_age(ann_date, scan_date):
    """事件龄（交易日）。"""
    if not ann_date or not scan_date:
        return 0
    ann = str(ann_date)[:8]
    cur = str(scan_date)[:8]
    if cur <= ann:
        return 0
    key = cur[:6]
    if key not in _TRADE_DATES_CACHE:
        try:
            _TRADE_DATES_CACHE[key] = get_trade_dates('20250101', cur)
        except Exception:
            _TRADE_DATES_CACHE[key] = []
    tds = _TRADE_DATES_CACHE[key]
    if not tds:
        return 0
    try:
        idx = int(np.searchsorted(np.array(tds), ann))
        return max(0, len(tds) - idx - 1)
    except Exception:
        return 0


def price_absorption(daily, ann_idx, cur_idx, bench_df=None):
    """
    价格吸收核心度量。返回 dict：
      pre_ret / gap_ann / post_ret / rel_str / r5 / rb5 /
      pre_priced / rel_str_improve / pullback_vol_ratio
    """
    out = {'pre_ret': np.nan, 'gap_ann': np.nan, 'post_ret': np.nan,
           'rel_str': np.nan, 'r5': np.nan, 'rb5': np.nan,
           'pre_priced': False, 'rel_str_improve': False,
           'pullback_vol_ratio': np.nan}
    if daily is None or ann_idx is None or not (0 < ann_idx < len(daily)):
        return out

    # 事前涨幅（ann 前 10 日）
    lo = max(0, ann_idx - 10)
    seg = daily.iloc[lo:ann_idx + 1]
    if len(seg) > 1:
        out['pre_ret'] = (np.prod(1 + seg['pct_chg'] / 100.0) - 1) * 100
    # 事件跳空
    prev_close = daily['close'].iloc[ann_idx - 1]
    ann_open = daily['open'].iloc[ann_idx]
    if _valid(prev_close) and float(prev_close) > 0 and _valid(ann_open):
        out['gap_ann'] = float(ann_open) / float(prev_close) - 1.0

    # 事后涨幅（ann 收盘 → cur_idx 收盘）
    hi = len(daily) - 1
    if cur_idx is not None and _valid(cur_idx):
        hi = min(hi, int(cur_idx))
    if hi > ann_idx:
        seg_post = daily['pct_chg'].iloc[ann_idx + 1: hi + 1]
        if len(seg_post) > 0:
            out['post_ret'] = float((np.prod(1 + seg_post / 100.0) - 1) * 100)

    # 近 5 日收益（个股 / 基准）
    if hi >= 5:
        r5 = (np.prod(1 + daily['pct_chg'].iloc[hi - 4: hi + 1] / 100.0) - 1) * 100
        out['r5'] = float(r5) if _valid(r5) else np.nan
    if bench_df is not None and len(bench_df) >= 5 and 'pct_chg' in bench_df.columns:
        rb5 = (np.prod(1 + bench_df['pct_chg'].iloc[-5:] / 100.0) - 1) * 100
        out['rb5'] = float(rb5) if _valid(rb5) else np.nan

    # 全程相对强势 = post_ret − 同期基准
    bench_ret = np.nan
    if bench_df is not None and len(bench_df) > 0 and 'trade_date' in daily.columns and 'trade_date' in bench_df.columns:
        ann_date = str(daily['trade_date'].iloc[ann_idx])
        cur_date = str(daily['trade_date'].iloc[hi])
        bm = bench_df[(bench_df['trade_date'] >= ann_date) & (bench_df['trade_date'] <= cur_date)]
        if len(bm) > 1:
            br = (np.prod(1 + bm['pct_chg'] / 100.0) - 1) * 100
            bench_ret = float(br) if _valid(br) else np.nan
    if _valid(out['post_ret']) and _valid(bench_ret):
        out['rel_str'] = float(out['post_ret']) - float(bench_ret)
    elif _valid(out['post_ret']):
        out['rel_str'] = float(out['post_ret'])

    # 事前已定价（pre_priced）
    pre_ret = out['pre_ret']
    gap = out['gap_ann']
    out['pre_priced'] = bool(
        (_valid(pre_ret) and pre_ret > PeaConfig.ABSORPTION['pre_priced_ret'] * 100) or
        (_valid(pre_ret) and _valid(gap) and
         pre_ret > PeaConfig.ABSORPTION['pre_priced_ret'] * 100 * 0.6 and
         gap > PeaConfig.ABSORPTION['pre_priced_gap'] * 100)
    )

    # 相对强势改善（近 5 日相对收益 > 全程相对强势）
    if _valid(out['r5']) and _valid(out['rb5']) and _valid(out['rel_str']):
        out['rel_str_improve'] = bool((out['r5'] - out['rb5']) > out['rel_str'])

    # 回踩量比（近 5 日均量 / 事件后段均量）
    if hi >= 10:
        v_recent = daily['vol'].iloc[hi - 4: hi + 1].mean()
        v_post = daily['vol'].iloc[max(ann_idx, hi - 10): hi + 1].mean()
        if _valid(v_post) and float(v_post) > 0 and _valid(v_recent):
            out['pullback_vol_ratio'] = float(v_recent / v_post)

    return out


def calc_absorption_state(pa, event_age):
    """
    吸收三态判定 + Refresh 评分。
      NOT_ABSORBED      rel_str ≤ 3%（事件未吸收，漂移空间最大）
      SECONDARY_CONFIRM 3% < rel_str（次级确认中，趋势延续）
      PRICED_IN         rel_str ≥ 15% 或 pre_priced（硬排除）
    Refresh 4 条件 → 4:4 分 / 3:6 分 / ≥2(非pre_priced):8 分
    返回 (state, decay, refresh, detail)
    """
    c = PeaConfig.ABSORPTION
    rel_str = pa.get('rel_str', np.nan)
    pre_priced = pa.get('pre_priced', False)

    # Decay 按事件龄分层
    decay = 0.25
    for age_cap, d in PeaConfig.DECAY_TIERS:
        if event_age <= age_cap:
            decay = d
            break

    if pre_priced:
        return 'PRICED_IN', decay, 0.0, {'pre_priced': True}
    if _valid(rel_str) and rel_str >= c['priced_in_min'] * 100:
        return 'PRICED_IN', decay, 0.0, {'rel_str': rel_str}
    if _valid(rel_str) and rel_str <= c['not_absorbed_max'] * 100:
        state = 'NOT_ABSORBED'
    elif _valid(rel_str):
        state = 'SECONDARY_CONFIRM'
    else:
        state = 'UNKNOWN'

    # Refresh 4 条件（需日线上下文，由 scan 注入 detail）
    refresh = 0.0
    detail = {'rel_str': rel_str, 'state': state}
    return state, decay, refresh, detail


def refresh_score(daily, pa, state, cur_idx=None):
    """吸收重启（Refresh）4 条件评分：
       ① 收盘 > ma20×0.97  ② 回踩缩量（pullback_vol_ratio ≤0.90）
       ③ 量比 ≥1.30        ④ 相对强势改善
       4 条 → 8 分 / 3 条 → 6 分 / ≥2 条 → 4 分；PRICED_IN 禁 Refresh。
    """
    if state == 'PRICED_IN':
        return 0.0, []
    r = PeaConfig.REFRESH
    conds = []
    if daily is not None and len(daily) > 0:
        c = float(daily['close'].iloc[-1])
        ma20 = float(daily['ma20'].iloc[-1]) if _valid(daily['ma20'].iloc[-1]) else np.nan
        if _valid(ma20) and ma20 > 0 and c > ma20 * r['ma20_hold']:
            conds.append('ma20_hold')
        pvr = pa.get('pullback_vol_ratio', np.nan)
        if _valid(pvr) and pvr <= r['pullback_vol_max']:
            conds.append('pullback_vol_dry')
        vr = daily['vol'].iloc[-1] / max(float(daily['vol_ma5'].iloc[-2]), 1e-9) if len(daily) > 5 else np.nan
        if _valid(vr) and vr >= r['vol_ratio']:
            conds.append('vol_surge')
    if pa.get('rel_str_improve'):
        conds.append('rel_str_improve')
    n = len(conds)
    if n >= 4:
        return r['cond_full'], conds
    if n == 3:
        return r['cond_strong'], conds
    if n >= 2:
        return r['cond_partial'], conds
    return 0.0, conds


def trigger_score(daily, cur_idx=None):
    """
    触发分类评分。
      T1_BREAKOUT 基 80：+ma60突破 5 / +close_pos≥0.7 5 / +1.5≤vr≤2.5 5 / +c>ma20 5
      T3_RECLAIM  基 72：+收复20线 6 / +vr≥1.3 6 / +ret>2% 4   ← 硬屏蔽，仅诊断输出
      T2_PULLBACK 基 70：+d20≥0 5 / +vr≤1.0 5 / +c>ma5 4
      cur_idx < 25 → NO_TRIGGER (0)
    返回 (trigger_type, ts, detail)
    """
    t = PeaConfig.TRIGGER
    if daily is None or len(daily) < t['min_history']:
        return 'NO_TRIGGER', 0.0, {}
    i = len(daily) - 1 if cur_idx is None else int(cur_idx)
    if i < t['min_history']:
        return 'NO_TRIGGER', 0.0, {}

    c = float(daily['close'].iloc[-1])
    ma5, ma20, ma60 = (float(daily['ma5'].iloc[-1]), float(daily['ma20'].iloc[-1]),
                       float(daily['ma60'].iloc[-1]))
    vr = float(daily['vol'].iloc[-1]) / max(float(daily['vol_ma5'].iloc[-2]), 1e-9)
    ret = float(daily['pct_chg'].iloc[-1])
    d20 = calc_ret_pct(daily, 20)
    close_pos = ((c - float(daily['low'].iloc[-1])) /
                 max(float(daily['high'].iloc[-1]) - float(daily['low'].iloc[-1]), 1e-9))
    detail = {'vr': vr, 'ret': ret, 'close_pos': close_pos, 'd20': d20}

    # T1 突破：20 日新高 或 ma60 上穿
    hi20 = float(daily['high'].iloc[-21:-1].max())
    if (c > hi20) or (c > ma60 and float(daily['close'].iloc[-6]) <= float(daily['ma60'].iloc[-6])):
        s = float(t['t1_base'])
        if c > ma60:
            s += 5
        if close_pos >= t['t1_close_pos']:
            s += 5
        if t['t1_vr_lo'] <= vr <= t['t1_vr_hi']:
            s += 5
        if c > ma20:
            s += 5
        return 'T1_BREAKOUT', min(100.0, s), detail

    # T3 收复：前 10 日内曾跌破 ma20×0.97 且今日收回
    lost = any(float(daily['close'].iloc[-k]) < float(daily['ma20'].iloc[-k]) * 0.97 for k in range(2, 11) if len(daily) > k)
    if lost and c > ma20:
        s = float(t['t3_base'])
        if c > ma20:
            s += 6
        if vr >= 1.3:
            s += 6
        if ret > 0.02 * 100:
            s += 4
        return 'T3_RECLAIM', min(100.0, s), detail

    # T2 回踩：贴近 ma20 附近
    bias = _pct(c, ma20)
    if _valid(bias) and -0.05 <= bias <= 0.04:
        s = float(t['t2_base'])
        if _valid(d20) and d20 >= 0:
            s += 5
        if vr <= t['t2_vol_max']:
            s += 5
        if c > ma5:
            s += 4
        return 'T2_PULLBACK', min(100.0, s), detail

    return 'NO_TRIGGER', 0.0, detail


def calc_ees(daily, ts, pqs, oh):
    """EES 事件执行分：trend 0.25 / trigger 0.30 / volume 0.15 / pqs 0.20 / overheat −0.10。"""
    w = PeaConfig.EES_W
    d = PeaConfig.EES_DEFAULT
    trend_v = trend_structure(daily) if daily is not None and len(daily) >= 60 else d['trend_v']
    vol_v = volume_structure(daily) if daily is not None and len(daily) >= 25 else d['vol_v']
    pqs_v = float(pqs) if _valid(pqs) else d['pqs_v']
    oh_v = (float(oh) / 40.0 * 100.0) if _valid(oh) else d['oh_v']
    ees = (trend_v * w['trend'] + float(ts) * w['trigger'] + vol_v * w['volume'] +
           pqs_v * w['pqs'] + oh_v * w['overheat'])
    return float(min(100.0, max(0.0, ees)))


# ============================================================
# 主扫描
# ============================================================
def _norm_by_group(rows):
    """组内 pct rank 归一（len≥3 启用），按 side 分组。"""
    if len(rows) < 3:
        for r in rows:
            r['norm'] = r['raw']
        return rows
    by_side = defaultdict(list)
    for r in rows:
        by_side[r['side']].append(r)
    for side, grp in by_side.items():
        if len(grp) < 3:
            for r in grp:
                r['norm'] = r['raw']
            continue
        vals = [r['raw'] for r in grp]
        ranks = pd.Series(vals).rank(pct=True) * 100.0
        for r, nr in zip(grp, ranks):
            r['norm'] = float(nr)
    return rows


def scan_pea(period='2026H1', scan_date=None, top_n=30, save=True):
    """
    PEA-Absorption 全池扫描。
    返回 (candidates_df, full_df, report_text)
    """
    os.makedirs(REPORT_DIR, exist_ok=True)
    if not scan_date:
        scan_date = pd.Timestamp.now().strftime('%Y%m%d')
    scan_date = str(scan_date)[:8]

    pool = load_pool(period)
    if pool is None or len(pool) == 0:
        return pd.DataFrame(), pd.DataFrame(), f'[PEA] 财报池为空 period={period}'
    bench = load_bench(end_date=scan_date, lookback=120)
    mkt_mult = market_multiplier(bench)
    mkt_state = _market_regime_6(bench)
    ind_map = _load_industry_map()

    rows = []
    for _, fin in pool.iterrows():
        ts_code = str(fin.get('ts_code', ''))
        code6 = ts_code.split('.')[0]
        ann_date = str(fin.get('ann_date', '') or '')[:8]
        if not code6 or len(code6) != 6:
            continue

        daily = load_daily_for(code6, end_date=scan_date)
        if daily is None:
            continue
        # ann_idx：ann_date 当日或其后首个交易日
        tds = daily['trade_date'].astype(str).tolist()
        ann_idx = None
        for i, td in enumerate(tds):
            if td >= ann_date:
                ann_idx = i
                break
        if ann_idx is None or ann_idx < 1:
            continue

        industry = ind_map.get(code6, str(fin.get('industry', '') or ''))

        # 事件与基本面
        etype, side, edesc = classify_event(fin)
        fq = fundamental_quality(fin)
        rqs = calc_rqs(fin, industry)
        gap_s = expect_gap_score(fin)
        ars = calc_ars(daily, ann_idx, len(daily) - 1)
        oh = overheat_penalty(daily)
        tqs = calc_tqs(daily)
        risk = risk_score(daily)

        # 范式核心
        event_age = calc_event_age(ann_date, scan_date)
        pa = price_absorption(daily, ann_idx, len(daily) - 1, bench)
        state, decay, _, _detail = calc_absorption_state(pa, event_age)
        refresh, refresh_conds = refresh_score(daily, pa, state)
        trig_type, ts, trig_detail = trigger_score(daily)
        pqs = pullback_quality(daily)
        ees = calc_ees(daily, ts, pqs, oh)

        # 语境
        conf = data_confidence(fin, ann_date)
        cf_key, cf_base, cf_adj, cf_desc = cashflow_context_engine(fin, industry)
        cfcs = cf_base + cf_adj
        eq_state = earnings_quality_context(fin)
        eq_penalty = PeaConfig.EQ_PENALTY.get(eq_state, 0.0)
        theme_adj, theme = theme_score(code6)

        # 相对风险（20 日量比 vs 基准）
        vol_ratio20 = np.nan
        if len(bench) >= 20 and 'vol' in bench.columns:
            sv = float(daily['vol'].iloc[-20:].mean())
            bv = float(bench['vol'].iloc[-20:].mean())
            if _valid(sv) and _valid(bv) and bv > 0:
                vol_ratio20 = sv / bv
        rel_risk = relative_risk_score(vol_ratio20 if _valid(vol_ratio20) else np.nan)

        # raw（组内归一在 collect 后）
        w = PeaConfig.W_A if side == 'A' else PeaConfig.W_B
        parts = ({'fq': fq, 'gap': gap_s, 'ars': ars, 'pqs': pqs, 'trend': tqs, 'risk': 100 - risk}
                 if side == 'A' else
                 {'rqs': rqs, 'fq': fq, 'gap': gap_s, 'ars': ars, 'tqs': tqs, 'risk': 100 - risk})
        raw = sum(float(parts[k]) * w[k] for k in w)

        rows.append({
            'ts_code': ts_code, 'code6': code6, 'ann_date': ann_date,
            'end_date': str(fin.get('end_date', '') or ''),
            'etype': etype, 'side': side, 'edesc': edesc,
            'industry': industry, 'theme': theme,
            'fq': fq, 'rqs': rqs, 'gap_s': gap_s, 'ars': ars,
            'pqs': pqs, 'tqs': tqs, 'trend_v': trend_structure(daily),
            'risk': risk, 'overheat': oh,
            'event_age': event_age, 'pre_ret': pa.get('pre_ret', np.nan),
            'gap_ann': pa.get('gap_ann', np.nan), 'post_ret': pa.get('post_ret', np.nan),
            'rel_str': pa.get('rel_str', np.nan),
            'pre_priced': pa.get('pre_priced', False),
            'absorption_state': state, 'decay': decay,
            'refresh': refresh, 'refresh_conds': '|'.join(refresh_conds),
            'trigger_type': trig_type, 'ts': ts,
            'ees': ees, 'conf': conf, 'cfcs': cfcs, 'cf_key': cf_key, 'cf_desc': cf_desc,
            'eq_state': eq_state, 'eq_penalty': eq_penalty,
            'theme_adj': theme_adj, 'rel_risk': rel_risk,
            'raw': raw, 'side_key': side,
            'close': float(daily['close'].iloc[-1]),
            'atr14': calc_atr14(daily),
            'vol_ratio20': vol_ratio20,
        })

    if not rows:
        return pd.DataFrame(), pd.DataFrame(), f'[PEA] 无有效候选 scan_date={scan_date}'

    rows = _norm_by_group(rows)

    # alpha 链 + 分级
    for r in rows:
        r['alpha'] = calc_pea_score(
            r['norm'], r['conf'], r['rel_risk'], mkt_mult, r['theme_adj'],
            r['decay'], r['refresh'], r['eq_penalty'])
        grade, reason = grade_pea(
            r['alpha'], r['ees'], r['ts'], r['risk'], r['conf'], r['fq'],
            r['overheat'], r['trigger_type'], r['absorption_state'],
            r['side'], r['rqs'], r['event_age'])
        r['grade'] = grade
        r['grade_reason'] = reason
        r['position'] = position_suggest(grade)
        r['filter_pass'] = int(grade in ('CORE', 'TEST', 'PROBE'))

    full = pd.DataFrame(rows)
    cands = apply_portfolio_cap(full[full['filter_pass'] == 1].copy()) if (full['filter_pass'] == 1).any() else pd.DataFrame()

    report = build_report(cands, full, period, scan_date, mkt_state, mkt_mult)
    if save:
        save_sqlite(full, cands, period, scan_date)
        fp = os.path.join(REPORT_DIR, f'pea_{period}_{scan_date}.txt')
        with open(fp, 'w', encoding='utf-8') as f:
            f.write(report)
    return cands, full, report


# ============================================================
# 落盘与报告
# ============================================================
def save_sqlite(full, cands, period, scan_date):
    """扫描结果落盘 SQLite（TEXT affinity，filter_pass 显式 int）。"""
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    try:
        full_out = full.copy()
        full_out['filter_pass'] = full_out['filter_pass'].astype(int)
        full_out['period'] = period
        full_out['scan_date'] = scan_date
        full_out.to_sql('scan_pea', conn, if_exists='append', index=False)
        if cands is not None and len(cands) > 0:
            c_out = cands.copy()
            c_out['filter_pass'] = c_out['filter_pass'].astype(int)
            c_out['period'] = period
            c_out['scan_date'] = scan_date
            c_out.to_sql('candidates_pea', conn, if_exists='append', index=False)
        conn.commit()
    finally:
        conn.close()


EXEC_TEMPLATE = """
执行模板（硬规则，不可绕过）：
  ┌ 持有期：T+15 个交易日收盘离场（实证唯一净超额为正路径 close15）
  ├ 止损  ：收盘价 < 买入价×0.92（-8% 收盘止损）→ 次日开盘离场
  ├ 追高  ：次日开盘 > 前收×1.08 → 放弃该笔
  ├ 禁入①：T3_RECLAIM 触发（实证 fwd20 -3.05% / 胜率 31% / n=77）
  └ 禁入②：PRICED_IN 吸收态（价格已完全消化事件）
"""


def build_report(cands, full, period, scan_date, mkt_state, mkt_mult):
    """6 区块报告：头部/市场/组合/分布/吸收诊断/执行模板。"""
    L = []
    L.append('=' * 64)
    L.append(f'PEA-Absorption 价格-事件吸收策略 V1.0 | {period} | {scan_date}')
    L.append('=' * 64)
    L.append('')
    L.append(f'[1] 市场环境：{mkt_state}（乘数 {mkt_mult:.2f}）')
    L.append(f'    池规模 {len(full)} | 有效候选 {len(full)} | 入池组合 {len(cands)}')
    L.append('')

    L.append('[2] 组合候选（PROBE→TEST→CORE）')
    if cands is None or len(cands) == 0:
        L.append('    （无入池标的）')
    else:
        for _, r in cands.iterrows():
            L.append(
                f"    {r['ts_code']:<11} {r['grade']:<5} α={r['alpha']:5.1f} "
                f"EES={r['ees']:5.1f} TS={r['ts']:5.1f} 状态={r['absorption_state']:<17}"
                f"触发={r['trigger_type']:<12} 仓位={r['position']*100:.0f}% "
                f"| {r['industry'][:6]} {r['theme'] or '-'}")
    L.append('')

    L.append('[3] 评分分布')
    for col, label in (('alpha', 'α'), ('ees', 'EES'), ('ts', 'TS'), ('conf', 'CONF')):
        if col in full.columns and len(full) > 0:
            L.append(f"    {label:<5} mean={full[col].mean():6.1f} "
                     f"p50={full[col].median():6.1f} p90={full[col].quantile(0.9):6.1f} "
                     f"max={full[col].max():6.1f}")
    L.append('')

    L.append('[4] 吸收态 / 触发分布')
    if 'absorption_state' in full.columns:
        for k, v in full['absorption_state'].value_counts().items():
            L.append(f'    吸收态 {k:<18} {v}')
    if 'trigger_type' in full.columns:
        for k, v in full['trigger_type'].value_counts().items():
            L.append(f'    触发   {k:<18} {v}')
    L.append('')

    L.append('[5] WAIT / REJECT 主要原因')
    if 'grade_reason' in full.columns:
        rej = full[full['grade'] == 'REJECT']
        for k, v in rej['grade'].value_counts().items():
            L.append(f'    {k:<8} {v}')
        for k, v in rej['grade_reason'].str.split('（').str[0].value_counts().head(6).items():
            L.append(f'    原因 {k:<40} {v}')
    L.append('')

    L.append('[6]' + EXEC_TEMPLATE)
    L.append('风险提示：本策略为事件漂移范式，事件龄 >15 日 Decay ≥0.15 强度递减；'
             '基准锚点 = er20 close15 Top2 净超额 +0.40%。')
    return '\n'.join(L)


def validate_pea(ts_code, period='2026H1', scan_date=None):
    """单票全链路诊断。"""
    if not scan_date:
        scan_date = pd.Timestamp.now().strftime('%Y%m%d')
    scan_date = str(scan_date)[:8]
    pool = load_pool(period)
    fin = None
    if pool is not None and len(pool) > 0:
        hit = pool[pool['ts_code'].astype(str).str.split('.').str[0] == str(ts_code).split('.')[0]]
        if len(hit) > 0:
            fin = hit.iloc[-1]
    if fin is None:
        print(f'[validate] {ts_code} 不在 {period} 财报池中')
        return None
    code6 = str(fin['ts_code']).split('.')[0]
    ann_date = str(fin.get('ann_date', ''))[:8]
    bench = load_bench(end_date=scan_date, lookback=120)
    daily = load_daily_for(code6, end_date=scan_date)
    if daily is None:
        print(f'[validate] {code6} 日线不足')
        return None
    tds = daily['trade_date'].astype(str).tolist()
    ann_idx = next((i for i, td in enumerate(tds) if td >= ann_date), None)
    if ann_idx is None or ann_idx < 1:
        print(f'[validate] {code6} ann_date={ann_date} 未在日线中定位')
        return None

    industry = str(fin.get('industry', '') or '')
    etype, side, edesc = classify_event(fin)
    fq = fundamental_quality(fin)
    rqs = calc_rqs(fin, industry)
    pa = price_absorption(daily, ann_idx, len(daily) - 1, bench)
    event_age = calc_event_age(ann_date, scan_date)
    state, decay, _, _ = calc_absorption_state(pa, event_age)
    refresh, conds = refresh_score(daily, pa, state)
    trig_type, ts, td_ = trigger_score(daily)
    oh = overheat_penalty(daily)
    ees = calc_ees(daily, ts, pullback_quality(daily), oh)
    conf = data_confidence(fin, ann_date)
    risk = risk_score(daily)
    mkt_mult = market_multiplier(bench)

    print('=' * 60)
    print(f'PEA 单票诊断 {fin["ts_code"]} | {period} | scan={scan_date}')
    print(f'事件 {etype}/{side} {edesc} ann={ann_date} 龄={event_age} 交易日')
    print(f'FQ={fq:.1f} RQS={rqs:.1f} CONF={conf:.1f} RISK={risk:.1f}')
    print(f'吸收: pre_ret={pa["pre_ret"]:.2f}% gap={pa["gap_ann"]*100:.2f}% '
          f'post_ret={pa["post_ret"]:.2f}% rel_str={pa["rel_str"]:.2f}%')
    print(f'状态={state} decay={decay:.2f} refresh={refresh:.0f} conds={conds}')
    print(f'触发={trig_type} TS={ts:.1f} EES={ees:.1f} OH={oh:.0f} 市场乘数={mkt_mult}')
    print('=' * 60)
    return {'code': code6, 'state': state, 'trigger': trig_type, 'alpha_diag': True}


# ============================================================
# 入口
# ============================================================
def main():
    ap = argparse.ArgumentParser(description='PEA-Absorption 价格-事件吸收策略 V1.0')
    ap.add_argument('--period', default='2026H1', help='财报期：2025H1 / 2026H1')
    ap.add_argument('--date', default=None, help='扫描日 YYYYMMDD（默认今日）')
    ap.add_argument('--top', type=int, default=30, help='报告候选数')
    ap.add_argument('--validate', default=None, help='单票诊断 code6')
    ap.add_argument('--no-save', action='store_true', help='不落盘')
    args = ap.parse_args()

    if args.validate:
        validate_pea(args.validate, args.period, args.date)
        return
    cands, full, report = scan_pea(args.period, args.date, top_n=args.top,
                                   save=not args.no_save)
    print(report)


if __name__ == '__main__':
    main()