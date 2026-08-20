"""RightConfirmBuyEngine — 右侧确认买入引擎（RIGHT CONFIRM BUY）

目标：不是寻找"跌得够多"的股票，而是寻找"现在重新转强"的股票。
链路：前期资金启动 → 多次涨停/强势拉升 → 正常回调洗盘 → 回调末端承接 →
      再次转强 → 右侧确认买入。

核心原则：
  宁可晚买1~3%，不要提前抄底5~10%。
  买入的核心依据来自当前右侧行为，而不是过去的历史强度。

评分结构：
  FinalScore = LaunchScore×20% + PullbackScore×30% + RightConfirmScore×40% + ThemeScore×10%
  RightConfirmScore 权重最高——衡量"现在是不是已经开始重新转强"。

信号等级：
  S｜右侧突破买 / A｜右侧确认买 / B｜等待确认 / C｜回踩观察 / D｜PASS

市场环境分级控制 BUY 阈值：
  强势/主升: BUY>=70  | 震荡回暖: BUY>=75 且必须趋势确认 | 震荡: BUY>=80 且必须放量突破 | 弱势/退潮: 禁止 BUY

数据全部复用已有设施：DataLoader（行情/均线/ATR）、大盘环境（Market Score/Regime/Recovery）、主题数据。
"""

import os
import sys
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'inst_pullback_v2'))
from data.loader import DataLoader


# ──────────────────────────────────────────────
# 结果结构
# ──────────────────────────────────────────────
@dataclass
class RightConfirmResult:
    """右侧确认买入引擎单股结果"""
    ts_code: str
    name: str = ""
    is_qualified: bool = False      # 进入输出（非 PASS）
    signal_level: str = ""          # S / A / B / C / D
    signal_label: str = ""          # 中文标签
    no_chase: bool = False          # 严禁追高
    false_breakout: bool = False    # 假突破
    distribution_breakout: bool = False  # 出货式突破

    # 四大评分
    launch_score: float = 0.0       # 启动质量 0-100
    pullback_score: float = 0.0     # 回调质量 0-100
    confirm_score: float = 0.0      # 右侧确认 0-100
    theme_score: float = 0.0        # 主题共振 0-100
    final_score: float = 0.0        # 最终交易评分

    # 子分
    launch_subs: Dict[str, float] = field(default_factory=dict)
    pullback_subs: Dict[str, float] = field(default_factory=dict)
    confirm_subs: Dict[str, float] = field(default_factory=dict)

    # 右侧确认信号
    confirm_signals: List[str] = field(default_factory=list)
    confirm_count: int = 0

    # Distribution 风险
    distribution_penalty: float = 0.0
    distribution_risk: bool = False

    # 拉升/回调概况
    rally_high_date: str = ""
    rally_amplitude: float = 0.0
    rally_limit_up_count: int = 0
    rally_max_consecutive_lu: int = 0
    rally_vol_expansion: float = 0.0
    drawdown: float = 0.0
    pullback_days: int = 0

    # 结构
    structure: Dict[str, float] = field(default_factory=dict)  # {pre_high, platform, pullback_low, ma20, vwap}

    # 交易计划
    entry: float = 0.0
    confirm_price: float = 0.0
    safe_entry: float = 0.0
    aggressive_entry: float = 0.0
    stop_loss: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    risk_pct: float = 0.0
    r_multiple: float = 0.0
    atr: float = 0.0

    env_tier: str = ""              # strong / recovery / neutral / weak
    summary: str = ""
    t1_confirm_advice: str = ""     # T+1 确认建议


# ──────────────────────────────────────────────
# 引擎
# ──────────────────────────────────────────────
class RightConfirmBuyEngine:
    """右侧确认买入引擎

    独立于 RallyPullbackEngine。候选池由上层传入（复用主板+市值>80亿候选池）。
    内部用 DataLoader 读取行情/均线/ATR，用大盘环境 + 主题数据做共振与阈值调节。
    """

    def __init__(self, config: dict = None):
        self.cfg = (config or {}).get('right_confirm_buy', {})
        self.loader = DataLoader()

    # ──────────────────────────────────────────────
    # 主入口
    # ──────────────────────────────────────────────
    def detect(self, ts_code: str, trade_date: str,
               market_env: dict = None, stock_theme: str = "") -> Optional[RightConfirmResult]:
        """检测单只股票

        Args:
            ts_code: 股票代码
            trade_date: 交易日 YYYYMMDD
            market_env: 大盘环境 dict:
                {market_score, regime, recovery_state, risk_score, breadth_score,
                 sentiment_score, limit_up_ratio, top_themes: [name,...], theme_scores: {name:score}}
            stock_theme: 个股主题（dominant_theme/subtheme）
        """
        if trade_date:
            self.loader.trade_date = trade_date
        td = self.loader.trade_date
        market_env = market_env or {}

        lookback = self.cfg.get('lookback', 100)
        start_date = (pd.to_datetime(td) - pd.Timedelta(days=lookback + 40)).strftime('%Y%m%d')
        df = self.loader.load_stk_factor(ts_code, start_date, td, silent=True)
        if df is None or df.empty or len(df) < 60:
            return None

        result = RightConfirmResult(
            ts_code=ts_code,
            name=self.loader.get_stock_name(ts_code),
        )

        close = df['close_qfq'].values if 'close_qfq' in df.columns else df['close'].values
        open_vals = df['open_qfq'].values if 'open_qfq' in df.columns else df['open'].values
        high_vals = df['high_qfq'].values if 'high_qfq' in df.columns else df['high'].values
        low_vals = df['low_qfq'].values if 'low_qfq' in df.columns else df['low'].values
        vol = df['vol'].values if 'vol' in df.columns else None
        pct_chg = df['pct_chg'].values if 'pct_chg' in df.columns else None
        dates = df['trade_date'].values if 'trade_date' in df.columns else None
        n = len(close)

        # 均线
        def _ma(col, period):
            if col in df.columns:
                s = df[col].iloc[-1]
                return float(s) if pd.notna(s) else 0.0
            return float(pd.Series(close).rolling(period).mean().iloc[-1]) if n >= period else 0.0

        ma5 = _ma('ma_qfq_5', 5)
        ma10 = _ma('ma_qfq_10', 10)
        ma20 = _ma('ma_qfq_20', 20)
        ma60 = _ma('ma_qfq_60', 60)

        # ATR
        atr_val = 0.0
        if 'atr_qfq' in df.columns:
            atr_val = float(df['atr_qfq'].iloc[-1]) if pd.notna(df['atr_qfq'].iloc[-1]) else 0.0
        if atr_val <= 0:
            atr_val = self._calc_atr(close, high_vals, low_vals, 14)
        result.atr = round(atr_val, 2)

        # VWAP（日线：典型价×量 加权）
        vwap_series = self._calc_vwap(high_vals, low_vals, close, vol)

        # ═══════════════════════════════════════════
        # 阶段0: 市场环境分级
        # ═══════════════════════════════════════════
        env_tier, env_reason = self._classify_env(market_env)
        result.env_tier = env_tier

        # ═══════════════════════════════════════════
        # 阶段A: 启动质量 LaunchScore
        # ═══════════════════════════════════════════
        launch = self._compute_launch(close, high_vals, low_vals, vol, pct_chg, n,
                                      ma5=ma5, ma10=ma10, ma20=ma20)
        if launch is None:
            return None  # 未满足启动质量门槛，不进入候选
        result.launch_score = launch['score']
        result.launch_subs = launch['subs']
        result.rally_amplitude = launch['amplitude']
        result.rally_vol_expansion = launch['vol_expansion']
        result.rally_limit_up_count = launch['limit_up_count']
        result.rally_max_consecutive_lu = launch['max_consec_lu']
        result.rally_high_date = str(dates[launch['high_idx']]) if dates is not None else ""
        high_idx = launch['high_idx']

        # ═══════════════════════════════════════════
        # 阶段B: 回调健康 PullbackScore + Distribution 判定
        # ═══════════════════════════════════════════
        pb = self._compute_pullback(close, open_vals, high_vals, low_vals, vol,
                                    pct_chg, high_idx, n, ma20=ma20,
                                    platform_low=launch['platform_low'])
        result.pullback_score = pb['score']
        result.pullback_subs = pb['subs']
        result.drawdown = pb['drawdown']
        result.pullback_days = pb['days']
        result.distribution_penalty = pb['distribution_penalty']
        result.distribution_risk = pb['distribution_penalty'] >= 30

        if result.distribution_risk:
            # DistributionPenalty>=30 → 直接 PASS
            result.signal_level = 'D'
            result.signal_label = 'D｜PASS'
            result.is_qualified = True  # 输出（用于展示被拒原因）
            result.summary = (f"Distribution风险(penalty {pb['distribution_penalty']:.0f})，"
                              f"放量下跌/破位/退潮，即使右侧确认也不参与")
            return result

        # 回调是否已进入可观察范围（回撤 5~18%，回调 3~12 天）—— 用于 C 级判定
        pullback_ok = pb['drawdown_ok']

        # ═══════════════════════════════════════════
        # 阶段C: 右侧确认 RightConfirmScore
        # ═══════════════════════════════════════════
        confirm = self._compute_confirm(close, open_vals, high_vals, low_vals, vol,
                                        pct_chg, n, ma5=ma5, ma10=ma10, ma20=ma20,
                                        vwap_series=vwap_series, pullback_score=pb['score'],
                                        stock_theme=stock_theme, market_env=market_env)
        result.confirm_score = confirm['score']
        result.confirm_subs = confirm['subs']
        result.confirm_signals = confirm['signals']
        result.confirm_count = confirm['count']
        result.theme_score = confirm['theme_score']

        # ═══════════════════════════════════════════
        # 阶段D: FinalScore
        # ═══════════════════════════════════════════
        final_cfg = self.cfg.get('final_weights', {})
        w_launch = final_cfg.get('launch', 0.20)
        w_pullback = final_cfg.get('pullback', 0.30)
        w_confirm = final_cfg.get('confirm', 0.40)
        w_theme = final_cfg.get('theme', 0.10)
        result.final_score = (result.launch_score * w_launch +
                              result.pullback_score * w_pullback +
                              result.confirm_score * w_confirm +
                              result.theme_score * w_theme)

        # ═══════════════════════════════════════════
        # 阶段E: 信号分级（结合环境阈值）
        # ═══════════════════════════════════════════
        grade = self._grade(result, confirm, pb, market_env, env_tier, close,
                            high_vals, vol, ma20=ma20, pullback_ok=pullback_ok)
        result.signal_level = grade['level']
        result.signal_label = grade['label']
        result.no_chase = grade['no_chase']
        result.false_breakout = grade['false_breakout']
        result.distribution_breakout = grade['distribution_breakout']

        # ═══════════════════════════════════════════
        # 阶段F: 交易计划（价格 + 止损 + 止盈）
        # ═══════════════════════════════════════════
        plan = self._build_plan(close, high_vals, low_vals, vol, n, atr_val,
                                pb['pullback_low'], ma20=ma20, platform_high=confirm['platform_high'])
        result.entry = plan['entry']
        result.confirm_price = plan['confirm_price']
        result.safe_entry = plan['safe_entry']
        result.aggressive_entry = plan['aggressive_entry']
        result.stop_loss = plan['stop_loss']
        result.tp1 = plan['tp1']
        result.tp2 = plan['tp2']
        result.risk_pct = plan['risk_pct']
        result.r_multiple = plan['r_multiple']
        result.structure = {
            'pre_high': round(float(launch['pre_high']), 2),
            'platform': round(float(launch['platform_low']), 2),
            'pullback_low': round(float(pb['pullback_low']), 2),
            'ma20': round(float(ma20), 2),
            'vwap': round(float(vwap_series[-1]), 2) if vwap_series is not None else 0.0,
        }

        # 结构性止损距离>8% → 禁止买入
        if result.signal_level in ('S', 'A') and plan['risk_pct'] > 0.08:
            result.signal_level = 'B'
            result.signal_label = 'B｜等待确认（止损距离>8%，风险收益比不合理）'
        # No Chase：Close 已高于 ConfirmPrice×1.08
        if plan['confirm_price'] > 0 and close[-1] > plan['confirm_price'] * 1.08:
            result.no_chase = True
            if result.signal_level in ('S', 'A'):
                result.signal_level = 'B'
                result.signal_label = 'B｜NO CHASE（已脱离最佳风险收益区间，等待回踩）'

        # T+1 确认建议
        result.t1_confirm_advice = self._build_t1_advice(result, close[-1])

        # 一句话总结
        result.summary = self._build_summary(result)

        return result

    # ──────────────────────────────────────────────
    # 阶段0: 环境分级
    # ──────────────────────────────────────────────
    def _classify_env(self, market_env: dict):
        market_score = market_env.get('market_score', 50)
        regime = market_env.get('regime', '')
        risk_score = market_env.get('risk_score', 50)

        # 弱势/退潮：熊市/下跌 + 低分
        if regime in ('Bear', 'Distress') or market_score < 45 or risk_score < 30:
            return 'weak', f"弱势/退潮 (score={market_score:.0f} regime={regime})"
        # 强势/主升
        if regime in ('Bull', 'Euphoria') or market_score >= 70:
            return 'strong', f"强势/主升 (score={market_score:.0f} regime={regime})"
        # 震荡回暖
        if regime == 'Recovery' or str(market_env.get('recovery_state') or '').startswith('Recovery'):
            return 'recovery', f"震荡回暖 (score={market_score:.0f} regime={regime})"
        # 震荡
        return 'neutral', f"震荡 (score={market_score:.0f} regime={regime})"

    # ──────────────────────────────────────────────
    # 阶段A: 启动质量
    # ──────────────────────────────────────────────
    def _compute_launch(self, close, high, low, vol, pct_chg, n,
                        ma5=None, ma10=None, ma20=None):
        """启动质量评分 0-100。
        识别近 lookback 内的放量多涨停拉升。
        """
        rl_cfg = self.cfg.get('launch', {})
        lookback = rl_cfg.get('lookback', 40)
        min_amplitude = rl_cfg.get('min_amplitude', 0.15)
        min_limit_up = rl_cfg.get('min_limit_up', 1)
        min_vol_expansion = rl_cfg.get('min_vol_expansion', 1.3)
        lu_threshold = rl_cfg.get('limit_up_threshold', 9.5)

        rally_start = max(0, n - lookback)
        # 拉升区间最高点（不含最后1日，避免把回调中的小反弹当高点）
        seg = close[rally_start:n - 1] if n - 1 > rally_start else close[rally_start:]
        high_idx = rally_start + int(np.argmax(seg))
        high_price = float(close[high_idx])

        # 拉升起点（最高点前的最低点）
        low_idx = rally_start + int(np.argmin(close[rally_start:high_idx + 1]))
        low_price = float(close[low_idx])

        # 幅度门槛
        amplitude = (high_price - low_price) / low_price if low_price > 0 else 0.0
        if amplitude < min_amplitude:
            return None

        # 放量检测
        vol_expansion = 0.0
        if vol is not None and low_idx > 0:
            rally_vol = np.mean(vol[low_idx:high_idx + 1])
            pre_vol = np.mean(vol[max(0, low_idx - 20):low_idx])
            if pre_vol > 0:
                vol_expansion = float(rally_vol / pre_vol)
        if vol_expansion < min_vol_expansion:
            return None

        # 涨停检测
        limit_up_count = 0
        max_consec_lu = 0
        if pct_chg is not None:
            mask = pct_chg[low_idx:high_idx + 1] >= lu_threshold
            limit_up_count = int(np.sum(mask))
            cur = 0
            for v in mask:
                cur = cur + 1 if v else 0
                max_consec_lu = max(max_consec_lu, cur)
        if limit_up_count < min_limit_up:
            return None

        # 突破质量：拉升是否突破拉升前的阶段高点（平台/箱体/阶段新高）
        pre_high = float(np.max(high[max(0, low_idx - 20):low_idx])) if low_idx > 0 else high_price
        break_through = float(high_price) > pre_high * 1.02  # 突破2%以上

        # 趋势强度：MA20 斜率（当前 vs 10日前）
        ma20_now = ma20 if ma20 else np.mean(close[-20:])
        ma20_prev = 0.0
        if len(close) >= 30:
            s = pd.Series(close).rolling(20).mean()
            ma20_prev = float(s.iloc[-11]) if not np.isnan(s.iloc[-11]) else ma20_now
        ma20_slope = (ma20_now - ma20_prev) / ma20_prev if ma20_prev > 0 else 0.0

        # ── 子评分（0-100）──
        def _cap(x):
            return max(0.0, min(100.0, x))

        # 放量强度 25%
        vs = _cap(100 * np.tanh((vol_expansion - 1.3) * 1.2) * 1.2)
        # 涨停强度 20%（涨停次数 + 连板）
        lu_s = _cap(30 + min(limit_up_count, 6) * 8 + min(max_consec_lu, 4) * 5)
        # 区间涨幅 15%（15%~80% 线性）
        amp_s = _cap((amplitude - 0.15) / 0.65 * 100) if amplitude < 0.80 else 80.0
        # 突破质量 20%
        brk_s = 100.0 if break_through else 50.0
        # 趋势强度 20%
        trend_s = _cap(50 + ma20_slope / 0.03 * 50)

        subs = {
            'vol_expansion': vs,
            'limit_up': lu_s,
            'amplitude': amp_s,
            'breakout_quality': brk_s,
            'trend': trend_s,
        }
        w = rl_cfg.get('weights', {})
        score = (vs * w.get('vol_expansion', 0.25) +
                 lu_s * w.get('limit_up', 0.20) +
                 amp_s * w.get('amplitude', 0.15) +
                 brk_s * w.get('breakout_quality', 0.20) +
                 trend_s * w.get('trend', 0.20))

        return {
            'score': _cap(score),
            'subs': {k: round(v, 1) for k, v in subs.items()},
            'amplitude': amplitude,
            'vol_expansion': vol_expansion,
            'limit_up_count': limit_up_count,
            'max_consec_lu': max_consec_lu,
            'high_idx': high_idx,
            'low_idx': low_idx,
            'pre_high': pre_high,
            'platform_low': low_price,
        }

    # ──────────────────────────────────────────────
    # 阶段B: 回调质量
    # ──────────────────────────────────────────────
    def _compute_pullback(self, close, open_vals, high, low, vol, pct_chg,
                          high_idx, n, ma20=None, platform_low=None):
        """回调质量 0-100 + Distribution penalty 判定。"""
        pb_cfg = self.cfg.get('pullback', {})
        dd_opt_min = pb_cfg.get('drawdown_min', 0.05)
        dd_opt_max = pb_cfg.get('drawdown_max', 0.18)
        days_min = pb_cfg.get('min_days', 3)
        days_max = pb_cfg.get('max_days', 12)

        high_price = float(close[high_idx])
        last_close = float(close[-1])
        drawdown = (high_price - last_close) / high_price if high_price > 0 else 0.0
        days = n - 1 - high_idx

        pullback_low = float(np.min(low[high_idx + 1:n])) if high_idx < n - 1 else last_close

        # 缩量程度：回调期均量 vs 拉升期均量；以及回调期内量能是否递减
        shrink_score = 50.0
        if vol is not None and high_idx > 0 and high_idx < n - 1:
            rally_vol = np.mean(vol[max(0, high_idx - 5):high_idx + 1])
            pb_vol = np.mean(vol[high_idx + 1:n])
            if rally_vol > 0:
                ratio = pb_vol / rally_vol
                shrink_score = max(0.0, min(100.0, 100 * (1 - ratio * 0.8)))
            # 回调期后半段 vs 前半段（量能递减）
            seg = vol[high_idx + 1:n]
            if len(seg) >= 4:
                half = len(seg) // 2
                front = np.mean(seg[:half]) if half > 0 else 0
                back = np.mean(seg[half:]) if len(seg) - half > 0 else 0
                if front > 0:
                    decl = (front - back) / front
                    if decl > 0.1:
                        shrink_score = min(100.0, shrink_score + 15)

        # 结构完整度：未跌破启动平台 + MA20 未明显破坏
        struct_score = 100.0
        if platform_low and platform_low > 0:
            if last_close < platform_low:
                struct_score -= 40
            elif last_close < platform_low * 1.02:
                struct_score -= 15
        if ma20 and ma20 > 0:
            if last_close < ma20:
                struct_score -= 25
            elif last_close < ma20 * 0.98:
                struct_score -= 45

        # 支撑有效性：VWAP/MA20 附近承接
        support_score = 60.0
        if ma20 and ma20 > 0:
            dist_ma20 = (last_close - ma20) / ma20
            if -0.01 <= dist_ma20 <= 0.05:
                support_score = 100.0
            elif -0.04 <= dist_ma20 <= 0.09:
                support_score = 75.0

        # K线承接：回调末端出现低开高走/低开阳线/长下影
        candle_score = 40.0
        for i in range(max(0, n - 5), n):
            if i < 1 or i >= n:
                continue
            o, c, l, h = open_vals[i], close[i], low[i], high[i]
            prev_c = close[i - 1]
            if prev_c > 0 and c > o and (prev_c - o) / prev_c >= 0.003:
                candle_score = max(candle_score, 80.0)
                if (c - o) / o >= 0.01:
                    candle_score = max(candle_score, 100.0)
            if prev_c > 0 and c < o and (o - c) / prev_c >= 0.04:  # 大阴线
                candle_score = min(candle_score, 30.0)

        # ── 回撤幅度分 / 回调时间分 ──
        if dd_opt_min <= drawdown <= dd_opt_max:
            dd_s = 100.0
        elif drawdown < dd_opt_min:
            dd_s = 50.0
        else:
            dd_s = max(0.0, 100 - (drawdown - dd_opt_max) / 0.10 * 100)  # 18%以上逐步减分
        if days_min <= days <= days_max:
            day_s = 100.0
        elif days < days_min:
            day_s = 55.0
        else:
            day_s = max(30.0, 100 - (days - days_max) * 5)

        drawdown_ok = (dd_opt_min <= drawdown <= 0.25 and days_min <= days)

        subs = {
            'drawdown': dd_s,
            'days': day_s,
            'shrink': shrink_score,
            'structure': struct_score,
            'support': support_score,
            'candle': candle_score,
        }
        w = pb_cfg.get('weights', {})
        score = (dd_s * w.get('drawdown', 0.20) +
                 day_s * w.get('days', 0.15) +
                 shrink_score * w.get('shrink', 0.20) +
                 struct_score * w.get('structure', 0.20) +
                 support_score * w.get('support', 0.15) +
                 candle_score * w.get('candle', 0.10))

        # ── Distribution Penalty ──
        penalty = 0.0
        if vol is not None and pct_chg is not None and high_idx < n - 1:
            seg_pct = pct_chg[high_idx + 1:n]
            seg_vol = vol[high_idx + 1:n]
            # 量能基线=拉升期均量（回调前正常量能），避免回调缩量拉低基线造成放量误判
            rally_vol_base = float(np.mean(vol[max(0, high_idx - 5):high_idx + 1])) if high_idx >= 0 else 0.0
            if rally_vol_base <= 0:
                rally_vol_base = float(np.mean(vol)) if len(vol) else 1.0
            # 连续2日以上放量下跌
            down_big = 0
            for j in range(len(seg_pct)):
                if seg_pct[j] <= -3 and (seg_vol[j] / rally_vol_base if rally_vol_base > 0 else 0) >= 1.3:
                    down_big += 1
                else:
                    down_big = 0
                if down_big >= 2:
                    penalty += 15
                    break
            # 回调期间成交量持续放大（后半段量高于前半段）
            if len(seg_vol) >= 4:
                half = len(seg_vol) // 2
                front = np.mean(seg_vol[:half])
                back = np.mean(seg_vol[half:])
                if front > 0 and back / front > 1.4:
                    penalty += 15
            # 连续大阴线（相邻2根跌幅>=3%，中间间隔不计数）
            consec_red = sum(1 for p1, p2 in zip(seg_pct, seg_pct[1:]) if p1 <= -3 and p2 <= -3)
            if consec_red >= 1:
                penalty += 20
            # 高位巨量长阴：最高点当日 或 最高点后2日内放量暴跌（相对拉升期均量）
            for j in range(high_idx, min(high_idx + 3, n - 1)):
                if pct_chg[j] <= -5 and (vol[j] / rally_vol_base if rally_vol_base > 0 else 0) >= 2.0:
                    penalty += 25
                    break
        # 跌破启动平台
        if platform_low and platform_low > 0 and last_close < platform_low:
            penalty += 20
        # 跌破MA20且无法收回
        if ma20 and ma20 > 0 and last_close < ma20 and n >= 2 and close[-2] < ma20:
            penalty += 15

        return {
            'score': max(0.0, min(100.0, score)),
            'subs': {k: round(v, 1) for k, v in subs.items()},
            'drawdown': drawdown,
            'days': days,
            'distribution_penalty': penalty,
            'pullback_low': pullback_low,
            'drawdown_ok': drawdown_ok,
        }

    # ──────────────────────────────────────────────
    # 阶段C: 右侧确认
    # ──────────────────────────────────────────────
    def _compute_confirm(self, close, open_vals, high, low, vol, pct_chg, n,
                         ma5=None, ma10=None, ma20=None, vwap_series=None,
                         pullback_score=0.0, stock_theme="", market_env=None):
        """右侧确认评分 0-100 + 确认信号列表。"""
        market_env = market_env or {}
        signals = []
        confirm_cfg = self.cfg.get('confirm', {})

        last_close = float(close[-1])
        today_open = float(open_vals[-1])
        today_high = float(high[-1])
        today_low = float(low[-1])
        prev_close = float(close[-2]) if n >= 2 else last_close

        # 回调平台高点（最近3-5日不含今日的最高价 → 突破位）
        window = min(5, n - 1)
        platform_high = float(np.max(high[n - 1 - window:n - 1])) if n - 1 > 0 else last_close

        # ── C1 低开阳线确认 (+8) ──
        open_gap = (prev_close - today_open) / prev_close if prev_close > 0 else 0.0
        body_pct = (last_close - today_open) / today_open if today_open > 0 else 0.0
        close_pos = (last_close - today_low) / (today_high - today_low) if today_high > today_low else 0.5
        c1 = open_gap >= 0.005 and last_close > today_open and body_pct >= 0.01 and close_pos >= 0.60
        if c1:
            signals.append('低开阳线')

        # ── C2 回调平台突破 (+15) ──
        c2 = last_close > platform_high
        if c2:
            signals.append('回调平台突破')

        # ── C3 突破放量 (+15, 量比>=2 再+5；巨量长上影 -10) ──
        vol_ratio = 1.0
        if vol is not None and n > 6:
            ma5_vol = np.mean(vol[-6:-1])
            if ma5_vol > 0:
                vol_ratio = float(vol[-1] / ma5_vol)
        c3 = vol_ratio >= 1.3 and last_close > platform_high
        if c3:
            signals.append('放量突破')
        elif c2 and vol_ratio >= 1.0:
            signals.append('温和放量突破')
        # 巨量出货识别
        upper_shadow = (today_high - last_close) / (today_high - today_low) if today_high > today_low else 0.0
        if vol_ratio > 3.0 and close_pos < 0.60:
            signals.append('巨量滞涨(出货)')

        # ── C4 MA5 重新拐头 (+8) ──
        ma5_prev = None
        if n >= 6:
            s5 = pd.Series(close).rolling(5).mean()
            ma5_prev = float(s5.iloc[-2]) if not np.isnan(s5.iloc[-2]) else None
        c4 = ma5 is not None and ma5_prev is not None and ma5 > ma5_prev and last_close > ma5
        if c4:
            signals.append('MA5拐头')

        # ── C5 短期高点突破 (+10/+15/+20) ──
        if last_close > float(np.max(high[max(0, n - 5):n - 1])):
            signals.append('5日高点突破')
        elif last_close > float(np.max(high[max(0, n - 10):n - 1])):
            signals.append('10日高点突破')

        # ── C6 VWAP 确认 (+8) ──
        if vwap_series is not None and len(vwap_series) >= 2:
            vwap_now = float(vwap_series[-1])
            vwap_prev = float(vwap_series[-2])
            c6 = last_close > vwap_now and vwap_now >= vwap_prev
            if c6:
                signals.append('VWAP站回')

        # ── C7 量价结构：缩量回调→放量上涨 (+12) ──
        c7 = self._is_shrink_fall_then_expand_rise(close, vol, n)
        if c7:
            signals.append('缩量回调→放量上涨')

        # ── C8 主题共振 ──
        theme_score, theme_extra = self._theme_score(stock_theme, market_env)
        if theme_extra:
            signals.append(theme_extra)

        # 去重
        seen = set()
        unique_signals = []
        for s in signals:
            if s not in seen:
                seen.add(s)
                unique_signals.append(s)
        signals = unique_signals

        # ── 六维度加权（0-100）──
        # 1) 回踩质量 30%: pullback_score
        # 2) 突破确认 25%: 平台突破+放量突破
        brk_s = 0.0
        if c2:
            brk_s += 55
        if c3:
            brk_s += 45
        elif vol_ratio >= 1.0 and c2:
            brk_s += 20
        brk_s = min(100.0, brk_s)
        # 3) 量价确认 20%: C7 + 量能健康
        vol_ok_s = 50.0
        if c7:
            vol_ok_s = 100.0
        elif 0.8 <= vol_ratio <= 2.0 and last_close > today_open:
            vol_ok_s = 75.0
        # 4) 趋势恢复 10%: C1 + C4
        trend_s = 0.0
        if c1:
            trend_s += 50
        if c4:
            trend_s += 50
        trend_s = min(100.0, trend_s)
        # 5) VWAP/均线 5%: C6 + close>ma5/ma10
        ma_s = 40.0
        if c6:
            ma_s += 40
        if ma5 and last_close > ma5:
            ma_s += 10
        if ma10 and last_close > ma10:
            ma_s += 10
        ma_s = min(100.0, ma_s)
        # 6) 主题共振 10%
        theme_s = theme_score

        w = confirm_cfg.get('weights', {})
        score = (pullback_score * w.get('pullback', 0.30) +
                 brk_s * w.get('breakout', 0.25) +
                 vol_ok_s * w.get('volume', 0.20) +
                 trend_s * w.get('trend', 0.10) +
                 ma_s * w.get('ma', 0.05) +
                 theme_s * w.get('theme', 0.10))

        return {
            'score': max(0.0, min(100.0, score)),
            'subs': {
                'pullback': round(pullback_score, 1),
                'breakout': round(brk_s, 1),
                'volume': round(vol_ok_s, 1),
                'trend': round(trend_s, 1),
                'ma': round(ma_s, 1),
                'theme': round(theme_s, 1),
            },
            'signals': signals,
            'count': len(signals),
            'theme_score': theme_s,
            'platform_high': platform_high,
            'vol_ratio': vol_ratio,
            'close_pos': close_pos,
            'c2': c2,
            'c3': c3,
            'upper_shadow': upper_shadow,
        }

    # ──────────────────────────────────────────────
    # 主题共振评分
    # ──────────────────────────────────────────────
    def _theme_score(self, stock_theme: str, market_env: dict):
        """主题共振 0-100 + C8 附加标签。
        theme_scores: {主题名: 0-100}（来自 ThemeBeta/ThemeResonance）
        top_themes: 当前主线名单（前3为主线，其余次主线）
        """
        theme_scores = market_env.get('theme_scores', {}) or {}
        top_themes = market_env.get('top_themes', []) or []
        if not stock_theme:
            return 50.0, ''

        # 主线/次主线判定
        in_top = False
        in_top3 = False
        for i, t in enumerate(top_themes):
            tname = t.get('name', '') if isinstance(t, dict) else t
            if tname == stock_theme:
                in_top = True
                in_top3 = i < 3
                break

        # 主题强度分（优先用 theme_scores）
        strength = theme_scores.get(stock_theme, None)
        if strength is not None:
            base = float(strength)
        else:
            base = 50.0

        score = base
        label = ''
        if in_top3:
            score = min(100.0, base * 0.6 + 45)
            label = '主题共振(主线)'
        elif in_top:
            score = min(100.0, base * 0.7 + 25)
            label = '主题共振(次主线)'
        elif strength is not None and strength >= 60:
            label = '主题共振(强势主题)'
        elif strength is not None and strength < 45:
            score = max(0.0, base - 15)
            label = '主题共振(弱)'
        return max(0.0, min(100.0, score)), label

    # ──────────────────────────────────────────────
    # 阶段E: 信号分级
    # ──────────────────────────────────────────────
    def _grade(self, result, confirm, pb, market_env, env_tier, close, high, vol,
               ma20=None, pullback_ok=False):
        """结合环境阈值分级。返回 {level, label, no_chase, false_breakout, distribution_breakout}"""
        last_close = float(close[-1])
        platform_high = confirm['platform_high']
        vol_ratio = confirm['vol_ratio']
        close_pos = confirm['close_pos']
        upper_shadow = confirm['upper_shadow']

        out = {'level': 'D', 'label': 'D｜PASS', 'no_chase': False,
               'false_breakout': False, 'distribution_breakout': False}

        # 突破失败识别
        if platform_high > 0:
            th = float(high[-1])
            if th > platform_high and last_close <= platform_high:
                out['false_breakout'] = True
            if (th > platform_high and last_close <= platform_high
                    and vol_ratio >= 2.0 and upper_shadow >= 0.4):
                out['distribution_breakout'] = True

        # 阈值（环境调节）
        env_thresholds = {
            'strong':   {'buy': 70},
            'recovery': {'buy': 75},
            'neutral':  {'buy': 80},
            'weak':     {'buy': 999},
        }
        buy_thr = env_thresholds.get(env_tier, {}).get('buy', 75)

        # 基础分级（先按绝对值，再按环境约束修正）
        fs = result.final_score
        cs = result.confirm_score
        ls = result.launch_score
        ps = result.pullback_score

        # S 级：RIGHT_BREAKOUT_BUY
        is_s = (fs >= 85 and cs >= 80 and confirm['c2'] and vol_ratio >= 1.3
                and not out['false_breakout'] and not out['distribution_breakout']
                and not result.distribution_risk)
        # A 级：CONFIRM_BUY（需 >= 2 个确认信号；环境阈值；recovery 需趋势确认；neutral 需放量突破）
        is_a = (fs >= 78 and cs >= 70 and confirm['count'] >= 2
                and not out['false_breakout'] and not out['distribution_breakout']
                and not result.distribution_risk)
        # 环境约束
        if env_tier == 'recovery':
            # 必须趋势确认（MA5 拐头 / 平台突破），禁止单纯低开阳线
            has_trend = confirm['c2'] or ('MA5拐头' in confirm['signals'])
            if not has_trend:
                is_a = False
        if env_tier == 'neutral':
            # 必须有放量突破
            if not (confirm['c2'] and vol_ratio >= 1.3):
                is_a = False
        if env_tier == 'weak':
            is_s = False
            is_a = False

        # 环境阈值过滤（FinalScore 必须达到当前环境 BUY 阈值）
        if fs < buy_thr:
            is_s = False
            is_a = False

        if is_s:
            out.update(level='S', label='S｜右侧突破买')
            return out
        if is_a:
            out.update(level='A', label='A｜右侧确认买')
            return out

        # B 级：WATCH（启动+回调达标，但右侧确认不足）
        if fs >= 70 and not result.distribution_risk:
            out.update(level='B', label='B｜等待确认')
            return out

        # C 级：PULLBACK_WATCH（回踩质量高，但还没突破）
        if ps >= 75 and pullback_ok and not result.distribution_risk:
            out.update(level='C', label='C｜回踩观察')
            return out

        # D 级：PASS
        out.update(level='D', label='D｜PASS')
        return out

    # ──────────────────────────────────────────────
    # 阶段F: 交易计划
    # ──────────────────────────────────────────────
    def _build_plan(self, close, high, low, vol, n, atr_val,
                    pullback_low, ma20=None, platform_high=0.0):
        """ConfirmPrice / SafeEntry / AggressiveEntry / StopLoss / TP1 / TP2 / 盈亏比"""
        last_close = float(close[-1])

        # ConfirmPrice = 回调平台高点（最近3-5日高点）
        confirm_price = platform_high if platform_high > 0 else last_close
        # SafeEntry = 突破后第一次回踩确认位（突破位附近）
        safe_entry = confirm_price * 1.00
        # AggressiveEntry = 突破当日直接参与（仅已放量突破时按当前价；未突破时为等待突破位，不追价）
        aggressive_entry = last_close if (platform_high > 0 and last_close > platform_high) else confirm_price

        # 结构止损：优先 回调低点 / MA20 支撑（取较高者），略压低 2%
        structural_support = 0.0
        if pullback_low > 0:
            structural_support = pullback_low
        if ma20 and ma20 > structural_support:
            structural_support = ma20
        structural_stop = structural_support * 0.98 if structural_support > 0 else last_close * 0.92

        # ATR 止损
        entry_ref = last_close
        atr_stop = entry_ref - atr_val * 1.1 if atr_val > 0 else entry_ref * 0.92

        # 最终止损 = max(结构止损, ATR合理止损)（取更紧者）
        stop_loss = max(structural_stop, atr_stop)
        risk = entry_ref - stop_loss
        risk_pct = risk / entry_ref if entry_ref > 0 else 0.0

        # 止盈 R-Multiple
        tp1 = entry_ref + 2 * risk if risk > 0 else entry_ref * 1.10
        tp2 = entry_ref + 3 * risk if risk > 0 else entry_ref * 1.15
        # 结合前高动态：TP2 不高于前高太远
        pre_high = float(np.max(high)) if len(high) else entry_ref
        if tp2 > pre_high * 1.15:
            tp2 = max(tp1, pre_high * 1.12)

        r_multiple = (tp1 - entry_ref) / risk if risk > 0 else 0.0

        return {
            'entry': round(entry_ref, 2),
            'confirm_price': round(confirm_price, 2),
            'safe_entry': round(safe_entry, 2),
            'aggressive_entry': round(aggressive_entry, 2),
            'stop_loss': round(stop_loss, 2),
            'tp1': round(tp1, 2),
            'tp2': round(tp2, 2),
            'risk_pct': round(risk_pct, 4),
            'r_multiple': round(r_multiple, 1),
        }

    # ──────────────────────────────────────────────
    # 工具
    # ──────────────────────────────────────────────
    def _is_shrink_fall_then_expand_rise(self, close, vol, n):
        """缩量回调→放量上涨 结构检测"""
        if vol is None or n < 8:
            return False
        # 近6日中：下跌日缩量（量比<0.9）且今日放量上涨
        ma5_vol = np.mean(vol[-6:-1])
        if ma5_vol <= 0:
            return False
        today_ratio = float(vol[-1] / ma5_vol)
        up = float(close[-1]) > float(close[-2])
        # 检查最近一次回调段（连续下跌段）量能是否低于前20日均量
        shrink_ok = False
        i = n - 2
        down_days = []
        while i >= 0 and float(close[i]) < float(close[i - 1]):
            down_days.append(i)
            i -= 1
        if down_days and i >= 0:
            down_vol_mean = np.mean(vol[down_days])
            pre_vol = np.mean(vol[max(0, i - 20):i]) if i > 0 else ma5_vol
            if pre_vol > 0:
                shrink_ok = down_vol_mean / pre_vol < 1.0
        return up and today_ratio >= 1.3 and shrink_ok

    @staticmethod
    def _calc_vwap(high, low, close, vol):
        """日线 VWAP 序列：典型价×量 加权累计"""
        if vol is None or len(vol) == 0:
            return None
        typical = (np.array(high) + np.array(low) + np.array(close)) / 3.0
        cum_pv = np.cumsum(typical * vol)
        cum_v = np.cumsum(vol)
        vwap = np.where(cum_v > 0, cum_pv / np.where(cum_v > 0, cum_v, 1), 0)
        return vwap

    @staticmethod
    def _calc_atr(close, high, low, period=14):
        """简易ATR计算"""
        n = len(close)
        if n < period + 1:
            return 0
        tr = []
        for i in range(1, n):
            tr.append(max(high[i] - low[i],
                          abs(high[i] - close[i - 1]),
                          abs(low[i] - close[i - 1])))
        return float(np.mean(tr[-period:])) if tr else 0

    def _build_t1_advice(self, result, last_close):
        """T+1 确认建议：突破真伪 → 回踩不破再介入"""
        if result.signal_level in ('S', 'A'):
            if result.confirm_price > 0:
                return (f"T+1 若小幅回踩不破 {result.confirm_price:.2f}（突破位）后重新走强，"
                        f"优先级高于追突破；若放量长上影跌破突破位，放弃")
        return ""

    def _build_summary(self, result):
        """一句话总结"""
        level_map = {
            'S': '前期启动有效，回调健康，已放量突破并强收盘，右侧确认成立，可参与',
            'A': '前期启动有效，回调健康，正在右侧确认，突破并放量后才正式买入',
            'B': '启动/回调尚可，但右侧确认不足，等待突破信号',
            'C': '回踩结构健康但尚未突破，仅观察，不是买点',
            'D': 'Distribution风险/退潮/破位，回避',
        }
        return level_map.get(result.signal_level, '')

    # ──────────────────────────────────────────────
    # 批量
    # ──────────────────────────────────────────────
    def detect_batch(self, ts_codes: list, trade_date: str,
                     market_env: dict = None, theme_of: dict = None) -> List[RightConfirmResult]:
        """批量检测

        Args:
            theme_of: {ts_code: 主题名}
        """
        theme_of = theme_of or {}
        results = []
        for code in ts_codes:
            try:
                r = self.detect(code, trade_date, market_env=market_env,
                                stock_theme=theme_of.get(code, ''))
                if r is not None and r.is_qualified:
                    results.append(r)
            except Exception as e:
                print(f"  [RCB] {code} 异常: {e}")
        results.sort(key=lambda x: x.final_score, reverse=True)
        return results
