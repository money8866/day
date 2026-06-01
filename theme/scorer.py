import time
from datetime import datetime, timedelta

import pandas as pd
import tushare as ts
import numpy as np

from config import TS_TOKEN, MIN_STOCKS, EMOTION_WEIGHTS, TREND_WEIGHTS
from db import (
    load_all_themes, get_theme_stock_codes, save_theme_score,
    get_all_stock_codes
)


def _get_pro():
    return ts.pro_api(TS_TOKEN)


# ───── in-memory daily cache ─────
_daily_cache = {}
_limit_cache = {}
_limit_list_cache = {}


def _get_limit_df(trade_date):
    """获取涨停数据"""
    if trade_date in _limit_cache:
        return _limit_cache[trade_date]
    pro = _get_pro()
    try:
        df = pro.limit_list_d(trade_date=trade_date)
        if df is not None and not df.empty:
            _limit_cache[trade_date] = df
            return df
    except:
        pass
    _limit_cache[trade_date] = pd.DataFrame()
    return _limit_cache[trade_date]


def _get_limit_list_ths(trade_date):
    """获取同花顺涨停池数据（含连板高度）"""
    if trade_date in _limit_list_cache:
        return _limit_list_cache[trade_date]
    pro = _get_pro()
    try:
        df = pro.limit_list_ths(trade_date=trade_date, limit_type='涨停池')
        if df is not None and not df.empty:
            _limit_list_cache[trade_date] = df
            return df
    except:
        pass
    _limit_list_cache[trade_date] = pd.DataFrame()
    return _limit_list_cache[trade_date]


def _preload_daily(stock_list, trade_date, days=30):
    """预加载日线数据"""
    start_date = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=days)).strftime("%Y%m%d")
    pro = _get_pro()

    need = [s for s in stock_list if s not in _daily_cache]
    if need:
        BATCH = 150
        for i in range(0, len(need), BATCH):
            batch = need[i:i + BATCH]
            try:
                df = pro.daily(ts_code=",".join(batch), start_date=start_date, end_date=trade_date)
                if df is not None and not df.empty:
                    for code, grp in df.groupby("ts_code"):
                        _daily_cache[code] = grp.sort_values("trade_date")
            except Exception as e:
                print(f"  batch daily error: {e}")
            time.sleep(0.15)

    return {s: _daily_cache.get(s) for s in stock_list if s in _daily_cache}


def _normalize(series):
    """百分位排名归一化（抗极端值）"""
    return series.rank(pct=True).fillna(0)


def _calc_emotion_score(stocks, limit_stocks, limit_code_map, zt_df, today_quotes, trade_date):
    """
    情绪分计算（游资视角）
    emotion_score = 
        0.30 * 涨停家数占比
        +0.20 * 连板家数占比
        +0.15 * 龙头高度
        +0.15 * 晋级率
        +0.10 * 炸板修正
        +0.10 * 20cm数量
    """
    n = len(stocks)
    if n == 0:
        return 0
    
    # 1. 涨停家数占比 (0-100)
    limit_cnt = sum(1 for s in stocks if s in limit_stocks)
    limit_ratio = (limit_cnt / n) * 100
    
    # 2. 连板家数占比 (0-100)
    lb_cnt = 0
    max_lb = 0
    for s in stocks:
        if s in limit_code_map:
            try:
                lt = limit_code_map[s].get("limit_times")
                if lt is not None and not (lt != lt):
                    bt = int(lt)
                    if bt >= 2:
                        lb_cnt += 1
                    if bt > max_lb:
                        max_lb = bt
            except (ValueError, TypeError):
                pass
    lb_ratio = (lb_cnt / n) * 100
    
    # 3. 龙头高度 (0-100)
    height_map = {0: 0, 1: 20, 2: 40, 3: 60, 4: 80, 5: 90}
    leader_height = height_map.get(max_lb, 100) if max_lb >= 6 else height_map.get(max_lb, 0)
    
    # 4. 晋级率 (0-100)
    promote_rate = 0
    if zt_df is not None and not zt_df.empty:
        zt_df['ts_code'] = zt_df['ts_code'].astype(str)
        stocks_set = set(str(s) for s in stocks)
        sector_zt = zt_df[zt_df['ts_code'].isin(stocks_set)]
        if not sector_zt.empty and 'up_stat' in sector_zt.columns:
            promoted = sector_zt[sector_zt['up_stat'].str.contains('连板', na=False)]
            promote_rate = min((len(promoted) / max(len(sector_zt), 1)) * 100, 100)
    
    # 5. 炸板修正 (0-100, 负向)
    break_rate = 0
    if zt_df is not None and not zt_df.empty:
        if 'limit_times' in zt_df.columns:
            broken = zt_df[zt_df['limit_times'] == 0]
            break_rate = min((len(broken) / max(n, 1)) * 100, 100)
    
    # 6. 20cm数量占比 (0-100)
    cm20_cnt = 0
    for s in stocks:
        if s in today_quotes:
            pct = today_quotes[s].get("pct_chg", 0)
            if pct >= 19.5:
                cm20_cnt += 1
    cm20_ratio = (cm20_cnt / n) * 100
    
    # 加权计算
    emotion_score = (
        EMOTION_WEIGHTS["limit_ratio"] * limit_ratio +
        EMOTION_WEIGHTS["lb_ratio"] * lb_ratio +
        EMOTION_WEIGHTS["leader_height"] * leader_height +
        EMOTION_WEIGHTS["promote_rate"] * promote_rate -
        EMOTION_WEIGHTS["break_rate"] * break_rate +
        EMOTION_WEIGHTS["cm20_ratio"] * cm20_ratio
    )
    
    return min(max(emotion_score, 0), 100)


def _calc_trend_score(stocks, daily_data, trade_date):
    """
    趋势分计算（机构视角）
    trend_score = 
        0.30 * 20日涨幅
        +0.20 * 10日涨幅
        +0.20 * 强势股比例
        +0.15 * 成交额增量
        +0.15 * 均线结构
    """
    n = len(stocks)
    if n == 0:
        return 0
    
    pct_20d_list = []
    pct_10d_list = []
    strong_cnt = 0
    amount_growth_list = []
    ma_structure_list = []
    
    for s in stocks:
        df = daily_data.get(s)
        if df is None or len(df) < 20:
            continue
        
        df = df.sort_values("trade_date")
        
        # 1. 20日涨幅
        if len(df) >= 20:
            pct_20d = (df["close"].iloc[-1] / df["close"].iloc[-20] - 1) * 100
            pct_20d_list.append(pct_20d)
        
        # 2. 10日涨幅
        if len(df) >= 10:
            pct_10d = (df["close"].iloc[-1] / df["close"].iloc[-10] - 1) * 100
            pct_10d_list.append(pct_10d)
        
        # 3. 强势股比例（涨幅>5%）
        if len(df) >= 1:
            pct_chg = df["pct_chg"].iloc[-1]
            if pct_chg >= 5:
                strong_cnt += 1
        
        # 4. 成交额增量（5日均量/20日均量）
        if len(df) >= 20:
            ma5_vol = df["amount"].iloc[-5:].mean()
            ma20_vol = df["amount"].iloc[-20:].mean()
            if ma20_vol > 0:
                growth = (ma5_vol / ma20_vol - 1) * 100
                amount_growth_list.append(growth)
        
        # 5. 均线结构（MA5>MA10>MA20）
        if len(df) >= 20:
            ma5 = df["close"].iloc[-5:].mean()
            ma10 = df["close"].iloc[-10:].mean()
            ma20 = df["close"].iloc[-20:].mean()
            if ma5 > ma10 > ma20:
                ma_structure_list.append(100)
            elif ma5 > ma10:
                ma_structure_list.append(60)
            else:
                ma_structure_list.append(20)
    
    # 计算各指标平均值
    avg_pct_20d = np.mean(pct_20d_list) if pct_20d_list else 0
    avg_pct_10d = np.mean(pct_10d_list) if pct_10d_list else 0
    strong_ratio = (strong_cnt / n) * 100 if n > 0 else 0
    avg_amount_growth = np.mean(amount_growth_list) if amount_growth_list else 0
    avg_ma_structure = np.mean(ma_structure_list) if ma_structure_list else 0
    
    # 归一化到0-100
    pct_20d_score = min(max((avg_pct_20d + 20) * 2.5, 0), 100)
    pct_10d_score = min(max((avg_pct_10d + 10) * 5, 0), 100)
    amount_growth_score = min(max((avg_amount_growth + 50), 0), 100)
    
    # 加权计算
    trend_score = (
        TREND_WEIGHTS["pct_20d"] * pct_20d_score +
        TREND_WEIGHTS["pct_10d"] * pct_10d_score +
        TREND_WEIGHTS["strong_ratio"] * strong_ratio +
        TREND_WEIGHTS["amount_growth"] * amount_growth_score +
        TREND_WEIGHTS["ma_structure"] * avg_ma_structure
    )
    
    return min(max(trend_score, 0), 100)


def _get_theme_style(theme_name):
    """
    判断主题风格类型
    emotion: 情绪驱动型（AI、机器人、华为等题材）
    trend: 趋势驱动型（电力、煤炭、有色等板块）
    """
    emotion_keywords = [
        "AI", "机器人", "华为", "鸿蒙", "智能驾驶", "低空", "商业航天",
        "核聚变", "信创", "金融科技", "半导体", "算力", "应用", "终端"
    ]
    for kw in emotion_keywords:
        if kw in theme_name:
            return "emotion"
    return "trend"


def calc_all_theme_scores(trade_date):
    """从 theme_portfolio.db 读取题材/成份股，计算双评分"""
    pro = _get_pro()

    themes = load_all_themes()
    print(f"从 theme_portfolio.db 加载 {len(themes)} 个题材")

    limit_df = _get_limit_df(trade_date)
    limit_stocks = set(limit_df["ts_code"].tolist()) if not limit_df.empty else set()
    limit_code_map = {}
    if not limit_df.empty:
        for _, r in limit_df.iterrows():
            limit_code_map[r["ts_code"]] = r

    zt_df = _get_limit_list_ths(trade_date)

    all_stock_codes = get_all_stock_codes()
    print(f"所有成份股（去重）: {len(all_stock_codes)} 只")
    daily_data = _preload_daily(all_stock_codes, trade_date)

    today_quotes = {}
    for code, df in daily_data.items():
        if df is not None and not df.empty and df["trade_date"].iloc[-1] == trade_date:
            today_quotes[code] = df.iloc[-1].to_dict()

    results = []
    for theme_name, industry, keywords in themes:
        stocks = get_theme_stock_codes(theme_name)
        if len(stocks) < MIN_STOCKS:
            continue

        valid_quotes = [today_quotes[s] for s in stocks if s in today_quotes]
        if len(valid_quotes) < MIN_STOCKS:
            continue

        quotes_df = pd.DataFrame(valid_quotes)

        avg_pct = quotes_df["pct_chg"].mean()
        up_ratio = (quotes_df["pct_chg"] > 0).mean()
        limit_cnt = sum(1 for s in stocks if s in limit_stocks)
        limit_ratio = limit_cnt / len(stocks) if stocks else 0
        amount = quotes_df["amount"].sum()
        top3 = quotes_df.nlargest(3, "pct_chg")
        leader_premium = top3.iloc[0]["pct_chg"] - top3.iloc[1]["pct_chg"] if len(top3) >= 2 else 0

        max_boards = 0
        for s in stocks:
            if s in limit_code_map:
                try:
                    lt = limit_code_map[s].get("limit_times")
                    if lt is not None and not (lt != lt):
                        bt = int(lt)
                        if bt > max_boards:
                            max_boards = bt
                except (ValueError, TypeError):
                    pass
        height_map = {0: 0, 1: 10, 2: 30, 3: 60, 4: 80}
        height_score = height_map.get(max_boards, 100) if max_boards >= 5 else height_map.get(max_boards, 0)

        emotion_score = _calc_emotion_score(
            stocks, limit_stocks, limit_code_map, zt_df, today_quotes, trade_date
        )
        
        trend_score = _calc_trend_score(stocks, daily_data, trade_date)
        
        style = _get_theme_style(theme_name)
        if style == "emotion":
            final_score = emotion_score * 0.7 + trend_score * 0.3
        else:
            final_score = emotion_score * 0.3 + trend_score * 0.7

        results.append({
            "theme_name": theme_name,
            "score": final_score,
            "emotion_score": emotion_score,
            "trend_score": trend_score,
            "style": style,
            "avg_pct": avg_pct,
            "limit_ratio": limit_ratio,
            "up_ratio": up_ratio,
            "amount": amount,
            "leader_premium": leader_premium,
            "height_score": height_score,
            "_stock_count": len(stocks)
        })

    if not results:
        print("无题材评分结果")
        return []

    df = pd.DataFrame(results)
    df = df.sort_values("score", ascending=False).reset_index(drop=True)

    for _, r in df.iterrows():
        save_theme_score(
            trade_date, r["theme_name"], round(r["score"], 2),
            round(r["emotion_score"], 2), round(r["trend_score"], 2),
            round(r["avg_pct"], 4), round(r["limit_ratio"], 4), round(r["up_ratio"], 4),
            round(r["amount"], 2), round(r["leader_premium"], 4), round(r["height_score"], 2)
        )

    print(f"评分完成: {len(df)} 个题材")
    return df.to_dict("records")
