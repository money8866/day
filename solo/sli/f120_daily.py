# -*- coding: utf-8 -*-
"""F120 Daily — 每日高可靠信号固化程序.

把 F120 引擎评分 + 230,792 样本空间研究(IC/类比/EV)固化为每日流水线:
  [1/5] 行情刷新: 自动补齐 cache/daily_*.parquet 至最近一个已收盘交易日
  [2/5] 引擎:    复用 f120.run() 生产信号(评分/HARD GATE/SETUP/T+1 TRIGGER)
  [3/5] EV 层:   复用 f120_space 样本库 + card_space 历史类比/路径模拟
  [4/5] 门控:    高可靠分级 A/B/剔除(EV>0, 入场溢价上限, setup 质量)
  [5/5] 输出:    output/f120_signal_{T}.csv + .md
  [6/6] 落库:    当日信号写入 stock_pick_db(strategy=sli_f120), 供盘后 T+N 跟踪

用法:
  python sli/f120_daily.py            # 刷新至最近交易日并出信号(幂等)
  python sli/f120_daily.py --force    # 忽略当日已有信号, 强制重算
  python sli/f120_daily.py --date 20260904  # 指定目标市场日(不晚于最近已收盘交易日)

定时任务(工作日 17:30, 数据发布后):
  schtasks /Create /TN "F120_Daily" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 17:30 ^
    /TR "cmd /c cd /d d:\\mystock\\solo && python sli\\f120_daily.py >> sli\\output\\f120_daily.log 2>&1"
"""
from __future__ import annotations

import argparse
import datetime
import glob
import os
import sys

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import f120 as F  # noqa: E402
import f120_space as S  # noqa: E402
from sli.config import CACHE_DIR  # noqa: E402

# ---- 高可靠门控参数(固化自 230,792 样本 / 51 个月度截面的 IC + EV 研究) ----
EV_MIN_A = 0.015       # A级最低 EV(参考: 吉比特 +2.4% 为当日最优)
EV_MIN_B = 0.0         # B级最低 EV(EV<0 一律剔除, 参考: 木林森 -0.3% / 宁波银行 -0.4%)
PREMIUM_MAX_A = 0.05   # A级最大入场溢价(溢价即成本, 参考: 药明康德 +19.3%)
PREMIUM_MAX_B = 0.08   # B级最大入场溢价(参考: 比音勒芬 +9.7% 降级)
BASE_BREAK_EV_MIN = 0.03  # BASE_BREAKOUT 为负超额 setup(-4.4%), 需更高 EV 补偿

GOOD_SETUPS = ("DEEP_PULLBACK", "FIRST_PULLBACK", "BREAKOUT_RETEST")

SAMPLE_DTYPE = {"ts_code": str, "date": str, "setup": str, "stage": str,
                "p_tag": str, "e_tag": str, "f_tag": str}


def refresh_market_data(upto: str | None = None) -> str:
    """补齐日线 parquet 至最近已收盘交易日, 返回市场日 T.

    幂等: 已有 parquet 的日期不重复抓取; 未收盘/空数据不落盘(.cache save 跳过空表).
    """
    from sli.cache import SliCache
    from sli.datasource import DataSource
    from sli.utils import load_token, trade_dates_from_cal

    ds = DataSource(load_token(), SliCache(CACHE_DIR))
    latest = F.latest_mkt_date()
    today = upto or datetime.date.today().strftime("%Y%m%d")
    start = (datetime.datetime.strptime(today, "%Y%m%d")
             - datetime.timedelta(days=30)).strftime("%Y%m%d")
    cal = ds.get_trade_cal(start, today)
    tds = [d for d in trade_dates_from_cal(cal) if d > latest]
    have = {os.path.basename(p)[len("daily_"):-len(".parquet")]
            for p in glob.glob(os.path.join(CACHE_DIR, "daily_????????.parquet"))}
    missing = [d for d in tds if d not in have]
    if missing:
        print(f"[1/5] 行情刷新: {missing[0]}~{missing[-1]} 共{len(missing)}个交易日 ...")
        ds.get_daily_dates(missing)
    else:
        print(f"[1/5] 行情已最新({latest}), 无需刷新")
    return F.latest_mkt_date()


def load_samples() -> pd.DataFrame:
    spath = os.path.join(F.OUT, "f120_space_samples.csv")
    if not os.path.exists(spath):
        raise RuntimeError("缺少空间研究样本库, 请先运行一次: python sli/f120_space.py")
    df = pd.read_csv(spath, dtype=SAMPLE_DTYPE)
    for c in ("setup", "stage", "p_tag", "e_tag", "f_tag"):
        df[c] = df[c].fillna("")
    return df


def grade_card(c: pd.Series) -> str:
    """高可靠分级: A=可执行 / B=试仓观察 / C=剔除."""
    ev, prem = float(c["ev"]), float(c["entry_premium"])
    setup = str(c["setup"] or "")
    if ev < EV_MIN_B or prem > PREMIUM_MAX_B:
        return "C"
    if setup == "BASE_BREAKOUT" and ev < BASE_BREAK_EV_MIN:
        return "B"
    if ev >= EV_MIN_A and prem <= PREMIUM_MAX_A:
        return "A"
    return "B"


def build_signals(res: pd.DataFrame, mk: dict, samples: pd.DataFrame, T: str):
    cards = res[res["verdict"].astype(str).str.startswith(("PRIMARY", "CONDITIONAL"))]
    cards = cards[cards["stage"] == "ready"].reset_index(drop=True)
    print(f"[3/5] EV 层: {len(cards)} 张 ready 卡 × 样本库{len(samples)}×"
          f"{samples['date'].nunique()}截面 ...")
    if cards.empty:
        return pd.DataFrame(columns=["ts_code", "name", "grade", "verdict", "setup", "stage",
                                     "F120", "subsector", "cur", "ideal", "zone_lo", "zone_hi",
                                     "stop", "target", "ceiling", "rr", "trigger", "reason",
                                     "ev", "ev_p10", "ev_p90", "raw_win", "p_target", "p_stop",
                                     "exp_days", "entry_premium", "analog_n", "rule", "pos"])
    cs = S.card_space(cards, samples, samples["ts_code"].unique())

    n_pri = int((res["verdict"] == "PRIMARY BUY").sum())
    cap = mk["cap"]
    pri_pos = min(0.12, max(0.04, cap / max(1, n_pri))) if n_pri else 0.0
    con_pos = pri_pos * 0.5

    rows = []
    for _, c in cs.iterrows():
        r = res[res["ts_code"] == c["ts_code"]].iloc[0]
        grade = grade_card(c)
        base = pri_pos if r["verdict"] == "PRIMARY BUY" else con_pos
        pos = 0.0 if grade == "C" else (base if grade == "A" else round(base * 0.5, 3))
        rows.append({
            "ts_code": c["ts_code"], "name": c["name"], "grade": grade,
            "verdict": c["verdict"], "setup": c["setup"], "stage": r["stage"],
            "F120": c["F120"], "subsector": r.get("subsector"),
            "cur": round(float(r["cur"]), 2), "ideal": round(float(r["ideal"]), 2),
            "zone_lo": round(float(r["zone_lo"]), 2), "zone_hi": round(float(r["zone_hi"]), 2),
            "stop": round(float(r["stop"]), 2), "target": round(float(r["target"]), 2),
            "ceiling": round(float(r["ceiling"]), 2),
            "rr": r["rr"], "trigger": r["trigger"], "reason": r["reason"],
            "ev": c["ev"], "ev_p10": c["ev_p10"], "ev_p90": c["ev_p90"],
            "raw_win": c["raw_win"], "p_target": c["p_target"], "p_stop": c["p_stop"],
            "exp_days": c["exp_days"], "entry_premium": c["entry_premium"],
            "analog_n": c["analog_n"], "rule": c["rule"],
            "pos": pos,
        })
    sig = pd.DataFrame(rows)
    if len(sig):
        sig = sig.sort_values(["grade", "ev"], ascending=[True, False]).reset_index(drop=True)
    return sig


def write_report(sig: pd.DataFrame, mk: dict, samples: pd.DataFrame, T: str):
    gA = sig[sig["grade"] == "A"]
    gB = sig[sig["grade"] == "B"]
    L = []
    A = L.append
    A(f"# 潜龙五维 · 每日高可靠信号（市场日 {T}｜基本面快照 {F.resolve_date()}）\n")
    A(f"> 市场状态：{mk['state']}｜F120最大仓位：{mk['cap'] * 100:.0f}%"
      f"｜样本库：{len(samples)}×{samples['date'].nunique()}截面"
      f"｜门控：EV>0，A级 EV≥{EV_MIN_A:.1%} 且溢价≤{PREMIUM_MAX_A:.0%}，"
      f"BASE_BREAKOUT 需 EV≥{BASE_BREAK_EV_MIN:.0%}\n")

    A("## A级（可执行）\n")
    if gA.empty:
        A("（无——宁缺毋滥）\n")
    else:
        A("| 卡片 | verdict | setup | F120 | 现价 | 入场区 | 止损 | 目标 | EV | EV P10/P90 "
          "| 胜率 | P到目标 | 溢价 | 期望持有 | 建议仓 |")
        A("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for _, r in gA.iterrows():
            A(f"| {r['name']} {r['ts_code']} | {str(r['verdict'])[:11]} | {r['setup']} "
              f"| {r['F120']:.1f} | {r['cur']} | {r['zone_lo']}~{r['zone_hi']} "
              f"| {r['stop']} | {r['target']} | {r['ev']:+.1%} | {r['ev_p10']:+.0%}/{r['ev_p90']:+.0%} "
              f"| {r['raw_win']:.0%} | {r['p_target']:.0%} | {r['entry_premium']:+.1%} "
              f"| {r['exp_days']:.0f}日 | {r['pos'] * 100:.1f}% |")
        A("")

    A("## B级（试仓观察，半仓口径）\n")
    if gB.empty:
        A("（无）\n")
    else:
        A("| 卡片 | verdict | setup | F120 | 现价 | 入场区 | EV | 胜率 | 溢价 | 建议仓 |")
        A("|---|---|---|---|---|---|---|---|---|---|")
        for _, r in gB.iterrows():
            A(f"| {r['name']} {r['ts_code']} | {str(r['verdict'])[:11]} | {r['setup']} "
              f"| {r['F120']:.1f} | {r['cur']} | {r['zone_lo']}~{r['zone_hi']} "
              f"| {r['ev']:+.1%} | {r['raw_win']:.0%} | {r['entry_premium']:+.1%} "
              f"| {r['pos'] * 100:.1f}% |")
        A("")

    gC = sig[sig["grade"] == "C"]
    if len(gC):
        A("## 被门控剔除（EV<0 或溢价过高）\n")
        for _, r in gC.iterrows():
            why = "EV<0" if r["ev"] < EV_MIN_B else f"溢价 {r['entry_premium']:+.1%} 过高"
            A(f"- {r['name']} {r['ts_code']}：EV {r['ev']:+.1%}，{why}")
        A("")

    A("## 门控依据（空间研究固化）\n")
    A("- IC：F +0.055 / E +0.051 为空间主引擎；T/V/shock 负 IC，仅用于入场择时与否决")
    A("- 组：DEEP_PULLBACK 唯一正超额(+0.7%)，BASE_BREAKOUT 最差(-4.4%)；ready 较 wait +2.2%")
    A("- EV：按 stop/target 路径首触模拟；入场溢价是隐藏成本，药明康德 +19.3% 即被溢价门控拦截")
    A("- 已知偏差：止损偏紧使期望持有日 ~25(理论 60+)，EV 口径偏保守\n")

    A("## T+1 执行纪律\n")
    A("- 竞价高开 >3% 一律 WAIT，不追")
    A("- 触发条件见各卡 trigger；入场区外的挂单无效")
    A("- 止损/目标以各卡 stop/target 为准，入场溢价 >5% 放弃当日执行\n")

    txt = "\n".join(L)
    mdp = os.path.join(F.OUT, f"f120_signal_{T}.md")
    with open(mdp, "w", encoding="utf-8") as fh:
        fh.write(txt)
    print(f"[5/5] 报告: {mdp}")
    return mdp


PICK_STRATEGY_ID = "sli_f120"
PICK_STRATEGY_NAME = "潜龙五维 F120 每日高可靠信号"


def sync_pick_db(T: str) -> int:
    """当日信号表落库 stock_pick_db(strategy=sli_f120), 幂等; 失败不阻塞信号输出.

    口径: f120_signal_{T}.csv 整表落库, signal=门控分级(A/B/C), action=引擎裁决
    (PRIMARY BUY / CONDITIONAL BUY); F120/EV/胜率/溢价等策略自有字段进 indicators,
    后续由 `python stock_pick_db.py tracking` 基于 pick_date 回填 T+N 与胜率。
    """
    cpath = os.path.join(F.OUT, f"f120_signal_{T}.csv")
    if not os.path.exists(cpath):
        print(f"[db] 信号表不存在, 跳过落库: {cpath}")
        return 0
    sig = pd.read_csv(cpath, dtype={"ts_code": str})
    if sig.empty:
        print("[db] 当日无信号行, 跳过落库")
        return 0
    try:
        from stock_pick_db import record_picks
    except Exception as exc:
        print(f"[db] stock_pick_db 不可用, 跳过落库: {exc}")
        return 0

    def _v(r, k):
        v = r.get(k)
        return None if v is None or pd.isna(v) else v

    rows = []
    for i, r in sig.iterrows():
        rows.append({
            "ts_code": str(r["ts_code"]),
            "stock_name": str(_v(r, "name") or ""),
            "close": _v(r, "cur"),
            "signal": str(_v(r, "grade") or ""),
            "action": str(_v(r, "verdict") or ""),
            "score": _v(r, "F120"),
            "rank_no": i + 1,
            "industry": str(_v(r, "subsector") or ""),
            "reason": str(_v(r, "reason") or ""),
            "stop_price": _v(r, "stop"),
            "target_price": _v(r, "target"),
            "setup": _v(r, "setup"), "stage": _v(r, "stage"),
            "ideal": _v(r, "ideal"), "zone_lo": _v(r, "zone_lo"), "zone_hi": _v(r, "zone_hi"),
            "ceiling": _v(r, "ceiling"), "rr": _v(r, "rr"), "trigger": _v(r, "trigger"),
            "ev": _v(r, "ev"), "ev_p10": _v(r, "ev_p10"), "ev_p90": _v(r, "ev_p90"),
            "raw_win": _v(r, "raw_win"), "p_target": _v(r, "p_target"), "p_stop": _v(r, "p_stop"),
            "exp_days": _v(r, "exp_days"), "entry_premium": _v(r, "entry_premium"),
            "analog_n": _v(r, "analog_n"), "rule": _v(r, "rule"), "pos": _v(r, "pos"),
        })
    try:
        n = record_picks(PICK_STRATEGY_ID, PICK_STRATEGY_NAME, rows, pick_date=T)
        print(f"[db] stock_pick_db 写入 {n}/{len(rows)} 条 "
              f"(strategy={PICK_STRATEGY_ID} pick_date={T})")
        return n
    except Exception as exc:
        print(f"[db] stock_pick_db 写入失败(不影响信号输出): {exc}")
        return 0


def main():
    ap = argparse.ArgumentParser(description="F120 Daily 每日高可靠信号")
    ap.add_argument("--force", action="store_true", help="忽略当日已有信号, 强制重算")
    ap.add_argument("--date", default=None, help="指定目标市场日 YYYYMMDD(默认最近已收盘交易日)")
    args = ap.parse_args()

    T = refresh_market_data(args.date)
    if args.date and args.date != T:
        print(f"[warn] 指定日期 {args.date} 尚无行情数据, 实际市场日 = {T}")

    cpath = os.path.join(F.OUT, f"f120_signal_{T}.csv")
    mpath = os.path.join(F.OUT, f"f120_signal_{T}.md")
    if not args.force and os.path.exists(cpath) and os.path.exists(mpath):
        print(f"[skip] {T} 信号已存在: {cpath} (--force 可重算)")
        sync_pick_db(T)
        return

    print(f"[2/5] 引擎运行(市场日 {T}) ...")
    res, mk = F.run()

    samples = load_samples()
    sig = build_signals(res, mk, samples, T)

    cpath = os.path.join(F.OUT, f"f120_signal_{T}.csv")
    sig.to_csv(cpath, index=False, encoding="utf-8-sig")
    if len(sig):
        print(f"[4/5] 信号表: {cpath}  A={int((sig['grade'] == 'A').sum())} "
              f"B={int((sig['grade'] == 'B').sum())} C={int((sig['grade'] == 'C').sum())}")
    else:
        print(f"[4/5] 信号表(空): {cpath}")

    write_report(sig, mk, samples, T)
    sync_pick_db(T)
    gA = sig[sig["grade"] == "A"] if len(sig) else sig
    if gA.empty:
        print("今日无 A 级信号(宁缺毋滥)")
    else:
        for _, r in gA.iterrows():
            print(f"  A级: {r['name']} {r['ts_code']} EV={r['ev']:+.1%} "
                  f"溢价={r['entry_premium']:+.1%} 建议仓={r['pos'] * 100:.1f}%")


if __name__ == "__main__":
    main()
