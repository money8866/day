# -*- coding: utf-8 -*-
"""IGE v1.1 一次性漏斗：高IGE行业→加速→高置信→高SIA个股→技术面确认→PRIMARY_BUY。

输入：ige/output/ige_quality_picks_20260901.csv（201 只，SIA分位≥70% 且 IndustryElasticity≥60）
行情：sli/cache/daily_*.parquet（截至 20260904 收盘）
输出：ige/output/ige_primary_buy_20260904.csv
说明：全部使用当时已公开数据；市值/换手口径 = 20260901（daily_basic 最近快照）。
"""
import glob
import os
import re
import sys

import numpy as np
import pandas as pd

BASE = r"d:\mystock\solo"
CACHE = os.path.join(BASE, "sli", "cache")
OUT = os.path.join(BASE, "ige", "output")
IGE_DATE = "20260901"
TECH_DATE = "20260904"
PICKS = os.path.join(OUT, f"ige_quality_picks_{IGE_DATE}.csv")
RESULT = os.path.join(OUT, f"ige_primary_buy_{TECH_DATE}.csv")


def limit_pct(code: str) -> float:
    """涨跌幅限制：双创 20%，主板 10%（不处理北交所/ST，池已剔）。"""
    if code.endswith(".SH") and code.startswith("688"):
        return 20.0
    if code.endswith(".SZ") and (code.startswith("300") or code.startswith("301")):
        return 20.0
    return 10.0


def load_history(codes: set[str], end_date: str, lookback_days: int = 110) -> dict[str, pd.DataFrame]:
    """读取池内个股 [end_date 往前约 lookback 交易日] 的日线，返回 ts_code -> 升序 df。"""
    files = []
    for p in glob.glob(os.path.join(CACHE, "daily_*.parquet")):
        base = os.path.basename(p)
        m = re.fullmatch(r"daily_(\d{8})\.parquet", base)
        if m:
            files.append((m.group(1), p))
    files.sort()
    use = [p for d, p in files if d <= end_date][-lookback_days:]
    cols = ["ts_code", "trade_date", "close", "pre_close", "pct_chg", "vol", "amount", "high", "low"]
    parts = []
    for p in use:
        df = pd.read_parquet(p, columns=cols)
        parts.append(df[df["ts_code"].isin(codes)])
    daily = pd.concat(parts, ignore_index=True)
    daily["ts_code"] = daily["ts_code"].astype(str)
    for c in ("close", "pre_close", "pct_chg", "vol", "amount", "high", "low"):
        daily[c] = pd.to_numeric(daily[c], errors="coerce")
    out = {}
    for code, g in daily.groupby("ts_code"):
        out[code] = g.sort_values("trade_date").reset_index(drop=True)
    return out


def tech_confirm(code: str, df: pd.DataFrame) -> dict:
    """逐股技术确认，输出结构状态位 + 买点层级。"""
    n = len(df)
    close = df["close"]
    if n < 25:
        return {"ok": False, "reason": "历史不足25日"}
    vol = df["vol"].fillna(0.0)
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean() if n >= 60 else pd.Series(np.nan, index=close.index)
    t = n - 1
    px = float(close.iloc[t])
    pct = float(pd.to_numeric(df["pct_chg"], errors="coerce").iloc[t] or np.nan)
    lim = limit_pct(code)
    prev5_high = float(close.iloc[t - 5:t].max()) if t >= 5 else float(close.iloc[:t].max())
    ma5t, ma10t, ma20t, ma60t = (float(ma5.iloc[t]), float(ma10.iloc[t]),
                                 float(ma20.iloc[t]), float(ma60.iloc[t]))
    # 均线 5 日前的值（判断方向，需 t>=5）
    ma20_prev5 = float(ma20.iloc[t - 5]) if t >= 5 else np.nan
    # 量能（不含当日的前5日均量 vs 当日；近3日 vs 再前5日）
    v_prev5 = float(vol.iloc[t - 5:t].mean()) if t >= 5 else np.nan
    vol_ratio = float(vol.iloc[t]) / v_prev5 if v_prev5 and v_prev5 > 0 else np.nan
    shrink = float(vol.iloc[t - 2:t].mean()) / v_prev5 if t >= 2 and v_prev5 else np.nan
    # 近5日最大回撤（现价距近10日最高收盘）
    hi10 = float(close.iloc[max(0, t - 10):t + 1].max())
    dd_from_hi = (px / hi10 - 1.0) * 100.0

    rec = {
        "code": code, "ok": True,
        "close": px, "pct_chg": pct, "lim_pct": lim,
        "ma5": ma5t, "ma10": ma10t, "ma20": ma20t,
        "ma60": (float(ma60t) if np.isfinite(ma60t) else np.nan),
        "ma5_gt_ma10": ma5t > ma10t,
        "ma20_dir_up": (np.isfinite(ma20_prev5) and ma20t > ma20_prev5 * 0.995),
        "px_vs_ma20": (px / ma20t - 1.0) * 100.0 if ma20t else np.nan,
        "vol_ratio": vol_ratio, "shrink": shrink,
        "breakout_5d": px > prev5_high,
        "reclaim_ma10_today": (close.iloc[t - 1] <= ma10.iloc[t - 1]) and (px > ma10t),
        "reclaim_2d": (px > ma10t) and (close.iloc[t - 1] > ma10.iloc[t - 1]),
        "near_ma10": abs(px / ma10t - 1.0) <= 0.015 if ma10t else False,
        "above_ma20": px > ma20t if ma20t else False,
        "below_ma60": np.isfinite(ma60t) and px < ma60t,
        "dd_from_hi": dd_from_hi,
    }
    return rec


def level_of(r: dict) -> tuple[str, str, str]:
    """返回 (层级, 信号名, 动作建议)。层级 PRIMARY_BUY/CONFIRMED_NEXT/WATCH/NO_TRADE。"""
    if not r.get("ok"):
        return "NO_TRADE", "数据不足", "无（历史不足，剔除）"
    px, pct, lim = r["close"], r["pct_chg"], r["lim_pct"]
    hot = (not np.isfinite(pct)) or pct >= lim - 2.5          # 接近涨停/涨停 → 当日不可追
    bear = (not r["above_ma20"]) and (not r["ma20_dir_up"])    # 破位且20日线走弱
    trend_ok = r["ma20_dir_up"] and r["above_ma20"]
    if bear:
        return "NO_TRADE", "趋势破位", "剔除：收盘跌破MA20 且 MA20 走平/向下，等待企稳"
    if hot:
        return "WATCH", "涨幅过热", f"当日 +{pct:.1f}%（近涨停），不追高；回踩 {r['ma20']:.2f} 附近再评估"
    # 中小阳突破：今日收阳 1~6%(主板)/1~8%(双创)，放量 1.1~3.5 倍，突破前5日高点
    small_up = 1.0 <= pct <= (6.0 if lim == 10 else 8.5)
    if (small_up and r["breakout_5d"] and r["ma5_gt_ma10"]
            and r["vol_ratio"] and 1.05 <= r["vol_ratio"] <= 3.5):
        return "PRIMARY_BUY", "放量中小阳突破", (
            f"突破前5日高点，多头排列+量比{r['vol_ratio']:.1f}；"
            f"回踩MA5 {r['ma5']:.2f} 不破低吸，跌破MA10 {r['ma10']:.2f} 放弃")
    # 上升趋势中回踩 MA10/MA20 缩量企稳（含 ma5<ma10 的二波结构，不处罚）
    if (trend_ok and r["dd_from_hi"] <= -3.0 and r["near_ma10"]
            and r["shrink"] is not None and r["shrink"] <= 0.85):
        return "PRIMARY_BUY", "缩量回踩企稳", (
            f"自高点回落{r['dd_from_hi']:.1f}%，回踩MA10缩量(量能{r['shrink']:.2f})；"
            f"不破MA10 {r['ma10']:.2f} 可低吸，破MA20 {r['ma20']:.2f} 放弃")
    # 刚站回 MA10 未足2日 → 等确认
    if trend_ok and r["reclaim_ma10_today"] and not r["reclaim_2d"]:
        return "CONFIRMED_NEXT", "MA10收复待确认", (
            f"今日收复MA10 {r['ma10']:.2f}，需连续站稳2日；明日不破可跟进，缩量回踩更佳")
    if trend_ok and r["reclaim_2d"] and r["shrink"] is not None and r["shrink"] <= 0.85:
        return "CONFIRMED_NEXT", "站稳MA10待放量", (
            f"连续2日站上MA10但未放量；放量过前高 {px:.2f} 或回踩不破再确认")
    if trend_ok:
        return "WATCH", "趋势健康未到点", (
            f"多头但无当日信号（距MA10 {abs(px / r['ma10'] - 1) * 100:.1f}%）；"
            f"等待回踩MA10/MA20缩量或放量突破")
    return "WATCH", "等待结构修复", f"MA20方向未确认，等待站回MA20 {r['ma20']:.2f} 之上"


def main() -> None:
    picks = pd.read_csv(PICKS)
    picks = picks[~picks["name"].astype(str).str.contains("ST", na=False)].copy()
    picks["code"] = picks["code"].astype(str)
    codes = set(picks["code"])
    hist = load_history(codes, TECH_DATE)
    basic = pd.read_parquet(os.path.join(CACHE, f"daily_basic_{IGE_DATE}.parquet"))
    basic = basic[basic["ts_code"].isin(codes)][["ts_code", "total_mv", "turnover_rate", "volume_ratio"]]
    basic = basic.rename(columns={"ts_code": "code"})

    rows = []
    for _, s in picks.iterrows():
        code = s["code"]
        if code not in hist:
            continue
        r = tech_confirm(code, hist[code])
        if not r.get("ok"):
            continue
        lv, sig, act = level_of(r)
        rows.append({
            **s.to_dict(), **r,
            "level": lv, "signal": sig, "action": act,
        })
    res = pd.DataFrame(rows)
    if not res.empty:
        res = res.merge(basic, on="code", how="left")
        res["total_mv_yi"] = pd.to_numeric(res["total_mv"], errors="coerce") / 1e4
    # 排序：层级(PRIMARY_BUY 最前) → igemix 降 → IndustryElasticity 降
    _lv = {"PRIMARY_BUY": 0, "CONFIRMED_NEXT": 1, "WATCH": 2, "NO_TRADE": 3}
    res["_o"] = res["level"].map(_lv)
    res = (res.sort_values(["_o", "ige_mix", "industry_elasticity"],
                           ascending=[True, False, False])
           .drop(columns="_o").reset_index(drop=True))
    res.to_csv(RESULT, index=False, encoding="utf-8-sig")

    cnt = res["level"].value_counts()
    print(f"IGE→技术面确认漏斗　池 {len(picks)} 只 → 有效 {len(res)} 只（行情截至 {TECH_DATE} 收盘）")
    for lv in ("PRIMARY_BUY", "CONFIRMED_NEXT", "WATCH", "NO_TRADE"):
        print(f"  {lv:14s} {int(cnt.get(lv, 0)):3d} 只")
    print("─" * 40)
    cols = ["level", "code", "name", "sw_l3", "ige_mix", "ige_adj", "sia",
            "industry_elasticity", "close", "ma5", "ma10", "ma20",
            "pct_chg", "vol_ratio", "total_mv_yi", "signal"]
    show = res[res["level"].isin(["PRIMARY_BUY", "CONFIRMED_NEXT"])]
    print(show[cols].head(40).to_string(index=False))
    print(f"─ 完整结果：{RESULT}")


if __name__ == "__main__":
    main()
