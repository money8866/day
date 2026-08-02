#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
增强版尾盘评分引擎 - 六维评分模型 + 诱多识别 + 量价共振

评分体系 (满分115分 - 扣分后):
  1. 尾盘攻击力 (35分): 尾盘拉升幅度 + 量能爆发 + 收盘位置 + 分时斜率
  2. 全天结构   (25分): 振幅控制 + 阳线实体 + 缩量蓄力 + 开盘强度
  3. 位置安全   (20分): MA5/MA10支撑 + 20日高回撤 + 突破确认
  4. 技术共振   (20分): MACD + KDJ + RSI + BOLL + CCI
  5. 主题共振   (10分): 主题强度 + 龙头地位 + 涨停配合
  6. 资金验证   (5分):  换手率健康 + 量比合理

  诱多扣分 (最高-30分): 四大红旗 + 分时异常

硬过滤: 涨停/跌停/振幅>9%/跌>3%/连板>=2/距MA20>28%/5日涨>18%/换手异常/市值<10亿
"""
import numpy as np
import pandas as pd
from typing import Dict, Tuple, Optional, List
from dataclasses import dataclass, field


@dataclass
class TailSignal:
    """尾盘信号"""
    ts_code: str
    name: str
    theme: str
    trade_date: str
    total_score: float = 0.0
    attack_score: float = 0.0
    structure_score: float = 0.0
    position_score: float = 0.0
    technical_score: float = 0.0
    theme_score: float = 0.0
    capital_score: float = 0.0
    trap_penalty: float = 0.0
    signal: str = ''  # 强买入/买入/关注
    pct_chg: float = 0.0
    price: float = 0.0
    detail: Dict = field(default_factory=dict)


class TailScoringEngine:
    """
    尾盘战法评分引擎
    
    核心胜率逻辑:
    - 尾盘放量拉升 + 收在高位 = 主力次日惯性做多概率极高
    - 缩量回调到MA5/MA10 + MACD多头 = 技术面共振,胜率叠加
    - 主题内有涨停配合 + 非龙头 = 补涨逻辑,溢价空间大
    - 诱多识别 = 排除"全天弱势尾盘偷袭"的陷阱
    """

    def __init__(self):
        # 硬过滤参数
        self.max_amplitude = 9.0       # 最大振幅%
        self.max_drop = -3.0           # 最大跌幅%
        self.max_ma20_dist = 28.0      # 距MA20最大偏离%
        self.max_5d_gain = 18.0        # 5日最大涨幅%
        self.max_turnover = 18.0       # 最大换手率%
        self.min_turnover = 0.5        # 最小换手率%
        self.min_market_cap = 100000   # 最小市值(万元) = 10亿

    # ═══════════════════════════════════════════
    # 硬过滤
    # ═══════════════════════════════════════════
    def hard_filter(self, ts_code: str, row: pd.Series, daily_df: pd.DataFrame,
                    factor_row: Optional[pd.Series] = None) -> Tuple[bool, str]:
        """
        硬过滤: 返回 (通过?, 原因)
        row: 当日行情数据 (open, high, low, close, vol, pct_chg, ...)
        daily_df: 历史日线(含当日之前)
        """
        pct = float(row.get('pct_chg', 0))
        high = float(row.get('high', 0))
        low = float(row.get('low', 0))
        close = float(row.get('close', 0))
        pre_close = float(row.get('pre_close', 0))

        # 1. 涨停/跌停
        limit_up = 19.5 if ts_code.startswith(('300', '688')) else 9.5
        if pct >= limit_up:
            return False, '涨停'
        if pct <= -9.5:
            return False, '跌停'

        # 2. 振幅过大
        if pre_close > 0 and high > 0 and low > 0:
            amplitude = (high - low) / pre_close * 100
            if amplitude > self.max_amplitude:
                return False, f'振幅{amplitude:.1f}%'

        # 3. 跌幅过大
        if pct < self.max_drop:
            return False, f'跌{pct:.1f}%'

        # 4. 连板>=2
        if daily_df is not None and len(daily_df) >= 3:
            prev_pcts = daily_df['pct_chg'].iloc[-2:].values.astype(float)
            prev_limit = 19.5 if ts_code.startswith(('300', '688')) else 9.5
            if all(p >= prev_limit for p in prev_pcts):
                return False, '连板2天'

        # 5. 距MA20太远
        if daily_df is not None and len(daily_df) >= 20:
            ma20 = daily_df['close'].iloc[-20:].mean()
            if close > 0 and ma20 > 0 and close > ma20 * (1 + self.max_ma20_dist / 100):
                return False, f'距MA20>{self.max_ma20_dist}%'

        # 6. 5日涨幅过大
        if daily_df is not None and len(daily_df) >= 6:
            close_5d_ago = float(daily_df['close'].iloc[-6])
            if close_5d_ago > 0 and close > 0:
                gain_5d = (close - close_5d_ago) / close_5d_ago * 100
                if gain_5d > self.max_5d_gain:
                    return False, f'5日涨{gain_5d:.1f}%'

        # 7. 换手率异常
        if factor_row is not None:
            turnover = float(factor_row.get('turnover_rate', 0) or 0)
            if turnover > 0:
                if turnover > self.max_turnover:
                    return False, f'换手{turnover:.1f}%过高'
                if turnover < self.min_turnover:
                    return False, f'换手{turnover:.1f}%过低'

        # 8. 市值过小
        if factor_row is not None:
            total_mv = float(factor_row.get('total_mv', 0) or 0)
            if 0 < total_mv < self.min_market_cap:
                return False, f'市值{total_mv/10000:.1f}亿'

        return True, 'OK'

    # ═══════════════════════════════════════════
    # 维度1: 尾盘攻击力 (35分)
    # ═══════════════════════════════════════════
    def score_attack(self, ts_code: str, row: pd.Series, daily_df: pd.DataFrame) -> Tuple[float, Dict]:
        """
        尾盘攻击力评分:
        - 尾盘拉升幅度 (12分): 用(close-low)/(high-low)近似尾盘强度
        - 量能爆发 (12分): 当日量vs5日均量, 放量上攻
        - 收盘位置 (11分): close/high 越接近1越好(光头阳)
        """
        score = 0.0
        detail = {}

        close = float(row.get('close', 0))
        high = float(row.get('high', 0))
        low = float(row.get('low', 0))
        open_p = float(row.get('open', 0))
        vol = float(row.get('vol', 0))
        pct = float(row.get('pct_chg', 0))

        # ── 尾盘拉升幅度 (12分) ──
        # 用 (close - low) / (high - low) 衡量收盘在日内区间的位置
        # 结合涨幅判断尾盘拉升强度
        if high > low and close > 0:
            close_position = (close - low) / (high - low)
            detail['close_position'] = round(close_position, 3)
            # 尾盘拉升 = 收在高位 + 涨幅为正
            if close_position > 0.85 and pct > 1.5:
                score += 12
            elif close_position > 0.75 and pct > 0.5:
                score += 9
            elif close_position > 0.65 and pct > 0:
                score += 6
            elif close_position > 0.55:
                score += 3
        else:
            detail['close_position'] = 0

        # ── 量能爆发 (12分) ──
        if daily_df is not None and len(daily_df) >= 5 and vol > 0:
            avg_vol_5d = daily_df['vol'].iloc[-5:].mean()
            if avg_vol_5d > 0:
                vol_ratio = vol / avg_vol_5d
                detail['vol_ratio'] = round(vol_ratio, 2)
                # 温和放量(1.2-2.5倍)最佳, 过度放量(>3倍)可能是出货
                if 1.3 <= vol_ratio <= 2.5:
                    score += 12
                elif 1.1 <= vol_ratio < 1.3:
                    score += 8
                elif 2.5 < vol_ratio <= 3.5:
                    score += 7
                elif vol_ratio > 3.5:
                    score += 3  # 过度放量减分
                elif 0.8 <= vol_ratio < 1.1:
                    score += 5  # 平量也可以
                else:
                    score += 2
            else:
                detail['vol_ratio'] = 0
        else:
            detail['vol_ratio'] = 0

        # ── 收盘位置/光头阳 (11分) ──
        if high > 0 and close > 0:
            close_ratio = close / high
            detail['close_ratio'] = round(close_ratio, 3)
            if close_ratio >= 0.99:
                score += 11  # 光头阳线
            elif close_ratio >= 0.97:
                score += 8
            elif close_ratio >= 0.93:
                score += 5
            elif close_ratio >= 0.88:
                score += 2
        else:
            detail['close_ratio'] = 0

        return min(score, 35), detail

    # ═══════════════════════════════════════════
    # 维度2: 全天结构 (25分)
    # ═══════════════════════════════════════════
    def score_structure(self, ts_code: str, row: pd.Series, daily_df: pd.DataFrame) -> Tuple[float, Dict]:
        """
        全天结构质量:
        - 振幅控制 (8分): 振幅小=主力控盘
        - 阳线实体 (7分): 实体占比大=多头强势
        - 缩量蓄力 (5分): 前几日缩量=洗盘结束
        - 开盘强度 (5分): 高开=资金抢筹
        """
        score = 0.0
        detail = {}

        close = float(row.get('close', 0))
        high = float(row.get('high', 0))
        low = float(row.get('low', 0))
        open_p = float(row.get('open', 0))
        pre_close = float(row.get('pre_close', 0))
        pct = float(row.get('pct_chg', 0))

        # ── 振幅控制 (8分) ──
        if pre_close > 0 and high > 0 and low > 0:
            amplitude = (high - low) / pre_close * 100
            detail['amplitude'] = round(amplitude, 1)
            if amplitude < 3:
                score += 8
            elif amplitude < 4.5:
                score += 6
            elif amplitude < 6:
                score += 4
            elif amplitude < 8:
                score += 2
        else:
            detail['amplitude'] = 0

        # ── 阳线实体 (7分) ──
        if close > 0 and open_p > 0 and high > low:
            body = abs(close - open_p)
            body_ratio = body / (high - low) if high > low else 0
            detail['body_ratio'] = round(body_ratio, 2)
            if close > open_p:  # 阳线
                if body_ratio > 0.7 and pct > 2:
                    score += 7
                elif body_ratio > 0.5 and pct > 1:
                    score += 5
                elif pct > 0:
                    score += 3
            else:
                # 阴线但跌幅小,可能是洗盘
                if pct > -0.5:
                    score += 1
        else:
            detail['body_ratio'] = 0

        # ── 缩量蓄力 (5分): 前3日平均量 < 前10日平均量 ──
        if daily_df is not None and len(daily_df) >= 10:
            vol_3d = daily_df['vol'].iloc[-3:].mean()
            vol_10d = daily_df['vol'].iloc[-10:].mean()
            if vol_10d > 0:
                shrink_ratio = vol_3d / vol_10d
                detail['shrink_ratio'] = round(shrink_ratio, 2)
                if shrink_ratio < 0.6:
                    score += 5  # 极度缩量
                elif shrink_ratio < 0.75:
                    score += 4
                elif shrink_ratio < 0.9:
                    score += 2
                elif shrink_ratio < 1.0:
                    score += 1
            else:
                detail['shrink_ratio'] = 0
        else:
            detail['shrink_ratio'] = 0

        # ── 开盘强度 (5分) ──
        if pre_close > 0 and open_p > 0:
            open_gap = (open_p - pre_close) / pre_close * 100
            detail['open_gap'] = round(open_gap, 2)
            if 0.5 <= open_gap <= 2.0:
                score += 5  # 适度高开
            elif 0 < open_gap < 0.5:
                score += 3
            elif 2.0 < open_gap <= 3.5:
                score += 3  # 高开太多有回落风险
            elif open_gap == 0:
                score += 2  # 平开
            # 低开不加分
        else:
            detail['open_gap'] = 0

        return min(score, 25), detail

    # ═══════════════════════════════════════════
    # 维度3: 位置安全 (20分)
    # ═══════════════════════════════════════════
    def score_position(self, ts_code: str, row: pd.Series, daily_df: pd.DataFrame) -> Tuple[float, Dict]:
        """
        位置安全边际:
        - MA5支撑 (7分): 价格在MA5附近或上方
        - MA10支撑 (6分): 价格在MA10上方
        - 20日高回撤 (4分): 有回撤=有空间
        - 突破确认 (3分): 突破近期平台
        """
        score = 0.0
        detail = {}
        close = float(row.get('close', 0))

        if daily_df is None or len(daily_df) < 20 or close <= 0:
            return 5.0, {'note': '数据不足'}

        closes = daily_df['close'].values.astype(float)
        highs = daily_df['high'].values.astype(float)

        ma5 = closes[-5:].mean()
        ma10 = closes[-10:].mean()
        ma20 = closes[-20:].mean()
        high_20d = highs[-20:].max()

        # ── MA5支撑 (7分) ──
        if ma5 > 0:
            ma5_dist = (close - ma5) / ma5 * 100
            detail['ma5_dist'] = round(ma5_dist, 1)
            if -1 <= ma5_dist <= 3:
                score += 7  # 贴近MA5,支撑有效
            elif 3 < ma5_dist <= 6:
                score += 4
            elif -3 <= ma5_dist < -1:
                score += 3  # 略低于MA5,可能回踩
            elif ma5_dist > 6:
                score += 1  # 偏离太远
        else:
            detail['ma5_dist'] = 0

        # ── MA10支撑 (6分) ──
        if ma10 > 0:
            ma10_ratio = close / ma10
            detail['ma10_ratio'] = round(ma10_ratio, 3)
            if 0.98 <= ma10_ratio <= 1.06:
                score += 6
            elif 0.95 <= ma10_ratio <= 1.10:
                score += 4
            elif ma10_ratio > 1.10:
                score += 1
            else:
                score += 2  # 低于MA10但不多
        else:
            detail['ma10_ratio'] = 0

        # ── 20日高回撤 (4分) ──
        if high_20d > 0:
            pullback = (high_20d - close) / high_20d * 100
            detail['pullback_20d'] = round(pullback, 1)
            if 3 <= pullback <= 12:
                score += 4  # 适度回撤,有空间
            elif 0 < pullback < 3:
                score += 2  # 接近高点
            elif pullback > 12:
                score += 2  # 回撤太深,趋势可能破坏
        else:
            detail['pullback_20d'] = 0

        # ── 突破确认 (3分): 收盘突破近10日最高收盘价 ──
        if len(closes) >= 11:
            high_close_10d = closes[-11:-1].max()  # 前10日最高收盘
            if close > high_close_10d:
                score += 3
                detail['breakout'] = True
            elif close > high_close_10d * 0.98:
                score += 1
                detail['breakout'] = False
            else:
                detail['breakout'] = False
        else:
            detail['breakout'] = False

        return min(score, 20), detail

    # ═══════════════════════════════════════════
    # 维度4: 技术共振 (20分)
    # ═══════════════════════════════════════════
    def score_technical(self, ts_code: str, factor_row: Optional[pd.Series]) -> Tuple[float, Dict]:
        """
        技术共振评分 (基于stk_factor_pro):
        - MACD (6分): 金叉/多头 + 零轴位置
        - KDJ (5分): J值健康 + 金叉
        - RSI (4分): 40-70健康区
        - BOLL (3分): 中轨上方
        - CCI (2分): 正向动能
        """
        score = 0.0
        detail = {}

        if factor_row is None:
            return 0, detail

        # ── MACD (6分) ──
        try:
            dif = float(factor_row.get('macd_dif_bfq', 0) or 0)
            dea = float(factor_row.get('macd_dea_bfq', 0) or 0)
            macd = float(factor_row.get('macd_bfq', 0) or 0)
            if dif > dea:
                score += 3
                detail['macd'] = '多头'
                if dif > 0 and dea > 0:
                    score += 2
                    detail['macd'] = '零上多头'
                # MACD柱放大(动能增强)
                if macd > 0:
                    score += 1
            elif dif > 0:
                score += 1  # 虽死叉但仍在零轴上
                detail['macd'] = '零上空头'
        except Exception:
            pass

        # ── KDJ (5分) ──
        try:
            kdj_j = float(factor_row.get('kdj_bfq', 50) or 50)
            kdj_k = float(factor_row.get('kdj_k_bfq', 50) or 50)
            kdj_d = float(factor_row.get('kdj_d_bfq', 50) or 50)
            detail['kdj_j'] = round(kdj_j, 1)
            if 20 < kdj_j < 80:
                if kdj_j > kdj_k:  # J上穿K=金叉
                    score += 5
                    detail['kdj'] = '金叉'
                elif kdj_k > kdj_d:  # K>D=多头排列
                    score += 3
                    detail['kdj'] = '多头'
                else:
                    score += 2
                    detail['kdj'] = '健康'
            elif kdj_j <= 20:
                score += 2  # 超卖区,有反弹空间
                detail['kdj'] = '超卖'
            # J>80 超买不加分
        except Exception:
            pass

        # ── RSI (4分) ──
        try:
            rsi_6 = float(factor_row.get('rsi_bfq_6', 50) or 50)
            rsi_12 = float(factor_row.get('rsi_bfq_12', 50) or 50)
            detail['rsi_6'] = round(rsi_6, 1)
            if 45 <= rsi_6 <= 70:
                score += 4  # 健康强势区
            elif 35 <= rsi_6 < 45:
                score += 3  # 偏弱但有反弹空间
            elif 70 < rsi_6 <= 80:
                score += 1  # 偏强但接近超买
            elif rsi_6 < 35:
                score += 2  # 超卖反弹
            # RSI6 > RSI12 短期强于中期
            if rsi_6 > rsi_12 and rsi_6 < 75:
                score += 0  # 已在区间内体现
        except Exception:
            pass

        # ── BOLL (3分) ──
        try:
            close = float(factor_row.get('close', 0) or 0)
            boll_mid = float(factor_row.get('boll_mid_bfq', 0) or 0)
            boll_upper = float(factor_row.get('boll_upper_bfq', 0) or 0)
            boll_lower = float(factor_row.get('boll_lower_bfq', 0) or 0)
            if close > 0 and boll_mid > 0:
                if close > boll_mid:
                    score += 2
                    detail['boll'] = '中轨上方'
                    if boll_upper > close and (boll_upper - close) / close < 0.03:
                        score += 1
                        detail['boll'] = '接近上轨'
                elif boll_lower > 0 and (close - boll_lower) / boll_lower < 0.02:
                    score += 1
                    detail['boll'] = '下轨支撑'
        except Exception:
            pass

        # ── CCI (2分) ──
        try:
            cci = float(factor_row.get('cci_bfq', 0) or 0)
            detail['cci'] = round(cci, 1)
            if 0 < cci < 200:
                score += 2  # 正向动能
            elif -100 < cci <= 0:
                score += 1  # 即将转正
        except Exception:
            pass

        return min(score, 20), detail

    # ═══════════════════════════════════════════
    # 维度5: 主题共振 (10分)
    # ═══════════════════════════════════════════
    def score_theme(self, ts_code: str, row: pd.Series,
                    theme_stocks: Dict, stock_themes: Dict,
                    all_quotes: Dict = None) -> Tuple[float, Dict]:
        """
        主题共振:
        - 所属主题强度 (4分)
        - 龙头/中军地位 (4分)
        - 主题内涨停配合 (2分)
        """
        score = 0.0
        detail = {}

        themes = stock_themes.get(ts_code, [])
        if not themes:
            return 0, detail

        best_theme = themes[0]
        best_layer = 'member'
        best_zt = 0

        for theme_name in themes:
            stocks = theme_stocks.get(theme_name, [])
            zt_cnt = 0
            layer = 'member'
            theme_pct_sum = 0
            theme_valid = 0

            for code, name, ly in stocks:
                if code == ts_code:
                    layer = ly
                if all_quotes and code in all_quotes:
                    q_pct = all_quotes[code].get('pct_chg', 0)
                    theme_pct_sum += q_pct
                    theme_valid += 1
                    limit = 19.5 if code.startswith(('300', '688')) else 9.5
                    if q_pct >= limit:
                        zt_cnt += 1

            if layer in ('leader', 'middle') or zt_cnt > best_zt:
                best_theme = theme_name
                best_layer = layer
                best_zt = zt_cnt

        detail['theme'] = best_theme
        detail['layer'] = best_layer
        detail['theme_zt'] = best_zt

        # 主题强度 (4分) - 基于主题内平均涨幅
        if all_quotes:
            stocks = theme_stocks.get(best_theme, [])
            pcts = [all_quotes[c].get('pct_chg', 0) for c, _, _ in stocks if c in all_quotes]
            if pcts:
                avg_pct = np.mean(pcts)
                detail['theme_avg_pct'] = round(avg_pct, 2)
                if avg_pct > 2:
                    score += 4
                elif avg_pct > 1:
                    score += 3
                elif avg_pct > 0:
                    score += 2
                else:
                    score += 1
            else:
                score += 2
        else:
            score += 2  # 无实时数据给基础分

        # 龙头地位 (4分)
        if best_layer == 'leader':
            score += 4
        elif best_layer == 'middle':
            score += 3
        else:
            score += 1

        # 涨停配合 (2分)
        if best_zt >= 2:
            score += 2
        elif best_zt >= 1:
            score += 1

        return min(score, 10), detail

    # ═══════════════════════════════════════════
    # 维度6: 资金验证 (5分)
    # ═══════════════════════════════════════════
    def score_capital(self, ts_code: str, row: pd.Series,
                      factor_row: Optional[pd.Series]) -> Tuple[float, Dict]:
        """
        资金验证:
        - 换手率健康 (3分): 3-10%为健康区间
        - 量比合理 (2分): 量比1-3为正常放量
        """
        score = 0.0
        detail = {}

        if factor_row is not None:
            turnover = float(factor_row.get('turnover_rate', 0) or 0)
            vol_ratio = float(factor_row.get('volume_ratio', 0) or 0)

            detail['turnover'] = round(turnover, 1)
            detail['volume_ratio'] = round(vol_ratio, 2)

            # 换手率 (3分)
            if 3 <= turnover <= 10:
                score += 3
            elif 2 <= turnover < 3 or 10 < turnover <= 15:
                score += 2
            elif 1 <= turnover < 2:
                score += 1

            # 量比 (2分)
            if 1.0 <= vol_ratio <= 3.0:
                score += 2
            elif 0.7 <= vol_ratio < 1.0 or 3.0 < vol_ratio <= 5.0:
                score += 1
        else:
            # 无因子数据时用日线量比估算
            vol = float(row.get('vol', 0))
            if vol > 0:
                score += 1  # 基础分

        return min(score, 5), detail

    # ═══════════════════════════════════════════
    # 诱多风险扣分 (最高-30分)
    # ═══════════════════════════════════════════
    def score_trap_penalty(self, ts_code: str, row: pd.Series,
                           daily_df: pd.DataFrame) -> Tuple[float, Dict]:
        """
        诱多风险识别 - 四大红旗:
        1. 全天弱势+尾盘急拉 (-15): 最危险
        2. 长下影线+尾盘拉回 (-10): 盘中暴跌
        3. 高位滞涨+尾盘偷袭 (-8): 近期涨太多
        4. 上影线过长 (-5): 冲高回落
        5. 放量阴线 (-7): 主力出货
        """
        penalty = 0.0
        detail = {}

        close = float(row.get('close', 0))
        high = float(row.get('high', 0))
        low = float(row.get('low', 0))
        open_p = float(row.get('open', 0))
        pre_close = float(row.get('pre_close', 0))
        pct = float(row.get('pct_chg', 0))

        if close <= 0 or open_p <= 0 or high <= 0 or low <= 0:
            return 0, detail

        price_range = high - low
        body = abs(close - open_p)
        lower_shadow = min(open_p, close) - low
        upper_shadow = high - max(open_p, close)

        # ── 红旗1: 全天弱势+尾盘急拉 (-15) ──
        # 收阴(close < open) 但 close在高位(close_position > 0.7)
        if price_range > 0:
            close_position = (close - low) / price_range
            day_change = (close - open_p) / open_p * 100
            if close < open_p and close_position > 0.7 and pct < 0:
                penalty += 15
                detail['trap_weak_tail'] = f'阴{day_change:+.1f}%位{close_position:.0%}'
            elif close < open_p and close_position > 0.6:
                penalty += 8
                detail['trap_weak_tail'] = f'阴{day_change:+.1f}%位{close_position:.0%}'

        # ── 红旗2: 长下影线 (-10) ──
        if price_range > 0:
            lower_ratio = lower_shadow / price_range
            body_ratio = body / price_range
            if lower_ratio > 0.45 and body_ratio < 0.25:
                penalty += 10
                detail['trap_lower_shadow'] = f'下影{lower_ratio:.0%}'
            elif lower_ratio > 0.35 and body_ratio < 0.3:
                penalty += 5
                detail['trap_lower_shadow'] = f'下影{lower_ratio:.0%}'

        # ── 红旗3: 高位滞涨 (-8) ──
        if daily_df is not None and len(daily_df) >= 6:
            close_5d_ago = float(daily_df['close'].iloc[-6])
            if close_5d_ago > 0:
                gain_5d = (close - close_5d_ago) / close_5d_ago * 100
                if gain_5d > 10 and pct < 0.5:
                    penalty += 8
                    detail['trap_high_stall'] = f'5日{gain_5d:.1f}%今{pct:+.1f}%'
                elif gain_5d > 8 and pct < 0:
                    penalty += 5
                    detail['trap_high_stall'] = f'5日{gain_5d:.1f}%今{pct:+.1f}%'

        # ── 红旗4: 上影线过长 (-5) ──
        if price_range > 0 and body > 0:
            if upper_shadow > body * 2.5 and upper_shadow / price_range > 0.3:
                penalty += 5
                detail['trap_upper_shadow'] = f'上影{upper_shadow/price_range:.0%}'

        # ── 红旗5: 放量阴线 (-7) ──
        if pct < -1 and daily_df is not None and len(daily_df) >= 5:
            avg_vol_5d = daily_df['vol'].iloc[-5:].mean()
            vol = float(row.get('vol', 0))
            if avg_vol_5d > 0 and vol / avg_vol_5d > 1.8:
                penalty += 7
                detail['trap_vol_yin'] = f'量比{vol/avg_vol_5d:.1f}跌{pct:.1f}%'

        return min(penalty, 30), detail

    # ═══════════════════════════════════════════
    # 综合评分
    # ═══════════════════════════════════════════
    def score_stock(self, ts_code: str, name: str, row: pd.Series,
                    daily_df: pd.DataFrame,
                    factor_row: Optional[pd.Series] = None,
                    theme_stocks: Dict = None,
                    stock_themes: Dict = None,
                    all_quotes: Dict = None,
                    trade_date: str = '') -> Optional[TailSignal]:
        """
        对单只股票进行完整评分
        返回 TailSignal 或 None(被硬过滤)
        """
        # 硬过滤
        passed, reason = self.hard_filter(ts_code, row, daily_df, factor_row)
        if not passed:
            return None

        # 六维评分
        attack, atk_d = self.score_attack(ts_code, row, daily_df)
        structure, str_d = self.score_structure(ts_code, row, daily_df)
        position, pos_d = self.score_position(ts_code, row, daily_df)
        technical, tech_d = self.score_technical(ts_code, factor_row)
        theme, theme_d = self.score_theme(
            ts_code, row,
            theme_stocks or {}, stock_themes or {},
            all_quotes
        )
        capital, cap_d = self.score_capital(ts_code, row, factor_row)

        # 诱多扣分
        trap, trap_d = self.score_trap_penalty(ts_code, row, daily_df)

        total = attack + structure + position + technical + theme + capital - trap

        # 信号分级
        if total >= 80:
            signal = '强买入'
        elif total >= 65:
            signal = '买入'
        elif total >= 50:
            signal = '关注'
        else:
            signal = ''

        if not signal:
            return None

        best_theme = theme_d.get('theme', '')

        return TailSignal(
            ts_code=ts_code,
            name=name,
            theme=best_theme,
            trade_date=trade_date,
            total_score=round(total, 1),
            attack_score=round(attack, 1),
            structure_score=round(structure, 1),
            position_score=round(position, 1),
            technical_score=round(technical, 1),
            theme_score=round(theme, 1),
            capital_score=round(capital, 1),
            trap_penalty=round(trap, 1),
            signal=signal,
            pct_chg=float(row.get('pct_chg', 0)),
            price=float(row.get('close', 0)),
            detail={**atk_d, **str_d, **pos_d, **tech_d, **theme_d, **cap_d, **trap_d},
        )
