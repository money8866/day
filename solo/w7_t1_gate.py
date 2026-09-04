# -*- coding: utf-8 -*-
"""
已突破股 · T+1 Trade Execution Gate V2.0
对 W7 报告确认突破的股票逐股计算：
  BQS(突破质量分) / TRIG / BUY_ZONE / CONFIRM / INVALID / STOP / TARGET1/2 / ENTRY_MODE / T+1_ACTION
数据源：D:\\mystock\\cache_daily\\stock_data.db (stk_factor_pro)
        + cache_daily/stock_basic.csv (行业映射与行业强度)
输出：report_daily/w7_t1_gate_YYYYMMDD.md
"""
import os
import sys
import csv
import sqlite3
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

CACHE_DIR = os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily")
DB_PATH = os.path.join(CACHE_DIR, "stock_data.db")
BASIC_PATH = os.path.join(CACHE_DIR, "stock_basic.csv")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_daily")

BREAK_DAY = "20260903"   # 突破日（W7 报告基准日）
T1_DAY = "20260904"      # T+1 交易日（本计划适用日）

# ts_code, 名称, TRIG(报告触发价/关键突破位), W7状态
STOCKS = [
    ("688239.SH", "航宇科技", 53.56, "BREAKOUT_CONFIRM"),
    ("605167.SH", "利柏特",  15.79, "BREAKOUT_CONFIRM"),
    ("002787.SZ", "华源控股", 23.11, "SECOND_WAVE"),
    ("601601.SH", "中国太保", 33.54, "BREAKOUT_CONFIRM"),
    ("601318.SH", "中国平安", 57.48, "BREAKOUT_CONFIRM"),
    ("300990.SZ", "同飞股份", 104.00, "BREAKOUT_CONFIRM"),
    ("600611.SH", "大众交通",  5.05, "BREAKOUT_CONFIRM"),
]

ACTION_ENUM = {"BUY_PULLBACK", "BUY_RECLAIM", "BUY_BREAKOUT", "WAIT", "NO_CHASE", "NO_BUY", "EXIT_IF_CLOSE_BELOW_TRIG"}


def clip(v, low=0.0, high=100.0):
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = 0.0
    if v != v:  # nan
        v = 0.0
    return max(low, min(high, v))


def f2(v):
    return round(v + 1e-9, 2)


def load_industry_map(conn, break_day):
    ind = {}
    if os.path.exists(BASIC_PATH):
        with open(BASIC_PATH, "r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                code = (row.get("ts_code") or "").strip()
                name = (row.get("industry") or "").strip()
                if code and name:
                    ind[code] = name
    return ind, ("stock_basic" if ind else "none")


def load_trade_dates(conn, break_day, need=25):
    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM stk_factor_pro WHERE trade_date<=? ORDER BY trade_date DESC LIMIT ?",
        (break_day, need),
    ).fetchall()
    return [str(r[0]) for r in rows][::-1]


def load_ret20_map(conn, dates):
    """近 20 日收益（breakout 相对 21 个交易日前的收盘），用于 RS 与行业强度"""
    if len(dates) < 21:
        return {}
    d0, d1 = dates[0], dates[-1]
    ret = {}
    for code, c in conn.execute("SELECT ts_code, close FROM stk_factor_pro WHERE trade_date=?", (d0,)):
        ret[code] = [float(c)]
    for code, c in conn.execute("SELECT ts_code, close FROM stk_factor_pro WHERE trade_date=?", (d1,)):
        if code not in ret:
            continue
        base = ret[code][0]
        if base and base > 0:
            ret[code] = float(c) / base - 1.0
        else:
            del ret[code]
    return {k: v for k, v in ret.items() if isinstance(v, float)}


def load_bars(conn, ts_code, break_day, limit=60):
    rows = conn.execute(
        "SELECT trade_date, open, high, low, close, pct_chg, vol, turnover_rate,"
        " ma_bfq_10, ma_bfq_20, ma_bfq_60 FROM stk_factor_pro"
        " WHERE ts_code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT ?",
        (ts_code, break_day, limit),
    ).fetchall()
    rows = rows[::-1]
    bars = []
    for r in rows:
        bars.append({
            "date": str(r[0]), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
            "close": float(r[4]), "pct_chg": float(r[5]) if r[5] is not None else 0.0,
            "vol": float(r[6]) if r[6] is not None else 0.0,
            "turnover": float(r[7]) if r[7] is not None else 0.0,
            "ma10": float(r[8]) if r[8] is not None else None,
            "ma20": float(r[9]) if r[9] is not None else None,
            "ma60": float(r[10]) if r[10] is not None else None,
        })
    return bars


def compute_bqs(bar, bars, trig, sector_strength, ret20, market_ret20):
    """BQS 七维评分（满分100）"""
    close, high, low, opn = bar["close"], bar["high"], bar["low"], bar["open"]
    prev_close = bars[-2]["close"] if len(bars) >= 2 else close
    day_pct = bar["pct_chg"] if bar["pct_chg"] else (close / prev_close - 1) * 100
    over_trig = max(0.0, (close / trig - 1) * 100)
    amp = day_pct + over_trig

    # 1) 突破幅度 20
    s_amp = clip(amp * 2.5, 0, 20)

    # 2) 突破成交量质量 20（突破日量 / 前5日均量）
    prev5 = [b["vol"] for b in bars[-6:-1]]
    vol_base = sum(prev5) / len(prev5) if prev5 else 0.0
    vol_ratio = bar["vol"] / vol_base if vol_base > 0 else 0.0
    s_vol = clip((vol_ratio - 0.6) * 13.5, 0, 20)

    # 3) 突破后承接 20（收盘位置 + 上影线）
    rng = high - low
    close_pos = (close - low) / rng if rng > 0 else 0.5
    shadow = (high - max(opn, close)) / close * 100 if close > 0 else 0.0
    s_abs = clip(close_pos * 16 + max(0.0, 4.0 - shadow * 1.5), 0, 20)

    # 4) MA20/MA60 结构 15
    ma20, ma60, ma10 = bar["ma20"], bar["ma60"], bar["ma10"]
    ma20_5 = bars[-6]["ma20"] if len(bars) >= 6 and bars[-6]["ma20"] else None
    ma60_5 = bars[-6]["ma60"] if len(bars) >= 6 and bars[-6]["ma60"] else None
    s_ma, ma_flags = 0.0, []
    if ma20 and ma60 and ma20 > ma60:
        s_ma += 4; ma_flags.append("MA20>MA60")
    if ma20 and close > ma20:
        s_ma += 4; ma_flags.append("收>MA20")
    if ma20 and ma20_5 and ma20 > ma20_5:
        s_ma += 3; ma_flags.append("MA20上行")
    if ma60 and ma60_5 and ma60 >= ma60_5 * 0.999:
        s_ma += 2; ma_flags.append("MA60走平向上")
    if ma20 and (close / ma20 - 1) <= 0.18:
        s_ma += 2; ma_flags.append("乖离可控")
    s_ma = clip(s_ma, 0, 15)

    # 5) 行业/主题强度 10（复用引擎口径：50 + 行业20日收益中位数×150）
    s_ind = clip(sector_strength / 10.0, 0, 10)

    # 6) 相对市场强度 10
    m20 = market_ret20 if market_ret20 is not None else 0.0
    s_rs = clip((50 + (ret20 - m20) * 250) / 10.0, 0, 10)

    # 7) 前期整理质量 5（突破前20日振幅越窄越好）
    win = bars[-21:-1]
    if win:
        hi = max(b["high"] for b in win); lo = min(b["low"] for b in win)
        tight = (hi - lo) / lo if lo > 0 else 1.0
    else:
        tight = 1.0
    s_base = clip((0.32 - tight) / 0.32 * 5, 0, 5)

    bqs = s_amp + s_vol + s_abs + s_ma + s_ind + s_rs + s_base
    return {
        "bqs": bqs, "s_amp": s_amp, "s_vol": s_vol, "s_abs": s_abs, "s_ma": s_ma,
        "s_ind": s_ind, "s_rs": s_rs, "s_base": s_base,
        "amp": amp, "day_pct": day_pct, "over_trig": over_trig,
        "vol_ratio": vol_ratio, "close_pos": close_pos, "shadow": shadow,
        "tight": tight, "ma_flags": ma_flags, "ret20": ret20 * 100, "m20": m20 * 100,
    }


def price_levels(bar, trig):
    close, high = bar["close"], bar["high"]
    buy_lo, buy_hi = trig * 0.98, trig * 1.01
    confirm = high                      # 重新转强确认价 = 突破日最高价
    invalid = trig * 0.985              # 结构失效价
    stop = invalid                      # 实际止损 = 失效价
    risk = max(trig - stop, 0.01)
    t1 = trig + 2 * risk                # R:R=2:1
    t2 = trig + 3 * risk                # R:R=3:1
    return {"buy_lo": f2(buy_lo), "buy_hi": f2(buy_hi), "confirm": f2(confirm),
            "invalid": f2(invalid), "stop": f2(stop), "t1": f2(t1), "t2": f2(t2)}


def decide(bqs, ext, failed):
    """返回 (entry_mode, action, group, reason)"""
    if failed:
        return "NONE", "NO_BUY", "NO_TRADE", "突破失败判定命中（收盘破TRIG 或 长上影+巨量）"
    if bqs < 65:
        return "NONE", "NO_BUY", "NO_TRADE", "BQS<65，不作为次日主动交易标的"
    if ext > 5:
        return "NONE", "NO_CHASE", "NO_TRADE", "乖离>5%（对应高开>5%档），禁止追涨"
    if ext > 3:
        return "PULLBACK", "WAIT", "WATCH", "乖离3~5%：不追第一波，等回踩→缩量→止跌→再上"
    if bqs >= 85:
        return "PULLBACK", "BUY_PULLBACK", "PRIMARY_EXECUTION", "A级突破，回踩确认直接执行"
    if bqs >= 75:
        return "PULLBACK", "BUY_PULLBACK", "SECONDARY_EXECUTION", "B级突破，等待回踩确认（更严格确认）"
    return "PULLBACK", "WAIT", "WATCH", "C级突破，仅观察；回踩确认后另行评估"


def grade_of(bqs):
    if bqs >= 85:
        return "A"
    if bqs >= 75:
        return "B"
    if bqs >= 65:
        return "C"
    return "D"


def main():
    t0 = datetime.now()
    conn = sqlite3.connect(DB_PATH)
    ind_map, ind_src = load_industry_map(conn, BREAK_DAY)
    dates = load_trade_dates(conn, BREAK_DAY)
    ret20_map = load_ret20_map(conn, dates)

    # 市场与行业中位数（与引擎 sector_strength 同口径）
    m_ret = sorted(ret20_map.values())
    market_ret20 = m_ret[len(m_ret) // 2] if m_ret else 0.0
    by_ind = {}
    for code, r in ret20_map.items():
        ind = ind_map.get(code)
        if ind:
            by_ind.setdefault(ind, []).append(r)
    sector_strength = {}
    for ind, vals in by_ind.items():
        vals.sort()
        sector_strength[ind] = clip(50 + vals[len(vals) // 2] * 150)

    results = []
    for ts_code, name, trig, state in STOCKS:
        bars = load_bars(conn, ts_code, BREAK_DAY)
        if len(bars) < 25 or bars[-1]["date"] != BREAK_DAY:
            results.append({"ts_code": ts_code, "name": name, "trig": trig, "state": state,
                            "error": f"K线不足({len(bars)}根)或突破日无数据"})
            continue
        bar = bars[-1]
        close = bar["close"]
        ext = (close / trig - 1) * 100

        # 突破失败判定（突破日静态检查）
        vol_ratio_chk = bar["vol"] / (sum(b["vol"] for b in bars[-6:-1]) / 5) if len(bars) >= 6 else 0
        failed_flags = []
        if close < trig:
            failed_flags.append("收盘<TRIG")
        rng = bar["high"] - bar["low"]
        cp = (close - bar["low"]) / rng if rng > 0 else 0.5
        shadow = (bar["high"] - max(bar["open"], close)) / close * 100 if close > 0 else 0
        if shadow > 2.5 and vol_ratio_chk > 2.5 and cp < 0.6:
            failed_flags.append("长上影+巨量")
        failed = bool(failed_flags)

        ind_name = ind_map.get(ts_code, "未知")
        r20 = ret20_map.get(ts_code)
        if r20 is None:
            r20 = 0.0
        m = compute_bqs(bar, bars, trig, sector_strength.get(ind_name, 50.0), r20, market_ret20)
        lv = price_levels(bar, trig)
        mode, action, group, reason = decide(m["bqs"], ext, failed)

        # 优先级分 = BQS × 回踩质量(乖离惩罚) × R:R × 主线强度
        rr_pull = (lv["t1"] - trig) / max(trig - lv["stop"], 0.01)
        rr_brk = (lv["t2"] - lv["confirm"]) / max(lv["confirm"] - lv["stop"], 0.01)
        pq = clip(1 - ext / 6.0, 0.3, 1.0)
        rr_f = clip(rr_brk / 2.0, 0.25, 1.5)
        ind_f = clip(sector_strength.get(ind_name, 50.0) / 100.0, 0.3, 1.2)
        prio = m["bqs"] * pq * rr_f * ind_f

        results.append({
            "ts_code": ts_code, "name": name, "trig": f2(trig), "state": state,
            "close": close, "open": bar["open"], "high": bar["high"], "low": bar["low"],
            "day_pct": m["day_pct"], "ext": ext, "vol_brk_wan": bar["vol"] / 1e4,
            "vol_ratio": m["vol_ratio"], "close_pos": cp, "shadow": shadow,
            "turnover": bar["turnover"], "ma10": bar["ma10"], "ma20": bar["ma20"], "ma60": bar["ma60"],
            "industry": ind_name, "sector_ss": sector_strength.get(ind_name, 50.0),
            "ret20": m["ret20"], "m20": m["m20"], "tight": m["tight"] * 100,
            "bqs": m["bqs"], "grade": grade_of(m["bqs"]),
            "subs": m, "levels": lv, "mode": mode, "action": action,
            "group": group, "reason": reason, "failed_flags": failed_flags,
            "prio": prio, "rr_pull": rr_pull, "rr_brk": rr_brk,
        })
    conn.close()

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"w7_t1_gate_{T1_DAY}.md")
    write_report(out_path, results, ind_src)

    # 控制台摘要
    print(f"\n=== T+1 Gate {T1_DAY}（突破日 {BREAK_DAY}）行业源: {ind_src} ===")
    print(f"{'股票':<6}{'BQS':>6}{'级':>3}{'TRIG':>8}{'乖离%':>7}{'ENTRY_MODE':>12}{'T+1_ACTION':>14}  优先级")
    for r in results:
        if "error" in r:
            print(f"{r['name']:<6}  数据异常: {r['error']}")
            continue
        print(f"{r['name']:<6}{r['bqs']:>6.1f}{r['grade']:>3}{r['trig']:>8.2f}{r['ext']:>7.2f}"
              f"{r['mode']:>12}{r['action']:>16}  {r['group']}")
    print(f"\n输出: {out_path}")
    print(f"耗时 {datetime.now() - t0}")
    return out_path


def write_report(out_path, results, ind_src):
    ok = [r for r in results if "error" not in r]
    order = {"PRIMARY_EXECUTION": 0, "SECONDARY_EXECUTION": 1, "WATCH": 2, "NO_TRADE": 3}
    ok.sort(key=lambda r: (order[r["group"]], -r["prio"]))
    L = []
    L.append(f"# 已突破股 · T+1 Trade Execution Gate V2.0（{T1_DAY} 盘前执行计划）\n")
    L.append(f"- 突破日：{BREAK_DAY}（W7 报告基准日）｜ T+1：{T1_DAY}")
    L.append(f"- 数据源：stk_factor_pro（OHLC/量能/MA10·20·60）＋ 行业快照 {ind_src}｜ 行业强度口径与 W7 引擎一致（50+行业20日收益中位数×150）")
    L.append(f"- 乖离代理：突破日收盘 vs TRIG（盘前口径）。**开盘后必须用开盘价重分档**：高开≤3%正常等回踩；3~5%不追第一波；>5% NO_CHASE；高开急拉禁止追买。")
    L.append(f"- 量价三档（T+1盘中/收盘复核）：Volume_T+1 < 0.8×突破日量=健康；0.8~1.2×=中性需更强承接；>1.2×且破 TRIG=NO_BUY（突破失败风险）。")
    L.append(f"- BQS 七维：突破幅度20＋量能质量20＋突破后承接20＋MA结构15＋行业强度10＋相对强度10＋整理质量5；分级 A≥85 / B 75-84 / C 65-74 / D<65。\n")

    # 总览表
    L.append("## 一、逐股执行总览\n")
    L.append("| 股票 | BQS(级) | TRIG | BUY_ZONE | CONFIRM | INVALID | STOP | TARGET1 | TARGET2 | ENTRY_MODE | T+1_ACTION | 优先级 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in ok:
        lv = r["levels"]
        L.append(f"| {r['name']} | {r['bqs']:.1f}({r['grade']}) | {r['trig']:.2f} | {lv['buy_lo']:.2f}~{lv['buy_hi']:.2f} "
                 f"| {lv['confirm']:.2f} | {lv['invalid']:.2f} | {lv['stop']:.2f} | {lv['t1']:.2f} | {lv['t2']:.2f} "
                 f"| {r['mode']} | **{r['action']}** | {r['group']} |")
    for r in results:
        if "error" in r:
            L.append(f"| {r['name']} | - | - | - | - | - | - | - | - | - | NO_BUY(数据异常) | NO_TRADE |")
    L.append("")

    # 优先级分组
    L.append("## 二、优先级分组（BQS × 回踩质量 × R:R × 主线强度）\n")
    for g in ["PRIMARY_EXECUTION", "SECONDARY_EXECUTION", "WATCH", "NO_TRADE"]:
        members = [r for r in ok if r["group"] == g]
        if not members:
            continue
        names = "、".join(f"{r['name']}({r['bqs']:.1f}/{r['action']})" for r in members)
        L.append(f"- **{g}**：{names}")
    L.append("")

    # 逐股明细
    L.append("## 三、逐股明细与盘中三分支\n")
    for r in ok:
        lv = r["levels"]
        s = r["subs"]
        L.append(f"### {r['name']}（{r['ts_code']}｜{r['industry']}｜W7状态 {r['state']}）\n")
        L.append(f"- 突破日：开 {r['open']:.2f} / 高 {r['high']:.2f} / 低 {r['low']:.2f} / 收 {r['close']:.2f}"
                 f"（{r['day_pct']:+.2f}%，量 {r['vol_brk_wan']:.0f}万手＝前5日均量 ×{r['vol_ratio']:.2f}，换手 {r['turnover']:.2f}%，"
                 f"收盘位置 {r['close_pos']*100:.0f}%，上影 {r['shadow']:.2f}%）")
        L.append(f"- 乖离：收/TRIG = {r['ext']:+.2f}%（{'≤3%可执行回踩' if r['ext']<=3 else ('3~5%等回踩' if r['ext']<=5 else '>5%禁追')}）；"
                 f"近20日收益 {r['ret20']:+.1f}% vs 市场 {r['m20']:+.1f}%；行业强度 {r['sector_ss']:.0f}/100；突破前20日振幅 {r['tight']:.1f}%")
        L.append(f"- MA 结构：MA10 {r['ma10']:.2f}｜MA20 {r['ma20']:.2f}｜MA60 {r['ma60']:.2f}（{'；'.join(s['ma_flags']) if s['ma_flags'] else '弱'}）")
        L.append(f"- BQS {r['bqs']:.1f}（{r['grade']}级）＝ 幅度 {s['s_amp']:.1f}/20 + 量能 {s['s_vol']:.1f}/20 + 承接 {s['s_abs']:.1f}/20 "
                 f"+ MA {s['s_ma']:.1f}/15 + 行业 {s['s_ind']:.1f}/10 + 相对强度 {s['s_rs']:.1f}/10 + 整理 {s['s_base']:.1f}/5")
        if r["failed_flags"]:
            L.append(f"- **突破失败判定**：{'；'.join(r['failed_flags'])} → BREAKOUT_FAILED")
        L.append(f"- **判定**：{r['reason']}｜优先级分 {r['prio']:.1f}｜R:R：回踩口径 {r['rr_pull']:.1f}:1（TRIG 入场→T1 vs STOP）；突破口径 {r['rr_brk']:.1f}:1（CONFIRM 入场→T2 vs STOP）\n")
        L.append("**盘中执行细则**：")
        L.append(f"1. **BUY_PULLBACK（首选）**：回踩进入 {lv['buy_lo']:.2f}~{lv['buy_hi']:.2f}（TRIG -2%~+1%），要求：不有效跌破 {r['trig']:.2f}；"
                 f"回踩量能 < 0.8×{r['vol_brk_wan']:.0f}万手；止跌承接（不再创日内新低+买盘回流）；随后重新站上 MA10（{r['ma10']:.2f}）或突破回踩K线高点 → 买入。")
        L.append(f"2. **BUY_RECLAIM（小仓试错）**：盘中跌破 TRIG 但幅度 ≤1%（低点 ≥ {f2(r['trig']*0.99):.2f}），无持续放量杀跌且快速收复 {r['trig']:.2f}、"
                 f"收复后出现主动买盘 → 小仓试错；持续弱势无法收复 → RECLAIM=FALSE，禁止买入。")
        rr_ok = r["rr_brk"] >= 2.0
        L.append(f"3. **BUY_BREAKOUT（不回踩时）**：放量再破 {lv['confirm']:.2f}（突破日高点），量能明显增强、乖离（价/TRIG）≤ +6%、无异常巨量冲高，"
                 f"且 R:R ≥ 2:1（按 CONFIRM 入场→T2 vs STOP 预估 {r['rr_brk']:.1f}:1，{'满足' if rr_ok else '不满足→该分支默认关闭，WAIT'}）→ 买入；否则 WAIT 继续等回踩。")
        L.append(f"4. **失效与退出**：放量（>1.2×突破日量）跌破 {r['trig']:.2f} → NO_BUY；**收盘 < {r['trig']:.2f} → BREAKOUT_FAILED，持仓次日 EXIT**"
                 f"（盘中击穿 {lv['invalid']:.2f} 可先减仓保护）。\n")

    L.append("## 四、T+1 收盘复核（次日重跑本门控）\n")
    L.append("1. 收盘价 < TRIG → BREAKOUT_FAILED：次日 NO_BUY；持仓 → EXIT_IF_CLOSE_BELOW_TRIG。")
    L.append("2. Volume_T+1 / Volume_breakout > 1.2 且盘中破 TRIG → 突破失败风险，NO_BUY。")
    L.append("3. 回踩买入成交后：止损 = INVALID；TARGET1 减半仓、TARGET2 清仓或上移保本。")
    L.append("4. 三种模式实际成交后，按 BUY_ZONE/CONFIRM/INVALID 三价管理，不再依赖主观判断。")
    text = "\n".join(L).rstrip() + "\n"
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(text)


if __name__ == "__main__":
    main()
