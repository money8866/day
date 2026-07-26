import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.loader import DataLoader, load_config


@dataclass
class AlphaResult:
    ts_code: str
    name: str
    theme: str
    alpha: float = 0.0
    market_state_score: float = 0.0
    theme_strength: float = 0.0
    leader_score: float = 0.0
    pullback_quality: float = 0.0
    etf_resonance_score: float = 0.0
    chip_stability: float = 0.0
    fund_flow_recovery: float = 0.0
    trend_health_score: float = 0.0
    buy_type: str = ""
    etf_code: str = ""
    suggestion: str = ""
    rating: str = ""

    def to_dict(self):
        return {
            'ts_code': self.ts_code,
            'name': self.name,
            'theme': self.theme,
            'alpha': self.alpha,
            'market_state_score': self.market_state_score,
            'theme_strength': self.theme_strength,
            'leader_score': self.leader_score,
            'pullback_quality': self.pullback_quality,
            'etf_resonance_score': self.etf_resonance_score,
            'chip_stability': self.chip_stability,
            'fund_flow_recovery': self.fund_flow_recovery,
            'trend_health_score': self.trend_health_score,
            'buy_type': self.buy_type,
            'etf_code': self.etf_code,
            'suggestion': self.suggestion,
            'rating': self.rating,
        }


class AlphaScorer:
    def __init__(self, config=None):
        self.config = config or load_config()
        self.cfg = self.config['alpha_scorer']
        self.loader = DataLoader()

    def score(self, components: Dict) -> AlphaResult:
        w = self.cfg.get('weights', {})
        ms = components.get('market_state', 0.5)
        ts = components.get('theme_strength', 0.5)
        ls = components.get('leader_score', 0.5)
        pq = components.get('pullback_quality', 0.5)
        er = components.get('etf_resonance', 0.5)
        cs = components.get('chip_stability', 0.5)
        fr = components.get('fund_flow_recovery', 0.5)
        th = components.get('trend_health', 0.5)

        alpha = (
            ms * w.get('market_state', 20) +
            ts * w.get('theme_strength', 15) +
            ls * w.get('leader_score', 15) +
            pq * w.get('pullback_quality', 20) +
            er * w.get('etf_resonance', 10) +
            cs * w.get('chip_stability', 10) +
            fr * w.get('fund_flow_recovery', 5) +
            th * w.get('trend_health', 5)
        )

        alpha = min(100, max(0, alpha))
        rating = self._get_rating(alpha)

        return AlphaResult(
            ts_code=components.get('ts_code', ''),
            name=components.get('name', ''),
            theme=components.get('theme', ''),
            alpha=round(alpha, 1),
            market_state_score=round(ms, 4),
            theme_strength=round(ts, 4),
            leader_score=round(ls, 4),
            pullback_quality=round(pq, 4),
            etf_resonance_score=round(er, 4),
            chip_stability=round(cs, 4),
            fund_flow_recovery=round(fr, 4),
            trend_health_score=round(th, 4),
            buy_type=components.get('buy_type', ''),
            etf_code=components.get('etf_code', ''),
            suggestion=components.get('suggestion', ''),
            rating=rating,
        )

    def _get_rating(self, alpha):
        if alpha >= 90:
            return "★★★★★"
        elif alpha >= 80:
            return "★★★★"
        elif alpha >= 70:
            return "★★★"
        elif alpha >= 60:
            return "★★"
        else:
            return "★"

    def generate_buy_signal(self, result: AlphaResult, pullback_info: Dict) -> str:
        lines = []
        lines.append(f"{result.rating}")
        lines.append(f"{result.name}")
        lines.append(f"主题: {result.theme}")
        lines.append(f"Alpha: {result.alpha}")
        lines.append(f"买点: {pullback_info.get('pullback_ma', '')}回踩")
        lines.append(f"ETF: {pullback_info.get('etf_status', '')}")
        lines.append(f"筹码: {pullback_info.get('chip_status', '')}")
        lines.append(f"资金: {pullback_info.get('flow_status', '')}")
        lines.append(f"建议: {result.suggestion}")
        return "\n".join(lines)

    def generate_sell_signal(self, position: Dict) -> str:
        lines = []
        lines.append(f"⚠️ 卖出信号")
        lines.append(f"{position.get('name', '')}")
        lines.append(f"主题: {position.get('theme', '')}")
        for reason in position.get('reasons', []):
            lines.append(f"- {reason}")
        lines.append(f"建议: {position.get('suggestion', '减仓')}")
        return "\n".join(lines)