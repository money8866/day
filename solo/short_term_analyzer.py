#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
短线胜率评分独立分析程序
支持以个股代码为参数，分析近20天每天的分数
"""

import os
import sys
import json
import datetime
from datetime import timedelta
import pandas as pd

# 添加主程序目录到路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 复用 tushare_quant.py 的配置和工具
from tushare_quant import (
    pro, TRADE_DATE, TUSHARE_API_CACHE_DIR, 
    CACHE_DIR, BASE_DIR, validate_trade_date
)


def calculate_short_term_win_score(ts_code, pro, trade_date=None, above_chips_pct=-1):
    """
    短线胜率评分模型（3-10交易日）
    使用 Tushare stk_factor_pro 技术因子 + 筹码分布（可选）构建

    参数:
        ts_code: 股票代码
        pro: Tushare pro 实例
        trade_date: 指定日期（None表示最新）
        above_chips_pct: 上方套牢盘比例(%)，-1表示暂无数据
    返回:
        {...}
        signal: '可参与趋势单' | '分歧博弈' | '持有/分批止盈' | '等待方向确认' | '规避'
    """
    result = {
        "ts_code": ts_code,
        "trade_date": trade_date or TRADE_DATE,
        "win_score": 0,
        "breakdown": {
            "trend_score": 0,
            "momentum_score": 0,
            "position_score": 0,
            "volatility_score": 0
        },
        "stage": "",
        "signal": "",
        "key_risk": "",
        "pattern_type": "",
        "ma_structure": "",
        "rsi_signal": "",
        "macd_signal": "",
        "kdj_signal": "",
        "volume_signal": "",
        "signal_resonance": ""
    }

    try:
        # 当日缓存路径（复用当天数据，避免重复API调用）
        _cache_file = os.path.join(CACHE_DIR, f"stk_pro_{ts_code}_{TRADE_DATE}.csv")

        # 1. 优先读缓存
        df = None
        if os.path.exists(_cache_file):
            try:
                df = pd.read_csv(_cache_file)
                df['trade_date'] = df['trade_date'].astype(str)
            except Exception:
                df = None

        # 2. 缓存缺失则调用 stk_factor_pro 专业版接口
        if df is None or df.empty:
            df = pro.stk_factor_pro(ts_code=ts_code, start_date='20250101')
            if df is not None and not df.empty:
                try:
                    df.to_csv(_cache_file, index=False)
                except Exception:
                    pass

        if df is None or df.empty:
            result["stage"] = "数据不足"
            result["signal"] = "规避"
            result["key_risk"] = "技术因子数据获取失败"
            return result

        # 按日期升序排列（便于取前N日）
        df = df.sort_values('trade_date').reset_index(drop=True)
        
        # 如果指定了日期，找到对应日期的数据
        if trade_date:
            mask = df['trade_date'] == trade_date
            if mask.any():
                date_idx = df[mask].index[0]
                latest = df.iloc[date_idx]
                prev1 = df.iloc[date_idx-1] if date_idx >= 1 else None
                prev5 = df.iloc[date_idx-5] if date_idx >= 5 else (df.iloc[0] if len(df) >= 1 else None)
            else:
                result["stage"] = "数据不足"
                result["signal"] = "规避"
                result["key_risk"] = f"指定日期 {trade_date} 无数据"
                return result
        else:
            latest = df.iloc[-1]
            prev1 = df.iloc[-2] if len(df) >= 2 else None
            prev5 = df.iloc[-6] if len(df) >= 6 else (df.iloc[0] if len(df) >= 1 else None)

        # 提取因子（stk_factor_pro 专业版字段命名为 *_bfq，不复权）
        ma5 = float(latest.get('ma_bfq_5', 0) or 0)
        ma10 = float(latest.get('ma_bfq_10', 0) or 0)
        ma20 = float(latest.get('ma_bfq_20', 0) or 0)
        ma60 = float(latest.get('ma_bfq_60', 0) or 0)
        close = float(latest.get('close', 0) or 0)
        high = float(latest.get('high', 0) or 0)
        low = float(latest.get('low', 0) or 0)
        macd = float(latest.get('macd_bfq', 0) or 0)
        dif = float(latest.get('macd_dif_bfq', 0) or 0)
        dea = float(latest.get('macd_dea_bfq', 0) or 0)
        rsi_6 = float(latest.get('rsi_bfq_6', 50) or 50)
        rsi_12 = float(latest.get('rsi_bfq_12', 50) or 50)
        rsi_24 = float(latest.get('rsi_bfq_24', 50) or 50)
        kdj_k = float(latest.get('kdj_k_bfq', 50) or 50)
        kdj_d = float(latest.get('kdj_d_bfq', 50) or 50)
        kdj_j = float(latest.get('kdj_j_bfq', 50) or 50)
        boll_upper = float(latest.get('boll_upper_bfq', 0) or 0)
        boll_mid = float(latest.get('boll_mid_bfq', 0) or 0)
        boll_lower = float(latest.get('boll_lower_bfq', 0) or 0)
        atr = float(latest.get('atr_bfq', 0) or 0)
        volume = float(latest.get('vol', 0) or 0)
        open_price = float(latest.get('open', 0) or 0)

        # 前1日 / 前5日指标（用于信号比较）
        prev_dif = float(prev1.get('macd_dif_bfq', 0) or 0) if prev1 is not None else dif
        prev_rsi6 = float(prev1.get('rsi_bfq_6', 50) or 50) if prev1 is not None else rsi_6
        prev_rsi12 = float(prev1.get('rsi_bfq_12', 50) or 50) if prev1 is not None else rsi_12
        prev_kdj_k = float(prev1.get('kdj_k_bfq', 50) or 50) if prev1 is not None else kdj_k
        prev_kdj_d = float(prev1.get('kdj_d_bfq', 50) or 50) if prev1 is not None else kdj_d
        prev5_vol = float(prev5.get('vol', 0) or 0) if prev5 is not None else volume
        prev5_close = float(prev5.get('close', 0) or 0) if prev5 is not None else close
        prev_vol = float(prev1.get('vol', 0) or 0) if prev1 is not None else volume
        prev_close = float(prev1.get('close', 0) or 0) if prev1 is not None else close

        # =========================
        # ① 趋势结构评分（40分）
        # =========================
        trend_score = 0

        # 判断均线排列结构
        ma_structure_ok = False
        if ma5 > 0 and ma10 > 0 and ma20 > 0 and ma60 > 0:
            if ma5 > ma10 and ma10 > ma20 and ma20 > ma60:
                trend_score = 40
                ma_structure_ok = True
            elif ma5 > ma10 and ma10 > ma20:
                trend_score = 30
                ma_structure_ok = True
            elif ma5 > ma10:
                trend_score = 20
            else:
                trend_score = 5
        else:
            trend_score = 5

        # 【关键修正：股价位置惩罚】
        # 当股价跌破短期均线时，即使均线排列良好也应扣分
        if ma_structure_ok and ma5 > 0:
            # 跌破5日线，严重扣分（滞后性修复）
            if close < ma5:
                trend_score = max(15, trend_score - 15)
            # 贴近但未跌破5日线，轻微扣分
            elif close < ma5 * 1.01:
                trend_score = max(25, trend_score - 5)

        if close > ma20 and ma20 > 0:
            trend_score += 5
        if macd > 0:
            trend_score += 5

        trend_score = min(40, max(0, trend_score))

        # =========================
        # ② 动量健康度（30分）
        # =========================
        momentum_score = 15

        avg_rsi = (rsi_6 + rsi_12 + rsi_24) / 3

        if 50 <= avg_rsi <= 70:
            momentum_score = 30
        elif 40 <= avg_rsi < 50:
            momentum_score = 20
        elif 70 < avg_rsi <= 75:
            momentum_score = 25
        elif 75 < avg_rsi <= 85:
            momentum_score = 15
        elif avg_rsi > 85:
            momentum_score = 5
        elif avg_rsi < 40:
            momentum_score = 10

        if kdj_j > 90 or kdj_j < 20:
            momentum_score = max(5, momentum_score - 5)

        if dif > dea:
            momentum_score = min(30, momentum_score + 5)

        momentum_score = min(30, max(0, momentum_score))

        # =========================
        # ③ 位置合理性（20分）
        # =========================
        position_score = 10

        if ma20 > 0:
            bias_ma20 = abs(close - ma20) / ma20 * 100

            if bias_ma20 <= 5:
                position_score = 20
            elif bias_ma20 <= 10:
                position_score = 18
            elif bias_ma20 <= 15:
                position_score = 12
            elif bias_ma20 <= 20:
                position_score = 8
            elif bias_ma20 <= 30:
                position_score = 3
            else:
                # 远离均线超过30%，严重扣分
                position_score = 0

        if boll_mid > 0:
            bias_boll = abs(close - boll_mid) / boll_mid * 100
            if bias_boll <= 5:
                position_score = min(20, position_score + 3)
            elif bias_boll > 20:
                # 突破布林带上轨过多，额外扣分
                position_score = max(0, position_score - 5)

        position_score = min(20, max(0, position_score))

        # =========================
        # ④ 波动匹配度（10分）
        # =========================
        volatility_score = 5

        if atr > 0 and close > 0:
            atr_ratio = atr / close * 100

            if 1 <= atr_ratio <= 5:
                volatility_score = 10
            elif atr_ratio < 1:
                volatility_score = 3
            elif atr_ratio > 8:
                volatility_score = 2

        if boll_upper > 0 and boll_lower > 0:
            boll_width = (boll_upper - boll_lower) / boll_mid * 100 if boll_mid > 0 else 0

            if 10 <= boll_width <= 25:
                volatility_score = min(10, volatility_score + 2)
            elif boll_width > 40:
                volatility_score = max(2, volatility_score - 2)

        volatility_score = min(10, max(0, volatility_score))

        total_score = int(trend_score + momentum_score + position_score + volatility_score)

        # =========================
        # 【关键修正1】RSI超买 + 套牢盘 联合惩罚
        #   RSI>75 且 上方套牢盘>15% → 总分 -15（如康强电子 68→53）
        #   上方套牢盘>40% → 强制归为"规避"（后面信号映射用）
        # =========================
        penalty_note = ""
        
        # 【新增】冲高回落形态识别
        is_high_wick = False
        if high > 0 and close > 0:
            wick_ratio = (high - close) / (high - low) if (high - low) > 0 else 0
            if wick_ratio > 0.6 and close < open_price:  # 长上影线且收阴
                is_high_wick = True
                total_score = max(0, total_score - 15)
                penalty_note = f"冲高回落形态(上影线比例{wick_ratio:.1f})惩罚-15分"
        
        # 【新增】连续下跌天数惩罚（独立生效）
        down_days = int(latest.get('downdays', 0) or 0)
        if down_days >= 2:
            deduct = min(down_days * 5, 20)
            total_score = max(0, total_score - deduct)
            if penalty_note:
                penalty_note += f"；连续下跌{down_days}天惩罚-{deduct}分"
            else:
                penalty_note = f"连续下跌{down_days}天惩罚-{deduct}分"
        
        # 【新增】MACD顶背离识别（独立生效）
        if dif < prev_dif and close > prev_close:
            total_score = max(0, total_score - 15)
            if penalty_note:
                penalty_note += "；MACD顶背离惩罚-15分"
            else:
                penalty_note = "MACD顶背离惩罚-15分"
        
        if not penalty_note:
            if avg_rsi > 85:
                # RSI极度疯狂，严厉扣分
                deduct = 20
                total_score = max(0, total_score - deduct)
                penalty_note = f"RSI极度疯狂({avg_rsi:.0f})惩罚-{deduct}分"
            elif avg_rsi > 80:
                # RSI严重超买
                deduct = 10
                total_score = max(0, total_score - deduct)
                penalty_note = f"RSI严重超买({avg_rsi:.0f})惩罚-{deduct}分"
            elif avg_rsi > 75 and above_chips_pct >= 0 and above_chips_pct > 15:
                deduct = 15
                total_score = max(0, total_score - deduct)
                penalty_note = f"RSI超买({avg_rsi:.0f})+套牢盘({above_chips_pct:.0f}%)联合惩罚-{deduct}分"

        # =========================
        # 【信号分析】基于前后日对比
        # =========================

        # --- MA结构 ---
        if ma5 > 0 and ma10 > 0 and ma20 > 0 and ma60 > 0:
            if ma5 > ma10 and ma10 > ma20 and ma20 > ma60:
                ma_structure = "完整多头排列"
            elif ma5 > ma10 and ma10 > ma20:
                ma_structure = "短期多头排列"
            elif ma5 > ma10:
                ma_structure = "初步多头"
            else:
                ma_structure = "空头排列"
        else:
            ma_structure = "均线数据不足"

        # --- RSI信号 ---
        rsi_chg = rsi_6 - prev_rsi6
        if avg_rsi < 40:
            rsi_signal = f"RSI弱势区(均值{avg_rsi:.0f})"
        elif avg_rsi < 50:
            if rsi_chg > 3:
                rsi_signal = f"RSI低位金叉向上({prev_rsi6:.0f}→{rsi_6:.0f})，动量转强"
            else:
                rsi_signal = f"RSI偏弱区(均值{avg_rsi:.0f})"
        elif 50 <= avg_rsi < 65:
            if rsi_chg > 3:
                rsi_signal = f"RSI强势区金叉({prev_rsi6:.0f}→{rsi_6:.0f})，上升动量强化"
            else:
                rsi_signal = f"RSI偏强区(均值{avg_rsi:.0f})"
        elif 65 <= avg_rsi < 75:
            rsi_signal = f"RSI强势偏热(均值{avg_rsi:.0f})"
        elif 75 <= avg_rsi < 85:
            rsi_signal = f"RSI高位过热(均值{avg_rsi:.0f})，注意回调"
        else:
            rsi_signal = f"RSI严重超买({avg_rsi:.0f})，风险极高"

        # --- MACD信号 ---
        dif_chg = dif - prev_dif
        if dif > dea and prev_dif <= prev1.get('macd_dea_bfq', dea):
            macd_signal = f"MACD金叉(零轴上方)，DIF拐头向上+{dif_chg:.3f}"
        elif dif > dea:
            macd_signal = f"MACD多头(DIF>DEA)，DIF向上+{dif_chg:.3f}"
        elif dif < dea and dif > 0 and prev_dif <= 0:
            macd_signal = f"MACD零轴附近死叉，注意方向选择"
        elif dif < dea and dif < 0:
            macd_signal = f"MACD空头(DIF<DEA)，DIF下行{abs(dif_chg):.3f}"
        elif dif > 0 and dif_chg > 0.05:
            macd_signal = f"DIF向上拐头({prev_dif:.3f}→{dif:.3f})，底部动量修复"
        elif dif > 0 and dif_chg < -0.05:
            macd_signal = f"DIF向下拐头({prev_dif:.3f}→{dif:.3f})，动量衰减"
        else:
            macd_signal = f"DIF={dif:.3f}，方向中性"

        # --- KDJ信号 ---
        if prev_kdj_k < prev_kdj_d and kdj_k > kdj_d:
            kdj_signal = f"KDJ日线金叉({prev_kdj_k:.1f}→{kdj_k:.1f})"
        elif prev_kdj_k > prev_kdj_d and kdj_k < kdj_d:
            kdj_signal = f"KDJ日线死叉({prev_kdj_k:.1f}→{kdj_k:.1f})"
        elif kdj_j > 90:
            kdj_signal = f"KDJ高位钝化(J={kdj_j:.0f})，警惕回调"
        elif kdj_j < 20:
            kdj_signal = f"KDJ低位钝化(J={kdj_j:.0f})，超卖反弹概率"
        elif kdj_k > kdj_d:
            kdj_signal = f"KDJ多头排列(J={kdj_j:.0f})"
        else:
            kdj_signal = f"KDJ空头排列(J={kdj_j:.0f})"

        # --- 成交量信号 ---
        vol_ratio = volume / prev5_vol if prev5_vol > 0 else 1
        prev_vol_ratio = prev_vol / prev5_vol if prev5_vol > 0 else 1
        close_chg = (close - prev5_close) / prev5_close * 100 if prev5_close > 0 else 0

        if vol_ratio >= 2.0 and close_chg > 3:
            volume_signal = f"放量阳线确认(量比{vol_ratio:.1f}倍，涨幅{close_chg:.1f}%)"
        elif vol_ratio >= 1.5 and close_chg > 1:
            volume_signal = f"温和放量上涨(量比{vol_ratio:.1f}倍)"
        elif vol_ratio >= 1.5 and close_chg < -1:
            volume_signal = f"放量下跌(量比{vol_ratio:.1f}倍)，抛压重"
        elif vol_ratio < 0.6:
            volume_signal = f"缩量整理(量比{vol_ratio:.1f}倍)，观望情绪浓"
        else:
            volume_signal = f"量能平稳(量比{vol_ratio:.1f}倍)"

        # --- 形态类型（二波反转 / 趋势突破 / 回调低吸 / 震荡整理）---
        is_2nd_wave = (rsi_6 > prev_rsi6 and prev_rsi6 < 50 and rsi_6 > 50 and
                       kdj_k > prev_kdj_k and dif > prev_dif and close > ma5)
        is_breakthrough = (ma5 > ma10 and ma10 > ma20 and close > ma5 and
                           rsi_6 > 60 and vol_ratio >= 1.3)
        # 【修正】回调低吸条件：跌破5日线但在20日线之上，RSI处于低位
        # 放宽条件：不要求KDJ必须转强，因为在最低点KDJ可能仍在下降
        is_pullback_buy = (ma5 > ma10 and ma10 > ma20 and  # 均线多头排列
                           close < ma5 and close >= ma20 and  # 跌破5日线但在20日线之上
                           35 <= avg_rsi < 55)  # RSI低位即可

        if is_2nd_wave:
            pattern_type = "二波反转（RSI金叉+动量修复）"
        elif is_breakthrough:
            pattern_type = "趋势突破（均线多头+放量确认）"
        elif is_pullback_buy:
            pattern_type = "回调低吸（回踩均线蓄力）"
        elif trend_score >= 30 and momentum_score >= 25:
            pattern_type = "强势延续（趋势+动量共振）"
        else:
            pattern_type = "震荡整理（信号不共振）"

        # =========================
        # 【关键修正2】重写交易阶段分类（以最终评分为主）
        #   核心原则:
        #     - 加速期(≥80分): 趋势+动量共振强势，应持有/分批止盈
        #     - 弱转强初期(60-80分): 指标共振偏多，可参与趋势单
        #     - 分歧期(45-60分): 多空力量平衡，等待方向确认
        #     - 回调低吸(45-60分): 股价回踩，可低吸
        #     - 退潮期(<45分): 趋势结构弱势，规避
        #   趋势+动量双强是基础前提，否则不应强行归为"可参与"阶段
        # =========================
        trend_baseline_ok = (trend_score >= 30 and momentum_score >= 20)
        
        # 【新增】优先判断回调低吸阶段
        if is_pullback_buy:
            stage = "回调低吸"
        elif trend_baseline_ok:
            if total_score >= 80:
                stage = "加速期"
            elif total_score >= 60:
                stage = "弱转强初期"
            elif total_score >= 45:
                stage = "分歧期"
            else:
                stage = "启动期"
        elif total_score >= 45:
            # 趋势偏弱但分数尚可 → 仍算分歧（多空拉锯）
            stage = "分歧期"
        else:
            stage = "退潮期"

        # =========================
        # 【关键修正3】重写交易信号（胜率-信号映射）
        #   用户规则1: 胜率>=80 and 加速期 → 持有/分批止盈
        #   用户规则1: 胜率>=80 and 分歧期 → 等待方向确认，评分-20
        #   用户规则2: 规避只在 总分<50 / RSI>85 / 套牢盘>40% 时出现
        # =========================
        signal = ""
        key_risk = ""
        key_risk_parts = []

        # 分歧期自动降权逻辑（高胜率+分歧期：说明指标共振偏多，但结构仍不稳）
        # 注: 降权仅用于信号展示，不反向修改total_score避免影响阶段判定
        display_score = total_score
        stage_note = ""
        if stage == "分歧期" and total_score >= 80:
            display_score = max(0, total_score - 20)
            stage_note = f"分歧期高胜率自动降权20分({total_score}→{display_score})"
        elif stage == "分歧期" and total_score >= 60:
            # 用户规则3: 分歧期合理范围45-60，超60应判定为弱转强初期
            # 这里做二次修正：若分数>60却仍被划分为分歧期，说明趋势基线不足
            # 保持分歧期判定，但加注说明趋势不足
            stage_note = f"趋势基线不足(trend={trend_score},mom={momentum_score})，虽分数{total_score}仍判定为分歧期"

        forced_avoid = False
        if total_score < 50:
            forced_avoid = True
            key_risk_parts.append(f"胜率过低({total_score}分)")
        if avg_rsi > 85:
            forced_avoid = True
            key_risk_parts.append(f"RSI极度疯狂({avg_rsi:.0f})")
        if above_chips_pct >= 0 and above_chips_pct > 40:
            forced_avoid = True
            key_risk_parts.append(f"上方套牢盘过重({above_chips_pct:.0f}%)")

        if forced_avoid:
            signal = "规避"
            if key_risk_parts:
                key_risk = "；".join(key_risk_parts) + "，观望"
            else:
                key_risk = "趋势结构弱势，观望"
        elif stage == "加速期":
            signal = "持有/分批止盈"
            key_risk = f"胜率{total_score}分处于加速尾声，注意获利盘抛压"
        elif stage == "回调低吸":
            signal = "可参与低吸"
            key_risk = f"股价回踩20日线支撑，RSI({avg_rsi:.0f})处于低位，适合分批低吸"
        elif stage == "分歧期":
            signal = "等待方向确认"
            if avg_rsi >= 70:
                key_risk = f"RSI偏高({avg_rsi:.0f})，等待回踩或方向选择"
            else:
                key_risk = f"多空力量平衡，需等待趋势确认"
        elif stage == "弱转强初期":
            signal = "可参与趋势单"
            key_risk = ""
        elif stage == "初升期":
            signal = "可参与趋势单"
            key_risk = ""
        elif stage == "启动期":
            signal = "等待转强"
            key_risk = "信号刚转多，等待均线确认"
        else:
            signal = "规避"
            key_risk = "观望"

        # 合并各类备注
        extra_notes = []
        if penalty_note:
            extra_notes.append(penalty_note)
        if stage_note:
            extra_notes.append(stage_note)
        if extra_notes:
            if key_risk:
                key_risk = key_risk + " | " + " | ".join(extra_notes)
            else:
                key_risk = " | ".join(extra_notes)

        # =========================
        # 信号共振判断（基于二波反转+趋势突破）
        # =========================
        buy_signals = sum([
            rsi_chg > 3,
            dif_chg > 0.05,
            kdj_k > prev_kdj_k,
            vol_ratio >= 1.3,
            close > ma5,
            dif > dea,
            position_score >= 15,
        ])
        buy共振 = buy_signals >= 5 and avg_rsi < 80 and trend_score >= 20
        sell共振 = (kdj_k < prev_kdj_k and rsi_chg < -3 and vol_ratio < 0.7) or avg_rsi > 88

        if buy共振:
            signal_resonance = "短线信号共振买入（量价、RSI、MACD、KDJ多维共振）"
        elif sell共振:
            signal_resonance = "短线信号共振卖出（多指标走弱，建议回避）"
        else:
            signal_resonance = "信号混乱，短线方向不明，观望"

        # 填充结果
        result["win_score"] = total_score
        result["breakdown"]["trend_score"] = int(trend_score)
        result["breakdown"]["momentum_score"] = int(momentum_score)
        result["breakdown"]["position_score"] = int(position_score)
        result["breakdown"]["volatility_score"] = int(volatility_score)
        result["stage"] = stage
        result["signal"] = signal
        result["key_risk"] = key_risk
        result["pattern_type"] = pattern_type
        result["ma_structure"] = ma_structure
        result["rsi_signal"] = rsi_signal
        result["macd_signal"] = macd_signal
        result["kdj_signal"] = kdj_signal
        result["volume_signal"] = volume_signal
        result["signal_resonance"] = signal_resonance

    except Exception as e:
        result["stage"] = "计算异常"
        result["signal"] = "规避"
        result["key_risk"] = f"计算错误: {str(e)}"

    return result


def detect_breakout(ts_code, pro, trade_date=None):
    """
    突破型策略检测函数
    总分100分，75分以上视为有效突破，可列入观察/买入名单
    
    评分标准：
    1. 价格突破 (30分): close > boll_upper
    2. 趋势均线 (25分): ma5 > ma10 且 ma10 > ma20 且 close > ma5
    3. 动能共振 (20分): macd > 0 且 dif > dea 且 kdj_j > 80
    4. 空间与安全 (15分): rsi_6 > 65 且 rsi_6 < 85
    5. 波动率辅助 (10分): atr > 0 且 close > ma60
    
    参数:
        ts_code: 股票代码
        pro: Tushare pro 实例
        trade_date: 指定日期（None表示最新）
    返回:
        {
            "breakout_score": int,      # 突破评分
            "is_valid_breakout": bool,  # 是否有效突破(>=75分)
            "breakdown": {...},         # 各维度得分
            "signal": str               # 信号建议
        }
    """
    result = {
        "ts_code": ts_code,
        "trade_date": trade_date or TRADE_DATE,
        "breakout_score": 0,
        "is_valid_breakout": False,
        "breakdown": {
            "price_breakout": 0,
            "trend_ma": 0,
            "momentum_resonance": 0,
            "safety_zone": 0,
            "volatility": 0
        },
        "signal": "非突破形态"
    }
    
    try:
        # 获取技术因子数据
        _cache_file = os.path.join(CACHE_DIR, f"stk_pro_{ts_code}_{TRADE_DATE}.csv")
        
        if os.path.exists(_cache_file):
            df = pd.read_csv(_cache_file)
        else:
            df = pro.stk_factor_pro(ts_code=ts_code, start_date=trade_date, end_date=TRADE_DATE)
            df.to_csv(_cache_file, index=False)
        
        df['trade_date'] = df['trade_date'].astype(str)
        # 按日期升序排列（确保df.iloc[-1]是最新数据）
        df = df.sort_values('trade_date').reset_index(drop=True)
        
        if trade_date:
            mask = df['trade_date'] == trade_date
            if mask.any():
                latest = df[mask].iloc[0]
            else:
                result["signal"] = "指定日期无数据"
                return result
        else:
            latest = df.iloc[-1]
        
        # 提取因子
        close = float(latest.get('close', 0) or 0)
        boll_upper = float(latest.get('boll_upper_bfq', 0) or 0)
        ma5 = float(latest.get('ma_bfq_5', 0) or 0)
        ma10 = float(latest.get('ma_bfq_10', 0) or 0)
        ma20 = float(latest.get('ma_bfq_20', 0) or 0)
        ma60 = float(latest.get('ma_bfq_60', 0) or 0)
        macd = float(latest.get('macd_bfq', 0) or 0)
        dif = float(latest.get('macd_dif_bfq', 0) or 0)
        dea = float(latest.get('macd_dea_bfq', 0) or 0)
        kdj_j = float(latest.get('kdj_bfq', 50) or 50)
        rsi_6 = float(latest.get('rsi_bfq_6', 50) or 50)
        atr = float(latest.get('atr_bfq', 0) or 0)
        
        total_score = 0
        
        # 1. 价格突破 (30分): close > boll_upper
        if close > boll_upper and boll_upper > 0:
            result["breakdown"]["price_breakout"] = 30
            total_score += 30
        
        # 2. 趋势均线 (25分): ma5 > ma10 且 ma10 > ma20 且 close > ma5
        if ma5 > ma10 and ma10 > ma20 and close > ma5 and ma5 > 0:
            result["breakdown"]["trend_ma"] = 25
            total_score += 25
        
        # 3. 动能共振 (20分): macd > 0 且 dif > dea 且 kdj_j > 80
        if macd > 0 and dif > dea and kdj_j > 80:
            result["breakdown"]["momentum_resonance"] = 20
            total_score += 20
        
        # 4. 空间与安全 (15分): rsi_6 > 65 且 rsi_6 < 85
        if 65 < rsi_6 < 85:
            result["breakdown"]["safety_zone"] = 15
            total_score += 15
        
        # 5. 波动率辅助 (10分): atr > 0 且 close > ma60
        if atr > 0 and close > ma60 and ma60 > 0:
            result["breakdown"]["volatility"] = 10
            total_score += 10
        
        result["breakout_score"] = total_score
        result["is_valid_breakout"] = total_score >= 75
        
        if result["is_valid_breakout"]:
            result["signal"] = "有效突破！列入观察/买入名单"
        elif total_score >= 60:
            result["signal"] = "突破待确认，建议观察"
        elif total_score >= 40:
            result["signal"] = "突破迹象初现，继续跟踪"
        else:
            result["signal"] = "非突破形态"
        
    except Exception as e:
        result["signal"] = f"计算错误: {str(e)}"
    
    return result


def detect_wave2_reversal(ts_code, pro, trade_date=None, lookback_days=20):
    """
    二波反转策略检测函数
    总分100分，80分以上视为完美的二波潜伏信号
    
    评分标准：
    1. 强股基因 (30分): ma20 > ma60 且 dif > 0，前期有过 close > boll_upper
    2. 调整健康 (30分): boll_mid <= close <= ma10，rsi_6 降至 45-60 之间
    3. 反转信号 (20分): close > open（阳线），kdj_j 触底勾头向上
    4. 量价配合 (20分): low 探底接近 ma20 后收回，volume 相比前期放量时显著萎缩
    
    信号延续：如果前1-2天出现完美二波信号，且股价未大幅拉升(涨幅<10%)，允许突破ma10/ma5仍视为有效
    
    参数:
        ts_code: 股票代码
        pro: Tushare pro 实例
        trade_date: 指定日期（None表示最新）
        lookback_days: 回溯天数，用于判断前期是否为强股
    返回:
        {
            "wave2_score": int,          # 二波反转评分
            "is_perfect_signal": bool,   # 是否完美信号(>=80分)
            "breakdown": {...},          # 各维度得分
            "signal": str                # 信号建议
        }
    """
    result = {
        "ts_code": ts_code,
        "trade_date": trade_date or TRADE_DATE,
        "wave2_score": 0,
        "is_perfect_signal": False,
        "breakdown": {
            "strong_stock": 0,
            "healthy_adjust": 0,
            "reversal_signal": 0,
            "volume_price": 0
        },
        "signal": "非二波形态"
    }
    
    try:
        # 获取技术因子数据
        _cache_file = os.path.join(CACHE_DIR, f"stk_pro_{ts_code}_{TRADE_DATE}.csv")
        
        if os.path.exists(_cache_file):
            df = pd.read_csv(_cache_file)
        else:
            df = pro.stk_factor_pro(ts_code=ts_code, start_date=trade_date, end_date=TRADE_DATE)
            df.to_csv(_cache_file, index=False)
        
        df['trade_date'] = df['trade_date'].astype(str)
        # 按日期升序排列（确保df.iloc[-1]是最新数据）
        df = df.sort_values('trade_date').reset_index(drop=True)
        
        if trade_date:
            mask = df['trade_date'] == trade_date
            if mask.any():
                latest = df[mask].iloc[0]
            else:
                result["signal"] = "指定日期无数据"
                return result
        else:
            latest = df.iloc[-1]
        
        # 获取前N天数据用于判断
        df_sorted = df.sort_values('trade_date')
        mask_lookback = df_sorted['trade_date'] <= (trade_date or TRADE_DATE)
        df_lookback = df_sorted[mask_lookback].tail(lookback_days)
        
        # 提取最新因子
        close = float(latest.get('close', 0) or 0)
        open_price = float(latest.get('open', 0) or 0)
        low = float(latest.get('low', 0) or 0)
        volume = float(latest.get('vol', 0) or 0)
        ma5 = float(latest.get('ma_bfq_5', 0) or 0)
        ma10 = float(latest.get('ma_bfq_10', 0) or 0)
        ma20 = float(latest.get('ma_bfq_20', 0) or 0)
        ma60 = float(latest.get('ma_bfq_60', 0) or 0)
        boll_mid = float(latest.get('boll_mid_bfq', 0) or 0)
        dif = float(latest.get('macd_dif_bfq', 0) or 0)
        rsi_6 = float(latest.get('rsi_bfq_6', 50) or 50)
        kdj_j = float(latest.get('kdj_bfq', 50) or 50)
        
        # 获取前一天的kdj_j用于判断勾头
        prev_kdj_j = kdj_j
        prev_close = close
        if len(df_lookback) >= 2:
            prev_day = df_lookback.iloc[-2]
            prev_kdj_j = float(prev_day.get('kdj_bfq', 50) or 50)
            prev_close = float(prev_day.get('close', close) or close)
        
        # 检查前1-2天是否有完美二波信号（信号延续）
        has_recent_perfect_signal = False
        days_since_perfect = 0
        max_gain_since_perfect = 0
        if len(df_lookback) >= 3:
            # 检查前1天和前2天
            for i in range(1, 3):
                if len(df_lookback) > i:
                    prev_day = df_lookback.iloc[-1 - i]
                    # 模拟计算前一天的二波分数
                    prev_close_val = float(prev_day.get('close', 0) or 0)
                    prev_ma10_val = float(prev_day.get('ma_bfq_10', 0) or 0)
                    prev_ma20_val = float(prev_day.get('ma_bfq_20', 0) or 0)
                    prev_ma60_val = float(prev_day.get('ma_bfq_60', 0) or 0)
                    prev_dif_val = float(prev_day.get('macd_dif_bfq', 0) or 0)
                    prev_rsi_val = float(prev_day.get('rsi_bfq_6', 50) or 50)
                    
                    # 判断是否是完美二波信号
                    if prev_ma20_val > prev_ma60_val and prev_dif_val > 0:
                        prev_boll_mid = float(prev_day.get('boll_mid_bfq', 0) or 0)
                        if prev_boll_mid > 0 and prev_ma10_val > 0:
                            if prev_boll_mid <= prev_close_val <= prev_ma10_val and 45 <= prev_rsi_val <= 60:
                                has_recent_perfect_signal = True
                                days_since_perfect = i
                                # 计算从完美信号以来的涨幅
                                if prev_close_val > 0:
                                    max_gain_since_perfect = (close - prev_close_val) / prev_close_val * 100
                                break
        
        total_score = 0
        
        # 1. 强股基因 (30分): ma20 > ma60 且 dif > 0，前期有过 close > boll_upper
        has_strong_gene = False
        if ma20 > ma60 and dif > 0 and ma20 > 0:
            # 检查前期是否有突破布林带上轨
            for _, row in df_lookback.iterrows():
                row_close = float(row.get('close', 0) or 0)
                row_boll_upper = float(row.get('boll_upper_bfq', 0) or 0)
                if row_close > row_boll_upper and row_boll_upper > 0:
                    has_strong_gene = True
                    break
        
        if has_strong_gene:
            result["breakdown"]["strong_stock"] = 30
            total_score += 30
        
        # 2. 调整健康 (30分): boll_mid <= close <= ma10，rsi_6 降至 45-60 之间
        # 信号延续时放宽条件：允许突破ma10甚至ma5
        adjust_score = 0
        if boll_mid > 0 and ma10 > 0:
            # 标准条件
            if boll_mid <= close <= ma10 and 45 <= rsi_6 <= 60:
                adjust_score = 30
            # 信号延续且未大幅拉升：允许突破ma10但不超过ma5
            elif has_recent_perfect_signal and max_gain_since_perfect < 10:
                ma5_val = ma5 if ma5 > 0 else ma10 * 1.05  # 假设ma5约等于ma10*1.05
                if close <= ma5_val and rsi_6 <= 70:
                    adjust_score = 25  # 放宽条件扣5分
        
        result["breakdown"]["healthy_adjust"] = adjust_score
        total_score += adjust_score
        
        # 3. 反转信号 (20分): close > open（阳线），kdj_j 触底勾头向上
        reversal_score = 0
        if close > open_price and kdj_j > prev_kdj_j:
            # 额外检查kdj_j是否在低位或刚从低位回升
            if kdj_j < 60:
                reversal_score = 20
        
        result["breakdown"]["reversal_signal"] = reversal_score
        total_score += reversal_score
        
        # 4. 量价配合 (20分): low 探底接近 ma20 后收回，volume 相比前期放量时显著萎缩
        volume_score = 0
        if ma20 > 0 and low > 0:
            # 判断下影线是否试探ma20
            low_to_ma20 = abs(low - ma20) / ma20 * 100
            
            # 判断成交量是否萎缩（相比前期最大成交量）
            max_vol = df_lookback['vol'].max() if not df_lookback.empty else volume
            vol_ratio = volume / max_vol if max_vol > 0 else 1
            
            # 标准条件
            if low_to_ma20 <= 5 and vol_ratio <= 0.5:
                volume_score = 20
            # 信号延续时放宽量价条件
            elif has_recent_perfect_signal and max_gain_since_perfect < 10:
                if low_to_ma20 <= 8:
                    volume_score = 15  # 信号延续时量价配合分数
        
        result["breakdown"]["volume_price"] = volume_score
        total_score += volume_score
        
        # 信号延续加分：前1-2天有完美二波信号，且涨幅不大，额外加分
        if has_recent_perfect_signal and max_gain_since_perfect < 10:
            total_score += int(min(10, (10 - max_gain_since_perfect)))
        
        result["wave2_score"] = int(total_score)
        result["is_perfect_signal"] = total_score >= 80
        
        if result["is_perfect_signal"]:
            result["signal"] = "完美二波反转！可潜伏买入"
        elif total_score >= 60:
            result["signal"] = "二波形态初现，继续跟踪"
        elif total_score >= 40:
            result["signal"] = "疑似二波结构，等待确认"
        else:
            result["signal"] = "非二波形态"
        
    except Exception as e:
        result["signal"] = f"计算错误: {str(e)}"
    
    return result


def get_trade_dates(start_date, end_date):
    """获取指定日期范围内的交易日"""
    try:
        df = pro.trade_cal(start_date=start_date, end_date=end_date)
        df = df[df['is_open'] == 1]
        return sorted(df['cal_date'].tolist())
    except Exception as e:
        print(f"获取交易日历失败: {e}")
        return []


def analyze_stock_short_term(ts_code, days=20):
    """
    分析股票近N天的短线胜率
    """
    print(f"\n{'='*60}")
    print(f"📊 短线胜率分析 - {ts_code}")
    print(f"{'='*60}")
    
    # 获取股票名称
    stock_name = ""
    try:
        df = pro.stock_basic(ts_code=ts_code)
        if not df.empty:
            stock_name = df.iloc[0]['name']
    except:
        pass
    
    if stock_name:
        print(f"股票名称: {stock_name}")
    
    # 获取近N天的交易日
    end_date = TRADE_DATE
    start_date = (datetime.datetime.strptime(end_date, '%Y%m%d') - timedelta(days=days+5)).strftime('%Y%m%d')
    trade_dates = get_trade_dates(start_date, end_date)
    
    if not trade_dates:
        print("❌ 无法获取交易日历")
        return
    
    # 取最近的N个交易日
    trade_dates = trade_dates[-days:]
    
    print(f"分析日期范围: {trade_dates[0]} ~ {trade_dates[-1]}")
    print(f"分析天数: {len(trade_dates)}")
    print("-"*60)
    
    # 分析每天的短线胜率、突破检测和二波反转检测
    results = []
    breakout_results = []
    wave2_results = []
    for date in trade_dates:
        print(f"正在分析 {date}...", end=' ')
        result = calculate_short_term_win_score(ts_code, pro, date)
        breakout = detect_breakout(ts_code, pro, date)
        wave2 = detect_wave2_reversal(ts_code, pro, date)
        results.append(result)
        breakout_results.append(breakout)
        wave2_results.append(wave2)
        print(f"胜率={result['win_score']} | {result['stage']} | 突破={breakout['breakout_score']}分 | 二波={wave2['wave2_score']}分")
    
    # 输出详细表格（包含突破检测和二波反转）
    print("\n" + "="*200)
    print(f"📈 {ts_code} 近{days}天短线胜率、突破检测与二波反转")
    print("="*200)
    print(f"{'日期':<12} {'胜率':<6} {'突破分':<8} {'二波分':<8} {'趋势':<6} {'动量':<6} {'位置':<6} {'波动':<6} {'阶段':<10} {'突破信号':<22} {'二波信号':<20}")
    print("-"*200)
    
    for result, breakout, wave2 in zip(results, breakout_results, wave2_results):
        # 突破信号文字表述
        breakout_score = breakout['breakout_score']
        if breakout_score >= 75:
            breakout_text = "有效突破！列入观察/买入名单"
        elif breakout_score >= 60:
            breakout_text = "突破待确认，建议观察"
        elif breakout_score >= 40:
            breakout_text = "突破迹象初现，继续跟踪"
        else:
            breakout_text = "非突破形态"
        
        # 二波反转信号文字表述
        wave2_score = wave2['wave2_score']
        if wave2_score >= 80:
            wave2_text = "完美二波！可潜伏买入"
        elif wave2_score >= 60:
            wave2_text = "二波形态初现，继续跟踪"
        elif wave2_score >= 40:
            wave2_text = "疑似二波结构，等待确认"
        else:
            wave2_text = "非二波形态"
        
        print(f"{result['trade_date']:<12} "
              f"{result['win_score']:<6} "
              f"{breakout_score:<8} "
              f"{wave2_score:<8} "
              f"{result['breakdown']['trend_score']:<6} "
              f"{result['breakdown']['momentum_score']:<6} "
              f"{result['breakdown']['position_score']:<6} "
              f"{result['breakdown']['volatility_score']:<6} "
              f"{result['stage']:<10} "
              f"{breakout_text:<22} "
              f"{wave2_text:<20}")
    
    print("="*100)
    
    # 统计分析
    scores = [r['win_score'] for r in results]
    avg_score = sum(scores) / len(scores)
    max_score = max(scores)
    min_score = min(scores)
    
    print(f"\n📊 统计分析:")
    print(f"  平均胜率: {avg_score:.1f}")
    print(f"  最高胜率: {max_score}")
    print(f"  最低胜率: {min_score}")
    print(f"  胜率波动: {max_score - min_score}")
    
    # 信号分布
    signal_counts = {}
    for r in results:
        signal = r['signal']
        signal_counts[signal] = signal_counts.get(signal, 0) + 1
    
    print(f"\n📶 信号分布:")
    for signal, count in signal_counts.items():
        percentage = count / len(results) * 100
        print(f"  {signal}: {count}次 ({percentage:.1f}%)")
    
    # 阶段分布
    stage_counts = {}
    for r in results:
        stage = r['stage']
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
    
    print(f"\n🎯 阶段分布:")
    for stage, count in stage_counts.items():
        percentage = count / len(results) * 100
        print(f"  {stage}: {count}次 ({percentage:.1f}%)")
    
    # 保存结果到文件
    output_file = os.path.join(TUSHARE_API_CACHE_DIR, f"short_term_analysis_{ts_code}_{TRADE_DATE}.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ 分析结果已保存到: {output_file}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python short_term_analyzer.py <股票代码> [天数]")
        print("示例: python short_term_analyzer.py 002119.SZ 20")
        sys.exit(1)
    
    ts_code = sys.argv[1]
    
    # 检查代码格式，补全市场后缀
    if '.' not in ts_code:
        if ts_code.startswith('6'):
            ts_code += '.SH'
        else:
            ts_code += '.SZ'
    
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    
    analyze_stock_short_term(ts_code, days)