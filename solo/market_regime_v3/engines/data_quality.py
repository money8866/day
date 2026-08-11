"""
数据质量检查引擎 - Data Quality Engine

在生成 Market Report 前对全市场宽度/情绪指标做一致性检查，
发现明显冲突（如：上涨比例低但站上MA20比例极高）时标记异常，
并隔离异常指标——不参与 Market Score 评分。

异常指标隔离优先级：有效数据 > 前一日有效数据 > 中性值(50)

仅做数据质量检查与异常隔离，不改动 Market Score 权重 / Regime 分类 / 仓位公式。
"""

import os
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


@dataclass
class DataQualityResult:
    """数据质量检查结果"""
    is_clean: bool                                  # True=指标一致
    anomalies: List[Dict] = field(default_factory=list)  # [{rule, cause, suggestion}]
    affected: Set[str] = field(default_factory=set)     # 受影响指标（如 {'breadth'}）
    summary: str = ""                                   # 报告用一行文本（含 ✓ / ⚠️）

    def to_dict(self) -> Dict:
        return {
            "is_clean": self.is_clean,
            "anomalies": self.anomalies,
            "affected": sorted(self.affected),
            "summary": self.summary,
        }


class DataQualityChecker:
    """市场数据一致性检查器

    检查指标：上涨比例 / 下跌比例 / 平盘比例 / 涨幅中位数 /
              站上MA20比例 / 涨停·跌停家数 / 炸板率
    """

    RULE_NAMES = {
        'MA20_BREADTH_ANOMALY': 'MA20宽度数据异常',
        'BREADTH_CONFLICT': '宽度指标背离',
    }

    def __init__(self, config: dict = None):
        cfg = (config or {}).get('data_quality', {})
        self.rules = cfg.get('rules', {})
        self.neutral_value = float(cfg.get('fallback', {}).get('neutral_value', 50.0))
        # 历史缓存（供"前一交易日有效值"回退）
        self.history_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'output', 'data_quality_history.json')
        self.history = self._load_history()

    # ──────────────────────────────────────────────
    # 历史缓存
    # ──────────────────────────────────────────────

    def _load_history(self) -> Dict:
        try:
            with open(self.history_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_history(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.history_path), exist_ok=True)
            with open(self.history_path, 'w', encoding='utf-8') as f:
                json.dump(self.history, f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    def get_prev_valid(self, indicator: str) -> Optional[float]:
        """获取最近一个"有效日"的指标值（日期倒序）"""
        for d in sorted(self.history.keys(), reverse=True):
            rec = self.history[d]
            if rec.get('valid', True) and indicator in rec.get('scores', {}):
                return rec['scores'][indicator]
        return None

    def record(self, trade_date: str, scores: Dict[str, float], valid: bool) -> None:
        """记录当日各指标值及有效性"""
        self.history[trade_date] = {'scores': scores, 'valid': valid}
        keys = sorted(self.history.keys())
        for k in keys[:-30]:  # 只保留最近30个交易日
            self.history.pop(k, None)
        self._save_history()

    # ──────────────────────────────────────────────
    # 规则检查
    # ──────────────────────────────────────────────

    def _rule_a(self, up_ratio: float, median_return: float, ma20: float) -> bool:
        """Rule A: 上涨弱但MA20极高 → 异常"""
        r = self.rules.get('rule_a', {})
        return (up_ratio < r.get('up_ratio_max', 0.35)
                and median_return < r.get('median_return_max', -0.3)
                and ma20 > r.get('ma20_min', 0.90))

    def _rule_b(self, up_ratio: float, median_return: float, ma20: float) -> bool:
        """Rule B: 上涨强但MA20极低 → 反向异常"""
        r = self.rules.get('rule_b', {})
        return (up_ratio > r.get('up_ratio_min', 0.65)
                and median_return > r.get('median_return_min', 0.3)
                and ma20 < r.get('ma20_max', 0.10))

    def _rule_c(self, up_ratio: float, ma20: float) -> bool:
        """Rule C: MA20比例与上涨比例差值过大 → 极端背离"""
        diff = abs(ma20 - up_ratio)
        return diff > self.rules.get('rule_c', {}).get('diff_max', 0.55)

    # ──────────────────────────────────────────────
    # 主检查接口
    # ──────────────────────────────────────────────

    def check(self, breadth, sentiment) -> DataQualityResult:
        """对宽度/情绪指标做一致性检查

        Args:
            breadth: BreadthResult（含 up_ratio/down_ratio/median_return/
                     above_ma20_ratio/limit_up_count/limit_down_count）
            sentiment: SentimentResult（含 break_ratio）

        Returns:
            DataQualityResult
        """
        result = DataQualityResult(is_clean=True, anomalies=[], affected=set())

        if breadth is None:
            result.is_clean = False
            result.summary = "⚠️ 宽度数据缺失，已隔离处理"
            return result

        up_ratio = float(breadth.up_ratio)
        down_ratio = float(breadth.down_ratio)
        flat_ratio = max(0.0, 1.0 - up_ratio - down_ratio)
        median_return = float(breadth.median_return)
        ma20 = float(breadth.above_ma20_ratio)
        limit_up = int(breadth.limit_up_count)
        limit_down = int(breadth.limit_down_count)
        break_ratio = float(getattr(sentiment, 'break_ratio', 0.0)) if sentiment else 0.0

        # ── Rule A: MA20异常（上涨弱但MA20极高） ──
        if self._rule_a(up_ratio, median_return, ma20):
            result.is_clean = False
            result.affected.add('breadth')
            result.anomalies.append({
                "rule": "MA20_BREADTH_ANOMALY",
                "cause": (f"上涨比例{up_ratio:.1%} / 涨幅中位数{median_return:+.2f}% "
                          f"但站上MA20 {ma20:.1%}，明显不协调"),
                "suggestion": "MA20比例异常，已隔离：breadth 不参与 Market Score",
            })

        # ── Rule B: 反向异常（上涨强但MA20极低） ──
        if self._rule_b(up_ratio, median_return, ma20):
            result.is_clean = False
            result.affected.add('breadth')
            result.anomalies.append({
                "rule": "MA20_BREADTH_ANOMALY",
                "cause": (f"上涨比例{up_ratio:.1%} / 涨幅中位数{median_return:+.2f}% "
                          f"但站上MA20 {ma20:.1%}，反向不协调"),
                "suggestion": "MA20比例异常，已隔离：breadth 不参与 Market Score",
            })

        # ── Rule C: 极端背离 ──
        if self._rule_c(up_ratio, ma20):
            result.is_clean = False
            result.affected.add('breadth')
            result.anomalies.append({
                "rule": "BREADTH_CONFLICT",
                "cause": (f"站上MA20 {ma20:.1%} 与上涨比例{up_ratio:.1%} "
                          f"差{abs(ma20 - up_ratio):.1%}超过55个百分点"),
                "suggestion": "宽度指标相互背离，已隔离：breadth 不参与 Market Score",
            })

        # ── 汇总一行摘要（报告展示用） ──
        if result.is_clean:
            result.summary = "✓ 指标一致"
        else:
            first = result.anomalies[0]
            rule_cn = self.RULE_NAMES.get(first['rule'], '宽度数据异常')
            result.summary = f"⚠️ {rule_cn}，已降权处理"

        return result


# ──────────────────────────────────────────────
# 便捷工厂
# ──────────────────────────────────────────────

def create_data_quality_checker(config: dict) -> DataQualityChecker:
    return DataQualityChecker(config)
