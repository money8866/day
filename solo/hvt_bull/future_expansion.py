# -*- coding: utf-8 -*-
"""HVT-BULL V3.1 Future Expansion（未来扩张空间）增强层

规格映射（V3.1 §一/§十三/§十五/§二十）：
- 纯增量层：只读取 V3.0 已有结果（ev.* / rs_maps / 行情 df），不回写、不覆盖任何 V3.0 状态与评分；
- 回答"从当前价格开始，T+10/T+20/T+60/T+120 是否仍具有足够的趋势扩张空间"；
- 区分"已经大涨"与"已经涨完"：TREND_GAIN 不做线性扣分，ExtensionPenalty 由 Continuation Score 调制；
- TREND_GAIN>200% 进入 EXTENDED_TREND_MODE，>500% 进入 EXTREME_TREND_MODE（§四）；
- TREND_GAIN>300% 强制生成"为什么还有空间 / 为什么可能没有空间"（§十九）。

核心公式（§十二/§十三）：FE = BaseExpansion - ExtensionPenalty + ContinuationBonus
  BaseExpansion = 30%延续 + 25%空间 + 15%基本面 + 10%加速度 + 10%生命周期 + 10%平台突破质量
  （TREND_GAIN>300% 时基本面权重提高至 22%、空间降至 18%，§十）
"""

import numpy as np
import pandas as pd


# ---------- 基础工具 ----------

def _f(x, default=0.0):
    try:
        v = float(x)
        return v if np.isfinite(v) else default
    except Exception:
        return default


def _sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=max(2, n // 3)).mean()


def _rsi(close: pd.Series, n: int = 14) -> float:
    d = close.diff()
    up = d.clip(lower=0.0).rolling(n, min_periods=max(2, n // 2)).mean()
    dn = (-d.clip(upper=0.0)).rolling(n, min_periods=max(2, n // 2)).mean()
    rs = up / dn.replace(0.0, np.nan)
    return _f((100.0 - 100.0 / (1.0 + rs)).iloc[-1], 50.0)


def _window_max_dd(close: pd.Series, window: int):
    """近 window 根K线最大回撤 → (dd%, 是否已修复, 修复用时根数)"""
    c = close.astype(float).iloc[-window:].reset_index(drop=True)
    n = len(c)
    if n < 5:
        return 0.0, True, 0
    peak = c.cummax()
    dd = 1.0 - c / peak
    ti = int(dd.values.argmin())
    dd_max = _f(dd.iloc[ti]) * 100.0
    if ti <= 0 or dd_max <= 0.5:
        return dd_max, True, 0
    pk = float(peak.iloc[ti])
    after = c.iloc[ti:]
    hit = after[after >= pk * 0.98]
    if len(hit) > 0:
        return dd_max, True, int(hit.index[0])
    return dd_max, False, n - 1 - ti


# ---------- Trend Lifecycle（§三：MajorBase 检测） ----------

def _detect_major_base(df: pd.DataFrame, regime_ma: int = 120, pad: int = 60):
    """趋势起点/MajorBase 检测。

    优先平台/趋势启动点，不机械使用120日最低价：
    1) 找最后一次收盘价真实跌破 regime_ma*0.97 的位置 last_below（触及但不破不算，保住多年趋势连续性）；
    2) 从 last_below 向后取首次有效收复 regime_ma 的确认点 confirm；
    3) MajorBase = [last_below - pad, confirm] 区间最低 low；
    4) 数据窗内从未跌破（长牛股）→ last_below 落在均线未成形区域，等效取窗口起始平台最低点；
    5) 当前就在 regime_ma 下方（regime 疑似破坏）→ 取下探区间最低点（TREND_GAIN 回到低位）。
    返回 (base_price, base_date, method)；数据不足返回 None。
    """
    c = df['close'].astype(float).reset_index(drop=True)
    lo = df['low'].astype(float).reset_index(drop=True)
    dates = df['trade_date'].reset_index(drop=True)
    n = len(df)
    if n < 40:
        return None
    mv = _sma(c, regime_ma).values
    cv = c.values
    below = np.where(np.isnan(mv) | (cv < mv * 0.97))[0]
    lb = int(below[-1]) if len(below) else 0
    if lb >= n - 3:
        a = max(0, lb - pad)
        i = int(lo.iloc[a:].values.argmin()) + a
        return _f(lo.iloc[i]), str(dates.iloc[i]), 'REGIME_BROKEN_LOW'
    confirm = None
    for k in range(lb, n):
        if not np.isnan(mv[k]) and cv[k] > mv[k]:
            confirm = k
            break
    a = max(0, lb - pad)
    end = confirm if confirm is not None else lb
    i = int(lo.iloc[a:end + 1].values.argmin()) + a
    method = 'REGIME_BASE' if confirm is not None else 'BELOW_MA_LOW'
    return _f(lo.iloc[i]), str(dates.iloc[i]), method


# ---------- Continuation Score 组件（§五） ----------

def _ma_component(cur, ma20, ma60, ma120, sl20, sl60, sl120) -> float:
    """§五.1 均线趋势：多头排列 + 三线向上 + 站上全部均线（满分100）"""
    s = 0.0
    if ma20 > ma60 > ma120:
        s += 40.0
    elif ma20 > ma60 or ma60 > ma120:
        s += 20.0
    s += 15.0 * sum(1 for x in (sl20, sl60, sl120) if x > 0)
    if cur > ma20 and cur > ma60 and cur > ma120:
        s += 15.0
    return min(s, 100.0)


def _rs_component(rs5, rs20, rs60, rs120) -> float:
    """§五.2 相对强度：RS20/60/120 均值打底 + RS5>RS20>RS60 强度扩张奖励"""
    base = (rs20 + rs60 + rs120) / 3.0
    s = base * 0.55
    if rs5 > rs20 > rs60:
        s += 25.0
    elif rs5 > rs20:
        s += 15.0
    if rs20 >= 85:
        s += 20.0
    elif rs20 >= 70:
        s += 12.0
    elif rs20 >= 50:
        s += 6.0
    return min(s, 100.0)


def _close_slopes(c: pd.Series) -> dict:
    """§五.3 趋势加速度：近5/20/60根价格斜率（近似动量）"""
    n = len(c)
    def _m(k):
        return _f(c.iloc[-1] / c.iloc[-1 - k] - 1.0) if n > k else 0.0
    return {'s5': _m(5), 's20': _m(20), 's60': _m(60)}


def _accel_component(sl: dict) -> float:
    s5, s20, s60 = sl['s5'], sl['s20'], sl['s60']
    s = 0.0
    if s5 > s20 > s60:
        s += 55.0
    elif s5 > s20:
        s += 35.0
    elif s20 > 0:
        s += 15.0
    if s60 > 0:
        s += 20.0
    if s20 > 0:
        s += 15.0
    if s5 > 0:
        s += 10.0
    return min(s, 100.0)


def _drawdown_component(c: pd.Series) -> dict:
    """§五.4 回撤质量：回撤浅 + 修复快得分；回撤越来越深则封顶"""
    dd20, rec20, b20 = _window_max_dd(c, 20)
    dd60, rec60, b60 = _window_max_dd(c, 60)
    s = 0.0
    s += 40.0 if dd20 < 5 else 30.0 if dd20 < 10 else 18.0 if dd20 < 15 else 5.0
    s += 30.0 if dd60 < 8 else 22.0 if dd60 < 15 else 12.0 if dd60 < 25 else 4.0
    if rec60:
        s += 30.0 if b60 <= 20 else 20.0 if b60 <= 40 else 10.0
    else:
        s += 10.0 * max(0.0, 1.0 - dd60 / 30.0)
    if dd20 > dd60 * 0.8 and dd60 > 15:
        s = min(s, 40.0)
    return {'score': min(s, 100.0), 'dd20': dd20, 'dd60': dd60,
            'rec60': rec60, 'b60': b60, 'rec20': rec20}


def _hvt_absorb_component(ev) -> float:
    """§五.5 HVT后吸收：只读 V3.0 已有结果（Ac/CQ/锁筹/DRYUP/回撤/量能），不重复计算"""
    s = 0.0
    if ev.strong_locked_chip:
        s += 40.0
    elif ev.locked_chip:
        s += 30.0
    dd = _f(ev.post_max_drawdown)
    s += 25.0 if dd < 8 else 15.0 if dd < 15 else 5.0
    vr = _f(ev.vol_5d_ratio, 1.0)
    s += 20.0 if vr <= 0.6 else 12.0 if vr <= 0.9 else 4.0
    if ev.breakout_date and not ev.false_breakout:
        s += 15.0
    return min(s, 100.0)


def _continuation_score(ma_sc, rs_sc, accel_sc, dd_sc, hvt_sc) -> float:
    """CONTINUATION_SCORE = 25%均线 + 25%相对强度 + 15%加速度 + 20%回撤质量 + 15%HVT吸收"""
    return min(100.0, 0.25 * ma_sc + 0.25 * rs_sc + 0.15 * accel_sc
               + 0.20 * dd_sc + 0.15 * hvt_sc)


# ---------- Extension Risk（V3.1 §六/§七） ----------

def _atr14(df: pd.DataFrame) -> float:
    h, l, c = df['high'], df['low'], df['close']
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    v = _f(tr.rolling(14, min_periods=5).mean().iloc[-1])
    return v if v > 0 else _f(c.iloc[-1]) * 0.02 + 1e-9


def _consec_up(c: pd.Series) -> int:
    n = 0
    for v in reversed(c.diff().iloc[-20:].tolist()):
        if _f(v) > 0:
            n += 1
        else:
            break
    return n


def _channel_pos(c: pd.Series, n: int = 60) -> float:
    """价格在n日通道（均值±2σ）中的位置：0=中轨，1=上轨，<0=下轨"""
    seg = c.iloc[-min(n, len(c)):]
    if len(seg) < 10:
        return 0.0
    sd = _f(seg.std())
    if sd <= 0:
        return 0.0
    return min(1.5, max(-1.5, (_f(seg.iloc[-1]) - _f(seg.mean())) / (2.0 * sd)))


def _extension_risk(df: pd.DataFrame, trend_gain: float) -> dict:
    """EXTENSION_RISK 0~100（§六）。
    注意（§七）：这只是风险度量，不等于趋势结束；对FE的实际惩罚
    由 Continuation Score 调制（§十四），且不按 TREND_GAIN 线性扣分。"""
    c = df['close']
    cur = _f(c.iloc[-1])
    ma20 = _f(c.rolling(20, min_periods=8).mean().iloc[-1])
    ma60 = _f(c.rolling(60, min_periods=20).mean().iloc[-1])
    ma120 = _f(c.rolling(120, min_periods=40).mean().iloc[-1])
    atr = _atr14(df)
    rsi = _rsi(c, 14)

    # 1) TREND_GAIN 非线性分档（§十四基座：<100几乎不惩罚，>500高）
    g = trend_gain
    if g < 0.5:
        tg = 5.0
    elif g < 1.0:
        tg = 12.0
    elif g < 2.0:
        tg = 28.0
    elif g < 3.0:
        tg = 48.0
    elif g < 5.0:
        tg = 68.0
    else:
        tg = 82.0

    # 2) 均线乖离（ATR单位）+ 60/120日乖离
    dev_atr = (cur - ma20) / atr if atr > 0 else 0.0
    d60 = cur / ma60 - 1.0 if ma60 > 0 else 0.0
    d120 = cur / ma120 - 1.0 if ma120 > 0 else 0.0
    ma_dev = min(100.0, max(0.0, (dev_atr - 2.0) * 30.0)
                 + max(0.0, d60 - 0.2) * 120.0 + max(0.0, d120 - 0.5) * 60.0)

    # 3) RSI 极端程度（仅上极端）
    rsi_s = min(100.0, max(0.0, (rsi - 65.0) * 4.0))

    # 4) 连续上涨天数
    nu = _consec_up(c)
    up_s = min(100.0, max(0.0, nu - 3) * 9.0)

    # 5) 距趋势通道上轨（>0.7 开始计入）
    cp = _channel_pos(c, 60)
    ch_s = min(100.0, max(0.0, (cp - 0.70) * 250.0))

    # 6) 高位振幅（20日振幅>15%开始计入）
    seg = c.iloc[-20:]
    lo = _f(seg.min())
    amp20 = _f(seg.max()) / lo - 1.0 if lo > 0 else 0.0
    amp_s = min(100.0, max(0.0, (amp20 - 0.15) * 250.0))

    score = (0.30 * tg + 0.20 * ma_dev + 0.10 * rsi_s
             + 0.10 * up_s + 0.20 * ch_s + 0.10 * amp_s)
    return {'score': min(100.0, score), 'tg_part': tg, 'ma_dev': ma_dev,
            'dev_atr20': dev_atr, 'd60': d60, 'd120': d120, 'rsi': rsi,
            'rsi_part': rsi_s, 'consec_up': nu, 'consec_part': up_s,
            'channel_pos': cp, 'channel_part': ch_s, 'amp20': amp20,
            'amp_part': amp_s}


# ---------- Future Space（V3.1 §九） ----------

def _future_space(df: pd.DataFrame) -> dict:
    """从当前价格起的剩余趋势空间（§九）。
    创新高=空间开阔；中轨附近空间高；上方针量套牢少=压力轻。"""
    c = df['close']
    n = len(c)
    cur = _f(c.iloc[-1])
    if cur <= 0 or n < 30:
        return {'score': 50.0, 'rm20': 0.0, 'rm250': 0.0, 'overhead': 0.0,
                'channel_pos': 0.0}

    def hh(k):
        return _f(c.iloc[-min(k, n):].max())

    def room(resist):
        return (resist - cur) / cur if resist > 0 else 0.0

    rm20, rm60 = room(hh(20)), room(hh(60))
    rm120, rm250, rmh = room(hh(120)), room(hh(250)), room(_f(c.max()))

    # 1) 平台/前高/各周期高点空间（创新高给满档=空间开阔）
    s_room = 0.0
    s_room += 20.0 if rm20 <= 0.02 else min(20.0, rm20 * 30.0)
    s_room += 20.0 if rm60 <= 0.02 else min(20.0, rm60 * 15.0)
    s_room += 15.0 if rm120 <= 0.02 else min(15.0, rm120 * 10.0)
    s_room += 15.0 if rm250 <= 0.02 else min(15.0, rm250 * 6.0)
    s_room += 10.0 if rmh <= 0.02 else min(10.0, rmh * 4.0)

    # 2) 趋势通道位置：中轨(≈0.45)附近最优，逼近上轨(>0.9)扣减
    cp = _channel_pos(c, 60)
    if cp <= 0.9:
        s_ch = max(5.0, min(25.0, 25.0 - abs(cp - 0.45) * 30.0))
    else:
        s_ch = max(0.0, 25.0 - (cp - 0.9) * 200.0)

    # 3) 上方压力密集区：近250日中高于现价的成交量占比（套牢盘）
    seg = df.iloc[-min(250, n):]
    tot = _f(seg['vol'].sum())
    overhead = _f(seg.loc[seg['close'] > cur, 'vol'].sum() / tot) if tot > 0 else 0.0
    s_over = min(20.0, max(0.0, (1.0 - overhead) * 20.0))

    score = min(100.0, (s_room + s_ch + s_over) / 1.25)
    return {'score': score, 'rm20': rm20, 'rm60': rm60, 'rm120': rm120,
            'rm250': rm250, 'rm_hist': rmh, 'channel_pos': cp,
            'overhead': overhead}


# ---------- 基本面承载 / 平台突破质量 / Lifecycle（§十/§十二/§三） ----------

def _fundamental_support(ev) -> dict:
    """只读复用V3.0已有基本面/资金/板块数据（§十），不重复计算。"""
    base = _f(getattr(ev, 'fundamental_score', 50.0), 50.0)
    mq = _f(getattr(ev, 'money_quality_score', 0.0))
    sec = _f(getattr(ev, 'sector_strength', 0.0))
    score = min(100.0, 0.60 * base + 0.20 * min(100.0, mq) + 0.20 * min(100.0, sec))
    return {'score': score, 'base': base, 'money': mq, 'sector': sec}


def _platform_breakout_quality(ev) -> float:
    """只读复用V3.0：平台突破/有效突破/锁筹（§十二第6项）。"""
    s = 0.0
    if getattr(ev, 'platform_breakout', False):
        s += 35.0
    if str(getattr(ev, 'breakout_date', '') or '').strip():
        s += 10.0
    if not getattr(ev, 'false_breakout', False):
        s += 10.0
    if _f(getattr(ev, 'breakout_close_pos', 0.0)) >= 0.7:
        s += 15.0
    if getattr(ev, 'strong_locked_chip', False):
        s += 20.0
    elif getattr(ev, 'locked_chip', False):
        s += 12.0
    return min(100.0, s)


def _lifecycle(trend_gain: float) -> str:
    """Trend Lifecycle 分类（§三）"""
    if trend_gain < 0.5:
        return 'EARLY'
    if trend_gain < 1.0:
        return 'DEVELOPING'
    if trend_gain < 2.0:
        return 'MATURE'
    if trend_gain < 3.0:
        return 'EXTENDED'
    if trend_gain < 5.0:
        return 'STRONG_EXTENDED'
    return 'EXTREME_EXTENDED'


_LC_SCORE = {'EARLY': 90.0, 'DEVELOPING': 85.0, 'MATURE': 75.0,
             'EXTENDED': 60.0, 'STRONG_EXTENDED': 50.0, 'EXTREME_EXTENDED': 45.0}


# ---------- 四周期 Future Expansion（§十一） ----------

def _fe10(df, ev, space: dict, rs5, rs20) -> float:
    """FE10：突破状态+平台空间+短期斜率+RS加速度"""
    c = df['close']
    s5 = _f(_close_slopes(c)['s5'])
    s = 0.0
    s += 30.0 if getattr(ev, 'platform_breakout', False) else 12.0
    if not getattr(ev, 'false_breakout', False):
        s += 8.0
    rm20 = space.get('rm20', 0.0)
    s += 22.0 if rm20 <= 0.02 else max(0.0, 22.0 - rm20 * 40.0)
    s += min(20.0, max(0.0, s5 * 400.0))
    ra = rs5 - rs20
    s += min(20.0, max(0.0, 10.0 + ra * 0.8)) if rs20 > 0 else 10.0
    return min(100.0, s)


def _fe20(df, ev, rs20) -> float:
    """FE20：新趋势段/回踩风险/新高概率"""
    c = df['close']
    ma20 = _f(c.rolling(20, min_periods=8).mean().iloc[-1])
    sl20 = _f(c.iloc[-1] / c.iloc[-21] - 1.0) if len(c) > 21 else 0.0
    dd = _drawdown_component(c)
    s = 0.0
    above = _f(c.iloc[-1]) / ma20 - 1.0 if ma20 > 0 else 0.0
    s += 18.0 if ma20 > 0 and sl20 > 0 else 4.0
    s += 12.0 if 0.0 <= above <= 0.08 else (6.0 if above > 0.08 else 0.0)
    s += min(25.0, max(0.0, sl20 * 300.0))
    s += max(0.0, 20.0 - abs(dd['dd20']) * 2.0)  # 回踩风险低→高分
    s += min(25.0, max(0.0, (rs20 - 50.0) * 0.5)) if rs20 > 0 else 12.0
    if getattr(ev, 'platform_breakout', False):
        s += 5.0
    return min(100.0, s)


def _fe60(df, sec, space: dict, fund: dict, rs60) -> float:
    """FE60：MA60/RS60/行业景气/通道位置/基本面"""
    c = df['close']
    ma60 = _f(c.rolling(60, min_periods=20).mean().iloc[-1])
    ma20 = _f(c.rolling(20, min_periods=8).mean().iloc[-1])
    s = 0.0
    s += 20.0 if ma60 > 0 and ma20 > ma60 else 5.0
    s += min(25.0, max(0.0, (rs60 - 40.0) * 0.5)) if rs60 > 0 else 12.0
    s += min(15.0, sec * 0.15)
    cp = space.get('channel_pos', 0.0)
    s += 15.0 if -0.2 <= cp <= 0.8 else (8.0 if cp < -0.2 else 0.0)
    s += min(25.0, fund['score'] * 0.25)
    return min(100.0, s)


def _fe120(ev, trend_gain, fund: dict, sec, c: pd.Series) -> float:
    """FE120：基本面承载+行业周期+长趋势+生命周期+右尾潜力"""
    s = 0.0
    s += min(35.0, fund['score'] * 0.35)
    s += min(15.0, sec * 0.15)
    ma120 = _f(c.rolling(120, min_periods=40).mean().iloc[-1])
    if ma120 > 0 and _f(c.iloc[-1]) > ma120:
        s += 20.0
    lc = _lifecycle(trend_gain)
    s += {'EARLY': 15.0, 'DEVELOPING': 14.0, 'MATURE': 12.0,
          'EXTENDED': 9.0, 'STRONG_EXTENDED': 7.0,
          'EXTREME_EXTENDED': 6.0}[lc]
    tail = _f(getattr(ev, 'tail_score', 0.0))
    s += min(15.0, tail * 0.15) if tail > 0 else 7.5
    return min(100.0, s)


# ---------- FE 装配（§十二/§十三/§十四） ----------

def _extension_penalty(ext_risk: float, continuation: float) -> float:
    """§十四：非线性惩罚，由 Continuation Score 调制——
    >=85 显著降低惩罚（强趋势大涨股不否定），<60 正常甚至加强。"""
    base = ext_risk * 0.40
    if continuation >= 85.0:
        return base * 0.55
    if continuation >= 70.0:
        return base * 0.75
    if continuation < 60.0:
        return base * 1.15
    return base


def _expansion_type(ev, lc: str, cont: float, ext: dict,
                    accel: float, sl: dict) -> str:
    """§十七 EXPANSION_TYPE 标签（DISTRIBUTION_RISK 优先判定；
    V3.0 已判 DISTRIBUTION 的股票绝不因 FE 提高重新进入买入池，§八）"""
    if getattr(ev, 'state', '') == 'DISTRIBUTION':
        return 'DISTRIBUTION_RISK'
    vol_up_price_flat = (_f(getattr(ev, 'vol_5d_ratio', 1.0), 1.0) >= 1.6
                         and _f(sl.get('s5', 0.0)) <= 0.0)
    if (cont < 45.0 and ext['score'] >= 60.0) or vol_up_price_flat:
        return 'DISTRIBUTION_RISK'
    if lc in ('STRONG_EXTENDED', 'EXTREME_EXTENDED') and cont >= 70.0:
        return 'EXTENDED_CONTINUATION'
    if accel >= 60.0 or getattr(ev, 'platform_breakout', False):
        return 'RE_ACCELERATION'
    if lc in ('MATURE', 'EXTENDED'):
        return 'MID_TREND'
    return 'NEW_TREND'


def _narratives(lc, cont, ext, space, dd, fund, ev) -> tuple:
    """§十九：生成 '为什么还有空间？' 与 '为什么可能没有空间？'"""
    why, risk = [], []
    if dd['score'] >= 60:
        why.append(f"回撤浅且修复快(60日最大回撤{dd['dd60']:.1f}%)")
    if ext['channel_pos'] <= 0.8:
        why.append('价格处于趋势通道中轨附近，未过度透支')
    if space.get('overhead', 1.0) < 0.2:
        why.append('上方套牢盘稀少，压力区遥远')
    if space.get('rm250', 0.0) <= 0.02:
        why.append('已创250日新高，上方无技术阻力')
    if fund['score'] >= 60:
        why.append(f"基本面/资金/板块承载良好(评分{fund['score']:.0f})")
    if getattr(ev, 'platform_breakout', False):
        why.append('平台突破有效，新扩张段启动')
    if getattr(ev, 'strong_locked_chip', False):
        why.append('HVT后缩量锁筹，筹码吸收良好')
    if cont >= 85.0:
        why.append(f'趋势延续能力强(Continuation={cont:.0f})')
    if ext['dev_atr20'] >= 3.0:
        risk.append(f"短期乖离过大({ext['dev_atr20']:.1f}×ATR20)")
    if ext['channel_pos'] >= 0.9:
        risk.append('贴近趋势通道上轨，追高风险大')
    if ext['rsi'] >= 80.0:
        risk.append(f"RSI偏极端({ext['rsi']:.0f})")
    if ext['consec_up'] >= 6:
        risk.append(f"连续上涨{ext['consec_up']}日，短线过热")
    if dd['score'] < 40:
        risk.append('回撤变深/修复变慢，趋势动能减弱')
    if ext['score'] >= 70:
        risk.append(f"扩张风险偏高({ext['score']:.0f}/100)")
    if lc in ('STRONG_EXTENDED', 'EXTREME_EXTENDED'):
        risk.append(f"累计涨幅巨大({lc})，安全边际低于低位启动股")
    if getattr(ev, 'state', '') == 'DISTRIBUTION':
        risk.append('V3.0已判定高位派发(DISTRIBUTION)，禁止重新入池')
    return why, risk


# ---------- 主入口（只读增强层，不改V3.0任何状态/评分，§一/§十五） ----------

def compute_future_expansion(df: pd.DataFrame, ev, rs_row: dict = None) -> dict:
    """计算单只股票的 Future Expansion 增强层。
    df: 截至当日截面的日线数据（含 open/high/low/close/vol/trade_date）
    ev: V3.0 HvtEvent（只读取，不写回）
    rs_row: 当日截面RS百分位 {'rs5','rs20','rs60','rs120'}（daily._build_rs_maps）"""
    out = {'fe_score': None, 'fe10': None, 'fe20': None, 'fe60': None,
           'fe120': None, 'lifecycle': '', 'trend_gain': None,
           'base_price': None, 'base_date': '', 'base_method': '',
           'continuation_score': None, 'extension_risk': None,
           'extension_penalty': None, 'continuation_bonus': None,
           'expansion_type': '', 'mode': '', 'fe_parts': {},
           'why_space': [], 'why_risk': []}
    if df is None or len(df) < 60 or ev is None:
        return out
    c = df['close'].astype(float).reset_index(drop=True)
    cur = _f(c.iloc[-1])
    if cur <= 0:
        return out

    # §三 Trend Lifecycle / TREND_GAIN
    base = _detect_major_base(df)
    if base is not None:
        base_price, base_date, base_method = base
    else:
        base_price, base_date, base_method = _f(df['low'].astype(float).min()), '', 'WINDOW_LOW'
    trend_gain = (cur / base_price - 1.0) if base_price > 0 else 0.0
    lc = _lifecycle(trend_gain)
    mode = 'EXTREME_TREND_MODE' if trend_gain > 5.0 else (
        'EXTENDED_TREND_MODE' if trend_gain > 2.0 else '')

    # §五 Continuation Score
    m20 = c.rolling(20, min_periods=8).mean()
    m60 = c.rolling(60, min_periods=20).mean()
    m120 = c.rolling(120, min_periods=40).mean()
    ma20, ma60, ma120 = _f(m20.iloc[-1]), _f(m60.iloc[-1]), _f(m120.iloc[-1])
    d = lambda s: _f(s.iloc[-1] / s.iloc[-6] - 1.0) if len(s) >= 6 and _f(s.iloc[-6]) > 0 else 0.0
    d20, d60, d120 = d(m20), d(m60), d(m120)
    ma_sc = _ma_component(cur, ma20, ma60, ma120, d20, d60, d120)
    rr = rs_row or {}
    rs5 = _f(rr.get('rs5', _f(getattr(ev, 'rs5', 0.0))))
    rs20 = _f(rr.get('rs20', _f(getattr(ev, 'rs20', 0.0))))
    rs60 = _f(rr.get('rs60', 0.0))
    rs120 = _f(rr.get('rs120', 0.0))
    rs_sc = _rs_component(rs5, rs20, rs60, rs120)
    sl = _close_slopes(c)
    accel_sc = _accel_component(sl)
    dd = _drawdown_component(c)
    hvt_sc = _hvt_absorb_component(ev)
    cont = _continuation_score(ma_sc, rs_sc, accel_sc, dd['score'], hvt_sc)

    # §六 Extension Risk / §九 Future Space / §十 基本面 / §十二 平台质量
    ext = _extension_risk(df, trend_gain)
    space = _future_space(df)
    fund = _fundamental_support(ev)
    plat_q = _platform_breakout_quality(ev)
    lc_sc = _LC_SCORE.get(lc, 50.0)

    # §十二 BaseExpansion（§十：TREND_GAIN>=300% 基本面权重 15%→22%、空间 25%→18%）
    if trend_gain >= 3.0:
        w = (0.30, 0.18, 0.22, 0.10, 0.10, 0.10)
    else:
        w = (0.30, 0.25, 0.15, 0.10, 0.10, 0.10)
    base_exp = (w[0] * cont + w[1] * space['score'] + w[2] * fund['score']
                + w[3] * accel_sc + w[4] * lc_sc + w[5] * plat_q)

    # §十三/§十四 FE = BaseExpansion - ExtensionPenalty + ContinuationBonus
    pen = _extension_penalty(ext['score'], cont)
    bonus = min(10.0, 0.25 * max(0.0, cont - 60.0))
    fe = min(100.0, max(0.0, base_exp - pen + bonus))

    # §十一 四周期
    fe10 = _fe10(df, ev, space, rs5, rs20)
    fe20 = _fe20(df, ev, rs20)
    fe60 = _fe60(df, fund['sector'], space, fund, rs60)
    fe120 = _fe120(ev, trend_gain, fund, fund['sector'], c)

    # §十七 标签 / §十九 叙事
    etype = _expansion_type(ev, lc, cont, ext, accel_sc, sl)
    why, risk = _narratives(lc, cont, ext, space, dd, fund, ev)

    out.update({'fe_score': round(fe, 1), 'fe10': round(fe10, 1),
                'fe20': round(fe20, 1), 'fe60': round(fe60, 1),
                'fe120': round(fe120, 1), 'lifecycle': lc,
                'trend_gain': round(trend_gain * 100.0, 1),
                'base_price': round(base_price, 2), 'base_date': str(base_date),
                'base_method': base_method,
                'continuation_score': round(cont, 1),
                'extension_risk': round(ext['score'], 1),
                'extension_penalty': round(pen, 1),
                'continuation_bonus': round(bonus, 1),
                'expansion_type': etype, 'mode': mode,
                'fe_parts': {'continuation': round(cont, 1),
                             'space': round(space['score'], 1),
                             'fundamental': round(fund['score'], 1),
                             'accel': round(accel_sc, 1), 'lifecycle': lc_sc,
                             'platform': round(plat_q, 1),
                             'base_expansion': round(base_exp, 1),
                             'ma': round(ma_sc, 1), 'rs': round(rs_sc, 1),
                             'dd20': dd['dd20'], 'dd60': dd['dd60'],
                             'overhead': space.get('overhead')},
                'why_space': why, 'why_risk': risk})
    return out