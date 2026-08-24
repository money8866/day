# -*- coding: utf-8 -*-
"""
PBP 核心引擎：状态机驱动的五阶段买点识别
================================================
完整交易路径（第一节）：
  平台形成 -> 平台有效性确认 -> 有效突破 -> 突破确认
  -> 第一次健康回踩 -> 关键位承接 -> 回踩结束 -> 重新转强 -> ★ PRIMARY BUY

铁律：
  1. 没有完整的平台识别，不允许定义突破
  2. 没有突破有效性验证，不允许定义回踩
  3. 没有第一次健康回踩确认，不允许给出最优买点
  4. 没有重新转强，不允许 BUY

防未来数据：只接收已截断到 T 日的 DataFrame，全部计算只用 <=T 的数据。
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .config import (
    PBP_CONFIG, STATE_PLATFORM_BUILDING, STATE_PLATFORM_CONFIRMED, STATE_NEAR_BREAKOUT,
    STATE_BREAKOUT_CONFIRMED, STATE_FIRST_PULLBACK, STATE_PULLBACK_SUPPORT,
    STATE_RE_ACCELERATION, STATE_PRIMARY_BUY, STATE_HOLD, STATE_INVALIDATED,
    STATE_BREAKOUT_FAILED, STATE_PULLBACK_FAILED,
    ACTION_WAIT_PLATFORM, ACTION_WAIT_BREAKOUT, ACTION_WAIT_PULLBACK,
    ACTION_WAIT_REACCELERATION, ACTION_EARLY_BUY, ACTION_PRIMARY_BUY,
    ACTION_CONFIRMED_BUY, ACTION_NO_TRADE, ACTION_BREAKOUT_FAILED, ACTION_PULLBACK_FAILED,
)
from .indicators import enrich, close_location, upper_shadow_ratio


@dataclass
class PBPResult:
    ts_code: str = ''
    name: str = ''
    industry: str = ''
    date: str = ''

    # ── 状态机 ──
    state: str = STATE_PLATFORM_BUILDING
    action: str = ACTION_NO_TRADE

    # ── 平台 ──
    platform_found: bool = False
    platform_start: str = ''
    platform_end: str = ''
    platform_high: float = 0.0
    platform_low: float = 0.0
    platform_range: float = 0.0
    platform_days: int = 0
    resistance_tests: int = 0
    support_tests: int = 0
    platform_score: float = 0.0
    platform_grade: str = ''
    platform_vol_shrink: float = 0.0     # 后半段量/前半段量
    platform_atr_compress: float = 0.0   # 后半段TR/前半段TR
    platform_volatility_converge: bool = False

    # ── 突破 ──
    breakout_found: bool = False
    breakout_date: str = ''
    breakout_level: float = 0.0
    breakout_price: float = 0.0
    breakout_pct: float = 0.0
    breakout_vol_ratio: float = 0.0
    breakout_close_loc: float = 0.0
    breakout_high: float = 0.0
    breakout_score: float = 0.0
    breakout_grade: str = ''
    breakout_days_ago: int = -1
    breakout_failed: bool = False

    # ── 回踩 ──
    pullback_started: bool = False
    pullback_start: str = ''
    pullback_low: float = 0.0
    pullback_low_date: str = ''
    pullback_depth: float = 0.0          # 0~1+（BreakoutHigh -> PullbackLow 相对突破幅度）
    pullback_days: int = 0
    pullback_vol_ratio: float = 0.0      # 回踩段量 / 突破日量
    pullback_broke_level: bool = False   # 是否有效跌破突破位
    pullback_score: float = 0.0
    pullback_first: bool = False         # 是否第一次回踩
    pullback_end_evidence: int = 0       # 回踩结束证据数
    pullback_end_evidences: List[str] = field(default_factory=list)
    pullback_failed: bool = False
    pullback_tried_turn: bool = False    # 回踩段内是否已出现过站上MA5后又回落（EARLY_BUY 一次性约束）

    # ── 重新转强 ──
    reacc_found: bool = False
    reacc_date: str = ''
    reacc_price: float = 0.0
    reacc_vol_ratio: float = 0.0
    reacc_close_loc: float = 0.0
    reacc_ma5_ma10: bool = False

    # ── 二次突破（CONFIRMED BUY）──
    confirmed_break: bool = False

    # ── 最终评分 ──
    final_score: float = 0.0
    stars: int = 0
    grade: str = ''
    score_platform: float = 0.0
    score_breakout: float = 0.0
    score_pullback: float = 0.0
    score_reacc: float = 0.0

    # ── 市场环境 ──
    market_regime: str = 'neutral'
    theme_score: float = 0.0
    theme_detail: dict = field(default_factory=dict)

    # ── 基础行情 ──
    close: float = 0.0
    ma5: float = 0.0
    ma10: float = 0.0
    ma20: float = 0.0
    ma60: float = 0.0
    atr20: float = 0.0
    dist_breakout_level: float = 0.0     # close/level - 1
    prev_high: float = 0.0

    # ── 诊断 ──
    reasons: List[str] = field(default_factory=list)
    risk_factors: List[str] = field(default_factory=list)
    checks: Dict[str, dict] = field(default_factory=dict)
    n_bars: int = 0

    # ── 回测标注（由回测层填充，不进入评分）──
    fut3: float = float('nan')
    fut5: float = float('nan')
    fut10: float = float('nan')
    fut20: float = float('nan')


def _safe(fn, default=0.0):
    try:
        v = fn()
        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            return default
        return float(v)
    except Exception:
        return default


_MISSING = object()  # 缓存哨兵：区分「未计算」与「结果为 None」


class PBPEngine:
    def __init__(self, config: Optional[dict] = None):
        self.cfg = dict(PBP_CONFIG)
        if config:
            self.cfg.update(config)
        # 平台缓存（方案1：消除回测逐日全量重算）
        # 键 = 平台截止日 end_idx；值 = _find_platform 原始结果（纯标量，不持有 df 引用）
        # 有效性由 score(cache_key=...) 显式控制：同一股票序列内复用，数据源变化即清空
        self._platform_cache: Dict[int, Optional[dict]] = {}
        self._cache_key = None

    # ═══════════════════════════════════════════
    # 阶段一：平台识别（第二节）
    # ═══════════════════════════════════════════
    def _find_platform(self, df: pd.DataFrame, end_idx: int) -> Optional[dict]:
        """在 end_idx（突破日或当前日）之前识别平台。

        扫描 10~60 日窗口，逐窗检查时间/幅度/收敛/测试次数，
        多窗口取 PLATFORM_SCORE 最高者。
        """
        cfg = self.cfg
        highs = df['high'].values.astype(float)
        lows = df['low'].values.astype(float)
        closes = df['close'].values.astype(float)
        vols = df['vol'].values.astype(float)
        atr = df['atr20'].values
        best = None
        for w in range(cfg['platform_days_min'], cfg['platform_days_max'] + 1, 5):
            s = end_idx - w
            if s < 20:  # 保证 MA20/ATR20 已有效
                continue
            e = end_idx
            if e - s < cfg['platform_days_min']:
                continue
            seg_h = highs[s:e]
            seg_l = lows[s:e]
            seg_c = closes[s:e]
            seg_v = vols[s:e]
            if len(seg_h) < cfg['platform_days_min']:
                continue
            hi = float(seg_h.max())
            lo = float(seg_l.min())
            if lo <= 0 or hi <= 0:
                continue
            rng = (hi - lo) / lo
            if rng > cfg['platform_range_wide']:  # >30% 不认定
                continue
            atr_e = _safe(lambda: float(atr[e - 1]), 0.0)
            if atr_e <= 0:
                continue

            stats = self._platform_stats(df, s, e, hi, lo, atr_e)
            score = self._platform_score(w, rng, stats, df, s, e)
            cand = {'start': s, 'end': e, 'days': e - s, 'high': hi, 'low': lo,
                    'range': rng, 'atr': atr_e, 'score': score, **stats}
            if best is None or score > best['score']:
                best = cand
        if best is None:
            return None
        return best

    def _platform_for(self, df: pd.DataFrame, end_idx: int) -> Optional[dict]:
        """带缓存的平台识别：回测逐日滑动窗口时，同一 end_idx 只计算一次。

        结果只依赖 df 在 [0, end_idx] 的数据；跨日复用（连续 15 日窗口
        命中率约 93%）由 score(cache_key=...) 保证数据源一致。
        """
        hit = self._platform_cache.get(end_idx, _MISSING)
        if hit is not _MISSING:
            return hit
        p = self._find_platform(df, end_idx)
        self._platform_cache[end_idx] = p
        return p

    def _platform_stats(self, df: pd.DataFrame, s: int, e: int, hi: float, lo: float, atr: float) -> dict:
        """平台内部统计：波动收敛/阻力测试/支撑测试/量能收缩/均线结构（numpy 向量化）"""
        cfg = self.cfg
        highs = df['high'].values.astype(float)
        lows = df['low'].values.astype(float)
        closes = df['close'].values.astype(float)
        vols = df['vol'].values.astype(float)
        opens = df['open'].values.astype(float)
        n = e - s
        half = n // 2

        # ── 波动收敛：后半段 vs 前半段（TR/日振幅/收盘std）──
        # 向量化 TR：前收盘 = closes[i-1]（s>=20 保证 i>0），三元素取最大
        pc = closes[s - 1:e - 1] if s > 0 else closes[s:e]
        seg_h, seg_l, seg_c = highs[s:e], lows[s:e], closes[s:e]
        trs = np.maximum(seg_h - seg_l,
                         np.maximum(np.abs(seg_h - pc), np.abs(seg_l - pc)))
        tr_1h = float(trs[:half].mean()) if half > 0 else 0.0
        tr_2h = float(trs[half:].mean()) if half > 0 else 0.0
        atr_compress = tr_2h / tr_1h if tr_1h > 0 else 1.0
        amp = (seg_h - seg_l) / seg_c
        amp_1h = float(amp[:half].mean()) if half > 0 else 0.0
        amp_2h = float(amp[half:].mean()) if half > 0 else 0.0
        amp_shrink = amp_2h / amp_1h if amp_1h > 0 else 1.0
        std_1h = float(np.std(seg_c[:half])) if half > 0 else 0.0
        std_2h = float(np.std(seg_c[half:])) if half > 0 else 0.0
        std_shrink = std_2h / std_1h if std_1h > 0 else 1.0
        converge = bool(atr_compress < 0.95 or amp_shrink < 0.95 or std_shrink < 0.95)

        # ── 阻力/支撑测试：分波触及上下沿次数（每5日一段，段内触及上/下沿区计1次）──
        # 平台顶部不是单一孤立高点：上沿区 = [hi - tol, hi]，tol 取 1.5*ATR 与 25%平台宽度较小者
        tol = min(1.5 * atr, 0.25 * (hi - lo)) if (hi - lo) > 0 else 0.0
        seg_idx = np.arange(0, n, 5)  # 段起点（0,5,10,...），reduceat 自动处理末段不足5日
        res_tests = int(np.sum(np.maximum.reduceat(seg_h, seg_idx) >= hi - tol))
        sup_tests = int(np.sum(np.minimum.reduceat(seg_l, seg_idx) <= lo + tol))

        # ── 量能收缩：后半段/前半段 ──
        v_1h = float(vols[s:s + half].mean()) if half > 0 else 0.0
        v_2h = float(vols[s + half:e].mean()) if half > 0 else 0.0
        vol_shrink = v_2h / v_1h if v_1h > 0 else 1.0

        # ── 持续放量大阴线检测（假平台特征）──
        vm = df['vol_ma20'].values[s:e]
        seg_v, seg_o = vols[s:e], opens[s:e]
        big_dn_days = int(np.sum((~np.isnan(vm)) & (vm > 0)
                                 & (seg_v > vm * 1.5) & (seg_c < seg_o)))

        # ── 均线结构（平台末端）──
        ma20_slope = _safe(lambda: closes[e - 1] / df['ma20'].values[e - 1] - 1.0)
        ma20_trend = _safe(lambda: df['ma20'].values[e - 1] / df['ma20'].values[e - 6] - 1.0) if e - 6 >= 0 else 0.0
        ma60_trend = _safe(lambda: df['ma60'].values[e - 1] / df['ma60'].values[e - 6] - 1.0) if e - 6 >= 0 else 0.0

        # ── 涨跌平衡（阴阳天数）──
        up_days = int(np.sum(closes[s:e] > opens[s:e]))
        dn_days = int(np.sum(closes[s:e] < opens[s:e]))
        balance = min(up_days, dn_days) / max(up_days, dn_days) if max(up_days, dn_days) > 0 else 1.0

        # ── 平台末段靠近MA20程度 ──
        near_ma20 = _safe(lambda: abs(closes[e - 1] / df['ma20'].values[e - 1] - 1.0))

        return {
            'res_tests': res_tests, 'sup_tests': sup_tests,
            'vol_shrink': vol_shrink, 'atr_compress': atr_compress,
            'amp_shrink': amp_shrink, 'std_shrink': std_shrink, 'converge': converge,
            'big_dn_days': big_dn_days, 'ma20_slope': ma20_slope, 'ma20_trend': ma20_trend,
            'ma60_trend': ma60_trend, 'balance': balance, 'near_ma20': near_ma20,
            'up_days': up_days, 'dn_days': dn_days,
        }

    def _platform_score(self, days: int, rng: float, st: dict, df: pd.DataFrame, s: int, e: int) -> float:
        """PLATFORM_SCORE 100分制（第三节）"""
        cfg = self.cfg
        score = 0.0
        # 平台时间 10
        best_lo, best_hi = cfg['platform_days_best']
        if best_lo <= days <= best_hi:
            score += 10.0
        elif days >= cfg['platform_days_min']:
            score += 7.0 if days <= cfg['platform_days_max'] else 4.0
        # 区间收敛 15
        if rng <= cfg['platform_range_good']:
            score += 15.0
        elif rng <= cfg['platform_range_ok']:
            score += 12.0
        elif rng <= cfg['platform_range_wide']:
            score += 8.0
        # 高点重复测试 15
        if st['res_tests'] >= cfg['resistance_test_good']:
            score += 15.0
        elif st['res_tests'] >= cfg['resistance_test_min']:
            score += 11.0
        else:
            score += 4.0
        # 下沿承接 10
        if st['sup_tests'] >= cfg['support_test_min']:
            score += 10.0
        else:
            score += 3.0
        # 成交量收缩 15
        if st['vol_shrink'] < 0.80:
            score += 15.0
        elif st['vol_shrink'] < 0.95:
            score += 11.0
        elif st['vol_shrink'] <= 1.05:
            score += 7.0
        else:
            score += 3.0
        # MA20结构 10
        if -0.01 <= st['ma20_trend'] <= 0.05:
            score += 10.0
        elif st['ma20_trend'] > 0.05:
            score += 8.0
        elif st['ma20_trend'] > -0.02:
            score += 6.0
        else:
            score += 2.0
        # MA60结构 5
        if st['ma60_trend'] >= 0:
            score += 5.0
        elif st['ma60_trend'] > -0.02:
            score += 3.0
        else:
            score += 1.0
        # 涨跌平衡 10
        if st['balance'] >= 0.6:
            score += 10.0
        elif st['balance'] >= 0.4:
            score += 7.0
        else:
            score += 4.0
        # ATR压缩 10
        if st['atr_compress'] < 0.75:
            score += 10.0
        elif st['atr_compress'] < 0.95:
            score += 7.0
        elif st['atr_compress'] <= 1.05:
            score += 4.0
        else:
            score += 1.0
        # 惩罚：持续放量大阴线（假平台）
        if st['big_dn_days'] >= 3:
            score -= 8.0
        return round(max(0.0, min(100.0, score)), 1)

    # ═══════════════════════════════════════════
    # 阶段二：突破识别与验证（第四、五节）
    # ═══════════════════════════════════════════
    def _breakout_valid(self, df: pd.DataFrame, b: int, level: float, platform: dict) -> Optional[dict]:
        """突破日 b 的有效性（价格+量能+收盘位置）。返回 None 表示非有效突破。"""
        cfg = self.cfg
        row = df.iloc[b]
        close_b = float(row['close'])
        vol_b = float(row['vol'])
        atr = platform['atr']
        if atr <= 0:
            return None
        loc = close_location(row)
        # 1. 价格：Close > Level + 0.3*ATR
        ok_price = close_b > level + cfg['breakout_atr_buffer'] * atr
        # 2. 幅度 >= 1%
        pct = close_b / level - 1.0
        ok_pct = pct >= cfg['breakout_pct_min']
        # 3. 量比 >= 1.30
        vol_ma20 = float(row['vol_ma20']) if not np.isnan(row['vol_ma20']) else 0.0
        vr = vol_b / vol_ma20 if vol_ma20 > 0 else 0.0
        ok_vol = vr >= cfg['breakout_vol_ratio_min']
        if vr < cfg['breakout_vol_ratio_weak']:
            return None  # 量能不足直接无效
        # 4. 收盘位置 >= 0.75
        ok_loc = loc >= cfg['breakout_close_loc_min']
        # 5. K线质量：非巨量长上影
        usr = upper_shadow_ratio(row)
        ok_candle = not (usr > 0.4 and vr > 3.0)
        # 阴线突破直接无效
        if close_b < float(row['open']):
            return None
        if not (ok_price and ok_pct and ok_vol and ok_loc and ok_candle):
            return None
        return {
            'b': b, 'level': level, 'close': close_b, 'high': float(row['high']),
            'pct': pct, 'vol_ratio': vr, 'close_loc': loc, 'upper_shadow': usr,
            'vol': vol_b, 'atr': atr,
        }

    def _breakout_score(self, bc: dict, platform: dict, post: dict, theme: float = 0.0) -> float:
        """BREAKOUT_SCORE 100分（第五节）
        幅度15 量能20 收盘位置15 K线质量15 持续性20 平台质量10 市场行业5
        """
        cfg = self.cfg
        s = 0.0
        # 幅度 15
        if bc['pct'] >= cfg['breakout_pct_strong']:
            s += 15.0
        elif bc['pct'] >= cfg['breakout_pct_good']:
            s += 13.0
        elif bc['pct'] >= cfg['breakout_pct_min']:
            s += 11.0
        if bc['pct'] >= cfg['breakout_pct_excessive']:
            s -= 4.0  # 单日爆涨警惕高潮
        # 量能 20
        vr = bc['vol_ratio']
        lo, hi = cfg['breakout_vol_ratio_ideal']
        if lo <= vr <= hi:
            s += 20.0
        elif vr >= cfg['breakout_vol_ratio_min']:
            s += 16.0
        elif vr > hi:
            s += 12.0
        else:
            s += 6.0
        # 收盘位置 15
        if bc['close_loc'] >= cfg['breakout_close_loc_good']:
            s += 15.0
        elif bc['close_loc'] >= cfg['breakout_close_loc_min']:
            s += 12.0
        else:
            s += 8.0
        # K线质量 15（实体阳线+上影短）
        s += 9.0 if bc['upper_shadow'] <= 0.2 else (6.0 if bc['upper_shadow'] <= 0.4 else 2.0)
        s += 6.0 if bc['close'] >= bc['high'] * 0.97 else 3.0
        # 持续性 20（突破后1~3日确认）
        s += post['score']
        # 平台质量 10
        s += 10.0 * platform['score'] / 100.0
        # 市场/行业共振 5
        s += theme
        return round(max(0.0, min(100.0, s)), 1)

    def _post_breakout_check(self, df: pd.DataFrame, b: int, level: float, atr: float, end_idx: int) -> dict:
        """突破后确认窗口（1~3日）：收盘维持/MA5向上/量能不崩塌"""
        cfg = self.cfg
        days = end_idx - b
        closes = df['close'].values.astype(float)
        vols = df['vol'].values.astype(float)
        ma5 = df['ma5'].values
        seg = range(b + 1, end_idx + 1)
        below = int(sum(1 for i in seg if closes[i] < level))
        below_run = 0
        run = 0
        for i in seg:
            if closes[i] < level:
                run += 1
                below_run = max(below_run, run)
            else:
                run = 0
        # 连续2日收盘回平台 -> FAILED
        failed = below_run >= cfg['breakout_fail_close_below_run']
        # 收盘维持突破位之上比例
        n_seg = len(list(seg))
        hold_ratio = 1.0 - below / n_seg if n_seg > 0 else 1.0
        # MA5 向上
        ma5_up = bool(days >= 1 and not np.isnan(ma5[end_idx]) and not np.isnan(ma5[end_idx - 1])
                      and ma5[end_idx] > ma5[end_idx - 1])
        # 量能崩塌检测：突破后量能连续 < 突破日量的 40%
        vol_collapse = False
        if days >= 2:
            cnt = sum(1 for i in seg if vols[i] < vols[b] * 0.4)
            vol_collapse = cnt >= 2
        score = 0.0
        if days == 0:
            score = 12.0  # 突破当日：尚未有确认信息，给中性分
        else:
            score += 8.0 * hold_ratio
            score += 4.0 if ma5_up else 0.0
            score += 4.0 if not vol_collapse else 0.0
            if failed:
                score = 0.0
        return {'failed': failed, 'below': below, 'below_run': below_run,
                'hold_ratio': hold_ratio, 'ma5_up': ma5_up, 'vol_collapse': vol_collapse,
                'days': days, 'score': round(score, 1)}

    # ═══════════════════════════════════════════
    # 阶段三：首次回踩识别（第六、七节）
    # ═══════════════════════════════════════════
    def _analyze_pullback(self, df: pd.DataFrame, b: int, bc: dict, end_idx: int) -> Optional[dict]:
        """分析突破后的首次回踩结构（基于突破后峰值 BreakoutHigh）

        回踩定义（第六节）：有效突破之后第一次明显回落，但尚未破坏突破结构。
        - BreakoutHigh = 突破日至今的最高价
        - 无实质回落（峰值回撤 < 0.3*ATR 且价格仍延续上行）返回 None（等待回踩）
        - 当日即峰值且冲高回落（收阴 + 回撤>=0.5*ATR）视为回踩进行中
        """
        cfg = self.cfg
        closes = df['close'].values.astype(float)
        highs = df['high'].values.astype(float)
        lows = df['low'].values.astype(float)
        vols = df['vol'].values.astype(float)
        opens = df['open'].values.astype(float)
        ma5 = df['ma5'].values
        level = bc['level']
        atr = bc['atr']

        # 突破后峰值（BreakoutHigh）：从突破日次日开始搜索，
        # 避免把突破日当天的上影线振幅误判为"回踩"（规范：回踩是突破后的回落）
        if b + 1 > end_idx:
            return None
        peak_idx = b + 1 + int(np.argmax(highs[b + 1:end_idx + 1]))
        peak = float(highs[peak_idx])
        bc['high'] = peak  # 突破段最高价（供深度计算）

        # 峰值至今最低
        pl_idx = peak_idx + int(np.argmin(lows[peak_idx:end_idx + 1]))
        pl = float(lows[pl_idx])
        decline = peak - pl

        if peak_idx == end_idx:
            # 当日即峰值：仅当日冲高回落（收阴 + 深回撤）视为回踩进行中
            if not (decline >= 0.5 * atr and closes[end_idx] < closes[end_idx - 1]):
                return None
        elif decline < 0.3 * atr:
            # 无实质回落，价格延续上行
            return None

        # 回踩深度（第七节）：(BreakoutHigh - PullbackLow) / (BreakoutHigh - BreakoutLevel)
        denom = peak - level
        depth = (peak - pl) / denom if denom > 0 else 1.0
        # 回踩天数：峰值 -> 至今（进行中）或 -> 最低点（已回升）
        days_since_peak = end_idx - peak_idx
        pb_days = pl_idx - peak_idx if pl_idx > peak_idx else days_since_peak
        if pb_days < 1:
            pb_days = days_since_peak if days_since_peak >= 1 else 1

        # 回踩段（峰值之后）平均量 / 突破日量
        pb_seg = list(range(peak_idx + 1, end_idx + 1))
        if pb_seg:
            seg_vol = float(np.mean(vols[pb_seg[0]:pb_seg[-1] + 1]))
        else:
            seg_vol = float(vols[end_idx])
        vol_ratio = seg_vol / vols[b] if vols[b] > 0 else 0.0
        # 单日放量下跌检测（下跌日量/突破日量最大值）
        max_dn_vol_ratio = 0.0
        for i in pb_seg:
            rr = vols[i] / vols[b] if vols[b] > 0 else 0.0
            if closes[i] < closes[i - 1] and rr > max_dn_vol_ratio:
                max_dn_vol_ratio = rr

        # 是否有效跌破突破位（PullbackLow >= Level - 0.8*ATR）
        broke = pl < level - cfg['pullback_level_atr_tolerance'] * atr
        # 收盘跌破计数（突破后全段）
        seg_all = list(range(b + 1, end_idx + 1))
        close_below_cnt = int(sum(1 for i in seg_all if closes[i] < level))
        # 连续2日收盘回平台 -> BREAKOUT FAILURE（保险，事件搜索层已过滤）
        run = 0
        below_run = 0
        for i in seg_all:
            if closes[i] < level:
                run += 1
                below_run = max(below_run, run)
            else:
                run = 0
        breakout_failure = below_run >= cfg['breakout_fail_close_below_run']
        # 放量跌破：下跌日收盘<突破位 且 量>突破日量
        vol_break = False
        for i in seg_all:
            if closes[i] < level and closes[i] < closes[i - 1] and vols[b] > 0 and vols[i] > vols[b]:
                vol_break = True
                break

        # ── 回踩结束证据（第八节，至少2项）──
        evidences = []
        i_end = end_idx
        row_end = df.iloc[i_end]
        # 1) 连续缩量（最近2日量递减）
        if end_idx - peak_idx >= 2 and all(vols[i] <= vols[i - 1] for i in (i_end, i_end - 1)):
            evidences.append('连续缩量')
        # 2) 下影线明显（下影 >= 实体1.5倍 或 >= 振幅40%）
        body = abs(float(row_end['close']) - float(row_end['open']))
        lower = min(float(row_end['open']), float(row_end['close'])) - float(row_end['low'])
        rng = float(row_end['high']) - float(row_end['low'])
        if rng > 0 and (lower >= body * 1.5 or lower / rng >= 0.4):
            evidences.append('长下影')
        # 3) 当日低点不创新低（高于回踩段此前最低）
        if pb_seg and pb_seg[0] < end_idx and float(row_end['low']) > float(np.min(lows[pb_seg[0]:end_idx])):
            evidences.append('低点不创新低')
        # 4) 收盘重新站上MA5
        if not np.isnan(ma5[i_end]) and float(row_end['close']) > ma5[i_end]:
            evidences.append('站上MA5')
        # 5) 当日涨幅转正
        if float(row_end['close']) > float(row_end['open']):
            evidences.append('当日转阳')
        # 6) 收盘站上前一日高点
        if end_idx >= 1 and float(row_end['close']) > highs[i_end - 1]:
            evidences.append('收复前日高点')
        # 7) 量能开始恢复
        if end_idx >= 1 and vols[i_end] > vols[i_end - 1]:
            vm = df['vol_ma20'].values[i_end]
            if not np.isnan(vm) and vols[i_end] > vm * 0.9:
                evidences.append('量能恢复')

        # ── Pullback Score 100分制（换算自第十五节 PULLBACK 25 分权重）──
        p_score = 0.0
        # 深度（60%~80% 偏深、>80% 过深风险显著）
        d_lo, d_hi = cfg['pullback_depth_ideal']
        if d_lo <= depth <= d_hi:
            p_score += 24.0
        elif depth < d_lo:
            p_score += 16.0  # 过浅：换手不足
        elif depth <= cfg['pullback_depth_too_deep']:
            p_score += 13.0  # 偏深
        else:
            p_score += 6.0   # 过深：风险显著增加
        # 时间（用回踩持续天数）
        t_lo, t_hi = cfg['pullback_days_ideal']
        tb_lo, tb_hi = cfg['pullback_days_best']
        if tb_lo <= pb_days <= tb_hi:
            p_score += 16.0
        elif t_lo <= pb_days <= t_hi:
            p_score += 12.0
        elif pb_days <= cfg['pullback_days_max']:
            p_score += 6.0
        else:
            p_score += 2.0
        # 缩量
        if vol_ratio <= cfg['pullback_vol_ratio_good']:
            p_score += 28.0
        elif vol_ratio <= cfg['pullback_vol_ratio_ok']:
            p_score += 21.0
        else:
            p_score += 7.0
        if max_dn_vol_ratio > cfg['pullback_high_vol_ban']:
            p_score -= 15.0
        # 关键位承接
        if not broke:
            p_score += 20.0
        else:
            p_score += 5.0
        if close_below_cnt >= 1:
            p_score -= 8.0
        # K线质量：阴线实体逐渐缩小
        bodies_dn = [abs(closes[i] - opens[i]) for i in pb_seg if closes[i] < opens[i]]
        if len(bodies_dn) >= 2:
            h = len(bodies_dn) // 2
            if float(np.mean(bodies_dn[h:])) < float(np.mean(bodies_dn[:h])):
                p_score += 12.0
        p_score = round(max(0.0, min(100.0, p_score)), 1)

        # ── 曾转强失败检测（EARLY_BUY 一次性约束）──
        # 回踩段内（含昨日至前几日）是否已出现过"收盘站上MA5"的K线，
        # 若曾转强又回落，说明首次试探失败，不应重复 EARLY_BUY
        tried_turn = False
        for i in range(peak_idx + 1, end_idx):
            if not np.isnan(ma5[i]) and closes[i] > ma5[i]:
                tried_turn = True
                break

        return {
            'peak_idx': peak_idx, 'peak': peak,
            'start_idx': peak_idx + 1, 'low': pl, 'low_idx': pl_idx, 'depth': depth,
            'days': days_since_peak, 'pb_days': pb_days,
            'vol_ratio': vol_ratio, 'max_dn_vol_ratio': max_dn_vol_ratio,
            'broke_level': broke, 'close_below_cnt': close_below_cnt,
            'breakout_failure': breakout_failure, 'vol_break': vol_break,
            'evidences': evidences, 'n_evidence': len(evidences), 'score': p_score,
            'tried_turn': tried_turn,
        }

    def _count_pullback_cycles(self, df: pd.DataFrame, b: int, end_idx: int, atr: float) -> (int, bool):
        """统计突破后已完成的"回踩->回升创新高"循环数

        返回 (cycles, in_decline)：
          cycles >= 2 或 (cycles >= 1 且当前处于新回落) -> 第二次回踩循环，
          PRIMARY BUY 模型失效（规范第六节：必须是第一次回踩）
        """
        highs = df['high'].values.astype(float)
        seg = highs[b + 1:end_idx + 1]
        if len(seg) < 3:
            return 0, False
        thr = max(0.8 * atr, 0.02 * float(seg[0]))
        cycles = 0
        run_peak = float(seg[0])
        in_decline = False
        for h in seg[1:]:
            if not in_decline:
                run_peak = max(run_peak, float(h))
                if run_peak - float(h) >= thr:
                    in_decline = True
            else:
                if float(h) >= run_peak:  # 回升创新高：一个完整循环
                    cycles += 1
                    in_decline = False
                    run_peak = float(h)
        return cycles, in_decline

    # ═══════════════════════════════════════════
    # 阶段五：重新转强（第九节）
    # ═══════════════════════════════════════════
    def _reacceleration_check(self, df: pd.DataFrame, b: int, end_idx: int, pb: dict) -> dict:
        """重新转强判定：价格/均线/量能/收盘位置"""
        cfg = self.cfg
        row = df.iloc[end_idx]
        close = float(row['close'])
        vol = float(row['vol'])
        ma5 = df['ma5'].values
        ma10 = df['ma10'].values
        ma20 = df['ma20'].values
        vol_ma20 = df['vol_ma20'].values
        atr = pb['atr'] if 'atr' in pb else _safe(lambda: float(df['atr20'].values[end_idx]))
        # 价格条件
        price_ok = close > ma5[end_idx] and close > float(df['high'].values[end_idx - 1])
        # 均线
        ma_ok = bool(ma5[end_idx] > ma10[end_idx] and not np.isnan(ma10[end_idx - 1])
                     and ma10[end_idx] >= ma10[end_idx - 1])
        # 量能：转强日量比
        vm = vol_ma20[end_idx]
        vr = vol / vm if (not np.isnan(vm) and vm > 0) else 0.0
        vol_ok = vr >= cfg['reacc_vol_ratio_min']
        # 收盘位置
        loc = close_location(row)
        loc_ok = loc >= cfg['reacc_close_loc_min']
        # 优选：收盘 > 回踩前一根K线高点（回踩起点前一日=突破日或突破后高点K线）
        prev_k_high = float(df['high'].values[end_idx - 1])
        good = close > prev_k_high and loc >= cfg['reacc_close_loc_good']
        return {
            'price_ok': price_ok, 'ma_ok': ma_ok, 'vol_ok': vol_ok, 'loc_ok': loc_ok,
            'vol_ratio': vr, 'close_loc': loc, 'good': good, 'atr': atr,
            'all_ok': bool(price_ok and ma_ok and vol_ok and loc_ok),
        }

    # ═══════════════════════════════════════════
    # 主入口
    # ═══════════════════════════════════════════
    def score(self, df: pd.DataFrame, ts_code: str = '', name: str = '',
              industry: str = '', market_regime: str = 'neutral',
              theme_info: Optional[dict] = None,
              cache_key=None) -> PBPResult:
        cfg = self.cfg
        # 平台缓存失效：cache_key 为 None 或数据源变化时清空，
        # 保证跨股票/跨数据复用引擎实例也不会读到过期平台结果
        if cache_key is None or cache_key != self._cache_key:
            self._platform_cache.clear()
            self._cache_key = cache_key
        r = PBPResult(ts_code=ts_code, name=name, industry=industry, market_regime=market_regime)
        if theme_info:
            r.theme_score = float(theme_info.get('score', 0.0))
            r.theme_detail = theme_info
        n = len(df)
        r.n_bars = n
        if n < cfg['min_bars']:
            r.reasons.append(f'K线不足{cfg["min_bars"]}根')
            r.state = STATE_INVALIDATED
            return r
        end_idx = n - 1
        r.date = str(df['trade_date'].iloc[end_idx])

        # 若已预计算过指标（回测多日复用场景）则跳过重复 enrich
        if 'atr20' not in df.columns or df['atr20'].iloc[-1] != df['atr20'].iloc[-1]:  # NaN check
            df = enrich(df)
        elif 'ma5' not in df.columns:
            df = enrich(df)
        closes = df['close'].values.astype(float)
        highs = df['high'].values.astype(float)
        lows = df['low'].values.astype(float)
        vols = df['vol'].values.astype(float)
        r.close = float(closes[end_idx])
        r.ma5 = _safe(lambda: float(df['ma5'].values[end_idx]))
        r.ma10 = _safe(lambda: float(df['ma10'].values[end_idx]))
        r.ma20 = _safe(lambda: float(df['ma20'].values[end_idx]))
        r.ma60 = _safe(lambda: float(df['ma60'].values[end_idx]))
        r.atr20 = _safe(lambda: float(df['atr20'].values[end_idx]))
        r.prev_high = float(highs[end_idx - 1]) if end_idx >= 1 else 0.0

        # ── 搜索最近 break_search_days 内的突破日 ──
        # 铁律1：先有平台，后有突破。逐日（从远到近）寻找"平台->突破"对
        search_start = max(cfg['platform_days_max'] + 25, end_idx - cfg['breakout_search_days'])
        best_event = None   # 最优突破事件
        fail_event = None   # 最近失败事件
        for b in range(search_start, end_idx + 1):
            platform = self._platform_for(df, b)
            if platform is None or platform['score'] < cfg['platform_score_min']:
                continue
            bc = self._breakout_valid(df, b, platform['high'], platform)
            if bc is None:
                continue
            post = self._post_breakout_check(df, b, platform['high'], platform['atr'], end_idx)
            bs = self._breakout_score(bc, platform, post, r.theme_score)
            if post['failed']:
                fail_event = {'b': b, 'platform': platform, 'bc': bc, 'post': post, 'score': bs}
                continue
            # 有效事件：平台>=75 且 突破>=75 才允许进入回踩识别（铁律2）
            if platform['score'] >= cfg['platform_score_min'] and bs >= cfg['breakout_score_min']:
                if best_event is None or b > best_event['b']:
                    best_event = {'b': b, 'platform': platform, 'bc': bc, 'post': post, 'score': bs}

        # 平台信息（无突破时也输出当前识别到的最优平台供观察）
        cur_platform = self._platform_for(df, end_idx)
        if cur_platform is not None:
            self._fill_platform(r, cur_platform, df)

        if best_event is None:
            # 无有效突破事件
            if fail_event is not None:
                self._fill_event(r, fail_event, df, end_idx)
                r.breakout_failed = True
                r.state = STATE_BREAKOUT_FAILED
                r.action = ACTION_BREAKOUT_FAILED
                r.reasons.append('突破后连续收盘跌回平台，突破失败')
                return r
            # 尚在平台内/接近突破
            if cur_platform is not None and cur_platform['score'] >= cfg['platform_score_min']:
                r.state = STATE_PLATFORM_CONFIRMED
                r.action = ACTION_WAIT_BREAKOUT
                r.reasons.append(f'平台已确认(score={cur_platform["score"]:.0f})，等待有效突破')
                # 接近突破判定
                if r.close > cur_platform['high'] - 0.5 * cur_platform['atr']:
                    r.state = STATE_NEAR_BREAKOUT
                    r.reasons.append('价格已接近平台上沿')
            else:
                r.state = STATE_PLATFORM_BUILDING
                r.action = ACTION_WAIT_PLATFORM
                sc = cur_platform['score'] if cur_platform else 0.0
                r.reasons.append(f'平台未成熟或不存在(score={sc:.0f})')
            # 等待状态也给出平台分映射，保证最终分可参与排序
            self._fill_final(r, None, None, None)
            return r

        # ── 有效突破事件 ──
        ev = best_event
        self._fill_event(r, ev, df, end_idx)
        platform, bc, post = ev['platform'], ev['bc'], ev['post']
        b = ev['b']
        level = bc['level']

        # 距离突破位过远禁追高（第十二节）
        r.dist_breakout_level = r.close / level - 1.0 if level > 0 else 0.0
        if r.close > level + cfg['post_breakout_max_atr'] * bc['atr']:
            r.state = STATE_HOLD
            r.action = ACTION_NO_TRADE
            r.reasons.append('当前价距突破位超过2*ATR，盈亏比恶化，禁止追高')
            self._fill_final(r, ev, None, None)
            return r

        # 突破日即当日：BREAKOUT_CONFIRMED，等待回踩
        days = end_idx - b
        if days == 0:
            r.state = STATE_BREAKOUT_CONFIRMED
            r.action = ACTION_WAIT_PULLBACK
            r.reasons.append('突破当日，等待突破确认与首次回踩')
            self._fill_final(r, ev, None, None)
            return r

        # ── 阶段三：首次回踩分析（铁律3）──
        pb = self._analyze_pullback(df, b, bc, end_idx)
        if pb is None:
            r.state = STATE_BREAKOUT_CONFIRMED
            r.action = ACTION_WAIT_PULLBACK
            r.reasons.append('突破后尚未出现实质回落，等待首次回踩')
            self._fill_final(r, ev, None, None)
            return r
        pb['atr'] = bc['atr']
        self._fill_pullback(r, pb, df)
        r.pullback_tried_turn = pb.get('tried_turn', False)

        # 回踩失败判定：连续2日收盘回平台 / 放量跌破突破位 / 深度跌破(-1.0ATR)
        plow_abs = pb['low']
        if pb['breakout_failure'] or pb['vol_break'] or plow_abs < level - cfg['pullback_level_atr_ban'] * bc['atr']:
            r.state = STATE_PULLBACK_FAILED
            r.action = ACTION_PULLBACK_FAILED
            r.pullback_failed = True
            r.reasons.append('放量/深度跌破突破位(-1ATR)或连续收盘回平台，回踩失败')
            self._fill_final(r, ev, pb, None)
            return r

        # 第一次回踩校验（第六节）：突破后若已发生 >=2 次"回踩->回升创新高"循环，
        # 或已发生1次完整循环且当前处于新一轮回落，则 PRIMARY BUY 模型失效
        cycles, in_decline = self._count_pullback_cycles(df, b, end_idx, bc['atr'])
        if cycles >= 2 or (cycles >= 1 and in_decline):
            r.state = STATE_INVALIDATED
            r.action = ACTION_NO_TRADE
            r.reasons.append(f'已发生{cycles}次完整回踩循环，PRIMARY BUY模型失效（规范：必须是第一次回踩）')
            self._fill_final(r, ev, pb, None)
            return r

        r.pullback_first = True

        # ── 回踩结束证据（第八节：至少2项）──
        if pb['n_evidence'] >= cfg['pullback_end_evidence_min']:
            r.state = STATE_PULLBACK_SUPPORT
        else:
            r.state = STATE_FIRST_PULLBACK
            r.action = ACTION_WAIT_REACCELERATION
            r.reasons.append(f'回踩进行中，止跌证据不足({pb["n_evidence"]}/{cfg["pullback_end_evidence_min"]})')
            self._fill_final(r, ev, pb, None)
            return r

        # ── 阶段五：重新转强（铁律4）──
        ra = self._reacceleration_check(df, b, end_idx, pb)
        if not ra['all_ok']:
            r.state = STATE_PULLBACK_SUPPORT
            r.action = ACTION_WAIT_REACCELERATION
            # 诊断填充：转强量比/收盘位置（即使未触发也输出）
            r.reacc_vol_ratio = ra['vol_ratio']
            r.reacc_close_loc = ra['close_loc']
            missing = []
            if not ra['price_ok']:
                missing.append('Close>MA5且>前日High')
            if not ra['ma_ok']:
                missing.append('MA5>MA10且MA10不向下')
            if not ra['vol_ok']:
                missing.append(f'转强量比>=1.10(当前{ra["vol_ratio"]:.2f})')
            if not ra['loc_ok']:
                missing.append(f'收盘位置>=0.70(当前{ra["close_loc"]:.2f})')
            r.reasons.append('回踩承接良好但未重新转强: ' + '；'.join(missing))
            # A级 EARLY BUY（低吸试探）：仍在低吸区（收盘未弱反弹至MA5上方）
            # + 深度20%~80% + 缩量<=0.8 + 未破位 + 低点已确认（非当日）+ 当日未创新低。
            # 依据回测：close<=MA5+深度<=0.8+踩量<=0.8 的候选 5日 +2.14% 胜率78%；
            # 证据>=3 与 tried_turn 与"低点已确认"冲突且无增益，故不再作硬性门槛。
            early = (
                pb['depth'] >= 0.15
                and pb['depth'] <= cfg['pullback_depth_too_deep']   # 深度20%~80%（过深不试探）
                and pb['vol_ratio'] <= cfg['pullback_vol_ratio_ok']
                and not pb['broke_level']
                and r.close <= r.ma5                              # 仍在低吸区（非弱反弹）
                and pb['low_idx'] < end_idx                       # 回踩低点已确认（非今日新低）
                and float(lows[end_idx]) >= pb['low']             # 当日未跌破回踩低点
            )
            if early:
                r.action = ACTION_EARLY_BUY
                r.reasons.append('回踩标准+缩量+承接强，可轻仓试探(20%~30%)')
            self._fill_final(r, ev, pb, ra)
            # 市场过滤器（第十三节）：EARLY_BUY 同样受限——weak/bear 不低吸、
            # neutral 需>=78、bull 需>=70；不达标降级为等待或放弃。
            if r.action == ACTION_EARLY_BUY:
                mf = cfg['market_filter'].get(market_regime, cfg['market_filter']['neutral'])
                r.action = self._final_action(r, False, mf)
                if r.action == ACTION_NO_TRADE:
                    r.reasons.append('市场环境不允许低吸试探(weak/bear或分数不足)')
            return r

        # ── 重新转强成立 ──
        r.reacc_found = True
        r.reacc_date = r.date
        r.reacc_price = r.close
        r.reacc_vol_ratio = ra['vol_ratio']
        r.reacc_close_loc = ra['close_loc']
        r.reacc_ma5_ma10 = ra['ma_ok']

        # 二次突破（B级 CONFIRMED BUY）：Close > PullbackHigh 且量比>1.2
        pb_high = pb['peak']
        if pb_high > 0 and r.close > pb_high and ra['vol_ratio'] > cfg['confirmed_vol_ratio_min']:
            r.confirmed_break = True

        # ── S级 PRIMARY BUY 硬性条件（第十节）──
        s_ok = (
            platform['score'] >= cfg['s_platform_score_min']
            and ev['score'] >= cfg['s_breakout_score_min']
            and r.pullback_first
            and pb['vol_ratio'] <= cfg['s_pullback_vol_ratio_max']
            and pb['low'] >= level - cfg['pullback_level_atr_tolerance'] * bc['atr']
            and r.close > r.ma5
            and r.close > r.prev_high
            and r.ma5 > r.ma10
            and ra['vol_ratio'] >= cfg['reacc_vol_ratio_min']
            and ra['close_loc'] >= cfg['s_close_loc_min']
        )
        self._fill_final(r, ev, pb, ra)

        # ── 市场过滤器（第十三节）──
        mf = cfg['market_filter'].get(market_regime, cfg['market_filter']['neutral'])
        action = self._final_action(r, s_ok, mf)
        r.action = action

        # 状态机落位
        if action == ACTION_PRIMARY_BUY:
            r.state = STATE_PRIMARY_BUY
        elif action == ACTION_CONFIRMED_BUY:
            r.state = STATE_PRIMARY_BUY
        elif action == ACTION_EARLY_BUY:
            r.state = STATE_PULLBACK_SUPPORT
        else:
            r.state = STATE_RE_ACCELERATION
        return r

    def _fill_platform(self, r: PBPResult, p: dict, df: pd.DataFrame):
        r.platform_found = True
        r.platform_start = str(df['trade_date'].iloc[p['start']])
        r.platform_end = str(df['trade_date'].iloc[p['end'] - 1]) if p['end'] - 1 >= p['start'] else r.platform_start
        r.platform_high = p['high']
        r.platform_low = p['low']
        r.platform_range = p['range']
        r.platform_days = p['days']
        r.resistance_tests = p['res_tests']
        r.support_tests = p['sup_tests']
        r.platform_score = p['score']
        r.platform_vol_shrink = p['vol_shrink']
        r.platform_atr_compress = p['atr_compress']
        r.platform_volatility_converge = p['converge']
        if p['score'] >= self.cfg['platform_score_a_plus']:
            r.platform_grade = 'A+'
        elif p['score'] >= self.cfg['platform_score_a']:
            r.platform_grade = 'A'
        elif p['score'] >= self.cfg['platform_score_b']:
            r.platform_grade = 'B'
        else:
            r.platform_grade = 'C'

    def _fill_event(self, r: PBPResult, ev: dict, df: pd.DataFrame, end_idx: int):
        platform, bc, post = ev['platform'], ev['bc'], ev['post']
        self._fill_platform(r, platform, df)
        r.breakout_found = True
        r.breakout_date = str(df['trade_date'].iloc[ev['b']])
        r.breakout_level = bc['level']
        r.breakout_price = bc['close']
        r.breakout_pct = bc['pct']
        r.breakout_vol_ratio = bc['vol_ratio']
        r.breakout_close_loc = bc['close_loc']
        r.breakout_high = bc['high']
        r.breakout_score = ev['score']
        r.breakout_days_ago = end_idx - ev['b']
        r.breakout_failed = post.get('failed', False)
        if ev['score'] >= self.cfg['breakout_score_strong']:
            r.breakout_grade = '强突破'
        elif ev['score'] >= self.cfg['breakout_score_min']:
            r.breakout_grade = '有效突破'
        elif ev['score'] >= 65:
            r.breakout_grade = '弱突破'
        else:
            r.breakout_grade = '无效突破'

    def _fill_pullback(self, r: PBPResult, pb: dict, df: pd.DataFrame):
        r.pullback_started = True
        r.pullback_start = str(df['trade_date'].iloc[pb['start_idx']]) if pb['start_idx'] < len(df) else ''
        r.pullback_low = pb['low']
        r.pullback_low_date = str(df['trade_date'].iloc[pb['low_idx']])
        r.pullback_depth = pb['depth']
        r.pullback_days = pb['pb_days']  # 回踩持续天数（峰值->最低/至今）
        r.pullback_vol_ratio = pb['vol_ratio']
        r.pullback_broke_level = pb['broke_level']
        r.pullback_score = pb['score']
        r.pullback_end_evidence = pb['n_evidence']
        r.pullback_end_evidences = list(pb['evidences'])

    def _fill_final(self, r: PBPResult, ev: Optional[dict], pb: Optional[dict], ra: Optional[dict]):
        """最终100分模型（第十五节）：PLATFORM 30 + BREAKOUT 25 + PULLBACK 25 + REACC 20"""
        # PLATFORM 30（从100分制平台分映射）
        if ev is not None:
            p = ev['platform']['score']
            b = ev['score']
            r.score_platform = round(p * 30.0 / 100.0, 1)
            r.score_breakout = round(b * 25.0 / 100.0, 1)
        else:
            p = r.platform_score
            r.score_platform = round(p * 30.0 / 100.0, 1)
            r.score_breakout = 0.0
        # PULLBACK 25
        if pb is not None:
            r.score_pullback = round(pb['score'] * 25.0 / 100.0, 1)
        else:
            r.score_pullback = 0.0
        # 回踩未转强信号（WAIT_REACCELERATION）打折：止跌证据成数×0.5 + 回踩成熟度×0.5。
        # 依据：回测显示"仍在下跌但结构漂亮"的等待信号 5 日收益为负，不应以满分挤入 A 级档
        # （低吸分支 EARLY_BUY 与已转强信号不受影响）。
        if (r.score_pullback > 0 and not r.reacc_found
                and r.action == ACTION_WAIT_REACCELERATION):
            ev_min = 3.0
            evidence_f = min(1.0, r.pullback_end_evidence / ev_min)
            da = r.breakout_days_ago
            mature_f = min(1.0, (da - 1) / 3.0) if da >= 1 else 0.0
            pb_factor = 0.5 * evidence_f + 0.5 * mature_f
            r.score_pullback = round(r.score_pullback * pb_factor, 1)
        # REACC 20：转强5 + MA5/MA10 4 + 放量4 + 突破回踩高点4 + 分时强度3(日线近似)
        if ra is not None:
            s = 0.0
            s += 5.0 if ra['price_ok'] else 0.0
            s += 4.0 if ra['ma_ok'] else 0.0
            s += 4.0 if ra['vol_ok'] else 0.0
            if ra.get('good'):
                s += 7.0  # 突破回踩前高 + 分时近似（收盘位优）
            elif ra.get('close_loc', 0) >= self.cfg['reacc_close_loc_good']:
                s += 5.0
            elif ra.get('loc_ok'):
                s += 3.0
            r.score_reacc = round(min(20.0, s), 1)
        else:
            r.score_reacc = 0.0
        r.final_score = round(r.score_platform + r.score_breakout + r.score_pullback + r.score_reacc, 1)
        # 等待转强信号硬顶：未转强即非买点，终分不得进入 A 级档（>=final_grade_a）。
        # 转强确认后由次日扫描以 EARLY_BUY/PRIMARY 重新评定，语义上"等待≠可买"。
        if r.action == ACTION_WAIT_REACCELERATION:
            cap = self.cfg['final_grade_a'] - 0.1
            if r.final_score >= cap:
                r.final_score = cap
        # 评级（第十五节）
        self._grade_stars(r)

    def _grade_stars(self, r: PBPResult):
        """按 final_score 评定星级（_final_action 硬顶后需重新评级）"""
        if r.final_score >= self.cfg['final_grade_s']:
            r.stars = 5
            r.grade = '★★★★★'
        elif r.final_score >= self.cfg['final_grade_strong']:
            r.stars = 4
            r.grade = '★★★★☆'
        elif r.final_score >= self.cfg['final_grade_a']:
            r.stars = 3
            r.grade = '★★★☆☆'
        elif r.final_score >= self.cfg['final_grade_wait']:
            r.stars = 2
            r.grade = '★★☆☆☆'
        else:
            r.stars = 0
            r.grade = ''

    def _final_action(self, r: PBPResult, s_ok: bool, mf: dict) -> str:
        """最终交易结论：S级硬性条件 + 最终评分 + 市场过滤器"""
        cfg = self.cfg
        # 严禁买入（第十二节）已在前面分支处理；此处只做分级
        if s_ok and r.final_score >= cfg['final_grade_s']:
            action = ACTION_PRIMARY_BUY
        elif r.confirmed_break and r.final_score >= cfg['final_grade_strong']:
            action = ACTION_CONFIRMED_BUY
        elif r.final_score >= cfg['final_grade_a']:
            # A 级：EARLY_BUY 仅限"未转强"的低吸试探；已转强但未二次突破 -> 等待确认
            # （转强成立不是低吸位，误标 EARLY_BUY 会让深回踩+冲高信号混入试探仓）
            action = ACTION_EARLY_BUY if not r.reacc_found else ACTION_WAIT_REACCELERATION
        elif r.final_score >= cfg['final_grade_wait']:
            action = ACTION_WAIT_REACCELERATION
        else:
            action = ACTION_NO_TRADE
        # 市场过滤器（第十三节）
        if action in (ACTION_PRIMARY_BUY, ACTION_EARLY_BUY, ACTION_CONFIRMED_BUY):
            if action not in mf['allow']:
                # weak/bear 豁免：极强信号（>=extreme_final 且行业前10%）
                extreme = (r.final_score >= cfg['extreme_final']
                           and r.theme_detail.get('rank_pct') is not None
                           and r.theme_detail.get('rank_pct') <= cfg['extreme_theme_rank'])
                if not extreme:
                    action = ACTION_NO_TRADE
            elif r.final_score < mf['min_final']:
                action = ACTION_WAIT_REACCELERATION if r.final_score >= cfg['final_grade_wait'] else ACTION_NO_TRADE
        # 等待转强信号硬顶：未确认即非买点，终分不得进入 A 级档（>=final_grade_a）。
        if action == ACTION_WAIT_REACCELERATION and r.final_score >= cfg['final_grade_a']:
            r.final_score = cfg['final_grade_a'] - 0.1
            self._grade_stars(r)
        return action
