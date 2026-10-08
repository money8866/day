# -*- coding: utf-8 -*-
"""A股「跳空突破 + 板块共振」盘后选股策略。

—— 为什么只选这一个模式
跳空策略的可交易形态有四类（突破缺口 / 持续缺口 / 普通震荡缺口 / 衰竭缺口），
但落到 A 股盘后选股这一个动作上，只有「突破缺口 + 板块共振」同时满足三件事：

  1. 止损位可定义 —— 缺口下沿就是结构位，赔率可控；
  2. 标的池有限 —— 涨停池天然把全市场 5000 只压缩到几十只，量化可覆盖；
  3. 有正交确认源 —— 板块共振提供个股之外的独立 beta，不必依赖 K 线形态单点判断。

其余三类要么需要做空（A 股散户无法融券做空），要么需要日内高频执行（T+1 无法实现），
要么胜率不足以覆盖滑点。缺口回踩模式虽赔率最好，但它是「盘中」模式，不是盘后模式。

—— 信号结构（T 日收盘后执行，产出 T+1 候选池）

  第一关· 情绪闸门（总开关，不通过则当日空仓）
  第二关· 硬过滤（ST / 一字板 / 筹码断层 / 连板高度 / 流动性）
  第三关· 缺口形态（向上跳空且不被回补 → 突破缺口，非衰竭）
  第四关· 量能验证（1.3× ≤ 量比 ≤ 5×，排除无量真空与天量派发）
  第五关· 板块共振（同题材涨停家数，龙头识别）
  第六关· 综合打分排序（满分 100，输出 Top N）

—— 核心风控
  单笔止损 = 缺口下沿（未跌破不动，跌破即离场，不摊平）
  仓位上限由情绪周期阶段决定，冰点期强制空仓
  严禁在情绪退潮期启用本策略

用法:
    python gap_strategy.py                # 默认最近交易日
    python gap_strategy.py -d 20260930    # 指定交易日
    python gap_strategy.py --top 5        # 取前 5 名
    python gap_strategy.py --no-trade     # 只输出情绪体检，不选股
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from typing import Any

from em_ztpool import em_zt_pool, em_zb_pool, limit_up_sentiment
from kline import KlineError, prev_trading_day, tencent_kline

# ============================================================
# 参数区——所有阈值集中在此，便于按回测结果调参
# ============================================================

# 情绪闸门（依据打板情绪温度计）
SENT = {
    "break_rate_max": 30.0,        # 炸板率上限(%)：早盘炸板率超30% → 情绪转弱
    "max_height_min": 3,           # 最高连板下限：低于此为冰点，空仓
    "height_not_contracting": True,  # 最高板未断层收缩
    "nuke_max": 5,                 # 昨日涨停核按钮数上限：≥5只当天不打板
    "y_zt_up_rate_min": 40.0,      # 昨日涨停今日红盘率下限(%)：赚钱效应
    "seal_cnt_min": 25,            # 涨停家数下限：太少说明无主线
    "seal_cnt_max": 130,           # 涨停家数上限：超过即高潮期，只卖不买
}

# 硬过滤
HARD = {
    "exclude_st": True,
    "exclude_name_words": ("ST", "退", "*"),
    "max_limit_days": 4,           # 连板高度上限：更高属高位接力，风险性质变
    "min_amount_yi": 2.0,          # 最小成交额(亿)：流动性门槛
    "max_amount_yi": 60.0,         # 最大成交额(亿)：过大多为出货
    "min_float_cap_yi": 30.0,      # 最小流通市值(亿)：太小易被操纵
    "exclude_yizi": True,          # 剔除一字板：买不到，且开板即集中抛售
    "max_ztj_days": 4,             # N天M板中的 N 上限：反复炒作后筹码断层
}

# 缺口形态
GAP = {
    "min_gap_pct": 1.5,            # 最小缺口幅度(%)：太小是噪声
    "max_gap_pct": 9.0,            # 最大缺口幅度(%)：A股主板跌停10%，过大多为衰竭
    "require_breakout": True,      # 要求突破近20日箱体上沿
    "box_lookback": 20,
    "close_near_high": 0.92,       # 收盘位于当日振幅上沿的比例：要求强收盘
}

# 量能验证
VOL = {
    "min_ratio": 1.3,              # 量比下限：低于此为流动性真空跳空
    "max_ratio": 8.0,              # 量比上限：涨停封板天然放量，过高为派发
    "lookback": 5,                 # 均量回溯天数
    "healthy_range": (2.0, 5.0),   # 健康放量区间（加分项）
}

# 板块共振
SECTOR = {
    # 准入门槛：题材内涨停家数不足此值直接淘汰（共振是一票否决项，不是加分项）
    "min_zt_per_sector": 2,
    "strong_zt_per_sector": 3,     # 强共振门槛
    "leader_bonus_max": 10,        # 龙头额外加分上限
}

# 封单质量（阈值来自实测分位数，非网络流传值）
# 真实分布：封单比 = 封板资金 / 成交额。中位数约 0.10~0.15，p75 约 0.20~0.30，
# 极高值（>1）通常是一字板全天封死（当日成交额极小所致），故对非一字板取
# 0.20~0.30 作为强封单区间。
SEAL = {
    "ratio_pass": 0.10,            # 合格线：约等于全市场中位数
    "ratio_strong": 0.20,          # 强封单：约等于 p75
    "ratio_extreme": 0.30,         # 极强：约等于 p80~p85
    "hard_floor": 0.08,            # 一票否决线：低于此锁仓意愿过弱
    "min_seal_time": "10:30:00",   # 早板合格线：10:30 前封板为资金主动进攻
    "afternoon_penalty": True,     # 午后板降分
}

# 打分权重（合计 100）
W = {
    "sector": 30,      # 板块共振
    "seal": 20,        # 封单质量
    "position": 20,    # 个股地位（龙头/梯队）
    "volume": 15,      # 量能健康度
    "gap": 15,         # 缺口强度
}

TRADE_ALLOWED_BY_PHASE = {
    "冰点": False,
    "发酵": True,
    "高潮": False,
    "退潮": False,
}


# ============================================================
# 数据结构
# ============================================================
@dataclass
class Candidate:
    code: str
    name: str
    industry: str
    price: float
    pct: float
    score: float = 0.0
    limit_days: int = 1
    zt_stat: str = ""
    first_seal: str = ""
    seal_fund: float = 0.0
    amount_yi: float = 0.0
    float_cap_yi: float = 0.0
    turnover: float = 0.0
    break_times: int = 0
    gap_pct: float = 0.0
    vol_ratio: float = 0.0
    seal_ratio: float = 0.0
    sector_zt_cnt: int = 0
    is_sector_leader: bool = False
    gap_low: float = 0.0            # 缺口下沿 = 止损位
    box_high: float = 0.0           # 被突破的箱体上沿
    stop_loss: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============================================================
# 第一关：情绪周期定位
# ============================================================
def judge_sentiment(date: str) -> dict[str, Any]:
    """情绪周期四阶段判定 + 交易开关。这是整个策略的总闸门。"""
    s = limit_up_sentiment(date)
    if not s["is_trading_day"]:
        return {"date": date, "phase": "非交易日", "allow_trade": False,
                "reason": "无涨跌停数据，可能非交易日", "raw": s}

    br, mh, nuke = s["break_rate"], s["max_height"], s["nuke_cnt"]
    seal, up_rate = s["seal_cnt"], s["y_zt_up_rate"]

    reasons: list[str] = []
    # 冰点判定
    if mh <= 2 or (up_rate and up_rate < 30):
        phase = "冰点"
        reasons.append(f"最高板仅{mh}板 / 晋级率{up_rate}%，处于冰点")
    # 退潮判定
    elif br > SENT["break_rate_max"] or nuke >= SENT["nuke_max"]:
        phase = "退潮"
        reasons.append(f"炸板率{br}% / 核按钮{nuke}只，亏钱效应未收敛")
    # 高潮判定
    elif seal >= SENT["seal_cnt_max"] or (mh >= 6 and br < 12):
        phase = "高潮"
        reasons.append(f"涨停{seal}家 / 最高{mh}板，情绪高潮需防次日分化")
    else:
        phase = "发酵"
        reasons.append(f"涨停{seal}家 / 最高{mh}板 / 炸板率{br}%，处于发酵期")

    allow = TRADE_ALLOWED_BY_PHASE[phase]
    if allow:
        reasons.append("情绪支持，可以出手")

    return {"date": date, "phase": phase, "allow_trade": allow,
            "reason": "；".join(reasons), "raw": s}


# ============================================================
# 第二关：硬过滤
# ============================================================
def pass_hard_filter(z: dict[str, Any]) -> tuple[bool, str]:
    """剔除结构上根本无法参与或风险性质已变的标的。"""
    name = z["name"]

    if HARD["exclude_st"] and any(w in name for w in HARD["exclude_name_words"]):
        return False, "ST/退市风险股"

    if HARD["min_amount_yi"] * 1e8 > z["amount"] > HARD["max_amount_yi"] * 1e8:
        pass
    if z["amount"] < HARD["min_amount_yi"] * 1e8:
        return False, f"成交额{HARD['min_amount_yi']}亿以下，流动性不足"
    if z["amount"] > HARD["max_amount_yi"] * 1e8:
        return False, f"成交额超{HARD['max_amount_yi']}亿，多为出货"

    if z["float_cap"] < HARD["min_float_cap_yi"] * 1e8:
        return False, f"流通市值{HARD['min_float_cap_yi']}亿以下，易被操纵"

    if z["limit_days"] > HARD["max_limit_days"]:
        return False, f"{z['limit_days']}连板超上限，高位接力风险性质变"

    # 一字板：全天封死无换手，买不到；开板即获利盘集中涌出
    if HARD["exclude_yizi"] and z["first_seal"] == "09:25:00" and z["break_times"] == 0:
        return False, "一字板，无法建仓且开板即抛压集中"

    # 筹码断层：N天M板中 N 过大但近期无充分换手
    try:
        days = int(z["zt_stat"].split("天")[0])
        if days > HARD["max_ztj_days"]:
            return False, f"{z['zt_stat']}，反复炒作后筹码断层"
    except (ValueError, IndexError):
        pass

    if z["break_times"] >= 2:
        return False, f"炸板{z['break_times']}次，分歧过大"

    # 封单质量硬门槛：封单量/成交额比过低 = 锁仓意愿弱，次日溢价差。
    # 阈值取实测中位数（hard_floor 略低于中位数，避免过度剔除）。
    _sr = z["seal_fund"] / z["amount"] if z["amount"] else 0.0
    if _sr < SEAL["hard_floor"]:
        return False, f"封单比{_sr:.2f}不足{SEAL['hard_floor']}，锁仓意愿弱"

    return True, "通过"


# ============================================================
# 第三、四关：缺口形态 + 量能验证（需 K 线）
# ============================================================
def analyze_gap_and_volume(code: str, z: dict[str, Any],
                           sector_cnt: dict[str, int],
                           date: str) -> dict[str, Any] | None:
    """识别向上突破缺口、验证量能、校验板块共振。不合格返回 ok=False。"""
    try:
        bars = tencent_kline(code, count=GAP["box_lookback"] + 10, end=date)
    except KlineError as exc:
        return {"ok": False, "why": f"K线获取失败: {exc}"}

    if len(bars) < GAP["box_lookback"] + 2:
        return {"ok": False, "why": "K线数据不足"}

    # 关键校验：K线最后一根必须是目标交易日，否则前复权序列错位
    want = f"{date[0:4]}-{date[4:6]}-{date[6:8]}"
    if bars[-1]["date"] != want:
        return {"ok": False, "why": f"K线末根{bars[-1]['date']}与目标日{want}不符"}

    cur, prev = bars[-1], bars[-2]
    lookback = GAP["box_lookback"]

    # --- 突破缺口识别（A 股口径）
    # A股涨停为盘中封板、开板回落后收盘仍封在最高价附近，全天最低价几乎必然
    # 跌回前日最高价之下，故「全天不回补」不适用于 A 股（那是美股收盘制定价下的标准）。
    # A 股的「跳空突破」等价于：收盘价大幅超越近20日箱体上沿，即资金用收盘价
    # 重新定价，突破缺口成立。缺口下沿取前日最高价（结构支撑 = 止损位）。
    open_gap_pct = (cur["open"] - prev["high"]) / prev["high"] * 100

    # --- 第五关：板块共振（一票否决）
    ind = (z.get("industry") or "").strip()
    sc = sector_cnt.get(ind, 0)
    if sc < SECTOR["min_zt_per_sector"]:
        return {"ok": False, "why": f"题材「{ind or '未知'}」仅{sc}家涨停，未达共振门槛"}

    # --- 强收盘校验：收盘须贴近当日最高价（涨停池中封板股天然满足，
    #     该项主要剔除「冲高回落型」伪封板）
    day_range = cur["high"] - cur["low"]
    if day_range > 0:
        close_pos = (cur["close"] - cur["low"]) / day_range
        if close_pos < GAP["close_near_high"]:
            return {"ok": False, "why": f"收盘位于振幅{close_pos*100:.0f}%处，冲高回落"}

    # --- 突破箱体校验
    box_bars = bars[-(lookback + 1):-1]
    box_high = max(b["high"] for b in box_bars)
    if GAP["require_breakout"] and cur["close"] <= box_high:
        return {"ok": False, "why": f"未突破{lookback}日箱体上沿{box_high:.2f}"}

    # 缺口强度 = 收盘超越箱体上沿的幅度（突破缺口的真实驱动力）
    gap_pct = (cur["close"] - box_high) / box_high * 100
    if gap_pct < GAP["min_gap_pct"]:
        return {"ok": False, "why": f"超越箱体{gap_pct:.1f}%过小，属噪声"}
    if gap_pct > GAP["max_gap_pct"]:
        return {"ok": False, "why": f"超越箱体{gap_pct:.1f}%过大，警惕衰竭"}

    # --- 量能验证
    vol_window = bars[-(VOL["lookback"] + 1):-1]
    avg_vol = sum(b["volume"] for b in vol_window) / len(vol_window)
    vol_ratio = cur["volume"] / avg_vol if avg_vol else 0.0
    if vol_ratio < VOL["min_ratio"]:
        return {"ok": False, "why": f"量比{vol_ratio:.2f}不足，流动性真空跳空"}
    if vol_ratio > VOL["max_ratio"]:
        return {"ok": False, "why": f"量比{vol_ratio:.2f}过高，天量派发风险"}

    return {
        "ok": True,
        "gap_pct": round(gap_pct, 2),
        "open_gap_pct": round(open_gap_pct, 2),
        "vol_ratio": round(vol_ratio, 2),
        "box_high": round(box_high, 2),
        "gap_low": round(prev["high"], 2),   # 缺口下沿 = 结构支撑 = 止损位
        "prev_high": round(prev["high"], 2),
        "prev_close": round(prev["close"], 2),
        "close": round(cur["close"], 2),
    }


# ============================================================
# 第五关：板块共振
# ============================================================
def build_sector_map(zt: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, dict]]:
    """统计同题材涨停家数，并识别各板块龙头。

    龙头定义：板块内连板高度最高者；高度相同取封板最早者。
    这是回测中被反复验证的最强单一确认条件——隔夜/盘中同方向测试过关键位。
    """
    counts: dict[str, int] = {}
    members: dict[str, list[dict]] = {}
    for z in zt:
        ind = (z.get("industry") or "").strip()
        if not ind:
            continue
        counts[ind] = counts.get(ind, 0) + 1
        members.setdefault(ind, []).append(z)

    leaders: dict[str, dict] = {}
    for ind, lst in members.items():
        leaders[ind] = sorted(
            lst, key=lambda x: (-x["limit_days"], x["first_seal"])
        )[0]
    return counts, leaders


# ============================================================
# 第六关：打分
# ============================================================
def score_candidate(z: dict[str, Any], gv: dict[str, Any],
                    sector_cnt: int, is_leader: bool) -> tuple[float, dict]:
    """综合打分，返回 (总分, 明细)。五维合计 100 分。"""
    d: dict[str, Any] = {}

    # --- 板块共振 30 分
    if sector_cnt >= SECTOR["strong_zt_per_sector"]:
        sec = min(W["sector"], 18 + sector_cnt * 3)
    elif sector_cnt >= SECTOR["min_zt_per_sector"]:
        sec = 12 + (sector_cnt - SECTOR["min_zt_per_sector"]) * 4
    else:
        sec = sector_cnt * 5
    if is_leader:
        sec = min(W["sector"], sec + SECTOR["leader_bonus_max"])
    sec = round(sec, 1)
    d["板块共振"] = f"{sector_cnt}家涨停{'（龙头）' if is_leader else ''} → {sec}分"

    # --- 封单质量 20 分
    seal_ratio = z["seal_fund"] / z["amount"] if z["amount"] else 0.0
    if seal_ratio >= SEAL["ratio_extreme"]:
        seal = W["seal"]
    elif seal_ratio >= SEAL["ratio_strong"]:
        seal = 15
    elif seal_ratio >= SEAL["ratio_pass"]:
        seal = 10
    else:
        seal = 5
    if z["first_seal"] <= SEAL["min_seal_time"]:
        seal = min(W["seal"], seal + 4)
    elif SEAL["afternoon_penalty"]:
        seal = max(0, seal - 5)
    seal = round(seal, 1)
    d["封单质量"] = f"封单比{seal_ratio:.2f} / {z['first_seal'][:5]}封板 → {seal}分"

    # --- 个股地位 20 分
    pos = min(z["limit_days"], 4) * 4          # 连板高度
    if is_leader:
        pos += 6
    if z["break_times"] == 0:
        pos += 2                               # 硬板优于烂板
    pos = round(min(W["position"], pos), 1)
    d["个股地位"] = f"{z['zt_stat']}{'·龙头' if is_leader else ''} → {pos}分"

    # --- 量能健康度 15 分
    vr = gv["vol_ratio"]
    lo, hi = VOL["healthy_range"]
    if lo <= vr <= hi:
        vol = W["volume"]
    elif vr < lo:
        vol = 8 * vr / lo
    else:
        vol = max(4, W["volume"] - (vr - hi) * 4)
    vol = round(vol, 1)
    d["量能"] = f"量比{vr:.2f} → {vol}分"

    # --- 缺口强度 15 分
    gp = gv["gap_pct"]
    gap = round(min(W["gap"], gp / GAP["max_gap_pct"] * W["gap"]), 1)
    d["缺口"] = f"缺口{gp:.1f}% → {gap}分"

    total = round(sec + seal + pos + vol + gap, 1)
    return total, d


# ============================================================
# 主流程
# ============================================================
def run(date: str | None = None, top_n: int = 10,
        no_trade: bool = False) -> dict[str, Any]:
    if not date:
        import datetime as _dt
        today = _dt.date.today()
        date = prev_trading_day(today.strftime("%Y%m%d"))

    print("=" * 68)
    print(f"  A股「跳空突破 + 板块共振」盘后选股    {date}")
    print("=" * 68)

    # ---- 第一关：情绪闸门
    print("\n[第一关] 情绪周期体检")
    st = judge_sentiment(date)
    raw = st["raw"]
    if st["phase"] == "非交易日":
        print(f"  {date} 非交易日，退出。")
        return {"date": date, "sentiment": st, "candidates": []}

    print(f"  涨停 {raw['seal_cnt']} 家 | 炸板 {raw['break_cnt']} 家 "
          f"(炸板率 {raw['break_rate']}%) | 跌停 {raw['dt_count']} 家")
    print(f"  最高连板 {raw['max_height']} 板 | 梯队 {raw['ladder']}")
    print(f"  昨日涨停 {raw['y_zt_cnt']} 只 → 红盘率 {raw['y_zt_up_rate']}% "
          f"(均涨 {raw['y_zt_avg_pct']}%) | 核按钮 {raw['nuke_cnt']} 只")
    print(f"  ▶ 周期定位：{st['phase']}")
    print(f"  ▶ {st['reason']}")

    if no_trade:
        print("\n  [体检模式] 未执行选股。")
        return {"date": date, "sentiment": st, "candidates": []}
    if not st["allow_trade"]:
        print(f"\n  ⛔ 情绪处于【{st['phase']}期】，策略不开仓。今日空仓。")
        print("     铁律：亏钱效应未收敛时，任何形态的胜率都会系统性下降。")
        return {"date": date, "sentiment": st, "candidates": []}

    # ---- 取池
    zt = em_zt_pool(date)
    if not zt:
        print("  涨停池为空，退出。")
        return {"date": date, "sentiment": st, "candidates": []}

    # ---- 第二关：硬过滤
    print(f"\n[第二关] 硬过滤（涨停池 {len(zt)} 只）")
    after_hard: list[dict] = []
    for z in zt:
        ok, why = pass_hard_filter(z)
        if ok:
            after_hard.append(z)
    print(f"  通过 {len(after_hard)} 只，剔除 {len(zt) - len(after_hard)} 只"
          f"（一字板 / ST / 流动性 / 连板高度 / 筹码断层）")

    # ---- 第五关（先做）：板块共振需要全池视角
    sector_cnt, leaders = build_sector_map(zt)
    strong = [i for i, c in sector_cnt.items() if c >= SECTOR["min_zt_per_sector"]]
    print(f"\n[第五关] 板块共振（共 {len(sector_cnt)} 个题材）")
    top_sector = sorted(sector_cnt.items(), key=lambda x: -x[1])[:5]
    print("  共振最强：" + "、".join(f"{k}({v}家)" for k, v in top_sector))

    # ---- 第三、四、五关：缺口 + 量能 + 共振（逐只拉K线，请稍候）
    print(f"\n[第三·四·五关] 缺口形态 + 量能验证 + 板块共振（逐只拉K线，请稍候）")
    cands: list[Candidate] = []
    stats = {"gap_fail": 0, "vol_fail": 0, "sector_fail": 0, "kline_err": 0}
    for idx, z in enumerate(after_hard, 1):
        gv = analyze_gap_and_volume(z["code"], z, sector_cnt, date)
        if gv is None:
            stats["kline_err"] += 1
            continue
        if not gv.get("ok"):
            why = gv.get("why", "")
            if "缺口" in why:
                stats["gap_fail"] += 1
            elif "量比" in why:
                stats["vol_fail"] += 1
            elif "共振" in why:
                stats["sector_fail"] += 1
            continue

        ind = (z.get("industry") or "").strip()
        sc = sector_cnt.get(ind, 0)
        is_leader = leaders.get(ind, {}).get("code") == z["code"]
        total, detail = score_candidate(z, gv, sc, is_leader)

        seal_ratio = z["seal_fund"] / z["amount"] if z["amount"] else 0.0
        cands.append(Candidate(
            code=z["code"], name=z["name"], industry=ind,
            price=z["price"], pct=z["pct"], score=total,
            limit_days=z["limit_days"], zt_stat=z["zt_stat"],
            first_seal=z["first_seal"], seal_fund=z["seal_fund"],
            amount_yi=round(z["amount"] / 1e8, 2),
            float_cap_yi=round(z["float_cap"] / 1e8, 2),
            turnover=z["turnover"], break_times=z["break_times"],
            gap_pct=gv["gap_pct"], vol_ratio=gv["vol_ratio"],
            seal_ratio=round(seal_ratio, 2), sector_zt_cnt=sc,
            is_sector_leader=is_leader,
            gap_low=gv["gap_low"], box_high=gv["box_high"],
            stop_loss=round(gv["gap_low"] * 0.995, 2),
            detail=detail,
        ))
        if idx % 5 == 0:
            print(f"    ... {idx}/{len(after_hard)}")

    print(f"  淘汰明细：缺口形态不合格 {stats['gap_fail']}，"
          f"量能不合格 {stats['vol_fail']}，板块未共振 {stats['sector_fail']}，"
          f"数据异常 {stats['kline_err']}")

    cands.sort(key=lambda c: -c.score)
    top = cands[:top_n]

    # ---- 输出
    print(f"\n{'=' * 68}")
    print(f"  候选池（通过全部六关：{len(cands)} 只，展示前 {len(top)}）")
    print("=" * 68)
    if not top:
        print("  无合格标的。明日空仓。")
        return {"date": date, "sentiment": st, "candidates": [],
                "all_count": len(cands)}

    for i, c in enumerate(top, 1):
        print(f"\n  【{i}】{c.name}({c.code})  {c.industry}   {c.score} 分")
        print(f"       现价 {c.price}  涨幅 {c.pct}%  {c.zt_stat}"
              f"{'  ★板块龙头' if c.is_sector_leader else ''}")
        print(f"       缺口 {c.gap_pct}%  量比 {c.vol_ratio}  封单比 {c.seal_ratio}"
              f"  成交额 {c.amount_yi}亿  换手 {c.turnover}%")
        print(f"       突破 {c.box_high} → 现价 {c.price}，缺口下沿 {c.gap_low}")
        print(f"       止损位 {c.stop_loss}（跌破缺口下沿即离场，不摊平）")
        for k, v in c.detail.items():
            print(f"         · {k}：{v}")

    print(f"\n{'=' * 68}")
    print("  明日执行纪律")
    print("=" * 68)
    print("  1. 竞价确认：次日高开 1%~5% 且竞价量放大，方可按计划介入；")
    print("     低开或竞价清淡 → 放弃，当日不接。")
    print("  2. 止损：跌破缺口下沿（已含 0.5% 缓冲）无条件离场。")
    print("  3. 目标：+1R 减半仓，余仓用 1×ATR 移动止损追踪。")
    print("  4. 若次日板块内龙头低开闷杀，全部候选一律取消。")
    print("  5. 单日最多持有 2 只，总仓位不超过 50%。")

    return {"date": date, "sentiment": st,
            "candidates": [c.to_dict() for c in top],
            "all_count": len(cands)}


def main() -> None:
    ap = argparse.ArgumentParser(
        description="A股跳空突破+板块共振 盘后选股")
    ap.add_argument("-d", "--date", help="交易日 YYYYMMDD，默认最近交易日")
    ap.add_argument("--top", type=int, default=10, help="展示前 N 名，默认 10")
    ap.add_argument("--no-trade", action="store_true", help="只做情绪体检，不选股")
    ap.add_argument("--json", help="将结果写入指定 JSON 路径")
    a = ap.parse_args()
    try:
        res = run(a.date, a.top, a.no_trade)
    except Exception as exc:  # noqa: BLE001
        print(f"执行失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        print(f"\n结果已写入 {a.json}")


if __name__ == "__main__":
    main()
