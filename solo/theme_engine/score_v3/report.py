"""TERE V3 完整报告输出模板.

Market × Theme × ETF × Leader 四层架构的完整输出。
"""

from __future__ import annotations

from typing import List, Optional

from theme_engine.score_v3.models import EngineV3Result, ThemeV3Score


def render_full_report(result: EngineV3Result) -> str:
    """渲染完整报告."""
    lines: List[str] = []

    def sep(c="═", n=70):
        lines.append(c * n)

    # ═══ 报告头 ═══
    sep()
    lines.append(f"  TERE V3 全景轮动报告 — {result.trade_date}")
    lines.append(f"  生成时间: {result.generated_at}")
    sep()

    # ═══ Market 信息 ═══
    market = result.market_info
    if market:
        exposure_pct = int(market.recommended_exposure * 100)
        conf_pct = int(market.confidence * 100)

        sep("─")
        lines.append("  Market  [Layer 1 — 市场状态]")
        sep("─")
        lines.append(f"  Score:              {market.market_score:.0f} / 100")
        lines.append(f"  Regime:             {market.market_regime_cn}")
        lines.append(f"  Confidence:         {conf_pct}%")
        lines.append(f"  Market Multiplier:  {market.market_multiplier:.2f}")
        lines.append(f"  Recommended Exposure: {exposure_pct}%")

        # 各维度投票
        if market.details:
            votes = market.details.get("vote_results", {})
            if votes:
                lines.append(f"  维度投票: 趋势={votes.get('trend','?')}  "
                             f"宽度={votes.get('breadth','?')}  "
                             f"情绪={votes.get('sentiment','?')}  "
                             f"资金={votes.get('liquidity','?')}")

    # ═══ 主题排行榜 ═══
    sep("─")
    lines.append("  Themes  [Layer 2-4 — 主题轮动评分]")
    sep("─")

    header = (f"  {'排名':>3} {'主题名称':<14}"
              f" {'Intrinsic':>9} {'Tradable':>9}"
              f" {'信号':<12} {'轮动概率':>7}"
              f" {'生命周期':<7} {'迁移':<8}"
              f" {'预期收益':<8} {'风险':<5}")
    lines.append(header)
    lines.append(f"  {'─' * 82}")

    life_cn_map = {
        "birth": "萌芽", "growth": "成长", "main_up": "主升",
        "late": "末期", "decline": "衰退",
    }

    for theme in result.ranking[:20]:
        life_cn = life_cn_map.get(theme.life_stage, theme.life_stage)
        prob = f"{theme.rotation_prob_5d:.0f}%"

        # 迁移方向显示
        trans_display = ""
        if theme.pre_rotate and theme.transition_result:
            trans_display = f"▶ {theme.transition_result.direction_cn}"
        elif theme.transition_direction:
            trans_display = theme.transition_direction

        lines.append(
            f"  {theme.rank:>3} "
            f"{theme.theme_name:<14} "
            f"{theme.intrinsic_score:>8.0f} "
            f"{theme.tradable_score:>8.0f} "
            f"{theme.signal:<12} "
            f"{prob:>7} "
            f"{life_cn:<7} "
            f"{trans_display:<8} "
            f"{theme.expected_return:<8} "
            f"{theme.risk:<5}"
        )

    if len(result.ranking) > 20:
        lines.append(f"  ... 共 {len(result.ranking)} 个主题")

    # ═══ Top 5 详情 ═══
    if result.top_themes:
        sep()
        lines.append("  Top 5 深度解析")
        sep()

        for theme in result.top_themes[:5]:
            life_cn = life_cn_map.get(theme.life_stage, theme.life_stage)
            sig_desc = _signal_desc_chinese(theme.signal)

            lines.append(f"  #{theme.rank} {theme.theme_name}")
            lines.append(f"    Intrinsic: {theme.intrinsic_score:.0f} / 100  "
                         f"Tradable: {theme.tradable_score:.0f} / 100")
            lines.append(f"    生命周期: {life_cn}  "
                         f"共振乘数: {theme.resonance_multiplier:.2f}  "
                         f"市场乘数: {theme.market_multiplier:.2f}")

            # 迁移检测信息 (V2 — 6因子 + 修正项)
            if theme.pre_rotate and theme.transition_result:
                tr = theme.transition_result
                lines.append(f"    迁移检测: ▶ {tr.direction_cn}  "
                             f"强度: {tr.strength:.0f}  "
                             f"置信度: {tr.confidence*100:.0f}%  "
                             f"预计: {tr.days_estimate}天  "
                             f"→ {tr.to_stage}")
                # 6因子明细
                lines.append(f"    6因子: "
                             f"距离P={tr.proximity_score:.0f} "
                             f"动量M={tr.momentum_score:.0f} "
                             f"扩散C={tr.confirmation_score:.0f} "
                             f"资金$={tr.money_resonance_score:.0f} "
                             f"龙头L={tr.leader_health_score:.0f} "
                             f"市场R={tr.regime_compat_score:.0f}")
                # 修正项
                corrections = []
                if tr.age_penalty != 0:
                    corrections.append(f"老化{tr.age_penalty:+.0f}")
                if tr.macro_filter != 0:
                    corrections.append(f"宏观{tr.macro_filter:+.0f}")
                if corrections:
                    lines.append(f"    修正项: {' '.join(corrections)}")
            elif theme.transition_result and theme.transition_result.direction != "STABLE":
                tr = theme.transition_result
                lines.append(f"    迁移检测: {tr.direction_cn}  "
                             f"强度: {tr.strength:.0f}  "
                             f"置信度: {tr.confidence*100:.0f}%")

            lines.append(f"    预期收益: {theme.expected_return}  "
                         f"风险: {theme.risk}  "
                         f"轮动概率: {theme.rotation_prob_5d:.0f}%")
            lines.append(f"    信号: {theme.signal} — {sig_desc}")

            # 8个因子分
            factors = (
                f"ETF趋势:{theme.etf_trend:.0f} "
                f"加速度:{theme.etf_accel:.0f} "
                f"扩散度:{theme.breadth:.0f} "
                f"龙头:{theme.leader:.0f} "
                f"龙头扩散:{theme.leader_expand:.0f} "
                f"资金:{theme.money:.0f} "
                f"排名动量:{theme.rank_momentum:.0f} "
                f"生命周期加分:{theme.lifecycle_bonus:+.0f}"
            )
            lines.append(f"    因子分解: {factors}")

            # 龙头股
            if theme.top_leaders:
                leaders = ", ".join(theme.top_leaders[:5])
                lines.append(f"    龙头股: {leaders}")
            if theme.etf_code:
                lines.append(f"    ETF: {theme.etf_code}")
            lines.append("")

    # ═══ 交易建议 ═══
    sep()
    lines.append("  交易建议")
    sep()

    if market:
        lines.append(f"  总仓位: {exposure_pct}%")
        lines.append(f"  单ETF上限: {min(30, int(exposure_pct * 40))}%")
        lines.append(f"  单股票上限: {min(15, int(exposure_pct * 20))}%")

        if market.market_regime in ("risk_on", "neutral"):
            lines.append("  允许追高: 是")
            lines.append("  允许打板: 是")
            lines.append("  允许左侧: 否")
        elif market.market_regime in ("weak",):
            lines.append("  允许追高: 否")
            lines.append("  允许打板: 否")
            lines.append("  允许左侧: 是 (低吸为主)")
        else:
            lines.append("  允许追高: 否")
            lines.append("  允许打板: 否")
            lines.append("  允许左侧: 是 (仅低吸)")

    # 首推主题
    if result.ranking:
        top = result.ranking[0]
        lines.append("")
        lines.append(f"  首推: {top.theme_name} "
                     f"(Intrinsic={top.intrinsic_score:.0f} → "
                     f"Tradable={top.tradable_score:.0f})")
        lines.append(f"  信号: {top.signal} | "
                     f"轮动概率: {top.rotation_prob_5d:.0f}% | "
                     f"预期收益: {top.expected_return}")

    sep()
    lines.append(f"  TERE V3 报告结束")
    sep()

    return "\n".join(lines)


def _signal_desc_chinese(signal: str) -> str:
    """信号中文描述."""
    desc = {
        "STRONG_BUY": "四层共振，主线确立，全力做多",
        "BUY": "趋势向好，适合建仓加仓",
        "ROTATE_IN": "排名快速上升，正在走强，轮动入场",
        "PRE_ROTATE": "提前轮动 — 生命周期迁移信号，即将进入上升通道",
        "WATCH": "评分中等，等待进一步确认",
        "HOLD": "评分尚可，维持现有仓位",
        "REDUCE": "评分下降，降低风险敞口",
        "EXIT": "评分过低，清仓回避",
    }
    return desc.get(signal, signal)


def mock_report() -> str:
    """生成模拟报告（用于展示输出格式）."""
    from theme_engine.score_v3.models import MarketInfo, ThemeV3Score

    market = MarketInfo(
        market_score=43.0,
        market_regime="risk_off",
        market_regime_cn="Risk-Off",
        confidence=0.91,
        market_multiplier=0.70,
        recommended_exposure=0.35,
        details={
            "vote_results": {
                "trend": "risk_off", "breadth": "risk_off",
                "sentiment": "weak", "liquidity": "neutral",
            },
        },
    )

    themes = [
        ThemeV3Score(rank=1, theme_name="AI算力", theme_code="AI",
            intrinsic_score=88.0, tradable_score=61.0, final_score=61.0,
            signal="ROTATE_IN", rotation_prob_5d=78.0,
            life_stage="main_up", lifecycle_bonus=8.0,
            resonance_multiplier=1.10, market_multiplier=0.70,
            etf_trend=85, etf_accel=78, breadth=72, leader=80,
            leader_expand=65, rank_momentum=90, money=70,
            expected_return="高 (>15%)", risk="中",
            top_leaders=["中际旭创", "工业富联", "寒武纪", "浪潮信息"],
            etf_code="159995.SZ"),
        ThemeV3Score(rank=2, theme_name="机器人", theme_code="ROBOT",
            intrinsic_score=72.0, tradable_score=49.0, final_score=49.0,
            signal="HOLD", rotation_prob_5d=55.0,
            life_stage="growth", lifecycle_bonus=3.0,
            resonance_multiplier=1.05, market_multiplier=0.70,
            etf_trend=68, etf_accel=62, breadth=58, leader=65,
            leader_expand=50, rank_momentum=45, money=55,
            expected_return="中高 (8%~15%)", risk="中",
            top_leaders=["拓普集团", "三花智控", "绿的谐波"],
            etf_code="562500.SH"),
        ThemeV3Score(rank=3, theme_name="半导体", theme_code="SEMI",
            intrinsic_score=56.0, tradable_score=39.0, final_score=39.0,
            signal="WATCH", rotation_prob_5d=42.0,
            life_stage="growth", lifecycle_bonus=3.0,
            resonance_multiplier=0.95, market_multiplier=0.70,
            etf_trend=55, etf_accel=48, breadth=50, leader=52,
            leader_expand=40, rank_momentum=30, money=45,
            expected_return="中 (3%~8%)", risk="中",
            top_leaders=["中芯国际", "北方华创", "韦尔股份"],
            etf_code="512760.SH"),
        ThemeV3Score(rank=4, theme_name="消费", theme_code="CONS",
            intrinsic_score=40.0, tradable_score=34.0, final_score=34.0,
            signal="REDUCE", rotation_prob_5d=25.0,
            life_stage="late", lifecycle_bonus=-4.0,
            resonance_multiplier=0.90, market_multiplier=0.70,
            etf_trend=38, etf_accel=30, breadth=42, leader=35,
            leader_expand=25, rank_momentum=15, money=30,
            expected_return="低 (<3%)", risk="高",
            etf_code="159928.SZ"),
        ThemeV3Score(rank=5, theme_name="银行", theme_code="BANK",
            intrinsic_score=38.0, tradable_score=37.0, final_score=37.0,
            signal="HOLD", rotation_prob_5d=20.0,
            life_stage="growth", lifecycle_bonus=3.0,
            resonance_multiplier=1.00, market_multiplier=0.70,
            etf_trend=45, etf_accel=35, breadth=35, leader=30,
            leader_expand=20, rank_momentum=10, money=40,
            expected_return="低 (<3%)", risk="低",
            top_leaders=["招商银行", "工商银行"],
            etf_code="512800.SH"),
    ]

    # 补充低排名主题
    extras = [
        ("创新药", 35, 28, "WATCH"), ("新能源", 30, 22, "WATCH"),
        ("军工", 28, 20, "REDUCE"), ("地产", 25, 18, "REDUCE"),
        ("有色金属", 22, 15, "EXIT"), ("煤炭", 18, 12, "EXIT"),
    ]
    for i, (name, intrinsic, tradable, sig) in enumerate(extras, start=6):
        themes.append(ThemeV3Score(
            rank=i, theme_name=name, theme_code=name[:4],
            intrinsic_score=float(intrinsic), tradable_score=float(tradable),
            final_score=float(tradable),
            signal=sig, rotation_prob_5d=10.0,
            life_stage="decline", lifecycle_bonus=-8.0,
            resonance_multiplier=0.90, market_multiplier=0.70,
            expected_return="低 (<3%)", risk="高",
        ))

    result = EngineV3Result(
        trade_date="20260724",
        generated_at="2026-07-25 08:30:00",
        themes=themes,
        ranking=themes,
        top_themes=themes[:5],
        market_info=market,
    )

    return render_full_report(result)
