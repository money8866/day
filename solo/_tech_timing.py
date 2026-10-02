# -*- coding: utf-8 -*-
"""
IGE v1.2 核心个股技术面择时快照 v3（v3 分类口径：破MA20幅度+贴近10日低点+5日动量）
用法：
    python -X utf8 _tech_timing.py                     # 目标日=缓存最新交易日
    python -X utf8 _tech_timing.py --date 20260904     # 指定目标交易日
候选池：ige/output 下最新 ige_full_<snap>.csv 中 t120_rocket_core 股票
  （优先取日期 ≤ 目标日的快照；快照即基本面分层池，行情窗口只影响技术指标）
行情源：本地 SQLite stock_data.db（daily_cache 日线 + daily_basic_cache 量比/换手）
输出：ige/output/ige_v12_tech_timing_<target>.csv
"""
import argparse
import glob
import os
import sqlite3
import numpy as np
import pandas as pd

CACHE = r"D:\mystock\cache_daily\stock_data.db"
OUTDIR = r"d:\mystock\solo\ige\output"

ap = argparse.ArgumentParser()
ap.add_argument("--date", default="", help="目标交易日 YYYYMMDD；默认取缓存最新交易日")
_ap, _ = ap.parse_known_args()

_con0 = sqlite3.connect(CACHE)
_db_max = str(_con0.execute("select max(trade_date) from daily_cache").fetchone()[0])
_con0.close()
TARGET = str(_ap.date or _db_max).strip()
if len(TARGET) != 8 or not TARGET.isdigit() or TARGET > _db_max:
    TARGET = _db_max          # 目标日行情未到位 → 用缓存最近一个交易日

# 候选池快照：最新 ige_full_*.csv（优先 ≤ TARGET）
_snaps = sorted(glob.glob(os.path.join(OUTDIR, "ige_full_*.csv")))
if not _snaps:
    raise SystemExit("缺少 ige/output/ige_full_*.csv 快照，先跑 ige.main 生成")
_cand = [p for p in _snaps if os.path.basename(p)[9:17] <= TARGET] or _snaps
SNAP = _cand[-1]
OUT = os.path.join(OUTDIR, f"ige_v12_tech_timing_{TARGET}.csv")

con = sqlite3.connect(CACHE)
full = pd.read_csv(SNAP, low_memory=False)
pool = full[full["t120_rocket_core"] == True][
    ["code", "name", "sw_l3", "ige_opportunity_type", "rank_tier"]].copy()

codes = tuple(pool["code"].tolist())
pl = ",".join("?" * len(codes))

d = pd.read_sql(
    f"select ts_code, trade_date, open, high, low, close, pre_close, "
    f"pct_chg, vol, amount from daily_cache "
    f"where ts_code in ({pl}) and trade_date>='20251201' "
    f"order by ts_code, trade_date", con, params=list(codes))
f = pd.read_sql(
    f"select ts_code, trade_date, volume_ratio, turnover_rate "
    f"from daily_basic_cache where ts_code in ({pl}) "
    f"order by ts_code, trade_date", con, params=list(codes))
con.close()

f["trade_date"] = f["trade_date"].astype(str)
d["trade_date"] = d["trade_date"].astype(str)
last = TARGET
fx = f[f["trade_date"] == last][["ts_code", "volume_ratio", "turnover_rate"]]

rows = []
for code, g0 in d.groupby("ts_code"):
    g = g0.sort_values("trade_date").reset_index(drop=True)
    c = g["close"]
    ma5, ma10 = c.rolling(5).mean(), c.rolling(10).mean()
    ma20, ma60 = c.rolling(20).mean(), c.rolling(60).mean()
    ma20p = ma20.shift(5)
    h60 = g["high"].rolling(60).max()
    vol5 = g["vol"].rolling(5).mean()
    vol20 = g["vol"].rolling(20).mean()
    # 短波段支撑/压力: 近5日最低 vs 再前5日最低 → 低点是否抬高
    low5 = g["low"].rolling(5).min()
    low5_prev = low5.shift(5)
    # 近10日与近20日箱体
    lo10 = g["low"].rolling(10).min()
    hi10 = g["high"].rolling(10).max()
    last_i = g.index[-1]

    def v(s, i):
        return float(s.iloc[i]) if not np.isnan(s.iloc[i]) else np.nan

    close = float(c.iloc[last_i])
    if np.isnan(close):
        continue
    x = {"code": code,
         "close": close,
         "pct_chg": float(g["pct_chg"].iloc[last_i]),
         "ma5": v(ma5, last_i), "ma10": v(ma10, last_i),
         "ma20": v(ma20, last_i), "ma60": v(ma60, last_i),
         "ma20_slope5": (v(ma20, last_i) / v(ma20, last_i - 5) - 1) * 100
         if last_i >= 5 else np.nan,
         "h60": v(h60, last_i),
         "dd_h60": (close / v(h60, last_i) - 1) * 100,
         "lo10": v(lo10, last_i), "hi10": v(hi10, last_i),
         "low_hi": bool(v(low5, last_i) > v(low5_prev, last_i)),
         "vol5_20": v(vol5, last_i) / v(vol20, last_i)
         if v(vol20, last_i) else np.nan,
         "gain5": (close / float(c.iloc[last_i - 5]) - 1) * 100
         if last_i >= 5 else np.nan}
    # 额外派生（分类用）
    x["up20"] = (close / x["ma20"] - 1) * 100
    x["up60"] = (close / x["ma60"] - 1) * 100
    x["near_lo"] = close <= x["lo10"] * 1.02
    r = fx[fx["ts_code"] == code]
    x["volume_ratio"] = float(r["volume_ratio"].iloc[0]) if len(r) else np.nan
    x["turnover"] = float(r["turnover_rate"].iloc[0]) if len(r) else np.nan
    rows.append(x)

tech = pd.DataFrame(rows)

# ── v3 盘面状态判定（风险主线：破MA20幅度 + 是否贴10日低点 + 5日动量）──
def classify(r):
    c, ma5, ma10, ma20, ma60 = r["close"], r["ma5"], r["ma10"], r["ma20"], r["ma60"]
    s20, dd = r["ma20_slope5"], r["dd_h60"]
    up20, up60 = r["up20"], r["up60"]
    vr5, pct, g5 = r["vol5_20"], r["pct_chg"], r["gain5"]
    low_hi, lo10, hi10, near_lo = r["low_hi"], r["lo10"], r["hi10"], r["near_lo"]
    if any(np.isnan(z) for z in (c, ma20, ma60, up20, up60)):
        return "数据不足", "复核K线"

    # 0) 5日崩跌（气泡破裂型）
    if g5 <= -14:
        return "破位-急跌", f"规避/反弹减；等放量止跌并收复MA10({ma10:.1f})再谈"
    # 1) 明显跌破 MA60 → 中期结构受损，只有"等右侧"玩法
    if up60 <= -3:
        # 1a 深度跌破MA20且贴近10日低点 / 或破MA20超8% → 下跌途中
        if (up20 <= -4 and (near_lo or pct <= -2)) or up20 <= -8:
            return "下跌中-勿接刀", (f"规避；2日不创新低并收回MA10({ma10:.1f})、"
                                     f"站上MA20({ma20:.1f})再看")
        # 1b 跌破MA20但不深、未创新低 → 下行整理
        if up20 <= -4:
            return "MA60下-下行整理", f"偏弱；等止跌(2日不创新低)+收复MA20({ma20:.1f})"
        # 1c 价在MA20上方或贴近MA20 → 箱体/反弹（区间玩法）
        warn = "（今日大阴线，先观察1-2日）" if pct <= -6 else ""
        return ("MA60下-箱体/反弹",
                f"参考低吸区近10日低点{lo10:.1f}；放量突破箱顶{hi10:.1f}"
                f"或收复MA60({ma60:.1f})才转右侧{warn}")
    # 2) 贴着 MA60（-3~0%）：MA60 得失决定短期方向
    if up60 <= 0:
        if pct <= -5 or (up20 <= -3 and (near_lo or pct <= -3)):
            return "贴MA60-防破位", (f"今日大阴/贴近MA20下沿，谨防跌穿MA60({ma60:.1f})；"
                                     f"反抽MA20({ma20:.1f})不过先减")
        if up20 < -1:
            return "贴MA60-弱势企稳", f"等重新放量站上MA60({ma60:.1f})再看；跌破则离场"
        return "贴MA60-待突破", (f"价在MA20({ma20:.1f})上沿，放量收复MA60({ma60:.1f})转右侧；"
                                 f"缩量回踩MA20不破可低吸")
    # 3) MA60 上方
    if up20 <= -6:
        # 3a 深跌破MA20（>6%），量价弱 → 调整中
        if near_lo or g5 <= -6:
            return "破MA20-调整中", (f"等止跌企稳；反弹MA20({ma20:.1f})不过则减，"
                                     f"低吸参考MA60({ma60:.1f})附近")
        return "回踩近MA60", f"关注MA60({ma60:.1f})支撑企稳；收复MA20({ma20:.1f})转多"
    # 3b MA20下方温和回踩（-6~0%）：价在 MA20~MA60 之间，区域为低吸带
    if up20 < 0:
        if vr5 < 1.05 and low_hi:
            return ("缩量回踩低吸区",
                    f"低吸带MA60({ma60:.1f})~MA20({ma20:.1f})；缩量企稳分批，收盘破MA60离场")
        return "回调企稳观察", f"等缩量止跌/站回MA10({ma10:.1f})；支撑MA60({ma60:.1f})"
    # 4) 站稳 MA20 上方（强势侧）
    if up20 > 15:
        return "强势-乖离过大", f"不追高；等回踩MA10({ma10:.1f})/MA20({ma20:.1f})分批，破MA10减"
    if dd > -8 and pct >= -4:
        return "主升/强势", "持有；回踩MA5/MA10分批低吸，不追阳"
    if pct <= -5:
        return ("冲高回踩中",
                f"今日大阴回踩；2日不破MA20({ma20:.1f})且缩量再低吸，收盘破MA20离场")
    if s20 > 0.2 or pct > 0:
        return "强势整理", f"回踩低吸参考MA10({ma10:.1f})；收盘破MA20({ma20:.1f})离场"
    return "冲高回落", f"等2日企稳或回踩MA20({ma20:.1f})不破再低吸"

res = tech.apply(classify, axis=1, result_type="expand")
tech["phase"] = res[0]
tech["action"] = res[1]

pool = pool.merge(tech, left_on="code", right_on="code", how="left")
pool = pool.sort_values(["rank_tier", "code"]).reset_index(drop=True)
order = ["code", "name", "sw_l3", "ige_opportunity_type", "rank_tier",
         "close", "pct_chg", "ma5", "ma10", "ma20", "ma60", "ma20_slope5",
         "up_ma20", "h60", "dd_h60", "vol5_20", "volume_ratio", "turnover",
         "gain5", "low_hi", "lo10", "hi10", "phase", "action"]
pool["up_ma20"] = (pool["close"] / pool["ma20"] - 1) * 100
pool[order].to_csv(OUT, index=False, encoding="utf-8-sig")

pd.set_option("display.unicode.east_asian_width", True)
pd.set_option("display.max_rows", None)
pd.set_option("display.width", 220)
print(f"== IGE 技术面择时 ==  快照池: {os.path.basename(SNAP)}   目标日: {TARGET}")
print(f"输出: {OUT}")
print("== 分类统计 ==")
print(pool["phase"].value_counts().to_string())
print()
sh = pool[["code", "name", "phase", "close", "pct_chg", "ma5", "ma10",
           "ma20", "ma60", "dd_h60", "vol5_20", "gain5"]].copy()
for col in ["close", "ma5", "ma10", "ma20", "ma60"]:
    sh[col] = sh[col].round(1)
sh["pct_chg"] = sh["pct_chg"].round(1)
sh["dd_h60"] = sh["dd_h60"].round(1)
sh["vol5_20"] = sh["vol5_20"].round(2)
sh["gain5"] = sh["gain5"].round(1)
print(sh.to_string(index=False))
