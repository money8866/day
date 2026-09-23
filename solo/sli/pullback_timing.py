# -*- coding: utf-8 -*-
"""
看长做短 · 回踩择时（Pullback Timing on POOL_MIDLONG_V2 = NEXT_LEADER_V2）
==========================================================================

「看长」用池定范围，「做短」用回踩定时点 —— 不追高，只在回调后接。

背景（sli.backtest 20230630~20260921 四十期点校准结论）：
mid_long_score 是「位置 + 动量」的复合暴露（与 pos120 秩相关 +0.276、与
mom60 +0.269），而位置高 → 未来差（池内 120 日区间位置 Q1 未来 20 日
+2.38%，Q4 仅 +0.55%；预警场景代理 [70,75) +2.18% 单调降到 ≥85 +0.79%）。
因此池内按分数排序取头部是**负向择时**。正确用法：池子当长线范围，
回踩低位当入场时点。

关于「回踩」口径的实证修正（本模块事件回测，2022-01 ~ 2026-09，
趋势 + 量价 + 闸门 + 缩量触发四层固定，只变回踩口径；数字为 20 日超额）：

  回撤深度硬门槛 ≥2% 反而压低超额——池内真正的甜点是**浅回踩**；
  再叠加「相对强度回踩」取交集最优：
    原严格(距高点回撤 2%~12%)          +0.85% (t=2.79)   1613 事件  ×
    无回踩对照                         +0.49% (t=2.76)   2124 事件
    相对强度回踩(5 日跑输、20 日跑赢)     +1.04% (t=3.00)   1071 事件
    浅回踩(距 20 日高点回撤 0.5%~6%)     +1.86% (t=3.69)   1102 事件
    浅回踩 ∩ 相对强度（默认）            +2.13% (t=3.39)    557 事件  √
                                       40 日 +2.07% (t=3.62)、MAE −11.2%
  触发口径上，「仅缩量」优于「收阳+低点抬高+缩量」：
    浅回踩+仅缩量 +1.68%(t=3.71) / +2.33%(t=5.33) ＞ 浅回踩+原触发 +0.38%(t=1.94)
  市场闸门是独立增益项（样本内闸门开启占比 59%）：
    趋势+量价+触发 无闸门 +0.90%(t=3.26) → 加闸门 +1.17%(t=2.91)

  已知风险：分年度看，各回踩口径 2023 年均微负（默认口径 −1.31%/41 个交易日），
  超额主要来自 2025—2026；市场闸门只能缓解、不能消除该时变暴露。

信号六层（全部后复权、全部 rolling/backward，无未来函数）：

  ① 看长 · 长线池
     交易日 d 的可交易范围 = 最近一个期点（≤ d）的池成员快照。
     快照口径 = sli.classify.next_leader_v2 的
     next_gate & mid_long_score ≥ CLS_V2["next_leader"]["score_min"]，
     与实盘 sli.reader.get_next_leaders() 同源。

  ② 已经走出上升趋势（trend_ok）
     T1 均线多头排列      MA20 > MA60 > MA120
     T2 中期均线上行      MA60 > MA60[20 日前] × (1 + mid_slope_min)
     T3 站上中期均线      C > MA60 × above_tol(0.97)
     T4 已实现显著波段涨幅 max(C,60日) / min(C,60日) − 1 ≥ leg_gain(20%)
        「走出」而非「刚反转」：过去 60 日内必须真的有过一段 20%+ 上攻。
        只看均线排列会把刚金叉的横盘票也算成上升趋势。

  ③ 量价关系良性（vp_ok，同时给 0~100 的 VPH 分）
     V1 上攻放量           近 60 日出现过 涨幅≥3% 且 量比≥1.3
     V2 回调缩量           近 3 日均量 / 20 日均量 ≤ 0.90
     V3 无放量下跌         近 10 日无 跌幅≤−3% 且 量比≥1.5（排除放量出货）
     V4 量价健康分         VPH = 缩量程度 40 + 上攻量比 30 + 无放量下跌 30
                           ≥ vph_min(60)
        「良性」= 涨要放量、跌要缩量；放量下跌与量价背离一律剔除。

  ④ 回踩不追高（pull_ok）—— 「做短」的买点位置，pull_mode 三选一
     P1 浅回踩             距 20 日高点回撤 0.5% ~ 6%（pull_mode="shallow"）
       （实测：要求回撤 ≥2% 的深回踩口径负贡献，浅回踩才是甜点；
         但必须已经离开高点 ≥0.5%，避免在创新高当天追高）
     P2 相对强度回踩（pull_mode="rs"） 近 rs_short(5) 日跑输池内等权、
        近 rs_long(20) 日仍跑赢池内等权 —— 「短期歇脚、中期仍强」。
     P1 ∩ P2（pull_mode="shallow+rs"，默认） 形态与择时双确认，
        事件数减半但 20 日超额最高（+2.13%）。
     三种 mode 都叠加「高点新鲜度」：20 日高点 ≥ 60 日高点 × 0.95，
     排除「60 日前见顶、之后一路阴跌」的伪回踩。

  ⑤ 缩量止跌触发（stop_fall）—— 入场扳机
     当日量 ≤ 前 5 日均量（trig_vol_vs_vma5）。回调缩量 = 抛压衰竭；
     实测优于「收阳 + 低点抬高 + 缩量」的复合触发（后者会把入场推迟到
     第一根反弹阳线，反而吃掉超额）。

  ⑥ 市场环境闸门（market_gate）
     全市场等权累计净值 > 其 gate_ma(20) 日均线才允许开仓。
     池内信号在时间上高度聚集，闸门剥离掉「逆风期的同向回撤」。

卖出 / 风控（阈值由回测的 MAE 与分持有期收益反推）：
    H1 止损   收盘跌破 20 日最低价（回踩低点）→ 无条件离场
    H2 止盈   累计 +8% / +12% 分批减仓
    H3 时间   持有 20 个交易日强制离场（做短）
    止损位保持「结构位」（20 日最低价）不收紧 —— 实测（565 笔事件逐日实际退出回测）：
      硬把止损收到 −10% / −15%，止损率升到 41% / 26%，20 日日均收益由 3.17%
      掉到 2.25% / 2.77%（这类高波动回踩票的正常噪声就在 −10% ~ −15%）；
      而入场时结构止损距离越宽、未来收益反而越高（0~6% +0.38% → 20%+ +6.27%），
      故「过滤止损距离过宽的标的」同样会砍掉最好的样本。
    极端回撤改由**仓位**消化，规则是风险预算折算：
      建议仓位 = risk_budget(1% 账户) ÷ 该股止损距离，上限 max_position(20%)
      止损距离 −39% 的标的自动只给约 2.5% 仓位 —— 账户级单笔最大回撤被钉在
      风险预算上，而策略正期望完整保留。扫描输出直接给 stop_dist_pct / position_pct。

防未来函数：
  · 池成员按期点快照，交易日只可见「≤ 当日」的最近一期；
  · 价格后复权（close × adj_factor），除权不产生假跌幅；
  · 信号 T 日收盘确认，回测 T+entry_lag（默认 1）日收盘成交；
  · 指标全部 rolling/backward。

用法：
  python -m sli.pullback_timing --init                    # 生成池历史快照缓存
  python -m sli.pullback_timing --backtest                # 全样本回测 + 消融
  python -m sli.pullback_timing --grid                    # 参数敏感性
  python -m sli.pullback_timing --scan                    # 实盘：最新交易日信号
  python -m sli.pullback_timing --scan --date 20260921    # 指定日扫描
输出：
  sli/output/pullback_timing_events_<tag>.csv    命中事件明细
  sli/output/pullback_timing_summary_<tag>.csv   分持有期 / 分变体汇总
  sli/output/pullback_timing_grid_<tag>.csv      参数敏感性
  sli/output/pullback_timing_scan_<date>.csv     实盘扫描
落库：
  统一跟踪表 stock_pick_db（strategy=sli_pullback_timing），--scan 自动写入
  「可买」(signal=可买) 与「形态就绪」(signal=形态就绪) 两类信号，
  由 python stock_pick_db.py tracking 回填 T+N 收益与止损/目标命中。
缓存：
  sli/cache/pool_midlong_v2_history.parquet      各期点池快照（长线范围）
  sli/cache/pool_pullback_daily.parquet          池内后复权日线长表（只扩不缩）
  sli/cache/pool_pullback_daily.parquet.codes.json  日线缓存覆盖的票池
  sli/cache/market_ew_daily.parquet              全市场等权日收益（基准 + 市场闸门）
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd

from .config import BASE_DIR, CACHE_DIR, CLS_V2, LOG_DIR, OUTPUT_DIR, POOL_MIDLONG_V2
from .utils import setup_logging

log = logging.getLogger("sli.pullback_timing")

# ══════════════════════════════════════════════════════
# 路径与参数
# ══════════════════════════════════════════════════════

POOL_HIST_PATH = os.path.join(CACHE_DIR, "pool_midlong_v2_history.parquet")
DAILY_PATH = os.path.join(CACHE_DIR, "pool_pullback_daily.parquet")
DAILY_CODES_PATH = DAILY_PATH + ".codes.json"
MARKET_PATH = os.path.join(CACHE_DIR, "market_ew_daily.parquet")
# 上一轮为 mid_long_score 择时校准所建的 40 期点面板（仅作池历史的一次性来源）
LEGACY_PERIOD_PANEL = os.path.join(os.path.dirname(BASE_DIR), "_pa_timing_panel.parquet")

CFG: dict[str, Any] = {
    # ── 回测口径 ──
    "cooldown": 20,          # 同一只股票两次入场信号的最小间隔（交易日）
    "entry_lag": 1,          # T 日收盘确认信号 → T+lag 日收盘成交
    "horizons": (5, 10, 20, 40),
    "primary_horizon": 20,   # 分年度稳健性检验所用的主持有期

    # ── ② 趋势「已经走出上升趋势」──
    "ma_short": 20, "ma_mid": 60, "ma_long": 120,
    "mid_slope_days": 20,    # 中期均线斜率回看窗口
    "mid_slope_min": 0.0,    # MA60 相对 20 日前的最小涨幅（0 = 不下行）
    "above_tol": 0.97,       # 收盘价 / MA60 的下限容忍
    "leg_days": 60,          # 已实现波段涨幅的观察窗口
    "leg_gain": 0.20,        # 「走出」门槛：60 日内波段涨幅 ≥ 20%

    # ── ④ 回踩不追高 ──
    "pull_dd_min": 0.005,    # 距 20 日高点的最小回撤（避免在创新高当天追高）
    "pull_dd_max": 0.06,     # 最大回撤（浅回踩甜点，深回踩实测负贡献）
    "high_fresh_k": 0.95,    # 高点新鲜度：20 日高点 / 60 日高点 的下限
    "high_fresh_days": 60,
    "pull_mode": "shallow+rs",  # shallow 浅回踩 | rs 相对强度回踩 | shallow+rs 两者取交集
    "rs_short": 5, "rs_long": 20,   # 相对强度回踩的短/中期回看窗口

    # ── ③ 量价关系良性 ──
    "up_pct": 0.03, "up_vr": 1.3, "up_win": 60,
    "shrink_win": 3, "shrink_max": 0.90,
    "bad_pct": -0.03, "bad_vr": 1.5, "bad_win": 10,
    "vph_min": 60.0,

    # ── ⑤ 缩量止跌触发 ──
    "trig_vol_vs_vma5": 1.0,

    # ── ⑥ 市场环境闸门 ──
    "gate_ma": 20,

    # ── 卖出 / 风控（止损位取结构位，风险预算靠仓位折算，不收紧止损位）──
    "risk_budget": 0.01,     # 单笔风险预算：止损打掉时允许损失的账户比例
    "max_position": 0.20,    # 单票仓位上限
}

# 消融变体：控制 趋势 / 回踩 / 量价 / 闸门 / 缩量触发 五层是否启用
VARIANTS: dict[str, dict[str, bool]] = {
    "全信号(趋势+回踩+量价+闸门+缩量)": dict(trend=True, pull=True, vp=True, gate=True, trig=True),
    "消融-去趋势": dict(trend=False, pull=True, vp=True, gate=True, trig=True),
    "消融-去回踩": dict(trend=True, pull=False, vp=True, gate=True, trig=True),
    "消融-去量价": dict(trend=True, pull=True, vp=False, gate=True, trig=True),
    "消融-去闸门": dict(trend=True, pull=True, vp=True, gate=False, trig=True),
    "消融-去缩量触发": dict(trend=True, pull=True, vp=True, gate=True, trig=False),
    "变体-相对强度回踩": dict(trend=True, pull=True, vp=True, gate=True, trig=True, pull_mode="rs"),
    "变体-浅回踩∩相对强度": dict(trend=True, pull=True, vp=True, gate=True, trig=True, pull_mode="shallow+rs"),
    "对照-原严格回踩(回撤2%~12%)": dict(trend=True, pull=True, vp=True, gate=True, trig=True, strict_pull=True),
}


# ══════════════════════════════════════════════════════
# 一、池历史（看长的范围）
# ══════════════════════════════════════════════════════

def ensure_pool_history(path: Optional[str] = None, force: bool = False) -> pd.DataFrame:
    """池历史快照（period, ts_code, ..., NEXT_LEADER_V2）。

    缺少缓存时从 LEGACY_PERIOD_PANEL 一次性转写：池口径与实盘完全一致 ——
    next_gate 是三硬门槛 + 动量门槛的掩码，mid_long_score 的阈值即
    CLS_V2["next_leader"]["score_min"]。
    """
    p = path or POOL_HIST_PATH
    if os.path.exists(p) and not force:
        return pd.read_parquet(p)
    if not os.path.exists(LEGACY_PERIOD_PANEL):
        raise FileNotFoundError(
            f"缺少池历史来源：{LEGACY_PERIOD_PANEL}\n"
            "该文件由 sli.backtest 的期点面板转存（next_gate + mid_long_score）。"
            "请先跑：python -X utf8 -m sli.backtest --run --start 20230630 --end 20260921")
    pan = pd.read_parquet(LEGACY_PERIOD_PANEL)
    pan["period"] = pan["period"].astype(str)
    keep = [c for c in ("period", "ts_code", "name", "l3_name", "subsector",
                        "sli_v2", "ind_boom_tier", "total_mv", "mid_long_score",
                        "next_gate") if c in pan.columns]
    out = pan[keep].copy()
    score_min = float(CLS_V2["next_leader"]["score_min"])
    out[POOL_MIDLONG_V2] = (out["next_gate"].fillna(False)
                            & (pd.to_numeric(out["mid_long_score"], errors="coerce") >= score_min))
    out.to_parquet(p, index=False)
    log.info("[池历史] 转写 %s：%d 期点 / 池成员并集 %d 只",
             os.path.basename(p), out["period"].nunique(),
             out.loc[out[POOL_MIDLONG_V2], "ts_code"].nunique())
    return out


def pool_periods(pool_hist: pd.DataFrame) -> dict[str, set[str]]:
    """{期点: {池成员}}。"""
    return {str(p): set(g.loc[g[POOL_MIDLONG_V2].fillna(False), "ts_code"])
            for p, g in pool_hist.groupby("period")}


def pool_codes(pool_hist: pd.DataFrame) -> list[str]:
    """40 个期点池成员的并集（历史回测的完整可交易范围）。"""
    return sorted(pool_hist.loc[pool_hist[POOL_MIDLONG_V2].fillna(False),
                                "ts_code"].dropna().unique().tolist())


def pool_mask(dates: list[str], codes: list[str], by_period: dict[str, set[str]]) -> pd.DataFrame:
    """交易日 × 股票 的池成员布尔矩阵（取 ≤ 当日的最近期点，防未来）。"""
    mat = pd.DataFrame(False, index=pd.Index(dates), columns=codes)
    pa = np.array(sorted(by_period))
    pos = np.searchsorted(pa, np.array(dates), side="right") - 1
    col_pos = {c: i for i, c in enumerate(codes)}
    for d, k in zip(dates, pos):
        if k < 0:
            continue
        sel = [col_pos[c] for c in by_period[pa[k]] if c in col_pos]
        if sel:
            mat.iloc[mat.index.get_loc(d), sel] = True
    return mat


# ══════════════════════════════════════════════════════
# 二、日线面板（后复权）
# ══════════════════════════════════════════════════════

def _cache_files(kind: str) -> dict[str, str]:
    """sli/cache 下 <kind>_YYYYMMDD.parquet 的 {日期: 路径}（排除 daily_basic_*）。"""
    pat = re.compile(rf"^{kind}_(\d{{8}})\.parquet$")
    out: dict[str, str] = {}
    for f in os.listdir(CACHE_DIR):
        m = pat.match(f)
        if m:
            out[m.group(1)] = os.path.join(CACHE_DIR, f)
    return out


def build_daily(codes: Iterable[str], force: bool = False) -> pd.DataFrame:
    """池内后复权日线长表（带缓存）。

    返回 trade_date / ts_code / C(后复权收) / H / L / V(成交量)。
    请求票池不是缓存覆盖集合的子集时自动重建；同时缓存全市场等权日收益
    （直接用 tushare pct_chg 的横截面均值，已做除权处理）。
    """
    codes = sorted({c for c in codes if isinstance(c, str) and c})
    dfiles = _cache_files("daily")
    have: set[str] = set()
    if not force and os.path.exists(DAILY_PATH) and os.path.exists(DAILY_CODES_PATH):
        try:
            have = set(json.load(open(DAILY_CODES_PATH, encoding="utf-8")))
        except (OSError, ValueError):
            have = set()
        if set(codes) <= have:
            cached = pd.read_parquet(DAILY_PATH)
            # 除票池覆盖外还须覆盖最新交易日，否则新一天的盘后数据进不了面板，
            # 扫描会静默退回上一交易日。
            if not dfiles or str(cached["trade_date"].max()) >= max(dfiles):
                return cached
    # 只扩不缩：回测（池成员并集）与实盘扫描（当期池）复用同一份缓存，
    # 否则一次扫描会把缓存缩小，下一次回测又得全量重建。
    codes = sorted(set(codes) | have)

    afiles = _cache_files("adj_factor")
    if not dfiles:
        raise FileNotFoundError(f"{CACHE_DIR} 下无 daily_YYYYMMDD.parquet 缓存")
    cs = set(codes)
    dates = sorted(dfiles)
    log.info("[日线] 构建 %d 只 × %d 个交易日（%s ~ %s）...",
             len(cs), len(dates), dates[0], dates[-1])

    frames: list[pd.DataFrame] = []
    mkt: dict[str, float] = {}
    for i, d in enumerate(dates, 1):
        df = pd.read_parquet(dfiles[d])
        if "pct_chg" in df.columns:
            pc = pd.to_numeric(df["pct_chg"], errors="coerce")
            mkt[d] = float(pc.mean()) if pc.notna().any() else np.nan
        sub = df[df["ts_code"].isin(cs)]
        if not sub.empty:
            frames.append(sub[["ts_code", "trade_date", "close", "high", "low", "vol"]])
        if i % 300 == 0:
            log.info("     日线 %d/%d", i, len(dates))
    px = pd.concat(frames, ignore_index=True)

    afs: list[pd.DataFrame] = []
    for d in sorted(afiles):
        a = pd.read_parquet(afiles[d])
        a = a[a["ts_code"].isin(cs)]
        if not a.empty:
            afs.append(a[["ts_code", "trade_date", "adj_factor"]])
    af = pd.concat(afs, ignore_index=True).drop_duplicates(["ts_code", "trade_date"])
    # 日线与复权因子的 trade_date 一律归一为字符串，保证 merge 与后续宽表索引一致
    for df in (px, af):
        df["trade_date"] = df["trade_date"].astype(str)
    px = px.merge(af, on=["ts_code", "trade_date"], how="left")
    px = px.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    px["adj_factor"] = pd.to_numeric(px["adj_factor"], errors="coerce")
    px["adj_factor"] = px.groupby("ts_code")["adj_factor"].transform(
        lambda s: s.ffill().bfill())
    px = px.dropna(subset=["adj_factor"]).reset_index(drop=True)
    for src, dst in (("close", "C"), ("high", "H"), ("low", "L")):
        px[dst] = pd.to_numeric(px[src], errors="coerce") * px["adj_factor"]
    px["V"] = pd.to_numeric(px["vol"], errors="coerce")
    px = px[["trade_date", "ts_code", "C", "H", "L", "V"]]

    px.to_parquet(DAILY_PATH, index=False)
    with open(DAILY_CODES_PATH, "w", encoding="utf-8") as f:
        json.dump(codes, f, ensure_ascii=False)
    pd.DataFrame({"trade_date": sorted(mkt), "mkt_ret": [mkt[d] for d in sorted(mkt)]}
                 ).to_parquet(MARKET_PATH, index=False)
    log.info("[日线] 完成 %d 行 → %s", len(px), os.path.basename(DAILY_PATH))
    return px


def market_series() -> pd.Series:
    """全市场等权日收益（小数），用于基准。"""
    df = pd.read_parquet(MARKET_PATH)
    s = pd.Series(pd.to_numeric(df["mkt_ret"], errors="coerce").values / 100.0,
                  index=df["trade_date"].astype(str))
    return s.sort_index()


def to_wide(px: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """长表 → {字段: 交易日 × 股票} 宽表（索引统一为字符串日期）。"""
    out: dict[str, pd.DataFrame] = {}
    for f in ("C", "H", "L", "V"):
        w = px.pivot(index="trade_date", columns="ts_code", values=f)
        w.index = w.index.astype(str)
        out[f] = w.sort_index().astype(float)
    return out


# ══════════════════════════════════════════════════════
# 三、特征与三层信号
# ══════════════════════════════════════════════════════

def _days_since(flag: pd.DataFrame) -> pd.DataFrame:
    """每格距该列最近一次 True 的交易日数（当日 True 记 0），无则 NaN。"""
    arr = flag.values.astype(bool)
    n, m = arr.shape
    out = np.full((n, m), np.nan)
    last = np.full(m, -1, dtype=int)
    for i in range(n):
        last = np.where(arr[i], i, last)
        out[i] = np.where(last >= 0, i - last, np.nan)
    return pd.DataFrame(out, index=flag.index, columns=flag.columns)


def features(w: dict[str, pd.DataFrame], cfg: dict[str, Any]) -> dict[str, pd.DataFrame]:
    """技术特征（全部 rolling/backward）。"""
    C, H, L, V = w["C"], w["H"], w["L"], w["V"]
    f: dict[str, pd.DataFrame] = {"C": C, "H": H, "L": L, "V": V}
    f["r1"] = C.pct_change()
    for n in (5, 10, cfg["ma_short"], cfg["ma_mid"], cfg["ma_long"]):
        f[f"ma{n}"] = C.rolling(n).mean()
    f["vma5"] = V.rolling(5).mean()
    f["vma20"] = V.rolling(20).mean()
    f["v3"] = V.rolling(cfg["shrink_win"]).mean()
    f["hh20"] = C.rolling(20).max()
    f[f"hh{cfg['high_fresh_days']}"] = C.rolling(cfg["high_fresh_days"]).max()
    f["ll20"] = L.rolling(20).min()
    f["ll60"] = C.rolling(cfg["leg_days"]).min()
    f["vr"] = V / f["vma20"]
    f["vma5_prev"] = V.shift(1).rolling(5).mean()
    f["days_since_hh20"] = _days_since(C.ge(f["hh20"]))
    f["dd_hh20"] = C / f["hh20"] - 1.0                      # 距 20 日高点的回撤
    f["ret_s"] = C / C.shift(cfg["rs_short"]) - 1.0          # 短期动量（相对强度回踩用）
    f["ret_l"] = C / C.shift(cfg["rs_long"]) - 1.0           # 中期动量
    return f


def market_gate(cfg: dict[str, Any], dates: list[str]) -> np.ndarray:
    """⑥ 市场环境闸门：全市场等权累计净值 > 其 gate_ma 日均线。

    池内信号在时间上高度聚集（同一波回调同时打中几十只），闸门把
    「逆风期的同向回撤」整段剥离，是独立于个股形态的增益层。
    """
    mkt = market_series().reindex(dates).fillna(0.0)
    cum = (1.0 + mkt).cumprod()
    return (cum > cum.rolling(cfg["gate_ma"]).mean()).values


def trend_ok(f: dict[str, pd.DataFrame], cfg: dict[str, Any]) -> pd.DataFrame:
    """T1 多头排列 · T2 中期均线上行 · T3 站上中期均线 · T4 已实现显著波段涨幅。"""
    C, ma = f["C"], f
    T1 = (ma[f"ma{cfg['ma_short']}"] > ma[f"ma{cfg['ma_mid']}"]) & \
         (ma[f"ma{cfg['ma_mid']}"] > ma[f"ma{cfg['ma_long']}"])
    T2 = ma[f"ma{cfg['ma_mid']}"] > ma[f"ma{cfg['ma_mid']}"].shift(cfg["mid_slope_days"]) \
        * (1.0 + cfg["mid_slope_min"])
    T3 = C > ma[f"ma{cfg['ma_mid']}"] * cfg["above_tol"]
    T4 = (ma[f"hh{cfg['high_fresh_days']}"] / f["ll60"] - 1.0) >= cfg["leg_gain"]
    return (T1 & T2 & T3 & T4).fillna(False)


def pull_ok(f: dict[str, pd.DataFrame], cfg: dict[str, Any],
            pm: Optional[pd.DataFrame] = None, mode: Optional[str] = None,
            strict: bool = False) -> pd.DataFrame:
    """④ 回踩不追高。

    mode="shallow"     浅回踩：距 20 日高点回撤 pull_dd_min ~ pull_dd_max
    mode="rs"          相对强度回踩：近 rs_short 日跑输池内等权、近 rs_long 日仍跑赢
    mode="shallow+rs"  两者取交集（样本最少、单笔最厚）
    strict=True        对照口径（原「深回踩」：回撤 2%~12%），实测负贡献，仅作消融
    三种 mode 都叠加「高点新鲜度」：20 日高点 ≥ 60 日高点 × high_fresh_k，
    排除「60 日前见顶、之后一路阴跌」的伪回踩。
    """
    mode = mode or cfg["pull_mode"]
    lo, hi = (0.02, 0.12) if strict else (cfg["pull_dd_min"], cfg["pull_dd_max"])
    dd = f["dd_hh20"]
    ok = (f["hh20"] >= f[f"hh{cfg['high_fresh_days']}"] * cfg["high_fresh_k"]).fillna(False)
    if strict or mode in ("shallow", "shallow+rs"):
        ok &= ((dd <= -lo) & (dd >= -hi)).fillna(False)
    if mode in ("rs", "shallow+rs"):
        if pm is None:
            raise ValueError("相对强度回踩需要传入池掩码 pm")
        b_s = f["ret_s"].where(pm).mean(axis=1)
        b_l = f["ret_l"].where(pm).mean(axis=1)
        ok &= (f["ret_s"].lt(b_s, axis=0) & f["ret_l"].gt(b_l, axis=0)).fillna(False)
    return ok


def vp_health(f: dict[str, pd.DataFrame], cfg: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """量价良性：V1 上攻放量 · V2 回调缩量 · V3 无放量下跌 · V4 VPH≥阈值。

    VPH = 缩量程度 40 + 上攻量比 30 + 无放量下跌 30（0~100）。
    """
    r1, vr = f["r1"], f["vr"]
    v3r = f["v3"] / f["vma20"]

    up = ((r1 >= cfg["up_pct"]) & (vr >= cfg["up_vr"])).astype(float)
    up_max = (up * vr).where(up > 0).rolling(cfg["up_win"], min_periods=1).max()
    V1 = up.rolling(cfg["up_win"], min_periods=1).max().fillna(0.0).astype(bool)
    V2 = (v3r <= cfg["shrink_max"]).fillna(False)

    bad = ((r1 <= cfg["bad_pct"]) & (vr >= cfg["bad_vr"])).astype(float).fillna(0.0)
    V3 = ~bad.rolling(cfg["bad_win"], min_periods=cfg["bad_win"]).max().fillna(0.0).astype(bool)

    s_shrink = ((0.95 - v3r) / 0.35).clip(0.0, 1.0).fillna(0.0) * 40.0
    s_up = (up_max / 3.0).clip(0.0, 1.0).fillna(0.0) * 30.0
    s_nobad = V3.astype(float) * 30.0
    vph = s_shrink + s_up + s_nobad
    ok = (V1 & V2 & V3 & (vph >= cfg["vph_min"])).fillna(False)
    return ok, vph


def stop_fall(f: dict[str, pd.DataFrame], cfg: dict[str, Any]) -> pd.DataFrame:
    """⑤ 缩量止跌触发：当日量 ≤ 前 5 日均量（回调缩量 = 抛压衰竭）。"""
    return (f["V"] <= f["vma5_prev"] * cfg["trig_vol_vs_vma5"]).fillna(False)


def build_signal(f: dict[str, pd.DataFrame], pm: pd.DataFrame, cfg: dict[str, Any],
                 use_trend: bool = True, use_pull: bool = True, use_vp: bool = True,
                 use_gate: bool = True, use_trig: bool = True,
                 pull_mode: Optional[str] = None, strict_pull: bool = False) -> pd.DataFrame:
    """六层合一的入场信号矩阵。"""
    m = pm.copy()
    if use_trend:
        m &= trend_ok(f, cfg)
    if use_vp:
        m &= vp_health(f, cfg)[0]
    if use_pull:
        m &= pull_ok(f, cfg, pm=pm, mode=pull_mode, strict=strict_pull)
    if use_trig:
        m &= stop_fall(f, cfg)
    if use_gate:
        g = market_gate(cfg, f["C"].index.astype(str).tolist())
        m = m.mul(pd.Series(g, index=m.index), axis=0)
    m &= pm.notna()
    return m.fillna(False).astype(bool)


# ══════════════════════════════════════════════════════
# 四、事件回测
# ══════════════════════════════════════════════════════

def extract_events(mask: pd.DataFrame, cooldown: int) -> tuple[np.ndarray, np.ndarray]:
    """命中 (信号日行号, 列号)，同股两次信号间隔 ≥ cooldown（避免重叠样本膨胀）。"""
    arr = mask.values
    n, m = arr.shape
    ii: list[int] = []
    jj: list[int] = []
    last = np.full(m, -10 ** 9, dtype=int)
    for i in range(n):
        row = np.flatnonzero(arr[i])
        for j in row:
            if i - last[j] >= cooldown:
                ii.append(i)
                jj.append(j)
                last[j] = i
    return np.array(ii, dtype=int), np.array(jj, dtype=int)


def _fwd(Cv: np.ndarray, i0: int, j: int, h: int) -> float:
    i1 = i0 + h
    if i0 >= Cv.shape[0] or i1 >= Cv.shape[0]:
        return np.nan
    c0, c1 = Cv[i0, j], Cv[i1, j]
    return float(c1 / c0 - 1.0) if np.isfinite(c0) and np.isfinite(c1) and c0 > 0 else np.nan


def event_returns(Cv: np.ndarray, ii: np.ndarray, jj: np.ndarray,
                  cfg: dict[str, Any]) -> tuple[dict[int, np.ndarray], np.ndarray]:
    """事件未来收益（后复权，T+entry_lag 收盘成交）与持有期最大浮亏 MAE。"""
    hs = cfg["horizons"]
    rets = {h: np.full(len(ii), np.nan) for h in hs}
    mae = np.full(len(ii), np.nan)
    n = Cv.shape[0]
    for k in range(len(ii)):
        i0 = ii[k] + cfg["entry_lag"]
        if i0 >= n:
            continue
        for h in hs:
            rets[h][k] = _fwd(Cv, i0, jj[k], h)
        i1 = min(n - 1, i0 + max(hs))
        seg = Cv[i0:i1 + 1, jj[k]]
        seg = seg[np.isfinite(seg)]
        c0 = Cv[i0, jj[k]]
        if len(seg) and np.isfinite(c0) and c0 > 0:
            mae[k] = float(seg.min() / c0 - 1.0)
    return rets, mae


def bench_by_depth(Cv: np.ndarray, pm: np.ndarray, ii: np.ndarray,
                   cfg: dict[str, Any], h: int) -> np.ndarray:
    """同一入场日的池内等权收益（对齐市场择时，剥离时间效应）。"""
    out = np.full(len(ii), np.nan)
    cache: dict[int, float] = {}
    n = Cv.shape[0]
    for k in range(len(ii)):
        e = ii[k] + cfg["entry_lag"]
        if e >= n:
            continue
        if e not in cache:
            e1 = e + h
            if e1 >= n:
                cache[e] = np.nan
            else:
                sel = pm[e]
                c0, c1 = Cv[e, sel], Cv[e1, sel]
                ok = np.isfinite(c0) & np.isfinite(c1) & (c0 > 0)
                cache[e] = float((c1[ok] / c0[ok] - 1.0).mean()) if ok.any() else np.nan
        out[k] = cache[e]
    return out


def _day_portfolio(rets: np.ndarray, entry_idx: np.ndarray) -> tuple[float, float, int]:
    """按入场日聚合为等权组合 → (均值, t 值, 交易日数)。

    信号在时间上聚集（同一波回调会同时打到几十只），把事件当独立样本会
    高估显著性。先按日聚合成一个组合收益序列，再对序列求均值与 t 值。
    """
    s = pd.Series(rets).groupby(pd.Series(entry_idx)).mean().dropna()
    if len(s) < 2:
        return (float(s.mean()) if len(s) else np.nan, np.nan, len(s))
    sd = s.std(ddof=1)
    t = float(s.mean() / (sd / np.sqrt(len(s)))) if sd > 0 else np.nan
    return float(s.mean()), t, len(s)


def backtest(cfg: Optional[dict[str, Any]] = None, tag: Optional[str] = None
             ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """全样本事件回测 + 消融，返回 (事件明细, 汇总, 分年度稳健性)。"""
    cfg = {**CFG, **(cfg or {})}
    pool = ensure_pool_history()
    by_period = pool_periods(pool)

    codes_all = pool_codes(pool)
    px = build_daily(codes_all)
    w = to_wide(px)
    dates = w["C"].index.astype(str).tolist()
    codes = w["C"].columns.tolist()
    f = features(w, cfg)
    pm = pool_mask(dates, codes, by_period)
    pm_arr = pm.values
    Cv = w["C"].values
    mkt = market_series().reindex(dates).fillna(0.0)
    mkt_cum = (1.0 + mkt).cumprod().values

    log.info("[回测] 交易日 %d（%s ~ %s）| 日线覆盖 %d 只 | 平均在池 %.0f 只/日",
             len(dates), dates[0], dates[-1], len(codes), pm.sum(axis=1).mean())

    rows: list[dict[str, Any]] = []
    ev_frames: list[pd.DataFrame] = []
    stab_rows: list[dict[str, Any]] = []
    names = pool.drop_duplicates("ts_code").set_index("ts_code")

    for vname, vopt in VARIANTS.items():
        sig = build_signal(f, pm, cfg,
                           use_trend=vopt.get("trend", True),
                           use_pull=vopt.get("pull", True),
                           use_vp=vopt.get("vp", True),
                           use_gate=vopt.get("gate", True),
                           use_trig=vopt.get("trig", True),
                           pull_mode=vopt.get("pull_mode"),
                           strict_pull=vopt.get("strict_pull", False))
        ii, jj = extract_events(sig, cfg["cooldown"])
        if not len(ii):
            log.warning("[回测] 变体 %s 无信号", vname)
            continue
        rets, mae = event_returns(Cv, ii, jj, cfg)
        entry_idx = ii + cfg["entry_lag"]
        mkt_ret = {}
        for h in cfg["horizons"]:
            e1 = entry_idx + h
            ok = e1 < len(dates)
            r = np.full(len(ii), np.nan)
            r[ok] = mkt_cum[e1[ok]] / mkt_cum[entry_idx[ok]] - 1.0
            mkt_ret[h] = r
            bench = bench_by_depth(Cv, pm_arr, ii, cfg, h)
            r = rets[h]
            m, t, nd = _day_portfolio(r, entry_idx)
            bm, _, _ = _day_portfolio(bench, entry_idx)
            bmk, _, _ = _day_portfolio(mkt_ret[h], entry_idx)
            rows.append({
                "variant": vname, "horizon": h, "n_events": int(np.isfinite(r).sum()),
                "n_days": nd, "avg_ret": np.nanmean(r) * 100 if np.isfinite(r).any() else np.nan,
                "median_ret": np.nanmedian(r) * 100 if np.isfinite(r).any() else np.nan,
                "win_rate": np.nanmean(r > 0) * 100 if np.isfinite(r).any() else np.nan,
                "day_avg": m * 100 if np.isfinite(m) else np.nan, "t_stat": t,
                "bench_pool_day_avg": bm * 100 if np.isfinite(bm) else np.nan,
                "excess_vs_pool": (m - bm) * 100 if np.isfinite(m) and np.isfinite(bm) else np.nan,
                "bench_mkt_day_avg": bmk * 100 if np.isfinite(bmk) else np.nan,
                "excess_vs_mkt": (m - bmk) * 100 if np.isfinite(m) and np.isfinite(bmk) else np.nan,
                "mae": np.nanmean(mae) * 100 if np.isfinite(mae).any() else np.nan,
            })
        ev = pd.DataFrame({
            "signal_date": [dates[i] for i in ii],
            "entry_date": [dates[i] if i < len(dates) else "" for i in entry_idx],
            "ts_code": [codes[j] for j in jj],
            "variant": vname,
            "mae_40d": mae * 100,
        })
        for c in ("name", "l3_name", "subsector"):
            ev[c] = ev["ts_code"].map(names[c]) if c in names.columns else ""
        for h in cfg["horizons"]:
            ev[f"ret{h}d"] = rets[h] * 100
        ev_frames.append(ev)

        # 分年度稳健性：按入场年切分主持有期的「超额 vs 池内同日」
        hp = cfg["primary_horizon"]
        bench_p = bench_by_depth(Cv, pm_arr, ii, cfg, hp)
        yrs = np.array([dates[min(int(i), len(dates) - 1)][:4] for i in entry_idx])
        for y in sorted(set(yrs)):
            sel = yrs == y
            my, _, ny = _day_portfolio(rets[hp][sel], entry_idx[sel])
            by, _, _ = _day_portfolio(bench_p[sel], entry_idx[sel])
            stab_rows.append({"variant": vname, "year": y, "n_days": ny,
                              "ret": my * 100, "bench": by * 100, "excess": (my - by) * 100})

    events = pd.concat(ev_frames, ignore_index=True) if ev_frames else pd.DataFrame()
    summ = pd.DataFrame(rows)

    # 基准对照：池内等权「每日买入并持有」（不择时）
    for h in cfg["horizons"]:
        rr = bench_by_depth(Cv, pm_arr, np.arange(len(dates)), cfg, h)
        m, t, nd = _day_portfolio(rr, np.arange(len(dates)))
        e1 = np.arange(len(dates)) + h
        ok = e1 < len(dates)
        mk = np.full(len(dates), np.nan)
        mk[ok] = mkt_cum[e1[ok]] / mkt_cum[np.arange(len(dates))[ok]] - 1.0
        mm, _, _ = _day_portfolio(mk, np.arange(len(dates)))
        rows.append({
            "variant": "基准-池内等权买入持有", "horizon": h, "n_events": nd * int(pm.sum(axis=1).mean()),
            "n_days": nd, "avg_ret": m * 100, "median_ret": np.nan, "win_rate": np.nan,
            "day_avg": m * 100, "t_stat": t, "bench_pool_day_avg": m * 100, "excess_vs_pool": 0.0,
            "bench_mkt_day_avg": mm * 100 if np.isfinite(mm) else np.nan,
            "excess_vs_mkt": (m - mm) * 100 if np.isfinite(mm) else np.nan, "mae": np.nan,
        })
    for h in cfg["horizons"]:
        e1 = np.arange(len(dates)) + h
        ok = e1 < len(dates)
        mk = np.full(len(dates), np.nan)
        mk[ok] = mkt_cum[e1[ok]] / mkt_cum[np.arange(len(dates))[ok]] - 1.0
        mm, t, nd = _day_portfolio(mk, np.arange(len(dates)))
        rows.append({
            "variant": "基准-全市场等权买入持有", "horizon": h, "n_events": nd,
            "n_days": nd, "avg_ret": mm * 100, "median_ret": np.nan, "win_rate": np.nan,
            "day_avg": mm * 100, "t_stat": t, "bench_pool_day_avg": np.nan,
            "excess_vs_pool": np.nan, "bench_mkt_day_avg": mm * 100,
            "excess_vs_mkt": 0.0, "mae": np.nan,
        })

    summ = pd.DataFrame(rows)
    stab = pd.DataFrame(stab_rows)
    tag = tag or f"{dates[0]}_{dates[-1]}"
    ep = os.path.join(OUTPUT_DIR, f"pullback_timing_events_{tag}.csv")
    sp = os.path.join(OUTPUT_DIR, f"pullback_timing_summary_{tag}.csv")
    tp = os.path.join(OUTPUT_DIR, f"pullback_timing_stability_{tag}.csv")
    events.to_csv(ep, index=False, encoding="utf-8-sig")
    summ.to_csv(sp, index=False, encoding="utf-8-sig")
    stab.to_csv(tp, index=False, encoding="utf-8-sig")
    log.info("[回测] 事件明细 → %s", ep)
    log.info("[回测] 汇总 → %s", sp)
    log.info("[回测] 分年度稳健性 → %s", tp)
    return events, summ, stab


# ══════════════════════════════════════════════════════
# 五、参数敏感性
# ══════════════════════════════════════════════════════

GRID = {
    "pull_dd_max": [0.04, 0.06, 0.08, 0.12],
    "leg_gain": [0.15, 0.20, 0.25, 0.30],
    "vph_min": [50.0, 60.0, 70.0],
    "gate_ma": [10, 20, 40],
}


def grid_scan(base_cfg: Optional[dict[str, Any]] = None) -> pd.DataFrame:
    """逐参数网格：看事件数 / 收益 / 相对池内等权超额在 20、40 日是否稳健。"""
    base = {**CFG, **(base_cfg or {})}
    pool = ensure_pool_history()
    by_period = pool_periods(pool)
    px = build_daily(pool_codes(pool))
    w = to_wide(px)
    dates = w["C"].index.astype(str).tolist()
    codes = w["C"].columns.tolist()
    pm = pool_mask(dates, codes, by_period)
    pm_arr, Cv = pm.values, w["C"].values
    mkt = market_series().reindex(dates).fillna(0.0)
    mkt_cum = (1.0 + mkt).cumprod().values

    rows: list[dict[str, Any]] = []
    for key, vals in GRID.items():
        for v in vals:
            cfg = {**base, key: v}
            f = features(w, cfg)
            sig = build_signal(f, pm, cfg)
            ii, jj = extract_events(sig, cfg["cooldown"])
            if not len(ii):
                rows.append({"param": key, "value": v, "n_events": 0})
                continue
            rets, mae = event_returns(Cv, ii, jj, cfg)
            e = ii + cfg["entry_lag"]
            rows.append({"param": key, "value": v, "n_events": len(ii)})
            o = rows[-1]
            for h in (20, 40):
                r = rets[h]
                m, t, nd = _day_portfolio(r, e)
                bm, _, _ = _day_portfolio(bench_by_depth(Cv, pm_arr, ii, cfg, h), e)
                o[f"ret{h}d"] = np.nanmean(r) * 100 if np.isfinite(r).any() else np.nan
                o[f"win{h}"] = np.nanmean(r > 0) * 100 if np.isfinite(r).any() else np.nan
                o[f"excess{h}"] = (m - bm) * 100 if np.isfinite(m) and np.isfinite(bm) else np.nan
                o[f"t{h}"] = t
                o[f"n_days{h}"] = nd
            o["mae"] = np.nanmean(mae) * 100 if np.isfinite(mae).any() else np.nan
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════
# 六、实盘扫描
# ══════════════════════════════════════════════════════

def scan(asof: Optional[str] = None, cfg: Optional[dict[str, Any]] = None) -> pd.DataFrame:
    """对最新（或指定）交易日的池内标的做回踩择时扫描。"""
    from .reader import get_next_leaders
    cfg = {**CFG, **(cfg or {})}
    pool = get_next_leaders(asof)
    if pool.empty:
        log.warning("[扫描] 池为空")
        return pd.DataFrame()
    meta = pool.attrs.get("_sli_meta", {})
    snap = str(meta.get("snapshot_date", asof or ""))
    codes = pool["ts_code"].tolist()
    px = build_daily(codes)
    w = to_wide(px)
    dates = w["C"].index.astype(str).tolist()
    d = asof or dates[-1]
    if d not in dates:
        d = dates[-1]
    f = features(w, cfg)
    i = dates.index(d)
    sub = slice(None, i + 1)
    f_now = {k: v.iloc[sub] for k, v in f.items()}
    col = [c for c in w["C"].columns if c in set(codes)]
    f_now = {k: v[col] for k, v in f_now.items()}
    pm = pd.DataFrame(True, index=f_now["C"].index, columns=col)

    gate_now = bool(market_gate(cfg, f_now["C"].index.astype(str).tolist())[-1])
    layers = {
        "trend": trend_ok(f_now, cfg).iloc[-1],
        "pull": pull_ok(f_now, cfg, pm=pm).iloc[-1],
        "vp": vp_health(f_now, cfg)[0].iloc[-1],
        "trigger": stop_fall(f_now, cfg).iloc[-1],
    }
    vph = vp_health(f_now, cfg)[1].iloc[-1]
    sig = build_signal(f_now, pm, cfg).iloc[-1]

    want = pool.set_index("ts_code")
    rows = []
    for c in col:
        if c not in want.index:
            continue
        r = want.loc[c]
        close = f_now["C"][c].iloc[-1]
        stop = f_now["ll20"][c].iloc[-1]
        # 单笔风险 = (入场价 − 止损价) / 入场价；仓位 = 风险预算 / 单笔风险。
        # 止损位保持结构位（20 日最低价）不动，极端止损距离靠仓位折算消化，
        # 而不是收紧止损位 —— 后者会把 41% 的交易在噪声里打掉（见模块 docstring）。
        stop_dist = max(cfg["pull_dd_min"], 1.0 - stop / close)
        rows.append({
            "signal_date": d, "ts_code": c,
            "name": r.get("name", ""), "l3_name": r.get("l3_name", ""),
            "subsector": r.get("subsector", ""),
            "mid_long_score": r.get("mid_long_score", np.nan),
            "close": close,
            "dist_hh20_pct": (close / f_now["hh20"][c].iloc[-1] - 1.0) * 100,
            "days_since_high": f_now["days_since_hh20"][c].iloc[-1],
            "leg_gain_60d_pct": (f_now["hh60"][c].iloc[-1] / f_now["ll60"][c].iloc[-1] - 1.0) * 100,
            "vol3_vs_ma20": (f_now["v3"][c].iloc[-1] / f_now["vma20"][c].iloc[-1]),
            "vph": vph[c], "trend": bool(layers["trend"][c]), "pull": bool(layers["pull"][c]),
            "vp_ok": bool(layers["vp"][c]), "trigger": bool(layers["trigger"][c]),
            "mkt_gate": gate_now,
            "buy": bool(sig[c]) and gate_now,
            "stop_loss": stop,
            "stop_dist_pct": -stop_dist * 100,
            "position_pct": min(cfg["max_position"], cfg["risk_budget"] / stop_dist) * 100,
            "target_8pct": close * 1.08,
        })
    out = pd.DataFrame(rows)
    out = out.sort_values(["buy", "vph"], ascending=[False, False]).reset_index(drop=True)
    p = os.path.join(OUTPUT_DIR, f"pullback_timing_scan_{d}.csv")
    out.to_csv(p, index=False, encoding="utf-8-sig")
    log.info("[扫描] %s（池快照 %s）%d 只 → 买入信号 %d 只 → %s",
             d, snap, len(out), int(out["buy"].sum()), p)
    return out


# ══════════════════════════════════════════════════════
# 七、落库（统一跟踪表 stock_pick_db）
# ══════════════════════════════════════════════════════

PICK_STRATEGY_ID = "sli_pullback_timing"
PICK_STRATEGY_NAME = "看长做短 · 回踩择时"


def _adj_ratio(d: str, codes: Iterable[str]) -> dict[str, float]:
    """信号日「后复权价 / 原始价」的换算系数（即当日 adj_factor）。

    跟踪表 pick_tracking 用共享行情库 daily_cache 的原始价回填 base_close 与
    止损/目标命中，故落库的价格必须是原始价，否则两套价格不同量纲会立刻
    误判成 STOP_HIT。取当日 daily_<d>.parquet 的原始收盘价作分母。
    """
    p = os.path.join(CACHE_DIR, f"daily_{d}.parquet")
    if not os.path.exists(p):
        return {}
    df = pd.read_parquet(p)
    df = df[df["ts_code"].isin(set(codes))]
    raw = dict(zip(df["ts_code"].astype(str), pd.to_numeric(df["close"], errors="coerce")))
    return {c: v for c, v in raw.items() if v and v > 0}


def sync_pick_db(out: pd.DataFrame, d: str) -> int:
    """把扫描结果里的「可买」与「形态就绪」两类信号落库 stock_pick_db（幂等）。

    口径：两类都用 signal 区分 ——
      · 可买     ：六层全满足（趋势 + 回踩 + 量价 + 缩量触发 + 市场闸门开启）；
      · 形态就绪 ：趋势 + 回踩 + 量价已就绪，仅缺缩量触发或市场闸门关闭，次日盯。
    action 相应给 BUY / WATCH；策略自有字段（vph / 距高点回撤 / 60 日波段涨幅 /
    量比 / 各层布尔 / 闸门）进 indicators JSON，随后由
    `python stock_pick_db.py tracking` 按 pick_date 回填 T+N 收益与止损/目标命中。
    """
    if out.empty:
        print("[db] 扫描无结果, 跳过落库")
        return 0
    near = ((~out["buy"]) & out["trend"] & out["pull"] & out["vp_ok"]).tolist()
    raw = _adj_ratio(d, out["ts_code"].astype(str))
    rows = []
    for i, (_, r) in enumerate(out.iterrows()):
        is_buy = bool(r["buy"])
        if not is_buy and not near[i]:
            continue
        rc = raw.get(str(r["ts_code"]))
        if not rc:
            print(f"[db] {r['ts_code']} 缺 {d} 原始价, 跳过该条")
            continue
        k = float(r["close"]) / rc          # 后复权 → 原始
        why = []
        if not is_buy:
            if not r["trigger"]:
                why.append("缺缩量触发")
            if not r["mkt_gate"]:
                why.append("市场闸门关闭")
        rows.append({
            "ts_code": str(r["ts_code"]),
            "stock_name": str(r.get("name") or ""),
            "close": rc,
            "signal": "可买" if is_buy else "形态就绪",
            "action": "BUY" if is_buy else "WATCH",
            "score": r["mid_long_score"],
            "rank_no": i + 1,
            "industry": str(r.get("l3_name") or ""),
            "reason": "六层全满足" if is_buy else ("+".join(why) or "形态就绪"),
            "stop_price": float(r["stop_loss"]) / k,
            "target_price": float(r["target_8pct"]) / k,
            "position_pct": float(r["position_pct"]),
            "signal_date": d,
            "close_adj": float(r["close"]), "adj_factor": k,
            "stop_dist_pct": r["stop_dist_pct"],
            "dist_hh20_pct": r["dist_hh20_pct"],
            "days_since_high": r["days_since_high"],
            "leg_gain_60d_pct": r["leg_gain_60d_pct"],
            "vol3_vs_ma20": r["vol3_vs_ma20"],
            "vph": r["vph"],
            "trend": bool(r["trend"]), "pull": bool(r["pull"]),
            "vp_ok": bool(r["vp_ok"]), "trigger": bool(r["trigger"]),
            "mkt_gate": bool(r["mkt_gate"]),
            "subsector": str(r.get("subsector") or ""),
        })
    if not rows:
        print("[db] 无「可买 / 形态就绪」信号, 跳过落库")
        return 0
    try:
        from stock_pick_db import record_picks
    except Exception as exc:
        print(f"[db] stock_pick_db 不可用, 跳过落库: {exc}")
        return 0
    try:
        n = record_picks(PICK_STRATEGY_ID, PICK_STRATEGY_NAME, rows, pick_date=d)
    except Exception as exc:
        print(f"[db] stock_pick_db 写入失败(不影响扫描): {exc}")
        return 0
    nb = sum(1 for r in rows if r["signal"] == "可买")
    print(f"[db] stock_pick_db 写入 {n}/{len(rows)} 条 (strategy={PICK_STRATEGY_ID} "
          f"pick_date={d}; 可买 {nb} + 形态就绪 {len(rows) - nb})")
    return n


# ══════════════════════════════════════════════════════
# 八、报告
# ══════════════════════════════════════════════════════

def print_summary(summ: pd.DataFrame) -> None:
    show = summ[summ["variant"] != "基准-全市场等权买入持有"]
    print("\n" + "=" * 118)
    print("看长做短 · 回踩择时 —— 事件回测（POOL_MIDLONG_V2，后复权，T+1 收盘成交，同股 20 日冷却）")
    print("=" * 118)
    print(f"{'变体':<32}{'持有':>5}{'事件':>6}{'交易日':>7}{'日均收益':>9}"
          f"{'胜率':>7}{'t值':>7}{'池内同日':>9}{'超额':>8}{'vs市场':>8}{'MAE':>8}")
    for _, r in show.iterrows():
        print(f"{r['variant']:<32}{int(r['horizon']):>5}{int(r['n_events']):>6}"
              f"{int(r['n_days']):>7}{r['day_avg']:>8.2f}%{r['win_rate']:>6.0f}%"
              f"{r['t_stat']:>7.2f}{r['bench_pool_day_avg']:>8.2f}%"
              f"{r['excess_vs_pool']:>7.2f}%{r['excess_vs_mkt']:>7.2f}%"
              f"{r['mae']:>7.2f}%")
    print("-" * 118)
    print("口径：'日均收益'=先按入场日聚合成等权组合再求均值（避免同期信号重叠夸大显著性）；")
    print("      '池内同日'=同一天池内全部成员等权买入持有的 20/40 日收益；")
    print("      '超额'=日均收益 − 池内同日（剥离池本身的 beta 与入场时点）；")
    print("      'MAE'=持有 40 日内的最大浮亏均值。")
    print("=" * 118)


def print_stability(stab: pd.DataFrame) -> None:
    """各变体的分年度 20 日超额（%）：看口径是否只在个别年份有效。"""
    if stab.empty:
        return
    piv = stab.pivot(index="variant", columns="year", values="excess")
    nyr = stab.pivot(index="variant", columns="year", values="n_days")
    years = list(piv.columns)
    print(f"\n分年度 20 日超额（%，括号内为该年入场交易日数）—— 判定口径是否靠个别年份")
    print(f"{'变体':<32}" + "".join(f"{y:>12}" for y in years) + f"{'均为正':>8}")
    for v in piv.index:
        cells = []
        allp = True
        for y in years:
            e, n = piv.loc[v, y], nyr.loc[v, y]
            if pd.isna(e):
                cells.append(f"{'—':>12}")
                continue
            allp &= e > 0
            cells.append(f"{e:>8.2f}({int(n):>2})")
        print(f"{v:<32}" + "".join(cells) + f"{'✓' if allp else '×':>8}")
    print("=" * 118)


def main() -> None:
    ap = argparse.ArgumentParser(description="看长做短 · 回踩择时（POOL_MIDLONG_V2）")
    ap.add_argument("--init", action="store_true", help="仅生成池历史快照缓存")
    ap.add_argument("--backtest", action="store_true", help="全样本回测 + 消融")
    ap.add_argument("--grid", action="store_true", help="参数敏感性网格")
    ap.add_argument("--scan", action="store_true", help="实盘扫描最新交易日")
    ap.add_argument("--date", type=str, default=None, help="扫描指定交易日 YYYYMMDD")
    ap.add_argument("--pull-mode", dest="pull_mode", default=None,
                    choices=["shallow", "rs", "shallow+rs"], help="④ 回踩口径（覆盖 CFG）")
    ap.add_argument("--force", action="store_true", help="强制重建缓存")
    ap.add_argument("--tag", type=str, default=None, help="输出文件名后缀")
    args = ap.parse_args()

    setup_logging(LOG_DIR, "sli.pullback_timing")
    overrides = {"pull_mode": args.pull_mode} if args.pull_mode else None

    if args.init or args.force:
        ensure_pool_history(force=args.force)
        if args.force and os.path.exists(DAILY_PATH):
            os.remove(DAILY_PATH)
        if args.init and not (args.backtest or args.scan or args.grid):
            return

    if args.scan:
        out = scan(args.date, cfg=overrides)
        if not out.empty:
            b = out[out["buy"]]
            g = bool(out["mkt_gate"].iloc[0])
            print(f"\n回踩择时信号 {out['signal_date'].iloc[0]}：池 {len(out)} 只 → 可买 {len(b)} 只"
                  f"（市场闸门 {'开启' if g else '关闭'}）")
            if not g:
                print("  注意：市场闸门处于关闭状态，本次不新开仓。")
            if len(b):
                cols = ["ts_code", "name", "l3_name", "mid_long_score", "close",
                        "dist_hh20_pct", "leg_gain_60d_pct",
                        "vol3_vs_ma20", "vph", "stop_loss", "stop_dist_pct",
                        "position_pct", "target_8pct"]
                print(b[cols].to_string(index=False))
                print(f"  position_pct = 单笔风险预算 {CFG['risk_budget']:.1%} ÷ 该股止损距离，"
                      f"上限 {CFG['max_position']:.0%}；止损距离越远仓位越小。")
            near = out[(~out["buy"]) & out["trend"] & out["pull"] & out["vp_ok"]]
            print(f"\n形态已就绪、仅缺缩量触发（次日盯）：{len(near)} 只")
            if len(near):
                print(near[["ts_code", "name", "l3_name", "close", "dist_hh20_pct",
                            "vol3_vs_ma20", "vph"]].to_string(index=False))
            sync_pick_db(out, str(out["signal_date"].iloc[0]))
        return

    if args.grid:
        g = grid_scan(overrides)
        p = os.path.join(OUTPUT_DIR, f"pullback_timing_grid_{args.tag or 'latest'}.csv")
        g.to_csv(p, index=False, encoding="utf-8-sig")
        print("\n=== 参数敏感性（20 日口径）===")
        print(g.to_string(index=False))
        print(f"\n→ {p}")
        return

    if args.backtest or not any([args.init, args.scan, args.grid]):
        _, summ, stab = backtest(overrides, tag=args.tag)
        print_summary(summ)
        print_stability(stab)


if __name__ == "__main__":
    main()
