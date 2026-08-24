# -*- coding: utf-8 -*-
"""
最终评分模块

RIB 最终100分评分模型：
  ① 长期下跌背景：10分
  ② 第一波反转：25分
  ③ POST_IMPULSE_BASE：30分
  ④ 第二波突破：15分
  ⑤ 第一次回踩：10分
  ⑥ 再启动：10分

评分等级：
  ≥90: S++++ 核心二波启动
  85~89: S 强二波机会
  80~84: A+ 重点关注
  75~79: A 观察
  70~74: B 等待
  <70: NO_TRADE
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from .config import RIB_CONFIG
from .detectors import (
    BreakoutResult, DowntrendResult, ImpulseResult,
    PostImpulseBaseResult, PullbackResult, ReAccelerationResult,
)


@dataclass
class FinalScore:
    """最终评分结果。"""
    total: float = 0.0
    grade: str = "NO_TRADE"
    stars: int = 0
    is_primary_buy: bool = False
    # 分项分数
    s_downtrend_bg: float = 0.0
    s_impulse: float = 0.0
    s_post_impulse_base: float = 0.0
    s_second_breakout: float = 0.0
    s_first_pullback: float = 0.0
    s_re_acceleration: float = 0.0
    # 诊断
    passed_checks: Dict[str, bool] = None  # type: ignore


class FinalScorer:
    """RIB 最终评分器。"""

    def __init__(self, config: Optional[dict] = None):
        self.cfg = dict(RIB_CONFIG)
        if config:
            self.cfg.update(config)

    def score(
        self,
        downtrend: DowntrendResult,
        impulse: ImpulseResult,
        base: PostImpulseBaseResult,
        breakout: BreakoutResult,
        pullback: PullbackResult,
        reacc: ReAccelerationResult,
        risk_reward: float = 0.0,
        theme_bonus: float = 0.0,
    ) -> FinalScore:
        """计算最终100分评分。"""
        s = FinalScore()
        w = self.cfg.get("weights", {})

        # ── ① 长期下跌背景 (10分) ──
        dw = w.get("downtrend_bg", 10)
        s.s_downtrend_bg = self._score_downtrend_bg(downtrend, dw)

        # ── ② 第一波反转 (25分) ──
        iw = w.get("impulse", 25)
        s.s_impulse = self._score_impulse(impulse, iw)

        # ── ③ POST_IMPULSE_BASE (30分) ──
        pw = w.get("post_impulse_base", 30)
        s.s_post_impulse_base = self._score_post_base(base, pw)

        # ── ④ 第二波突破 (15分) ──
        bw = w.get("second_breakout", 15)
        s.s_second_breakout = self._score_breakout(breakout, bw)

        # ── ⑤ 第一次回踩 (10分) ──
        pw2 = w.get("first_pullback", 10)
        s.s_first_pullback = self._score_pullback(pullback, pw2)

        # ── ⑥ 再启动 (10分) ──
        rw = w.get("re_acceleration", 10)
        s.s_re_acceleration = self._score_reacc(reacc, rw, risk_reward)

        # 主题增强
        total = (s.s_downtrend_bg + s.s_impulse + s.s_post_impulse_base +
                 s.s_second_breakout + s.s_first_pullback + s.s_re_acceleration)
        total += theme_bonus
        s.total = round(min(100.0, max(0.0, total)), 1)

        # 评级
        grades = self.cfg.get("grades", {})
        if s.total >= grades.get("s_plus_plus", 90):
            s.grade = "S++++ 核心二波启动"
            s.stars = 5
        elif s.total >= grades.get("s", 85):
            s.grade = "S 强二波机会"
            s.stars = 4
        elif s.total >= grades.get("a_plus", 80):
            s.grade = "A+ 重点关注"
            s.stars = 3
        elif s.total >= grades.get("a", 75):
            s.grade = "A 观察"
            s.stars = 2
        elif s.total >= grades.get("b", 70):
            s.grade = "B 等待"
            s.stars = 1
        else:
            s.grade = "NO_TRADE"
            s.stars = 0

        # PRIMARY BUY 判定
        s.is_primary_buy = (
            s.total >= grades.get("s", 85) and
            reacc.is_reacceleration and
            pullback.is_pullback and
            breakout.is_breakout and
            base.is_base and
            impulse.is_impulse and
            downtrend.is_downtrend
        )

        # 关键检查项
        s.passed_checks = {
            "Q1_downtrend": downtrend.is_downtrend,
            "Q2_impulse_strong": impulse.is_impulse and impulse.score >= 80,
            "Q3_trend_changed": impulse.is_reversal_confirmed,
            "Q4_base_holds_most": base.retain_ratio >= 0.5,
            "Q5_base_volume_shrink": base.volume_shrink_ratio <= 0.85,
            "Q6_low_stable": base.low_structure != "低点降低",
            "Q7_ma20_up": base.ma20_slope >= 0,
            "Q8_near_impulse_high": breakout.is_breakout,
            "Q9_true_breakout": breakout.is_breakout and not breakout.is_fake_breakout,
            "Q10_healthy_pullback": pullback.is_pullback and pullback.pullback_volume_ratio <= 0.80,
            "Q11_support_held": pullback.support_found,
            "Q12_re_acceleration": reacc.is_reacceleration,
            "Q13_rr_ge_2": risk_reward >= 2.0,
        }

        return s

    def _score_downtrend_bg(self, dt: DowntrendResult, weight: float) -> float:
        """① 长期下跌背景评分。"""
        if not dt.is_downtrend:
            return 0.0
        ratio = min(1.0, dt.score / 100.0)
        return round(weight * ratio, 2)

    def _score_impulse(self, imp: ImpulseResult, weight: float) -> float:
        """② 第一波反转评分。"""
        if not imp.is_impulse:
            return 0.0
        ratio = min(1.0, imp.score / 100.0)
        # 涨幅 (5分)
        ret_score = min(1.0, imp.impulse_return / 0.6) if imp.impulse_return > 0 else 0
        # 速度 (4分)
        days_score = 1.0 if 5 <= imp.impulse_days <= 10 else 0.6 if 3 <= imp.impulse_days < 5 else 0.3
        # 量能 (6分)
        vol_score = min(1.0, imp.volume_ratio / 2.5) if imp.volume_ratio > 0 else 0
        # 突破 (5分)
        break_score = 1.0 if imp.is_reversal_confirmed else 0.4
        # MA改善 (5分)
        ma_score = 1.0 if imp.broke_ma20 and imp.broke_ma60 else 0.5

        detailed = (5 * ret_score + 4 * days_score + 6 * vol_score +
                    5 * break_score + 5 * ma_score) / 25.0
        return round(weight * max(ratio, detailed), 2)

    def _score_post_base(self, base: PostImpulseBaseResult, weight: float) -> float:
        """③ POST_IMPULSE_BASE 评分。"""
        if not base.is_base:
            return 0.0

        # 平台时间 (4分)
        days = base.platform_days
        days_score = 1.0 if 7 <= days <= 15 else 0.7 if 5 <= days <= 20 else 0.3

        # 回撤深度 (6分)
        d = base.pullback_depth
        depth_score = 1.0 if 0.2 <= d <= 0.4 else 0.7 if 0.15 <= d <= 0.5 else 0.3

        # 涨幅保留率 (6分)
        r = base.retain_ratio
        retain_score = 1.0 if r >= 0.7 else 0.7 if r >= 0.6 else 0.4 if r >= 0.5 else 0.1

        # 缩量 (6分)
        vs = base.volume_shrink_ratio
        shrink_score = 1.0 if vs <= 0.7 else 0.7 if vs <= 0.85 else 0.2

        # 高低点结构 (4分)
        structure_score = 0.5
        if base.high_structure in ("高点持平", "高点抬高"):
            structure_score += 0.25
        if base.low_structure in ("低点持平", "低点抬高"):
            structure_score += 0.25

        # MA20改善 (4分)
        ma20_score = 1.0 if base.ma20_slope >= 0 else 0.3

        detailed = (4 * days_score + 6 * depth_score + 6 * retain_score +
                    6 * shrink_score + 4 * structure_score + 4 * ma20_score) / 30.0

        ratio = min(1.0, base.score / 100.0)
        return round(weight * max(ratio, detailed), 2)

    def _score_breakout(self, bo: BreakoutResult, weight: float) -> float:
        """④ 第二波突破评分。"""
        if not bo.is_breakout:
            return 0.0

        # 突破第一波高点 (5分)
        high_score = 1.0

        # 成交量 (4分)
        vol_s = min(1.0, bo.volume_ratio / 2.5) if bo.volume_ratio > 0 else 0

        # 收盘位置 (3分)
        loc_s = bo.close_location

        # K线质量 (3分)
        candle_s = 1.0 if bo.upper_shadow <= 0.15 else 0.6 if bo.upper_shadow <= 0.3 else 0.2

        detailed = (5 * high_score + 4 * vol_s + 3 * loc_s + 3 * candle_s) / 15.0

        ratio = min(1.0, bo.score / 100.0)
        return round(weight * max(ratio, detailed), 2)

    def _score_pullback(self, pb: PullbackResult, weight: float) -> float:
        """⑤ 第一次回踩评分。"""
        if not pb.is_pullback:
            return 0.0

        # 缩量 (4分)
        vol_s = 1.0 if pb.pullback_volume_ratio <= 0.65 else 0.7 if pb.pullback_volume_ratio <= 0.8 else 0.2

        # 关键位承接 (3分)
        support_s = 1.0 if pb.support_found else 0.3

        # 回踩深度 (3分)
        d = pb.pullback_depth
        depth_s = 1.0 if 0.2 <= d <= 0.6 else 0.5 if d <= 0.8 else 0.1

        detailed = (4 * vol_s + 3 * support_s + 3 * depth_s) / 10.0
        ratio = min(1.0, pb.score / 100.0) if pb.score > 0 else detailed
        return round(weight * ratio, 2)

    def _score_reacc(self, ra: ReAccelerationResult, weight: float,
                     risk_reward: float) -> float:
        """⑥ 再启动评分。"""
        if not ra.is_reacceleration:
            return 0.0

        # 重新转强 (4分)
        reacc_s = 1.0 if ra.ma5_slope_up and ra.break_pullback_high else 0.5

        # 量能恢复 (2分)
        vol_s = min(1.0, ra.volume_ratio / 2.0) if ra.volume_ratio > 0 else 0.5

        # 分时强度 (2分)
        loc_s = ra.close_location

        # 盈亏比 (2分)
        rr_s = 1.0 if risk_reward >= 3.0 else 0.7 if risk_reward >= 2.0 else 0.3

        detailed = (4 * reacc_s + 2 * vol_s + 2 * loc_s + 2 * rr_s) / 10.0
        ratio = min(1.0, ra.score / 100.0) if ra.score > 0 else detailed
        return round(weight * max(ratio, detailed), 2)
