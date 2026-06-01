import time
from datetime import datetime, timedelta

import pandas as pd
import tushare as ts
import numpy as np

from config import TS_TOKEN, MIN_STOCKS, WEIGHTS
from db import (
    load_all_themes, get_theme_stock_codes, save_theme_score,
    get_all_stock_codes
)


def _get_pro():
    return ts.pro_api(TS_TOKEN)


# ───── in-memory daily cache ─────
_daily_cache = {}
_limit_cache = {}


def _get_limit_df(trade_date):
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


def _preload_daily(stock_list, trade_date, days=30):
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
    mn, mx = series.min(), series.max()
    if mx - mn < 1e-9:
        return pd.Series(np.zeros(len(series)))
    return (series - mn) / (mx - mn + 1e-9)


def calc_all_theme_scores(trade_date):
    """从 theme_portfolio.db 读取题材/成份股，计算评分"""
    pro = _get_pro()

    # 读取题材列表
    themes = load_all_themes()
    print(f"从 theme_portfolio.db 加载 {len(themes)} 个题材")

    # 涨停数据
    limit_df = _get_limit_df(trade_date)
    limit_stocks = set(limit_df["ts_code"].tolist()) if not limit_df.empty else set()
    limit_code_map = {}
    if not limit_df.empty:
        for _, r in limit_df.iterrows():
            limit_code_map[r["ts_code"]] = r

    # 批量预加载成份股的日线数据
    all_stock_codes = get_all_stock_codes()
    print(f"所有成份股（去重）: {len(all_stock_codes)} 只")
    daily_data = _preload_daily(all_stock_codes, trade_date)

    # 获取今天行情
    today_quotes = {}
    for code, df in daily_data.items():
        if df is not None and not df.empty and df["trade_date"].iloc[-1] == trade_date:
            today_quotes[code] = df.iloc[-1]

    results = []
    for theme_name, industry, keywords in themes:
        stocks = get_theme_stock_codes(theme_name)
        if len(stocks) < MIN_STOCKS:
            continue

        valid_quotes = [today_quotes[s] for s in stocks if s in today_quotes]
        if len(valid_quotes) < MIN_STOCKS:
            continue

        quotes_df = pd.DataFrame(valid_quotes)

        # 1. 平均涨幅
        avg_pct = quotes_df["pct_chg"].mean()

        # 2. 上涨占比
        up_ratio = (quotes_df["pct_chg"] > 0).mean()

        # 3. 涨停率
        limit_cnt = sum(1 for s in stocks if s in limit_stocks)
        limit_ratio = limit_cnt / len(stocks) if stocks else 0

        # 4. 总成交额
        amount = quotes_df["amount"].sum()

        # 5. 龙头溢价（涨幅第1-第2的差）
        top3 = quotes_df.nlargest(3, "pct_chg")
        leader_premium = top3.iloc[0]["pct_chg"] - top3.iloc[1]["pct_chg"] if len(top3) >= 2 else 0

        # 6. 连板高度
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

        results.append({
            "theme_name": theme_name,
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
    for col in ["avg_pct", "limit_ratio", "up_ratio", "amount", "leader_premium", "height_score"]:
        df[f"{col}_norm"] = _normalize(df[col])

    df["score"] = (
        WEIGHTS["avg_pct"] * df["avg_pct_norm"]
        + WEIGHTS["limit_ratio"] * df["limit_ratio_norm"]
        + WEIGHTS["up_ratio"] * df["up_ratio_norm"]
        + WEIGHTS["amount"] * df["amount_norm"]
        + WEIGHTS["leader_premium"] * df["leader_premium_norm"]
        + WEIGHTS["height_score"] * df["height_score_norm"]
    ) * 100

    df = df.sort_values("score", ascending=False).reset_index(drop=True)

    for _, r in df.iterrows():
        save_theme_score(
            trade_date, r["theme_name"], round(r["score"], 2),
            round(r["avg_pct"], 4), round(r["limit_ratio"], 4), round(r["up_ratio"], 4),
            round(r["amount"], 2), round(r["leader_premium"], 4), round(r["height_score"], 2)
        )

    print(f"评分完成: {len(df)} 个题材")
    return df.to_dict("records")
