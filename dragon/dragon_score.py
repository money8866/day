# -*- coding: utf-8 -*-
"""龙头评分系统 - 自动筛选核心资产 Top10%"""
import pandas as pd
import numpy as np
import time
from config import DRAGON
from data_engine import (
    fetch_stock_daily, fetch_daily_basic, fetch_stock_basic,
    calc_ma, calc_rsi, fetch_csi2000_stocks
)

def score_dragon_candidates(candidates_df, max_candidates=30, recurring_themes=None):
    """
    对候选股票进行龙头评分
    candidates_df: from get_main_theme_stocks(), 需含 code 列
    max_candidates: 最多评分N只 (控制API调用量)
    recurring_themes: set() of recurring theme names (历史反复活跃板块)
    返回: scored DataFrame (scored, sorted desc)
    """
    if candidates_df is None or len(candidates_df) == 0:
        return pd.DataFrame()

    # 预筛选: 只取每个板块涨幅前3只
    if 'pct_chg' in candidates_df.columns:
        candidates_df = candidates_df.sort_values('pct_chg', ascending=False)
        top_per_board = candidates_df.groupby('board_name').head(3)
    else:
        top_per_board = candidates_df.head(max_candidates)

    if len(top_per_board) > max_candidates:
        top_per_board = top_per_board.head(max_candidates)

    result = []
    basic_df = fetch_stock_basic(use_cache=True)
    if basic_df is None:
        return pd.DataFrame()

    today = pd.Timestamp.now().strftime("%Y%m%d")

    for _, row in candidates_df.iterrows():
        code = row.get("code", "")
        if not code:
            continue

        # 排除ST
        name = row.get("name", "")
        if "ST" in name.upper():
            continue

        # 检查上市日期
        stock_info = basic_df[basic_df['symbol'] == code]
        if len(stock_info) == 0:
            continue
        list_date = stock_info['list_date'].iloc[0]
        if pd.notna(list_date) and (pd.Timestamp.now() - list_date).days < DRAGON["min_price"]:
            # min_price这里复用为天数
            pass  # 简化处理，不严格过滤

        # 获取日线
        ts_code = code + ".SH" if code.startswith(('6', '5', '9')) else code + ".SZ"
        try:
            daily = fetch_stock_daily(ts_code, start_date="20260101", use_cache=True)
        except Exception:
            time.sleep(0.5)
            continue
        if daily is None or len(daily) < 20:
            continue

        daily = calc_ma(daily, [5, 10, 20, 60])
        daily = calc_rsi(daily, 14)
        latest = daily.iloc[-1]

        price = latest['close']
        if price < DRAGON["min_price"]:
            continue

        # 获取换手率/市值
        try:
            db = fetch_daily_basic(ts_code=ts_code, trade_date=today, use_cache=True)
        except Exception:
            db = None
        turnover = 0
        total_mv = 0
        pe = 999
        if db is not None and len(db) > 0:
            db_row = db.iloc[0]
            turnover = db_row.get('turnover_rate', 0) or 0
            total_mv = db_row.get('total_mv', 0) or 0
            pe = db_row.get('pe', 999) or 999
        else:
            total_mv = 999999  # 无数据时跳过市值过滤

        # 市值过滤 (total_mv单位是万元)
        # 1. 最小市值过滤
        if total_mv < DRAGON["min_market_cap"] / 10000 and total_mv < 999999:
            continue
        
        # 2. CSI2000过滤 (精确成分股列表)
        if DRAGON.get("csi2000_only", False):
            csi2000_set = fetch_csi2000_stocks(use_cache=True)
            if csi2000_set is not None:
                if code not in csi2000_set:
                    continue
            else:
                # API失败, fallback到市值过滤
                if total_mv >= 1000000:  # 大于等于100亿，排除
                    continue

        # PE过滤
        if pe > 0 and pe < DRAGON.get("max_pe", 200):
            pass  # PE正常
        elif pe <= 0:
            pass  # 亏损股,不过滤

        # RSI过滤
        rsi = latest.get('rsi_14', 50) or 50
        if rsi > DRAGON["max_rsi"]:
            continue

        # === 评分 ===
        scores = {}

        # 板块排名 (0-100, rank 1=100, rank 20=0)
        board_rank = row.get("board_rank", 20)
        scores["sector_rank"] = max(0, 100 - (board_rank - 1) * 5)

        # 5日涨幅
        if len(daily) >= 5:
            mom5 = (latest['close'] - daily['close'].iloc[-5]) / daily['close'].iloc[-5] * 100
            scores["momentum_5d"] = min(100, max(0, 50 + mom5))
        else:
            mom5 = 0
            scores["momentum_5d"] = 30

        # 10日涨幅
        if len(daily) >= 10:
            mom10 = (latest['close'] - daily['close'].iloc[-10]) / daily['close'].iloc[-10] * 100
            scores["momentum_10d"] = min(100, max(0, 50 + mom10))
        else:
            mom10 = 0
            scores["momentum_10d"] = 30

        # 20日涨幅
        if len(daily) >= 20:
            mom20 = (latest['close'] - daily['close'].iloc[-20]) / daily['close'].iloc[-20] * 100
            scores["momentum_20d"] = min(100, max(0, 50 + mom20 * 0.5))
        else:
            mom20 = 0
            scores["momentum_20d"] = 30

        # 换手率
        scores["turnover"] = min(100, turnover * 10)

        # 量比
        vol_ratio = row.get("vol_ratio", 1.0) or 1.0
        scores["volume_ratio"] = min(100, vol_ratio * 30)

        # 均线多头排列 (MA5>MA10>MA20>MA60)
        ma_vals = [latest.get(f'ma{p}', 0) or 0 for p in [5, 10, 20, 60]]
        aligned = all(ma_vals[i] > ma_vals[i+1] for i in range(3) if ma_vals[i+1] > 0)
        above_cnt = sum(1 for v in ma_vals if v > 0 and price > v)
        scores["ma_alignment"] = 100 if aligned else above_cnt * 25

        # 连板天数 (近似: 用5日涨幅估算)
        limit_days = 0
        if mom5 >= 10: limit_days = 1
        if mom5 >= 20: limit_days = 2
        if mom5 >= 30: limit_days = 3
        scores["limit_days"] = min(100, limit_days * 33)

        # 加权总分
        w = DRAGON["weights"]
        total_score = sum(scores[k] * w.get(k, 0) for k in scores)

        # 历史反复活跃板块加分 (+10)
        if recurring_themes and row.get('board_name', '') in recurring_themes:
            total_score += 10
            print(f"  📌 {name}({code}) 历史反复活跃板块加分 +10")

        result.append({
            "code": code,
            "name": name,
            "price": price,
            "pct_chg": row.get("pct_chg", 0),
            "turnover": turnover,
            "total_mv_yi": total_mv / 10000 if total_mv else 0,
            "pe": pe if pe > 0 else "-",
            "rsi": rsi,
            "mom5": round(mom5, 2),
            "mom10": round(mom10, 2),
            "mom20": round(mom20, 2),
            "board_name": row.get("board_name", ""),
            "board_rank": board_rank,
            "ma_aligned": aligned,
            "total_score": round(total_score, 1),
            **{f"score_{k}": round(v, 1) for k, v in scores.items()},
        })

        time.sleep(0.3)  # Tushare限频

    if not result:
        return pd.DataFrame()

    df = pd.DataFrame(result)
    df = df.sort_values('total_score', ascending=False).reset_index(drop=True)

    # Top 10%
    top_n = max(1, int(len(df) * DRAGON["top_pct"]))
    top_n = max(top_n, 5)  # 至少5只

    df['is_dragon'] = False
    df.loc[:top_n, 'is_dragon'] = True
    df.loc[df['total_score'] >= DRAGON["min_score"], 'is_dragon'] = True

    return df

def get_dragon_list(max_count=20):
    """
    完整龙头筛选流程
    返回: top dragons DataFrame
    """
    from sector_strength import analyze_sector_strength, get_main_theme_stocks

    # 1. 板块强度分析
    sector_result = analyze_sector_strength()
    main_themes = sector_result["main_themes"]

    # 2. 获取主线板块成分股
    candidates = get_main_theme_stocks(main_themes, top_n_boards=5)
    if candidates is None or len(candidates) == 0:
        return pd.DataFrame(), sector_result

    # 3. 龙头评分
    scored = score_dragon_candidates(candidates)
    dragons = scored[scored['is_dragon'] == True].head(max_count)

    return dragons, sector_result
