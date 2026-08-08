# -*- coding: utf-8 -*-
"""
回踩买点形态检测器（Pullback Buy Point Detector）V1.0
=====================================================
目标：每日盘后从全量名单中找出"急跌洗盘→缩量止跌→放量首阳→缩量回踩不破"形态的股票，
      这类股票次日有二次启动概率（药康生物 688046 基准案例：7/28-30 急跌 -15% →
      7/31-8/3 缩量止跌 → 8/4 放量首阳 +13.81% → 8/5-8/6 缩量回踩 → 8/7 20cm 涨停）

形态阶段标注：
  - 洗盘缩量 : 近10日有明显回撤且当前缩量横盘（等待首阳）
  - 首阳确认 : 当日放量收大阳（首阳日，次日观察回踩）
  - 回踩中   : 首阳后 1-3 日缩量小阳/小阴且未破首阳实体（最优买点）
  - 回踩完成 : 回踩 2-4 日缩量到位、贴近首阳实体/MA5（次日启动窗口）
  - 延续上涨 : 首阳后继续大涨/放量（主升中继），非回踩，直接剔除不进名单

次日操作决策列（decision，直接回答"次日是否可买入"）：
  - ✅ 次日可买入 : 回踩中 且 PullbackScore≥60（最优买点窗口）
  - ⚠️ 次日观察等回踩 : 首阳确认（次日需缩量回踩不破才算买点）
  - ❌ 仅观察不买入 : 回踩完成（回踩≥3日无次日alpha）
  - ❌ 等待首阳 : 洗盘缩量

评分（PullbackScore 0-100）：
  洗盘深度 20 + 首阳质量 30 + 回踩质量 30 + 结构健康 20

【回测结论·20260807 多日回测 653 样本 × 9 个交易日】
  - "回踩中"(首阳后1-2日)是最优买点窗口：次日均值 +1.33% / 中位 +1.31% /
    上涨率 68%；score≥60 次日 +1.96%(涨>3%占38%)；score≥70 次日 +2.65%(涨>3%占54%、上涨率77%)
  - "回踩完成"(回踩3-4日)无次日 alpha：全部均值 +0.09%，≥60分反而 -0.54%——
    回踩超过2天仍未启动的，资金认可度下降，仅作观察、不建议入场
  - 市场环境主导：20260727 大跌日，形态名单次日整体 -4.98%（弱市日应整体回避）
  - 药康生物基准案例全程命中：08-05 回踩中63.1 → 08-06 62.5 → 08-07 20cm涨停

用法：
  【已合并】本文件已并入 enhanced_timing_bull_all.py（每日由 run_washout_push.bat 自动调度），
           日常不需要单独运行；本 main() 仅保留为形态调试/回测入口。
  python pullback_buy.py --date 20260806 --input report_daily/double_score_full.csv
输出：
  report_daily/pullback_buy_YYYYMMDD.csv（形态匹配股票 + 评分 + V15/TAE 参考列）
  （正式流程的输出为 report_daily/enhanced_timing_bull_all_YYYYMMDD.csv 中的 形态阶段/次日操作/回踩买点分 列）
"""
import argparse
import os
import sys
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# 兼容 data_fetcher 导入路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "multi_factor_picker"))
from data_fetcher import DataFetcher
from multi_factor_picker.main import load_config, get_token

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")
DEFAULT_INPUT = os.path.join(REPORT_DIR, "double_score_full.csv")
DEFAULT_OUT = os.path.join(REPORT_DIR, "pullback_buy_{date}.csv")

# 形态参数
LOOKBACK = 15          # 扫描窗口（近 N 日找首阳）
WASH_MAX_DD = 8.0      # 洗盘：近10日最大回撤阈值 %（至少 -8%）
YANG_MIN_PCT = 3.0     # 首阳：当日涨幅阈值 %
YANG_MIN_VR = 1.5      # 首阳：量能放大倍数阈值（vs 前5日均量）
PULLBACK_MAX_DAY = 4   # 回踩：最多回踩天数
PULLBACK_SHRINK = 0.8  # 回踩：当日量 <= 首阳量 × 该系数才算缩量


def _to_ts_code(code):
    s = str(code).split(".")[0].zfill(6)
    return f"{s}.SH" if s[0] in "69" else f"{s}.SZ"


def analyze_shape(daily: pd.DataFrame):
    """
    单股日线形态分析
    返回 dict 或 None（无形态）
    """
    if daily is None or len(daily) < 20:
        return None
    d = daily.sort_values("trade_date").reset_index(drop=True)
    closes = d["close"].astype(float).values
    vols = d["vol"].astype(float).values
    pcts = d["pct_chg"].astype(float).values
    n = len(d)

    # ── 1. 洗盘：近10日最大回撤 ──
    base10 = closes[-11] if n >= 11 else closes[0]
    dd10 = min((closes[-10:] / base10 - 1) * 100) if base10 > 0 else 0.0
    max_dd = min(dd10, 0.0)

    # ── 2. 放量首阳：扫描近 LOOKBACK 日 ──
    # 首阳前置校验：首阳前13日窗口内必须有洗盘回撤（窗口内高点→低点 ≥8%），
    # 排除上升趋势中的中继大阳/追高大阳被误判为"首阳"（如连涨中继涨停）
    def _wash_before(i):
        lo = max(0, i - 13)
        hi = i - 1  # 首阳前一日截止，洗盘低点最晚出现在首阳前一日
        if hi <= lo:
            return False
        seg = closes[lo:hi + 1]
        seg_high = seg.max()
        if seg_high <= 0:
            return False
        return (seg.min() / seg_high - 1) * 100 <= -WASH_MAX_DD

    first_yang = None
    for i in range(max(1, n - LOOKBACK), n):
        prev5 = np.mean(vols[max(0, i - 5):i])
        if prev5 <= 0:
            continue
        vr = vols[i] / prev5
        if pcts[i] >= YANG_MIN_PCT and vr >= YANG_MIN_VR and d["close"].iloc[i] >= d["open"].iloc[i]:
            if not _wash_before(i):
                continue  # 无洗盘前置 → 非有效首阳，继续往前找
            # 首阳须为"洗盘后第一根大阳"：前一日非大涨（拦截连板中继大阳当首阳）
            if i >= 1 and pcts[i - 1] > 5.0:
                continue
            first_yang = {"idx": i, "date": d["trade_date"].iloc[i], "pct": pcts[i],
                          "vr": vr, "close": closes[i], "open": d["open"].iloc[i],
                          "vol": vols[i]}
            break

    if first_yang is None:
        # 无首阳：若洗盘充分且当前缩量横盘 → "洗盘缩量"阶段（低分观察）
        vol_ratio_5v20 = np.mean(vols[-5:]) / np.mean(vols[-20:]) if np.mean(vols[-20:]) > 0 else 1.0
        if max_dd <= -WASH_MAX_DD and vol_ratio_5v20 <= 0.9 and abs(pcts[-1]) < 3:
            return {
                "stage": "洗盘缩量", "decision": "❌ 等待首阳", "pullback_score": 40.0,
                "first_yang_date": "", "first_yang_pct": 0.0, "first_yang_vr": 0.0,
                "pullback_days": 0, "pullback_shrink": 0.0,
                "max_dd10": round(max_dd, 1), "vol_ratio_5v20": round(vol_ratio_5v20, 2),
            }
        return None

    y = first_yang
    # 首阳距今天数
    days_since_yang = n - 1 - y["idx"]

    # ── 3. 回踩：首阳后逐日检查 ──
    # 首阳实体低点（不破实体一半为健）
    body_low = min(y["open"], y["close"])
    body_mid = (y["open"] + y["close"]) / 2
    pull_days = 0
    pull_shrink = 1.0
    broke = False
    rising = False   # 首阳后延续上涨（大涨/放量）→ 非回踩
    for j in range(y["idx"] + 1, n):
        if d["close"].iloc[j] < body_low * 0.99:  # 跌破首阳实体 = 形态破坏
            broke = True
            break
        # 回踩日要求：不涨停 且 量能≤首阳量1.2倍 且 相对首阳收盘累计涨幅≤5%
        # （缩量小幅整理才叫回踩；持续上涨=趋势延续，放量/涨停=主升加速）
        if pcts[j] >= 9.0 or (y["vol"] > 0 and vols[j] > y["vol"] * 1.2) \
                or (d["close"].iloc[j] / y["close"] - 1) * 100 > 5.0:
            rising = True
            break
        pull_days += 1
        if pull_days == 1:
            pull_shrink = vols[j] / y["vol"] if y["vol"] > 0 else 1.0
        if pull_days > PULLBACK_MAX_DAY:
            break

    # ── 4. 阶段判定 ──
    if days_since_yang == 0:
        stage = "首阳确认"
    elif rising:
        stage = "延续上涨"
    elif not broke and pull_days >= 1 and pull_days <= PULLBACK_MAX_DAY:
        if pull_days <= 2:
            stage = "回踩中"
        else:
            stage = "回踩完成"
    else:
        stage = "首阳后破位"

    # ── 5. 评分 ──
    # 洗盘深度 20：回撤 -8%~-20% 线性（越深越充分，超过 -25% 减分）
    dd_score = 0.0
    if max_dd <= -WASH_MAX_DD:
        dd_score = min(20, 20 * (abs(max_dd) - WASH_MAX_DD) / 12.0) if abs(max_dd) <= 20 else 18.0
    # 首阳质量 30：涨幅分15 + 量比分15
    pct_s = min(15, max(0, (y["pct"] - YANG_MIN_PCT) / 10.0 * 15))
    vr_s = min(15, max(0, (y["vr"] - YANG_MIN_VR) / 2.0 * 15))
    yang_s = pct_s + vr_s
    # 回踩质量 30：缩量 15 + 未破位 15（破位为 0）
    shrink_s = min(15, max(0, (1.0 - pull_shrink) / 0.5 * 15)) if not broke and pull_days >= 1 else 0
    hold_s = 15.0 if not broke else 0.0
    pull_s = shrink_s + hold_s
    # 结构健康 20：收盘站上 MA10（15）+ 距120日高有空间（5）
    ma10 = np.mean(closes[-10:])
    struct_s = 15.0 if closes[-1] >= ma10 else 0.0
    high120 = max(closes[-120:]) if n >= 120 else max(closes)
    headroom = (high120 - closes[-1]) / high120 * 100 if high120 > 0 else 0
    struct_s += 5.0 if headroom > 8 else 0.0

    score = round(dd_score + yang_s + pull_s + struct_s, 1)
    # 破位 / 回踩过长 / 延续上涨（主升中继）→ 不推荐，不进名单
    if broke or pull_days > PULLBACK_MAX_DAY or stage in ("延续上涨", "首阳后破位"):
        return None

    # ── 6. 次日操作决策（回测结论：回踩中1-2日为最优窗口；回踩≥3日无次日alpha）──
    if stage == "回踩中":
        decision = "✅ 次日可买入" if score >= 60 else "⚠️ 观察"
    elif stage == "首阳确认":
        decision = "⚠️ 次日观察等回踩"
    elif stage == "回踩完成":
        decision = "❌ 仅观察不买入"
    else:  # 洗盘缩量
        decision = "❌ 等待首阳"

    return {
        "stage": stage,
        "decision": decision,
        "pullback_score": score,
        "first_yang_date": str(y["date"]),
        "first_yang_pct": round(y["pct"], 1),
        "first_yang_vr": round(y["vr"], 2),
        "pullback_days": pull_days,
        "pullback_shrink": round(pull_shrink, 2),
        "max_dd10": round(max_dd, 1),
        "vol_ratio_5v20": round(np.mean(vols[-5:]) / np.mean(vols[-20:]), 2) if np.mean(vols[-20:]) > 0 else 1.0,
    }


def main():
    parser = argparse.ArgumentParser(description="回踩买点形态检测器")
    parser.add_argument("--date", type=str, default=None, help="交易日 YYYYMMDD（默认最新）")
    parser.add_argument("--input", type=str, default=DEFAULT_INPUT, help="V15 全量名单 CSV")
    parser.add_argument("--limit", type=int, default=0, help="仅扫描前 N 只（调试用）")
    args = parser.parse_args()

    df = pd.read_csv(args.input, encoding="utf-8-sig")
    if "name" not in df.columns:
        df["name"] = df.get("ts_code", "")

    # 交易日
    if args.date:
        trade_date = args.date
    else:
        files = sorted(f for f in os.listdir(REPORT_DIR) if f.startswith("theme_scores_v2_"))
        trade_date = files[-1].replace("theme_scores_v2_", "").replace(".csv", "") if files else None
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")
    print(f"交易日: {trade_date} | 名单 {len(df)} 只")

    # 确定扫描子集（默认全量）
    scan = df.head(args.limit).copy() if args.limit > 0 else df.copy()

    fetcher = DataFetcher(get_token(load_config()), load_config())
    rows = []
    t0 = time.time()
    for i, (_, r) in enumerate(scan.iterrows()):
        code = r.get("code") or r.get("ts_code")
        ts = _to_ts_code(code)
        try:
            daily = fetcher.get_daily_by_code(ts, start_date="20260301", end_date=trade_date)
        except Exception:
            daily = None
        shape = analyze_shape(daily)
        if shape:
            rows.append({
                "code": ts, "name": r.get("name", ""),
                "theme": r.get("theme", ""),
                "FinalScore": r.get("FinalScore", np.nan),
                "Recommendation": r.get("Recommendation", ""),
                **shape,
            })
        if (i + 1) % 50 == 0:
            print(f"  [{i+1}/{len(scan)}] 匹配 {len(rows)} | {time.time()-t0:.0f}s")

    res = pd.DataFrame(rows)
    if len(res) == 0:
        print("未匹配到回踩买点形态")
        return
    # 叠加 TAE 参考列（若存在）
    tae_path = os.path.join(REPORT_DIR, "v15_theme_alpha.csv")
    if os.path.exists(tae_path):
        tae = pd.read_csv(tae_path, encoding="utf-8-sig")
        tae["code"] = tae["code"].astype(str).str.zfill(6)
        res["_c6"] = res["code"].str.split(".").str[0]
        merge_cols = [c for c in ["TradeScore", "MoneyAttack", "LeaderUniqueness", "BuyPointQuality", "Action"] if c in tae.columns]
        if merge_cols:
            tae_m = tae[["code"] + merge_cols].copy()
            tae_m["code"] = tae_m["code"].astype(str)
            res = res.merge(tae_m, left_on="_c6", right_on="code", how="left", suffixes=("", "_tae"))
            res = res.drop(columns=["_c6", "code_tae"] if "code_tae" in res.columns else ["_c6"])

    # 排序：回踩中/回踩完成优先 + 评分降序（回测结论：回踩中1-2日为最优窗口，回踩完成仅观察）
    stage_order = {"回踩中": 0, "回踩完成": 1, "首阳确认": 2, "洗盘缩量": 3}
    res["_stage_rank"] = res["stage"].map(stage_order).fillna(9)
    res = res.sort_values(["_stage_rank", "pullback_score"], ascending=[True, False]).drop(columns=["_stage_rank"])

    # 回测结论标注：回踩完成(回踩≥3日)无次日alpha，仅观察；回踩中高分是最优买点窗口
    def _note(row):
        if row["stage"] == "回踩中":
            if row["pullback_score"] >= 70:
                return "最优买点窗口(回踩中高分)"
            if row["pullback_score"] >= 60:
                return "买点窗口(回踩中)"
            return "回踩中"
        if row["stage"] == "回踩完成":
            return "仅观察(回踩≥3日无次日alpha)"
        return ""
    res["note"] = res.apply(_note, axis=1)

    out = os.path.join(REPORT_DIR, f"pullback_buy_{trade_date}.csv")
    res.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n✅ 形态匹配 {len(res)} 只 → {out}")
    print("\n阶段分布:", res["stage"].value_counts().to_dict())
    show = ["code", "name", "theme", "stage", "decision", "pullback_score", "first_yang_date",
            "first_yang_pct", "first_yang_vr", "pullback_days", "pullback_shrink",
            "max_dd10", "FinalScore", "Recommendation"]
    show = [c for c in show if c in res.columns]
    print(res[show].to_string(index=False))


if __name__ == "__main__":
    main()
