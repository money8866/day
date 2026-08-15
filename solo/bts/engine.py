# -*- coding: utf-8 -*-
"""
BTS（Breakout Trend Start）核心引擎
================================================
流程：Base(震荡平台) → Resistance(压力簇) → Breakout(真突破)
      → Post-Breakout(突破后确认) → MA5趋势 → 量能持续 → 量价配合
      → 回踩质量 → Extension控制 → BTS Score / Entry Score / Signal

防未来数据：本引擎只接收"已截断到 T 日"的 DataFrame（T 日为最后一根K线），
全部计算只用 <=T 的数据。未来收益由回测层单独计算，绝不进入 score/feature。

信号门槛（GATE）：
  BTS >= 70 且 BreakoutConfirmed 且 MA5Trend 且 VolumePersistence → 正式买入池
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .config import BTS_CONFIG, SCORE_WEIGHTS, SIGNAL_CN, GRADE_STARS
from .indicators import add_ma, add_vol_ma, add_rsi, ma5_up_streak, ma_slope


@dataclass
class BTSResult:
    ts_code: str = ''
    name: str = ''
    industry: str = ''
    date: str = ''

    bts_score: float = 0.0
    entry_score: float = 0.0
    grade: str = 'NO_BUY'          # S/A/B/C/WATCH/NO_BUY
    signal: str = 'NO_SIGNAL'      # BREAKOUT_NOW / TREND_START / PULLBACK_BUY / TREND_EXTENDED / FAILED_BREAKOUT
    signal_cn: str = '无信号'
    buy_point: str = 'NO_BUY'      # BUY-A / BUY-B / BUY-C / WATCH / NO_BUY
    status: str = 'NEW'            # NEW / CONTINUE / UPGRADE / DOWNGRADE（跨日对比）

    breakout_date: str = ''
    days_after_breakout: int = -1

    base_start: str = ''
    base_end: str = ''
    base_days: int = 0
    base_high: float = 0.0
    base_low: float = 0.0
    base_range: float = 0.0        # 0~1
    base_slope: float = 0.0        # 平台总涨跌幅
    resistance: float = 0.0
    resistance_width: float = 0.0
    resistance_touches: int = 0

    close: float = 0.0
    ma5: float = 0.0
    ma10: float = 0.0
    ma20: float = 0.0
    ma60: float = 0.0
    distance_ma5: float = 0.0      # close/ma5-1
    distance_ma20: float = 0.0
    distance_breakout: float = 0.0
    ma5_slope_1: float = 0.0
    ma5_slope_3: float = 0.0
    ma10_slope_1: float = 0.0
    ma5_up_streak: int = 0
    ma5_accel: bool = False

    vol: float = 0.0
    vol_ma5: float = 0.0
    vol_ma10: float = 0.0
    vol_ma20: float = 0.0
    vol_ratio: float = 0.0         # vol/vol_ma20 (V1)
    vol_ratio_breakout: float = 0.0
    v2: float = 0.0
    volume_persistence: int = 0    # 最近5日 vol>vol_ma20 天数（GATE用）
    vol_persist_post: int = 0      # 突破日至今 vol>vol_ma20 天数（连续性评分用，V1.4）
    vol_persist_post_n: int = 0
    up_volume_ratio: float = 0.0
    down_volume_ratio: float = 0.0
    up_down_ratio: float = 0.0
    spike_volume: bool = False

    breakout_amp: float = 0.0
    candle_pos: float = 0.0
    upper_shadow_ratio: float = 0.0
    pullback_low: float = 0.0
    pullback_depth: float = 0.0    # (breakout_high - post_low)/breakout_high
    post_breakout_low: float = 0.0
    post_breakout_failed: bool = False
    trend_eff: float = 0.0         # 突破后累计涨幅/交易日数
    ma5_track: int = 0             # 最近5日 close>ma5 天数
    ma5_track_close: float = 0.0

    rsi: float = 0.0
    market_regime: str = 'neutral'
    market_weight: float = 1.0

    score_base: float = 0.0
    score_breakout: float = 0.0
    score_ma5: float = 0.0
    score_vol: float = 0.0
    score_vol_price: float = 0.0
    score_pullback: float = 0.0
    score_ext: float = 0.0

    gate_breakout: bool = False
    gate_ma5: bool = False
    gate_vol: bool = False
    gate_new_high: bool = False       # V1.6：信号日高点站上 120 日新高（过滤未突破前高的弱突破）
    high_120d_new: bool = False       # V1.6：信号日高点是否创 120 日新高
    high_120d_prev: float = 0.0       # V1.6：前 120 日最高价（不含信号日）

    ext_penalty: float = 0.0
    fake_penalty: float = 0.0
    over_vol_penalty: float = 0.0
    day1_premium: float = 0.0        # 突破后第1日确认加分
    sustained_ok: bool = False       # V1.7：突破后稳步向上+量能充沛（非 Day1 持续确认买点）
    mainline_heat: float = 0.0       # V1.8：主线板块加分（scanner 层汇总后回填）
    sector_heat: float = 0.0         # 行业共振加分
    market_cap: float = 0.0          # 总市值（亿元）
    score_mv: float = 0.0            # 市值因子加分（V1.3）
    extra_raw: float = 0.0           # 未压缩附加分(day1+市值，V1.4 供 scanner 统一压缩防饱和)

    core_reason: str = ''
    risk_factors: List[str] = field(default_factory=list)
    action: str = ''
    checks: Dict[str, dict] = field(default_factory=dict)
    n_bars: int = 0


def _safe(fn, default=0.0):
    try:
        v = fn()
        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            return default
        return float(v)
    except Exception:
        return default


class BTSEngine:
    def __init__(self, config: Optional[dict] = None):
        self.cfg = dict(BTS_CONFIG)
        if config:
            self.cfg.update(config)

    # ── 平台 ──
    def _find_base(self, df: pd.DataFrame, b: int):
        """突破日 b 之前的震荡平台。扫描 20~60 日窗口，选最紧凑且不超限的平台。"""
        cfg = self.cfg
        closes = df['close'].values.astype(float)
        highs = df['high'].values.astype(float)
        lows = df['low'].values.astype(float)
        best = None
        for w in range(20, 61, 5):
            s = b - w
            if s < 0:
                continue
            seg_c = closes[s:b]
            seg_h = highs[s:b]
            seg_l = lows[s:b]
            if b - s < max(18, w - 2):
                continue
            hi = float(seg_h.max())
            lo = float(seg_l.min())
            if lo <= 0 or hi <= 0:
                continue
            rng = (hi - lo) / lo
            if rng > cfg['max_base_range']:
                continue
            slope = seg_c[-1] / seg_c[0] - 1.0
            peak = np.maximum.accumulate(seg_c)
            max_dd = float(((peak - seg_c) / peak).max()) if peak[-1] > 0 else 0.0
            sc = -rng * 100 + (b - s) * 0.3 + max(0.0, slope) * 50 \
                 - max(0.0, -slope) * 80 - max_dd * 150
            cand = {'start': s, 'end': b, 'days': b - s, 'high': hi, 'low': lo,
                    'range': rng, 'slope': slope, 'max_dd': max_dd, '_score': sc}
            if best is None or sc > best['_score']:
                best = cand
        if best is None:
            return None
        return best['start'], best['end'], best

    # ── 压力簇 ──
    def _find_resistance(self, df: pd.DataFrame, start: int, end: int):
        """压力簇识别：平台内 highs 贪心聚类，选"触及次数达标且最高"的压力带。
        单根异常K线的高点（触及1次）不会成为平台压力位。"""
        cfg = self.cfg
        highs = df['high'].iloc[start:end].values.astype(float)
        lows = df['low'].iloc[start:end].values.astype(float)
        highs = highs[np.isfinite(highs) & (highs > 0)]
        if len(highs) == 0:
            return None
        tol = cfg['resistance_atol_pct']
        clusters = []
        for h in sorted(highs):
            placed = False
            for cl in clusters:
                if cl[0] * (1 - tol) <= h <= cl[1] * (1 + tol):
                    cl[0] = min(cl[0], h)
                    cl[1] = max(cl[1], h)
                    cl[2] += 1
                    placed = True
                    break
            if not placed:
                clusters.append([h, h, 1])
        cands = [c for c in clusters if c[2] >= cfg['resistance_min_touches']]
        if cands:
            c = max(cands, key=lambda x: (x[1] + x[0]) / 2)
        else:
            c = max(clusters, key=lambda x: x[1])
        price = (c[0] + c[1]) / 2
        width = (c[1] - c[0]) / c[1] if c[1] > 0 else 0.0
        return {'price': price, 'width': width, 'touches': int(c[2]),
                'zone_low': c[0], 'zone_high': c[1], 'base_low': float(lows.min())}

    # ── 突破日条件 ──
    def _breakout_conditions(self, df: pd.DataFrame, b: int, res: dict, base):
        """B1 收盘突破 / B2 K线有效性 / B3 成交量突破"""
        cfg = self.cfg
        row = df.iloc[b]
        close_b = float(row['close'])
        high_b = float(row['high'])
        low_b = float(row['low'])
        open_b = float(row['open'])
        vol_b = float(row['vol'])
        res_p = res['price']
        amp = close_b / res_p - 1.0
        base_vol = float(df['vol'].iloc[base['start']:base['end']].mean()) or 1.0
        vr = vol_b / base_vol if base_vol > 0 else 0.0
        rng = high_b - low_b
        pos = (close_b - low_b) / rng if rng > 0 else (1.0 if close_b >= open_b else 0.0)
        upper = high_b - max(open_b, close_b)
        usr = upper / rng if rng > 0 else 0.0
        ok_b1 = amp >= cfg['breakout_threshold']
        ok_b2 = (close_b > open_b) and (pos >= cfg['candle_min_pos'])
        ok_b3 = vr >= cfg['breakout_volume_ratio']
        return {
            'ok': ok_b1 and ok_b2 and ok_b3,
            'amp': amp, 'vr': vr, 'pos': pos, 'usr': usr,
            'close': close_b, 'high': high_b, 'low': low_b,
            'vol_ratio_breakout': vr,
        }

    # ── 突破后确认 ──
    def _post_check(self, df: pd.DataFrame, b: int, res: dict, end_idx: int):
        cfg = self.cfg
        res_p = res['price']
        if b >= end_idx:
            return {'ok': True, 'post_low': float(df['high'].iloc[b]), 'failed_days': 0}
        seg = df.iloc[b + 1:end_idx + 1]
        post_low = float(seg['low'].min())
        ok = post_low >= res_p * cfg['post_breakout_floor']
        failed_days = int((seg['close'] < res_p * cfg['failure_close_below']).sum())
        if failed_days > 0:
            ok = False
        return {'ok': ok, 'post_low': post_low, 'failed_days': failed_days}

    # ── 突破日搜索（近30日）──
    # 规则：找出满足突破条件的所有候选日；取"最近一段连续突破段"的起点作为突破日
    # （避免把连续上攻的每一天都当成新突破）；若该段后续已确认失败，则回退到更早的
    # 仍成立（post 未破）的突破段。
    def _find_breakout(self, df: pd.DataFrame, end_idx: int):
        cfg = self.cfg
        cands = []  # 每个元素 (b, info)
        closes = df['close'].values.astype(float)
        highs = df['high'].values.astype(float)
        vols = df['vol'].values.astype(float)
        for b in range(max(0, end_idx - 30), end_idx + 1):
            if b < cfg['base_window_min']:
                continue
            # 快速预筛：收盘接近/突破近25日"典型上沿"(90分位，排除单日尖峰)，或显著放量
            lo = b - 25
            if lo < 0:
                continue
            p25 = float(np.quantile(highs[lo:b - 1], 0.90)) if b - 1 > lo else 0.0
            if p25 <= 0:
                continue
            base_v = float(vols[lo:b - 1].mean())
            if not (closes[b] > p25 * 0.98 or (base_v > 0 and vols[b] > base_v * 1.8)):
                continue
            base = self._find_base(df, b)
            if base is None:
                continue
            s, e, bstats = base
            res = self._find_resistance(df, s, e)
            if res is None or res['price'] <= 0:
                continue
            bc = self._breakout_conditions(df, b, res, bstats)
            if not bc['ok']:
                continue
            cands.append((b, {'b': b, 'base': (s, e, bstats), 'res': res, 'bc': bc,
                              'post': self._post_check(df, b, res, end_idx)}))
        if not cands:
            return None, False, ''
        # 最近一段连续突破段的起点
        last_b = cands[-1][0]
        run_start = last_b
        i = len(cands) - 1
        while i > 0 and cands[i][0] == cands[i - 1][0] + 1:
            run_start = cands[i - 1][0]
            i -= 1
        info_map = {b: info for b, info in cands}
        # 首选：连续段起点，且 post 成立
        info = info_map[run_start]
        if info['post']['ok']:
            return info, False, ''
        # 回退：更早的、post 仍成立的突破段
        for b in reversed([b for b, _ in cands]):
            if b < run_start and info_map[b]['post']['ok']:
                return info_map[b], False, ''
        # 全不成立：最新突破段已失败
        return info, True, '突破后跌破平台上沿/收盘跌回平台'

    # ── 子项评分 ──
    def _score_base(self, bstats, res, r: BTSResult) -> float:
        """平台质量 15 分"""
        s = 0.0
        days, rng, slope, max_dd = bstats['days'], bstats['range'], bstats['slope'], bstats['max_dd']
        if days >= 40:
            s += 5.0
        elif days >= 25:
            s += 3.0
        if days >= 60:
            s += 3.0
        if rng <= 0.20:
            s += 5.0
        elif rng <= 0.30:
            s += 2.0
        if res['touches'] >= 3:
            s += 5.0
        elif res['touches'] == 2:
            s += 3.0
        if slope >= -0.02 and max_dd <= 0.15:
            s += 5.0
        elif slope >= -0.05 and max_dd <= 0.18:
            s += 2.0
        return round(min(s, 15.0), 1)

    def _score_breakout(self, bc, r: BTSResult) -> float:
        """突破强度 20 分：幅度(8) + K线质量(6) + 成交量(6)
        V1.5：压低 +1% 弱突破档——温和小阳刚过平台上沿(3分) vs 放量突破盘整(8分) 拉开差距；
        2-4% 幅度仍为理想区，>8% 单日暴涨维持追高惩罚"""
        s = 0.0
        amp = bc['amp']
        if amp >= 0.08:
            s += 1.0
        elif amp >= 0.06:
            s += 4.0
        elif amp >= 0.04:
            s += 7.0
        elif amp >= 0.02:
            s += 8.0
        elif amp >= 0.01:
            s += 3.0
        if bc['pos'] >= 0.75:
            s += 4.0
        elif bc['pos'] >= 0.60:
            s += 3.0
        else:
            s += 1.0
        if bc['usr'] <= 0.20:
            s += 2.0
        elif bc['usr'] <= 0.40:
            s += 1.0
        vr = bc['vr']
        if 1.5 <= vr <= 3.0:
            s += 6.0
        elif 1.3 <= vr < 1.5:
            s += 4.0
        elif 3.0 < vr <= 5.0:
            s += 3.0
        elif vr > 5.0:
            s += 1.0
        else:
            s += 0.0
        return round(min(s, 20.0), 1)

    def _score_ma5(self, r: BTSResult) -> float:
        """MA5 趋势质量 20 分"""
        s = 0.0
        if r.ma5_up_streak >= 3:
            s += 6.0
        elif r.ma5_up_streak == 2:
            s += 4.0
        if r.ma5_accel:
            s += 4.0
        if r.close > r.ma5:
            s += 3.0
        if r.ma5 > r.ma10:
            s += 3.0
        if r.ma10_slope_1 > 0:
            s += 2.0
        if r.close > r.ma10:
            s += 2.0
        return round(min(s, 20.0), 1)

    def _score_vol(self, r: BTSResult) -> float:
        """量能持续性 20 分：连续性(8) + V1(4) + V2(4) + 无爆量(4)"""
        s = 0.0
        # 连续性 8 分（V1.4）：突破日至今 vol>vol_ma20 的比例，不惩罚突破前缩量整理
        if r.vol_persist_post_n > 0:
            s += 8.0 * r.vol_persist_post / r.vol_persist_post_n
        if r.vol_ratio >= 1.2:
            s += 4.0
        elif r.vol_ratio >= 1.0:
            s += 2.0
        if r.v2 >= 1.1:
            s += 4.0
        elif r.v2 >= 1.0:
            s += 2.0
        if not r.spike_volume:
            s += 4.0
        return round(min(s, 20.0), 1)

    def _score_vol_price(self, df: pd.DataFrame, end_idx: int, r: BTSResult) -> float:
        """量价配合 10 分：上涨放量/回调缩量"""
        s = 0.0
        udr = r.up_down_ratio
        if udr >= 1.5:
            s += 7.0
        elif udr >= 1.2:
            s += 5.0
        elif udr >= 1.0:
            s += 3.0
        seg = df.iloc[max(0, end_idx - 4):end_idx + 1]
        up_days = int((seg['close'] > seg['open']).sum())
        if up_days >= 3:
            s += 3.0
        elif up_days == 2:
            s += 1.5
        return round(min(s, 10.0), 1)

    def _score_pullback(self, df: pd.DataFrame, b: int, res: dict, end_idx: int, r: BTSResult) -> float:
        """回踩质量 10 分"""
        cfg = self.cfg
        if b >= end_idx:
            return 5.0
        seg = df.iloc[b + 1:end_idx + 1]
        pull_low = float(seg['low'].min())
        b_high = float(df['high'].iloc[b])
        depth = (b_high - pull_low) / b_high if b_high > 0 else 0.0
        s = 0.0
        if depth < 0.05:
            s += 5.0
        elif depth < 0.08:
            s += 3.0
        elif depth < 0.12:
            s += 1.0
        vol_b = float(df['vol'].iloc[b])
        post_vol = float(seg['vol'].mean())
        # 回踩缩量 3 分（V1.4）：Day1 无实质回踩（深度<5%）时量能保持即为优，不再要求缩量
        if end_idx - b == 1 and depth < 0.05:
            s += 3.0
        elif vol_b > 0 and post_vol <= vol_b * 0.8:
            s += 3.0
        if r.post_breakout_low >= res['price'] * cfg['post_breakout_floor']:
            s += 2.0
        return round(min(s, 10.0), 1)

    def _score_ext(self, r: BTSResult) -> float:
        """乖离率因子 5 分（V1.3：数据验证 6-10% 是甜区，贴线<5% 反而平庸）
        回测：乖离0-2%→20日+0.84 / 2-4%→+1.17 / 4-6%→+1.59 / 6-8%→+2.80 / 8-10%→+2.83（甜区）
        Day1池内 8-10%→+6.03% 最强；10-12%→+0.99、12-15%→+0.94 衰减"""
        d = r.distance_ma5
        if d <= 0.05:
            s = 3.0      # 贴线：20日均 +0.8~1.2%，动能一般
        elif d <= 0.06:
            s = 4.0
        elif d <= 0.10:
            s = 5.0      # 甜区 6-10%：20日均 +2.8%，Day1 内最强
        elif d <= 0.12:
            s = 2.0
        else:
            s = 0.0
        return round(min(s, 5.0), 1)

    def _score_mv(self, cap: float) -> float:
        """市值因子（V1.3）：小市值占优，回测实证分档加分"""
        if cap <= 0:
            return 0.0
        cfg = self.cfg
        for upper, score in cfg['mv_edges']:
            if cap < upper:
                return round(min(score, cfg['mv_max']), 1)
        return round(cfg['mv_edges'][-1][1], 1)

    # ── 回踩买点判定（BUY-B）──
    def _is_pullback_buy(self, df: pd.DataFrame, b: int, res: dict, end_idx: int, r: BTSResult) -> bool:
        cfg = self.cfg
        d = end_idx - b
        if not (1 <= d <= 5):
            return False
        # 近 min(d,3) 日出现过贴近MA5的回踩
        look = min(d, 3)
        ma5s = df['ma5'].values
        lows = df['low'].values
        vols = df['vol'].values
        vma5s = df['vol_ma5'].values
        touched = False
        shrink = False
        for i in range(end_idx - look + 1, end_idx + 1):
            if not np.isnan(ma5s[i]) and lows[i] <= ma5s[i] * 1.02:
                touched = True
            if not np.isnan(vma5s[i]) and vma5s[i] > 0 and vols[i] <= vma5s[i] * 1.2:
                shrink = True
        if not (touched and shrink):
            return False
        # 当日重新站回MA5
        if not (r.close > r.ma5):
            return False
        # 平台上沿未破
        if not (r.post_breakout_low >= res['price'] * cfg['post_breakout_floor']):
            return False
        return True

    # ── 单股评分入口 ──
    def score(self, df: pd.DataFrame, ts_code: str = '', name: str = '',
              industry: str = '', market_regime: str = 'neutral',
              market_cap: float = 0.0) -> BTSResult:
        """df 必须已截断到 T 日（T 日为最后一根K线）。
        market_cap: 总市值（亿元），V1.3 市值因子；传 0 不加分。"""
        cfg = self.cfg
        r = BTSResult(ts_code=ts_code, name=name, industry=industry, market_regime=market_regime)
        r.market_cap = market_cap
        n = len(df)
        r.n_bars = n
        if n < cfg['min_bars']:
            r.grade, r.signal = 'NO_BUY', 'NO_SIGNAL'
            r.core_reason = 'K线不足'
            return r
        end_idx = n - 1
        r.date = str(df['trade_date'].iloc[end_idx])

        df = add_ma(df, (5, 10, 20, 60))
        df = add_vol_ma(df, (5, 10, 20))
        df = add_rsi(df)

        close = float(df['close'].iloc[end_idx])
        r.close = close
        r.ma5 = _safe(lambda: float(df['ma5'].iloc[end_idx]))
        r.ma10 = _safe(lambda: float(df['ma10'].iloc[end_idx]))
        r.ma20 = _safe(lambda: float(df['ma20'].iloc[end_idx]))
        r.ma60 = _safe(lambda: float(df['ma60'].iloc[end_idx]))
        r.distance_ma5 = (close / r.ma5 - 1.0) if r.ma5 > 0 else 0.0
        r.distance_ma20 = (close / r.ma20 - 1.0) if r.ma20 > 0 else 0.0
        r.ma5_slope_1 = _safe(lambda: ma_slope(df['ma5'], end_idx, 1))
        r.ma5_slope_3 = _safe(lambda: ma_slope(df['ma5'], end_idx, 3))
        r.ma10_slope_1 = _safe(lambda: ma_slope(df['ma10'], end_idx, 1))
        r.ma5_up_streak = ma5_up_streak(df['ma5'], end_idx)
        # V1.4：加速度用 3日斜率 vs 前三日3日斜率（突破日大阳会让 slope_1<prev3 恒 False，误杀 Day1）
        prev3 = _safe(lambda: ma_slope(df['ma5'], end_idx - 3, 3))
        r.ma5_accel = (r.ma5_slope_3 > prev3) and (r.ma5_slope_3 > 0)

        r.vol = float(df['vol'].iloc[end_idx])
        r.vol_ma5 = _safe(lambda: float(df['vol_ma5'].iloc[end_idx]))
        r.vol_ma10 = _safe(lambda: float(df['vol_ma10'].iloc[end_idx]))
        r.vol_ma20 = _safe(lambda: float(df['vol_ma20'].iloc[end_idx]))
        r.vol_ratio = (r.vol / r.vol_ma20) if r.vol_ma20 > 0 else 0.0
        r.v2 = (r.vol_ma5 / r.vol_ma20) if r.vol_ma20 > 0 else 0.0
        p = 0
        for i in range(max(0, end_idx - 4), end_idx + 1):
            vm = df['vol_ma20'].iloc[i]
            if not np.isnan(vm) and vm > 0 and df['vol'].iloc[i] > vm:
                p += 1
        r.volume_persistence = p
        spike = False
        for i in range(max(1, end_idx - 4), end_idx + 1):
            vm = df['vol_ma20'].iloc[i]
            if not np.isnan(vm) and vm > 0:
                if df['vol'].iloc[i] > cfg['burst_volume_ratio'] * vm and df['vol'].iloc[i - 1] < 0.6 * df['vol'].iloc[i]:
                    spike = True
                    break
        r.spike_volume = spike
        seg5 = df.iloc[max(0, end_idx - 4):end_idx + 1]
        up_v = seg5.loc[seg5['close'] > seg5['open'], 'vol']
        dn_v = seg5.loc[seg5['close'] < seg5['open'], 'vol']
        r.up_volume_ratio = float(up_v.mean()) if len(up_v) else 0.0
        r.down_volume_ratio = float(dn_v.mean()) if len(dn_v) else 0.0
        if r.down_volume_ratio > 0:
            r.up_down_ratio = r.up_volume_ratio / r.down_volume_ratio
        elif len(dn_v) == 0 and r.up_volume_ratio > 0:
            # 最近5日无下跌日：视为强势推动（无出货放量）
            r.up_down_ratio = 3.0
        else:
            r.up_down_ratio = 0.0
        r.rsi = _safe(lambda: float(df['rsi'].iloc[end_idx]))

        # V1.6：信号日高点是否站上过去 N 日新高（过滤未突破前高的弱突破）
        nhw = max(1, cfg['new_high_window'])
        lo = end_idx - nhw
        if lo < 0:
            lo = 0
        prev_highs = df['high'].iloc[lo:end_idx].values
        r.high_120d_new = bool(len(prev_highs) > 0 and float(df['high'].iloc[end_idx]) >= float(prev_highs.max()))
        r.high_120d_prev = float(prev_highs.max()) if len(prev_highs) > 0 else 0.0

        # ── 突破识别 ──
        bf, failed_flag, failed_msg = self._find_breakout(df, end_idx)
        if bf is None:
            r.grade, r.signal = 'NO_BUY', 'NO_SIGNAL'
            r.core_reason = '未发现有效平台突破'
            r.checks['平台突破'] = {'ok': False, 'detail': '无突破日'}
            return r
        b = bf['b']
        s, e, bstats = bf['base']
        res = bf['res']
        bc = bf['bc']
        post = bf['post']
        r.breakout_date = str(df['trade_date'].iloc[b])
        r.days_after_breakout = end_idx - b
        r.base_start = str(df['trade_date'].iloc[s])
        r.base_end = str(df['trade_date'].iloc[e - 1]) if e - 1 >= s else r.base_start
        r.base_days = bstats['days']
        r.base_high = bstats['high']
        r.base_low = bstats['low']
        r.base_range = bstats['range']
        r.base_slope = bstats['slope']
        r.resistance = res['price']
        r.resistance_width = res['width']
        r.resistance_touches = res['touches']
        r.breakout_amp = bc['amp']
        r.candle_pos = bc['pos']
        r.upper_shadow_ratio = bc['usr']
        r.vol_ratio_breakout = bc['vr']
        r.distance_breakout = (close / bc['close'] - 1.0) if bc['close'] > 0 else 0.0
        r.post_breakout_low = post['post_low']
        r.post_breakout_failed = not post['ok']
        # V1.4：突破日至今量能持续（评分用，不惩罚突破前缩量整理日）
        p_post = 0
        for i in range(b, end_idx + 1):
            vm = df['vol_ma20'].iloc[i]
            if not np.isnan(vm) and vm > 0 and df['vol'].iloc[i] > vm:
                p_post += 1
        r.vol_persist_post = p_post
        r.vol_persist_post_n = end_idx - b + 1
        # 回踩深度（突破后至今）
        if b < end_idx:
            seg_post = df.iloc[b + 1:end_idx + 1]
            r.pullback_low = float(seg_post['low'].min())
            r.pullback_depth = (float(df['high'].iloc[b]) - r.pullback_low) / float(df['high'].iloc[b]) \
                if float(df['high'].iloc[b]) > 0 else 0.0
        # 趋势启动效率 = 突破后累计涨幅/交易日数
        days_after = r.days_after_breakout
        if days_after > 0:
            r.trend_eff = r.distance_breakout / days_after
        # MA5轨道稳定性
        track = 0
        for i in range(max(0, end_idx - 4), end_idx + 1):
            m5 = df['ma5'].iloc[i]
            if not np.isnan(m5) and df['close'].iloc[i] > m5:
                track += 1
        r.ma5_track = track
        close_dists = [abs(df['close'].iloc[i] / df['ma5'].iloc[i] - 1.0)
                       for i in range(max(0, end_idx - 4), end_idx + 1)
                       if not np.isnan(df['ma5'].iloc[i]) and df['ma5'].iloc[i] > 0]
        r.ma5_track_close = float(np.mean(close_dists)) if close_dists else 0.0

        # ── 子项分 ──
        r.score_base = self._score_base(bstats, res, r)
        r.score_breakout = self._score_breakout(bc, r)
        r.score_ma5 = self._score_ma5(r)
        r.score_vol = self._score_vol(r)
        r.score_vol_price = self._score_vol_price(df, end_idx, r)
        r.score_pullback = self._score_pullback(df, b, res, end_idx, r)
        r.score_ext = self._score_ext(r)
        r.bts_score = round(min(100.0,
                                r.score_base + r.score_breakout + r.score_ma5 + r.score_vol
                                + r.score_vol_price + r.score_pullback + r.score_ext), 1)

        # ── 门槛（GATE）──
        r.gate_breakout = not failed_flag and post['ok']
        r.gate_ma5 = (r.ma5_up_streak >= 2) and (r.close > r.ma5) and (r.ma5_slope_3 > 0)
        # 量能门槛：持续3/5 + 量能中枢抬升；当日缩量回踩（V1<1.2）时若4/5天放量也算通过
        r.gate_vol = (r.volume_persistence >= cfg['min_volume_persistence']) and (r.v2 >= 1.1) \
            and (r.vol_ratio >= 1.2 or r.volume_persistence >= 4)
        # V1.6：信号日高点需站上 120 日新高（真正突破前高阻力位，过滤平台内小突破）
        r.gate_new_high = r.high_120d_new

        # ── 信号分类 ──
        if failed_flag:
            r.signal = 'FAILED_BREAKOUT'
            r.signal_cn = SIGNAL_CN['FAILED_BREAKOUT']
        elif r.distance_ma5 > cfg['no_buy_ma5_distance']:
            r.signal = 'TREND_EXTENDED'
            r.signal_cn = SIGNAL_CN['TREND_EXTENDED']
        elif self._is_pullback_buy(df, b, res, end_idx, r):
            r.signal = 'PULLBACK_BUY'
            r.signal_cn = SIGNAL_CN['PULLBACK_BUY']
        elif r.days_after_breakout <= 2:
            r.signal = 'BREAKOUT_NOW'
            r.signal_cn = SIGNAL_CN['BREAKOUT_NOW']
        else:
            r.signal = 'TREND_START'
            r.signal_cn = SIGNAL_CN['TREND_START']

        # ── 等级 ──
        gates_ok = r.gate_breakout and r.gate_ma5 and r.gate_vol and r.gate_new_high
        score = r.bts_score
        if r.signal in ('FAILED_BREAKOUT',):
            r.grade = 'NO_BUY'
        elif r.signal == 'TREND_EXTENDED':
            r.grade = 'NO_BUY'
        else:
            if gates_ok:
                if score >= cfg['grade_s']:
                    r.grade = 'S'
                elif score >= cfg['grade_a']:
                    r.grade = 'A'
                elif score >= cfg['grade_b']:
                    r.grade = 'B'
                elif score >= cfg['grade_c']:
                    r.grade = 'C'
                elif score >= 50:
                    r.grade = 'WATCH'
                else:
                    r.grade = 'NO_BUY'
            else:
                # 门槛未全过：封顶 C
                if score >= cfg['grade_c']:
                    r.grade = 'C'
                elif score >= 50:
                    r.grade = 'WATCH'
                else:
                    r.grade = 'NO_BUY'

        # 熊市限制：非极强信号只进 WATCH
        if market_regime == 'bear' and r.grade in ('S', 'A', 'B') and r.bts_score < 85:
            r.grade = 'WATCH'

        # ── 买点评分 ──
        ext_pen = 0.0
        # V1.1：距MA5惩罚起点右移 8%→10%，10~15% 为重罚区
        if r.distance_ma5 > 0.10:
            ext_pen = min(25.0, (r.distance_ma5 - 0.10) * 200)
        fake_pen = 18.0 if failed_flag else 0.0
        over_vol = 8.0 if r.spike_volume else 0.0
        # V1.1：突破后第1日确认加分（Day1 且未跌回平台/量能不衰）
        day1_ok = (r.days_after_breakout == 1 and not failed_flag
                   and not r.spike_volume and r.vol_ratio >= 1.0)
        r.day1_premium = cfg['day1_premium'] if day1_ok else 0.0

        # V1.7：持续确认买点——突破后稳步向上+量能充沛（非 Day1 也可进买入池）
        sb = cfg['sustained_buy']
        if sb['enabled'] and sb['min_days_after'] <= r.days_after_breakout <= min(sb['max_days_after'], cfg['post_breakout_days']):
            # 未跌回平台 + 站上MA5 + 稳步向上(突破后日均涨幅>=0) + 放量天数比例>=阈值
            # + 当日量能不衰 + 量能中枢抬升 + 距MA5 未追高
            r.sustained_ok = bool(
                not failed_flag
                and r.close > r.ma5
                and r.trend_eff >= sb['min_trend_eff']
                and r.vol_persist_post_n > 0
                and (r.vol_persist_post / r.vol_persist_post_n) >= sb['min_vol_persist_ratio']
                and r.vol_ratio >= sb['min_vol_ratio']
                and r.v2 >= sb['min_v2']
                and r.distance_ma5 <= sb['max_dist_ma5']
            )
        else:
            r.sustained_ok = False
        r.ext_penalty, r.fake_penalty, r.over_vol_penalty = ext_pen, fake_pen, over_vol
        # V1.3：市值因子（小市值占优，回测实证）
        r.score_mv = self._score_mv(r.market_cap)
        # V1.4：未压缩附加分（供 scanner 层对高分股统一压缩，避免 Entry 饱和失真）
        r.extra_raw = r.day1_premium + r.score_mv
        # V1.1：行业共振加分由 scanner 层汇总后叠加（单股引擎无法看到全市场）
        r.entry_score = round(max(0.0, min(100.0,
                                r.bts_score - ext_pen - fake_pen - over_vol
                                + r.day1_premium + r.sector_heat + r.score_mv)), 1)

        # ── 买点类型 ──
        if r.signal == 'PULLBACK_BUY' and r.gate_breakout:
            r.buy_point = 'BUY-B'
        elif r.signal == 'BREAKOUT_NOW' and gates_ok:
            r.buy_point = 'BUY-A'
        elif r.signal == 'TREND_START' and gates_ok:
            r.buy_point = 'BUY-C'
        elif r.sustained_ok and r.grade in ('S', 'A', 'B'):
            # V1.7：突破后稳步向上+量能充沛的持续确认买点
            r.buy_point = sb['buy_point']
        elif r.grade in ('S', 'A', 'B'):
            r.buy_point = 'BUY-C'
        elif r.grade == 'C' or r.grade == 'WATCH':
            r.buy_point = 'WATCH'
        else:
            r.buy_point = 'NO_BUY'

        # ── 核心原因 / 风险 / 操作 ──
        self._compose_text(r)
        self._build_checks(r, df, end_idx)
        return r

    # ── 文本 ──
    def _compose_text(self, r: BTSResult):
        base_days = f"平台{r.base_days}日"
        amp = r.breakout_amp * 100
        parts = [f"{r.breakout_date}突破平台(幅度+{amp:.1f}%)",
                 f"量比{r.vol_ratio_breakout:.2f}", base_days,
                 f"MA5 {r.ma5_up_streak}连升", f"距MA5 {r.distance_ma5 * 100:+.1f}%",
                 f"量能持续{r.volume_persistence}/5"]
        if r.days_after_breakout >= 0:
            parts.append(f"突破后{r.days_after_breakout}日")
        if r.day1_premium > 0:
            parts.append(f"Day1确认+{r.day1_premium:.0f}")
        if r.sector_heat > 0:
            parts.append(f"行业共振+{r.sector_heat:.0f}")
        if r.score_mv > 0:
            parts.append(f"市值{r.market_cap:.0f}亿+{r.score_mv:.0f}")
        r.core_reason = "；".join(parts)
        risk = []
        if r.distance_ma5 > 0.12:
            risk.append(f"距MA5达{r.distance_ma5 * 100:.1f}%，扩张过度")
        if r.spike_volume:
            risk.append("存在单日爆量陷阱风险")
        if failed_flag_ref(r):
            risk.append("突破后有收盘跌回平台风险")
        if r.rsi > 75:
            risk.append(f"RSI={r.rsi:.0f} 极端超买")
        if r.ma5_track < 3:
            risk.append("MA5承接偏弱")
        r.risk_factors = risk
        if r.grade in ('S', 'A', 'B'):
            r.action = "可纳入买入池，按信号类型分仓参与"
        elif r.grade == 'C':
            r.action = "列入观察，等待回踩缩量或趋势确认"
        elif r.signal == 'TREND_EXTENDED':
            r.action = "禁止追高，等待回踩MA5后的低吸机会"
        elif r.signal == 'FAILED_BREAKOUT':
            r.action = "突破失败，观望"
        else:
            r.action = "暂不参与"

    def _build_checks(self, r: BTSResult, df: pd.DataFrame, end_idx: int):
        c = {}
        c['平台突破'] = {'ok': True, 'detail': f"平台{r.base_days}日 振幅{r.base_range * 100:.1f}%"}
        c['突破幅度'] = {'ok': r.breakout_amp >= self.cfg['breakout_threshold'],
                        'detail': f"{r.breakout_amp * 100:+.1f}%"}
        c['突破成交量'] = {'ok': r.vol_ratio_breakout >= self.cfg['breakout_volume_ratio'],
                         'detail': f"{r.vol_ratio_breakout:.2f}x"}
        c['突破K线质量'] = {'ok': r.candle_pos >= self.cfg['candle_min_pos'],
                          'detail': f"收盘位{r.candle_pos * 100:.0f}% 上影{r.upper_shadow_ratio * 100:.0f}%"}
        c['MA5上拐'] = {'ok': r.ma5_slope_1 > 0, 'detail': f"{r.ma5_slope_1 * 100:+.2f}%"}
        c['MA5连续向上'] = {'ok': r.ma5_up_streak >= 2, 'detail': f"{r.ma5_up_streak}日"}
        c['股价站MA5'] = {'ok': r.close > r.ma5, 'detail': f"{r.distance_ma5 * 100:+.1f}%"}
        c['量能持续'] = {'ok': r.gate_vol, 'detail': f"持续{r.volume_persistence}/5 V1={r.vol_ratio:.2f} V2={r.v2:.2f}"}
        c['上涨放量'] = {'ok': r.up_down_ratio >= 1.2, 'detail': f"涨/跌量比{r.up_down_ratio:.2f}"}
        c['回调缩量'] = {'ok': r.up_down_ratio >= 1.0 and r.down_volume_ratio <= r.vol_ma20,
                        'detail': f"跌日量比{r.down_volume_ratio / r.vol_ma20 if r.vol_ma20 > 0 else 0:.2f}"}
        c['距离MA5合理'] = {'ok': r.distance_ma5 <= self.cfg['max_ma5_distance'],
                          'detail': f"{r.distance_ma5 * 100:+.1f}%"}
        c['Day1确认'] = {'ok': r.day1_premium > 0,
                         'detail': f"{'突破后第1日+量能不衰 +'+str(r.day1_premium) if r.day1_premium > 0 else '非Day1/量能衰减'}"}
        c['持续确认'] = {'ok': r.sustained_ok,
                        'detail': f"{'突破后稳步向上+量能充沛(可进池)' if r.sustained_ok else '非持续确认(不满足稳步向上/量能充沛)'}"}
        c['行业共振'] = {'ok': r.sector_heat > 0,
                        'detail': f"{'+'+str(r.sector_heat) if r.sector_heat > 0 else '行业信号<3'}"}
        c['突破前高'] = {'ok': r.gate_new_high,
                        'detail': f"{'信号日高点创'+str(self.cfg['new_high_window'])+'日新高' if r.gate_new_high else '未突破前高(前'+str(self.cfg['new_high_window'])+'日高点'+str(round(r.high_120d_prev,2))+')'}"}
        if r.post_breakout_failed:
            c['假突破风险'] = {'ok': False, 'detail': 'HIGH：突破后收盘跌回平台'}
        elif r.spike_volume:
            c['假突破风险'] = {'ok': False, 'detail': 'MEDIUM：爆量陷阱'}
        else:
            c['假突破风险'] = {'ok': True, 'detail': 'LOW'}
        r.checks = c


def failed_flag_ref(r: BTSResult) -> bool:
    return r.post_breakout_failed
