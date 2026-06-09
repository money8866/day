#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
V9开仓评分算法测试 - 埃斯顿(002747.SZ) 20260605分析
"""
import os
import sys
import pandas as pd
import numpy as np

# 添加路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from tushare_quant import pro, TRADE_DATE, CACHE_DIR, calc_dual_layer_score_v7, calc_hot_money_open_score_v9

def load_stock_data(ts_code, trade_date):
    """加载指定日期的股票数据"""
    # 先从缓存加载
    cache_file = os.path.join(CACHE_DIR, f"{ts_code}.csv")
    if os.path.exists(cache_file):
        df = pd.read_csv(cache_file)
        df['trade_date'] = df['trade_date'].astype(str)
        # 获取指定日期之前的数据
        df = df[df['trade_date'] <= trade_date]
        if not df.empty:
            return df
    
    # 从Tushare获取
    if pro:
        try:
            df = pro.daily(ts_code=ts_code, start_date='20250101', end_date=trade_date)
            if not df.empty:
                df = df.sort_values('trade_date')
                return df
        except Exception as e:
            print(f"从Tushare获取数据失败: {e}")
    
    return None

def test_eston_v9_score():
    """测试埃斯顿V9开仓评分"""
    ts_code = '002747.SZ'
    stock_name = '埃斯顿'
    test_date = '20260605'
    
    print("=" * 80)
    print(f"🔥 V9开仓评分测试 - {stock_name}({ts_code}) {test_date}")
    print("=" * 80)
    
    # 1. 加载数据
    df = load_stock_data(ts_code, test_date)
    if df is None or df.empty:
        print(f"❌ 无法获取{stock_name}的数据")
        return
    
    print(f"\n📊 数据加载完成: {len(df)} 条记录")
    print(f"   日期范围: {df['trade_date'].min()} ~ {df['trade_date'].max()}")
    
    # 确保有足够数据
    if len(df) < 60:
        print(f"❌ 数据不足（需要至少60条）")
        return
    
    # 2. 构造完整的stock_info数据（这是主题纯度计算的关键）
    print("\n📋 构造股票信息...")
    stock_info = {
        'name': '埃斯顿',
        'ts_code': ts_code,
        'industries': ['自动化设备', '机器人', '智能制造'],
        'concepts': ['工业机器人', '机器人概念', '人工智能', '智能制造', '高端装备', '工业4.0'],
        'business_text': '公司是国内领先的工业机器人制造商，专注于工业机器人、智能制造系统、自动化设备的研发、生产和销售。主要产品包括六轴工业机器人、SCARA机器人、Delta机器人等，广泛应用于汽车制造、3C电子、新能源、金属加工等领域。公司拥有自主核心技术，在运动控制、伺服系统、机器视觉等方面具有较强的竞争力。',
        'total_market_cap': 180e8  # 180亿市值（用于资金体量因子）
    }
    print(f"   行业: {stock_info['industries']}")
    print(f"   概念: {stock_info['concepts']}")
    print(f"   市值: {stock_info['total_market_cap'] / 1e8:.0f}亿")
    
    # 3. 计算V7评分
    print("\n🔢 计算V7评分...")
    v7_result = calc_dual_layer_score_v7(df, ts_code=ts_code, stock_info=stock_info, theme='人形机器人')
    print(f"   V7总评分: {v7_result.get('V7总评分', 0):.2f}")
    print(f"   趋势概率: {v7_result.get('趋势概率', 0):.4f}")
    print(f"   失败概率: {v7_result.get('失败概率', 0):.4f}")
    print(f"   突破强度: {v7_result.get('突破强度', 0):.4f}")
    print(f"   资金动量: {v7_result.get('资金动量', 0):.4f}")
    print(f"   趋势稳定: {v7_result.get('趋势稳定', 0):.4f}")
    print(f"   量能爆发: {v7_result.get('量能爆发', 0):.4f}")
    print(f"   压缩度: {v7_result.get('压缩度', 0):.4f}")
    print(f"   趋势强度: {v7_result.get('趋势强度', 0):.4f}")
    print(f"   主题纯度: {v7_result.get('主题纯度', 0):.2f}")
    print(f"   所属主题: {v7_result.get('所属主题', '')}")
    
    # 3. 计算V9开仓评分
    print("\n🔮 计算V9开仓评分...")
    open_score, structure_type, recommendation = calc_hot_money_open_score_v9(v7_result, df, stock_info, '人形机器人')
    
    print(f"\n📈 V9开仓评分结果:")
    print(f"   开仓评分: {open_score:.2f}")
    print(f"   结构类型: {structure_type}")
    print(f"   推荐理由: {recommendation}")
    
    # 4. 详细分解评分
    print("\n🔍 评分详细分解:")
    
    # 主题热度
    theme_rank_score = float(v7_result.get('主题纯度', 50))  # 默认50
    print(f"\n   1. 主题热度: {theme_rank_score:.0f} × 0.25 = {theme_rank_score * 0.25:.1f}")
    
    # 主题纯度
    purity_score = float(v7_result.get('主题纯度', 30))
    print(f"   2. 主题纯度: {purity_score:.0f} × 0.20 = {purity_score * 0.20:.1f}")
    
    # 龙头得分
    money_momentum = float(v7_result.get('资金动量', 0.5))
    trend_stability = float(v7_result.get('趋势稳定', 0.5))
    trend_probability = float(v7_result.get('趋势概率', 0.5))
    trend_strength = float(v7_result.get('趋势强度', 0.5))
    
    leader_score = (
        money_momentum * 0.30 +
        trend_strength * 0.35 +
        trend_stability * 0.20 +
        trend_probability * 0.15
    ) * 100
    print(f"   3. 龙头得分: {leader_score:.0f} × 0.25 = {leader_score * 0.25:.1f}")
    print(f"      - 资金动量: {money_momentum:.3f} × 30%")
    print(f"      - 趋势强度: {trend_strength:.3f} × 35%")
    print(f"      - 趋势稳定: {trend_stability:.3f} × 20%")
    print(f"      - 趋势概率: {trend_probability:.3f} × 15%")
    
    # 结构位置
    close_series = df['close']
    MA20 = float(close_series.rolling(20).mean().iloc[-1])
    MA60 = float(close_series.rolling(60).mean().iloc[-1])
    HHV20 = float(close_series.tail(20).max())
    LLV20 = float(close_series.tail(20).min())
    current_price = float(df['close'].iloc[-1])
    price_position = current_price / MA20 if MA20 > 0 else 1.0
    
    if len(df) >= 2:
        today_pct = float((df['close'].iloc[-1] / df['close'].iloc[-2] - 1) * 100)
    else:
        today_pct = 0
    
    # 判断结构类型
    if current_price >= HHV20 * 0.95 and (1 < today_pct and today_pct <= 10):
        structure_score = 100
        structure_type_desc = "启动型"
    elif price_position > 1.05 and MA20 > MA60 and (0 < today_pct and today_pct <= 7):
        structure_score = 80
        structure_type_desc = "加速型"
    elif current_price > HHV20 * 1.08 or today_pct > 10:
        structure_score = 20
        structure_type_desc = "高位分歧"
    elif price_position < 1.02 and float(v7_result.get('量能爆发', 0)) < 0.3 and today_pct > -3:
        structure_score = 60
        structure_type_desc = "调整型"
    else:
        structure_score = 40
        structure_type_desc = "震荡型"
    
    print(f"   4. 结构位置: {structure_score:.0f} × 0.15 = {structure_score * 0.15:.1f} ({structure_type_desc})")
    print(f"      - 当前价: {current_price:.2f} | MA20: {MA20:.2f} | 位置: {price_position:.3f}")
    print(f"      - 20日最高: {HHV20:.2f} | 今日涨幅: {today_pct:.2f}%")
    
    # 突破强度
    breakout_score = float(v7_result.get('突破强度', 0)) * 100
    print(f"   5. 突破强度: {breakout_score:.0f} × 0.10 = {breakout_score * 0.10:.1f}")
    
    # 量能爆发
    volume_score = float(v7_result.get('量能爆发', 0)) * 100
    print(f"   6. 量能爆发: {volume_score:.0f} × 0.05 = {volume_score * 0.05:.1f}")
    
    # 基础分合计
    base_score = (
        theme_rank_score * 0.25 +
        purity_score * 0.20 +
        leader_score * 0.25 +
        structure_score * 0.15 +
        breakout_score * 0.10 +
        volume_score * 0.05
    )
    print(f"\n   ──────────────────────────────────────")
    print(f"   基础分合计: {base_score:.2f}")
    
    # 修正项
    fail_prob = float(v7_result.get('失败概率', 0.5))
    
    # 失败概率加分
    fail_bonus = 0
    if fail_prob < 0.15:
        fail_bonus = 10
    elif fail_prob < 0.25:
        fail_bonus = 5
    print(f"   + 失败概率加分: {fail_bonus}")
    
    # 主题排名加分
    rank_bonus = 0  # 需要主题数据
    print(f"   + 主题排名加分: {rank_bonus}")
    
    # 风险惩罚
    risk_penalty = 0
    if fail_prob >= 0.55:
        risk_penalty = -25
    elif fail_prob >= 0.50:
        risk_penalty = -15
    elif fail_prob >= 0.45:
        risk_penalty = -8
    print(f"   + 风险惩罚: {risk_penalty}")
    
    # 最终得分
    final_score = base_score + fail_bonus + rank_bonus + risk_penalty
    final_score = min(100, max(0, final_score))
    print(f"   ──────────────────────────────────────")
    print(f"   最终开仓评分: {final_score:.2f}")
    
    print("\n" + "=" * 80)
    print("💡 分析建议:")
    print("=" * 80)
    
    if structure_score < 60:
        print(f"⚠️ 结构位置偏低 ({structure_type_desc})，可能影响排名")
    
    if fail_prob >= 0.45:
        print(f"⚠️ 失败概率较高 ({fail_prob:.1%})，建议关注风险")
    
    if purity_score < 50:
        print(f"⚠️ 主题纯度较低 ({purity_score:.0f})，建议确认主题匹配")
    
    if leader_score < 60:
        print(f"⚠️ 龙头得分较低 ({leader_score:.0f})，资金关注度可能不足")
    
    print("\n✅ 测试完成")

if __name__ == '__main__':
    test_eston_v9_score()
