"""Theme Diffusion Scorer - 主题扩散度评分器。

判断ETF/主题是否处于扩散阶段：
  - 龙头已涨完 → 资金开始向二三线扩散 → 补涨机会出现
  - 扩散度高的主题，补涨策略胜率更高

扩散信号：
  1. 成份股上涨扩散度：ETF内上涨股票占比变化
  2. 新高涨扩散度：创新高的股票数量变化
  3. 龙头-滞涨股分化度：龙头与中位数的涨幅差
  4. 资金集中度下降：龙头成交额占比下降
  5. 板块轮动信号：从龙头向边缘扩散

DiffusionScore: 0-100，越高代表扩散预期越强
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional
from dataclasses import dataclass

from etf_resonance.utils.helpers import safe_div, Config


@dataclass
class DiffusionResult:
    """Per-ETF diffusion scoring result."""
    etf_code: str
    theme: str
    diffusion_score: float          # 0-100 composite
    breadth_expansion: float        # 上涨扩散度
    new_high_expansion: float       # 新高扩散度
    leader_laggard_gap: float       # 龙头-滞涨股分化度
    concentration_decline: float    # 资金集中度下降
    rotation_signal: float          # 轮动信号
    # 诊断
    advancing_ratio: float          # 当前上涨股票占比
    advancing_ratio_5d_ago: float   # 5日前上涨占比
    new_high_count: int             # 近5日创新高股票数
    median_ret_20d: float           # 成份股20日涨幅中位数
    leader_ret_20d: float           # 龙头20日涨幅


class DiffusionScorer:
    """主题扩散度评分器。"""

    def __init__(self, config: Optional[Config] = None):
        cfg = config.get("diffusion", {}) if config else {}
        self.breadth_w = cfg.get("breadth_weight", 0.25)
        self.new_high_w = cfg.get("new_high_weight", 0.20)
        self.gap_w = cfg.get("gap_weight", 0.25)
        self.concentration_w = cfg.get("concentration_weight", 0.15)
        self.rotation_w = cfg.get("rotation_weight", 0.15)

    def score(self,
              stock_data: Dict[str, pd.DataFrame],
              etf_data: Dict[str, pd.DataFrame],
              constituents: Dict[str, List[str]],
              etf_theme_map: Optional[Dict[str, str]] = None
              ) -> Dict[str, DiffusionResult]:
        """Score all ETFs for diffusion potential."""
        results: Dict[str, DiffusionResult] = {}

        for etf_code, stock_codes in constituents.items():
            if etf_code not in etf_data:
                continue

            # 收集该ETF成份股数据
            valid_stocks = []
            for code in stock_codes:
                if code in stock_data and len(stock_data[code]) >= 30:
                    valid_stocks.append(code)

            if len(valid_stocks) < 5:
                continue

            etf_df = etf_data[etf_code]
            theme = etf_theme_map.get(etf_code, "") if etf_theme_map else ""

            result = self._score_etf(
                etf_code, etf_df, valid_stocks, stock_data, theme
            )
            if result is not None:
                results[etf_code] = result

        return results

    def _score_etf(self, etf_code: str, etf_df: pd.DataFrame,
                   stock_codes: List[str],
                   stock_data: Dict[str, pd.DataFrame],
                   theme: str) -> Optional[DiffusionResult]:
        """Score diffusion for a single ETF."""
        try:
            # 收集所有股票近20日涨幅
            rets_20d = []
            rets_5d = []
            vols_20d = []
            vols_5d = []
            new_highs_5d = 0
            new_highs_before = 0
            advancing_now = 0
            advancing_5d_ago = 0

            for code in stock_codes:
                df = stock_data[code]
                close = df["close"].values
                vol = df["vol"].values
                high = df["high"].values

                if len(close) < 25:
                    continue

                # 20日涨幅
                ret_20d = (close[-1] / close[-20] - 1) * 100
                rets_20d.append(ret_20d)

                # 5日涨幅
                ret_5d = (close[-1] / close[-5] - 1) * 100
                rets_5d.append(ret_5d)

                # 量能
                vols_20d.append(np.mean(vol[-20:]))
                vols_5d.append(np.mean(vol[-5:]))

                # 创新高判断
                if len(high) >= 25:
                    hh_before = np.max(high[-25:-5])  # 5天前的60日高点
                    hh_now = np.max(high[-20:])  # 近20日高点
                    if high[-1] >= hh_before:
                        new_highs_before += 1
                    if high[-1] >= hh_now * 0.98:  # 接近新高
                        new_highs_5d += 1

                # 上涨股票
                if ret_20d > 0:
                    advancing_now += 1
                if len(close) >= 25 and close[-5] > close[-20]:
                    advancing_5d_ago += 1

            n = len(rets_20d)
            if n < 5:
                return None

            rets_20d = np.array(rets_20d)
            rets_5d = np.array(rets_5d)
            vols_20d = np.array(vols_20d)
            vols_5d = np.array(vols_5d)

            # === 1. 上涨扩散度 (breadth_expansion) ===
            # 上涨股票占比提升
            advancing_ratio = advancing_now / n
            advancing_ratio_5d_ago = advancing_5d_ago / n
            breadth_change = advancing_ratio - advancing_ratio_5d_ago

            # 扩散度: 占比提升且占比 > 0.5
            breadth_expansion = 0
            if advancing_ratio > 0.6:
                breadth_expansion += 40
            elif advancing_ratio > 0.4:
                breadth_expansion += 25

            if breadth_change > 0.1:
                breadth_expansion += 30
            elif breadth_change > 0:
                breadth_expansion += 15

            # 中位数涨幅为正
            median_ret = np.median(rets_20d)
            if median_ret > 0:
                breadth_expansion += 30
            elif median_ret > -3:
                breadth_expansion += 15

            breadth_expansion = min(100, breadth_expansion)

            # === 2. 新高扩散度 (new_high_expansion) ===
            new_high_expansion = min(100, (new_highs_5d / n) * 200)

            # === 3. 龙头-滞涨股分化度 (leader_laggard_gap) ===
            # 龙头涨幅 vs 中位数涨幅，分化越大说明扩散预期越强
            leader_ret = np.max(rets_20d)
            median_ret_20d = np.median(rets_20d)
            gap = leader_ret - median_ret_20d

            # 分化度评分：gap在20-50之间最优（龙头已涨但中位数还没跟上）
            leader_laggard_gap = 0
            if 20 <= gap <= 50:
                leader_laggard_gap = 100
            elif 10 <= gap < 20:
                leader_laggard_gap = 70
            elif 50 < gap <= 80:
                leader_laggard_gap = 60  # 分化过大可能见顶
            elif gap > 80:
                leader_laggard_gap = 30  # 主升浪末期
            else:
                leader_laggard_gap = 20  # 无明显分化

            # === 4. 资金集中度下降 (concentration_decline) ===
            # 龙头成交额占比下降，说明资金在扩散
            if len(vols_20d) >= 5 and np.sum(vols_20d) > 0:
                leader_vol_share_now = np.max(vols_5d) / np.sum(vols_5d) if np.sum(vols_5d) > 0 else 0
                leader_vol_share_before = np.max(vols_20d) / np.sum(vols_20d) if np.sum(vols_20d) > 0 else 0
                share_decline = leader_vol_share_before - leader_vol_share_now

                if share_decline > 0.05:
                    concentration_decline = 100  # 明显扩散
                elif share_decline > 0.02:
                    concentration_decline = 70
                elif share_decline > 0:
                    concentration_decline = 40
                else:
                    concentration_decline = 10  # 资金仍集中
            else:
                concentration_decline = 30

            # === 5. 轮动信号 (rotation_signal) ===
            # 滞涨股近期涨幅 > 龙头近期涨幅 = 轮动开始
            bottom_rets_5d = np.percentile(rets_5d, 25)  # 底部25%分位的5日涨幅
            top_rets_5d = np.percentile(rets_5d, 75)  # 顶部25%分位

            rotation_signal = 0
            if bottom_rets_5d > top_rets_5d and bottom_rets_5d > 0:
                rotation_signal = 100  # 明确轮动
            elif bottom_rets_5d > 0 and bottom_rets_5d > top_rets_5d * 0.5:
                rotation_signal = 70  # 初步轮动
            elif bottom_rets_5d > 0:
                rotation_signal = 40  # 普涨
            else:
                rotation_signal = 10  # 无轮动

            # === 综合评分 ===
            diffusion_score = (
                breadth_expansion * self.breadth_w +
                new_high_expansion * self.new_high_w +
                leader_laggard_gap * self.gap_w +
                concentration_decline * self.concentration_w +
                rotation_signal * self.rotation_w
            )

            return DiffusionResult(
                etf_code=etf_code,
                theme=theme,
                diffusion_score=round(float(diffusion_score), 2),
                breadth_expansion=round(float(breadth_expansion), 2),
                new_high_expansion=round(float(new_high_expansion), 2),
                leader_laggard_gap=round(float(leader_laggard_gap), 2),
                concentration_decline=round(float(concentration_decline), 2),
                rotation_signal=round(float(rotation_signal), 2),
                advancing_ratio=round(float(advancing_ratio), 3),
                advancing_ratio_5d_ago=round(float(advancing_ratio_5d_ago), 3),
                new_high_count=int(new_highs_5d),
                median_ret_20d=round(float(median_ret_20d), 2),
                leader_ret_20d=round(float(leader_ret), 2),
            )

        except Exception:
            return None
