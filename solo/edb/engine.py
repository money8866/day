# -*- coding: utf-8 -*-
"""
EDB 核心引擎 —— Extreme Dry-up Breakout（极致缩量 → 爆量突破）
================================================================================
结构：
    长期横盘 → 价格波动收缩 → 成交量持续下降 → DryUpRatio<=0.45 → 极致缩量
    → 突然 BreakoutVR>=2.0 → 突破长期平台 → ClosePosition>=0.75 → EDB
    → 不追高 → 缩量回踩 → 平台不破 → 再次放量 → EDB_REBREAKOUT

核心原则（对应规范第 22 条）：
  · 不是「所有2倍放量」都叫 EDB；
  · 下跌途中突然放量反弹不是 EDB（横盘有效性 + 死猫跳 + 趋势过滤三重拦截）；
  · 单日缩量不等于极致缩量（需最近10日持续性 + MinVOL10 确认）；
  · 计算均量一律排除当日，绝不让突破日巨量污染 MA20_VOL；
  · VR 超过 5 倍不再加分；涨停不自动加分；突破超过 10% 不进主池；
  · EDB_SCORE 不直接等于 BUY，只输出到分层与建议动作。

无未来函数：所有窗口均为「截至当前 bar 的 backward rolling」，且突破日均量取突破日前 20/120 日。
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional

from .config import EDB_CONFIG, STATUS_CN, ACTION_CN

cfg = EDB_CONFIG


# ═══════════════════════════════════════════════════════════
# 结果对象
# ═══════════════════════════════════════════════════════════
@dataclass
class EdbResult:
    ts_code: str = ''
    name: str = ''
    industry: str = ''
    date: str = ''
    status: str = 'NO_SIGNAL'
    action: str = ''
    edb_score: float = 0.0
    risk_penalty: float = 0.0
    edb_score_adj: float = 0.0
    score_detail: dict = field(default_factory=dict)

    # Base / 压缩
    base_days: int = 0
    base_range: float = np.nan
    range60: float = np.nan
    atr20: float = np.nan
    atr120: float = np.nan
    atr_ratio: float = np.nan

    # 量能
    ma20_vol: float = np.nan
    ma120_vol: float = np.nan
    dry_up_ratio: float = np.nan
    minvol10_ratio: float = np.nan
    persist_cnt_50: int = 0
    persist_cnt_45: int = 0

    # 平台 / 突破
    platform_high: float = np.nan
    platform_low: float = np.nan
    hh60: float = np.nan
    hh90: float = np.nan
    hh120: float = np.nan
    close: float = np.nan
    breakout: bool = False
    breakout_vr: float = np.nan
    breakout_distance: float = np.nan
    day_gain: float = np.nan
    close_position: float = np.nan
    breakout_date: str = ''
    days_after: int = -1

    # 趋势
    ma20: float = np.nan
    ma60: float = np.nan
    ma120: float = np.nan
    ma60_slope20: float = np.nan
    ret20_pre: float = np.nan
    dist_250d_high: float = np.nan
    near_52w_high: bool = False

    # 联动
    hvt_state: str = ''
    hvt_band: str = ''
    hvt_quality: float = np.nan
    fundamental: str = 'NA'
    fund_np_yoy: float = np.nan
    fund_q2_yoy: float = np.nan
    event_driven: bool = False
    event_types: list = field(default_factory=list)

    risk_tags: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    note: str = ''


# ═══════════════════════════════════════════════════════════
# 指标计算（单点，仅使用 <= idx 的数据）
# ═══════════════════════════════════════════════════════════
def _base_win(high: np.ndarray, low: np.ndarray, i: int) -> tuple:
    """最长有效横盘窗口：(base_days, base_range, base_ok)

    自长到短取第一个 Range<=base_range_max 的窗口；无有效窗口则 base_ok=False。
    """
    for W in cfg['base_candidates']:
        if W > i:
            continue
        hh = float(np.max(high[i - W:i]))
        ll = float(np.min(low[i - W:i]))
        if ll <= 0:
            continue
        rw = (hh - ll) / ll
        if rw <= cfg['base_range_max']:
            return int(W), rw, True
    return 60, np.nan, False


def _platform_high(high: np.ndarray, base_days: int, i: int) -> float:
    """平台高点（规范第 7 节）。

    优先 max(HH60, HH90)；但 HH90 只有在 90 日窗口本身构成有效平台（base_days>=90）
    时才可作为平台高点 —— 否则它是「已经失效的早期极端价」，会把平台抬到远离现价处，
    使突破/接近平台永远无法成立。此时退回 HH60。
    """
    hh60 = float(np.max(high[i - cfg['platform_window_short']:i]))
    if base_days >= cfg['platform_window_long']:
        hh90 = float(np.max(high[i - cfg['platform_window_long']:i]))
        return max(hh60, hh90)
    return hh60


def _metrics(df: pd.DataFrame, idx: int) -> Optional[dict]:
    n = len(df)
    if idx < cfg['min_bars'] - 1 or idx >= n:
        return None
    high = df['high'].to_numpy(dtype=float)
    low = df['low'].to_numpy(dtype=float)
    close = df['close'].to_numpy(dtype=float)
    vol = df['vol'].to_numpy(dtype=float)
    amount = df['amount'].to_numpy(dtype=float) if 'amount' in df.columns else None
    i = idx
    c = close[i]
    if not np.isfinite(c) or c <= 0:
        return None

    m = {'idx': i, 'close': float(c), 'date': str(df['trade_date'].iloc[i])}

    # ── 流动性阀（近20日均成交额，亿元）──
    if amount is not None:
        a20 = float(np.nanmean(amount[i - 19:i + 1]))
        m['amount_yi_20'] = a20 / 1e5 if np.isfinite(a20) else np.nan
    else:
        m['amount_yi_20'] = np.nan

    # ── 当日涨幅 / 收盘质量 ──
    prev = close[i - 1]
    m['day_gain'] = (c / prev - 1.0) * 100 if prev > 0 else 0.0
    rng = high[i] - low[i]
    m['close_position'] = float((c - low[i]) / rng) if rng > 0 else 1.0

    # ── 均线（含当日）──
    m['ma20'] = float(np.mean(close[i - 19:i + 1]))
    m['ma60'] = float(np.mean(close[i - 59:i + 1]))
    m['ma120'] = float(np.mean(close[i - 119:i + 1]))
    if not np.isfinite(m['ma120']):
        return None
    ma60_prev = float(np.mean(close[i - 79:i - 19])) if i >= 79 else np.nan
    m['ma60_slope20'] = (m['ma60'] / ma60_prev - 1.0) if (ma60_prev and np.isfinite(ma60_prev)) else np.nan

    # ── 突破前量能（严格排除当日，防止巨量污染 MA20_VOL）──
    ma20_vol = float(np.mean(vol[i - 20:i]))
    ma120_vol = float(np.mean(vol[i - 120:i]))
    if not (ma120_vol > 0) or not np.isfinite(ma20_vol):
        return None
    m['ma20_vol'] = ma20_vol
    m['ma120_vol'] = ma120_vol
    m['dry_up_ratio'] = ma20_vol / ma120_vol

    # 持续缩量：最近10日（截至 i-1）
    vol_series = pd.Series(vol)
    ma120_ser = vol_series.rolling(120).mean().to_numpy()
    ratios = []
    for t in range(i - cfg['persist_days'], i):
        mv = ma120_ser[t]
        ratios.append(vol[t] / mv if (mv and mv > 0) else np.nan)
    ratios = [r for r in ratios if np.isfinite(r)]
    m['persist_cnt_50'] = int(sum(1 for r in ratios if r < cfg['persist_thr_pass']))
    m['persist_cnt_45'] = int(sum(1 for r in ratios if r < cfg['persist_thr_strong']))
    m['minvol10_ratio'] = float(np.min(vol[i - cfg['persist_days']:i])) / ma120_vol

    # ── 价格压缩 ──
    hh60 = float(np.max(high[i - 60:i]))
    ll60 = float(np.min(low[i - 60:i]))
    m['range60'] = (hh60 - ll60) / ll60 if ll60 > 0 else np.nan
    hh90 = float(np.max(high[i - 90:i]))
    hh120 = float(np.max(high[i - 120:i]))
    m['hh60'], m['hh90'], m['hh120'] = hh60, hh90, hh120

    # ── ATR 收敛（截至 i-1，不被突破日放大）──
    pc = np.empty_like(close)
    pc[1:] = close[:-1]
    pc[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - pc), np.abs(low - pc)))
    atr20 = float(np.mean(tr[i - 20:i]))
    atr120 = float(np.mean(tr[i - 120:i]))
    m['atr20'], m['atr120'] = atr20, atr120
    m['atr_ratio'] = atr20 / atr120 if atr120 > 0 else np.nan

    # ── Base：取最长有效横盘窗口 ──
    base_days, base_range, base_ok = _base_win(high, low, i)
    m['base_days'], m['base_range'], m['base_ok'] = base_days, base_range, base_ok

    # ── 趋势 / 单边下跌检查 ──
    seg = close[i - 20:i]
    runmax = np.maximum.accumulate(seg)
    dd20 = float(np.min(seg / runmax - 1.0)) if len(seg) else 0.0
    down_days20 = int(np.sum(np.diff(seg) < 0)) if len(seg) > 1 else 0
    m['dd20'], m['down_days20'] = dd20, down_days20
    ret20_pre = (close[i - 1] / close[i - 21] - 1.0) if i >= 21 else np.nan
    m['ret20_pre'] = float(ret20_pre)
    m['base_valid'] = bool(
        np.isfinite(m['ma60_slope20']) and m['ma60_slope20'] >= cfg['base_ma60_slope_min']
        and dd20 >= cfg['base_dd20_min']
        and down_days20 <= cfg['base_down_days_max']
    )
    m['down_trend_risk'] = bool(
        np.isfinite(m['ma60_slope20']) and m['ma60_slope20'] < cfg['ma60_slope_down']
        and c < m['ma60']
    )

    # ── 平台高点（规范第 7 节：避免早期失效极端价）──
    m['platform_high'] = _platform_high(high, base_days, i)
    m['platform_low'] = float(np.min(low[i - base_days:i]))

    # ── 距250日高点 ──
    hh250 = float(np.max(high[max(0, i - 249):i + 1]))
    m['dist_250d_high'] = (c / hh250 - 1.0) if hh250 > 0 else np.nan
    m['near_52w_high'] = bool(np.isfinite(m['dist_250d_high']) and
                              m['dist_250d_high'] > cfg['near_52w_high'])

    # ── 今日是否构成突破 ──
    ph = m['platform_high']
    m['breakout'] = bool(ph > 0 and c >= ph)
    m['breakout_vr'] = float(vol[i] / ma20_vol) if ma20_vol > 0 else np.nan
    m['breakout_distance'] = ((c - ph) / ph) if ph > 0 else np.nan
    return m


def _breakout_at(df: pd.DataFrame, j: int):
    """bar j 是否构成爆量突破（用 j 之前的数据定平台与均量）。返回 (platform_high, vr) 或 None"""
    if j < cfg['platform_window_long'] or j >= len(df):
        return None
    high = df['high'].to_numpy(dtype=float)
    low = df['low'].to_numpy(dtype=float)
    close = df['close'].to_numpy(dtype=float)
    vol = df['vol'].to_numpy(dtype=float)
    base_days, _, _ = _base_win(high, low, j)
    ph = _platform_high(high, base_days, j)
    v20 = float(np.mean(vol[j - 20:j]))
    if ph <= 0 or v20 <= 0 or close[j] < ph:
        return None
    vr = float(vol[j] / v20)
    if vr < cfg['vr_valid']:
        return None
    return ph, vr


# ═══════════════════════════════════════════════════════════
# 评分（A 15 + B 15 + C 25 + D 20 + E 15 + F 10 = 100）
# ═══════════════════════════════════════════════════════════
def _score(m: dict, is_breakout: bool) -> tuple:
    d = {}
    # A 长期横盘（V1.2 校准：≥60 档 8→10 —— 32/36 突破日样本 base_days∈[60,90)，
    # 8/15=53% 得分率属体系性压分，证据链见 config.py 第九节 V1.2 注释）
    bd = m['base_days']
    if bd > 150:
        a = 15
    elif bd >= 90:
        a = 12
    elif bd >= 60:
        a = 10
    else:
        a = 0
    d['A_base'] = a

    # B 价格压缩
    r = m['range60']
    b = 0
    if np.isfinite(r):
        if r <= cfg['range60_max']:
            b = 8
        if r <= cfg['base_range_strong']:
            b = 11
        if r <= cfg['base_range_very_strong']:
            b = 13
        if r <= cfg['range60_extreme']:
            b = 15
    d['B_range'] = b

    # C 极致缩量（含持续缩量确认；单日偶然缩量封顶）
    dry = m['dry_up_ratio']
    c = 0
    if np.isfinite(dry):
        if dry <= cfg['dry_up_normal']:
            c = 10
        if dry <= cfg['dry_up_pass']:
            c = 18
        if dry <= cfg['dry_up_strong']:
            c = 25
    if m['persist_cnt_50'] >= cfg['persist_cnt_pass']:
        c += 3
    if m['persist_cnt_45'] >= cfg['persist_cnt_strong']:
        c += 5
    mv10 = m['minvol10_ratio']
    if np.isfinite(mv10):
        if mv10 > cfg['persist_thr_pass']:
            c = min(c, 10)
        elif mv10 > cfg['minvol10_pass']:
            c = min(c, 18)
    c = min(c, 25)
    d['C_dry_up'] = c

    # D 爆量（VR>5 不再加分）
    dd = 0
    vr = m['breakout_vr'] if is_breakout else np.nan
    if is_breakout and np.isfinite(vr):
        if vr >= cfg['vr_valid']:
            dd = 10
        if vr >= cfg['vr_strong']:
            dd = 15
        if vr >= cfg['vr_extreme']:
            dd = 20
    d['D_vr'] = dd

    # E 突破质量
    e = 0
    if is_breakout:
        if m['breakout']:
            e += 5
        bdist = m['breakout_distance']
        if np.isfinite(bdist) and cfg['bd_ideal'][0] <= bdist <= cfg['bd_ideal'][1]:
            e += 5
        cp = m['close_position']
        if cp >= cfg['cp_strong']:
            e += 5
        if cp < cfg['cp_weak']:
            e -= 5
    d['E_breakout'] = e

    # F 趋势背景
    f = 0
    if m['ma20'] >= m['ma60']:
        f += 4
    if np.isfinite(m['ma60_slope20']) and m['ma60_slope20'] >= cfg['ma60_slope_flat']:
        f += 3
    if np.isfinite(m['ret20_pre']) and m['ret20_pre'] >= cfg['ret20_no_drop']:
        f += 3
    d['F_trend'] = f

    total = a + b + c + dd + e + f
    return float(max(0.0, min(100.0, total))), d


def _risk(m: dict, is_breakout: bool) -> tuple:
    pen = 0.0
    tags = []
    if is_breakout:
        vr = m['breakout_vr']
        if np.isfinite(vr):
            if vr > cfg['vr_climax_hard']:
                pen += 8
                tags.append('CLIMAX_VOL')
            elif vr > cfg['vr_climax']:
                pen += 5
                tags.append('CLIMAX_VOL')
        dry = m['dry_up_ratio']
        if np.isfinite(dry) and dry > cfg['dry_up_strong']:
            tags.append('DRY_UP_SHALLOW')
        cp = m['close_position']
        if cp < cfg['cp_weak']:
            pen += 5
            tags.append('BREAKOUT_WEAK')
        bdist = m['breakout_distance']
        if np.isfinite(bdist):
            if bdist > cfg['bd_max']:
                pen += 8
                tags.append('BREAKOUT_EXTENDED')
            elif bdist > cfg['bd_penalty']:
                pen += 5
                tags.append('BREAKOUT_EXTENDED')
        dg = m['day_gain']
        if dg > cfg['day_gain_ext'] and np.isfinite(vr) and vr > cfg['vr_extreme']:
            tags.append('EXTENSION_RISK')
        if dg >= cfg['day_gain_ext'] and np.isfinite(vr) and vr >= cfg['vr_climax']:
            tags.append('CLIMAX_WARNING')
    if m['near_52w_high']:
        tags.append('NEAR_52W_HIGH')
    if m['down_trend_risk']:
        pen += abs(cfg['down_trend_penalty'])
        tags.append('DOWN_TREND_RISK')
    r20 = m['ret20_pre']
    if np.isfinite(r20):
        if r20 <= cfg['dead_cat_ret20']:
            pen += abs(cfg['dead_cat_penalty'])
            tags.append('DEAD_CAT_RISK')
        elif r20 <= cfg['ret20_no_drop']:
            pen += abs(cfg['dead_cat_soft_penalty'])
            tags.append('RALLY_WEAK_PRE')
    return pen, tags


def _hard_blocked(m: dict) -> str:
    """回测驱动的硬排除闸门：命中则不得进入主买入池（EDB / EDB_STRONG），只降级观察。

    返回命中的原因标签（'' 表示未命中）。

    依据（backtest 20240601~20260918，去重 335 事件）：
      · CLIMAX_VOL —— BreakoutVR > vr_climax(5.0)：n=13，T+5 -2.40% / T+20 -5.62% /
                      胜率 30.8%，负期望；对照 3.0~5.0 的 T+20 +25.46%。
                      （强证据，默认保留）
      · DRY_UP_SHALLOW —— 单边 dry 闸门：突破日 0.35~0.40 弱（n=5，T+20 -2.87%）
                      但 0.40~0.45 强（n=4，T+20 +23.70%，含最大赢家），单边闸门
                      无法分离两档，高收益优先 → 默认关闭（dry_up_tier_block=False）。
                      DRY_UP_SHALLOW 风险标签仍在 _risk 中输出，仅作信息不扣分。

    注意：只用于「突破日」判定。突破后的回踩 / 再突破不受限。
    """
    if cfg['climax_hard_block']:
        vr = m['breakout_vr']
        if np.isfinite(vr) and vr > cfg['vr_climax']:
            return 'CLIMAX_VOL'
    if cfg['dry_up_tier_block']:
        dry = m['dry_up_ratio']
        if np.isfinite(dry) and dry > cfg['dry_up_strong']:
            return 'DRY_UP_SHALLOW'
    return ''


# ═══════════════════════════════════════════════════════════
# 引擎
# ═══════════════════════════════════════════════════════════
class EdbEngine:

    def score(self, df: pd.DataFrame, ts_code: str = '', name: str = '',
              industry: str = '', date: str = '', hvt: dict = None,
              fundamental: dict = None, event: dict = None) -> Optional[EdbResult]:
        if df is None or len(df) == 0:
            return None
        date = str(date)
        if str(df['trade_date'].iloc[-1]) != date:
            return None  # 停牌 / 数据缺失：当日无完整成交数据
        i = len(df) - 1
        m = _metrics(df, i)
        if m is None:
            return None
        if not m['base_ok']:
            return None
        a20 = m['amount_yi_20']
        if np.isfinite(a20) and a20 < cfg['min_amount_yi_20']:
            return None

        # ── 最近一次爆量突破（含今日；今日优先于历史日判断）──
        lo = max(cfg['platform_window_long'], i - cfg['recent_breakout_window'])
        bo = None
        for j in range(i, lo - 1, -1):
            bt = _breakout_at(df, j)
            if bt is not None:
                bo = (j, bt[0], bt[1])
                break
        is_today_bo = bo is not None and bo[0] == i
        # 事件日结构快照：突破类取突破日指标，PRE_EDB 取当日
        mj = m if (bo is None or is_today_bo) else _metrics(df, bo[0])
        src = mj if mj is not None else m

        r = EdbResult(ts_code=ts_code, name=name, industry=industry, date=date)
        # ① 结构 / 事件快照（Base、压缩、量能、平台、突破质量）
        r.base_days = src['base_days']
        r.base_range = src['base_range']
        r.range60 = src['range60']
        r.atr20, r.atr120, r.atr_ratio = src['atr20'], src['atr120'], src['atr_ratio']
        r.ma20_vol, r.ma120_vol = src['ma20_vol'], src['ma120_vol']
        r.dry_up_ratio = src['dry_up_ratio']
        r.minvol10_ratio = src['minvol10_ratio']
        r.persist_cnt_50, r.persist_cnt_45 = src['persist_cnt_50'], src['persist_cnt_45']
        r.platform_high, r.platform_low = src['platform_high'], src['platform_low']
        r.hh60, r.hh90, r.hh120 = src['hh60'], src['hh90'], src['hh120']
        r.breakout = src['breakout']
        r.breakout_vr = src['breakout_vr']
        r.breakout_distance = src['breakout_distance']
        r.day_gain = src['day_gain']
        r.close_position = src['close_position']
        r.ma60_slope20 = src['ma60_slope20']
        r.ret20_pre = src['ret20_pre']
        r.near_52w_high = src['near_52w_high']
        # ② 当日价格与均线（现价口径）
        r.close = m['close']
        r.ma20, r.ma60, r.ma120 = m['ma20'], m['ma60'], m['ma120']
        r.dist_250d_high = m['dist_250d_high']

        # ── 一字/连板 → EVENT_ONLY（不作为 EDB 主信号）──
        event_only = self._event_only(df, m, ts_code)

        # ── 分层 ──
        if event_only:
            r.status = 'EVENT_ONLY'
        elif bo is not None and bo[0] == i:
            r.status = self._classify_today(m)
            r.breakout_date = date
            r.days_after = 0
        elif bo is not None and 0 < (i - bo[0]) <= cfg['recent_breakout_window']:
            r.status = self._classify_after(df, m, bo, src['dry_up_ratio'])
            r.breakout_date = str(df['trade_date'].iloc[bo[0]])
            r.days_after = int(i - bo[0])
        else:
            r.status = self._classify_pre(m)

        if r.status in ('NO_SIGNAL', ''):
            return None

        # ── 评分：突破类用突破日指标，PRE_EDB 用当日指标（即 src）──
        # 突破类 = 今日突破 / 突破后回踩 / 突破后再放量，D、E 两项均取突破日快照
        is_bo = r.status in ('EDB_STRONG', 'EDB', 'EDB_WATCH',
                             'EDB_PULLBACK', 'EDB_REBREAKOUT')
        r.edb_score, r.score_detail = _score(src, is_bo)
        pen, tags = _risk(src, is_bo)
        r.risk_penalty = pen
        r.edb_score_adj = float(max(0.0, min(100.0, r.edb_score - pen)))
        # 二次机会类：以突破日质量为基底，附加回踩/再突破结构分
        if r.status == 'EDB_PULLBACK':
            r.edb_score = min(100.0, r.edb_score + 3)
            r.edb_score_adj = min(100.0, r.edb_score_adj + 3)
        elif r.status == 'EDB_REBREAKOUT':
            r.edb_score = min(100.0, r.edb_score + 5)
            r.edb_score_adj = min(100.0, r.edb_score_adj + 5)

        # 事件驱动：不剔除，单独分类
        ev = event or {}
        if ev.get('event_driven'):
            r.event_driven = True
            r.event_types = ev.get('types', [])
            tags.append('EVENT_DRIVEN')
        else:
            r.event_driven = False

        # 联动标签
        hv = hvt or {}
        r.hvt_state = str(hv.get('hvt_state', ''))
        r.hvt_band = str(hv.get('hvt_band', ''))
        r.hvt_quality = hv.get('hvt_quality', np.nan)
        fn = fundamental or {}
        r.fundamental = str(fn.get('status', 'NA'))
        r.fund_np_yoy = fn.get('np_yoy', np.nan)
        r.fund_q2_yoy = fn.get('q2_yoy', np.nan)

        r.risk_tags = tags
        r.flags = list(dict.fromkeys(tags + [r.status]))
        r.action = self._action(r)
        return r

    # ── 一字板 / 连续涨停 ──
    @staticmethod
    def _event_only(df: pd.DataFrame, m: dict, ts_code: str = '') -> bool:
        i = m['idx']
        close = df['close'].to_numpy(dtype=float)
        high = df['high'].to_numpy(dtype=float)
        low = df['low'].to_numpy(dtype=float)
        code6 = str(ts_code)[:2]
        limit = 20.0 if code6 in ('30', '68') else 10.0
        amp = (high[i] - low[i]) / close[i] if close[i] > 0 else 1.0
        if amp <= cfg['one_line_amp'] and m['day_gain'] >= limit * 0.9:
            return True
        cnt = 0
        t = i
        while t >= 1:
            pc = (close[t] / close[t - 1] - 1.0) * 100 if close[t - 1] > 0 else 0.0
            if pc >= limit - 0.3:
                cnt += 1
                t -= 1
            else:
                break
        return cnt >= cfg['consec_limit_min']

    # ── 今日即突破日 ──
    @staticmethod
    def _classify_today(m: dict) -> str:
        # 缩量前提闸门（规范第 22 节 #1 / 第 25 节核心组合）：
        # 没有缩量的放量突破只是普通放量突破，不允许进入任何 EDB 层级。
        dry = m['dry_up_ratio']
        if not np.isfinite(dry) or dry > cfg['dry_up_normal']:
            return 'NO_SIGNAL'
        score, _ = _score(m, True)
        pen, tags = _risk(m, True)
        # 结构失效（趋势走坏 / 死猫跳）或命中回测硬排除（天量高潮 / 缩量不极致）
        # → 一律不得进入主买入池，只降级为 EDB_WATCH 观察
        blocked = (m['down_trend_risk'] or ('DEAD_CAT_RISK' in tags)
                   or bool(_hard_blocked(m)))
        strong_ok = (score >= cfg['strong_score']
                     and dry <= cfg['dry_up_pass']
                     and m['breakout_vr'] >= cfg['vr_strong']
                     and m['breakout']
                     and m['close_position'] >= cfg['cp_min'])
        if blocked:
            return 'EDB_WATCH' if score >= cfg['watch_score'] else 'NO_SIGNAL'
        if strong_ok:
            return 'EDB_STRONG'
        # EDB 层准入：宽档缩量上限 edb_dry_up_max；EDB_STRONG 仍要求 dry≤dry_up_pass
        if score >= cfg['edb_score'] and dry <= cfg['edb_dry_up_max']:
            return 'EDB'
        if score >= cfg['watch_score']:
            return 'EDB_WATCH'
        return 'NO_SIGNAL'

    # ── 突破已发生（D+1 ~ D+10）：回踩 / 再突破 ──
    @staticmethod
    def _classify_after(df: pd.DataFrame, m: dict, bo: tuple, dry: float) -> str:
        # 同样要求突破前处于缩量状态（突破日快照口径）
        if not np.isfinite(dry) or dry > cfg['dry_up_normal']:
            return 'NO_SIGNAL'
        j, ph = bo[0], bo[1]
        i = m['idx']
        close = df['close'].to_numpy(dtype=float)
        high = df['high'].to_numpy(dtype=float)
        vol = df['vol'].to_numpy(dtype=float)
        k = i - j
        seg_c = close[j + 1:i + 1]
        if len(seg_c) == 0:
            return 'NO_SIGNAL'
        seg_vol = float(np.mean(vol[j + 1:i + 1]))
        shrink = seg_vol < vol[j] * cfg['pullback_vol_shrink']
        hold = bool(np.min(seg_c) >= ph * cfg['pullback_floor']) and close[i] >= ph * cfg['pullback_hold']
        # 再突破：回踩后重新放量 + 突破短期高点
        ma5_vol = float(np.mean(vol[i - 5:i]))
        short_high = float(np.max(high[j + 1:i])) if i > j + 1 else np.nan
        vol_ok = ma5_vol > 0 and vol[i] > cfg['rebreakout_vol_ratio'] * ma5_vol
        if (k <= cfg['recent_breakout_window'] and vol_ok and np.isfinite(short_high)
                and close[i] > short_high and np.min(seg_c) >= ph * cfg['pullback_floor']):
            return 'EDB_REBREAKOUT'
        if k <= cfg['pullback_window'] and shrink and hold:
            return 'EDB_PULLBACK'
        return 'NO_SIGNAL'

    # ── 尚未突破：PRE_EDB ──
    @staticmethod
    def _classify_pre(m: dict) -> str:
        ph = m['platform_high']
        near = (ph > 0 and m['close'] >= ph * (1.0 - cfg['pre_max_gap']))
        if (m['base_days'] >= cfg['base_days_min']
                and m['base_ok'] and m['base_valid']
                and m['dry_up_ratio'] <= cfg['dry_up_pass']
                and m['range60'] <= cfg['range60_max']
                and near
                and not m['breakout']):
            return 'PRE_EDB'
        return 'NO_SIGNAL'

    # ── 建议动作（EDB_SCORE 不直接等于 BUY）──
    @staticmethod
    def _action(r: EdbResult) -> str:
        if r.status == 'PRE_EDB':
            return 'WATCH'
        if r.status == 'EVENT_ONLY':
            return 'EVENT_ONLY_WATCH'
        if r.status == 'EDB_PULLBACK':
            # V1.3 质量闸门：突破日已远离平台 ≥ pb_bd_reject 的「回踩」未回到
            # 承接位，IS/OOS 双窗一致负收益（证据链见 config 第八节）→ 等回踩至平台
            if np.isfinite(r.breakout_distance) and r.breakout_distance >= cfg['pb_bd_reject']:
                return 'WAIT_DEEPER_PULLBACK'
            return 'BUY_ON_PULLBACK'
        if r.status == 'EDB_REBREAKOUT':
            return 'REBREAKOUT_BUY_CANDIDATE'
        major = set(r.risk_tags) & {'CLIMAX_WARNING', 'BREAKOUT_WEAK', 'DEAD_CAT_RISK',
                                    'DOWN_TREND_RISK', 'BREAKOUT_EXTENDED'}
        bd = r.breakout_distance
        if r.status == 'EDB_STRONG':
            ok = (np.isfinite(bd) and bd <= cfg['bd_ideal'][1]
                  and r.close_position >= cfg['cp_min'] and not major)
            return 'BREAKOUT_READY' if ok else 'WAIT_PULLBACK'
        if r.status == 'EDB':
            ok = (np.isfinite(bd) and bd <= cfg['bd_ideal'][1]
                  and r.close_position >= cfg['cp_min'] and not major)
            return 'BREAKOUT_CONFIRM' if ok else 'WATCH'
        return 'WATCH'


# ═══════════════════════════════════════════════════════════
# 结果 → 字典（标准化候选池字段，供 HVT / TE 二筛）
# ═══════════════════════════════════════════════════════════
def result_to_dict(r: EdbResult, rank: int = 0) -> dict:
    return {
        '排名': rank,
        '代码': r.ts_code,
        '名称': r.name,
        '行业': r.industry,
        'EDB状态': r.status,
        'EDB状态说明': STATUS_CN.get(r.status, ''),
        'EDB_SCORE': round(r.edb_score, 1),
        '风险扣分': round(r.risk_penalty, 1),
        'EDB_SCORE_ADJ': round(r.edb_score_adj, 1),
        'BaseDays': r.base_days,
        'BaseRange': round(r.base_range * 100, 2) if np.isfinite(r.base_range) else np.nan,
        'Range60': round(r.range60 * 100, 2) if np.isfinite(r.range60) else np.nan,
        'MA20_VOL': round(r.ma20_vol, 1),
        'MA120_VOL': round(r.ma120_vol, 1),
        'DryUpRatio': round(r.dry_up_ratio, 3) if np.isfinite(r.dry_up_ratio) else np.nan,
        'MinVOL10_Ratio': round(r.minvol10_ratio, 3) if np.isfinite(r.minvol10_ratio) else np.nan,
        '持续缩量_lt050': r.persist_cnt_50,
        '持续缩量_lt045': r.persist_cnt_45,
        'BreakoutVR': round(r.breakout_vr, 2) if np.isfinite(r.breakout_vr) else np.nan,
        'PlatformHigh': round(r.platform_high, 2),
        'PlatformLow': round(r.platform_low, 2),
        '现价': round(r.close, 2),
        'BreakoutDistance': round(r.breakout_distance * 100, 2) if np.isfinite(r.breakout_distance) else np.nan,
        'DayGain': round(r.day_gain, 2),
        'ClosePosition': round(r.close_position, 2),
        'ATR20_ATR120': round(r.atr_ratio, 2) if np.isfinite(r.atr_ratio) else np.nan,
        'MA20': round(r.ma20, 2),
        'MA60': round(r.ma60, 2),
        'MA120': round(r.ma120, 2),
        '距250日高': round(r.dist_250d_high * 100, 2) if np.isfinite(r.dist_250d_high) else np.nan,
        '突破日': r.breakout_date,
        'D+N': r.days_after,
        'HVT状态': r.hvt_state,
        'HVT分档': r.hvt_band,
        '基本面状态': r.fundamental,
        '扣非同比': round(r.fund_np_yoy, 1) if np.isfinite(r.fund_np_yoy) else np.nan,
        '单季Q2同比': round(r.fund_q2_yoy, 1) if np.isfinite(r.fund_q2_yoy) else np.nan,
        'EVENT_DRIVEN': r.event_driven,
        '事件类型': '；'.join(r.event_types),
        '风险标签': '；'.join(r.risk_tags),
        '建议动作': r.action,
        '建议动作说明': ACTION_CN.get(r.action, r.action),
        '评分明细': '；'.join(f'{k}={v:g}' for k, v in r.score_detail.items()),
    }
