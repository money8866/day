"""Explainer - 市场评分可解释性引擎

自动生成 MarketScore 和 HeatScore 的详细归因分析。
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field


@dataclass
class ExplainBlock:
    title: str
    items: List[str]
    score: float = 0.0
    impact: str = "neutral"  # positive | negative | neutral


class MarketExplainer:
    """市场评分解释器

    为每个评分组件生成可读的归因文本。
    """

    @staticmethod
    def explain_index_strength(per_index: Dict[str, float],
                                weighted_score: float,
                                sub_scores: Dict[str, Dict[str, float]]) -> ExplainBlock:
        items = []
        total = 0
        for code, score in sorted(per_index.items(), key=lambda x: x[1], reverse=True):
            name = MarketExplainer._index_name(code)
            diff = score - 50
            if diff > 10:
                items.append(f"{name}强势 +{diff:.0f}分 → 贡献{score:.0f}分")
                total += diff
            elif diff < -10:
                items.append(f"{name}走弱 {diff:.0f}分 → 贡献{score:.0f}分")
                total += diff
            else:
                items.append(f"{name}中性 {score:.0f}分")

        # 添加细分因子说明
        detail_items = []
        for code, subs in sub_scores.items():
            name = MarketExplainer._index_name(code)
            best_factor = max(subs.items(), key=lambda x: x[1]) if subs else ("-", 0)
            worst_factor = min(subs.items(), key=lambda x: x[1]) if subs else ("-", 0)
            detail_items.append(f"{name}: {best_factor[0]}最好({best_factor[1]:.0f}分), {worst_factor[0]}最弱({worst_factor[1]:.0f}分)")

        impact = "positive" if total > 5 else ("negative" if total < -5 else "neutral")
        return ExplainBlock(
            title="指数强度分析",
            items=items + [""] + detail_items[:3],
            score=weighted_score,
            impact=impact,
        )

    @staticmethod
    def explain_breadth(result) -> ExplainBlock:
        """解释宽度评分"""
        items = []
        items.append(f"上涨比例 {result.up_ratio*100:.0f}%")
        items.append(f"涨停 {result.limit_up_count}家")
        items.append(f"跌停 {result.limit_down_count}家")
        items.append(f"20日新高 {result.new_high_20_ratio*100:.1f}%")
        items.append(f"站上MA20 {result.above_ma20_ratio*100:.0f}%")
        items.append(f"涨幅中位数 {result.median_return*100:.2f}%")

        total = result.score - 50
        impact = "positive" if total > 5 else ("negative" if total < -5 else "neutral")
        return ExplainBlock(
            title="市场宽度（赚钱效应）",
            items=items,
            score=result.score,
            impact=impact,
        )

    @staticmethod
    def explain_sentiment(result) -> ExplainBlock:
        items = []
        items.append(f"涨停率 {result.limit_up_ratio*100:.2f}%")
        items.append(f"炸板率 {result.break_ratio*100:.0f}%")
        items.append(f"最高连板 {result.max_continuous}板")
        items.append(f"20cm涨停 {result.gem_count}家")
        items.append(f"北向资金 {result.north_flow:+.0f}亿")
        items.append(f"成交额变化 {result.amount_change*100:+.1f}%")

        total = result.score - 50
        impact = "positive" if total > 5 else ("negative" if total < -5 else "neutral")
        return ExplainBlock(
            title="市场情绪",
            items=items,
            score=result.score,
            impact=impact,
        )

    @staticmethod
    def explain_style(result) -> ExplainBlock:
        items = []
        items.append(f"主导风格: {result.dominant_style}")
        for style_name, score in sorted(result.style_scores.items(),
                                          key=lambda x: x[1], reverse=True)[:5]:
            items.append(f"  {style_name}: {score:.0f}分")
        if result.suggestions:
            items.append(f"建议关注: {', '.join(result.suggestions[:3])}")

        return ExplainBlock(
            title="风格轮动",
            items=items,
            score=result.style_scores.get(result.dominant_style, 50),
        )

    @staticmethod
    def explain_heat(result) -> ExplainBlock:
        items = []
        items.append(f"热度等级: {result.level}")
        items.append(f"热度趋势: {result.trend}")
        items.append(f"热度周期: {result.cycle}")
        for name, score in sorted(result.sub_scores.items(),
                                    key=lambda x: x[1], reverse=True):
            items.append(f"  {name}: {score:.0f}分")

        total = result.score - 50
        impact = "positive" if total > 10 else ("negative" if total < -10 else "neutral")
        items.append(f"仓位修正系数: {result.adjustment_factor:.2f}")
        items.append(f"建议每日交易: {result.max_trades_per_day}次")

        return ExplainBlock(
            title="市场热度",
            items=items,
            score=result.score,
            impact=impact,
        )

    @staticmethod
    def explain_market_score(score: float, contributions: Dict[str, float]) -> ExplainBlock:
        items = []
        for name, contrib in sorted(contributions.items(), key=lambda x: x[1], reverse=True):
            sign = "+" if contrib > 0 else ""
            items.append(f"{name}: {sign}{contrib:.1f}分")

        total = score - 50
        if total > 20:
            summary = "市场整体偏强"
        elif total > 5:
            summary = "市场温和偏强"
        elif total > -5:
            summary = "市场震荡中性"
        elif total > -20:
            summary = "市场偏弱"
        else:
            summary = "市场弱势明显"

        items.insert(0, f"综合判断: {summary}")
        return ExplainBlock(
            title=f"最终市场评分 {score:.0f}分",
            items=items,
            score=score,
            impact="positive" if total > 0 else "negative",
        )

    @staticmethod
    def _index_name(code: str) -> str:
        names = {
            "000001.SH": "上证指数",
            "000300.SH": "沪深300",
            "000852.SH": "中证1000",
            "399006.SZ": "创业板",
            "000688.SH": "科创50",
        }
        return names.get(code, code)
