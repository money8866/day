#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""实时盘「天量T0确认」V2.1 —— Scope-Locked / Non-Interference 增量模块

模块定位
────────
本模块不是一个独立选股系统，只回答三个问题：
  Q1 今天/近期是否出现了值得关注的「天量启动结构」（T0）？
  Q2 T0 之后结构是否保持？
  Q3 现在是「可执行 / 该等待 / 已追高」？
核心原则：确认信号 ≠ 买入信号。

隔离协议（硬约束）
────────
* 只读：价格 / 成交量 / 均线 / 已有技术指标 / 已有基本面字段（由调用方通过 sig / base 传入）。
* 只写：T0_ 前缀字段。不覆盖、不重算、不改名、不改阈值、不改排序、不动任何其他模块字段。
* 只输出自己的 5 个分类：T0_BUY_CANDIDATE / T0_WAIT_PULLBACK / T0_WATCH / T0_NO_CHASE / T0_INVALID。
  不把其他模块股票强行加入/删除任何池子，不修改全局 BUY 数量、仓位与主题排名。
* 与其他模块结论冲突时（例如 HVT=BUY 而本模块=NO_CHASE），保留原结论、并行输出，交由总决策层合并。

状态机（把「天量确认」从追高信号升级为可执行交易流程）
────────
    天量 → 确认 → 不是立即 BUY → 观察结构
      ├─ 放量继续上涨 → EXPANSION → WAIT / NO_CHASE
      ├─ 缩量调整     → SHRINK_CONFIRM（关键位不破）
      ├─ 再次启动     → REBREAKOUT → T0_BUY_CANDIDATE（仍需过 ENTRY/压力位硬门槛）
      └─ 跌破关键结构 → INVALID

对外接口
────────
    compute_t0_v21(df, event_idx, sig=None, base=None, pre=False) -> dict   单票 V2.1 全字段
    format_console_block(signals, ts='', pre=False) -> str                  §15 控制台输出
    format_wechat_lines(signals, pre=False) -> list[str]                    §15 微信输出
"""
from __future__ import annotations

import math

import numpy as np

# ════════════════════════════════════════════════════════
# 阈值常量（全部属于本模块私有阈值，不与其他模块共享）
# ════════════════════════════════════════════════════════

# §3 天量识别：VOL_RATIO_T0 = T0成交量 / T0前20交易日均量
VOL_BANDS = (
    (1.5, 'NO_CONFIRM'),
    (1.8, 'WEAK_CONFIRM'),
    (2.5, 'VALID_CONFIRM'),
    (3.5, 'STRONG_CONFIRM'),
    (float('inf'), 'EXTREME_VOLUME'),
)
# 量能质量得分（20分满）：>3.5 倍后不再增加正向评分（极端放量亦可能是高位换手/派发）
VOL_BAND_SCORE = {
    'NO_CONFIRM': 0.0, 'WEAK_CONFIRM': 8.0, 'VALID_CONFIRM': 16.0,
    'STRONG_CONFIRM': 20.0, 'EXTREME_VOLUME': 20.0,
}

# §4 T0结构确认：8项中至少满足3项，否则 EARLY_CONFIRM
STRUCT_MIN_PASS = 3

# §7 追高检测：T0后涨幅 → L阶段
STAGE_BANDS = (
    (0.05, 'L0'), (0.15, 'L1'), (0.25, 'L2'), (0.40, 'L3'), (0.80, 'L4'),
    (float('inf'), 'L5'),
)
# §11 追高惩罚
STAGE_PENALTY = {'L0': 0.0, 'L1': 0.0, 'L2': -5.0, 'L3': -10.0, 'L4': -20.0, 'L5': -30.0}
EXTRA_PENALTY_CAP = 40.0

# §8 压力位分类
PRESSURE_SPACE_OK = 0.08
PRESSURE_SPACE_LIMITED = 0.03

# §6 缩量/放量分流
SHRINK_VOLR = 0.8      # 当前量比 < 0.8 → 缩量确认
EXPANSION_VOLR = 1.0   # 当前量比 >= 1.0 且继续上涨 → 放量确认

# §9 回撤判断
HEALTHY_PULLBACK_MAX = 0.05   # 健康回撤上限 5%
SHALLOW_PULLBACK = 0.01       # 1% 以内视为贴近高点、未形成回撤

# §12/§13 硬门槛
BUY_EXEC_MIN = 70.0           # T0_BUY_CANDIDATE 的 T0_EXEC_SCORE 下限
ENTRY_OK_DEV = 0.03           # ENTRY 打分阶梯：偏离 ≤3% 给高分（不用于 OK/WAIT 判定）
ENTRY_WAIT_DEV = 0.08         # 高于合理 ENTRY 8% 以上计入「明显远离 ENTRY」
ENTRY_ZONE_HALF = 0.015       # ENTRY 区间半宽 ±1.5%：现价落在区间内才算 ENTRY 可执行

# §15 输出分桶
BUCKET_ORDER = ('T0_BUY_CANDIDATE', 'T0_WAIT_PULLBACK', 'T0_WATCH', 'T0_NO_CHASE', 'T0_INVALID')
BUCKET_CN = {
    'T0_BUY_CANDIDATE': '买点候选(BUY_CANDIDATE)',
    'T0_WAIT_PULLBACK': '等待回踩(WAIT_PULLBACK)',
    'T0_WATCH': '观察(WATCH)',
    'T0_NO_CHASE': '不追高(NO_CHASE)',
    'T0_INVALID': '结构失效(INVALID)',
}
VOL_STATE_CN = {
    'SHRINK_CONFIRM': '缩量确认', 'EXPANSION_CONFIRM': '放量确认',
    'VOLUME_NEUTRAL': '量能中性', 'NO_SIGNAL': '-',
}
T0_VOL_BAND_CN = {
    'NO_CONFIRM': '未确认', 'WEAK_CONFIRM': '弱确认', 'VALID_CONFIRM': '有效确认',
    'STRONG_CONFIRM': '强确认', 'EXTREME_VOLUME': '极端放量',
}
PULLBACK_CN = {
    'HEALTHY_PULLBACK': '健康回撤', 'WEAK_PULLBACK': '弱回撤/破位',
    'DISTRIBUTION_RISK': '派发风险', 'NO_PULLBACK': '贴近高点', 'NO_SIGNAL': '-',
}


# ════════════════════════════════════════════════════════
# 基础工具
# ════════════════════════════════════════════════════════

def finite(value, default=0.0):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _clip(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, finite(v)))


def _col(df, name, idx, default=0.0):
    """安全取单元格数值（列不存在或NaN时返回默认值）"""
    try:
        if name not in df.columns:
            return default
        return finite(df[name].iloc[idx], default)
    except Exception:
        return default


def _pos_in_range(o, h, l, c):
    """收盘位于日内区间的位置(0~100)，§4「收盘位于日内强势区域」用"""
    rng = finite(h) - finite(l)
    if rng <= 0:
        return 50.0
    return _clip((finite(c) - finite(l)) / rng * 100.0)


def _upper_shadow_ratio(o, h, l, c):
    """上影占全天振幅比例，§4「突破日无明显长上影」用"""
    rng = max(finite(h) - finite(l), 0.01)
    return max(0.0, finite(h) - max(finite(o), finite(c))) / rng


def classify_vol_band(vol_ratio_t0):
    """§3 天量分档"""
    vr = finite(vol_ratio_t0)
    for edge, name in VOL_BANDS:
        if vr < edge:
            return name
    return 'EXTREME_VOLUME'


def classify_stage(ret_from_t0):
    """§7 追高检测：T0后涨幅 → L0~L5"""
    r = finite(ret_from_t0)
    for edge, name in STAGE_BANDS:
        if r < edge:
            return name
    return 'L5'


def classify_pressure_gap(gap):
    """§8 压力位分类（gap 为负表示已突破压力位）"""
    g = finite(gap, -9.9)
    if g < 0:
        return 'BREAKOUT_CONFIRM_REQUIRED'
    if g <= PRESSURE_SPACE_LIMITED:
        return 'PRESSURE_NEAR'
    if g <= PRESSURE_SPACE_OK:
        return 'SPACE_LIMITED'
    return 'SPACE_OK'


# ════════════════════════════════════════════════════════
# §4 T0 结构确认（8 项）
# ════════════════════════════════════════════════════════

def _dense_zone_top(df, ai, window=60, bins=10):
    """T0 前 window 根 K 线的成交密集区上沿（按典型价分箱累计成交量取最重箱体上边缘）"""
    lo_i = max(0, ai - window)
    if ai - lo_i < 10:
        return 0.0
    seg = df.iloc[lo_i:ai]
    try:
        high = seg['high'].to_numpy(dtype=float)
        low = seg['low'].to_numpy(dtype=float)
        close = seg['close'].to_numpy(dtype=float)
        vol = np.nan_to_num(seg['vol'].to_numpy(dtype=float), nan=0.0)
    except Exception:
        return 0.0
    if len(close) < 10 or vol.sum() <= 0:
        return 0.0
    tp = (high + low + close) / 3.0
    p_lo, p_hi = float(low.min()), float(high.max())
    if p_hi <= p_lo:
        return 0.0
    edges = np.linspace(p_lo, p_hi, bins + 1)
    idx = np.clip(np.digitize(tp, edges) - 1, 0, bins - 1)
    acc = np.zeros(bins)
    for k, v in zip(idx, vol):
        acc[int(k)] += float(v)
    return float(edges[int(np.argmax(acc)) + 1])


def structure_checks(df, ai):
    """§4 T0 结构确认：返回 (明细dict, 通过数)。

    8 项：突破20日压力 / 突破60日压力 / 突破平台上沿 / 突破近期成交密集区 /
          MA20向上 / MA20>MA60 / 收盘位于日内强势区 / 突破日无明显长上影
    """
    row = df.iloc[ai]
    c = _col(df, 'close', ai)
    checks = {}

    hi20 = finite(df['high'].iloc[max(0, ai - 20):ai].max(), 0.0)
    hi60 = finite(df['high'].iloc[max(0, ai - 60):ai].max(), 0.0)
    checks['突破20日压力'] = bool(hi20 > 0 and c > hi20)
    checks['突破60日压力'] = bool(hi60 > 0 and c > hi60)

    # 平台上沿：T0 前 20 根振幅收敛（≤35%）且 T0 收盘站上该平台上沿
    if ai >= 25:
        plat_seg = df.iloc[ai - 20:ai]
        p_hi = finite(plat_seg['high'].max(), 0.0)
        p_lo = finite(plat_seg['low'].min(), 0.0)
        amp = (p_hi - p_lo) / p_lo if p_lo > 0 else 1.0
        checks['突破平台上沿'] = bool(p_hi > 0 and amp <= 0.35 and c > p_hi)
    else:
        checks['突破平台上沿'] = False

    dz_top = _dense_zone_top(df, ai)
    checks['突破成交密集区'] = bool(dz_top > 0 and c > dz_top)

    ma20 = _col(df, 'ma_bfq_20', ai)
    ma20_prev = _col(df, 'ma_bfq_20', max(0, ai - 5)) or _col(df, 'ma_bfq_20', max(0, ai - 1))
    ma60 = _col(df, 'ma_bfq_60', ai)
    checks['MA20向上'] = bool(ma20 > 0 and ma20_prev > 0 and ma20 > ma20_prev)
    checks['MA20>MA60'] = bool(ma20 > 0 and ma60 > 0 and ma20 > ma60)
    checks['收盘强势区'] = bool(_pos_in_range(row.get('open'), row.get('high'), row.get('low'), c) >= 70)
    checks['无长上影'] = bool(_upper_shadow_ratio(row.get('open'), row.get('high'), row.get('low'), c) < 0.35)
    return checks, int(sum(1 for v in checks.values() if v))


# ════════════════════════════════════════════════════════
# §5/§6/§9 路径状态
# ════════════════════════════════════════════════════════

def _entry_zone(df, ai, j, cur, key_level, cur_ma20):
    """§13 ENTRY 生成：突破位回踩 > 平台上沿回踩 > 缩量回踩低点 > MA20附近 > 成交密集区 > 当前价

    不用当前价自动生成 ENTRY；返回 (中值, 来源, 是否退化为当前价)
    """
    cands = []
    if key_level > 0:
        cands.append(('突破位回踩', key_level))
    if ai >= 25:
        p_hi = finite(df['high'].iloc[ai - 20:ai].max(), 0.0)
        if p_hi > 0:
            cands.append(('平台上沿回踩', p_hi))
    if j - ai >= 2:
        try:
            pb_low = finite(df['low'].iloc[max(ai + 1, j - 4):j + 1].min(), 0.0)
            if pb_low > 0:
                cands.append(('缩量回踩低点', pb_low))
        except Exception:
            pass
    if cur_ma20 > 0:
        cands.append(('MA20附近', cur_ma20))
    dz_top = _dense_zone_top(df, ai)
    if dz_top > 0:
        cands.append(('成交密集区', dz_top))

    # 按 §13 优先级取第一个「现价可及」的结构位（现价高于它 → 等回踩，而不是追价）
    for lbl, val in cands:
        if 0 < val <= cur * (1 + ENTRY_ZONE_HALF):
            return val, lbl, False
    if cands:
        # 全部结构位都在现价上方（已跌破结构）→ 取最近的一个作为回踩/收复参考位
        lbl, val = min(cands, key=lambda kv: kv[1] - cur)
        if val <= cur * 1.06:
            return val, lbl, False
    return cur, '当前价(无结构位可用)', True


def _volume_and_pullback_state(cur, prev_close, cur_volr, key_level, hard_level,
                               post_high, cur_ma20):
    """§6 量能分流 + §9 回撤判断"""
    pullback = max(0.0, (post_high - cur) / post_high) if post_high > 0 else 0.0
    down_today = cur < prev_close
    # 破位：软口径=跌破 T0 突破位3%以上；硬口径=跌破 T0 日最低
    broken_soft = key_level > 0 and cur < key_level * 0.97
    broken_hard = hard_level > 0 and cur < hard_level
    # §9 DISTRIBUTION_RISK：下跌 + 放量 + 跌破结构
    if down_today and cur_volr >= 1.2 and (broken_soft or broken_hard):
        pb_state = 'DISTRIBUTION_RISK'
    elif pullback > HEALTHY_PULLBACK_MAX or broken_soft:
        pb_state = 'WEAK_PULLBACK'
    elif pullback >= SHALLOW_PULLBACK and cur_volr < EXPANSION_VOLR and not broken_soft:
        pb_state = 'HEALTHY_PULLBACK'
    else:
        pb_state = 'NO_PULLBACK'

    # §6.1 缩量确认：量比<0.8 + 回撤≤5% + 未破 T0 关键结构
    if cur_volr < SHRINK_VOLR and pullback <= HEALTHY_PULLBACK_MAX and not broken_soft:
        vol_state = 'SHRINK_CONFIRM'
    # §6.2 放量确认：量比>=1.0 且价格继续上涨
    elif cur_volr >= EXPANSION_VOLR and not down_today:
        vol_state = 'EXPANSION_CONFIRM'
    else:
        vol_state = 'VOLUME_NEUTRAL'
    return vol_state, pb_state, pullback, broken_soft, broken_hard


# ════════════════════════════════════════════════════════
# §10/§11 双评分
# ════════════════════════════════════════════════════════

def _confirm_score(checks, n_pass, vol_band, base, sig, df, ai, j):
    """T0_CONFIRM_SCORE：这个信号本身是否成立（结构25/量能20/承接20/趋势15/突破10/相对强度10）"""
    s_struct = 25.0 * n_pass / 8.0
    s_vol = VOL_BAND_SCORE.get(vol_band, 0.0)

    # 资金承接（只读：优先用已算好的 base/sig 字段，缺失时退回K线自算）
    def _pick(key):
        v = (base or {}).get(key)
        if v is None:
            v = (sig or {}).get(key)
        return finite(v, None) if v is not None else None

    acc, sds, cq = _pick('acceptance'), _pick('sds'), _pick('cq')
    if acc is not None and sds is not None and cq is not None:
        s_cap = 20.0 * (0.5 * acc + 0.3 * sds + 0.2 * cq) / 100.0
    else:
        # 退路：T0 后承接 = 1 - 破位幅度（不依赖其他模块字段）
        hard_low = _col(df, 'low', ai)
        post_low = finite(df['low'].iloc[ai + 1:j + 1].min(), hard_low) if j > ai else hard_low
        breach = max(0.0, (hard_low - post_low) / hard_low) if hard_low > 0 else 0.0
        s_cap = 20.0 * _clip(100.0 - breach * 400.0) / 100.0

    ma20 = _col(df, 'ma_bfq_20', j)
    ma60 = _col(df, 'ma_bfq_60', j)
    cur = _col(df, 'close', j)
    s_trend = 5.0 * (1 if checks.get('MA20向上') else 0) \
        + 5.0 * (1 if checks.get('MA20>MA60') else 0) \
        + 5.0 * (1 if (ma20 > 0 and cur > ma20) else 0)

    s_break = 4.0 * (1 if checks.get('突破20日压力') else 0) \
        + 3.0 * (1 if checks.get('突破60日压力') else 0) \
        + 3.0 * (1 if checks.get('突破平台上沿') else 0)

    # 相对强度（只读已有指标；缺失时退回 T0 后超额涨幅近似）
    rs = None
    dims = (sig or {}).get('dims') or {}
    if isinstance(dims, dict) and dims.get('rs') is not None:
        rs = finite(dims['rs'], None)
    if rs is None:
        rs = finite((base or {}).get('rs'), None)
    if rs is None:
        r_t0 = (cur / _col(df, 'close', ai) - 1.0) * 100.0 if _col(df, 'close', ai) > 0 else 0.0
        rs = _clip(50.0 + r_t0 * 2.0)
    s_rs = 10.0 * _clip(rs) / 100.0
    return _clip(s_struct + s_vol + s_cap + s_trend + s_break + s_rs, 0.0, 100.0)


def _exec_score(n_pass, pb_state, vol_state, dev_entry, pressure_state,
                checks, extra_penalties):
    """T0_EXEC_SCORE：现在这个位置是否值得执行（结构20/回撤20/量能15/ENTRY15/压力10/趋势10 − 追高惩罚）"""
    # 结构稳定：站上 MA20 且结构项通过多
    s_struct = _clip(20.0 * n_pass / 8.0, 0.0, 20.0)
    s_pull = {'HEALTHY_PULLBACK': 20.0, 'NO_PULLBACK': 16.0, 'WEAK_PULLBACK': 6.0,
              'DISTRIBUTION_RISK': 0.0}.get(pb_state, 8.0)
    s_vol = {'SHRINK_CONFIRM': 15.0, 'VOLUME_NEUTRAL': 10.0, 'EXPANSION_CONFIRM': 5.0}.get(vol_state, 5.0)
    dev = max(0.0, finite(dev_entry))
    if dev <= ENTRY_ZONE_HALF:
        s_entry = 15.0
    elif dev <= ENTRY_OK_DEV:
        s_entry = 12.0
    elif dev <= 0.05:
        s_entry = 8.0
    elif dev <= ENTRY_WAIT_DEV:
        s_entry = 4.0
    else:
        s_entry = 0.0
    s_pressure = {'SPACE_OK': 10.0, 'SPACE_LIMITED': 6.0,
                  'PRESSURE_NEAR': 2.0, 'BREAKOUT_CONFIRM_REQUIRED': 5.0}.get(pressure_state, 5.0)
    s_trend = 5.0 * (1 if checks.get('MA20向上') else 0) + 5.0 * (1 if checks.get('MA20>MA60') else 0)
    return _clip(s_struct + s_pull + s_vol + s_entry + s_pressure + s_trend
                 - max(0.0, finite(extra_penalties)), 0.0, 100.0)


# ════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════

def compute_t0_v21(df, event_idx, sig=None, base=None, pre=False):
    """计算单票「天量T0确认」V2.1 全字段。

    参数
    ────
    df        : 含 open/high/low/close/vol/pct_chg/(ma_bfq_20/ma_bfq_60) 的 DataFrame，
                最后一行为「当前/最新」交易日（盘中即实时价近似的虚拟K线）
    event_idx : T0 锚（天量启动日）在 df 中的行号（由已有 W7 锚点逻辑给出，只读）
    sig       : 已有 W7 analyze() 结果（只读，用于 pressure/volr/acceptance/dims 等）
    base      : 已有 behavior_features 结果（只读，可选）
    pre       : 是否盘中预检（盘中累计量口径偏低，仅影响展示，不改变硬门槛）

    返回 dict（全部 T0_ 前缀字段；不覆盖任何非 T0_ 字段）
    """
    sig = sig or {}
    base = base or {}
    n = len(df)
    ai = int(event_idx)
    j = n - 1
    out = {'T0_V21': True}
    if ai < 0 or j <= ai or ai >= n:
        out.update({'T0_STATE': 'INVALID', 'T0_FINAL': 'T0_INVALID', 'T0_REASON': 'T0锚无效'})
        return out

    t0_row = df.iloc[ai]
    cur_row = df.iloc[j]
    t0_close = finite(t0_row.get('close'))
    cur = finite(cur_row.get('close'))
    if t0_close <= 0 or cur <= 0:
        out.update({'T0_STATE': 'INVALID', 'T0_FINAL': 'T0_INVALID', 'T0_REASON': '价格缺失'})
        return out

    # ── §3 天量识别 ──
    vol_ai = _col(df, 'vol', ai)
    vol20_t0 = float(np.nan_to_num(df['vol'].iloc[max(0, ai - 20):ai].to_numpy(dtype=float), nan=0.0).mean()) \
        if ai >= 5 else 0.0
    vol_ratio_t0 = vol_ai / vol20_t0 if vol20_t0 > 0 else 0.0
    vol_band = classify_vol_band(vol_ratio_t0)

    # ── §4 结构确认 ──
    checks, n_pass = structure_checks(df, ai)

    # ── 当前量比（优先只读 sig 的现成口径，缺失时自算）──
    cur_volr = finite(sig.get('volr'), None)
    if cur_volr is None:
        vol20_now = float(np.nan_to_num(df['vol'].iloc[max(0, j - 19):j].to_numpy(dtype=float), nan=0.0).mean()) \
            if j >= 5 else 0.0
        cur_volr = _col(df, 'vol', j) / vol20_now if vol20_now > 0 else 0.0
    cur_volr = finite(cur_volr)

    # ── §7 追高阶段（T0后涨幅，仅影响本模块 EXEC 判断）──
    ret_t0 = cur / t0_close - 1.0
    stage = classify_stage(ret_t0)

    # ── §8 压力位 ──
    pressure = finite(sig.get('pressure'), None)
    if pressure is None or pressure <= 0:
        pressure = finite(df['high'].iloc[max(ai + 1, j - 9):j].max(), 0.0) if j > ai + 1 else 0.0
        if pressure <= 0:
            pressure = finite(df['high'].iloc[max(0, j - 19):j].max(), 0.0)
    gap = (pressure - cur) / cur if (pressure > 0 and cur > 0) else 0.0
    pressure_state = classify_pressure_gap(gap) if pressure > 0 else 'SPACE_OK'

    # ── 路径特征 ──
    post_high = finite(df['high'].iloc[ai + 1:j + 1].max(), t0_close) if j > ai else t0_close
    prev_close = _col(df, 'close', max(0, j - 1), cur)
    cur_ma20 = _col(df, 'ma_bfq_20', j)
    hard_level = _col(df, 'low', ai)

    vol_state, pb_state, pullback, broken_soft, broken_hard = _volume_and_pullback_state(
        cur, prev_close, cur_volr, t0_close, hard_level, post_high, cur_ma20)

    # ── §13 ENTRY ──
    entry_mid, entry_src, entry_degraded = _entry_zone(df, ai, j, cur, t0_close, cur_ma20)
    dev_entry = (cur - entry_mid) / entry_mid if entry_mid > 0 else 0.0
    # 现价落在合理 ENTRY 区间内才算「可执行」；高于区间一律 WAIT（不追价）
    entry_state = 'OK' if dev_entry <= ENTRY_ZONE_HALF else 'WAIT'

    # ── 追高因子 ──
    vol20_now = float(np.nan_to_num(df['vol'].iloc[max(0, j - 19):j].to_numpy(dtype=float), nan=0.0).mean()) \
        if j >= 5 else 0.0
    pct_today = _col(df, 'pct_chg', j)
    pct_yday = _col(df, 'pct_chg', max(0, j - 1))
    vol_yday = _col(df, 'vol', max(0, j - 1))
    big_up_today = pct_today >= 7.0
    two_day_expansion = bool(pct_today > 0 and pct_yday > 0 and vol20_now > 0
                             and vol_yday >= vol20_now and _col(df, 'vol', j) >= vol20_now)
    far_from_entry = dev_entry > ENTRY_WAIT_DEV
    heavy_vol_far = cur_volr > 2.5 and dev_entry > 0.05
    pressure_near = (pressure_state == 'PRESSURE_NEAR')
    new_high_after_shrink = False
    if j - ai >= 5:
        try:
            prior_high = finite(df['high'].iloc[ai + 1:j].max(), 0.0)
            vol_recent = float(np.nan_to_num(df['vol'].iloc[max(ai + 1, j - 5):j].to_numpy(dtype=float), nan=0.0).mean())
            new_high_after_shrink = bool(prior_high > 0 and cur >= prior_high
                                         and vol20_now > 0 and vol_recent < vol20_now * 0.8)
        except Exception:
            new_high_after_shrink = False

    # ── §10/§11 双评分 ──
    confirm_score = _confirm_score(checks, n_pass, vol_band, base, sig, df, ai, j)
    extra_pen = 0.0
    if pressure_near:
        extra_pen += 10.0
    if two_day_expansion:
        extra_pen += 10.0
    if big_up_today:
        extra_pen += 10.0
    if heavy_vol_far:
        extra_pen += 10.0
    extra_pen = min(extra_pen, EXTRA_PENALTY_CAP)
    stage_pen = STAGE_PENALTY.get(stage, 0.0)
    exec_score = _exec_score(n_pass, pb_state, vol_state, dev_entry, pressure_state, checks, extra_pen)
    exec_score_pure = _clip(exec_score - stage_pen, 0.0, 100.0)

    # ── §5 状态机 ──
    structure_ok = n_pass >= STRUCT_MIN_PASS
    structure_fail = broken_hard or (broken_soft and cur_ma20 > 0 and cur < cur_ma20)
    invalid = bool(pb_state == 'DISTRIBUTION_RISK' or structure_fail)
    overextension = bool(vol_state == 'EXPANSION_CONFIRM' and stage in ('L2', 'L3', 'L4', 'L5')) \
        or bool(big_up_today and cur_volr >= 2.0)

    if invalid:
        t0_state = 'INVALID'
    elif not structure_ok:
        t0_state = 'EARLY_CONFIRM'
    elif overextension:
        t0_state = 'OVEREXTENSION'
    elif two_day_expansion:
        t0_state = 'EXPANSION'
    elif new_high_after_shrink:
        t0_state = 'REBREAKOUT'
    elif vol_state == 'SHRINK_CONFIRM':
        t0_state = 'SHRINK_CONFIRM'
    elif pb_state == 'HEALTHY_PULLBACK' or broken_soft or cur < t0_close:
        t0_state = 'PULLBACK'
    elif j - ai <= 5:
        t0_state = 'CONFIRMED'
    else:
        t0_state = 'STRUCTURE_HOLD'

    # ── §12 最终分类（硬门槛优先；NO_CHASE 不是看空，只是不建议追价）──
    # 注：§12「压力位附近」单独触发 NO_CHASE 过于宽泛，按 §8「禁止仅凭高CONFIRM_SCORE触发BUY」+
    #     §20「缩量确认才有资格进入买点候选」的联合口径处理：压力位附近非缩量确认 → NO_CHASE，
    #     缩量确认+健康回撤 → 降级为 WAIT_PULLBACK（由 buy_gates 的 PRESSURE_NEAR 硬门槛拦住）。
    no_chase_flag = bool(
        stage in ('L4', 'L5')
        or overextension
        or two_day_expansion
        or far_from_entry
        or (big_up_today and cur_volr >= 2.0)
        or (pressure_near and vol_state != 'SHRINK_CONFIRM')
    )
    buy_gates = (structure_ok and not invalid and not overextension
                 and exec_score_pure >= BUY_EXEC_MIN
                 and stage in ('L0', 'L1')
                 and vol_state == 'SHRINK_CONFIRM'
                 and entry_state == 'OK'
                 and pb_state != 'WEAK_PULLBACK'
                 and pressure_state != 'PRESSURE_NEAR')
    if pre:
        # 盘中预检的成交量是不完整累计口径,「缩量」判定会系统性偏低(几乎必现),
        # 故盘中不给买点候选(与既有模块「盘中不分档、以 14:50 定稿为准」口径一致)。
        buy_gates = False
    wait_gates = (structure_ok and not invalid and not no_chase_flag
                  and ((vol_state == 'EXPANSION_CONFIRM')
                       or (t0_state in ('CONFIRMED', 'STRUCTURE_HOLD', 'SHRINK_CONFIRM',
                                        'PULLBACK', 'REBREAKOUT')
                           and (entry_state == 'WAIT' or pb_state == 'HEALTHY_PULLBACK'))))
    if invalid:
        final = 'T0_INVALID'
    elif no_chase_flag:
        final = 'T0_NO_CHASE'
    elif buy_gates:
        final = 'T0_BUY_CANDIDATE'
    elif wait_gates:
        final = 'T0_WAIT_PULLBACK'
    else:
        final = 'T0_WATCH'

    # ── 核心理由 ──
    ck_names = '、'.join(k for k, v in checks.items() if v)
    reason = (('【盘中预检·量能口径不完整】' if pre else '') +
              f"结构{n_pass}/8({ck_names or '无'}) ｜ 量能{T0_VOL_BAND_CN.get(vol_band, vol_band)}×{vol_ratio_t0:.2f} "
              f"｜ 当前{VOL_STATE_CN.get(vol_state, vol_state)}(量比{cur_volr:.2f}) "
              f"｜ {PULLBACK_CN.get(pb_state, pb_state)}回撤{pullback * 100:.1f}% "
              f"｜ {stage}(T0后{ret_t0 * 100:+.1f}%) ｜ 压力空间{gap * 100:+.1f}%({pressure_state}) "
              f"｜ ENTRY{entry_mid:.2f}({entry_src})偏离{dev_entry * 100:+.1f}%")

    out.update({
        'T0_DATE': str(t0_row.get('trade_date', '')),
        'T0_PRICE': round(t0_close, 2),
        'T0_VOL_RATIO': round(vol_ratio_t0, 2),
        'T0_VOL_BAND': vol_band,
        'T0_STRUCT_PASS': n_pass,
        'T0_STRUCT_CHECKS': checks,
        'T0_STATE': t0_state,
        'T0_VOLUME_STATE': vol_state,
        'T0_PULLBACK_STATE': pb_state,
        'T0_PULLBACK': round(pullback * 100, 2),
        'T0_STAGE': stage,
        'T0_RET_FROM_T0': round(ret_t0 * 100, 2),
        'T0_PRESSURE': round(pressure, 2),
        'T0_PRESSURE_GAP': round(gap * 100, 2),
        'T0_PRESSURE_STATE': pressure_state,
        'T0_OVEREXTENSION': overextension,
        'T0_ENTRY_ZONE': f"{entry_mid * (1 - ENTRY_ZONE_HALF):.2f}~{entry_mid * (1 + ENTRY_ZONE_HALF):.2f}",
        'T0_ENTRY_MID': round(entry_mid, 2),
        'T0_ENTRY_SRC': entry_src,
        'T0_ENTRY_DEV': round(dev_entry * 100, 2),
        'T0_ENTRY_STATE': entry_state,
        'T0_NO_CHASE': bool(final == 'T0_NO_CHASE'),
        'T0_CONFIRM_SCORE': round(confirm_score, 1),
        'T0_EXEC_SCORE': round(exec_score_pure, 1),
        'T0_EXEC_PENALTY': round(stage_pen - extra_pen, 1),
        'T0_CUR_VOLR': round(cur_volr, 2),
        'T0_FINAL': final,
        'T0_REASON': reason,
        'T0_PRE': bool(pre),
    })
    return out


def bucket_of(item):
    """取 V2.1 分类（兼容 dict 与 sig 包装）"""
    return (item or {}).get('T0_FINAL') or 'T0_WATCH'


def group_buckets(signals):
    """按 §15 的 5 个分类归组（空组保留，便于输出「无」）"""
    groups = {k: [] for k in BUCKET_ORDER}
    for s in signals or []:
        groups.setdefault(bucket_of(s), []).append(s)
    for k in groups:
        groups[k].sort(key=lambda s: (-finite(s.get('T0_EXEC_SCORE')), -finite(s.get('T0_CONFIRM_SCORE'))))
    return groups


def _one_line(s, idx=None):
    tag = f"{idx}. " if idx else ""
    return (f"{tag}{s.get('name', '')}({s.get('code', '')}) [{s.get('theme', '')}] "
            f"T0={s.get('T0_DATE', s.get('event_date', ''))} 价{s.get('T0_PRICE', 0):.2f}→{s.get('close', 0):.2f} "
            f"T0量比×{s.get('T0_VOL_RATIO', 0):.1f}({s.get('T0_VOL_BAND', '')}) 现量比×{s.get('T0_CUR_VOLR', 0):.2f} "
            f"回撤{s.get('T0_PULLBACK', 0):.1f}% {s.get('T0_STAGE', '')} 压力{s.get('T0_PRESSURE', 0):.2f}"
            f"({s.get('T0_PRESSURE_GAP', 0):+.1f}%) ENTRY[{s.get('T0_ENTRY_ZONE', '')}]"
            f"{s.get('T0_ENTRY_SRC', '')} 确认{s.get('T0_CONFIRM_SCORE', 0):.0f}/执行{s.get('T0_EXEC_SCORE', 0):.0f}")


def format_console_block(signals, ts='', pre=False):
    """§15 控制台输出（只输出本模块结果；无股票的分类打印「无」）"""
    groups = group_buckets(signals)
    mode = '盘中预检' if pre else '定稿'
    lines = []
    lines.append('═' * 108)
    lines.append(f"【天量T0实时确认 V2.1】{mode} {ts}  候选{len(signals or [])}只"
                 f"  (只读其他模块数据,只写 T0_* 字段)")
    lines.append('─' * 108)
    for bucket in BUCKET_ORDER:
        items = groups.get(bucket, [])
        lines.append(f"◆ {BUCKET_CN.get(bucket, bucket)}  {len(items) if items else '无'}")
        if not items:
            continue
        for i, s in enumerate(items, 1):
            lines.append('  ' + _one_line(s, i))
            lines.append(f"     状态{s.get('T0_STATE', '')}/{VOL_STATE_CN.get(s.get('T0_VOLUME_STATE', ''), '')}"
                         f"/{PULLBACK_CN.get(s.get('T0_PULLBACK_STATE', ''), '')}  ENTRY:{s.get('T0_ENTRY_STATE', '')}")
            lines.append(f"     核心理由: {s.get('T0_REASON', '')}")
    lines.append('═' * 108)
    return '\n'.join(lines)


def format_wechat_lines(signals, pre=False, max_per_bucket=3):
    """§15 微信输出（同样只输出本模块结果）"""
    groups = group_buckets(signals)
    mode = '盘中预检' if pre else '定稿'
    lines = [f"【天量T0实时确认 V2.1】{mode} 候选{len(signals or [])}只"]
    for bucket in BUCKET_ORDER:
        items = groups.get(bucket, [])
        lines.append(f"— {BUCKET_CN.get(bucket, bucket)}: {len(items) if items else '无'}")
        for i, s in enumerate(items[:max_per_bucket], 1):
            lines.append(_one_line(s, i))
            lines.append(f"   {s.get('T0_STATE', '')} 理由:{s.get('T0_REASON', '')}")
    lines.append("规则: 放量确认→只进观察/等待回踩; 缩量确认+关键位不破+ENTRY可执行 才有资格进买点候选; 巨量日不追。")
    return lines
