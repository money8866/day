# -*- coding: utf-8 -*-
"""
PRB（Platform-Reacceleration Breakout）核心引擎 V1.0
================================================
完整交易路径（状态机，第十六节）：
  PLATFORM_BUILDING -> PLATFORM_CONFIRMED -> NEAR_BREAKOUT -> BREAKOUT_PENDING
  -> BREAKOUT_CONFIRMED -> FIRST_PULLBACK -> PULLBACK_SUPPORT -> RE_ACCELERATION
  -> PRIMARY_BUY -> HOLD -> EXIT
任何阶段结构破坏 -> INVALIDATED / BREAKOUT_FAILED / PULLBACK_FAILED

四段评分（第十五节 100 分模型）：
  PLATFORM_SCORE 100 制（时间10+收敛15+高测15+承接10+量缩15+MA20结构10+MA60结构5+平衡10+ATR压缩10）
  BREAKOUT_SCORE 100 制（幅度15+量能20+收盘位15+K线质量15+持续性20+平台10+共振5）
  PULLBACK_SCORE 100 制（深度6+时间4+缩量7+关键位5+K线3 -> 按 25 分权重折算）
  REACCEL 影响 20 分

防未来数据：本引擎只接收"已截断到 T 日"的 DataFrame（T 日为最后一根K线），
全部计算只用 <=T 的数据。
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import PRB_CONFIG, STATE_CN, ACTION_CN


# ═════════════════════════════════════════════
# 结果数据结构
# ═════════════════════════════════════════════
@dataclass
class PRBResult:
    ts_code: str = ''
    name: str = ''
    industry: str = ''
    date: str = ''

    # ── 状态机 ──
    state: str = 'PLATFORM_BUILDING'
    state_cn: str = '平台构建中'
    action: str = 'NO_TRADE'
    action_cn: str = '不交易'
    action_reason: str = ''

    # ── 阶段一：平台 ──
    platform_start: str = ''
    platform_end: str = ''
    platform_days: int = 0
    platform_high: float = 0.0
    platform_low: float = 0.0
    platform_range: float = 0.0           # (H-L)/L
    platform_range_atr: float = 0.0       # (H-L)/ATR20
    resistance_tests: int = 0             # 上沿测试次数
    support_tests: int = 0                # 下沿承接次数
    vol_shrink_ratio: float = 1.0         # 后半段量/前半段量
    convergence_ratio: float = 1.0        # 后半段ATR/前半段ATR
    platform_score: float = 0.0
    platform_grade: str = ''              # A+/A/B/INVALID

    # ── 阶段二：突破 ──
    breakout_date: str = ''
    breakout_level: float = 0.0           # BreakoutLevel = PlatformHigh
    breakout_price: float = 0.0           # 突破日收盘
    breakout_pct: float = 0.0             # (Close-BL)/BL
    breakout_vr: float = 0.0              # VolumeRatio = Vol/MA20Vol
    breakout_candle_pos: float = 0.0      # (Close-Low)/(High-Low)
    breakout_upper_shadow: float = 0.0
    breakout_score: float = 0.0
    breakout_grade: str = ''              # 强/有效/弱/无效
    post_breakout_days: int = 0           # 突破至今交易日
    post_keep_ratio: float = 0.0          # 观察窗内收盘维持 BL 之上比例
    breakout_failed: bool = False
    breakout_failed_reason: str = ''

    # ── 阶段三：回踩 ──
    pullback_start: str = ''
    pullback_low: float = 0.0
    pullback_low_date: str = ''
    pullback_depth: float = 0.0           # (BH-PL)/(BH-BL) 斐波那契口径
    pullback_days: int = 0
    pullback_vol_ratio: float = 0.0       # 回踩量/突破量
    pullback_below_bl: bool = False       # 是否收盘跌破突破位
    pullback_below_days: int = 0
    pullback_end_evidence: List[str] = field(default_factory=list)  # 满足哪些止跌证据
    pullback_end_ok: bool = False
    pullback_score: float = 0.0
    pullback_failed: bool = False
    pullback_failed_reason: str = ''

    # ── 阶段四：再启动 ──
    reaccel_date: str = ''
    reaccel_price: float = 0.0
    reaccel_vol_ratio: float = 0.0        # TurnStrengthVolume
    reaccel_candle_pos: float = 0.0
    reaccel_close_above_prev_high: bool = False
    reaccel_ma5_above_ma10: bool = False
    reaccel_break_pullback_high: bool = False
    reaccel_ok: bool = False

    # ── 最终 ──
    final_score: float = 0.0
    grade: str = ''                        # S/STRONG/A/WAIT/NO_TRADE
    grade_cn: str = ''

    # ── T 日行情 ──
    close: float = 0.0
    pct_chg: float = 0.0
    atr20: float = 0.0
    ma5: float = 0.0
    ma10: float = 0.0
    ma20: float = 0.0
    ma60: float = 0.0
    vol_ratio_today: float = 0.0
    dist_breakout_level: float = 0.0      # 当前价距 BL 的 ATR 倍数
    dist_ma5: float = 0.0

    # ── 严禁买入检查（第十二节）──
    forbidden_reasons: List[str] = field(default_factory=list)
    # 警示（不拦单，仅提示）：突破日爆涨 7~10% 警惕等
    warnings: List[str] = field(default_factory=list)

    # ── 市场过滤 ──
    market_regime: str = 'neutral'
    market_allowed: bool = True
    market_level: int = 2               # 3=S/A/B, 2=S/A, 1=仅S且极强, 0=关闭
    market_strong_gate: tuple = (0.0, 0.0)  # weak/bear 极强门槛(平台, 突破)

    # ── 行业共振 ──
    theme_strength: float = 0.0
    theme_heat: float = 0.0               # scanner 层回填

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


# ═════════════════════════════════════════════
# 引擎
# ═════════════════════════════════════════════
class PRBEngine:
    def __init__(self, config: Optional[dict] = None):
        self.cfg = dict(PRB_CONFIG)
        if config:
            self.cfg.update(config)

    # ── 指标预计算 ──
    @staticmethod
    def _prep(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df['ma5'] = df['close'].rolling(5).mean()
        df['ma10'] = df['close'].rolling(10).mean()
        df['ma20'] = df['close'].rolling(20).mean()
        df['ma60'] = df['close'].rolling(60).mean()
        df['vol_ma20'] = df['vol'].rolling(20).mean()
        prev_close = df['close'].shift(1)
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - prev_close).abs(),
            (df['low'] - prev_close).abs(),
        ], axis=1).max(axis=1)
        df['tr'] = tr
        df['atr20'] = tr.rolling(20).mean()
        df['amplitude'] = (df['high'] - df['low']) / df['close']
        return df

    # ═══ 阶段一：平台识别 ═══
    def find_platforms(self, df: pd.DataFrame, b: int) -> List[dict]:
        """在突破日 b 之前找全部合格平台（score >= gate）。按分数降序返回。

        返回多个候选的原因：最高分平台的 high 可能含前期尖峰（如博济 7/16 冲高），
        导致实际突破日无法满足收盘>BL；需让突破条件逐平台验证，选"实际被突破的最高分平台"。
        """
        cfg = self.cfg
        highs = df['high'].values.astype(float)
        lows = df['low'].values.astype(float)
        closes = df['close'].values.astype(float)
        vols = df['vol'].values.astype(float)
        atrs = df['atr20'].values
        out = []
        for w in range(cfg['platform_min_days'], cfg['platform_max_days'] + 1, 2):
            s = b - w
            if s < 30:
                continue
            seg_h = highs[s:b]
            seg_l = lows[s:b]
            hi = float(seg_h.max())
            lo = float(seg_l.min())
            if hi <= 0 or lo <= 0:
                continue
            rng = (hi - lo) / lo
            if rng > cfg['platform_range_max']:
                continue
            atr_b = atrs[b - 1] if not np.isnan(atrs[b - 1]) else (hi - lo) / 4.0
            if atr_b <= 0:
                continue
            rng_atr = (hi - lo) / atr_b
            if rng_atr > cfg['platform_range_atr_max']:
                continue
            # 高点测试（容差 = resistance_atol_atr × ATR）
            tol = cfg['resistance_atol_atr'] * atr_b
            tests_hi = int(np.sum(np.abs(seg_h - hi) <= tol))
            # 低点承接
            tol_l = cfg['support_atol_atr'] * atr_b
            tests_lo = int(np.sum(np.abs(seg_l - lo) <= tol_l))
            # 量能收缩：后半段/前半段
            half = w // 2
            v1 = float(vols[s:s + half].mean()) if half > 0 else 0.0
            v2 = float(vols[s + half:b].mean()) if half > 0 else 0.0
            vol_shrink = v2 / v1 if v1 > 0 else 1.0
            # 波动收敛：后半段ATR/前半段ATR
            a1 = _safe(lambda: float(np.nanmean(atrs[s:s + half])))
            a2 = _safe(lambda: float(np.nanmean(atrs[s + half:b])))
            conv = a2 / a1 if a1 > 0 else 1.0
            seg_c = closes[s:b]
            slope = seg_c[-1] / seg_c[0] - 1.0
            # 均线结构
            ma20_slope = _safe(lambda: float(df['ma20'].iloc[b - 1] / df['ma20'].iloc[b - 11] - 1.0)) \
                if b - 11 >= 0 else 0.0
            ma60_slope = _safe(lambda: float(df['ma60'].iloc[b - 1] / df['ma60'].iloc[b - 21] - 1.0)) \
                if b - 21 >= 0 else 0.0
            stats = {
                'start': s, 'end': b, 'days': w, 'high': hi, 'low': lo,
                'range': rng, 'range_atr': rng_atr, 'slope': slope,
                'tests_hi': tests_hi, 'tests_lo': tests_lo,
                'vol_shrink': vol_shrink, 'conv': conv,
                'ma20_slope': ma20_slope, 'ma60_slope': ma60_slope,
                'atr': atr_b, 'ma20_dev': float(np.nanmean(
                    np.abs(seg_c / df['ma20'].values[s:b] - 1.0))),
            }
            sc = self._score_platform(stats)
            if sc >= cfg['platform_score_gate']:
                stats['score'] = sc
                stats['grade'] = ('A+' if sc >= cfg['platform_score_a_plus']
                                  else 'A' if sc >= cfg['platform_score_a']
                                  else 'B')
                out.append(stats)
        out.sort(key=lambda x: -x['score'])
        return out

    def _score_platform(self, st: dict) -> float:
        """PLATFORM_SCORE 100分制（第三节权重）"""
        cfg = self.cfg
        W = cfg['platform_weights']
        s = 0.0
        # 时间 10
        d = st['days']
        lo_i, hi_i = cfg['platform_ideal_days']
        if lo_i <= d <= hi_i:
            s += W['duration']
        elif d >= cfg['platform_min_days']:
            s += W['duration'] * 0.7
        if d > cfg['platform_very_long']:
            s -= 2.0
        # 收敛 15：后半段/前半段 ATR 比
        cr = cfg['convergence_ratios']
        conv_score = 0.0
        if st['conv'] <= 0.70:
            conv_score = 1.0
        elif st['conv'] <= cr['atr']:
            conv_score = 0.8
        elif st['conv'] <= 1.0:
            conv_score = 0.5
        s += W['convergence'] * conv_score
        # 高点重复测试 15
        if st['tests_hi'] >= cfg['resistance_good_tests']:
            s += W['resistance_tests']
        elif st['tests_hi'] >= cfg['resistance_min_tests']:
            s += W['resistance_tests'] * 0.7
        # 下沿承接 10
        if st['tests_lo'] >= 2:
            s += W['support_tests']
        elif st['tests_lo'] == 1:
            s += W['support_tests'] * 0.4
        # 量缩 15
        vs = st['vol_shrink']
        if vs <= 0.70:
            s += W['volume_shrink']
        elif vs <= cfg['platform_vol_shrink']:
            s += W['volume_shrink'] * 0.8
        elif vs <= cfg['platform_vol_hard']:
            s += W['volume_shrink'] * 0.4
        # MA20 结构 10
        if st['ma20_slope'] >= 0:
            s += W['ma20_structure']
        elif st['ma20_slope'] >= cfg['platform_ma20_slope_min']:
            s += W['ma20_structure'] * 0.6
        # MA60 结构 5
        if st['ma60_slope'] >= 0:
            s += W['ma60_structure']
        elif st['ma60_slope'] >= cfg['platform_ma60_slope_min']:
            s += W['ma60_structure'] * 0.6
        # 涨跌平衡 10：平台斜率绝对值小
        if abs(st['slope']) <= 0.03:
            s += W['balance']
        elif abs(st['slope']) <= 0.08:
            s += W['balance'] * 0.6
        # ATR 压缩 10：range_atr 越小越紧
        if st['range_atr'] <= 4.0:
            s += W['atr_compression']
        elif st['range_atr'] <= 6.0:
            s += W['atr_compression'] * 0.7
        return round(min(s, 100.0), 1)

    # ═══ 阶段二：突破识别 ═══
    def _breakout_conditions(self, df: pd.DataFrame, b: int, plat: dict) -> dict:
        """突破日 b 的价格/量能/K线条件（第四节）"""
        cfg = self.cfg
        row = df.iloc[b]
        close_b = float(row['close'])
        high_b = float(row['high'])
        low_b = float(row['low'])
        open_b = float(row['open'])
        vol_b = float(row['vol'])
        bl = plat['high']
        atr = plat['atr']
        amp = close_b / bl - 1.0
        vr = vol_b / float(row['vol_ma20']) if float(row['vol_ma20']) > 0 else 0.0
        rng = high_b - low_b
        pos = (close_b - low_b) / rng if rng > 0 else 0.5
        upper = high_b - max(open_b, close_b)
        usr = upper / rng if rng > 0 else 0.0
        ok_price = close_b > bl + cfg['breakout_atr_buffer'] * atr and amp >= cfg['breakout_pct_min']
        ok_vol = vr >= cfg['breakout_vr_min']
        ok_pos = pos >= cfg['candle_pos_min']
        ok = ok_price and ok_vol and ok_pos
        return {
            'ok': ok, 'ok_price': ok_price, 'ok_vol': ok_vol, 'ok_pos': ok_pos,
            'amp': amp, 'vr': vr, 'pos': pos, 'usr': usr,
            'close': close_b, 'high': high_b, 'low': low_b, 'vol': vol_b,
        }

    def _score_breakout(self, bc: dict, plat: dict, post: dict, theme_heat: float) -> float:
        """BREAKOUT_SCORE 100分制（第五节权重）"""
        cfg = self.cfg
        W = cfg['breakout_weights']
        s = 0.0
        # 幅度 15
        amp = bc['amp']
        if amp >= cfg['breakout_pct_strong']:
            s += W['amplitude']
        elif amp >= cfg['breakout_pct_ideal']:
            s += W['amplitude'] * 0.9
        elif amp >= cfg['breakout_pct_min']:
            s += W['amplitude'] * 0.7
        # 量能 20
        vr = bc['vr']
        if cfg['breakout_vr_ideal'][0] <= vr <= cfg['breakout_vr_ideal'][1]:
            s += W['volume']
        elif vr >= cfg['breakout_vr_min']:
            s += W['volume'] * 0.7
        elif vr > cfg['breakout_vr_exhaust']:
            s += W['volume'] * 0.2
        # 收盘位置 15
        pos = bc['pos']
        if pos >= cfg['candle_pos_ideal']:
            s += W['close_location']
        elif pos >= cfg['candle_pos_min']:
            s += W['close_location'] * 0.8
        # K线质量 15（实体阳线+短上影）
        usr = bc['usr']
        if usr <= cfg['upper_shadow_strict']:
            s += W['candle_quality']
        elif usr <= cfg['upper_shadow_max']:
            s += W['candle_quality'] * 0.7
        # 持续性 20：观察窗收盘维持 BL 之上比例
        keep = post.get('keep_ratio', 0.0)
        s += W['persistence'] * min(1.0, keep / max(cfg['post_confirm_keep_ratio'], 1e-9) * 0.5 + keep * 0.5)
        # 平台质量 10
        s += W['platform_quality'] * (plat['score'] / 100.0)
        # 共振 5
        s += min(theme_heat, W['resonance'])
        return round(min(s, 100.0), 1)

    # ═══ 突破后确认（1~3日）═══
    def _post_confirm(self, df: pd.DataFrame, b: int, end_idx: int, bl: float) -> dict:
        """突破后 1~3 日确认（第四节.6）：收盘维持 BL 之上、无快速跌回平台"""
        cfg = self.cfg
        win = cfg['post_breakout_window']
        e = min(b + win, end_idx)
        if e <= b:
            return {'keep_ratio': 1.0, 'close_below_days': 0, 'failed': False}
        seg = df.iloc[b + 1:e + 1]
        closes = seg['close'].values
        keep_ratio = float(np.mean(closes >= bl * cfg['post_confirm_floor']))
        close_below_days = int(np.sum(closes < bl))
        # 连续2日收盘回平台 -> BREAKOUT FAILED
        failed = False
        run = 0
        for c in closes:
            if c < bl:
                run += 1
                if run >= cfg['breakout_failed_close_days']:
                    failed = True
                    break
            else:
                run = 0
        return {'keep_ratio': keep_ratio, 'close_below_days': close_below_days, 'failed': failed}

    # ═══ 阶段三：首次健康回踩 ═══
    def _analyze_pullback(self, df: pd.DataFrame, b: int, end_idx: int, plat: dict, bc: dict) -> dict:
        """突破后的首次回踩分析（第六~八节）

        回踩定义：突破后 close < MA5 或 close < 前日 close 的回落段；
        回踩段 = 突破日之后到 T 日的全部K线（用斐波那契口径衡量深度）。
        """
        cfg = self.cfg
        bl = plat['high']
        bh = bc['high']                    # 突破日高点 = BreakoutHigh
        atr = plat['atr']
        if b >= end_idx:
            return {'exists': False}
        seg = df.iloc[b + 1:end_idx + 1]
        if seg.empty:
            return {'exists': False}
        lows = seg['low'].values
        closes = seg['close'].values
        vols = seg['vol'].values
        pl = float(lows.min())
        pl_idx = int(np.argmin(lows))
        pl_date = str(seg['trade_date'].iloc[pl_idx])
        # 回踩是否存在：出现 close < MA5 或 close 较前日下跌
        ma5s = seg['ma5'].values
        pull_days_mask = (closes < ma5s) | (np.r_[bc['close'], closes[:-1]] > closes)
        exists = bool(np.any(pull_days_mask))
        # 深度（斐波那契口径）
        denom = (bh - bl)
        depth = (bh - pl) / denom if denom > 0 else 0.0
        # 回踩天数：从突破日次日到最低点
        pb_days = pl_idx + 1
        # 回踩缩量：回踩段总量/突破量（spec：回踩阶段量 vs 突破日量）
        vol_b = bc['vol']
        pb_vol = float(vols.mean())
        pb_vol_ratio = pb_vol / vol_b if vol_b > 0 else 1.0
        # 回踩K线结构
        worst = seg['pct_chg'].min() if 'pct_chg' in seg.columns else 0.0
        worst = float(worst) if not np.isnan(worst) else 0.0
        # 是否收盘跌破 BL（规格七.3："连续2日收盘回到原平台"才定义 BREAKOUT FAILURE）
        below_days = int(np.sum(closes < bl))
        run, max_run = 0, 0
        for c in closes:
            if c < bl:
                run += 1
                max_run = max(max_run, run)
            else:
                run = 0
        below = below_days > 0
        # 关键位：PullbackLow >= BL - 0.8×ATR
        floor_ok = pl >= bl - cfg['pullback_floor_atr'] * atr
        # 止跌证据（第八节，至少2项）
        ev = []
        today = df.iloc[end_idx]
        prev = df.iloc[end_idx - 1] if end_idx >= 1 else None
        # 连续缩量：最近2日 vol 递减且 < 突破量
        if len(vols) >= 2 and vols[-1] < vols[-2] and vols[-1] < vol_b:
            ev.append('consecutive_shrink')
        # 下影线明显（最近回踩K线）
        if len(lows) >= 1:
            rng_t = float(today['high']) - float(today['low'])
            if rng_t > 0 and (float(today['close']) - float(today['low'])) / rng_t >= cfg['lower_shadow_min']:
                ev.append('lower_shadow')
        # 当日低点不再创新低
        if len(lows) >= 2 and float(today['low']) >= float(prev['low']):
            ev.append('no_new_low')
        # 收盘重新站上MA5
        if float(today['close']) > float(today['ma5']):
            ev.append('close_above_ma5')
        # 收盘站上前一日高点
        if prev is not None and float(today['close']) > float(prev['high']):
            ev.append('close_above_prev_high')
        # 量能开始恢复
        if len(vols) >= 2 and vols[-1] > vols[-2] and vols[-1] >= float(df['vol_ma20'].iloc[end_idx]):
            ev.append('volume_recover')
        # 当日涨幅转正
        if float(today['pct_chg']) > 0:
            ev.append('turn_positive')
        # 突破前一交易日高点
        if prev is not None and float(today['high']) > float(prev['high']) and float(today['close']) > float(prev['high']):
            ev.append('break_prev_high')
        end_ok = len(ev) >= cfg['pullback_end_min_evidence']
        return {
            'exists': exists, 'low': pl, 'low_date': pl_date, 'depth': depth,
            'days': pb_days, 'vol_ratio': pb_vol_ratio,
            'below_bl': below, 'below_days': below_days, 'below_max_run': max_run,
            'floor_ok': floor_ok,
            'evidence': ev, 'end_ok': end_ok, 'worst_day': worst,
        }

    def _score_pullback(self, pb: dict) -> float:
        """PULLBACK_SCORE 100分制（规格第十五节：深度6+时间4+缩量7+关键位5+K线3=25，
        按 4 倍折算为 100 制：24/16/28/20/12）"""
        cfg = self.cfg
        if not pb.get('exists'):
            return 0.0
        s = 0.0
        # 深度 24
        d = pb['depth']
        lo_i, hi_i = cfg['pullback_depth_ideal']
        if lo_i <= d <= hi_i:
            s += 24.0
        elif d >= cfg['pullback_depth_shallow'] and d < lo_i:
            s += 18.0
        elif d > hi_i and d <= cfg['pullback_depth_deep']:
            s += 10.0
        else:
            s += 4.0
        # 时间 16
        days = pb['days']
        bl_, bh_ = cfg['pullback_days_best']
        if bl_ <= days <= bh_:
            s += 16.0
        elif cfg['pullback_days_ideal'][0] <= days <= cfg['pullback_days_ideal'][1]:
            s += 12.0
        elif days <= cfg['pullback_days_max']:
            s += 8.0
        elif days <= cfg['pullback_days_hard']:
            s += 4.0
        else:
            s += 0.0
        # 缩量 28
        vr = pb['vol_ratio']
        if vr <= cfg['pullback_vol_ratio_ideal']:
            s += 28.0
        elif vr <= cfg['pullback_vol_ratio_ok']:
            s += 22.0
        elif vr <= cfg['pullback_vol_expand_hard']:
            s += 10.0
        else:
            s += 0.0
        # 关键位承接 20
        if pb['floor_ok']:
            s += 20.0
        # K线质量 12
        if pb['worst_day'] > -3.0:
            s += 12.0
        elif pb['worst_day'] > -6.0:
            s += 5.0
        return round(min(s, 100.0), 1)

    # ═══ 阶段四：重新转强 ═══
    def _analyze_reaccel(self, df: pd.DataFrame, end_idx: int, b: int, pb: dict) -> dict:
        """重新转强判定（第九节）。T 日 = end_idx，b = 突破日索引"""
        cfg = self.cfg
        today = df.iloc[end_idx]
        prev = df.iloc[end_idx - 1] if end_idx >= 1 else None
        close = float(today['close'])
        ma5 = float(today['ma5'])
        ma10 = float(today['ma10'])
        vol = float(today['vol'])
        vma20 = float(today['vol_ma20'])
        vr = vol / vma20 if vma20 > 0 else 0.0
        rng = float(today['high']) - float(today['low'])
        pos = (close - float(today['low'])) / rng if rng > 0 else 0.5
        above_prev_high = prev is not None and close > float(prev['high'])
        ma5_ma10 = ma5 > ma10
        ma10_slope = _safe(lambda: float(ma10 / df['ma10'].iloc[end_idx - 1] - 1.0)) if end_idx >= 1 else 0.0
        ok = (close > ma5 and above_prev_high and ma5_ma10
              and ma10_slope >= cfg['reaccel_ma10_slope_min']
              and vr >= cfg['reaccel_vol_ratio_min']
              and pos >= cfg['reaccel_candle_pos_min'])
        # 突破回踩段高点（再启动确认的优选条件）
        break_pb_high = False
        if pb.get('exists') and b + 1 < end_idx:
            seg_highs = df['high'].iloc[b + 1:end_idx].values.astype(float)
            if len(seg_highs) > 0:
                break_pb_high = close > float(seg_highs.max())
        return {
            'ok': ok, 'close': close, 'vr': vr, 'pos': pos,
            'above_prev_high': above_prev_high, 'ma5_ma10': ma5_ma10,
            'ma10_slope': ma10_slope, 'break_pb_high': break_pb_high,
        }

    # ═══ 单股评分入口 ═══
    def score(self, df: pd.DataFrame, ts_code: str = '', name: str = '',
              industry: str = '', market_regime: str = 'neutral') -> PRBResult:
        """df 必须已截断到 T 日（T 日为最后一根K线）。"""
        cfg = self.cfg
        r = PRBResult(ts_code=ts_code, name=name, industry=industry, market_regime=market_regime)
        n = len(df)
        r.n_bars = n
        if n < cfg['min_bars']:
            r.state = 'PLATFORM_BUILDING'
            r.action = 'NO_TRADE'
            r.action_reason = 'K线不足'
            return r
        end_idx = n - 1
        r.date = str(df['trade_date'].iloc[end_idx])
        df = self._prep(df)

        today = df.iloc[end_idx]
        r.close = float(today['close'])
        r.pct_chg = _safe(lambda: float(today['pct_chg']))
        r.atr20 = _safe(lambda: float(today['atr20']))
        r.ma5 = _safe(lambda: float(today['ma5']))
        r.ma10 = _safe(lambda: float(today['ma10']))
        r.ma20 = _safe(lambda: float(today['ma20']))
        r.ma60 = _safe(lambda: float(today['ma60']))
        vma20 = float(today['vol_ma20'])
        r.vol_ratio_today = float(today['vol']) / vma20 if vma20 > 0 else 0.0
        r.dist_ma5 = (r.close / r.ma5 - 1.0) if r.ma5 > 0 else 0.0

        # ═══ 市场过滤器（第十三节）═══
        # strong/bull：允许完整执行 S 级信号
        # neutral：只执行 S/A 级
        # weak：关闭普通突破，仅保留极强（平台/突破双高）S 级
        # bear：关闭全部突破策略
        mf = cfg['market_filter']
        regime = (market_regime or 'neutral').lower()
        r.market_regime = regime
        mf_cfg = mf.get(regime) or mf['neutral']
        r.market_allowed = 'S' in mf_cfg['allow_grades']  # 至少允许 S 级
        r.market_level = {
            'strong': 3, 'bull': 3, 'neutral': 2, 'weak': 1, 'bear': 0,
        }.get(regime, 2)  # 3=S/A/B 全开, 2=S/A, 1=仅S且极强, 0=关闭
        # weak/bear 极强门槛：仅保留平台/突破双强（近似"极强行业龙头、极强突破"）
        r.market_strong_gate = tuple(mf_cfg['strong_gate'])

        # ═══ 突破日搜索 ═══
        # 找最近 30 日内满足突破条件的最早一日作为突破日 b
        # 每个候选日对"全部合格平台"逐个验证突破条件，选"实际被突破的最高分平台"
        candidates = []
        for bi in range(max(40, end_idx - 30), end_idx + 1):
            if bi < 40:
                continue
            plats = self.find_platforms(df, bi)
            if not plats:
                continue
            for p in plats:
                bcx = self._breakout_conditions(df, bi, p)
                if not bcx['ok']:
                    continue
                post = self._post_confirm(df, bi, end_idx, p['high'])
                if not post['failed']:
                    candidates.append((bi, p, bcx))
                    break  # 该日取最高分被突破平台即可
        if not candidates:
            # 无有效突破：状态停在平台阶段
            r.state = 'PLATFORM_BUILDING'
            r.action = 'WAIT_BREAKOUT'
            r.action_reason = '无有效平台突破'
            self._finalize(r, df)
            return r
        # 取最早的未失败突破日 = 真正的首次突破日（spec 第六节：只做第一次回踩）
        # 注意不能用"最近连续段起点"：健康回踩日 close<BL+0.3ATR 会使连续段断开，
        # 导致转强日被误选为新突破日（博济 8/7 突破->8/10 回踩->8/11 转强 案例）
        b, plat, bc = candidates[0]

        # ═══ 平台信息 ═══
        r.platform_start = str(df['trade_date'].iloc[plat['start']])
        r.platform_end = str(df['trade_date'].iloc[plat['end'] - 1])
        r.platform_days = plat['days']
        r.platform_high = plat['high']
        r.platform_low = plat['low']
        r.platform_range = plat['range']
        r.platform_range_atr = plat['range_atr']
        r.resistance_tests = plat['tests_hi']
        r.support_tests = plat['tests_lo']
        r.vol_shrink_ratio = plat['vol_shrink']
        r.convergence_ratio = plat['conv']
        r.platform_score = plat['score']
        r.platform_grade = plat['grade']
        r.breakout_level = plat['high']
        r.dist_breakout_level = (r.close - r.breakout_level) / r.atr20 if r.atr20 > 0 else 0.0

        # ═══ 突破信息 ═══
        post = self._post_confirm(df, b, end_idx, plat['high'])
        r.breakout_date = str(df['trade_date'].iloc[b])
        r.breakout_price = bc['close']
        r.breakout_pct = bc['amp']
        r.breakout_vr = bc['vr']
        r.breakout_candle_pos = bc['pos']
        r.breakout_upper_shadow = bc['usr']
        r.post_breakout_days = end_idx - b
        r.post_keep_ratio = post['keep_ratio']
        r.breakout_failed = post['failed']
        theme_heat = 0.0  # scanner 层回填
        r.breakout_score = self._score_breakout(bc, plat, post, theme_heat)
        r.breakout_grade = ('强突破' if r.breakout_score >= cfg['breakout_score_strong']
                            else '有效突破' if r.breakout_score >= cfg['breakout_score_valid']
                            else '弱突破' if r.breakout_score >= cfg['breakout_score_weak'] else '无效突破')

        # ═══ 回踩分析 ═══
        pb = self._analyze_pullback(df, b, end_idx, plat, bc)
        if pb.get('exists'):
            r.pullback_start = str(df['trade_date'].iloc[b + 1])
            r.pullback_low = pb['low']
            r.pullback_low_date = pb['low_date']
            r.pullback_depth = pb['depth']
            r.pullback_days = pb['days']
            r.pullback_vol_ratio = pb['vol_ratio']
            r.pullback_below_bl = pb['below_bl']
            r.pullback_below_days = pb['below_days']
            r.pullback_end_evidence = pb['evidence']
            r.pullback_end_ok = pb['end_ok']
            r.pullback_score = self._score_pullback(pb)
        # 回踩失败判定
        if pb.get('exists'):
            if pb.get('below_max_run', pb['below_days']) >= cfg['pullback_close_below_days']:
                r.pullback_failed = True
                r.pullback_failed_reason = f"连续{pb['below_max_run']}日收盘跌回平台（BREAKOUT FAILURE）"
            elif pb['vol_ratio'] > cfg['pullback_vol_expand_hard']:
                r.pullback_failed = False
                r.forbidden_reasons.append('回踩放量：抛压未衰减')
            elif pb['low'] < plat['high'] - 1.0 * plat['atr']:
                r.pullback_failed = True
                r.pullback_failed_reason = '回踩过深：跌破 BL-1.0ATR（严禁买入12.3）'
            else:
                # 注意：不用 depth>=100% 判死——突破幅度小时低点略破 BL 即深度>100%，
                # 但只要守住 BL-0.8ATR（floor_ok）就是"探突破位不破"的健康回踩（地铁设计案例）
                r.pullback_failed = False
        # 距突破位过远（盈亏比恶化）
        if r.dist_breakout_level > cfg['reaccel_max_price_ext']:
            r.forbidden_reasons.append(f"距突破位过远：{r.dist_breakout_level:.1f}×ATR")
        # 突破日爆涨过度（严禁买入12.6：>=10% 高潮不追）
        if bc['amp'] >= cfg['breakout_pct_climax']:
            r.forbidden_reasons.append(f"突破日爆涨过度 +{bc['amp'] * 100:.1f}%（严禁）")
        elif bc['amp'] >= cfg['breakout_pct_overheat']:
            # +7~10%：仅警惕短线高潮，不构成硬禁令（规格十二.6 严禁的是"爆涨过度"）
            r.warnings.append(f"突破日爆涨警惕 +{bc['amp'] * 100:.1f}%")

        # ═══ 再启动分析 ═══
        ra = self._analyze_reaccel(df, end_idx, b, pb)
        r.reaccel_ok = ra['ok']
        r.reaccel_price = ra['close']
        r.reaccel_vol_ratio = ra['vr']
        r.reaccel_candle_pos = ra['pos']
        r.reaccel_close_above_prev_high = ra['above_prev_high']
        r.reaccel_ma5_above_ma10 = ra['ma5_ma10']
        r.reaccel_break_pullback_high = ra['break_pb_high']

        # ═══ 状态机推进（第十六节）═══
        # PLATFORM_BUILDING -> PLATFORM_CONFIRMED（score>=75）
        if r.platform_score >= cfg['platform_score_gate']:
            r.state = 'PLATFORM_CONFIRMED'
        # BREAKOUT_CONFIRMED：突破条件成立 + post 未失败 + BREAKOUT_SCORE >= 75
        if post['failed']:
            r.state = 'BREAKOUT_FAILED'
            r.action = 'BREAKOUT_FAILED'
            r.action_reason = post.get('reason', '突破后连续收盘跌回平台')
            self._finalize(r, df)
            return r
        if r.breakout_score >= cfg['breakout_score_gate'] or bc['ok']:
            r.state = 'BREAKOUT_CONFIRMED'
        # 首次回踩
        if pb.get('exists'):
            if r.pullback_failed:
                r.state = 'PULLBACK_FAILED'
                r.action = 'PULLBACK_FAILED'
                r.action_reason = r.pullback_failed_reason
                self._finalize(r, df)
                return r
            r.state = 'FIRST_PULLBACK'
            # 关键位承接
            if pb['floor_ok']:
                r.state = 'PULLBACK_SUPPORT'
            # 回踩结束（至少2项止跌证据）
            if pb['end_ok']:
                # 再启动判定
                if ra['ok']:
                    r.state = 'RE_ACCELERATION'
                    r.reaccel_date = r.date
                    # 第二次大幅上涨检查：回踩结束后股价已大幅走高 -> 错过买点
                    second_wave_done = (
                        r.dist_breakout_level > cfg['reaccel_max_price_ext']
                        or (r.pullback_low > 0 and r.close / r.pullback_low - 1.0 > cfg['second_wave_max_gain'])
                    )
                    # PRIMARY BUY 判定（第十节 S 级硬条件）
                    pbr = cfg['primary_buy_rules']
                    # 市场过滤（第十三节）：weak/bear 仅极强平台+突破允许
                    mkt_strong = (r.platform_score >= r.market_strong_gate[0]
                                  and r.breakout_score >= r.market_strong_gate[1])
                    primary_ok = (
                        r.market_level >= 1
                        and mkt_strong
                        and r.platform_score >= pbr['platform_score_min']
                        and r.breakout_score >= pbr['breakout_score_min']
                        and pb['vol_ratio'] <= pbr['pullback_vol_ratio_max']
                        and pb['floor_ok']
                        and ra['close'] > r.ma5
                        and ra['above_prev_high']
                        and ra['ma5_ma10']
                        and ra['vr'] >= pbr['vol_ratio_min']
                        and ra['pos'] >= pbr['candle_pos_min']
                        and not r.forbidden_reasons
                        and not second_wave_done
                    )
                    if primary_ok:
                        r.state = 'PRIMARY_BUY'
                        r.action = 'PRIMARY_BUY'
                        r.action_reason = '突破后首次健康缩量回踩，关键位不破，重新转强'
                    elif second_wave_done:
                        r.state = 'HOLD'
                        r.action = 'NO_TRADE'
                        r.action_reason = ('第二次上涨已发生，错过 PRIMARY BUY 窗口'
                                           + ('；' + '；'.join(r.forbidden_reasons) if r.forbidden_reasons else ''))
                    else:
                        # B级 CONFIRMED BUY（第十一节）：二次突破 Close > PullbackHigh 且量比>1.2
                        cbr = cfg['confirmed_buy_rules']
                        confirmed_ok = (
                            r.market_level >= 3  # 追涨仅在强势市允许
                            and ra['break_pb_high']
                            and ra['vr'] >= cbr['vol_ratio_min']
                            and r.platform_score >= cbr['platform_score_min']
                            and r.breakout_score >= cbr['breakout_score_min']
                            and not r.forbidden_reasons
                        )
                        if confirmed_ok:
                            r.state = 'RE_ACCELERATION'
                            r.action = 'CONFIRMED_BUY'
                            r.action_reason = '二次突破回踩高点+放量确认，确认型追涨（仓位不超S级）'
                        else:
                            r.state = 'RE_ACCELERATION'
                            r.action = 'WAIT_REACCELERATION'
                            if r.market_level == 0:
                                r.action_reason = f'市场{regime}：关闭突破策略，仅观察'
                            elif r.market_level == 1 and not mkt_strong:
                                r.action_reason = f'市场{regime}：仅极强平台/突破(≥88分)可交易，当前观察'
                            else:
                                r.action_reason = '转强但未满足 PRIMARY BUY 全部硬条件'
                else:
                    # 回踩结束但未转强 -> 检查 A级 EARLY BUY（第十一节）
                    ebr = cfg['early_buy_rules']
                    early_ok = (
                        r.market_level >= 2  # A级轻仓仅在 non-weak 市允许
                        and pb['floor_ok']
                        and pb['depth'] >= ebr['pullback_depth_min']
                        and pb['vol_ratio'] <= ebr['pullback_vol_ratio_max']
                        and pb['end_ok']
                        and r.platform_score >= ebr['platform_score_min']
                        and r.breakout_score >= ebr['breakout_score_min']
                        and not r.forbidden_reasons
                    )
                    if early_ok:
                        r.state = 'PULLBACK_SUPPORT'
                        r.action = 'EARLY_BUY'
                        r.action_reason = '首次回踩关键位承接标准+缩量+止跌证据>=2，轻仓试探(20%~30%)'
                    else:
                        r.state = 'PULLBACK_SUPPORT'
                        r.action = 'WAIT_REACCELERATION'
                        r.action_reason = '回踩结束（止跌证据>=2）但尚未重新转强'
            else:
                r.state = 'FIRST_PULLBACK'
                r.action = 'WAIT_PULLBACK'
                r.action_reason = '回踩中，止跌证据不足（<2项）'
        else:
            # 无回踩：刚突破或突破后一路走强
            if end_idx - b <= cfg['post_breakout_window']:
                r.state = 'BREAKOUT_CONFIRMED'
                r.action = 'WAIT_PULLBACK'
                r.action_reason = '突破确认，等待首次健康回踩'
            else:
                r.state = 'BREAKOUT_CONFIRMED'
                r.action = 'WAIT_PULLBACK'
                r.action_reason = '突破后走强未回踩，等待首次健康回踩'

        self._finalize(r, df)
        return r

    def _finalize(self, r: PRBResult, df: pd.DataFrame):
        """最终评分 + 等级 + 文本"""
        cfg = self.cfg
        # 最终评分（第十五节）
        fw = cfg['final_weights']
        # PULLBACK_SCORE 折算：100 制 -> 25 分权重
        if r.pullback_score > 0:
            pb_contrib = fw['pullback'] * (r.pullback_score / 100.0)
        else:
            pb_contrib = 0.0
        ra_score = self._score_reaccel(r)
        ra_contrib = fw['reaccel'] * (ra_score / 100.0)
        r.final_score = round(min(100.0,
            fw['platform'] * (r.platform_score / 100.0)
            + fw['breakout'] * (r.breakout_score / 100.0)
            + pb_contrib + ra_contrib), 1)
        # 等级（第十五节）
        # 状态机动作与最终评级必须一致：动作已判定买入时，评级不得低于该动作对应级别
        if r.action == 'PRIMARY_BUY':
            # S级 PRIMARY BUY 硬条件（第十节）已全部满足 -> 至少 S 级（≥90）
            r.final_score = max(r.final_score, cfg['grade_s'])
            r.grade = 'S'
            r.grade_cn = '★ PRIMARY BUY'
        elif r.action == 'EARLY_BUY':
            # A级 EARLY BUY（第十一节）：轻仓试探 -> 至少 A 级（78）
            r.final_score = max(r.final_score, cfg['grade_a'])
            r.grade = 'A'
            r.grade_cn = 'A级观察/轻仓'
        elif r.action == 'CONFIRMED_BUY':
            # B级 CONFIRMED BUY（第十一节）：确认型追涨 -> 至少强买点（85）
            r.final_score = max(r.final_score, cfg['grade_strong_buy'])
            r.grade = 'STRONG'
            r.grade_cn = '强买点'
        elif r.market_level <= 1:
            # 市场弱/熊：策略关闭，grade 一律不得显示买入级（第十三节）
            r.grade = 'WAIT' if r.market_level == 1 else 'NO_TRADE'
            r.grade_cn = '等待确认（弱市仅极强可交易）' if r.market_level == 1 else '不交易（熊市关闭）'
        elif r.final_score >= cfg['grade_strong_buy']:
            r.grade = 'STRONG'
            r.grade_cn = '强买点'
        elif r.final_score >= cfg['grade_a']:
            r.grade = 'A'
            r.grade_cn = 'A级观察/轻仓'
        elif r.final_score >= cfg['grade_wait']:
            r.grade = 'WAIT'
            r.grade_cn = '等待确认'
        else:
            r.grade = 'NO_TRADE'
            r.grade_cn = '不交易'
        r.state_cn = STATE_CN.get(r.state, r.state)
        r.action_cn = ACTION_CN.get(r.action, r.action)
        self._build_checks(r)

    def _score_reaccel(self, r: PRBResult) -> float:
        """RE-ACCELERATION 100 制（第九节）"""
        cfg = self.cfg
        W = cfg['reaccel_weights']
        s = 0.0
        if r.reaccel_close_above_prev_high:
            s += W['close_strength']
        if r.reaccel_ma5_above_ma10:
            s += W['ma_structure']
        if r.reaccel_vol_ratio >= cfg['reaccel_vol_ratio_min']:
            s += W['volume_recovery']
        if r.reaccel_break_pullback_high:
            s += W['break_pullback_high']
        # 分时强度近似：收盘位置
        if r.reaccel_candle_pos >= cfg['reaccel_candle_pos_ideal']:
            s += W['intraperiod']
        elif r.reaccel_candle_pos >= cfg['reaccel_candle_pos_min']:
            s += W['intraperiod'] * 0.5
        return round(min(s, 100.0), 1)

    def _build_checks(self, r: PRBResult):
        c = {}
        c['平台质量'] = {'ok': r.platform_score >= self.cfg['platform_score_gate'],
                       'detail': f"{r.platform_score:.1f}分/{r.platform_grade}级 {r.platform_days}日 振幅{r.platform_range * 100:.1f}% "
                                 f"上测{r.resistance_tests}次 下承{r.support_tests}次 量缩比{r.vol_shrink_ratio:.2f}"}
        c['突破有效性'] = {'ok': r.breakout_score >= self.cfg['breakout_score_gate'],
                        'detail': f"{r.breakout_score:.1f}分/{r.breakout_grade} {r.breakout_date} +{r.breakout_pct * 100:.1f}% "
                                  f"量比{r.breakout_vr:.2f} 收盘位{r.breakout_candle_pos * 100:.0f}%"}
        c['首次回踩'] = {'ok': r.pullback_score > 0,
                      'detail': (f"深度{r.pullback_depth * 100:.0f}% {r.pullback_days}日 量/突破量{r.pullback_vol_ratio:.2f} "
                                 f"{'跌破BL' if r.pullback_below_bl else '守住BL'}") if r.pullback_days > 0 else '未回踩'}
        c['关键位承接'] = {'ok': not r.pullback_failed,
                        'detail': ('守住 BL-0.8ATR' if r.pullback_days > 0 and not r.pullback_failed else
                                   (r.pullback_failed_reason or '未回踩'))}
        c['回踩缩量'] = {'ok': 0 < r.pullback_vol_ratio <= self.cfg['pullback_vol_ratio_ok'],
                       'detail': f"回踩量/突破量 {r.pullback_vol_ratio:.2f}" if r.pullback_days > 0 else '未回踩'}
        c['重新转强'] = {'ok': r.reaccel_ok,
                      'detail': f"close>MA5:{r.close > r.ma5} close>前日高:{r.reaccel_close_above_prev_high} "
                                f"MA5>MA10:{r.reaccel_ma5_above_ma10} 量比{r.reaccel_vol_ratio:.2f} 收盘位{r.reaccel_candle_pos * 100:.0f}%"}
        c['市场过滤'] = {'ok': r.market_allowed,
                        'detail': (f"{r.market_regime} 级别{r.market_level}"
                                   + (' 极强门槛' if r.market_level <= 1 else ''))}
        if r.forbidden_reasons:
            c['严禁买入'] = {'ok': False, 'detail': '；'.join(r.forbidden_reasons)}
        r.checks = c
