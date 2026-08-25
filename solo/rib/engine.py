# -*- coding: utf-8 -*-
"""
RIB 核心引擎 - 状态机驱动的六阶段形态识别

完整交易路径：
  DOWNTREND → REVERSAL_SETUP → IMPULSE_START → IMPULSE_ACTIVE
  → IMPULSE_PEAK → POST_IMPULSE_BASE → PRE_BREAKOUT → SECOND_LEG_BREAKOUT
  → FIRST_PULLBACK → PULLBACK_SUPPORT → RE_ACCELERATION → ★ PRIMARY BUY

铁律：
  1. 没有长期下跌识别，不允许进入反转识别
  2. 没有第一波反转确认，不允许识别平台
  3. 没有高质量平台，不允许识别突破
  4. 没有第二波突破，不允许识别回踩
  5. 没有第一次健康回踩，不允许给出买点
  6. 没有重新转强，不允许 PRIMARY BUY

防未来函数：只接收已截断到 T 日的 DataFrame。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .config import (
    RIB_CONFIG, STATE_DOWNTREND, STATE_REVERSAL_SETUP,
    STATE_IMPULSE_START, STATE_IMPULSE_ACTIVE, STATE_IMPULSE_PEAK,
    STATE_POST_IMPULSE_BASE, STATE_PRE_BREAKOUT, STATE_SECOND_LEG_BREAKOUT,
    STATE_FIRST_PULLBACK, STATE_PULLBACK_SUPPORT, STATE_RE_ACCELERATION,
    STATE_PRIMARY_BUY, STATE_HOLD, STATE_EXIT, STATE_INVALIDATED,
    STATE_FAILED_REVERSAL, STATE_FAILED_BREAKOUT, STATE_FAILED_PULLBACK,
)
from .indicators import enrich
from .state_machine import RIBState, StateMachine
from .detectors import (
    DowntrendDetector, ImpulseDetector, ImpulsePeakDetector,
    PostImpulseBaseDetector, PreBreakoutDetector,
    SecondLegBreakoutDetector, FirstPullbackDetector,
    ReAccelerationDetector,
    DowntrendResult, ImpulseResult, ImpulsePeakResult,
    PostImpulseBaseResult, BreakoutResult, PullbackResult,
    ReAccelerationResult, PreBreakoutResult,
)
from .scoring import FinalScorer, FinalScore
from .filters import (
    MarketFilter, ThemeFilter, RiskRewardEngine, VetoChecker,
    MarketSnapshot, ThemeInfo, TradePlan,
)
from .v2_next_state import NextStateAnalyzer, classify_distance_to_breakout
from .v2_readiness import ReadinessEngine, assign_tier
from .v2_structure_risk import StructureRiskEngine


@dataclass
class RIBResult:
    """RIB 完整识别结果。"""
    # ── 基础 ──
    ts_code: str = ""
    name: str = ""
    date: str = ""
    industry: str = ""
    close: float = 0.0
    state: str = STATE_DOWNTREND
    is_valid: bool = True
    veto_triggered: List[str] = field(default_factory=list)

    # ── 状态机摘要 ──
    state_sequence: List[str] = field(default_factory=list)

    # ── 各阶段结果 ──
    downtrend: Optional[DowntrendResult] = None
    impulse: Optional[ImpulseResult] = None
    peak: Optional[ImpulsePeakResult] = None
    base: Optional[PostImpulseBaseResult] = None
    breakout: Optional[BreakoutResult] = None
    pullback: Optional[PullbackResult] = None
    reacc: Optional[ReAccelerationResult] = None

    # ── 评分与交易计划 ──
    final_score: Optional[FinalScore] = None
    trade_plan: Optional[TradePlan] = None
    risk_reward: float = 0.0
    theme_bonus: float = 0.0

    # ── 结论 ──
    conclusion: str = ""  # 为什么能买/不能买
    q_checks: Dict[str, bool] = field(default_factory=dict)

    # ── 市场环境 ──
    market_regime: str = "normal"

    # ── V2.0/V2.1 新增 ──
    memory: dict = field(default_factory=dict)          # 历史记忆(§3)
    buy_readiness: float = 0.0                          # BUY_READINESS(§23)
    readiness_cap: float = 100.0
    next_state: str = ""                                # NEXT_STATE(§21)
    next_state_score: float = 0.0
    next_state_level: str = "NOT_READY"                 # V2.1 §22 分级
    next_state_gap_price: float = 0.0
    next_state_gap_atr: float = 0.0
    next_state_gap_desc: str = ""
    next_state_trigger: str = ""
    structure_risk: float = 0.0                         # STRUCTURE_RISK(§34)
    structure_risk_items: List[str] = field(default_factory=list)
    distance_to_breakout: str = ""                      # DISTANCE_TO_BREAKOUT(§11 五档)
    distance_to_breakout_atr: float = 0.0
    pool_tier: str = "IGNORE"                           # NOW/NEXT/WATCH(§24~28)
    next_rule: str = ""                                 # V2.1 入选NEXT的规则(A/B/C/D)
    priority_score: float = 0.0                         # PriorityScore(§37)
    cannot_buy_reason: str = ""                         # 为什么现在不能买(§39)
    base_quality: float = 0.0                           # BASE_QUALITY(§8)
    structure_quality: float = 100.0                    # 100 - STRUCTURE_RISK
    pre_breakout: Optional[PreBreakoutResult] = None    # V2.1 §9~13
    prob_level: str = ""                                # V2.1 §30 结构概率等级


class RIBEngine:
    """RIB 核心引擎。"""

    def __init__(self, config: Optional[dict] = None):
        self.cfg = dict(RIB_CONFIG)
        if config:
            self.cfg.update(config)

        # 初始化检测器
        self.downtrend_detector = DowntrendDetector(self.cfg.get("downtrend"))
        self.impulse_detector = ImpulseDetector(self.cfg.get("impulse"))
        self.peak_detector = ImpulsePeakDetector()
        self.base_detector = PostImpulseBaseDetector(self.cfg.get("post_impulse_base"))
        self.pre_breakout_detector = PreBreakoutDetector()
        self.breakout_detector = SecondLegBreakoutDetector(self.cfg.get("breakout"))
        self.pullback_detector = FirstPullbackDetector(self.cfg.get("pullback"))
        self.reacc_detector = ReAccelerationDetector(self.cfg.get("re_acceleration"))

        # 评分器
        self.scorer = FinalScorer(self.cfg)

        # 过滤器
        self.market_filter = MarketFilter(self.cfg.get("market_filter"))
        self.theme_filter = ThemeFilter(self.cfg.get("theme"))
        self.rr_engine = RiskRewardEngine(self.cfg.get("rr"))
        self.veto_checker = VetoChecker(self.cfg.get("veto"))

        # V2.0 引擎
        self.next_state_analyzer = NextStateAnalyzer(self.cfg)
        self.readiness_engine = ReadinessEngine(self.cfg)
        self.structure_risk_engine = StructureRiskEngine(self.cfg)

    def analyze(
        self,
        df: pd.DataFrame,
        ts_code: str = "",
        name: str = "",
        industry: str = "",
        market_snapshot: Optional[MarketSnapshot] = None,
        theme_info: Optional[ThemeInfo] = None,
    ) -> RIBResult:
        """对单只股票执行完整的 RIB 形态分析。

        采用反向扫描策略：
        1. 先从最新K线往回扫描，找到最近的第一波（反转）
        2. 用第一波启动点验证其之前的下跌结构
        3. 依次验证第一波高点、平台、突破、回踩、二次启动

        这样可以避免"截至最新K线，MA结构已经改变"的问题。

        Returns:
            RIBResult 完整识别结果
        """
        result = RIBResult(ts_code=ts_code, name=name, industry=industry)
        sm = StateMachine(ts_code, name)

        n = len(df)
        min_bars = self.cfg.get("min_bars", 130)

        if n < min_bars:
            result.conclusion = f"K线不足{min_bars}根，无法评估"
            result.state = STATE_INVALIDATED
            result.is_valid = False
            result.state_sequence = sm.state_sequence
            return result

        end_idx = n - 1
        df = enrich(df)

        result.date = str(df["trade_date"].iloc[end_idx]) if "trade_date" in df.columns else ""
        result.close = float(df["close"].values[end_idx])
        result.market_regime = market_snapshot.regime if market_snapshot else "normal"

        # ══════════════════════════════════════════════════════════
        # 阶段 0：反向扫描，寻找最近的第一波反转
        # ══════════════════════════════════════════════════════════
        imp = self._scan_for_impulse(df, end_idx)

        if imp is None or not imp.is_impulse:
            result.state = STATE_DOWNTREND
            result.conclusion = (
                "未检测到有效的第一波反转。"
                "RIB 模型需要先有长期下跌后的第一波强势上涨，"
                "当前股票可能仍在下跌趋势中，或第一波反转尚未形成。"
            )
            result.state_sequence = sm.state_sequence
            return self._finalize_v2(result, df, sm)

        result.impulse = imp
        impulse_start_idx = imp.impulse_low_idx
        impulse_peak_idx = imp.impulse_high_idx

        # ══════════════════════════════════════════════════════════
        # 阶段 1：在第一波启动点之前验证长期下跌
        # ══════════════════════════════════════════════════════════
        result.downtrend = self.downtrend_detector.detect(df, impulse_start_idx)
        dt = result.downtrend

        if not dt.is_downtrend:
            result.state = STATE_DOWNTREND
            result.conclusion = (
                f"第一波启动前未检测到长期下降趋势（DOWNTREND_SCORE={dt.score:.0f}）。"
                "RIB 模型要求第一波反转必须发生在长期下跌之后。"
                f"当前第一波涨幅={imp.impulse_return*100:.1f}%，"
                f"但缺乏前置的下降趋势结构。"
            )
            result.state_sequence = sm.state_sequence
            return self._finalize_v2(result, df, sm)

        if dt.score < self.cfg.get("downtrend", {}).get("score_min", 65):
            result.state = STATE_DOWNTREND
            result.conclusion = (
                f"长期下跌存在但强度不足（{dt.score:.0f}<65）。"
                f"第一波启动点({impulse_start_idx})前的下降趋势结构不够清晰。"
            )
            result.state_sequence = sm.state_sequence
            return self._finalize_v2(result, df, sm)

        sm.transition(STATE_REVERSAL_SETUP, f"检测到长期下跌({dt.score:.0f}分)")
        sm.transition(STATE_IMPULSE_START, f"第一波启动({imp.impulse_return*100:.1f}%)")
        sm.transition(STATE_IMPULSE_ACTIVE, f"第一波进行中({imp.impulse_days}日)")

        if imp.is_extreme_acceleration:
            result.veto_triggered.append(
                f"第一波暴涨({imp.impulse_days}日{imp.impulse_return*100:.1f}%)，质量存疑"
            )

        # ══════════════════════════════════════════════════════════
        # 阶段 2：识别第一波高点（基于已知的 impulse）
        # ══════════════════════════════════════════════════════════
        result.peak = self.peak_detector.detect(df, imp, end_idx)
        peak = result.peak

        if not peak.is_peak_valid:
            result.state = STATE_IMPULSE_ACTIVE
            result.conclusion = "第一波高点尚未确认，仍在上涨通道中。"
            result.state_sequence = sm.state_sequence
            return self._finalize_v2(result, df, sm)

        sm.transition(STATE_IMPULSE_PEAK, f"第一波高点确认({peak.peak_price:.2f})")

        # ══════════════════════════════════════════════════════════
        # 阶段 3：识别 POST_IMPULSE_BASE
        # ══════════════════════════════════════════════════════════
        result.base = self.base_detector.detect(df, imp, peak, end_idx)
        base = result.base

        if not base.is_base:
            result.state = STATE_IMPULSE_PEAK
            result.conclusion = "第一波后尚未形成高质量整理平台，可能仍在调整或已反转。"
            result.state_sequence = sm.state_sequence
            return self._finalize_v2(result, df, sm)

        sm.transition(STATE_POST_IMPULSE_BASE,
                       f"POST_IMPULSE_BASE形成({base.platform_days}日, 保留{base.retain_ratio*100:.0f}%)")

        # ══════════════════════════════════════════════════════════
        # 阶段 4：检测预突破
        # ══════════════════════════════════════════════════════════
        pre_bo = self.pre_breakout_detector.detect(df, base, imp, end_idx)
        result.pre_breakout = pre_bo

        # ══════════════════════════════════════════════════════════
        # 阶段 5：检测第二波突破
        # ══════════════════════════════════════════════════════════
        result.breakout = self.breakout_detector.detect(df, imp, base, end_idx)
        bo = result.breakout

        if not bo.is_breakout:
            # V2.1 §10：成熟平台 -> PRE_BREAKOUT；早期平台 -> POST_IMPULSE_BASE
            if pre_bo.is_pre_breakout:
                sm.transition(STATE_PRE_BREAKOUT,
                              f"成熟平台(PB分{pre_bo.score:.0f}/{pre_bo.grade})")
                if pre_bo.expired:
                    result.state = STATE_PRE_BREAKOUT
                    result.conclusion = (
                        f"PRE_BREAKOUT_EXPIRED：平台成熟后{pre_bo.days_at_level}日"
                        f"仍未突破第一波高点({imp.impulse_high:.2f})，"
                        "信号过期，重新评估平台。"
                    )
                else:
                    aged_tag = " PRE_BREAKOUT_AGED" if pre_bo.aged else ""
                    result.state = STATE_PRE_BREAKOUT
                    result.conclusion = (
                        f"平台成熟（{base.platform_days}日，质量{base.score:.0f}，"
                        f"距突破触发{pre_bo.distance_atr:.1f}ATR/{pre_bo.distance_band}）"
                        f"{aged_tag}。等待放量突破{pre_bo.trigger_price:.2f}。"
                    )
            else:
                result.state = STATE_POST_IMPULSE_BASE
                result.conclusion = (
                    f"平台形成中（{base.platform_days}日），尚未成熟到 PRE_BREAKOUT。"
                    f"第一波高点{imp.impulse_high:.2f}未突破。"
                )
            result.state_sequence = sm.state_sequence
            return self._finalize_v2(result, df, sm)

        sm.transition(STATE_SECOND_LEG_BREAKOUT,
                       f"第二波突破({bo.breakout_price:.2f}, 量比{bo.volume_ratio:.2f})")

        if bo.is_fake_breakout:
            sm.fail_breakout("假突破")
            result.state = STATE_FAILED_BREAKOUT
            result.is_valid = False
            result.conclusion = "第二波突破距离过大或质量差，判定为假突破。"
            result.state_sequence = sm.state_sequence
            return self._finalize_v2(result, df, sm)

        # ══════════════════════════════════════════════════════════
        # 阶段 6：检测第一次回踩
        # ══════════════════════════════════════════════════════════
        result.pullback = self.pullback_detector.detect(df, bo, base, imp, end_idx)
        pb = result.pullback

        if not pb.is_pullback:
            # ── 突破信号时效检查：突破后超过回踩窗口(max_days)仍未回踩 → 信号过期 ──
            # 短线模型(3-5日持仓)要求突破后窗口内出现健康回踩；窗口关闭后
            # 仍未回踩，说明最佳介入时机已过（追高或形态周期结束），不再报信号。
            pullback_cfg = self.cfg.get("pullback", {})
            max_wait = pullback_cfg.get("max_days", 5)
            days_since_breakout = end_idx - bo.breakout_idx
            if days_since_breakout > max_wait:
                result.state = STATE_DOWNTREND
                result.is_valid = False
                result.conclusion = (
                    f"突破后{days_since_breakout}日仍未出现健康回踩，"
                    f"突破信号已过期（> {max_wait}日），形态周期结束，等待新一轮形态。"
                )
                result.state_sequence = sm.state_sequence
                return self._finalize_v2(result, df, sm)

            result.state = STATE_SECOND_LEG_BREAKOUT
            result.conclusion = (
                "突破后尚未出现第一次健康回踩。"
                "价格可能继续上行，或正在高位震荡。"
            )
            result.state_sequence = sm.state_sequence
            return self._finalize_v2(result, df, sm)

        sm.transition(STATE_FIRST_PULLBACK,
                       f"第一次回踩({pb.pullback_depth*100:.0f}%, 量比{pb.pullback_volume_ratio:.2f})")

        if pb.fell_back_to_base:
            sm.fail_pullback("跌回平台内部")
            result.state = STATE_FAILED_PULLBACK
            result.is_valid = False
            result.conclusion = "回踩深度过大，跌回平台内部，形态破坏。"
            result.state_sequence = sm.state_sequence
            return self._finalize_v2(result, df, sm)

        # ── V2.1 §18：PULLBACK_SUPPORT 需"缩量+深度有限+收盘站回关键位" ──
        # 回踩发生但支撑尚未确认（放量/深度过大/未站回）-> 停留 FIRST_PULLBACK
        pb_cfg = self.cfg.get("pullback", {})
        vol_ok = pb.pullback_volume_ratio <= pb_cfg.get("volume_ratio_max", 0.80)
        depth_ok = pb.pullback_depth <= 0.60
        reclaim_ok = (not pb.broke_impulse_high) or pb.is_test_and_reclaim
        if not (vol_ok and depth_ok and reclaim_ok):
            result.state = STATE_FIRST_PULLBACK
            fails = []
            if not vol_ok:
                fails.append(f"回踩放量(量比{pb.pullback_volume_ratio:.2f})")
            if not depth_ok:
                fails.append(f"回踩过深({pb.pullback_depth*100:.0f}%)")
            if not reclaim_ok:
                fails.append("收盘未站回关键位")
            result.conclusion = (
                "第一次回踩进行中，尚未确认健康回踩（"
                + "；".join(fails) + "）。等待关键位承接确认。"
            )
            result.state_sequence = sm.state_sequence
            return self._finalize_v2(result, df, sm)

        sm.transition(STATE_PULLBACK_SUPPORT, "回踩关键位承接")

        # ══════════════════════════════════════════════════════════
        # 阶段 7：检测二次启动
        # ══════════════════════════════════════════════════════════
        result.reacc = self.reacc_detector.detect(df, pb, bo, imp, end_idx)
        ra = result.reacc

        if not ra.is_reacceleration:
            result.state = STATE_PULLBACK_SUPPORT
            result.conclusion = (
                f"回踩后尚未重新转强。"
                f"MA5>MA10={'是' if ra.ma5 and ra.ma10 and ra.ma5 > ra.ma10 else '否'}，"
                f"量比={ra.volume_ratio:.2f}。"
                "等待放量阳线确认。"
            )
            result.state_sequence = sm.state_sequence
            return self._finalize_v2(result, df, sm)

        sm.transition(STATE_RE_ACCELERATION,
                       f"二次启动({ra.reacc_price:.2f}, 量比{ra.volume_ratio:.2f})")

        # ══════════════════════════════════════════════════════════
        # 盈亏比计算
        # ══════════════════════════════════════════════════════════
        atr_val = float(df["atr20"].values[end_idx]) if "atr20" in df.columns else 0
        result.trade_plan = self.rr_engine.compute(ra.reacc_price, atr_val, pb.pullback_low)
        result.risk_reward = result.trade_plan.risk_reward

        # ══════════════════════════════════════════════════════════
        # 主题增强
        # ══════════════════════════════════════════════════════════
        if theme_info:
            result.theme_bonus = self.theme_filter.compute_bonus(theme_info)

        # ══════════════════════════════════════════════════════════
        # 强制否决检查
        # ══════════════════════════════════════════════════════════
        market_is_bear = result.market_regime == "bear"
        is_vetoed, veto_reasons = self.veto_checker.check(
            impulse_return=imp.impulse_return,
            impulse_volume_ratio=imp.volume_ratio,
            trend_changed=imp.is_reversal_confirmed,
            pullback_depth=base.pullback_depth,
            is_volume_plunge=base.is_volume_plunge,
            is_back_to_origin=base.is_back_to_origin,
            ma20_down=base.ma20_slope < 0 if base.ma20_slope else True,
            is_fake_breakout=bo.is_fake_breakout,
            fell_back_to_base=pb.fell_back_to_base,
            pullback_volume_high=pb.pullback_volume_ratio > 1.0,
            distance_atr=bo.breakout_distance_atr,
            risk_reward=result.risk_reward,
            market_is_bear=market_is_bear,
        )

        if is_vetoed:
            result.veto_triggered = veto_reasons
            result.state = STATE_INVALIDATED
            result.is_valid = False
            result.conclusion = "触发强制否决：\n" + "\n".join(f"  {r}" for r in veto_reasons)
            sm.invalidate("；".join(veto_reasons))
            result.state_sequence = sm.state_sequence
            return self._finalize_v2(result, df, sm)

        # ══════════════════════════════════════════════════════════
        # 最终评分
        # ══════════════════════════════════════════════════════════
        result.final_score = self.scorer.score(
            dt, imp, base, bo, pb, ra,
            risk_reward=result.risk_reward,
            theme_bonus=result.theme_bonus,
        )
        fs = result.final_score

        # Q1~Q13 检查
        result.q_checks = fs.passed_checks if fs.passed_checks else {}

        # 市场环境判定
        can_trade, trade_msg = self.market_filter.can_trade(
            result.market_regime, fs.grade, fs.total
        )

        # 最终判定
        if fs.is_primary_buy and can_trade and result.risk_reward >= 2.0:
            result.state = STATE_PRIMARY_BUY
            result.is_valid = True
            sm.transition(STATE_PRIMARY_BUY,
                           f"★ PRIMARY BUY (SCORE={fs.total:.0f}, RR={result.risk_reward:.1f})")
            result.conclusion = (
                f"★ PRIMARY BUY\n"
                f"为什么现在可以买：\n"
                f"  - 长期下跌趋势已被第一波反转破坏（Q1~Q3通过）\n"
                f"  - 高位平台保留了{base.retain_ratio*100:.0f}%的第一波涨幅（Q4~Q6通过）\n"
                f"  - 第二波突破确认且缩量回踩健康（Q7~Q10通过）\n"
                f"  - 二次启动且盈亏比{result.risk_reward:.1f}≥2（Q11~Q13通过）\n"
                f"  - 最终评分{fs.total:.0f}分（{fs.grade}），市场环境{result.market_regime}允许交易"
            )
        else:
            reasons = []
            if not fs.is_primary_buy:
                failed_q = [k for k, v in result.q_checks.items() if not v]
                reasons.append(f"Q检查未全通过: {failed_q}")
            if not can_trade:
                reasons.append(f"市场过滤: {trade_msg}")
            if result.risk_reward < 2.0:
                reasons.append(f"盈亏比不足({result.risk_reward:.1f}<2)")

            result.state = STATE_RE_ACCELERATION
            result.is_valid = True
            result.conclusion = (
                f"暂不满足 PRIMARY BUY 条件。\n"
                f"为什么现在不能买：\n"
                + "\n".join(f"  - {r}" for r in reasons)
                + f"\n  分数={fs.total:.0f}({fs.grade})，等待进一步信号。"
            )

        result.state_sequence = sm.state_sequence
        return self._finalize_v2(result, df, sm)

    # ══════════════════════════════════════════════════════════
    # V2.0 集中收尾
    # ══════════════════════════════════════════════════════════
    def _finalize_v2(self, result: RIBResult, df: pd.DataFrame,
                     sm: StateMachine) -> RIBResult:
        """V2.0 收尾：历史记忆 / 结构风险 / 距离突破 / 下一状态 /
        BUY_READINESS / 三层池 / PriorityScore / "为什么现在不能买"。

        所有返回路径统一经过这里，保证任何状态都输出完整 V2 字段。
        """
        # ── ① 历史记忆（§3）──
        result.memory = self._build_memory(result, df)

        # ── ② 结构破坏评分（§20）──
        sr = self.structure_risk_engine.compute(df, result)
        result.structure_risk = sr.score
        result.structure_risk_items = sr.items
        result.structure_quality = round(max(0.0, 100.0 - sr.score), 1)

        # 终态（FAILED_*/HOLD/EXIT）保留原状态，其余状态风险>=70 → INVALIDATED
        terminal = (STATE_FAILED_REVERSAL, STATE_FAILED_BREAKOUT,
                    STATE_FAILED_PULLBACK, STATE_HOLD, STATE_EXIT)
        if sr.score >= self.cfg["v2"]["structure_risk_invalidate"] \
                and result.state not in terminal:
            risk_desc = "、".join(sr.items) if sr.items else "结构破坏"
            result.state = STATE_INVALIDATED
            result.is_valid = False
            result.cannot_buy_reason = (
                f"结构破坏评分{sr.score:.0f}≥70（{risk_desc}），"
                "本轮结构失效，必须重新寻找完整结构。"
            )
            sm.invalidate(risk_desc)
            result.conclusion = result.cannot_buy_reason
            result.state_sequence = sm.state_sequence
            result.buy_readiness = 0.0
            result.pool_tier = "IGNORE"
            result.priority_score = 0.0
            return result

        # ── ③ DISTANCE_TO_BREAKOUT（V2.1 §11 五档：距触发位 ImpulseHigh+0.3ATR）──
        if result.state in (STATE_POST_IMPULSE_BASE, STATE_PRE_BREAKOUT) and result.impulse:
            atr = self._safe_atr(df)
            cat, dist = classify_distance_to_breakout(
                result.close, result.impulse.impulse_high, atr)
            result.distance_to_breakout = cat
            result.distance_to_breakout_atr = round(dist, 2)
            # PRE_BREAKOUT 检测器结果优先（含§12抛压扣分后的精确值）
            if result.pre_breakout and result.pre_breakout.is_pre_breakout:
                result.distance_to_breakout = result.pre_breakout.distance_band
                result.distance_to_breakout_atr = result.pre_breakout.distance_atr

        # ── ④ NEXT_STATE 预测（V2.1 §21/§22）──
        next_info = self.next_state_analyzer.analyze(df, result)
        result.next_state = next_info.next_state
        result.next_state_score = next_info.score
        result.next_state_level = next_info.level
        result.next_state_gap_price = round(next_info.gap_price, 2)
        result.next_state_gap_atr = round(next_info.gap_atr, 2)
        result.next_state_gap_desc = next_info.gap_desc
        result.next_state_trigger = next_info.trigger_cond

        # ── ⑤ BUY_READINESS（V2.1 §23）──
        ri = self.readiness_engine.compute(df, result, next_info)
        result.buy_readiness = ri.readiness
        result.readiness_cap = ri.cap

        # 追涨硬约束：>2ATR 禁止 PRIMARY_BUY（§22）
        if result.state == STATE_PRIMARY_BUY and result.impulse:
            atr = self._safe_atr(df)
            if atr > 0 and \
                    (result.close - result.impulse.impulse_high) / atr > \
                    self.cfg["v2"]["chase_forbid_atr"]:
                result.state = STATE_RE_ACCELERATION
                result.conclusion = (
                    f"二次启动已确认，但距第一波高点已超"
                    f"{self.cfg['v2']['chase_forbid_atr']}ATR，追涨约束禁止 PRIMARY_BUY。"
                )

        # ── ⑥ BASE_QUALITY（§9）──
        result.base_quality = round(result.base.score, 1) if \
            result.base and result.base.is_base else 0.0

        # ── ⑦ 三层交易池（V2.1 §24~§28）──
        pre = result.pre_breakout
        pre_score = pre.score if pre else 0.0
        pb_dist = 99.0
        if result.pullback and result.pullback.is_pullback:
            atr_pb = self._safe_atr(df) or 1.0
            support_lv = 0.0
            if result.impulse:
                support_lv = result.impulse.impulse_high
            if support_lv <= 0 and result.base and result.base.is_base:
                support_lv = result.base.base_high
            if support_lv > 0:
                pb_dist = max(0.0, (support_lv - result.close) / atr_pb)
        result.pool_tier = assign_tier(
            result.state, result.buy_readiness,
            structure_risk=result.structure_risk,
            risk_reward=result.risk_reward,
            market_regime=result.market_regime,
            distance_atr=result.distance_to_breakout_atr
            if result.distance_to_breakout_atr > 0 else 99.0,
            pre_score=pre_score,
            pullback_vol_ratio=(result.pullback.pullback_volume_ratio
                                if result.pullback and result.pullback.is_pullback
                                else 1.0),
            support_gap_atr=pb_dist,
        )
        if result.pool_tier == "NEXT":
            result.next_rule = self._next_rule_of(result, pre_score, pb_dist)

        # ── ⑧ PriorityScore（V2.1 §37）──
        result.priority_score = self._compute_priority(result, next_info)

        # ── ⑨ 状态转换概率等级（V2.1 §30，样本不足只给等级）──
        result.prob_level = self._prob_level(result, next_info)

        # ── ⑩ 为什么现在不能买（V2.1 §39）──
        if result.state != STATE_PRIMARY_BUY:
            result.cannot_buy_reason = self._build_cannot_buy_reason(result)

        result.state_sequence = sm.state_sequence
        return result

    def _next_rule_of(self, result: RIBResult, pre_score: float,
                      pb_dist: float) -> str:
        """V2.1 §24：标记入选 NEXT 的规则。"""
        state = result.state
        if state == "PRE_BREAKOUT" and pre_score >= 75:
            return "A(成熟平台近突破位)"
        if state == "PULLBACK_SUPPORT":
            return "B(回踩承接待再加速)"
        if state == "FIRST_PULLBACK" and pb_dist <= 0.5:
            return "C(回踩近支撑缩量)"
        if state == "RE_ACCELERATION":
            return "D(二次启动待确认)"
        if state == "PRIMARY_BUY":
            return "PB(买点待条件补齐)"
        return ""

    def _prob_level(self, result: RIBResult, next_info) -> str:
        """V2.1 §30：结构概率等级（无回测样本时不伪造概率数字）。

        等级由 NEXT_STATE_SCORE 映射：
          >=85 高 / 75~84 中高 / 65~74 中 / <65 低
        """
        s = next_info.score
        if result.structure_risk >= 50:
            return "低(结构风险≥50)"
        if s >= 85:
            return "高"
        if s >= 75:
            return "中高"
        if s >= 65:
            return "中"
        return "低"

    def _build_memory(self, result: RIBResult, df: pd.DataFrame) -> dict:
        """构建状态历史记忆（§3）。"""
        imp = result.impulse
        base = result.base
        bo = result.breakout
        pb = result.pullback

        mem = {
            "previous_state": (result.state_sequence[-2]
                               if len(result.state_sequence) >= 2 else ""),
            "state_start_date": result.date,
            "state_history": list(result.state_sequence),
            "last_breakout_date": "",
            "impulse_high": round(imp.impulse_high, 2) if imp else 0.0,
            "impulse_low": round(imp.impulse_low, 2) if imp else 0.0,
            "base_high": round(base.base_high, 2) if base and base.is_base else 0.0,
            "base_low": round(base.base_low, 2) if base and base.is_base else 0.0,
            "breakout_price": round(bo.breakout_price, 2) if bo and bo.is_breakout else 0.0,
            "pullback_low": round(pb.pullback_low, 2) if pb and pb.is_pullback else 0.0,
            "pullback_high": 0.0,
            "failed_reason": result.conclusion,
        }
        if bo and bo.is_breakout and "trade_date" in df.columns:
            idx = int(bo.breakout_idx)
            if 0 <= idx < len(df):
                mem["last_breakout_date"] = str(df["trade_date"].values[idx])
        if pb and pb.is_pullback and len(df) > 0:
            highs = df["high"].values.astype(float)
            lo, hi = int(pb.pullback_start_idx), int(pb.pullback_low_idx)
            if hi >= lo and 0 <= lo <= hi < len(df):
                mem["pullback_high"] = round(float(np.max(highs[lo:hi + 1])), 2)
        return mem

    def _compute_priority(self, result: RIBResult,
                          next_info) -> float:
        """PriorityScore（§27）。"""
        w = self.cfg["v2"]["priority_weights"]
        imp_score = (result.impulse.score if result.impulse else 0.0) or 0.0
        base_q = result.base_quality
        mkt = float(self.cfg["v2"]["regime_scores"]
                    .get(result.market_regime, 60))
        rr_norm = min(1.0, result.risk_reward / 3.0) if result.risk_reward > 0 else 0.0
        score = (
            w["buy_readiness"] * result.buy_readiness +
            w["next_state"] * next_info.score +
            w["structure_quality"] * result.structure_quality +
            w["impulse"] * imp_score +
            w["base_quality"] * base_q +
            w["market_align"] * mkt +
            w["risk_reward"] * rr_norm * 100.0
        )
        return round(min(100.0, score), 1)

    def _safe_atr(self, df: pd.DataFrame) -> float:
        """读取最新 ATR20，异常返回 0。"""
        if "atr20" in df.columns:
            try:
                v = float(df["atr20"].values[-1])
                if math.isfinite(v) and v > 0:
                    return v
            except (TypeError, ValueError, IndexError):
                pass
        return 0.0

    def _build_cannot_buy_reason(self, result: RIBResult) -> str:
        """根据当前状态生成"为什么现在不能买"（§29）。"""
        state = result.state
        imp = result.impulse
        base = result.base
        bo = result.breakout

        if state in (STATE_FAILED_REVERSAL, STATE_FAILED_BREAKOUT,
                     STATE_FAILED_PULLBACK, STATE_INVALIDATED):
            return result.conclusion

        if state == STATE_DOWNTREND:
            if result.downtrend and \
                    result.downtrend.score < self.cfg.get("downtrend", {}).get("score_min", 65):
                return (f"长期下跌结构强度不足（{result.downtrend.score:.0f}<65），"
                        "尚未形成潜在反转背景，只能放入候选池。")
            return ("尚未出现有效的第一波反转，当前仍处于下跌趋势，"
                    "等待第一波放量反转信号（IMPULSE_RETURN≥15%）。")

        if state == STATE_IMPULSE_ACTIVE:
            return ("第一波上涨尚未结束，第一波高点未确认，"
                    "此时介入属于追第一波，违背模型核心原则（不买第一波）。")

        if state == STATE_IMPULSE_PEAK:
            return ("第一波上涨刚结束，尚未形成有效 POST_IMPULSE_BASE，"
                    "必须等待 5~20 日缩量强势平台形成。")

        if state == STATE_POST_IMPULSE_BASE:
            if imp:
                return (f"平台尚未突破第一波高点{imp.impulse_high:.2f}，"
                        "当前仍属于等待确认阶段，必须等待放量突破（量比≥1.3+收盘站上）。")
            return "平台形成中，尚未突破第一波高点，等待放量突破确认。"

        if state == STATE_PRE_BREAKOUT:
            if result.pre_breakout and result.pre_breakout.is_pre_breakout:
                p = result.pre_breakout
                if p.expired:
                    return ("成熟平台已超过10日仍未突破（EXPIRED），"
                            "信号过期，等待重新评估平台或新结构。")
                return (f"平台已成熟（PB分{p.score:.0f}/{p.grade}），"
                        f"但尚未放量突破触发价{p.trigger_price:.2f}。"
                        "下一步：放量突破+收盘站稳+无明显长上影；"
                        f"触发后不追涨，等待第一次缩量回踩{p.trigger_price:.2f}附近，"
                        "回踩不破再等重新加速。")
            return ("平台内部已转强但尚未突破第一波高点，"
                    "等待放量突破确认（收盘价+0.3ATR 且量比≥1.3）。")

        if state == STATE_SECOND_LEG_BREAKOUT:
            return ("第二波突破已完成但尚未出现第一次健康回踩，"
                    "等待 1~5 日缩量回踩（优选2~3日），不追突破。")

        if state == STATE_FIRST_PULLBACK:
            return ("第一次回踩正在进行，尚未确认关键位承接，"
                    "等待回踩缩量且收盘重新站回关键支撑。")

        if state == STATE_PULLBACK_SUPPORT:
            return ("回踩关键位已获承接，但尚未重新放量突破回踩高点，"
                    "等待 RE_ACCELERATION 确认（量比≥1.1+收盘位置≥0.75）。")

        if state == STATE_RE_ACCELERATION:
            return ("二次启动已确认，但尚未完全满足 PRIMARY_BUY 条件"
                    "（盈亏比≥2 / 市场非BEAR / 无追高/结构风险）。")

        return result.conclusion

    def _scan_for_impulse(self, df: pd.DataFrame, end_idx: int) -> Optional[ImpulseResult]:
        """从 end_idx 往回扫描，寻找最近的第一波反转。

        核心策略：
        1. 找到扫描窗口内的最低点（长期下跌的底部）
        2. 从该低点出发，寻找【第一波】显著上涨的局部峰值
           （不是全局最高点，而是第一个达到15%+涨幅的局部高点）
        3. 检查该峰值之后是否出现整理（平台），验证这不是持续上涨
        4. 评分并选出最优候选

        Returns:
            ImpulseResult（is_impulse=True 表示找到有效第一波）
        """
        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)
        closes = df["close"].values.astype(float)
        vols = df["vol"].values.astype(float)

        cfg = self.cfg.get("impulse", {})
        min_return = cfg.get("min_return", 0.15)
        max_lookback = self.cfg.get("scan_lookback", 200)  # Look back 200 bars

        scan_start = max(60, end_idx - max_lookback)

        from .indicators import find_local_extremes

        candidates = []

        # 策略：找扫描窗口内的主要低点，然后使用"第一波结束检测"找到 impulse high
        # 第一波结束 = 从低点开始，上涨达到15%+后出现第一次显著回撤(>5%)
        seg_lows = lows[scan_start:end_idx + 1]
        min_idx_in_seg = int(np.argmin(seg_lows))
        lowest_idx = scan_start + min_idx_in_seg
        lowest_price = lows[lowest_idx]

        # 从低点向后扫描，找到 impulse high
        # 规则：在最多60天内，找到最高点，但该点之后必须有回撤
        # 或者：找到达到15%涨幅后的第一个高点（使用回撤检测）
        search_limit = min(lowest_idx + 60, end_idx)
        if search_limit - lowest_idx < 3:
            return None

        # 方法：检测"第一波上涨后进入整理"的转折点
        # 信号：在上涨过程中，当成交量萎缩 + 价格横盘持续出现时，
        # 意味着第一波结束，进入整理阶段
        peak_found = False

        # 扫描区间内的峰值
        peak_volume = 0
        peak_price = lowest_price
        peak_idx = lowest_idx
        consolidation_start = None
        vol_dry_days = 0  # 连续量能枯竭天数

        for i in range(lowest_idx + 1, search_limit + 1):
            # 更新峰值 -- 关键：整理信号出现后峰值锁定
            # （平台期的小反弹高点不属于第一波，否则第一波高点会被平台高点污染）
            if consolidation_start is None and highs[i] > peak_price:
                peak_price = highs[i]
                peak_idx = i

            # peak_volume 跟踪脉冲期间的最大量能（不是峰值当日量），
            # 避免平台期高点更新把量能基准污染成平台小量
            if vols[i] > peak_volume:
                peak_volume = vols[i]

            if peak_price <= lowest_price * (1 + min_return):
                continue  # 还没达到最低涨幅

            # 检查当前是否出现整理信号
            # 信号A：量能衰竭（最可靠的第一波结束标志）
            # 成交量萎缩到峰值的40%以下，累计2天 => 第一波结束
            if peak_volume > 0 and vols[i] < peak_volume * 0.40:
                vol_dry_days += 1
            else:
                vol_dry_days = 0
            if vol_dry_days >= 2 and consolidation_start is None:
                consolidation_start = max(i - 1, peak_idx + 1)

            # 信号B：缩量+价格停滞/回落
            # 条件1: 成交量萎缩到峰值的60%以下
            vol_ratio = vols[i] / peak_volume if peak_volume > 0 else 1.0

            # 条件2: 价格低于峰值2%以上，或峰值后5日仍未创新高
            price_weakness = (peak_price - highs[i]) / peak_price if peak_price > 0 else 0
            price_stall = price_weakness > 0.02 or (
                i - peak_idx >= 5 and highs[i] < peak_price * 0.995
            )

            # 如果出现整理信号，记录开始时间（峰值从此锁定）
            if vol_ratio < 0.6 and price_stall and consolidation_start is None:
                consolidation_start = i

            # 如果整理持续5天以上，确认峰值
            if consolidation_start is not None and i - consolidation_start >= 4:
                # 确认整理持续了至少5天
                highest_idx = peak_idx
                highest_seen = peak_price
                peak_found = True
                break

        if not peak_found:
            # 没检测到整理信号，使用简单方法
            seg = highs[lowest_idx:search_limit + 1]
            peak_offset = int(np.argmax(seg))
            highest_idx = lowest_idx + peak_offset
            highest_seen = seg.max()

        ret = (highest_seen - lowest_price) / lowest_price
        days = highest_idx - lowest_idx

        # 评分函数
        def score_candidate(c, is_primary=False):
            score = 0.0
            r = c.impulse_return
            d = c.impulse_days
            vr = c.volume_ratio

            # 涨幅评分（15-60%最佳，超过60%仍给合理分）
            if 0.20 <= r <= 0.60:
                score += 40
            elif 0.15 <= r < 0.20:
                score += 30
            elif 0.60 < r <= 1.0:
                score += 35  # 较大涨幅仍是有效的第一波
            elif r > 1.0:
                score += 25  # 超大涨幅可能包含后续行情
            else:
                score += 10

            # 时间评分（5-20天最佳）
            if 5 <= d <= 20:
                score += 25
            elif 3 <= d < 5:
                score += 15
            elif 20 < d <= 30:
                score += 10
            else:
                score += 5

            # 量能评分
            if vr >= 1.5:
                score += 20
            elif vr >= 1.2:
                score += 15
            elif vr >= 1.0:
                score += 5

            # 突破确认
            if c.is_reversal_confirmed:
                score += 15

            # 不是极端加速
            if not c.is_extreme_acceleration:
                score += 10

            # MA突破
            if c.broke_ma60:
                score += 5
            if c.broke_ma20:
                score += 3

            # 优先选择从最低点开始的候选
            if is_primary:
                score += 30  # 绝对低点优先

            return score

        # 首选：从绝对最低点检测
        primary_candidate = None
        if ret >= min_return and 3 <= days <= 60:
            candidate = self._build_impulse_candidate(
                df, lowest_idx, highest_idx, ret, days, lows, highs, vols
            )
            if candidate:
                primary_candidate = candidate

        # 备选：其他局部低点（使用简单方法）
        alternate_candidates = []
        _, low_indices = find_local_extremes(lows[scan_start:end_idx+1], order=5)
        if len(low_indices) <= 1:
            _, low_indices = find_local_extremes(lows[scan_start:end_idx+1], order=3)
        low_indices = low_indices + scan_start

        for li in low_indices:
            if li >= lowest_idx or (end_idx - li) < 20:
                continue
            low_p = lows[li]
            local_search = min(li + 60, end_idx)
            seg_local = highs[li:local_search + 1]
            local_high_idx = li + int(np.argmax(seg_local))
            local_high = seg_local.max()

            r = (local_high - low_p) / low_p
            d = local_high_idx - li
            if r >= min_return and 3 <= d <= 60:
                c = self._build_impulse_candidate(df, li, local_high_idx, r, d, lows, highs, vols)
                if c:
                    alternate_candidates.append(c)

        # 组合所有候选并评分
        all_candidates = []
        if primary_candidate:
            all_candidates.append((primary_candidate, score_candidate(primary_candidate, is_primary=True)))
        for c in alternate_candidates:
            all_candidates.append((c, score_candidate(c, is_primary=False)))

        if not all_candidates:
            return None

        # 优先选择主候选（如果其分>=70），否则选择最优备选
        all_candidates.sort(key=lambda x: x[1], reverse=True)
        best_cand, best_score = all_candidates[0]

        # 关键：把评分写回候选对象（否则 imp.score 恒为0，Q2检查永远失败）
        # score_candidate 最大约130分，归一化到100
        best_cand.score = round(min(100.0, best_score), 1)
        return best_cand

    def _build_impulse_candidate(
        self, df: pd.DataFrame, low_idx: int, high_idx: int,
        ret: float, days: int, lows: np.ndarray, highs: np.ndarray,
        vols: np.ndarray,
    ) -> Optional[ImpulseResult]:
        """构建一个 impulse 候选结果。"""
        low_price = lows[low_idx]
        high_price = highs[high_idx]

        candidate = ImpulseResult()
        candidate.is_impulse = True
        candidate.impulse_start_idx = low_idx
        candidate.impulse_low_idx = low_idx
        candidate.impulse_low = low_price
        candidate.impulse_high_idx = high_idx
        candidate.impulse_high = high_price
        candidate.impulse_days = days
        candidate.impulse_return = ret
        candidate.volume_ratio = 1.0  # 先默认，后面计算

        # 检查 MA 突破
        has_ma20 = "ma20" in df.columns
        has_ma60 = "ma60" in df.columns
        break_ma20 = False
        break_ma60 = False
        break_trend = False
        break_prev_high = False

        if has_ma20 and has_ma60:
            ma20_at_low = float(df["ma20"].values[low_idx]) if not np.isnan(df["ma20"].values[low_idx]) else low_price
            ma60_at_low = float(df["ma60"].values[low_idx]) if not np.isnan(df["ma60"].values[low_idx]) else low_price

            if high_price > ma20_at_low:
                break_ma20 = True
            if high_price > ma60_at_low:
                break_ma60 = True

            # 趋势线突破
            prev_highs = highs[max(0, low_idx - 60):low_idx]
            if len(prev_highs) > 10:
                x = np.arange(len(prev_highs))
                slope, intercept = np.polyfit(x, prev_highs, 1)
                if slope < 0:
                    trend_line_at_high = slope * len(prev_highs) + intercept
                    if high_price > trend_line_at_high:
                        break_trend = True

            # 前期高点突破
            if len(prev_highs) > 5:
                prev_max = prev_highs.max()
                if high_price > prev_max:
                    break_prev_high = True

        is_confirmed = break_ma20 or break_ma60 or break_trend or break_prev_high

        candidate.is_reversal_confirmed = is_confirmed
        candidate.broke_ma20 = break_ma20
        candidate.broke_ma60 = break_ma60
        candidate.broke_trend_line = break_trend
        candidate.broke_previous_high = break_prev_high

        # ATR
        if "atr20" in df.columns:
            atr_val = float(df["atr20"].values[low_idx]) if not np.isnan(df["atr20"].values[low_idx]) else 0
            if atr_val > 0:
                candidate.impulse_atr = (high_price - low_price) / atr_val

        # 暴涨标记
        if days <= 2 and ret > 0.20:
            candidate.is_extreme_acceleration = True

        # 计算量能比
        vol_ma20 = df["vol_ma20"].values if "vol_ma20" in df.columns else None
        if vol_ma20 is not None:
            avg_impulse_vol = np.mean(vols[low_idx:high_idx + 1])
            baseline_vol = float(vol_ma20[low_idx]) if not np.isnan(vol_ma20[low_idx]) else np.mean(vols[max(0, low_idx - 20):low_idx])
            if baseline_vol > 0:
                candidate.volume_ratio = avg_impulse_vol / baseline_vol
            else:
                candidate.volume_ratio = 1.0

        # 最低量能要求
        if candidate.volume_ratio < 0.8:
            return None  # 量能萎缩，不是有效的第一波

        return candidate
