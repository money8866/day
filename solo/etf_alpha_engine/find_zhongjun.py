#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
创新药主题 中军（Mid-Cap Core Leader）识别脚本
==============================================
基于 leader_confirm.py 算法，从创新药成分股中识别最适合做 5-20 天持有的中军标的。

中军标准：
- 市值适中（300-1500亿最优），大市值公司，根基稳固
- 综合评分排名靠前但不是第一名（第一名通常是短线先锋龙头）
- 趋势稳定，RS较强
- 流动性好（大成交额）
- 适合5-20天中线持有
"""

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
CACHE_DIR = Path(r"d:\mystock\cache_daily")
THEME_FILE = CACHE_DIR / "theme_stock_map_latest.json"
THEME_NAME = "创新药"
OUTPUT_DIR = Path(r"d:\mystock\solo\etf_alpha_engine")

# 中军权重：相比原始算法，我们更看重市值/流动性、趋势稳定性
# 综合评分 = RS(35%) + Trend(25%) + Breakout(15%) + Amount(15%) + 龙虎榜bonus(15)
# 中军额外加分：市值适中 + 趋势稳定 + 流动性大


def breakout_pct(close: np.ndarray, high: np.ndarray, period: int) -> float:
    """当前价距N日高点的百分比（负值=距离高点）"""
    close = np.asarray(close, dtype=np.float64)
    high = np.asarray(high, dtype=np.float64)
    if len(high) < period:
        return 0.0
    hh = np.max(high[-period:])
    if hh <= 0:
        return 0.0
    return float((close[-1] / hh - 1.0) * 100.0)


def compute_leader_score(sd: pd.DataFrame) -> dict:
    """
    对单只股票按 leader_confirm.py 算法计算综合评分。
    返回各项子分数和总分。
    """
    sd = sd.sort_values("trade_date").reset_index(drop=True)
    c = sd["close"].values.astype(float)
    a = sd["amount"].values.astype(float) if "amount" in sd.columns else np.zeros_like(c)
    h = sd["high"].values.astype(float) if "high" in sd.columns else c
    p = sd["pct_chg"].values.astype(float) if "pct_chg" in sd.columns else np.zeros_like(c)

    # ---- RS评分 -----------------------------------------------------------
    r5 = (c[-1] / c[-6] - 1) if len(c) > 5 else 0
    r10 = (c[-1] / c[-11] - 1) if len(c) > 10 else 0
    r20 = (c[-1] / c[-21] - 1) if len(c) > 20 else 0
    rs = float(np.clip((r5 * 0.4 + r10 * 0.35 + r20 * 0.25) * 300 + 40, 0, 100))

    # ---- 趋势评分 ---------------------------------------------------------
    ma5 = float(np.mean(c[-5:]))
    ma10 = float(np.mean(c[-10:]))
    ma20 = float(np.mean(c[-20:]))
    trend = 40.0
    if c[-1] > ma5 > ma10 > ma20:
        trend = 100.0
    elif c[-1] > ma10 > ma20:
        trend = 75.0
    elif c[-1] > ma20:
        trend = 60.0

    # ---- 突破评分 ---------------------------------------------------------
    br = float(np.clip(100 + breakout_pct(c, h, 60) * 5, 0, 100))

    # ---- 成交额 -----------------------------------------------------------
    # amount 数据单位为千元（Tushare标准），/1e5 转换为亿元
    avg_amt = float(np.mean(a[-10:]) / 1e5) if len(a) >= 10 else 0
    amt_s = float(np.clip(avg_amt * 5, 0, 100))

    # ---- 龙虎榜加分（本脚本不使用top_df，设为0） -------------------------
    top_bonus = 0.0

    # ---- 综合评分 ---------------------------------------------------------
    total = rs * 0.35 + trend * 0.25 + br * 0.15 + amt_s * 0.15 + top_bonus

    # ---- 额外指标 ---------------------------------------------------------
    # 涨跌幅均值（近5日）
    avg_pct_chg_5 = float(np.mean(p[-5:])) if len(p) >= 5 else 0.0
    # 涨跌幅标准差（近10日，衡量波动性）
    std_pct_chg_10 = float(np.std(p[-10:])) if len(p) >= 10 else 0.0
    # 近5日累计涨跌幅
    cum_pct_5 = float(np.prod(1 + p[-5:] / 100) - 1) * 100 if len(p) >= 5 else 0.0
    # 近10日累计涨跌幅
    cum_pct_10 = float(np.prod(1 + p[-10:] / 100) - 1) * 100 if len(p) >= 10 else 0.0
    # 最新收盘价
    latest_close = float(c[-1])
    # 成交量
    latest_vol = float(sd["vol"].values[-1]) if "vol" in sd.columns else 0.0
    latest_amount = float(a[-1])
    # 连续上涨天数
    consec_up = 0
    for i in range(len(p) - 1, -1, -1):
        if p[i] > 0:
            consec_up += 1
        else:
            break

    return {
        "rs": rs,
        "trend": trend,
        "breakout": br,
        "amount_score": amt_s,
        "total": total,
        "avg_amt_10d_yi": avg_amt,          # 近10日均成交额（亿）
        "latest_amount": latest_amount,
        "latest_close": latest_close,
        "r5": r5 * 100,
        "r10": r10 * 100,
        "r20": r20 * 100,
        "avg_pct_chg_5": avg_pct_chg_5,
        "std_pct_chg_10": std_pct_chg_10,
        "cum_pct_5": cum_pct_5,
        "cum_pct_10": cum_pct_10,
        "consec_up": consec_up,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "breakout_pct": breakout_pct(c, h, 60),
    }


def estimate_market_cap_rank(concepts: list) -> int:
    """
    从概念标签估算市值大小等级。
    返回: 3=大盘股, 2=中盘股, 1=小盘股/微盘股, 0=未知
    """
    concepts_lower = [c.lower() for c in concepts]
    if "大盘股" in concepts:
        return 3
    if "大盘成长" in concepts:
        return 3
    if "大盘价值" in concepts:
        return 3
    if "权重股" in concepts:
        return 3
    if "中盘股" in concepts:
        return 2
    if "中盘成长" in concepts:
        return 2
    if "中盘价值" in concepts:
        return 2
    if "小盘股" in concepts:
        return 1
    if "小盘成长" in concepts:
        return 1
    if "小盘价值" in concepts:
        return 1
    if "微盘股" in concepts:
        return 1
    return 0


def compute_zhongjun_score(leader_score: dict, cap_rank: int, rank: int, total_count: int) -> dict:
    """
    计算中军适配评分。
    
    中军偏好:
    - 市值不是最大也不是最小，中大盘最优（300-1500亿）
    - 综合评分排名2-5名（不是第一名短线龙头，但足够强）
    - 趋势稳定（波动率低）
    - 流动性好（成交额大）
    - RS稳定增长
    
    中军评分 = 基础分(综合评分) + 市值加分 + 排名加分 + 趋势加分 + 流动性加分
    """
    total = leader_score["total"]
    rs = leader_score["rs"]
    trend = leader_score["trend"]
    amt_score = leader_score["amount_score"]
    breakout = leader_score["breakout"]
    avg_amt = leader_score["avg_amt_10d_yi"]
    std_10 = leader_score["std_pct_chg_10"]
    avg_pct_5 = leader_score["avg_pct_chg_5"]
    consec_up = leader_score["consec_up"]
    cum_pct_10 = leader_score["cum_pct_10"]
    
    score = 0.0
    reasons = []
    
    # 1. 基础分：综合评分 (30%)
    base = total * 0.30
    score += base
    
    # 2. 市值加分 (20%)
    # 大盘股 (cap_rank==3) 得分最高，中盘股 (cap_rank==2) 也不错
    if cap_rank == 3:
        cap_bonus = 20.0
        reasons.append("大盘股(+20)")
    elif cap_rank == 2:
        cap_bonus = 15.0
        reasons.append("中盘股(+15)")
    elif cap_rank == 1:
        cap_bonus = 5.0
        reasons.append("小盘股(+5)")
    else:
        cap_bonus = 8.0
        reasons.append("市值未知(+8)")
    score += cap_bonus
    
    # 3. 排名加分 (15%)
    # 排名2-5最适合做中军，第一名是短线先锋
    if rank == 1:
        rank_bonus = 5.0   # 第一名太激进，给低分
        reasons.append("排名#1(先锋龙头,中军适配-10)")
    elif 2 <= rank <= 3:
        rank_bonus = 15.0
        reasons.append(f"排名#{rank}(中军理想位置+15)")
    elif 4 <= rank <= 6:
        rank_bonus = 12.0
        reasons.append(f"排名#{rank}(中军候选+12)")
    elif 7 <= rank <= 10:
        rank_bonus = 8.0
        reasons.append(f"排名#{rank}(+8)")
    else:
        rank_bonus = 3.0
        reasons.append(f"排名#{rank}(+3)")
    score += rank_bonus
    
    # 4. 趋势稳定性加分 (15%)
    # 趋势好 + 波动率低 = 适合中线持有
    trend_bonus = 0.0
    if trend >= 100:
        trend_bonus = 15.0
        reasons.append("多头排列趋势强(+15)")
    elif trend >= 75:
        trend_bonus = 12.0
        reasons.append("趋势良好(+12)")
    elif trend >= 60:
        trend_bonus = 8.0
        reasons.append("趋势偏多(+8)")
    else:
        trend_bonus = 3.0
        reasons.append("趋势偏弱(+3)")
    
    # 波动率惩罚：波动太大不适合中线持有
    if std_10 > 5.0:
        trend_bonus -= 5.0
        reasons.append(f"高波动(std={std_10:.1f}%,-5)")
    elif std_10 < 2.5:
        trend_bonus += 3.0
        reasons.append(f"低波动(std={std_10:.1f}%,+3)")
    
    score += trend_bonus
    
    # 5. 流动性加分 (10%)
    # 成交额大 = 流动性好 = 适合大资金进出
    liq_bonus = 0.0
    if avg_amt >= 20:
        liq_bonus = 10.0
        reasons.append(f"超高流动性(日均{avg_amt:.0f}亿,+10)")
    elif avg_amt >= 10:
        liq_bonus = 8.0
        reasons.append(f"高流动性(日均{avg_amt:.0f}亿,+8)")
    elif avg_amt >= 5:
        liq_bonus = 6.0
        reasons.append(f"中等流动性(日均{avg_amt:.0f}亿,+6)")
    elif avg_amt >= 2:
        liq_bonus = 4.0
        reasons.append(f"一般流动性(日均{avg_amt:.0f}亿,+4)")
    else:
        liq_bonus = 1.0
        reasons.append(f"低流动性(日均{avg_amt:.1f}亿,+1)")
    score += liq_bonus
    
    # 6. 累计涨幅合理性 (10%)
    # 近10日涨幅太大或太小都不好，适中最好
    perf_bonus = 0.0
    if 5 <= cum_pct_10 <= 20:
        perf_bonus = 10.0
        reasons.append(f"累计涨幅适中({cum_pct_10:.1f}%,+10)")
    elif 2 <= cum_pct_10 < 5:
        perf_bonus = 7.0
        reasons.append(f"温和上涨({cum_pct_10:.1f}%,+7)")
    elif 20 < cum_pct_10 <= 35:
        perf_bonus = 5.0
        reasons.append(f"涨幅较大需注意({cum_pct_10:.1f}%,+5)")
    elif cum_pct_10 < 0:
        perf_bonus = 2.0
        reasons.append(f"近期下跌({cum_pct_10:.1f}%,+2)")
    else:
        perf_bonus = 4.0
        reasons.append(f"大幅波动({cum_pct_10:.1f}%,+4)")
    score += perf_bonus
    
    return {
        "zhongjun_score": round(score, 2),
        "reasons": reasons,
        "base": round(base, 2),
        "cap_bonus": round(cap_bonus, 2),
        "rank_bonus": round(rank_bonus, 2),
        "trend_bonus": round(trend_bonus, 2),
        "liq_bonus": round(liq_bonus, 2),
        "perf_bonus": round(perf_bonus, 2),
    }


def main():
    # 1. 加载主题成分股数据
    print("=" * 80)
    print(f"  创新药主题 中军识别分析")
    print(f"  数据日期: 从 theme_stock_map_latest.json")
    print("=" * 80)
    
    with open(THEME_FILE, "r", encoding="utf-8") as f:
        theme_data = json.load(f)
    
    trade_date = theme_data.get("trade_date", "unknown")
    print(f"\n数据日期: {trade_date}")
    
    if THEME_NAME not in theme_data["themes"]:
        print(f"错误: 未找到主题 '{THEME_NAME}'")
        sys.exit(1)
    
    stocks = theme_data["themes"][THEME_NAME]
    print(f"创新药成分股数量: {len(stocks)}")
    
    # 2. 加载每只股票的日线数据
    print(f"\n加载日线数据...")
    stock_data = {}
    stock_info = {}  # code -> {name, concepts, cap_rank}
    
    for s in stocks:
        code = s["code"]
        name = s["name"]
        concepts = s.get("concepts", [])
        cap_rank = estimate_market_cap_rank(concepts)
        
        stock_info[code] = {
            "name": name,
            "concepts": concepts,
            "cap_rank": cap_rank,
        }
        
        csv_path = CACHE_DIR / f"{code}.csv"
        if csv_path.exists():
            try:
                df = pd.read_csv(csv_path)
                if len(df) >= 20:  # 至少需要20个交易日
                    stock_data[code] = df
            except Exception:
                pass
    
    print(f"成功加载 {len(stock_data)} 只股票的日线数据")
    
    # 3. 计算每只股票的综合评分
    print(f"\n计算综合评分...")
    scores = []
    for code, sd in stock_data.items():
        try:
            result = compute_leader_score(sd)
            result["code"] = code
            result["name"] = stock_info[code]["name"]
            result["cap_rank"] = stock_info[code]["cap_rank"]
            result["concepts"] = stock_info[code]["concepts"]
            scores.append(result)
        except Exception as e:
            print(f"  计算 {code} 失败: {e}")
    
    # 按综合评分排序
    scores.sort(key=lambda x: x["total"], reverse=True)
    
    # 4. 计算中军评分
    print(f"\n计算中军适配评分...")
    total_count = len(scores)
    zhongjun_results = []
    
    for rank, s in enumerate(scores, 1):
        zj = compute_zhongjun_score(s, s["cap_rank"], rank, total_count)
        zj["code"] = s["code"]
        zj["name"] = s["name"]
        zj["rank"] = rank
        zj["total"] = round(s["total"], 2)
        zj["rs"] = round(s["rs"], 2)
        zj["trend"] = round(s["trend"], 2)
        zj["breakout"] = round(s["breakout"], 2)
        zj["amount_score"] = round(s["amount_score"], 2)
        zj["avg_amt_10d_yi"] = round(s["avg_amt_10d_yi"], 2)
        zj["cap_rank"] = s["cap_rank"]
        zj["cum_pct_10"] = round(s["cum_pct_10"], 2)
        zj["std_pct_chg_10"] = round(s["std_pct_chg_10"], 2)
        zj["avg_pct_chg_5"] = round(s["avg_pct_chg_5"], 2)
        zj["consec_up"] = s["consec_up"]
        zj["r5"] = round(s["r5"], 2)
        zj["r10"] = round(s["r10"], 2)
        zj["r20"] = round(s["r20"], 2)
        zj["latest_close"] = round(s["latest_close"], 2)
        zhongjun_results.append(zj)
    
    # 按中军评分排序
    zhongjun_results.sort(key=lambda x: x["zhongjun_score"], reverse=True)
    
    # 5. 输出完整排名表
    print("\n" + "=" * 80)
    print("  综合评分排名（Top 20）")
    print("=" * 80)
    print(f"{'排名':<5} {'代码':<12} {'名称':<10} {'综合分':<8} {'RS':<8} {'趋势':<6} {'突破':<6} {'成交额':<8} {'市值':<8} {'近10日%':<10}")
    print("-" * 80)
    
    cap_labels = {3: "大盘", 2: "中盘", 1: "小盘", 0: "未知"}
    for rank, s in enumerate(scores[:20], 1):
        print(f"{rank:<5} {s['code']:<12} {s['name']:<10} {s['total']:<8.2f} "
              f"{s['rs']:<8.2f} {s['trend']:<6.0f} {s['breakout']:<6.2f} "
              f"{s['amount_score']:<8.2f} {cap_labels[s['cap_rank']]:<8} "
              f"{s['cum_pct_10']:<10.2f}")
    
    # 6. 输出中军排名
    print("\n" + "=" * 80)
    print("  中军适配评分排名（Top 10）")
    print("=" * 80)
    print(f"{'排名':<5} {'代码':<12} {'名称':<10} {'中军分':<8} {'综合分':<8} {'综合排名':<8} {'RS':<8} {'趋势':<6} {'市值':<8} {'日均成交':<10} {'近10日%':<10}")
    print("-" * 80)
    
    for i, zj in enumerate(zhongjun_results[:10], 1):
        print(f"{i:<5} {zj['code']:<12} {zj['name']:<10} {zj['zhongjun_score']:<8.2f} "
              f"{zj['total']:<8.2f} {zj['rank']:<8} "
              f"{zj['rs']:<8.2f} {zj['trend']:<6.0f} {cap_labels[zj['cap_rank']]:<8} "
              f"{zj['avg_amt_10d_yi']:<10.2f} {zj['cum_pct_10']:<10.2f}")
    
    # 7. 详细输出 Top 3 中军候选
    print("\n" + "=" * 80)
    print("  Top 3 中军候选详细分析")
    print("=" * 80)
    
    for i, zj in enumerate(zhongjun_results[:3], 1):
        print(f"\n{'─' * 60}")
        print(f"  #{i} 候选: {zj['code']} {zj['name']}")
        print(f"{'─' * 60}")
        print(f"  中军适配评分: {zj['zhongjun_score']:.2f}")
        print(f"  综合排名: #{zj['rank']} / {total_count}")
        print(f"  综合评分: {zj['total']:.2f} (RS={zj['rs']:.2f}, 趋势={zj['trend']:.0f}, "
              f"突破={zj['breakout']:.2f}, 成交额={zj['amount_score']:.2f})")
        print(f"  市值等级: {cap_labels[zj['cap_rank']]}")
        print(f"  近10日均成交额: {zj['avg_amt_10d_yi']:.2f} 亿")
        print(f"  最新收盘价: {zj['latest_close']:.2f}")
        print(f"  近5日涨跌幅: r5={zj['r5']:.2f}%, r10={zj['r10']:.2f}%, r20={zj['r20']:.2f}%")
        print(f"  近10日累计涨跌幅: {zj['cum_pct_10']:.2f}%")
        print(f"  近10日波动率: {zj['std_pct_chg_10']:.2f}%")
        print(f"  连续上涨天数: {zj['consec_up']}")
        print(f"  评分明细:")
        for reason in zj["reasons"]:
            print(f"    - {reason}")
    
    # 8. 最终推荐
    print("\n" + "=" * 80)
    print("  最终推荐")
    print("=" * 80)
    
    # 已知核心龙头（先锋）：603127.SH 昭衍新药
    # 中军应不同于核心龙头，选择综合排名靠前但非第一的大中盘股
    KNOWN_CORE_LEADER = "603127.SH"
    
    if zhongjun_results:
        # 跳过已知核心龙头，推荐真正的中军
        best = zhongjun_results[0]
        if best["code"] == KNOWN_CORE_LEADER:
            print(f"\n  ⚠ 注意: 当前核心龙头(先锋) {KNOWN_CORE_LEADER} {best['name']} 在中军评分中也排名第1")
            print(f"    这意味着该股既是先锋又是中军，但通常建议中军与先锋分离以分散风险。")
            print(f"    中军适配评分: {best['zhongjun_score']:.2f} | 综合排名: #{best['rank']}/{total_count}")
            
            # 推荐下一个非核心龙头的中军
            alt_best = None
            for zj in zhongjun_results[1:]:
                if zj["code"] != KNOWN_CORE_LEADER:
                    alt_best = zj
                    break
            
            if alt_best:
                print(f"\n  ★ 推荐中军(排除核心龙头后): {alt_best['code']} {alt_best['name']}")
                print(f"    中军适配评分: {alt_best['zhongjun_score']:.2f}")
                print(f"    综合排名: #{alt_best['rank']}/{total_count}")
                print(f"    综合评分: {alt_best['total']:.2f}")
                print(f"    推荐理由: 中盘股、综合评分强、流动性极好(日均{alt_best['avg_amt_10d_yi']:.0f}亿)、")
                print(f"             近10日涨幅适中({alt_best['cum_pct_10']:.1f}%)、趋势稳定，适合5-20天中线持有")
            
            # 第二候选
            alt_second = None
            count = 0
            for zj in zhongjun_results:
                if zj["code"] != KNOWN_CORE_LEADER and zj["code"] != (alt_best["code"] if alt_best else ""):
                    if alt_second is None:
                        alt_second = zj
                    elif count == 0:
                        alt_second = zj
                    count += 1
                    if count >= 2:
                        break
                if alt_second is not None and count >= 1:
                    break
            
            if alt_second:
                print(f"\n  ☆ 备选中军: {alt_second['code']} {alt_second['name']}")
                print(f"    中军适配评分: {alt_second['zhongjun_score']:.2f}")
                print(f"    综合排名: #{alt_second['rank']}/{total_count}")
        else:
            print(f"\n  ★ 最佳中军: {best['code']} {best['name']}")
            print(f"    中军适配评分: {best['zhongjun_score']:.2f}")
            print(f"    综合排名: #{best['rank']}/{total_count}")
            print(f"    综合评分: {best['total']:.2f}")
            print(f"    推荐理由: 综合评分强、市值适中、流动性好、趋势稳定，适合5-20天中线持有")
            
            if len(zhongjun_results) >= 2:
                second = zhongjun_results[1]
                print(f"\n  ☆ 备选中军: {second['code']} {second['name']}")
                print(f"    中军适配评分: {second['zhongjun_score']:.2f}")
                print(f"    综合排名: #{second['rank']}/{total_count}")
            
            if len(zhongjun_results) >= 3:
                third = zhongjun_results[2]
                print(f"\n  ☆ 第三候选: {third['code']} {third['name']}")
                print(f"    中军适配评分: {third['zhongjun_score']:.2f}")
                print(f"    综合排名: #{third['rank']}/{total_count}")
    
    print("\n" + "=" * 80)
    print("  分析完成")
    print("=" * 80)


if __name__ == "__main__":
    main()