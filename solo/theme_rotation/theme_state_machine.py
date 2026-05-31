# -*- coding: utf-8 -*-
"""主题状态机：休眠→萌芽→主线→加速→分化→退潮"""
import json
from collections import defaultdict
from typing import Dict, List, Tuple

from .config import (
    MAINLINE_SCORE, EMERGING_SCORE, DECLINE_DAYS,
    MOMENTUM_W, ACC_W,
)


STATES = ("DORMANT", "EMERGING", "MAINLINE", "ACCELERATING", "DIVERGING", "DECLINING")

STATE_CN = {
    "DORMANT": "休眠",
    "EMERGING": "萌芽",
    "MAINLINE": "主线",
    "ACCELERATING": "加速",
    "DIVERGING": "分化",
    "DECLINING": "退潮",
}


class ThemeStateMachine:
    def __init__(self):
        self._history: Dict[str, List[float]] = defaultdict(list)

    def update(self, theme_name: str, score: float) -> Dict:
        h = self._history[theme_name]
        h.append(score)
        if len(h) > 10:
            h.pop(0)

        momentum = self._calc_momentum(h)
        acc = self._calc_acceleration(h)
        strength = score + MOMENTUM_W * momentum + ACC_W * acc
        state = self._determine_state(h, score, momentum, acc)

        return {
            "theme_name": theme_name,
            "score": round(score, 2),
            "strength": round(strength, 2),
            "momentum": round(momentum, 2),
            "acceleration": round(acc, 2),
            "state": state,
            "state_cn": STATE_CN.get(state, state),
            "history": json.dumps(h),
        }

    def _calc_momentum(self, h: List[float]) -> float:
        if len(h) < 2:
            return 0.0
        if len(h) >= 3:
            return h[-1] - h[-3]
        return h[-1] - h[-2]

    def _calc_acceleration(self, h: List[float]) -> float:
        if len(h) < 3:
            return 0.0
        return (h[-1] - h[-2]) - (h[-2] - h[-3])

    def _determine_state(
        self, h: List[float], score: float, momentum: float, acc: float
    ) -> str:
        if len(h) >= DECLINE_DAYS and all(
            h[-i] < h[-i - 1] for i in range(1, DECLINE_DAYS)
        ):
            return "DECLINING"

        if score >= MAINLINE_SCORE and momentum > 0 and acc > 0:
            return "ACCELERATING"

        if score >= MAINLINE_SCORE and momentum >= 0:
            return "MAINLINE"

        if score >= EMERGING_SCORE and momentum > 5:
            return "EMERGING"

        if score >= MAINLINE_SCORE and momentum < 0:
            return "DIVERGING"

        if score < EMERGING_SCORE:
            return "DORMANT"

        return "EMERGING"

    def rank_themes(self, theme_results: List[Dict]) -> List[Dict]:
        ranked = sorted(theme_results, key=lambda x: x["strength"], reverse=True)
        for i, r in enumerate(ranked, 1):
            r["rank"] = i
        return ranked

    def pick_mainline(self, ranked: List[Dict]) -> Tuple[str, str]:
        """返回 (主线主题, 备选主题)"""
        active = [
            t for t in ranked
            if t["state"] in ("MAINLINE", "ACCELERATING", "EMERGING")
            and t["strength"] > EMERGING_SCORE
        ]
        if not active:
            active = ranked[:2] if len(ranked) >= 2 else ranked

        mainline = active[0]["theme_name"] if active else ""
        backup = active[1]["theme_name"] if len(active) > 1 else ""
        return mainline, backup
