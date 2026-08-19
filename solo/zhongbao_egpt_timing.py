# -*- coding: utf-8 -*-
"""
中报猎手 × EGPT 回踩择时
=====================================================
对中报猎手(zhongbao_hunt_*.csv)选出的翻倍潜力股，叠加 EGPT
(Earnings Growth Pullback Timing) 回踩择时算法：

  EGPT 回踩形态（pullback_buy.analyze_shape）：
    洗盘(近10日回撤≥8%) → 放量首阳(涨幅≥3%+量比≥1.5) →
    缩量回踩不破实体(1-2日为最优买点窗口) → 回踩买点分(0-100)
    次日操作：✅次日可买入(回踩中&分≥60) / ⚠️次日观察等回踩(首阳)
              / ⚠️观察(回踩中低分) / ❌仅观察不买入(回踩完成)

  买点确认（EGPT enhanced 规则）：
    买点2(缩量回踩VWAP确认) > 买点1(放量突破VWAP+筹码峰) > 未突破
    ATR动态止损 = 现价 - 2×ATR14

  回测结论(EGPT自带)：回踩中1-2日为最优买点窗口(次日上涨率68%，
  分≥70次日+2.65%)；回踩≥3日无次日alpha，仅观察。
"""
import os
import sys
import argparse

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "multi_factor_picker"))
sys.path.insert(0, os.path.join(BASE_DIR, "multi_factor_picker", ".."))

from data_fetcher import DataFetcher
from enhanced_timing_analysis import _calc_vwap, _calc_atr, _calc_chip_concentration_peak
from pullback_buy import analyze_shape

from multi_factor_picker.main import load_config, get_token
import treasure_hunter as th

REPORT_DIR = os.path.join(BASE_DIR, "report_daily")

STAGE_ORDER = {"回踩中": 0, "回踩完成": 1, "首阳确认": 2, "洗盘缩量": 3,
               "延续上涨": 4, "首阳后破位": 5}
OP_ORDER = {"✅ 次日可买入": 0, "⚠️ 次日观察等回踩": 1, "⚠️ 观察": 2,
            "❌ 仅观察不买入": 3, "❌ 等待首阳": 4}


def find_latest_zhongbao_csv() -> str:
    files = [f for f in os.listdir(REPORT_DIR)
             if f.startswith("zhongbao_hunt_") and f.endswith(".csv")]
    if not files:
        return None
    files.sort(reverse=True)
    return os.path.join(REPORT_DIR, files[0])


def buy_point_type(daily: pd.DataFrame, vwap: float, peak_high: float,
                   peak_low: float, price: float, ma20: float,
                   vols: np.ndarray, closes: np.ndarray) -> tuple:
    """EGPT 买点确认：买点2(回踩VWAP确认) > 买点1(放量突破) > 未突破"""
    vwap_bt = vwap is not None and price > vwap
    chip_bt = peak_low is not None and peak_high is not None and price > peak_high
    confirm = False
    if vwap_bt and chip_bt and ma20:
        above_ma20 = price > ma20
        vol_ratio = float(np.mean(vols[-5:])) / float(np.mean(vols[-20:])) if float(np.mean(vols[-20:])) > 0 else 99
        has_dipped = any(closes[-i] <= vwap * 1.02 for i in range(1, min(11, len(closes) + 1)))
        if above_ma20 and vol_ratio < 1.2 and has_dipped:
            confirm = True
    if vwap_bt and chip_bt and confirm:
        return "买点2(缩量回踩VWAP确认)", True
    if vwap_bt and chip_bt:
        return "买点1(放量突破VWAP+筹码峰)", False
    return "未突破", False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="择时数据截止日 YYYYMMDD（默认最近交易日）")
    ap.add_argument("--csv", default=None, help="中报猎手CSV路径（默认最新）")
    args = ap.parse_args()

    trade_date = args.date or th.get_last_trade_date()
    csv_path = args.csv or find_latest_zhongbao_csv()
    if csv_path is None or not os.path.exists(csv_path):
        print(f"[错误] 未找到中报猎手CSV: {csv_path}")
        return

    print("━" * 70)
    print("  中报猎手 × EGPT 回踩择时")
    print(f"  中报名单: {os.path.basename(csv_path)}")
    print(f"  择时截止: {trade_date}")
    print("━" * 70)

    src = pd.read_csv(csv_path, encoding="utf-8-sig")
    if "ts_code" not in src.columns:
        print("[错误] CSV 缺少 ts_code 列")
        return
    print(f"  中报猎手标的: {len(src)} 只")

    config = load_config()
    fetcher = DataFetcher(get_token(config), config)
    start_date = (pd.Timestamp(trade_date) - pd.Timedelta(days=220)).strftime("%Y%m%d")

    rows = []
    for i, (_, r) in enumerate(src.iterrows()):
        code = str(r["ts_code"]).strip()
        name = str(r.get("name", ""))
        if (i + 1) % 10 == 0 or i == 0:
            print(f"  [{i+1}/{len(src)}] {name}({code})")

        try:
            daily = fetcher.get_daily_by_code(code, start_date=start_date, end_date=trade_date)
        except Exception:
            daily = None
        if daily is None or len(daily) < 30:
            rows.append({"代码": code, "名称": name, "形态阶段": "无形态", "次日操作": "--",
                         "回踩买点分": np.nan, "回踩天数": 0})
            continue

        daily = daily.sort_values("trade_date").reset_index(drop=True)
        closes = daily["close"].astype(float).values
        vols = daily["vol"].astype(float).values
        price = float(closes[-1])
        ma20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else None
        vwap = _calc_vwap(daily, 20)
        atr = _calc_atr(daily, 14)
        peak_low, peak_high, peak_ratio = _calc_chip_concentration_peak(daily, 60)

        shape = analyze_shape(daily) or {}
        bp, confirm = buy_point_type(daily, vwap, peak_high, peak_low, price, ma20, vols, closes)
        dynamic_stop = round(price - 2.0 * atr, 2) if atr and atr > 0 else np.nan

        rows.append({
            "代码": code,
            "名称": name,
            "形态阶段": shape.get("stage", "无形态"),
            "次日操作": shape.get("decision", "--"),
            "回踩买点分": shape.get("pullback_score", np.nan),
            "首阳日期": shape.get("first_yang_date", ""),
            "首阳涨幅%": shape.get("first_yang_pct", ""),
            "首阳量比": shape.get("first_yang_vr", ""),
            "回踩天数": shape.get("pullback_days", 0),
            "回踩缩量比": shape.get("pullback_shrink", ""),
            "近10日最大回撤%": shape.get("max_dd10", ""),
            "买点确认": bp,
            "回踩确认": "✅ 是" if confirm else "否",
            "现价": round(price, 2),
            "VWAP": round(vwap, 2) if vwap else np.nan,
            "MA20": round(ma20, 2) if ma20 else np.nan,
            "筹码峰顶": round(peak_high, 2) if peak_high else np.nan,
            "ATR动态止损价": dynamic_stop,
        })

    out = pd.DataFrame(rows)
    # 合并中报业绩
    merge_cols = [c for c in ["营收增速", "净利增速", "扣非增速", "翻倍潜力分", "市值(亿)", "来源"] if c in src.columns]
    if merge_cols:
        out = out.merge(src[["ts_code"] + merge_cols].rename(columns={"ts_code": "代码"}),
                        on="代码", how="left")
        out["翻倍潜力分"] = pd.to_numeric(out.get("翻倍潜力分"), errors="coerce")
        out["回踩买点分"] = pd.to_numeric(out.get("回踩买点分"), errors="coerce")

    # 排序：次日可买入 → 观察等回踩 → 观察 → 等待首阳 → 无形态；组内按回踩买点分
    out["_op"] = out["次日操作"].map(OP_ORDER).fillna(9)
    out["_st"] = out["形态阶段"].map(STAGE_ORDER).fillna(9)
    out = out.sort_values(["_op", "_st", "回踩买点分"], ascending=[True, True, False], na_position="last")
    out = out.drop(columns=["_op", "_st"]).reset_index(drop=True)

    csv_out = os.path.join(REPORT_DIR, f"zhongbao_egpt_timing_{trade_date}.csv")
    out.to_csv(csv_out, index=False, encoding="utf-8-sig")
    print(f"\n完整CSV已保存: {csv_out}")

    def _fmt(v, fmt="{:.1f}"):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return "--"
        if isinstance(v, str) and v == "":
            return "--"
        try:
            return fmt.format(float(v))
        except Exception:
            return str(v)

    def _show(sub, title):
        if len(sub) == 0:
            print(f"\n{title}: 无")
            return
        print("\n" + "━" * 70)
        print(f"  {title} ({len(sub)} 只)")
        print("━" * 70)
        for _, r in sub.iterrows():
            pb = _fmt(r.get("回踩买点分"))
            pot = _fmt(r.get("翻倍潜力分"))
            npg = r.get("净利增速")
            npg_s = f"净利{npg:+.0f}%" if isinstance(npg, (int, float)) and not (isinstance(npg, float) and np.isnan(npg)) else ""
            py = r.get("首阳日期", "")
            pd_ = r.get("回踩天数", 0)
            sl = _fmt(r.get("ATR动态止损价"), "{:.2f}")
            print(f"  {r['名称']}({r['代码']}) {npg_s} | 潜力{pot} 回踩{pb} | 阶段{r['形态阶段']} "
                  f"回踩{pd_}日 首阳{py} 止损{sl}")
            print(f"     买点:{r['买点确认']} 回踩确认:{r['回踩确认']} 现价{_fmt(r.get('现价'), '{:.2f}')} "
                  f"VWAP{_fmt(r.get('VWAP'), '{:.2f}')} MA20{_fmt(r.get('MA20'), '{:.2f}')} "
                  f"筹码峰{_fmt(r.get('筹码峰顶'), '{:.2f}')}")

    buy = out[out["次日操作"] == "✅ 次日可买入"]
    watch = out[out["次日操作"].isin(["⚠️ 次日观察等回踩", "⚠️ 观察"])]
    rest = out[~out["次日操作"].isin(["✅ 次日可买入", "⚠️ 次日观察等回踩", "⚠️ 观察"])]

    _show(buy, "✅ 次日可买入（回踩中×分≥60，最优买点窗口）")
    _show(watch, "⚠️ 观察 / 等回踩（形态未确认，需触发）")
    _show(rest, "❌ 不买入 / 无形态")

    print("\n" + "─" * 70)
    print("  提示：回踩中1-2日为最优窗口(次日上涨率68%)；回踩≥3日无alpha仅观察；")
    print("  止损=现价-2×ATR；买点2(回踩VWAP确认)优于买点1(放量突破)。")


if __name__ == "__main__":
    main()
