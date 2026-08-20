"""回测：批量验证「业绩预告 → 正式报告」窗口规律（石药创新 2026-08 案例提炼的 5 条经验）。

经验假设（待验证）：
  ① 预告前大涨的票 → 预告次日不追高（次日/3日冲高回落）
  ② 预告→报告窗口宽幅震荡 → 缩量回踩低吸更优
  ③ 报告前 1 周资金抢跑 → 报告落地日平盘/回落
  ④ 真正主升在报告落地后由二次催化引爆
  ⑤ 需批量验证（本脚本即批量验证）

数据源：2025 中报（period=20250630，完整走完「预告→报告」窗口）。
统计口径（交易日，基于日线 close）：
  pre_ann_10d   预告日前10日累计涨幅（预告前是否已大涨）
  ann_1d        预告日后第1个交易日涨跌幅
  ann_3d        预告日后3日累计
  pre_rep_5d    报告日前5日累计涨幅（抢跑幅度）
  rep_0d        报告日当日涨跌幅（落地日）
  post_rep_3d/5d 报告日后3/5日累计（落地后走势）

输出：控制台分组统计 + backtest_report_window_result.csv（明细）

2026-08-20 首跑结论（2025 中报，n=200）：
  ①预告前大涨次日不追高 ── 不成立，反为强者恒强（大涨组预告后3日 +3.4%）
  ②预告→报告窗口回踩低吸 ── 成立，窗口内回调组落地后3日胜率62% vs 震荡组28%
  ③报告前抢跑、落地平盘 ── 部分成立，落地日整体偏弱(胜率43%)，大幅抢跑组落地日反而强(+3.4%)
  ④落地后主升 ── 不成立，落地后3/5日胜率仅35%/32%，整体回调（二次催化才引爆，非常规）
  ⑤核心启示 ── 报告落地观察期追高期望为负，report_post 阶段 BUY 降级 WATCH 得到数据支持
"""
import csv
import logging
import os
import sys
from datetime import datetime
from statistics import mean, median

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_env_path = r"D:\mystock\config\.env"
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("TUSHARE_TOKEN="):
                os.environ["TUSHARE_TOKEN"] = line.split("=", 1)[1].strip()

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("eld.backtest_report_window")

from eld.config import get_config
from eld.cache import EldCache
from eld.datasource import EldDataSource

PERIOD = "20250630"          # 2025 中报（完整窗口）
MIN_GROWTH = 50.0            # 预增 ≥50%（与主流程口径一致）
MAX_SAMPLE = 200             # 控制样本量，避免 API 限速
DAILY_START = "20250401"
DAILY_END = "20250930"


def ret_at(closes, i, k):
    """close[i] 相对 close[i-k] 的累计涨跌幅；越界返回 None。k>0 表示向后看（未来），k<0 向前看（过去）。"""
    if k > 0:
        if i + k >= len(closes):
            return None
        return closes[i + k] / closes[i] - 1.0
    j = i + k  # k<0
    if j < 0:
        return None
    return closes[i] / closes[j] - 1.0


def stats(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return {
        "n": len(vals),
        "mean": mean(vals) * 100,
        "median": median(vals) * 100,
        "win": sum(1 for v in vals if v > 0) / len(vals) * 100,
    }


def fmt(st):
    if st is None:
        return "  N/A"
    return f"  n={st['n']:<4} 均值{st['mean']:+6.2f}% 中位{st['median']:+6.2f}% 胜率{st['win']:5.1f}%"


def main():
    cfg = get_config()
    cache = EldCache(cfg.cache)
    ds = EldDataSource(cfg.tushare.token, cache)

    # ── 1. 拉 2025 中报预告 ──
    logger.info("拉取 %s 业绩预告...", PERIOD)
    df_fc = ds._call_api("forecast_vip", period=PERIOD,
                         fields="ts_code,ann_date,end_date,type,period,p_change_min,p_change_max,summary")
    if df_fc is None or len(df_fc) == 0:
        print("无预告数据，退出")
        return
    fc = df_fc.to_dict("records")
    logger.info("预告总数: %d", len(fc))

    rows, ann_ok, rep_ok = [], 0, 0
    for i, row in enumerate(fc):
        if len(rows) >= MAX_SAMPLE:
            break
        ts = str(row.get("ts_code", ""))
        if not ts:
            continue
        try:
            pmin = float(row.get("p_change_min") or 0)
            pmax = float(row.get("p_change_max") or 0)
        except (TypeError, ValueError):
            continue
        if pmax < MIN_GROWTH or (pmin + pmax) / 2 < MIN_GROWTH:
            continue
        ann_date = str(row.get("ann_date", "") or "")
        if len(ann_date) != 8:
            continue
        ann_ok += 1

        # 正式报告披露日
        try:
            df_fina = ds._get_df_cached(
                "fina_indicator", f"fina_indicator_{ts}_{PERIOD}",
                ts_code=ts, end_date=PERIOD, fields="ts_code,end_date,ann_date",
            )
        except Exception as exc:
            logger.warning("%s fina 失败: %s", ts, exc)
            continue
        rep_date = ""
        if df_fina is not None and len(df_fina) > 0:
            rep_date = str(df_fina.iloc[0].get("ann_date", "") or "")
        if len(rep_date) != 8:
            continue
        rep_ok += 1

        # 日线
        dd = ds.get_daily_data(ts, DAILY_START, DAILY_END)
        if len(dd) < 30:
            continue
        dates = [b.trade_date for b in dd]
        closes = [b.close for b in dd]
        if ann_date not in dates or rep_date not in dates:
            continue
        ai = dates.index(ann_date)
        ri = dates.index(rep_date)

        rows.append({
            "ts_code": ts,
            "ann_date": ann_date,
            "rep_date": rep_date,
            "pre_ann_10d": ret_at(closes, ai, -10),
            "ann_1d": ret_at(closes, ai, 1),
            "ann_3d": ret_at(closes, ai, 3),
            "pre_rep_5d": ret_at(closes, ri, -5),
            "rep_0d": closes[ri] / closes[ri - 1] - 1.0 if ri >= 1 else None,
            "post_rep_3d": ret_at(closes, ri, 3),
            "post_rep_5d": ret_at(closes, ri, 5),
        })
        if (i + 1) % 50 == 0:
            logger.info("进度 %d/%d 已取 %d", i + 1, len(fc), len(rows))

    print(f"预告样本(增速≥{MIN_GROWTH:.0f}%): {ann_ok}  已披露正式报告: {rep_ok}  完整样本: {len(rows)}")
    if not rows:
        print("无完整样本，退出")
        return

    # ── 2. 全样本统计 ──
    print("")
    print("═" * 30)
    print("全样本窗口规律统计")
    print("═" * 30)
    for k, label in [
        ("pre_ann_10d", "预告前10日累计涨幅"),
        ("ann_1d", "预告次日涨跌"),
        ("ann_3d", "预告后3日累计"),
        ("pre_rep_5d", "报告前5日累计(抢跑)"),
        ("rep_0d", "报告落地日涨跌"),
        ("post_rep_3d", "报告后3日累计"),
        ("post_rep_5d", "报告后5日累计"),
    ]:
        print(f"{label}:{fmt(stats([r[k] for r in rows]))}")

    # ── 3. 分组验证①：预告前涨幅 vs 次日表现 ──
    print("")
    print("═" * 30)
    print("分组① 预告前涨幅 → 预告次日/3日 (验证: 预告前大涨不追高)")
    print("═" * 30)
    for lo, hi, label in [(-999, 0, "预告前10日 <0%"), (0, 10, "0% ~ 10%"), (10, 999, ">10% (已大涨)")]:
        grp = [r for r in rows if r["pre_ann_10d"] is not None and lo <= r["pre_ann_10d"] * 100 < hi]
        print(f"[{label}]  n={len(grp)}")
        print(f"    次日:{fmt(stats([r['ann_1d'] for r in grp]))}")
        print(f"    3日 :{fmt(stats([r['ann_3d'] for r in grp]))}")

    # ── 4. 分组验证③：报告前抢跑 vs 落地日/落地后 ──
    print("")
    print("═" * 30)
    print("分组③ 报告前抢跑幅度 → 落地日/落地后 (验证: 抢跑后落地平盘)")
    print("═" * 30)
    for lo, hi, label in [(-999, 3, "报告前5日 <3% (未抢跑)"), (3, 10, "3% ~ 10% (温和抢跑)"), (10, 999, ">10% (大幅抢跑)")]:
        grp = [r for r in rows if r["pre_rep_5d"] is not None and lo <= r["pre_rep_5d"] * 100 < hi]
        print(f"[{label}]  n={len(grp)}")
        print(f"    落地日:{fmt(stats([r['rep_0d'] for r in grp]))}")
        print(f"    落地后3日:{fmt(stats([r['post_rep_3d'] for r in grp]))}")
        print(f"    落地后5日:{fmt(stats([r['post_rep_5d'] for r in grp]))}")

    # ── 5. 分组②：预告→报告窗口内回撤幅度 vs 落地后表现 ──
    print("")
    print("═" * 30)
    print("分组② 预告→报告窗口回撤 → 落地后表现 (验证: 缩量回踩低吸更优)")
    print("═" * 30)
    for lo, hi, label in [(-999, -5, "报告前5日涨幅 <-5% (窗口内回调)"), (-5, 5, "-5% ~ 5% (震荡)"), (5, 999, ">5% (窗口内强势)")]:
        grp = [r for r in rows if r["pre_rep_5d"] is not None and lo <= r["pre_rep_5d"] * 100 < hi]
        print(f"[{label}]  n={len(grp)}")
        print(f"    落地后3日:{fmt(stats([r['post_rep_3d'] for r in grp]))}")
        print(f"    落地后5日:{fmt(stats([r['post_rep_5d'] for r in grp]))}")

    # ── 保存明细 ──
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_report_window_result.csv")
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["ts_code", "ann_date", "rep_date", "pre_ann_10d", "ann_1d", "ann_3d",
                    "pre_rep_5d", "rep_0d", "post_rep_3d", "post_rep_5d"])
        for r in rows:
            w.writerow([r["ts_code"], r["ann_date"], r["rep_date"]] +
                       [f"{v * 100:.2f}" if v is not None else "" for v in
                        [r["pre_ann_10d"], r["ann_1d"], r["ann_3d"], r["pre_rep_5d"], r["rep_0d"], r["post_rep_3d"], r["post_rep_5d"]]])
    print("")
    print(f"明细已保存: {out}")


if __name__ == "__main__":
    main()
