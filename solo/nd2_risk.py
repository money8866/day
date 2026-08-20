# -*- coding: utf-8 -*-
"""
「猎尾V5」L3 Risk Engine 风险控制引擎 (最高 -20分)

  1. 高位风险        (-8): 距20日高过近 + 连续上涨 + 距MA20远
  2. 高换手风险      (-5): 区分低位抢筹(轻扣)/高位派发(重扣)
  3. 尾盘诱多        (-10): 量暴增价滞涨 + 长上影 + 收盘弱
  4. 孤立上涨        (-5): 主题全弱个股独涨 (STEALTH减半)
"""

from nd2_config import RISK, RISK_PENALTY_MAX


class RiskEngine:

    @staticmethod
    def score(f, pattern, turnover=0, theme_up_ratio=50, theme_limit_count=0,
              theme_leader_pct=0):
        """
        f: precompute特征
        返回 (penalty 0~20, detail)
        """
        penalty = 0
        d = {}

        price = f.get('price', 0)

        # ── 1. 高位风险 (最高-8) ──
        high_risk = 0
        dd = f.get('drawdown_20d', 0)
        g5 = f.get('gain_5d', 0)
        ma20_dist = (price / f['ma20'] - 1) * 100 if f.get('ma20', 0) > 0 and price > 0 else 0
        d['ma20_dist_pct'] = round(ma20_dist, 1)
        # 距20日高<2% + 5日涨幅>12% = 连续加速后高位
        if dd < 2 and g5 > 12:
            high_risk = RISK['high_position']
            d['risk_high_pos'] = f'距高{dd:.1f}% 5日涨{g5:.1f}%'
        elif dd < 2 and g5 > 8:
            high_risk = RISK['high_position'] // 2
            d['risk_high_pos'] = f'距高{dd:.1f}% 5日涨{g5:.1f}%'
        elif ma20_dist > 18:
            high_risk = RISK['high_position'] // 2
            d['risk_high_pos'] = f'距MA20 +{ma20_dist:.0f}%'
        penalty += high_risk

        # ── 2. 高换手风险 (最高-5, 区分低位/高位) ──
        turnover_risk = 0
        if turnover > 20:
            if dd < RISK['turnover_high_pos_threshold']:
                # 高位高换手 = 派发嫌疑, 重扣
                turnover_risk = RISK['high_turnover']
                d['risk_turnover'] = f'高位换手{turnover:.1f}%'
            else:
                # 低位高换手 = 可能抢筹, 轻扣
                turnover_risk = RISK['high_turnover'] // 2
                d['risk_turnover'] = f'低位换手{turnover:.1f}%'
        penalty += turnover_risk

        # ── 3. 尾盘诱多 (最高-10) ──
        tail_risk = 0
        tail_ret = 0
        if f.get('tail_base_price', 0) > 0 and price > 0:
            tail_ret = (price - f['tail_base_price']) / f['tail_base_price'] * 100
        ratio = f.get('tail_vs_noon_ratio')
        day_change = 0
        if f.get('open', 0) > 0 and price > 0:
            day_change = (price - f['open']) / f['open'] * 100

        # 3a. 量暴增但价滞涨 (派发)
        if ratio and ratio >= 1.8 and tail_ret < 0.1:
            tail_risk += 6
            d['tail_distribution'] = f'量比{ratio:.1f}价滞{tail_ret:+.1f}%'
        # 3b. 长上影 + 收盘弱
        if f.get('high', 0) > 0 and price > 0:
            upper_shadow = (f['high'] - price) / price * 100
            if upper_shadow > 1.5 and f.get('close_position', 0.5) < 0.75:
                tail_risk += 4
                d['upper_shadow_trap'] = f'上影{upper_shadow:.1f}%'
        # 3c. 尾拉但全天弱 (V3保留逻辑)
        if tail_ret > 0.5 and day_change < -0.3:
            tail_risk += 6
            d['trap_weak_day'] = f'全天{day_change:+.1f}%尾拉{tail_ret:+.1f}%'
        elif tail_ret > 0.3 and day_change < 0:
            tail_risk += 3
            d['trap_weak_day'] = f'全天{day_change:+.1f}%尾拉{tail_ret:+.1f}%'
        tail_risk = min(tail_risk, RISK['tail_distribution'])
        penalty += tail_risk

        # ── 4. 孤立上涨 (最高-5, STEALTH减半) ──
        isolated = 0
        if theme_limit_count == 0 and theme_up_ratio < 40 and f.get('pct', 0) > 1:
            isolated = RISK['isolated_rise']
            if pattern == 'STEALTH_ACCUMULATION':
                isolated = isolated // 2
            d['risk_isolated'] = f'主题涨比{theme_up_ratio:.0f}%涨停0'
        penalty += isolated

        return min(penalty, RISK_PENALTY_MAX), d
