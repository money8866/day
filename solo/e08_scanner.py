# -*- coding: utf-8 -*-
"""E08 全市场扫描器：跳空不补缺 → 缩量回踩 → 放量再突破

形态逻辑（强主升浪）：
  主力借跳空缺口直接跨越阻力区/筹码密集区，回踩时抛压枯竭、缺口上沿成强支撑，
  随后放量再突破开启第二波。

三阶段硬性条件（数值阈值唯一来源见下方常量区）
  阶段一 T0     : Low_T0 > High_T-1 且缺口幅度 >=GAP_MIN；T0 为放量阳线(涨幅>=T0_MIN_PCT 或涨停，
                  量 >=T0_VOL_MULT×前T0_VOL_MA日均量)；位置为突破缺口(距LOW_WINDOW日低点涨幅 <RISE_FROM_LOW_MAX)
  阶段二 T+1..N : N∈[PULLBACK_N_MIN, PULLBACK_N_MAX]；回踩期收盘价 >= High_T-1（缺口未回补，
                  允许盘中下影短暂破位）；缩量（均量 <=PULLBACK_VOL_AVG_MAX×Vol_T0 或
                  最低量 <=PULLBACK_VOL_MIN_MAX×Vol_T0）；无大阴线、波幅收窄；
                  回踩期不创新高（<=High_T0×(1+PULLBACK_MAX_NEWHIGH)，排除连续一字板/连板）
  阶段三 T+N+1  : 大阳线(涨幅>=REBRK_MIN_PCT 或涨停)，收盘 > 阶段一+二所有最高价；
                  量 >=REBRK_VOL_MULT×回踩均量

输出
  ① 今日 E08 完全符合信号（再突破日 = 最新交易日）→ 得分/评级/交易指令计划
  ② 回踩中跟踪池（T0 已成立、缺口未补、缩量进行中、等待放量再突破）→ 次日盯盘候选
  ③ 早鸟池（T0 刚成立、仅回踩 1~EARLY_N_MAX 天，尚未进入正式回踩窗口）→ 提前预警
  ④ report_daily/e08_scan_{date}.md + report_daily/e08_signals_{date}.json

数据源：本地 stock_data.db（daily_cache + adj_factor_cache + index_daily_cache）优先，
        基本面/名称读 stock_basic.csv，缺失不补网（本脚本默认零 API 调用）。

用法：
    python e08_scanner.py                    # 扫最新交易日
    python e08_scanner.py --date 20260930
    python e08_scanner.py --push             # 扫完后邮件推送日报（HTML 22px）
    python e08_scanner.py --limit 500 --verbose
"""

import argparse
import json
import os
import sqlite3
import subprocess
import time

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────
# 路径与数据源
# ─────────────────────────────────────────────
CACHE_DIR = os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily")
DB_PATH = os.path.join(CACHE_DIR, "stock_data.db")
BASIC_PATH = os.path.join(CACHE_DIR, "stock_basic.csv")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_daily")

MARKET_INDEX = "000001.SH"   # 上证指数（主板口径）
GEM_INDEX = "399006.SZ"      # 创业板指（双创口径）

# ─────────────────────────────────────────────
# E08 参数（唯一口径来源，改这里即可）
# ─────────────────────────────────────────────
LOOKBACK_BARS = 150          # 载入窗口交易日数（需覆盖 60日均线 + 120日筹码近似）
GAP_MIN = 0.010              # 阶段一：缺口幅度下限 1.0%
T0_MIN_PCT = 3.0             # 阶段一：T0 涨幅下限 3%（或涨停）
T0_VOL_MULT = 1.5            # 阶段一：T0 量 >= 1.5 × 前 N 日均量
T0_VOL_MA = 5                # 阶段一：均量窗口（前 5 日，不含 T0，避免自抬基准）
T0_MUST_BULLISH = True       # 阶段一：T0 须为阳线（close > open）
RISE_FROM_LOW_MAX = 0.50     # 阶段一：距 60 日低点累计涨幅上限 50%（排除高位出货缺口）
LOW_WINDOW = 60              # 阶段一：低点回看窗口
PLATFORM_NEAR = 0.97         # 阶段一：High_T-1 须 >= 前 20 日最高价 × 该系数（平台突破属性，展示用）

PULLBACK_N_MIN = 2           # 阶段二：回踩整理天数下限
PULLBACK_N_MAX = 6           # 阶段二：回踩整理天数上限
PULLBACK_VOL_AVG_MAX = 0.60  # 阶段二：回踩均量 <= 0.60 × Vol_T0
PULLBACK_VOL_MIN_MAX = 0.45  # 阶段二：回踩最低量 <= 0.45 × Vol_T0
PULLBACK_MAX_PCT_DOWN = -6.0 # 阶段二：单日最大跌幅下限（无大阴线砸盘）
PULLBACK_MAX_RANGE = 0.10    # 阶段二：单日最大振幅上限（日内波幅收窄）
PULLBACK_MAX_NEWHIGH = 0.03  # 阶段二：期间最高价 <= T0最高价×(1+3%)，排除连续一字板/连板，保证真回踩

REBRK_MIN_PCT = 1.0          # 阶段三：再突破日涨幅下限 1%（或涨停）
REBRK_VOL_MULT = 1.5         # 阶段三：量 >= 1.5 × 回踩均量

WATCH_N_MAX = PULLBACK_N_MAX + 1   # 跟踪池：已回踩天数上限（回踩天数含当日，信号 N=回踩天数-1）
EARLY_N_MAX = PULLBACK_N_MIN       # 早鸟池：已回踩天数上限（尚不足正式回踩窗口，跳空后刚启动）
WIN_MIN_BARS = 120           # 参与扫描的最少 K 线（上市满一年+窗口预热）
MIN_TOTAL_MV = 800000.0      # 总市值下限（万元，=80亿元；取 daily_basic_cache.total_mv）

SCORE_S, SCORE_A, SCORE_B = 85, 70, 55   # 评级阈值：S/A/B，低于 B 为「观察」
GAP_BUFFER_STRONG = 0.03     # 缺口缓冲加分线 3%
WINNER_STRONG = 0.85         # 获利盘比例加分线 85%
CHIP_WINDOW = 120            # 获利盘近似的回看交易日数
THEME_TOP_N = 3              # 板块效应：全市场 Top N 核心热点主线
THEME_WINDOW = "month"       # 主题热度窗口（today/week/month）


# ─────────────────────────────────────────────
# 数据读取
# ─────────────────────────────────────────────
def _trade_dates(conn, end_date, bars):
    """最近 bars 个交易日（升序）：优先用 index_daily_cache（小表），回退 daily_cache"""
    try:
        rows = conn.execute(
            "SELECT DISTINCT trade_date FROM index_daily_cache WHERE ts_code=? AND trade_date<=? "
            "ORDER BY trade_date DESC LIMIT ?", (MARKET_INDEX, str(end_date), int(bars))).fetchall()
        if len(rows) >= 30:
            return sorted(str(r[0]) for r in rows)
    except Exception:
        pass
    try:
        rows = conn.execute(
            "SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date<=? "
            "ORDER BY trade_date DESC LIMIT ?", (str(end_date), int(bars))).fetchall()
        return sorted(str(r[0]) for r in rows)
    except Exception:
        return []


def _load_window(conn, start_date, end_date):
    """载入区间全市场日线 + 复权因子。

    顺序全表扫描 + pandas mergesort（结论：`ORDER BY ts_code, trade_date` 会走主键索引
    随机读，21GB 库上会阻塞到分钟级；顺序扫描后本地排序更快更稳）。
    """
    sql = ("SELECT d.ts_code, d.trade_date, d.open, d.high, d.low, d.close, d.pre_close, "
           "d.pct_chg, d.vol, a.adj_factor "
           "FROM daily_cache AS d "
           "LEFT JOIN adj_factor_cache AS a "
           "ON a.ts_code = d.ts_code AND a.trade_date = d.trade_date "
           "WHERE d.trade_date >= ? AND d.trade_date <= ?")
    df = pd.read_sql_query(sql, conn, params=(str(start_date), str(end_date)))
    if df.empty:
        return df
    df['trade_date'] = df['trade_date'].astype(str)
    return df.sort_values(['ts_code', 'trade_date'], kind='mergesort').reset_index(drop=True)


def _load_basic():
    """股票基础信息（名称/行业/上市日）"""
    if not os.path.exists(BASIC_PATH):
        return pd.DataFrame(columns=['ts_code', 'name', 'industry', 'list_date'])
    df = pd.read_csv(BASIC_PATH, dtype={'ts_code': str, 'list_date': str})
    return df.drop_duplicates('ts_code').set_index('ts_code')


def _load_total_mv(conn, date):
    """交易日总市值（万元）：daily_basic_cache.total_mv → {ts_code: float}"""
    try:
        df = pd.read_sql_query(
            "SELECT ts_code, total_mv FROM daily_basic_cache WHERE trade_date=?",
            conn, params=(str(date),))
    except Exception:
        return {}
    if df is None or df.empty:
        return {}
    mv = pd.to_numeric(df['total_mv'], errors='coerce')
    return dict(zip(df['ts_code'].astype(str), mv))


def _index_ma20(conn, code, end_date):
    """指数收盘与 MA20：返回 (close, ma20) 或 (None, None)"""
    try:
        df = pd.read_sql_query(
            "SELECT trade_date, close FROM index_daily_cache WHERE ts_code=? AND trade_date<=? "
            "ORDER BY trade_date DESC LIMIT 25", conn, params=(code, str(end_date)))
    except Exception:
        return None, None
    if df is None or len(df) < 20:
        return None, None
    c = pd.to_numeric(df['close'], errors='coerce').to_numpy(dtype=float)
    c = c[:20][::-1]                      # 取最近 20 日并转升序
    if not np.isfinite(c).all():
        return None, None
    return float(c[-1]), float(c.mean())


def _limit_thr(code):
    """涨停判定阈值（%）：双创 20cm 用 19.5，主板用 9.8（ST 已在宇宙层剔除）"""
    return 19.5 if str(code).startswith(('300', '301', '688', '689')) else 9.8


def _is_bse(code):
    """北交所（用户明确不参与）"""
    c = str(code)
    return c.endswith('.BJ') or c[:2] in ('43', '83', '87', '88', '92')


# ─────────────────────────────────────────────
# 单股几何准备
# ─────────────────────────────────────────────
class Bars:
    """单股窗口数组（几何用前复权、量价用原始口径）"""

    __slots__ = ('code', 'dates', 'o', 'h', 'l', 'c', 'pre', 'pct', 'vol',
                 'qo', 'qh', 'ql', 'qc')

    def __init__(self, code, g):
        self.code = code
        self.dates = g['trade_date'].to_numpy()
        self.o = g['open'].to_numpy(dtype=float)
        self.h = g['high'].to_numpy(dtype=float)
        self.l = g['low'].to_numpy(dtype=float)
        self.c = g['close'].to_numpy(dtype=float)
        self.pre = g['pre_close'].to_numpy(dtype=float)
        self.pct = g['pct_chg'].to_numpy(dtype=float)
        self.vol = g['vol'].to_numpy(dtype=float)
        # 前复权因子：锚定序列末端（与 w7._qfq_price 一致）
        adj = pd.to_numeric(g['adj_factor'], errors='coerce')
        adj = adj.ffill().bfill()
        if adj.notna().all() and float(adj.iloc[-1]) > 0:
            q = (adj / float(adj.iloc[-1])).to_numpy(dtype=float)
        else:
            q = np.ones(len(g), dtype=float)
        self.qo = self.o * q
        self.qh = self.h * q
        self.ql = self.l * q
        self.qc = self.c * q
        # 说明：几何判定用前复权（qh/ql/qc，抗窗口内除权干扰，比值口径不变），
        #       展示与交易指令价格直接用原始数组 o/h/l/c（用户看盘价）。

    def __len__(self):
        return len(self.c)


# ─────────────────────────────────────────────
# 形态识别
# ─────────────────────────────────────────────
def _stage1(a, i0):
    """阶段一：跳空突破日校验。返回 detail dict 或 None"""
    if i0 - 1 - T0_VOL_MA < 0 or i0 < LOW_WINDOW:
        return None
    hp = a.qh[i0 - 1]                     # 缺口上沿 = 前一日最高价（前复权）
    if not np.isfinite(hp) or hp <= 0:
        return None
    gap = (a.ql[i0] - hp) / hp
    if gap < GAP_MIN:
        return None
    if T0_MUST_BULLISH and a.c[i0] <= a.o[i0]:
        return None
    if not np.isfinite(a.pct[i0]) or a.pct[i0] < T0_MIN_PCT:
        return None
    base_v = np.nanmean(a.vol[i0 - T0_VOL_MA:i0])
    if not (base_v > 0) or not np.isfinite(a.vol[i0]) or a.vol[i0] < T0_VOL_MULT * base_v:
        return None
    low60 = np.nanmin(a.ql[i0 - LOW_WINDOW + 1:i0 + 1])
    rise = (a.qc[i0] / low60 - 1.0) if low60 > 0 else np.inf
    if rise >= RISE_FROM_LOW_MAX:
        return None
    prev20 = np.nanmax(a.qh[i0 - 20:i0]) if i0 >= 21 else np.nan
    return {
        'i0': int(i0),
        'date_t0': str(a.dates[i0]),
        'gap_top': float(hp),
        'gap': float(gap),
        't0_pct': float(a.pct[i0]),
        't0_vol_mult': float(a.vol[i0] / base_v),
        't0_limit': bool(a.pct[i0] >= _limit_thr(a.code)),
        'rise_from_low': float(rise),
        'platform_break': bool(np.isfinite(prev20) and hp >= prev20 * PLATFORM_NEAR),
    }


def _stage2(a, i0, i1):
    """阶段二：缩量整理且不补缺。返回 detail dict 或 None（N = i1-i0-1）"""
    N = i1 - i0 - 1
    if N < PULLBACK_N_MIN or N > PULLBACK_N_MAX:
        return None
    hp = a.qh[i0 - 1]
    seg = slice(i0 + 1, i1)               # 回踩期 [i0+1, i1-1]
    highs, vols, pcts = a.qh[seg], a.vol[seg], a.pct[seg]
    closes = a.qc[seg]
    if len(closes) != N or not np.isfinite(closes).all():
        return None
    close_min = float(np.nanmin(closes))      # 回踩期最低收盘（缺口防守判定）
    if close_min < hp:                        # 收盘回补缺口 → 形态不成立（允许盘中下影破位）
        return None
    seg_high = float(np.nanmax(highs))
    if seg_high > a.qh[i0] * (1.0 + PULLBACK_MAX_NEWHIGH):   # 回踩期创新高 → 实为连板，非真回踩
        return None
    v_avg = float(np.nanmean(vols))
    v_min = float(np.nanmin(vols))
    v0 = float(a.vol[i0])
    if v0 <= 0:
        return None
    if not (v_avg <= PULLBACK_VOL_AVG_MAX * v0 or v_min <= PULLBACK_VOL_MIN_MAX * v0):
        return None
    pre = np.maximum(a.pre[i0 + 1:i1], 1e-9)
    rng = (a.h[i0 + 1:i1] - a.l[i0 + 1:i1]) / pre
    max_rng = float(np.nanmax(rng)) if len(rng) else 0.0
    min_pct = float(np.nanmin(pcts))
    if max_rng > PULLBACK_MAX_RANGE or min_pct < PULLBACK_MAX_PCT_DOWN:
        return None
    return {
        'n': int(N),
        'vol_avg': v_avg,
        'vol_min': v_min,
        'vol_avg_ratio': float(v_avg / v0),
        'vol_min_ratio': float(v_min / v0),
        'close_min': close_min,
        'gap_hold': float(close_min / hp - 1.0),
        'pullback_high': seg_high,
        'max_range': max_rng,
        'min_pct': min_pct,
    }


def _stage3(a, i1, i0, s2):
    """阶段三：放量再突破校验。返回 detail dict 或 None"""
    if not np.isfinite(a.pct[i1]) or a.pct[i1] < REBRK_MIN_PCT:
        return None
    prior_high = float(np.nanmax(a.qh[i0:i1]))
    if not (a.qc[i1] > prior_high):
        return None
    if s2['vol_avg'] <= 0 or a.vol[i1] < REBRK_VOL_MULT * s2['vol_avg']:
        return None
    return {
        'date_rb': str(a.dates[i1]),
        'rb_pct': float(a.pct[i1]),
        'rb_limit': bool(a.pct[i1] >= _limit_thr(a.code)),
        'rb_close': float(a.qc[i1]),
        'prior_high': prior_high,
        'rb_vol_mult': float(a.vol[i1] / s2['vol_avg']),
    }


def ma_stack(a, i1):
    """前复权均线多头排列判定，返回 dict"""
    c = a.qc
    if i1 < 60:
        return {'ma5': None, 'ma10': None, 'ma20': None, 'ma60': None, 'bullish': False}
    ma5 = float(np.nanmean(c[i1 - 4:i1 + 1]))
    ma10 = float(np.nanmean(c[i1 - 9:i1 + 1]))
    ma20 = float(np.nanmean(c[i1 - 19:i1 + 1]))
    ma60 = float(np.nanmean(c[i1 - 59:i1 + 1]))
    return {'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
            'bullish': bool(ma5 > ma10 > ma20 > ma60)}


def winner_ratio(a, i1, window=CHIP_WINDOW):
    """获利盘比例近似：近 window 日成交量加权，成本价(收盘) <= 现价 的量占比。

    非 Tushare cyq_perf 官方口径，为本地零依赖近似（口径已在报告中标注）。
    """
    j0 = max(0, i1 - window + 1)
    px = a.c[j0:i1 + 1]
    vol = a.vol[j0:i1 + 1]
    ok = np.isfinite(px) & np.isfinite(vol) & (vol > 0)
    if ok.sum() < 20:
        return None
    px, vol = px[ok], vol[ok]
    now = a.c[i1]
    return float(vol[px <= now].sum() / vol.sum())


def scan_stock(a, i1):
    """返回 (signal, watch, early)。

    signal = 今日再突破的完全符合信号；
    watch  = 回踩中跟踪池（已回踩 EARLY_N_MAX+1 ~ WATCH_N_MAX 天，进入正式窗口）；
    early  = 早鸟池（已回踩 1 ~ EARLY_N_MAX 天，跳空后刚启动回踩，提前预警）。
    """
    signal = watch = early = None
    for N in range(PULLBACK_N_MIN, PULLBACK_N_MAX + 1):     # N 升序 → 取最近（最紧）的缺口
        i0 = i1 - N - 1
        if i0 < 0:
            continue
        s1 = _stage1(a, i0)
        if not s1:
            continue
        s2 = _stage2(a, i0, i1)
        if not s2:
            continue
        s3 = _stage3(a, i1, i0, s2)
        if s3:
            signal = {**s1, **s2, **s3, 'i1': int(i1)}
            break

    # 跟踪池 / 早鸟池：T0 在近 WATCH_N_MAX 日内成立，缺口未回补、缩量进行中，尚未再突破
    if signal is None and i1 >= 1:
        for i0 in range(i1 - 1, max(i1 - WATCH_N_MAX - 1, -1), -1):
            nd = i1 - i0                                   # 已回踩天数（含今日）
            if nd < 1 or nd > WATCH_N_MAX:
                continue
            s1 = _stage1(a, i0)
            if not s1:
                continue
            hp = a.qh[i0 - 1]
            closes = a.qc[i0 + 1:i1 + 1]
            highs = a.qh[i0 + 1:i1 + 1]
            vols = a.vol[i0 + 1:i1 + 1]
            if len(closes) != nd or not np.isfinite(closes).all():
                continue
            if float(np.nanmin(closes)) < hp:
                continue                                   # 收盘回补缺口 → 该 T0 失效（更早的 T0 另行判定）
            if float(np.nanmax(highs)) > a.qh[i0] * (1.0 + PULLBACK_MAX_NEWHIGH):
                continue                                   # 已创新高（连板/续涨）→ 非回踩，放弃该 T0
            v0 = float(a.vol[i0])
            v_avg = float(np.nanmean(vols))
            pcts = a.pct[i0 + 1:i1 + 1]
            if v0 <= 0 or v_avg > 0.80 * v0:               # 回踩量须明显低于 T0 量（缩量进行中）
                continue
            if np.nanmin(pcts) < PULLBACK_MAX_PCT_DOWN:
                continue
            # 再突破日 r 需满足 i0+3 <= r <= i0+WATCH_N_MAX 且 r > i1（尚未发生）
            earliest_idx = max(i1 + 1, i0 + PULLBACK_N_MIN + 1)
            latest_idx = i0 + PULLBACK_N_MAX + 1
            rec = {**s1, 'days_pullback': int(nd),
                   'vol_avg_ratio': float(v_avg / v0),
                   'gap_hold': float(np.nanmin(closes) / hp - 1.0),
                   'gap_top': float(hp),                 # 缺口上沿（低吸防线，前复权）
                   'break_price': float(a.qh[i0]),       # T0 最高价（再突破确认价，前复权）
                   'earliest_in': int(earliest_idx - i1),  # 距最早可完成日（交易日）
                   'latest_in': int(latest_idx - i1)}      # 距最后有效日（交易日）
            if nd <= EARLY_N_MAX:
                early = rec
            else:
                watch = rec
            break
    return signal, watch, early


# ─────────────────────────────────────────────
# 评分与交易计划
# ─────────────────────────────────────────────
def score_signal(sig, ctx):
    """按规范评分（满分 100）并给出评级与明细"""
    items = []
    score = 0.0

    # 板块效应 +20
    th = sig.get('theme_hit') or []
    if th:
        score += 20
        items.append(('板块效应', 20, '命中全市场 Top%d 主线: %s' % (THEME_TOP_N, '、'.join(th))))
    elif ctx['theme_ready']:
        items.append(('板块效应', 0, '未属 Top%d 主线' % THEME_TOP_N))
    else:
        items.append(('板块效应', 0, '主题热度/映射数据缺失，本项未计'))

    # 大盘环境 +15
    if ctx['index_ok']:
        score += 15
        items.append(('大盘环境', 15, ctx['index_desc']))
    else:
        items.append(('大盘环境', 0, ctx['index_desc']))

    # 筹码结构 +15
    wr = sig.get('winner')
    if wr is not None and wr >= WINNER_STRONG:
        score += 15
        items.append(('筹码结构', 15, '获利盘 %.1f%%（>85%%）' % (wr * 100)))
    elif wr is not None:
        items.append(('筹码结构', 0, '获利盘 %.1f%%（<85%%）' % (wr * 100)))
    else:
        items.append(('筹码结构', 0, '窗口样本不足，无法估计'))

    # 回踩极致度 +15
    if sig['n'] == 2 and sig['vol_min_ratio'] < 0.30:
        score += 15
        items.append(('回踩极致度', 15, '回踩仅 2 天且最低量 %.2f×T0量（<0.30×）' % sig['vol_min_ratio']))
    else:
        items.append(('回踩极致度', 0, '回踩 %d 天，最低量 %.2f×T0量' % (sig['n'], sig['vol_min_ratio'])))

    # 首板/涨停属性 +15
    if sig['t0_limit'] and sig['rb_limit']:
        score += 15
        items.append(('首板/涨停', 15, 'T0 与再突破日均为涨停板'))
    else:
        items.append(('首板/涨停', 0, 'T0 %s / 再突破日 %s' % (
            '涨停' if sig['t0_limit'] else '非涨停',
            '涨停' if sig['rb_limit'] else '非涨停')))

    # 均线多头 +10
    if sig['ma']['bullish']:
        score += 10
        items.append(('均线多头', 10, 'MA5>MA10>MA20>MA60 标准多头排列'))
    else:
        items.append(('均线多头', 0, '均线未完全多头'))

    # 缺口缓冲 +10
    if sig['gap'] > GAP_BUFFER_STRONG:
        score += 10
        items.append(('缺口缓冲', 10, '缺口幅度 %.2f%%（>3%%）' % (sig['gap'] * 100)))
    else:
        items.append(('缺口缓冲', 0, '缺口幅度 %.2f%%（<3%%）' % (sig['gap'] * 100)))

    score = round(min(score, 100.0), 1)
    grade = 'S级' if score >= SCORE_S else ('A级' if score >= SCORE_A else
                                            ('B级' if score >= SCORE_B else '观察'))
    return score, grade, items


def build_plan(sig, a):
    """交易指令计划（价格统一为原始价，用户看盘口径）"""
    close = float(a.c[-1])
    wave = sig['wave_raw']                     # 缺口浪高 = High_T0 - Low_T-1（原始价）
    return {
        'entry_low': round(close * 0.97, 2),
        'entry_high': round(close * 1.03, 2),
        'hard_stop': round(sig['gap_top_raw'], 2),      # 缺口上沿 = High_T-1（原始价）
        'trail_stop': round(sig['body_mid_raw'], 2),    # T0 阳线实体半分位
        'target1': round(close + wave, 2),
        'target2': round(close + 2 * wave, 2),
        'ma5': round(sig['ma5_raw'], 2) if sig.get('ma5_raw') else None,
        't0_high': round(sig['t0_high_raw'], 2),
        'close': round(close, 2),
    }


# ─────────────────────────────────────────────
# 上下文（主题 / 大盘 / 获利盘）
# ─────────────────────────────────────────────
def load_theme_ctx(date):
    """全市场 Top3 核心热点主线 + 个股归属热点主题"""
    try:
        from theme_hot_pool import hot_theme_heat, stock_hot_themes
    except Exception as exc:
        return {'ready': False, 'reason': 'theme_hot_pool 不可用(%s)' % exc,
                'top3': [], 'mapping': {}}
    heat = hot_theme_heat(date, window=THEME_WINDOW, heat_min=0.0)
    if not heat:
        return {'ready': False, 'reason': '无主题热度文件', 'top3': [], 'mapping': {}}
    top3 = [t for t, _ in sorted(heat.items(), key=lambda kv: -kv[1])[:THEME_TOP_N]]
    smap = stock_hot_themes(date, window=THEME_WINDOW, heat_min=min(heat[t] for t in top3)) or {}
    return {'ready': True, 'reason': '', 'top3': [(t, round(heat[t], 2)) for t in top3],
            'top3_set': set(top3), 'mapping': smap}


def market_ctx(conn, date, code):
    """大盘环境：双创看创业板指、其余看上证指数；指数在 MA20 上方 → +15"""
    idx = GEM_INDEX if str(code).startswith(('300', '301', '688', '689')) else MARKET_INDEX
    close, ma20 = _index_ma20(conn, idx, date)
    if close is None:
        return False, '指数 MA20 数据缺失'
    ok = close > ma20
    return ok, '%s %.2f %s MA20 %.2f' % (idx, close, '在' if ok else '低于', ma20)


# ─────────────────────────────────────────────
# 报告
# ─────────────────────────────────────────────
def _fmt_pct(x, digits=2):
    return ('%+.' + str(digits) + 'f%%') % (x * 100)


def render_signal_block(k, sig, score, grade, items, plan):
    L = []
    L.append('### %d. %s %s ｜ %s（%.1f 分）' % (k, sig['code'], sig['name'], grade, score))
    tag = []
    if sig.get('theme_hit'):
        tag.append('主线: ' + '、'.join(sig['theme_hit']))
    if sig.get('total_mv'):
        tag.append('市值 %.0f亿' % (sig['total_mv'] / 10000.0))
    tag.append('缺口 %s' % _fmt_pct(sig['gap']))
    tag.append('回踩 %d 天' % sig['n'])
    tag.append('T0量 %.1fx' % sig['t0_vol_mult'])
    L.append('　'.join(tag))
    L.append('')
    L.append('════════ 阶段特征拆解 ════════')
    L.append('阶段一 跳空突破（T0 = %s）' % sig['date_t0'])
    L.append('  缺口：Low %.2f > 前日 High %.2f ｜ 缺口幅度 %s（阈值 %.2f%%）'
             % (sig['low_t0_raw'], sig['gap_top_raw'], _fmt_pct(sig['gap']), GAP_MIN * 100))
    L.append('  动能：涨幅 %+.2f%%（阈值 +%.2f%% 或涨停）｜ 量能 %.2fx 前%d日均量（阈值 %.1fx）'
             % (sig['t0_pct'], T0_MIN_PCT, sig['t0_vol_mult'], T0_VOL_MA, T0_VOL_MULT))
    L.append('  位置：距 %d 日低点 %+.1f%%（上限 +%.0f%%）｜ 平台突破属性：%s'
             % (LOW_WINDOW, sig['rise_from_low'] * 100, RISE_FROM_LOW_MAX * 100,
                '是' if sig['platform_break'] else '否'))
    L.append('阶段二 缩量回踩（%d 日）' % sig['n'])
    L.append('  缺口防守：回踩期最低收盘 %.2f ≥ 缺口上沿 %.2f（未回补，富余 %s；允许盘中下影破位）'
             % (sig['close_min_raw'], sig['gap_top_raw'], _fmt_pct(sig['gap_hold'])))
    L.append('  回踩约束：回踩期最高 %.2f ≤ T0 最高 %.2f×%.2f（未创新高，属真回踩）'
             % (sig['pullback_high_raw'], sig['t0_high_raw'], 1.0 + PULLBACK_MAX_NEWHIGH))
    L.append('  极度缩量：均量 %.2fx T0量 ｜ 最低量 %.2fx T0量（阈值 ≤%.2fx / ≤%.2fx）'
             % (sig['vol_avg_ratio'], sig['vol_min_ratio'],
                PULLBACK_VOL_AVG_MAX, PULLBACK_VOL_MIN_MAX))
    L.append('  实体收敛：最大日内波幅 %.1f%%（≤%.0f%%）｜ 最弱单日涨跌 %+.2f%%（≥%.0f%%）'
             % (sig['max_range'] * 100, PULLBACK_MAX_RANGE * 100,
                sig['min_pct'], PULLBACK_MAX_PCT_DOWN))
    L.append('阶段三 放量再突破（%s）' % sig['date_rb'])
    L.append('  收盘 %.2f > 阶段一+二最高价 %.2f ｜ 涨幅 %+.2f%%（阈值 ≥%.1f%% 或涨停）'
             ' ｜ 量能 %.2fx 回踩均量（阈值 %.1fx）'
             % (sig['rb_close_raw'], sig['prior_high_raw'], sig['rb_pct'],
                REBRK_MIN_PCT, sig['rb_vol_mult'], REBRK_VOL_MULT))
    L.append('')
    L.append('──────── 形态得分明细 ────────')
    for name, val, why in items:
        L.append('  %-6s %+3d  %s' % (name, val, why))
    L.append('  合计 %.1f / 100 → %s' % (score, grade))
    L.append('')
    L.append('──────── 交易指令计划 ────────')
    L.append('  形态判定    完全符合（E08 三阶段硬性条件全部满足）')
    L.append('  参考建仓区  %.2f ~ %.2f（现价 %.2f ±3%%；回踩不破缺口上沿为低吸区）'
             % (plan['entry_low'], plan['entry_high'], plan['close']))
    L.append('  硬止损      %.2f（缺口上沿，跌破=缺口封闭，无条件清仓）' % plan['hard_stop'])
    L.append('  移动止损    %.2f（T0 阳线实体半分位，收盘跌破执行）' % plan['trail_stop'])
    L.append('  时间止损    入场后 3 个交易日未创新高，无条件离场')
    L.append('  止盈目标①   %.2f（现价 + 1.0×缺口浪高 %.2f）' % (plan['target1'], plan['target1'] - plan['close']))
    L.append('  止盈目标②   %.2f（现价 + 2.0×缺口浪高）' % plan['target2'])
    L.append('  动态止盈    放量滞涨长上影，或跌破 MA5（%s）时分批止盈'
             % ('%.2f' % plan['ma5'] if plan['ma5'] else '数据缺失'))
    L.append('  买点提示    买点一：盘中放量突破 T0 最高价 %.2f 瞬间；买点二：临近收盘(14:45后)'
             '确认放量大阳成立。' % plan['t0_high'])
    L.append('')
    return L


def render(signals, watch, early, date, meta):
    L = []
    L.append('# E08 跳空不补缺 → 缩量回踩 → 放量再突破 ｜ 全市场扫描')
    L.append('')
    L.append('交易日 %s ｜ 股池 %d ｜ 载入窗口 %d 交易日 ｜ 生成 %s'
             % (date, meta['universe'], meta['lookback'], meta['now']))
    L.append('')
    L.append('════════ 市场环境 ════════')
    L.append('上证指数 %s ｜ 创业板指 %s' % (meta['idx_desc_sh'], meta['idx_desc_gem']))
    if meta['theme_ready']:
        L.append('全市场 Top%d 核心热点主线：%s'
                 % (THEME_TOP_N, '、'.join('%s(%.1f)' % (t, h) for t, h in meta['theme_top3'])))
    else:
        L.append('主题热度数据不可用（%s）→ 板块效应项一律计 0' % meta['theme_reason'])
    L.append('')
    L.append('════════ 一、今日 E08 完全符合信号（再突破日 = %s）════════' % date)
    L.append('')
    if signals:
        for k, (sig, score, grade, items, plan) in enumerate(signals, 1):
            L.extend(render_signal_block(k, sig, score, grade, items, plan))
    else:
        L.append('今日无完全符合 E08 硬性条件的标的（跳空不补缺 + %d~%d 日缩量回踩 + 放量再突破同日成立）。'
                 % (PULLBACK_N_MIN, PULLBACK_N_MAX))
        L.append('')
    L.append('════════ 二、回踩中跟踪池（等待放量再突破，次日盯盘候选）════════')
    L.append('')
    if watch:
        for k, w in enumerate(watch, 1):
            L.append('### %d. %s %s ｜ 已回踩 %d 天%s'
                     % (k, w['code'], w['name'], w['days_pullback'],
                        (' ｜ 市值 %.0f亿' % (w['total_mv'] / 10000.0)) if w.get('total_mv') else ''))
            L.append('  T0 %s：缺口 %s ｜ 涨幅 %+.2f%% ｜ 量能 %.2fx ｜ 距60日低点 %+.1f%%'
                     % (w['date_t0'], _fmt_pct(w['gap']), w['t0_pct'],
                        w['t0_vol_mult'], w['rise_from_low'] * 100))
            L.append('  缺口上沿 %.2f（回踩期最低收盘距其 %s，未回补）｜ 回踩均量 %.2fx T0量'
                     % (w['gap_top_raw'], _fmt_pct(w['gap_hold']), w['vol_avg_ratio']))
            L.append('  触发条件：放量站上 T0 最高价 %.2f 且收盘 > 阶段最高价 → 当日构成 E08 再突破买点'
                     % w['break_price_raw'])
            L.append('  失效条件：任一交易日收盘跌破缺口上沿 %.2f（缺口回补）即出局观察'
                     % w['gap_top_raw'])
            L.append('  时间窗口：最早可完成于 T+%d，最后有效日 T+%d（回踩超 %d 日胜率大幅衰减，当前已 %d 日）'
                     % (w['earliest_in'], w['latest_in'], PULLBACK_N_MAX, w['days_pullback']))
            L.append('')
    else:
        L.append('无处于回踩跟踪窗口内的标的。')
        L.append('')
    L.append('════════ 三、早鸟池（跳空后刚启动回踩 1~%d 天，提前预警）════════' % EARLY_N_MAX)
    L.append('')
    if early:
        for k, w in enumerate(early, 1):
            L.append('### %d. %s %s ｜ 已回踩 %d 天%s'
                     % (k, w['code'], w['name'], w['days_pullback'],
                        (' ｜ 市值 %.0f亿' % (w['total_mv'] / 10000.0)) if w.get('total_mv') else ''))
            L.append('  T0 %s：缺口 %s ｜ 涨幅 %+.2f%% ｜ 量能 %.2fx ｜ 距60日低点 %+.1f%%'
                     % (w['date_t0'], _fmt_pct(w['gap']), w['t0_pct'],
                        w['t0_vol_mult'], w['rise_from_low'] * 100))
            L.append('  缺口上沿 %.2f（回踩期最低收盘距其 %s，未回补）｜ 回踩均量 %.2fx T0量'
                     % (w['gap_top_raw'], _fmt_pct(w['gap_hold']), w['vol_avg_ratio']))
            L.append('  关注条件：放量站上 T0 最高价 %.2f 且收盘 > 阶段最高价 → 当日构成 E08 再突破买点'
                     % w['break_price_raw'])
            L.append('  失效条件：任一交易日收盘跌破缺口上沿 %.2f（缺口回补）即出局观察'
                     % w['gap_top_raw'])
            L.append('  时间窗口：最早可完成于 T+%d，最后有效日 T+%d（当前仅回踩 %d 天，尚未进入正式回踩窗口）'
                     % (w['earliest_in'], w['latest_in'], w['days_pullback']))
            L.append('')
    else:
        L.append('无处于早鸟窗口内的标的。')
        L.append('')
    L.append('════════ 四、口径与参数说明 ════════')
    L.append('硬性条件：缺口 ≥%.2f%% ｜ T0 涨幅 ≥%.1f%%（或涨停）且量 ≥%.1fx 前%d日均量 ｜ '
             '距%d日低点涨幅 <%.0f%%' % (GAP_MIN * 100, T0_MIN_PCT, T0_VOL_MULT, T0_VOL_MA,
                                        LOW_WINDOW, RISE_FROM_LOW_MAX * 100))
    L.append('　　　　　回踩 %d~%d 日 ｜ 收盘不破缺口上沿（允许盘中下影破位）｜ 期间不创新高(≤T0最高×%.2f) ｜ '
             '均量 ≤%.2fx 或 最低量 ≤%.2fx T0量 ｜ 单日跌幅 ≥%.0f%% 且振幅 ≤%.0f%%'
             % (PULLBACK_N_MIN, PULLBACK_N_MAX, 1.0 + PULLBACK_MAX_NEWHIGH,
                PULLBACK_VOL_AVG_MAX, PULLBACK_VOL_MIN_MAX,
                PULLBACK_MAX_PCT_DOWN, PULLBACK_MAX_RANGE * 100))
    L.append('　　　　　再突破日涨幅 ≥%.1f%%（或涨停）｜ 收盘 > 阶段一+二最高价 ｜ 量 ≥%.1fx 回踩均量'
             % (REBRK_MIN_PCT, REBRK_VOL_MULT))
    L.append('　　　　　清单分栏：① 完全符合信号（再突破日=当日）｜ ② 回踩池（已回踩 %d~%d 天，'
             '进入正式窗口）｜ ③ 早鸟池（已回踩 1~%d 天，跳空后刚启动，提前预警）'
             % (EARLY_N_MAX + 1, WATCH_N_MAX, EARLY_N_MAX))
    L.append('口径提示：几何量（缺口/最高最低价比较）用前复权价，量能与涨跌幅用原始口径，'
             '交易指令价格换算回原始价。')
    L.append('　　　　　获利盘比例为本地近似（近 %d 日成交量加权、成本<=现价占比），'
             '非 Tushare cyq_perf 官方口径。' % CHIP_WINDOW)
    if meta['min_mv'] > 0:
        L.append('　　　　　北交所、ST/退市、上市不足一年、总市值 ≤ %.0f 亿的标的不参与扫描。'
                 % (meta['min_mv'] / 10000.0))
    else:
        L.append('　　　　　北交所、ST/退市、上市不足一年的标的不参与扫描（未做市值过滤）。')
    L.append('')
    L.append('免责声明：本扫描为量化形态统计输出，不构成投资建议，据此交易风险自负。')
    return '\n'.join(L) + '\n'


# ─────────────────────────────────────────────
# 邮件推送（Agent Mail CLI，HTML 正文 22px）
# ─────────────────────────────────────────────
MAIL_CLI = r'C:\Users\kongx\AppData\Roaming\npm\agently-cli.cmd'
MAIL_TO = 'stock1975@qq.com'

# 移动端优先：22px 大字（用户老花）、卡片式、保留行内缩进与换行
_MAIL_CSS = """
* { box-sizing: border-box; }
body { margin:0; padding:0; background:#eef2f7; -webkit-text-size-adjust:100%;
       font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",Arial,sans-serif;
       font-size:22px; line-height:1.8; color:#1f2937; }
.wrap { padding:14px; }
.hd { background:#0f766e; border-radius:14px; padding:18px 16px; margin-bottom:14px; color:#fff; }
.hd .t { font-size:26px; font-weight:700; line-height:1.45; }
.hd .s { font-size:18px; opacity:.92; margin-top:6px; }
.card { background:#fff; border-radius:14px; padding:18px 16px 8px; box-shadow:0 1px 4px rgba(15,23,42,.08); }
.ft { text-align:center; color:#94a3b8; font-size:17px; line-height:1.7; padding:14px 8px 4px; }
h1,h2,h3,h4 { margin:22px 0 12px; line-height:1.45; font-weight:700; color:#0f172a; }
h1 { font-size:26px; padding-bottom:10px; border-bottom:3px solid #0f766e; }
h2 { font-size:24px; padding-left:12px; border-left:7px solid #0f766e; }
h3 { font-size:22px; color:#334155; padding-top:6px; border-top:1px dashed #cbd5e1; }
h4 { font-size:22px; color:#475569; }
p { margin:0 0 14px; white-space:pre-wrap; word-break:break-word; }
strong { color:#b45309; }
hr { border:0; border-top:1px dashed #cbd5e1; margin:22px 0; }
code { background:#f1f5f9; color:#be123c; border-radius:6px; padding:2px 7px; font-size:21px; word-break:break-all; }
"""


def _md_to_email_html(md_text, title, subtitle=''):
    """markdown → 邮件 HTML：卡片式、正文 22px、保留原报告行内缩进与换行"""
    import re
    import markdown2
    from datetime import datetime
    body = markdown2.markdown(
        md_text,
        extras=['tables', 'fenced-code-blocks', 'breaks', 'strike', 'task_list']
    )
    body = re.sub(r'<table>', '<div class="tbl"><table>', body).replace('</table>', '</table></div>')
    sub = '<div class="s">%s</div>' % subtitle if subtitle else ''
    gen_time = datetime.now().strftime('%Y-%m-%d %H:%M')
    return (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<title>%s</title>\n<style>%s</style>\n</head>\n<body>\n'
        '<div class="wrap">\n'
        '<div class="hd"><div class="t">%s</div>%s</div>\n'
        '<div class="card">\n%s\n</div>\n'
        '<div class="ft">生成时间 %s<br>'
        '本邮件由 E08 全市场扫描器自动推送，仅做形态统计，不构成投资建议</div>\n'
        '</div>\n</body>\n</html>' % (title, _MAIL_CSS, title, sub, body, gen_time)
    )


def send_email(md_text, trade_date, subject, subtitle='', mail_to=MAIL_TO):
    """Agent Mail CLI 发送日报邮件（HTML 正文 22px）。异常只打日志不抛出。"""
    if not os.path.exists(MAIL_CLI):
        print('[e08-push] 未找到 Agent Mail CLI: %s，跳过邮件推送' % MAIL_CLI)
        return False
    # CLI 的 --body-file 须落在当前工作目录内且 ≤1MB，故先写 cwd 下临时 html
    body_path = os.path.join(os.getcwd(), '_e08_mail_%s.html' % trade_date)
    try:
        html = _md_to_email_html(md_text, subject, subtitle)
        raw = html.encode('utf-8')
        if len(raw) > 1000 * 1024:
            html = raw[:1000 * 1024].decode('utf-8', 'ignore') + '<p>（正文超过 1MB，已截断）</p>'
        with open(body_path, 'w', encoding='utf-8') as fh:
            fh.write(html)
    except Exception as exc:
        print('[e08-push] 邮件正文写入失败: %s' % exc)
        return False

    cmd = [MAIL_CLI, 'message', '+send', '--to', mail_to, '--subject', subject,
           '--body-file', os.path.basename(body_path), '--body-format', 'html', '--confirmed']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                           errors='ignore', timeout=180)
        if r.returncode == 0 and '"ok": true' in (r.stdout or ''):
            print('[e08-push] 邮件推送成功: %s' % mail_to)
            os.remove(body_path)
            return True
        detail = ((r.stdout or '') + (r.stderr or '')).strip().replace('\n', ' ')[:300]
        print('[e08-push] 邮件推送失败: rc=%s %s（正文留存: %s）' % (r.returncode, detail, body_path))
        return False
    except Exception as exc:
        print('[e08-push] 邮件推送异常: %s' % exc)
        return False


# ─────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='', help='交易日 YYYYMMDD，默认最新')
    ap.add_argument('--limit', type=int, default=0, help='仅扫描前 N 只（调试）')
    ap.add_argument('--output', default='', help='报告 md 路径')
    ap.add_argument('--lookback', type=int, default=LOOKBACK_BARS, help='载入窗口交易日数')
    ap.add_argument('--min-mv', type=float, default=MIN_TOTAL_MV,
                    help='总市值下限（万元，默认 80 亿元；0=不过滤）')
    ap.add_argument('--no-theme', action='store_true', help='跳过主题取数（板块效应计 0）')
    ap.add_argument('--push', action='store_true', help='扫描完成后邮件推送日报（HTML 正文 22px）')
    ap.add_argument('--mail-to', default=MAIL_TO, help='推送邮箱（默认 %s）' % MAIL_TO)
    ap.add_argument('--verbose', action='store_true')
    args = ap.parse_args()

    t0 = time.time()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        row = conn.execute('SELECT MAX(trade_date) FROM daily_cache').fetchone()
        latest = str(row[0]) if row and row[0] else ''
        date = args.date or latest
        if not date:
            print('[] daily_cache 无数据')
            return

        dates = _trade_dates(conn, date, args.lookback)
        if len(dates) < 70:
            print('[] 交易日窗口不足: %d' % len(dates))
            return
        start = dates[0]
        print('[e08] 日期=%s 载入区间=%s~%s(%d 交易日)...' % (date, start, dates[-1], len(dates)), flush=True)
        df = _load_window(conn, start, dates[-1])
        print('[e08] 日线载入 %d 行 / %d 只，耗时 %.1fs'
              % (len(df), df['ts_code'].nunique() if not df.empty else 0, time.time() - t0), flush=True)
        if df.empty:
            print('[] 无日线数据')
            return

        basic = _load_basic()
        mv_map = _load_total_mv(conn, date) if args.min_mv > 0 else {}
        if args.min_mv > 0:
            print('[e08] 市值过滤 >%.0f 亿：命中 %d 只含市值数据'
                  % (args.min_mv / 10000.0, len(mv_map)), flush=True)
        theme_ctx = ({'ready': False, 'reason': '--no-theme', 'top3': [], 'mapping': {}}
                     if args.no_theme else load_theme_ctx(date))
        if theme_ctx.get('ready'):
            print('[e08] Top%d 主线: %s' % (THEME_TOP_N, theme_ctx['top3']), flush=True)
        else:
            print('[e08] 主题数据不可用: %s' % theme_ctx.get('reason'), flush=True)

        sh_c, sh_ma = _index_ma20(conn, MARKET_INDEX, date)
        gem_c, gem_ma = _index_ma20(conn, GEM_INDEX, date)
    finally:
        pass  # conn 稍后关闭

    idx_desc_sh = ('%.2f vs MA20 %.2f' % (sh_c, sh_ma)) if sh_c else '数据缺失'
    idx_desc_gem = ('%.2f vs MA20 %.2f' % (gem_c, gem_ma)) if gem_c else '数据缺失'

    signals, watch_list, early_list, universe = [], [], [], 0
    codes = df['ts_code'].drop_duplicates().tolist()
    if args.limit:
        codes = codes[:args.limit]
    n_ok = 0
    for n, (code, g) in enumerate(df.groupby('ts_code', sort=False), 1):
        if args.limit and n > args.limit:
            break
        if _is_bse(code):
            continue
        if len(g) < WIN_MIN_BARS:
            continue
        name = ''
        industry = ''
        list_date = ''
        if code in basic.index:
            b = basic.loc[code]
            name = str(b.get('name') or '')
            industry = str(b.get('industry') or '')
            list_date = str(b.get('list_date') or '')
        if 'ST' in name.upper() or '退' in name:
            continue
        if list_date.isdigit() and int(list_date) > int(date) - 365:
            continue
        mv = mv_map.get(code)
        if args.min_mv > 0 and (mv is None or not (float(mv) > args.min_mv)):
            continue                                          # 缺市值数据视为不达标，保守剔除
        universe += 1
        gn = g.reset_index(drop=True)
        # 最后一根必须落在请求交易日（停牌股当日无 bar → 跳过）
        if str(gn['trade_date'].iloc[-1]) != str(date):
            continue
        a = Bars(code, gn)
        i1 = len(a) - 1
        sig, watch, early = scan_stock(a, i1)
        if not sig and not watch and not early:
            continue
        n_ok += 1
        if args.verbose:
            print('[e08] %s %s 命中%s' % (code, name,
                  '信号' if sig else ('回踩池' if watch else '早鸟池')), flush=True)

        if sig:
            i0, i1s = sig['i0'], sig['i1']
            sig['code'], sig['name'], sig['industry'] = code, name or code, industry
            sig['ma'] = ma_stack(a, i1)
            sig['winner'] = winner_ratio(a, i1)
            # 原始价口径（展示 + 交易指令；几何判定仍用前复权比值）
            sig['t0_high_raw'] = float(a.h[i0])
            sig['gap_top_raw'] = float(a.h[i0 - 1])                 # 缺口上沿 = High_T-1
            sig['low_t0_raw'] = float(a.l[i0])
            sig['close_min_raw'] = float(np.nanmin(a.c[i0 + 1:i1s]))  # 回踩期最低收盘（原始价，缺口防守）
            sig['pullback_high_raw'] = float(np.nanmax(a.h[i0 + 1:i1s]))  # 回踩期最高（原始价）
            sig['rb_close_raw'] = float(a.c[i1s])
            sig['prior_high_raw'] = float(np.nanmax(a.h[i0:i1s]))
            sig['body_mid_raw'] = float((a.o[i0] + a.c[i0]) / 2.0)   # T0 阳线实体半分位
            sig['wave_raw'] = float(a.h[i0] - a.l[i0 - 1])           # 缺口浪高
            sig['ma5_raw'] = float(np.nanmean(a.c[i1s - 4:i1s + 1])) if i1s >= 4 else None
            sig['total_mv'] = (float(mv) if mv is not None else None)
            hits = [t for t, _h in (theme_ctx['mapping'].get(code) or [])
                    if theme_ctx.get('ready') and t in theme_ctx['top3_set']]
            sig['theme_hit'] = hits
            ok, desc = market_ctx(conn, date, code)
            ctx = {'theme_ready': bool(theme_ctx.get('ready')), 'index_ok': ok, 'index_desc': desc}
            score, grade, items = score_signal(sig, ctx)
            plan = build_plan(sig, a)
            sig['index_ok'], sig['index_desc'] = ok, desc
            signals.append((sig, score, grade, items, plan))
        if watch:
            watch['code'], watch['name'], watch['industry'] = code, name or code, industry
            wi0 = watch['i0']
            watch['gap_top_raw'] = float(a.h[wi0 - 1])               # 缺口上沿（原始价）
            watch['break_price_raw'] = float(a.h[wi0])               # T0 最高价（原始价）
            watch['total_mv'] = (float(mv) if mv is not None else None)
            watch_list.append(watch)
        if early:
            early['code'], early['name'], early['industry'] = code, name or code, industry
            ei0 = early['i0']
            early['gap_top_raw'] = float(a.h[ei0 - 1])               # 缺口上沿（原始价）
            early['break_price_raw'] = float(a.h[ei0])               # T0 最高价（原始价）
            early['total_mv'] = (float(mv) if mv is not None else None)
            early_list.append(early)

    signals.sort(key=lambda x: -x[1])
    watch_list.sort(key=lambda w: (w['days_pullback'], -w['t0_vol_mult']))
    early_list.sort(key=lambda w: (w['days_pullback'], -w['t0_vol_mult']))

    meta = {
        'universe': universe, 'lookback': len(dates), 'now': time.strftime('%Y-%m-%d %H:%M:%S'),
        'min_mv': args.min_mv,
        'idx_desc_sh': idx_desc_sh, 'idx_desc_gem': idx_desc_gem,
        'theme_ready': bool(theme_ctx.get('ready')),
        'theme_reason': theme_ctx.get('reason', ''),
        'theme_top3': theme_ctx.get('top3') or [],
    }
    text = render(signals, watch_list, early_list, date, meta)
    output = os.path.abspath(args.output or os.path.join(OUTPUT_DIR, 'e08_scan_%s.md' % date))
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, 'w', encoding='utf-8') as fh:
        fh.write(text)

    payload = {
        'trade_date': date,
        'universe': universe,
        'signals': [{
            'code': s['code'], 'name': s['name'], 'industry': s['industry'],
            'grade': g, 'score': sc,
            'date_t0': s['date_t0'], 'date_rb': s['date_rb'],
            'gap': round(s['gap'], 4), 'gap_top': round(s['gap_top_raw'], 3),
            'total_mv_wan': (round(s['total_mv'], 0) if s.get('total_mv') else None),
            't0_pct': round(s['t0_pct'], 2), 't0_vol_mult': round(s['t0_vol_mult'], 2),
            'n': s['n'], 'vol_avg_ratio': round(s['vol_avg_ratio'], 3),
            'vol_min_ratio': round(s['vol_min_ratio'], 3),
            'rb_pct': round(s['rb_pct'], 2), 'rb_vol_mult': round(s['rb_vol_mult'], 2),
            'close': plan['close'], 'entry': [plan['entry_low'], plan['entry_high']],
            'hard_stop': plan['hard_stop'], 'trail_stop': plan['trail_stop'],
            'target1': plan['target1'], 'target2': plan['target2'],
            'ma5': plan['ma5'], 'winner': (round(s['winner'], 4) if s['winner'] is not None else None),
            'ma_bullish': s['ma']['bullish'], 'theme_hit': s['theme_hit'],
            'index_ok': s['index_ok'],
            'dims': [{'name': n_, 'score': v_, 'why': w_} for n_, v_, w_ in items],
        } for s, sc, g, items, plan in signals],
        'watch': [{
            'code': w['code'], 'name': w['name'], 'date_t0': w['date_t0'],
            'days_pullback': w['days_pullback'], 'gap': round(w['gap'], 4),
            'gap_top': round(w['gap_top_raw'], 3), 'break_price': round(w['break_price_raw'], 3),
            'total_mv_wan': (round(w['total_mv'], 0) if w.get('total_mv') else None),
            'vol_avg_ratio': round(w['vol_avg_ratio'], 3),
            'earliest_in': w['earliest_in'], 'latest_in': w['latest_in'],
        } for w in watch_list],
        'early': [{
            'code': w['code'], 'name': w['name'], 'date_t0': w['date_t0'],
            'days_pullback': w['days_pullback'], 'gap': round(w['gap'], 4),
            'gap_top': round(w['gap_top_raw'], 3), 'break_price': round(w['break_price_raw'], 3),
            'total_mv_wan': (round(w['total_mv'], 0) if w.get('total_mv') else None),
            'vol_avg_ratio': round(w['vol_avg_ratio'], 3),
            'earliest_in': w['earliest_in'], 'latest_in': w['latest_in'],
        } for w in early_list],
    }
    jpath = os.path.join(os.path.dirname(output), 'e08_signals_%s.json' % date)
    with open(jpath, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)

    conn.close()
    stats = {'date': date, 'universe': universe, 'hit': n_ok,
             'signals': len(signals), 'watch': len(watch_list), 'early': len(early_list),
             'output': output, 'json': jpath, 'elapsed_s': round(time.time() - t0, 1)}

    if args.push:
        subject = '%s E08 日报 ｜ 信号%d ｜ 回踩池%d ｜ 早鸟池%d' % (
            date, len(signals), len(watch_list), len(early_list))
        subtitle = '跳空不补缺 → 缩量回踩 → 放量再突破'
        stats['mail_to'] = args.mail_to
        stats['mail_ok'] = send_email(text, date, subject, subtitle, mail_to=args.mail_to)

    print(json.dumps(stats, ensure_ascii=False))


if __name__ == '__main__':
    main()
