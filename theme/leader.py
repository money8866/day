import time
from datetime import datetime, timedelta

import pandas as pd
import tushare as ts

from config import TS_TOKEN
from db import get_theme_stock_codes, save_leader


def _get_pro():
    return ts.pro_api(TS_TOKEN)


def identify_leaders(trade_date, scored_themes):
    """游资风格：识别每题材龙头/中军/补涨"""
    pro = _get_pro()

    limit_df = None
    try:
        limit_df = pro.limit_list_d(trade_date=trade_date)
    except:
        pass
    limit_stock_boards = {}
    if limit_df is not None and not limit_df.empty:
        for _, r in limit_df.iterrows():
            try:
                lt = r.get("limit_times")
                if lt is not None and not (lt != lt):
                    limit_stock_boards[r["ts_code"]] = int(lt)
                else:
                    limit_stock_boards[r["ts_code"]] = 1
            except (ValueError, TypeError):
                limit_stock_boards[r["ts_code"]] = 1

    mf_df = None
    try:
        mf_df = pro.moneyflow(trade_date=trade_date)
    except:
        pass
    mf_map = {}
    if mf_df is not None and not mf_df.empty:
        for _, r in mf_df.iterrows():
            mf_map[r["ts_code"]] = r

    start_date = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=20)).strftime("%Y%m%d")

    results = []
    for item in scored_themes:
        theme_name = item["theme_name"]
        stocks = get_theme_stock_codes(theme_name)

        stock_scores = []
        daily_cache = {}

        BATCH = 150
        for i in range(0, len(stocks), BATCH):
            batch = stocks[i:i + BATCH]
            try:
                df = pro.daily(ts_code=",".join(batch), start_date=start_date, end_date=trade_date)
                if df is not None and not df.empty:
                    for code, grp in df.groupby("ts_code"):
                        daily_cache[code] = grp.sort_values("trade_date")
            except:
                pass
            time.sleep(0.15)

        for s in stocks:
            try:
                chg_rank_score = 0
                amount_rank_score = 0
                limit_cnt_score = 0
                mf_score = 0
                pct_chg_val = 0

                if s in daily_cache:
                    df = daily_cache[s]
                    if not df.empty and df["trade_date"].iloc[-1] == trade_date:
                        today = df.iloc[-1]
                        pct = today["pct_chg"]
                        amount = today["amount"]
                        pct_chg_val = pct
                        chg_rank_score = (pct + 10) / 20 * 40
                        amount_rank_score = min(amount / 1e8, 30)

                if s in limit_stock_boards:
                    limit_cnt_score = min(limit_stock_boards[s] * 10, 20)

                if s in mf_map:
                    mf = mf_map[s]
                    try:
                        net = float(mf["net_mf_vol"]) if "net_mf_vol" in mf.index else 0
                        if net > 0:
                            mf_score = min(net / 1e6, 10)
                    except:
                        pass

                total_score = chg_rank_score + amount_rank_score + limit_cnt_score + mf_score
                stock_scores.append({"ts_code": s, "score": total_score, "pct_chg": pct_chg_val})
            except:
                continue

        if not stock_scores:
            continue

        stock_scores.sort(key=lambda x: x["score"], reverse=True)

        # rank 1 = 龙头, rank 2 = 中军, rank 3~6 = 弹性补涨
        leader_code = stock_scores[0]["ts_code"] if len(stock_scores) > 0 else ""
        core_code = stock_scores[1]["ts_code"] if len(stock_scores) > 1 else ""
        supp_codes = [stock_scores[i]["ts_code"] for i in range(2, min(6, len(stock_scores)))]

        # 批量查询名称
        code_name_map = {}
        all_codes = [c for c in [leader_code, core_code] + supp_codes if c]
        for code in all_codes:
            try:
                basic = pro.stock_basic(ts_code=code, fields="name")
                if basic is not None and not basic.empty:
                    code_name_map[code] = basic.iloc[0]["name"]
                time.sleep(0.05)
            except:
                pass

        leader_name = code_name_map.get(leader_code, "")
        core_name = code_name_map.get(core_code, "")
        # 弹性补涨：多个名字用逗号连接
        supp_names = [code_name_map.get(c, "") for c in supp_codes]
        supp_names = [n for n in supp_names if n]
        supp_name = "、".join(supp_names)

        save_leader(trade_date, theme_name, leader_name, core_name, supp_name)
        results.append({
            "theme_name": theme_name,
            "leader": leader_name,
            "core": core_name,
            "supplement": supp_name,
            "leader_code": leader_code,
            "core_code": core_code,
            "supp_codes": supp_codes
        })
        time.sleep(0.1)

    return results
