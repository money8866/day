import time
from datetime import datetime, timedelta

import pandas as pd
import tushare as ts

from config import TS_TOKEN
from db import get_theme_stock_codes, save_leader


def _get_pro():
    return ts.pro_api(TS_TOKEN)


def identify_leaders(trade_date, scored_themes):
    """自动识别每题材的龙头/中军/补涨"""
    pro = _get_pro()

    # 1. 获取今日涨停数据
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

    # 2. 获取股票基础信息（市值）
    basic_df = None
    try:
        basic_df = pro.daily_basic(trade_date=trade_date, fields="ts_code,total_mv")
    except:
        pass
    mv_map = {}
    if basic_df is not None and not basic_df.empty:
        for _, r in basic_df.iterrows():
            mv_map[r["ts_code"]] = r["total_mv"]

    # 3. 获取日线数据
    start_date = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=20)).strftime("%Y%m%d")

    results = []
    for item in scored_themes:
        theme_name = item["theme_name"]
        stocks = get_theme_stock_codes(theme_name)

        # 加载日线数据
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

        # 计算各股票得分
        stock_data = []
        for s in stocks:
            try:
                if s not in daily_cache:
                    continue
                df = daily_cache[s]
                if df.empty or df["trade_date"].iloc[-1] != trade_date:
                    continue
                today = df.iloc[-1]
                pct_chg = today["pct_chg"]
                amount = today["amount"]

                # 连板高度
                lb_height = limit_stock_boards.get(s, 0)

                # 市值
                total_mv = mv_map.get(s, 0)

                # 成交量（今日 vs 昨日）
                volume_growth = 0
                if len(df) >= 2:
                    vol_today = today["vol"]
                    vol_yesterday = df.iloc[-2]["vol"]
                    if vol_yesterday > 0:
                        volume_growth = (vol_today / vol_yesterday - 1) * 100

                # 趋势分（MA5 > MA10）
                trend_score = 0
                if len(df) >= 10:
                    ma5 = df["close"].iloc[-5:].mean()
                    ma10 = df["close"].iloc[-10:].mean()
                    if ma5 > ma10:
                        trend_score = 100
                    else:
                        trend_score = 50

                stock_data.append({
                    "ts_code": s,
                    "pct_chg": pct_chg,
                    "amount": amount,
                    "lb_height": lb_height,
                    "total_mv": total_mv,
                    "volume_growth": volume_growth,
                    "trend_score": trend_score
                })
            except:
                continue

        if not stock_data:
            continue

        # ==========================================
        # 1. 识别龙头
        # ==========================================
        leader_candidates = []
        for sd in stock_data:
            # 市场辨识度：简化为（成交额排名 + 涨幅排名）/ 2
            amount_rank = 0
            pct_rank = 0
            sorted_by_amount = sorted(stock_data, key=lambda x: x["amount"], reverse=True)
            sorted_by_pct = sorted(stock_data, key=lambda x: x["pct_chg"], reverse=True)
            for i, s in enumerate(sorted_by_amount):
                if s["ts_code"] == sd["ts_code"]:
                    amount_rank = (len(sorted_by_amount) - i) / len(sorted_by_amount) * 100
                    break
            for i, s in enumerate(sorted_by_pct):
                if s["ts_code"] == sd["ts_code"]:
                    pct_rank = (len(sorted_by_pct) - i) / len(sorted_by_pct) * 100
                    break
            recognition_score = (amount_rank + pct_rank) / 2

            # 各指标归一化到 0-100
            lb_score = min(sd["lb_height"] * 20, 100)  # 5板以上给100
            pct_score = min(max((sd["pct_chg"] + 10) * 5, 0), 100)
            amount_score = min(sd["amount"] / 1e8 * 3.33, 100)  # 30亿成交额给100

            leader_score = (
                0.40 * lb_score +
                0.30 * pct_score +
                0.20 * amount_score +
                0.10 * recognition_score
            )
            leader_candidates.append({
                "ts_code": sd["ts_code"],
                "score": leader_score,
                "pct_chg": sd["pct_chg"]
            })

        leader_candidates.sort(key=lambda x: x["score"], reverse=True)
        leader = leader_candidates[0] if leader_candidates else None

        # ==========================================
        # 2. 识别中军
        # ==========================================
        core_candidates = []
        for sd in stock_data:
            if sd["total_mv"] < 200:  # 市值 < 200亿，排除
                continue
            amount_score = min(sd["amount"] / 1e8 * 3.33, 100)
            mv_score = min(sd["total_mv"] / 10, 100)  # 1000亿市值给100
            pct_score = min(max((sd["pct_chg"] + 10) * 5, 0), 100)
            core_score = (
                0.50 * amount_score +
                0.30 * mv_score +
                0.20 * pct_score
            )
            core_candidates.append({
                "ts_code": sd["ts_code"],
                "score": core_score
            })

        core_candidates.sort(key=lambda x: x["score"], reverse=True)
        core = core_candidates[0] if core_candidates else None

        # ==========================================
        # 3. 识别补涨
        # ==========================================
        supplement_candidates = []
        leader_pct = leader["pct_chg"] if leader else 100
        for sd in stock_data:
            if sd["pct_chg"] >= leader_pct:  # 涨幅 >= 龙头，排除
                continue
            if sd["lb_height"] >= 2:  # 已连续涨停，排除
                continue

            pct_score = min(max((sd["pct_chg"] + 10) * 5, 0), 100)
            volume_score = min(max(sd["volume_growth"] + 50, 0), 100)  # -50% 到 +50% 归一化
            trend_score = sd["trend_score"]

            # 强度排名：简化为成交额排名
            strength_score = 0
            sorted_by_amount = sorted(stock_data, key=lambda x: x["amount"], reverse=True)
            for i, s in enumerate(sorted_by_amount):
                if s["ts_code"] == sd["ts_code"]:
                    strength_score = (len(sorted_by_amount) - i) / len(sorted_by_amount) * 100
                    break

            supplement_score = (
                0.35 * pct_score +
                0.25 * volume_score +
                0.20 * trend_score +
                0.20 * strength_score
            )
            supplement_candidates.append({
                "ts_code": sd["ts_code"],
                "score": supplement_score
            })

        supplement_candidates.sort(key=lambda x: x["score"], reverse=True)
        supplement = supplement_candidates[:3]  # 取前3名

        # ==========================================
        # 批量查询名称
        # ==========================================
        code_name_map = {}
        all_codes = []
        if leader:
            all_codes.append(leader["ts_code"])
        if core:
            all_codes.append(core["ts_code"])
        for s in supplement:
            all_codes.append(s["ts_code"])

        for code in all_codes:
            try:
                basic = pro.stock_basic(ts_code=code, fields="name")
                if basic is not None and not basic.empty:
                    code_name_map[code] = basic.iloc[0]["name"]
                time.sleep(0.05)
            except:
                pass

        leader_name = code_name_map.get(leader["ts_code"], "") if leader else ""
        core_name = code_name_map.get(core["ts_code"], "") if core else ""
        supp_names = [code_name_map.get(s["ts_code"], "") for s in supplement]
        supp_names = [n for n in supp_names if n]
        supp_name = "、".join(supp_names)

        save_leader(trade_date, theme_name, leader_name, core_name, supp_name)
        results.append({
            "theme_name": theme_name,
            "leader": leader_name,
            "core": core_name,
            "supplement": supp_name,
            "leader_code": leader["ts_code"] if leader else "",
            "core_code": core["ts_code"] if core else "",
            "supp_codes": [s["ts_code"] for s in supplement]
        })
        time.sleep(0.1)

    return results
