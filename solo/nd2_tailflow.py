# -*- coding: utf-8 -*-
"""
「猎尾V5」L3 Tail Flow Engine 尾盘资金抢筹引擎 (25分, V5核心)

  1. Tail Volume Expansion  (0~10): vol(14:30~14:50) / vol(14:00~14:30)
  2. Tail Price Acceleration(0~5):  price_1450/price_1430 - 1 (温和最优)
  3. Close Position         (0~6):  (price-low)/(high-low)
  4. Buy Pressure           (0~4):  代理指标(尾盘阳线效率)

同时识别: 有效放量 / 无效放量 / 高位派发 (量增价不涨)
"""

from nd2_config import TAIL_FLOW


def _table_score(table, top_score, value):
    """查表得分: table = [(上限, 得分), ...] 升序"""
    for bound, sc in table:
        if value < bound:
            return sc
    return top_score


class TailFlowEngine:

    @staticmethod
    def score(f):
        """
        f: PatternClassifier.precompute 输出的特征字典
        返回 (score 0~25, detail)
        """
        score = 0
        detail = {}

        # ── 1. 尾盘量能扩张 (0~10) ──
        ratio = f.get('tail_vs_noon_ratio')
        if ratio is None:
            # 无分时快照: 用当日量/昨日量代理(弱信号)
            if f.get('vol_yesterday', 0) > 0 and f.get('cur_vol', 0) > 0:
                ratio = min(f['cur_vol'] / f['vol_yesterday'], 3.0)
                detail['vol_ratio_proxy'] = True
            else:
                ratio = 0
        detail['tail_volume_ratio'] = round(ratio, 2) if ratio else 0
        s_vol = _table_score(TAIL_FLOW['vol_expansion_table'], TAIL_FLOW['vol_expansion_top_score'], ratio)
        score += s_vol
        detail['s_vol_expansion'] = s_vol

        # ── 2. 尾盘价格加速度 (0~5) ──
        tail_ret = 0
        if f.get('tail_base_price', 0) > 0 and f.get('price', 0) > 0:
            tail_ret = (f['price'] - f['tail_base_price']) / f['tail_base_price'] * 100
        detail['tail_return'] = round(tail_ret, 2)
        s_acc = _table_score(TAIL_FLOW['price_accel_table'], TAIL_FLOW['price_accel_top_score'], tail_ret)
        score += s_acc
        detail['s_price_accel'] = s_acc

        # ── 3. 收盘位置 (0~6) ──
        cp = f.get('close_position', 0.5)
        detail['close_position'] = round(cp, 3)
        s_cp = _table_score(TAIL_FLOW['close_pos_table'], TAIL_FLOW['close_pos_top_score'], cp)
        score += s_cp
        detail['s_close_position'] = s_cp

        # ── 4. 买压代理 (0~4) ──
        # 无逐笔主动买卖数据, 用组合代理: 量价配合度(放量上涨强/缩量上涨中/量增价滞弱)
        buy_p = 50  # 中性基线
        if ratio and ratio > 0 and tail_ret > 0:
            if ratio >= 1.2:
                # 放量上涨 = 主动买盘抢筹(量价齐升是抢筹最直接证据)
                if tail_ret >= 0.3:
                    buy_p = 65 if ratio >= 1.8 else 58
                else:
                    buy_p = 52
            elif ratio >= 0.8:
                # 常量上涨
                buy_p = 55 if tail_ret >= 0.3 else 51
            else:
                # 缩量上涨(拉升无承接,可能虚)
                buy_p = 48
        elif ratio and ratio > 1.5 and tail_ret <= 0.05:
            # 量增价不涨: 派发嫌疑
            buy_p = 40
            detail['distribution_suspect'] = True
        elif ratio and ratio >= 1.8 and tail_ret < -0.3:
            # 放量下跌
            buy_p = 35
        detail['buy_pressure_proxy'] = buy_p
        s_bp = _table_score(TAIL_FLOW['buy_pressure_table'], TAIL_FLOW['buy_pressure_top_score'], buy_p)
        score += s_bp
        detail['s_buy_pressure'] = s_bp

        # ── 无效放量识别: 量比>1.8 但涨幅<0.1% ──
        if ratio and ratio >= 1.8 and tail_ret < 0.1:
            detail['invalid_volume'] = True   # 量增价滞 = 高位派发风险
        # ── 有效放量: 量比>=1.2 且 涨幅>=0.3% ──
        if ratio and ratio >= 1.2 and tail_ret >= 0.3:
            detail['effective_volume'] = True

        return min(score, 25), detail
