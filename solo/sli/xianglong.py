# -*- coding: utf-8 -*-
"""
降龙一掌 —— 主线板块基本面龙头 · 首次缩量回调低吸策略
=====================================================

【策略思想】
  只做"主线中最强的基本面龙头"，在它第一次缩量回调、缩量止跌时低吸，
  赚情绪惯性与资金回流的一段短波，5 个交易日内了结。

【三段式信号】（收盘判定 → 次日开盘买入）
  ① 强势确认：近 10 个交易日涨幅位于全市场前 20%
  ② 首次缩量回调：
       - 最近 2~4 个交易日连续缩量（每日 vol < 前一日，且最新 vol < 5日均量×0.8）
       - 回调期间收盘价始终未跌破 10 日均线
       - 回调开始前的 5 个交易日内，至少一根涨幅>5% 的大阳线或涨停
       - 最近 10 个交易日内该股未出现过同类信号（保证"首次"）
  ③ 缩量止跌（信号日 K 线形态，二选一）：
       - 下影线长度 > 实体长度
       - 十字星（|收-开|/昨收 < 0.3%）且量能萎缩
  ※ 备选入场方式（本代码未启用）：次日放量重新站上 5 日均线时买入。
    如需启用，在 run_backtest 的买入段增加"次日 vol > 1.5×vol_ma5 且
    qclose 上穿 ma5"的条件，把 buy_date 顺延一天即可。

【卖出规则】（收盘判定 → 次日开盘执行，最短 T+1）
  止损   收盘跌破 MA10，或收盘较买价亏损 ≥ 4%           → 次日开盘全部卖出
  止盈1  持仓第 3 个交易日收盘涨幅 ≥ 5%                 → 次日开盘卖出 50%
  止盈2  持仓第 5 个交易日收盘距前高（买入前 20 日高点）< 1% → 次日开盘全部卖出
  横盘   持仓第 5 个交易日累计涨幅 < 1% 且未触发止损    → 次日开盘全部卖出
  兜底   持仓超过 5 个交易日仍未离场                    → 次日开盘全部卖出

【回测设置】
  默认纯信号模式：不做仓位控制，每条信号独立模拟完整生命周期
    （每笔固定名义 10 万，仅影响盈亏金额展示；同股多信号互不影响）
  组合模式（--portfolio）：初始资金 100 万；买入万 2.5；卖出万 2.5 + 印花税千 0.5
    滑点 0.1%；单票最大仓位 = 总资产 20%；同时最多持有 3 只

【数据说明】
  主数据：sli/cache/daily_YYYYMMDD.parquet（tushare daily 全市场缓存，
          2022-03-07 起，含 close/pre_close/pct_chg/vol/amount/high/low）
  复权：  用 pct_chg 链构建"后复权价"（等价于含分红再投资的总回报序列）。
          所有均线 / 止损 / 前高 / 收益率均在后复权空间计算，
          比例关系与真实行情完全一致，且天然规避除权跳空失真。
  开盘价：daily 缓存无 open 列 → 用通达信 .day 文件的真实开盘价，
          按 open/close 比例还原到后复权空间；TDX 缺失时以昨收近似。
  股票池：sli/output/sli_full_*.csv 中 leader_type_v2 != 'NONE' 的细分龙头
  基准：  通达信 sh000300.day（沪深300 指数全历史，指数无复权问题）

【用法】
  python -X utf8 sli/xianglong.py --demo                     # 模拟数据自检
  python -X utf8 sli/xianglong.py                            # 真实数据回测（默认窗口）
  python -X utf8 sli/xianglong.py --start 20240101 --end 20260904
  python -X utf8 sli/xianglong.py --date 20260904            # 仅生成当日信号清单
"""
import os
import sys
import glob
import struct
import argparse
from collections import defaultdict

import numpy as np
import pandas as pd

# ── 控制台中文输出保护 ──────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── 项目路径（与 f120_daily.py 相同的导入模式） ─────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from sli.config import CACHE_DIR, OUTPUT_DIR  # noqa: E402

# ════════════════════════════════════════════════════════
# §1 策略参数（全部集中于此，便于调参）
# ════════════════════════════════════════════════════════
TDX_DIR     = r"C:\new_tdx"                                   # 通达信安装目录
POOL_GLOB   = os.path.join(OUTPUT_DIR, "sli_full_*.csv")      # SLI 龙头池

INIT_CAPITAL  = 1_000_000.0     # 初始资金（仅组合模式 --portfolio 使用）
SIGNAL_NOTIONAL = 100_000.0     # 纯信号模式：每笔信号固定名义金额（仅影响 pnl 展示）
BUY_FEE       = 0.00025         # 买入佣金 万2.5
SELL_FEE      = 0.00025         # 卖出佣金 万2.5
STAMP_TAX     = 0.0005          # 印花税 千0.5（仅卖出）
SLIPPAGE      = 0.001           # 滑点 0.1%
MAX_POS_PCT   = 0.20            # 单票最大仓位（占总资产）
MAX_HOLDINGS  = 3               # 同时最多持有只数

TREND_WINDOW    = 10            # ① 强势确认回看窗口（交易日）
TREND_TOP_PCT   = 0.20          # ① 全市场分位阈值（前 20%）
PULLBACK_MIN    = 2             # ② 回调最短天数
PULLBACK_MAX    = 4             # ② 回调最长天数
VOL_SHRINK_MA5  = 0.80          # ② 最新量 < 5日均量 × 0.8
STRONG_BAR_PCT  = 5.0           # ② 大阳线阈值（单日涨幅 %）
STRONG_BAR_WIN  = 5             # ② 回调前回看大阳线的窗口（交易日）
SIGNAL_COOLDOWN = 10            # ② "首次"冷却期（交易日内不重复出信号）
DOJI_BODY_PCT   = 0.003         # ③ 十字星实体阈值（相对昨收 0.3%）

STOP_LOSS_PCT   = 0.04          # 止损：亏损 4%
TAKE_PROFIT_D3  = 0.05          # 第3日止盈：涨幅 5%
SELL_HALF_PCT   = 0.50          # 止盈卖出比例 50%
NEAR_HIGH_PCT   = 0.01          # 第5日：距前高 1% 以内全卖
SIDEWAYS_PCT    = 0.01          # 第5日：累计涨幅 < 1% 视为横盘全卖
MAX_HOLD_DAYS   = 5             # 最长持仓交易日
PREHIGH_WINDOW  = 20            # 前高回看窗口（买入前 20 日高点）
MIN_LIST_DAYS   = 60            # 上市（有数据）不足 60 日不出信号

WARMUP_DAYS     = 40            # 回测窗口前的最小预热数据量


# ════════════════════════════════════════════════════════
# §2 数据层：TDX 解析 / 龙头池 / 全市场缓存面板
# ════════════════════════════════════════════════════════

def ts_code_to_tdx_file(ts_code: str) -> str | None:
    """600000.SH -> C:/new_tdx/vipdoc/sh/lday/sh600000.day"""
    if "." not in ts_code:
        return None
    sym, mkt = ts_code.split(".")
    if mkt not in ("SH", "SZ"):
        return None
    p = mkt.lower()
    return os.path.join(TDX_DIR, "vipdoc", p, "lday", f"{p}{sym}.day")


def parse_tdx_day_file(path: str, min_date: str = "0") -> pd.DataFrame | None:
    """解析通达信 .day 二进制（每条 32 字节小端）。

    字段: 日期YYYYMMDD / 开高低收×100(int32) / 成交额(float32元) / 成交量(手)
    返回按日期升序的 DataFrame，仅保留 trade_date >= min_date 的记录。
    """
    if not path or not os.path.exists(path):
        return None
    buf = open(path, "rb").read()
    n = len(buf) // 32
    if n == 0:
        return None
    rows = []
    for i in range(n):
        d, o, h, l, c, amt, vol, _r = struct.unpack_from("<IIIIIfII", buf, i * 32)
        if str(d) >= min_date:
            rows.append((str(d), o / 100.0, h / 100.0, l / 100.0, c / 100.0, vol))
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["trade_date", "open", "high", "low", "close", "vol"])
    return df.sort_values("trade_date").reset_index(drop=True)


def load_pool() -> pd.DataFrame:
    """加载最新一期 SLI 股票池，仅保留细分龙头（leader_type_v2 != NONE）。"""
    files = sorted(glob.glob(POOL_GLOB))
    if not files:
        raise FileNotFoundError(f"未找到 SLI 股票池: {POOL_GLOB}，请先运行 SLI 引擎生成")
    df = pd.read_csv(files[-1], dtype=str)
    if "leader_type_v2" not in df.columns:
        raise KeyError("股票池缺少 leader_type_v2 列")
    df["leader_type_v2"] = df["leader_type_v2"].fillna("NONE")
    pool = df[df["leader_type_v2"] != "NONE"][
        ["ts_code", "name", "l1_name", "l3_name"]].drop_duplicates("ts_code")
    # 排除北交所（与 SLI 配置 INCLUDE_BJ=False 保持一致）
    pool = pool[~pool["ts_code"].str.endswith(".BJ")]
    print(f"[股票池] {os.path.basename(files[-1])} → 细分龙头 {len(pool)} 只")
    return pool.reset_index(drop=True)


def load_market_daily(start: str = "0", end: str = "9") -> pd.DataFrame:
    """合并全部 daily_YYYYMMDD.parquet 缓存为全市场日线长表。

    注意用 8 位数字精确匹配文件名，避免误读 daily_basic_*。
    """
    pat = os.path.join(CACHE_DIR, "daily_[0-9]" * 1 + "[0-9]" * 7 + ".parquet")
    pat = pat.replace("[0-9]" * 8, "[0-9]" * 8)  # 保持可读
    files = [f for f in glob.glob(os.path.join(CACHE_DIR, "daily_*.parquet"))
             if os.path.basename(f).startswith("daily_")
             and os.path.basename(f)[6:14].isdigit()]
    files = [f for f in files if start <= os.path.basename(f)[6:14] <= end]
    if not files:
        raise FileNotFoundError(f"{CACHE_DIR} 下无 daily_YYYYMMDD.parquet 缓存")
    parts = [pd.read_parquet(f) for f in sorted(files)]
    mkt = pd.concat(parts, ignore_index=True)
    mkt = mkt.drop_duplicates(["ts_code", "trade_date"]).sort_values(
        ["trade_date", "ts_code"]).reset_index(drop=True)
    print(f"[全市场] {len(files)} 个交易日缓存, {mkt['ts_code'].nunique()} 只股票, "
          f"{os.path.basename(sorted(files)[0])[6:14]} → {os.path.basename(files[-1])[6:14]}")
    return mkt


def build_market_panel(mkt: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """构建全市场矩阵：
      qclose_panel  后复权收盘矩阵  (trade_date × ts_code)
      rank_panel    ret10 全市场分位矩阵（0~1，越大越强）
      trade_dates   交易日历（升序）
    后复权链：q[t] = q[t-1] × close[t]/pre_close[t]（停牌/缺失日 ret=0）
    """
    close_pv = mkt.pivot_table(index="trade_date", columns="ts_code",
                               values="close", aggfunc="last")
    pct_pv = mkt.pivot_table(index="trade_date", columns="ts_code",
                             values="pct_chg", aggfunc="last")
    trade_dates = sorted(close_pv.index.tolist())
    ret = (pct_pv / 100.0).fillna(0.0)          # 停牌日/缺失日收益记 0
    qclose = (1.0 + ret).cumprod()              # 归一化后复权链（起点=1）
    ret10 = qclose / qclose.shift(TREND_WINDOW) - 1.0
    rank = ret10.rank(axis=1, pct=True, ascending=True).astype("float32")
    return qclose, rank, trade_dates


def load_tdx_open_map(codes: list[str]) -> dict[str, dict[str, float]]:
    """批量读取池内股票的通达信 .day，构建 {ts_code: {trade_date: open_real}}。

    用途：daily 缓存无 open 列，用 TDX 真实开盘价按 open/close 比例
    还原后复权开盘价；当日除权不影响"日内 open/close 比例"的正确性。
    """
    out: dict[str, dict[str, float]] = {}
    hit = 0
    for code in codes:
        df = parse_tdx_day_file(ts_code_to_tdx_file(code), min_date="20220101")
        if df is not None and len(df):
            out[code] = dict(zip(df["trade_date"], df["open"]))
            hit += 1
    print(f"[TDX开盘价] 命中 {hit}/{len(codes)} 只（缺失者以昨收近似今开）")
    return out


def build_stock_frames(mkt: pd.DataFrame, pool: pd.DataFrame,
                       rank: pd.DataFrame,
                       tdx_open: dict | None) -> dict[str, dict]:
    """为池内每只股票构建后复权行情帧（含全部指标），供信号与回测使用。

    每帧包含列:
      trade_date/open/high/low/close/vol   原始（未复权）行情
      qopen/qhigh/qlow/qclose              后复权价
      pct_chg / limit_up                   涨跌幅 / 涨停标志
      ma10 / ma5v                          10日均价线 / 5日均量线
      rank_pct                             ret10 全市场分位（强势确认用）
    返回 {ts_code: {"df": df, "idx": {date: 行号}, "arr": numpy数组包}}
    """
    codes = set(pool["ts_code"])
    frames: dict[str, dict] = {}
    for code, g in mkt[mkt["ts_code"].isin(codes)].groupby("ts_code"):
        g = g.sort_values("trade_date").reset_index(drop=True)
        if len(g) < MIN_LIST_DAYS:
            continue
        close = g["close"].to_numpy(float)
        pre = g["pre_close"].to_numpy(float)
        # ── 后复权链（起点=首日收盘） ──
        r = np.where(pre > 0, close / np.where(pre > 0, pre, 1.0), 1.0)
        qclose = np.concatenate([[close[0] if close[0] > 0 else 1.0],
                                 (1.0 + np.nan_to_num(g["pct_chg"].to_numpy(float)[1:] / 100.0))])
        qclose = close[0] * np.cumprod(
            np.concatenate([[1.0], np.nan_to_num(g["pct_chg"].to_numpy(float)[1:] / 100.0) + 1.0]))
        # ── 开盘价：优先 TDX 真实 open，缺失以昨收近似 ──
        open_real = g["trade_date"].map((tdx_open or {}).get(code, {})).to_numpy(float)
        fallback = np.concatenate([[np.nan], close[:-1]])          # 昨收
        open_real = np.where(np.isfinite(open_real) & (open_real > 0), open_real, fallback)
        ratio = np.where(close > 0, open_real / close, 1.0)
        qopen = qclose * ratio
        qhigh = qclose * np.where(close > 0, g["high"].to_numpy(float) / close, 1.0)
        qlow = qclose * np.where(close > 0, g["low"].to_numpy(float) / close, 1.0)
        pct = g["pct_chg"].to_numpy(float)
        # ── 涨停标志（主板≈10%，创业板/科创板≈20%） ──
        lim_th = 19.5 if code[:2] in ("30", "68") else 9.8
        limit = pct >= lim_th
        g = g.assign(
            qopen=qopen, qhigh=qhigh, qlow=qlow, qclose=qclose,
            ma10=pd.Series(qclose).rolling(10).mean().to_numpy(),
            ma5v=pd.Series(g["vol"].to_numpy(float)).rolling(5).mean().to_numpy(),
            rank_pct=g["trade_date"].map(rank[code]).to_numpy(float),
            limit_up=limit,
        )
        frames[code] = {
            "df": g,
            "idx": dict(zip(g["trade_date"].tolist(), range(len(g)))),
            "arr": {
                "date": g["trade_date"].to_numpy(),
                "qo": qopen, "qh": qhigh, "ql": qlow, "qc": qclose,
                "vol": g["vol"].to_numpy(float), "ma10": g["ma10"].to_numpy(float),
                "ma5v": g["ma5v"].to_numpy(float),
                "rank": g["rank_pct"].to_numpy(float),
                "pct": pct, "limit": limit,
            },
        }
    return frames


# ════════════════════════════════════════════════════════
# §3 信号引擎：强势确认 → 首次缩量回调 → 缩量止跌
# ════════════════════════════════════════════════════════

def generate_signals(frames: dict[str, dict], pool: pd.DataFrame) -> pd.DataFrame:
    """对池内每只股票扫描三段式信号。

    返回信号表: ts_code / signal_date(信号日) / buy_date(次日买入日)
                sidx(帧内行号) / k_days(回调天数) / ret10 / rank_pct / vol_ratio
    """
    meta = pool.set_index("ts_code")[["name", "l1_name", "l3_name"]].to_dict("index")
    rows = []
    for code, fr in frames.items():
        a = fr["arr"]
        n = len(a["date"])
        last_sig = -(10 ** 9)                       # 冷却计数：距上次信号日
        for t in range(MIN_LIST_DAYS, n - 1):
            # ── ① 强势确认：ret10 位于全市场前 20% ──
            rk = a["rank"][t]
            if not np.isfinite(rk) or rk < 1.0 - TREND_TOP_PCT:
                continue
            if a["vol"][t] <= 0 or t - last_sig <= SIGNAL_COOLDOWN:
                continue
            qc, qo, ql = a["qc"][t], a["qo"][t], a["ql"][t]
            qc1, ma10 = a["qc"][t - 1], a["ma10"][t]
            if not np.isfinite(ma10) or qc < ma10:   # 当日已破 MA10，直接排除
                continue
            # ── ③ 缩量止跌形态（先判便宜的，再回溯回调段） ──
            body = abs(qc - qo)
            lower_shadow = min(qo, qc) - ql
            doji = body <= DOJI_BODY_PCT * qc1
            shrunk = a["vol"][t] < VOL_SHRINK_MA5 * a["ma5v"][t]
            if not ((lower_shadow > body and lower_shadow > 0) or (doji and shrunk)):
                continue
            # ── ② 首次缩量回调段回溯（k = 2~4 天） ──
            sig_k = 0
            for k in range(PULLBACK_MIN, PULLBACK_MAX + 1):
                s = t - k + 1                        # 回调段起点
                if s - STRONG_BAR_WIN < 0:
                    break
                ok = True
                prev_v = a["vol"][s - 1]
                for i in range(s, t + 1):            # 段内逐日缩量 + 未破 MA10
                    if a["vol"][i] >= prev_v or a["qc"][i] < a["ma10"][i]:
                        ok = False
                        break
                    prev_v = a["vol"][i]
                if not ok or not (a["vol"][t] < VOL_SHRINK_MA5 * a["ma5v"][t]):
                    continue
                # 前期强势：回调前 5 日内有大阳线(>5%)或涨停
                lo, hi = t - k + 1 - STRONG_BAR_WIN, t - k
                if lo < 0:
                    continue
                if (a["pct"][lo:hi + 1].max() <= STRONG_BAR_PCT
                        and not a["limit"][lo:hi + 1].any()):
                    continue
                sig_k = k                            # 取最短满足的 k
                break
            if sig_k == 0:
                continue
            rows.append({
                "ts_code": code,
                "name": meta.get(code, {}).get("name", ""),
                "l1_name": meta.get(code, {}).get("l1_name", ""),
                "l3_name": meta.get(code, {}).get("l3_name", ""),
                "signal_date": a["date"][t],
                "buy_date": a["date"][t + 1],
                "sidx": t,
                "k_days": sig_k,
                "ret10": round((qc / a["qc"][t - TREND_WINDOW] - 1) * 100, 2),
                "rank_pct": round(rk, 4),
                "vol_ratio": round(a["vol"][t] / a["ma5v"][t], 3)
                if a["ma5v"][t] > 0 else np.nan,
            })
            last_sig = t
    sig = pd.DataFrame(rows)
    if len(sig):
        sig = sig.sort_values(["signal_date", "rank_pct"],
                              ascending=[True, False]).reset_index(drop=True)
    print(f"[信号] 共扫描 {len(frames)} 只龙头, 生成信号 {len(sig)} 条")
    return sig


# ════════════════════════════════════════════════════════
# §4 回测引擎（事件驱动：收盘判定 → 次日开盘执行）
# ════════════════════════════════════════════════════════

def run_backtest(frames: dict[str, dict], signals: pd.DataFrame,
                 trade_dates: list[str], start: str, end: str) -> tuple[pd.Series, pd.DataFrame]:
    """按市场日历推进的组合回测。

    每日流程:
      1) 卖出：以"昨日收盘"判定止损/止盈/横盘，今日开盘价执行
      2) 买入：昨日信号列表按 rank_pct 降序，今日开盘价买入
      3) 收盘按后复权价 mark-to-market
    全部成交、现金、市值均在后复权空间记账（收益率语义 = 含分红总回报）。
    """
    sig_by_date: dict[str, list] = defaultdict(list)
    for r in signals.itertuples(index=False):
        sig_by_date[r.signal_date].append(r._asdict() if hasattr(r, "_asdict") else dict(r._asdict()))
    # itertuples 返回 namedtuple，转 dict
    sig_by_date = defaultdict(list)
    for _, r in signals.iterrows():
        sig_by_date[r["signal_date"]].append(r.to_dict())

    dates = [d for d in trade_dates if start <= d <= end]
    if len(dates) < WARMUP_DAYS:
        raise ValueError(f"回测窗口交易日不足: {len(dates)}")

    cash = INIT_CAPITAL
    positions: dict[str, dict] = {}
    equity, trades = [], []

    def row_le(code: str, date: str):
        """该股在 date 当日或之前最近一个有成交的行号；无数据返回 None。"""
        fr = frames.get(code)
        if fr is None:
            return None
        j = fr["idx"].get(date)
        if j is not None:
            return j
        import bisect
        ds = fr["arr"]["date"]
        pos = bisect.bisect_right(ds, date) - 1
        return pos if pos >= 0 else None

    for i, d in enumerate(dates):
        dprev = dates[i - 1] if i > 0 else None

        # ── 1) 卖出（收盘判定已在昨日完成，今日开盘执行） ──
        for code in list(positions):
            pos = positions[code]
            j_today = frames[code]["idx"].get(d)
            if j_today is None:
                continue                          # 今日停牌无法成交
            hold = i - pos["i_buy"]
            # 判定行：昨日（或该股昨日之前最近成交日）的收盘
            j_prev = row_le(code, dprev) if dprev else None
            if j_prev is None:
                continue
            a = frames[code]["arr"]
            qc_prev, ma10_prev = a["qc"][j_prev], a["ma10"][j_prev]
            reason, sell_all, sell_ratio = None, False, 1.0
            if qc_prev < ma10_prev:               # 止损1：收盘跌破 MA10
                reason, sell_all = "止损_MA10", True
            elif qc_prev / pos["qbuy"] - 1 <= -STOP_LOSS_PCT:   # 止损2：亏损≥4%
                reason, sell_all = "止损_4pct", True
            elif hold == 3 and not pos["half_sold"] \
                    and qc_prev / pos["qbuy"] - 1 >= TAKE_PROFIT_D3:
                reason, sell_ratio = "止盈_3日5pct", SELL_HALF_PCT
            elif hold >= MAX_HOLD_DAYS:           # 第5日规则 + 兜底
                if qc_prev >= pos["prev_high"] * (1 - NEAR_HIGH_PCT):
                    reason, sell_all = "止盈_前高", True
                elif qc_prev / pos["qbuy"] - 1 < SIDEWAYS_PCT:
                    reason, sell_all = "离场_横盘", True
                elif hold > MAX_HOLD_DAYS:
                    reason, sell_all = "离场_超5日", True
                elif hold == MAX_HOLD_DAYS:
                    continue                      # 第5日两条件均不满足 → 继续持有
            if reason is None:
                continue
            # 开盘执行
            qpx = a["qo"][j_today] * (1 - SLIPPAGE)
            n_sell = pos["shares"] if sell_all else max(
                int(pos["shares"] * sell_ratio // 100) * 100, 0)
            if n_sell <= 0:
                continue
            gross = n_sell * qpx
            fee = gross * (SELL_FEE + STAMP_TAX)
            cash += gross - fee
            ret_pct = qpx * (1 - SELL_FEE - STAMP_TAX) / pos["qbuy"] - 1
            trades.append({
                "ts_code": code, "name": pos["name"], "l1_name": pos["l1"],
                "signal_date": pos["signal_date"], "buy_date": pos["buy_date"],
                "sell_date": d, "hold_days": hold, "shares": n_sell,
                "buy_px": round(pos["qbuy"], 3), "sell_px": round(qpx, 3),
                "ret_pct": round(ret_pct * 100, 2), "reason": reason,
                "pnl": round(gross - fee - n_sell * pos["qbuy"], 2),
            })
            if sell_all:
                del positions[code]
            else:
                pos["shares"] -= n_sell
                pos["half_sold"] = True

        # ── 2) 买入（昨日信号 → 今日开盘） ──
        if dprev is not None:
            eq_prev = equity[-1] if equity else INIT_CAPITAL
            for s in sorted(sig_by_date.get(dprev, []),
                            key=lambda x: -x.get("rank_pct", 0)):
                if len(positions) >= MAX_HOLDINGS:
                    break
                code = s["ts_code"]
                if code in positions or code not in frames:
                    continue
                fr = frames[code]
                j = fr["idx"].get(d)              # 今日有成交才可买入
                if j is None:
                    continue
                a = fr["arr"]
                if not np.isfinite(a["ma10"][j]):
                    continue
                target = eq_prev * MAX_POS_PCT
                # 股数在后复权空间取整（份额×复权价=市值，收益率语义自洽）
                shares = int(target / (a["qo"][j] * (1 + SLIPPAGE)) // 100) * 100
                if shares < 100:
                    continue
                cost = shares * a["qo"][j] * (1 + SLIPPAGE)
                fee = cost * BUY_FEE
                if cost + fee > cash:             # 现金不足 → 按现金缩减
                    shares = int(cash / (a["qo"][j] * (1 + SLIPPAGE) * (1 + BUY_FEE))
                                 // 100) * 100
                    if shares < 100:
                        continue
                    cost = shares * a["qo"][j] * (1 + SLIPPAGE)
                    fee = cost * BUY_FEE
                # 前高 = 信号日及之前 20 日最高点
                sidx = int(s["sidx"])
                lo = max(0, sidx - PREHIGH_WINDOW + 1)
                prev_high = float(np.nanmax(a["qh"][lo:sidx + 1]))
                cash -= cost + fee
                positions[code] = {
                    "shares": shares, "qbuy": a["qo"][j] * (1 + SLIPPAGE),
                    "i_buy": i, "half_sold": False, "prev_high": prev_high,
                    "name": s.get("name", ""), "l1": s.get("l1_name", ""),
                    "signal_date": s["signal_date"], "buy_date": d,
                }

        # ── 3) 收盘 mark-to-market（停牌股取最近收盘） ──
        mv = 0.0
        for code, pos in positions.items():
            j = row_le(code, d)
            if j is not None:
                mv += pos["shares"] * frames[code]["arr"]["qc"][j]
        equity.append(cash + mv)

    # ── 4) 期末未平仓持仓：按最近收盘 mark-to-market 记入明细（reason=未平仓） ──
    last_d = dates[-1]
    for code, pos in positions.items():
        j = row_le(code, last_d)
        if j is None:
            continue
        a = frames[code]["arr"]
        qpx = a["qc"][j]
        trades.append({
            "ts_code": code, "name": pos["name"], "l1_name": pos["l1"],
            "signal_date": pos["signal_date"], "buy_date": pos["buy_date"],
            "sell_date": "", "hold_days": len(dates) - 1 - pos["i_buy"],
            "shares": pos["shares"], "buy_px": round(pos["qbuy"], 3),
            "sell_px": round(qpx, 3),
            "ret_pct": round((qpx / pos["qbuy"] - 1) * 100, 2),
            "reason": "未平仓",
            "pnl": round(pos["shares"] * (qpx - pos["qbuy"]), 2),
        })

    eq = pd.Series(equity, index=pd.to_datetime(dates, format="%Y%m%d"),
                   name="equity")
    return eq, pd.DataFrame(trades)


# ════════════════════════════════════════════════════════
# §4b 纯信号模拟（默认模式：不做仓位控制）
# ════════════════════════════════════════════════════════

def simulate_signals(frames: dict, sig: pd.DataFrame, dates: list,
                     start: str, end: str) -> pd.DataFrame:
    """逐信号独立模拟生命周期（无仓位上限 / 资金约束）。

    与组合回测共用同一套买卖规则：
      T 日收盘判定信号 → T+1 开盘买入 → 五条卖出规则（收盘判定→次日开盘执行）
    差异：不做 MAX_HOLDINGS/资金控制，同一股票的多条信号互不影响、各自独立；
    每笔按 SIGNAL_NOTIONAL 名义金额计股数（仅影响 pnl 展示，收益率与股数无关）。
    """
    mkt_idx = {d: i for i, d in enumerate(dates)}      # 窗口内市场日历 → 序号
    trades = []
    for _, s in sig.iterrows():
        fr = frames.get(s["ts_code"])
        if fr is None:
            continue
        a = fr["arr"]
        j_buy = int(s["sidx"]) + 1                     # buy_date = 个股帧次一交易日
        if a["date"][j_buy] > end:
            continue                                   # 买入日在窗口外（数据末端信号）
        if not np.isfinite(a["ma10"][j_buy]):
            continue
        i_buy = mkt_idx.get(a["date"][j_buy])
        if i_buy is None:
            continue
        qbuy = a["qo"][j_buy] * (1 + SLIPPAGE)
        # 名义金额向下取整到手；复权价过高买不起一手时按 1 手计
        shares = int(SIGNAL_NOTIONAL / qbuy // 100) * 100 or 100
        sidx = int(s["sidx"])
        lo = max(0, sidx - PREHIGH_WINDOW + 1)
        pos = {"qbuy": qbuy, "shares": shares, "half_sold": False,
               "prev_high": float(np.nanmax(a["qh"][lo:sidx + 1]))}
        exited = False
        # 逐日推进卖出判定（j-1 收盘判定 → j 开盘执行，天然满足 T+1）
        for j in range(j_buy + 1, len(a["date"])):
            d_j = a["date"][j]
            if d_j > end:
                break
            di = mkt_idx.get(d_j)
            if di is None:
                continue                               # 当日不在窗口日历内
            hold = di - i_buy                          # 按市场交易日计数（含停牌日）
            qc_prev, ma10_prev = a["qc"][j - 1], a["ma10"][j - 1]
            reason, sell_all, sell_ratio = None, False, 1.0
            if qc_prev < ma10_prev:                    # 止损1：收盘跌破 MA10
                reason, sell_all = "止损_MA10", True
            elif qc_prev / qbuy - 1 <= -STOP_LOSS_PCT:  # 止损2：亏损≥4%
                reason, sell_all = "止损_4pct", True
            elif hold == 3 and not pos["half_sold"] \
                    and qc_prev / qbuy - 1 >= TAKE_PROFIT_D3:
                reason, sell_ratio = "止盈_3日5pct", SELL_HALF_PCT
            elif hold >= MAX_HOLD_DAYS:                # 第5日规则 + 兜底
                if qc_prev >= pos["prev_high"] * (1 - NEAR_HIGH_PCT):
                    reason, sell_all = "止盈_前高", True
                elif qc_prev / qbuy - 1 < SIDEWAYS_PCT:
                    reason, sell_all = "离场_横盘", True
                elif hold > MAX_HOLD_DAYS:
                    reason, sell_all = "离场_超5日", True
                else:
                    continue                           # 第5日两条件均不满足 → 续持
            if reason is None:
                continue
            qpx = a["qo"][j] * (1 - SLIPPAGE)
            n_sell = pos["shares"] if sell_all else max(
                int(pos["shares"] * sell_ratio // 100) * 100, 0)
            if n_sell <= 0:
                continue
            gross = n_sell * qpx
            fee = gross * (SELL_FEE + STAMP_TAX)
            trades.append({
                "ts_code": s["ts_code"], "name": s["name"],
                "l1_name": s["l1_name"], "l3_name": s["l3_name"],
                "signal_date": s["signal_date"], "buy_date": a["date"][j_buy],
                "sell_date": d_j, "hold_days": hold, "shares": n_sell,
                "buy_px": round(qbuy, 3), "sell_px": round(qpx, 3),
                "ret_pct": round((qpx * (1 - SELL_FEE - STAMP_TAX) / qbuy - 1)
                                 * 100, 2),
                "reason": reason,
                "pnl": round(gross - fee - n_sell * qbuy, 2),
            })
            if sell_all:
                exited = True
                break
            pos["shares"] -= n_sell
            pos["half_sold"] = True
        if not exited and pos["shares"] > 0:
            # 期末未平仓：按最近收盘 mark-to-market 记入明细
            jl = len(a["date"]) - 1
            if a["date"][jl] <= end:
                qpx = a["qc"][jl]
                trades.append({
                    "ts_code": s["ts_code"], "name": s["name"],
                    "l1_name": s["l1_name"], "l3_name": s["l3_name"],
                    "signal_date": s["signal_date"],
                    "buy_date": a["date"][j_buy], "sell_date": "",
                    "hold_days": mkt_idx.get(a["date"][jl], i_buy) - i_buy,
                    "shares": pos["shares"], "buy_px": round(qbuy, 3),
                    "sell_px": round(qpx, 3),
                    "ret_pct": round((qpx / qbuy - 1) * 100, 2),
                    "reason": "未平仓",
                    "pnl": round(pos["shares"] * (qpx - qbuy), 2),
                })
    return pd.DataFrame(trades)


# ════════════════════════════════════════════════════════
# §5 绩效统计
# ════════════════════════════════════════════════════════

def load_benchmark(start: str, end: str) -> pd.Series | None:
    """从通达信读沪深300 指数（指数不复权，无复权问题）。"""
    p = os.path.join(TDX_DIR, "vipdoc", "sh", "lday", "sh000300.day")
    df = parse_tdx_day_file(p, min_date=start)
    if df is None or df.empty:
        return None
    s = df[(df["trade_date"] >= start) & (df["trade_date"] <= end)]
    return pd.Series(s["close"].to_numpy(float),
                     index=pd.to_datetime(s["trade_date"], format="%Y%m%d"),
                     name="hs300")


def perf_stats(eq: pd.Series, trades: pd.DataFrame, bench: pd.Series | None) -> dict:
    """总收益 / 年化 / 最大回撤 / 胜率 / 盈亏比 / 交易次数等。"""
    e = eq.to_numpy(float)
    total = e[-1] / e[0] - 1.0
    years = len(e) / 244.0                              # A股年均约244个交易日
    annual = (1.0 + total) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    dd = float((e / np.maximum.accumulate(e) - 1.0).min())
    out = {
        "区间交易日": len(e),
        "总收益率%": round(total * 100, 2),
        "年化收益率%": round(annual * 100, 2),
        "最大回撤%": round(dd * 100, 2),
        "交易次数": 0, "胜率%": np.nan, "盈亏比": np.nan,
        "平均持仓天数": np.nan, "单笔平均收益%": np.nan,
    }
    if len(trades):
        closed = trades[trades["reason"] != "未平仓"]   # 统计口径只用已平仓交易
        rets = closed["ret_pct"].astype(float)
        win = rets[rets > 0]
        loss = rets[rets <= 0]
        out.update({
            "交易次数": len(closed),
            "胜率%": round(len(win) / len(rets) * 100, 1) if len(rets) else np.nan,
            "盈亏比": round(win.mean() / abs(loss.mean()), 2)
            if len(win) and len(loss) else np.nan,
            "平均持仓天数": round(closed["hold_days"].astype(float).mean(), 2),
            "单笔平均收益%": round(rets.mean(), 2),
        })
        n_open = int((trades["reason"] == "未平仓").sum())
        out["未平仓持仓"] = n_open
    if bench is not None and len(bench) > 1:
        b0, b1 = bench.iloc[0], bench.iloc[-1]
        out["沪深300同期%"] = round((b1 / b0 - 1) * 100, 2)
    return out


def print_stats(tag: str, st: dict) -> None:
    print(f"\n{'=' * 52}\n  降龙一掌 回测绩效  [{tag}]\n{'=' * 52}")
    for k, v in st.items():
        print(f"  {k:<12} {v}")
    print("=" * 52)


def print_signal_stats(tag: str, n_sig: int, trades: pd.DataFrame) -> None:
    """纯信号模式统计：只评价信号本身的质量，不涉及仓位 / 资金。"""
    print(f"\n{'=' * 52}\n  降龙一掌 信号质量统计（纯信号模式）  [{tag}]\n{'=' * 52}")
    print(f"  信号总数          {n_sig}")
    if not len(trades):
        print("  （无可模拟交易）")
        print("=" * 52)
        return
    closed = trades[trades["reason"] != "未平仓"]
    rets = closed["ret_pct"].astype(float)
    win, loss = rets[rets > 0], rets[rets <= 0]
    n_open = int((trades["reason"] == "未平仓").sum())
    n_sim = len(trades.drop_duplicates(["ts_code", "signal_date"]))
    print(f"  已成交信号        {n_sim}")
    print(f"  未平仓            {n_open}")
    if len(rets):
        print(f"  已平仓交易        {len(closed)} 笔（含分笔卖出）")
        print(f"  胜率%             {len(win) / len(rets) * 100:.1f}")
        if len(win) and len(loss):
            print(f"  盈亏比            {win.mean() / abs(loss.mean()):.2f}")
        print(f"  单笔平均收益%     {rets.mean():.2f}")
        print(f"  单笔收益中位数%   {rets.median():.2f}")
        print(f"  平均持仓天数      {closed['hold_days'].astype(float).mean():.2f}")
        print("  退出原因分布:")
        for rsn, cnt in closed["reason"].value_counts().items():
            sub = closed.loc[closed["reason"] == rsn, "ret_pct"].astype(float)
            print(f"    {rsn:<10} {cnt:>5} 笔   均值 {sub.mean():+.2f}%")
    print("=" * 52)


# ════════════════════════════════════════════════════════
# §6 模拟数据生成（demo 自检：植入"暴涨→缩量回调→止跌"形态）
# ════════════════════════════════════════════════════════

def make_demo_data(n_market=2500, n_pool=60, n_days=600, seed=7):
    """生成与真实缓存同构的模拟数据，用于验证代码链路。

    池内股票植入 2 处形态事件：
      前10日 +28%（含一根 +8% 大阳，放量）→ 3日连续缩量回调
      → 止跌日（下影线>实体、缩量）→ 反弹
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=n_days).strftime("%Y%m%d")
    codes = [f"{600000 + k}.SH" for k in range(n_market)]
    rets = rng.normal(0.0003, 0.018, (n_market, n_days))
    rets[:, 0] = 0.0
    vols = rng.lognormal(13.5, 0.6, (n_market, n_days))   # 股

    pool_rows = []
    for j in range(n_pool):
        for e in rng.integers(80, n_days - 30, 2):        # 两个事件锚点
            e = int(e)
            # 前 10 日暴涨 +28%（第 4 根 +8% 大阳放量 3×）
            surge = [0.025, 0.030, 0.020, 0.080, 0.025, 0.030, 0.025, 0.020, 0.015, 0.015]
            for d_i, rr in enumerate(surge):
                rets[j, e - 9 + d_i] = rr
                vols[j, e - 9 + d_i] *= 3.0 if d_i == 3 else 1.8
            # 3 日连续缩量回调
            for d_i, (rr, vf) in enumerate([(-0.008, 0.80), (-0.012, 0.60), (-0.015, 0.42)]):
                rets[j, e + 1 + d_i] = rr
                vols[j, e + 1 + d_i] *= vf
            # 止跌日（e+4）：微涨 0.1%、量继续缩 → 下影线在 K 线生成时构造
            rets[j, e + 4] = 0.001
            vols[j, e + 4] *= 0.38
            # 随后反弹
            rets[j, e + 5:e + 8] = 0.02
            vols[j, e + 5:e + 8] *= 1.5
        pool_rows.append({
            "ts_code": codes[j], "name": f"模拟龙头{j:02d}",
            "l1_name": "模拟行业", "l3_name": f"模拟细分{j // 5}",
            "leader_type_v2": "ABSOLUTE",
        })

    close = 100.0 * np.cumprod(1.0 + rets, axis=1)
    pre_close = np.concatenate([close[:, :1], close[:, :-1]], axis=1)
    open_ = pre_close * (1 + rng.normal(0, 0.004, close.shape))       # 模拟开盘
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.005, close.shape)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, close.shape)))
    pct = (close / pre_close - 1) * 100
    amount = vols * close / 1000.0                                    # 千元，与 tushare 一致

    recs = []
    for j, code in enumerate(codes):
        recs.append(pd.DataFrame({
            "ts_code": code, "trade_date": dates,
            "close": close[j], "pre_close": pre_close[j],
            "pct_chg": pct[j], "vol": vols[j], "amount": amount[j],
            "high": high[j], "low": low[j],
        }))
    mkt = pd.concat(recs, ignore_index=True)
    pool = pd.DataFrame(pool_rows)
    print(f"[模拟数据] 全市场 {n_market} 只 × {n_days} 日, 龙头池 {n_pool} 只")
    return mkt, pool


# ════════════════════════════════════════════════════════
# §7 主入口
# ════════════════════════════════════════════════════════

def prepare_frames(mkt: pd.DataFrame, pool: pd.DataFrame, use_tdx_open: bool = True):
    """公共流水线：面板 → TDX 开盘价 → 个股帧。"""
    qclose, rank, trade_dates = build_market_panel(mkt)
    tdx_open = load_tdx_open_map(sorted(pool["ts_code"])) if use_tdx_open else {}
    frames = build_stock_frames(mkt, pool, rank, tdx_open)
    print(f"[个股帧] 有效帧 {len(frames)} 只（数据不足 {MIN_LIST_DAYS} 日的已剔除）")
    return frames, trade_dates


def mode_signal_only(date: str) -> None:
    """--date 模式：输出指定交易日收盘判定的信号清单（次日开盘买入）。"""
    pool = load_pool()
    mkt = load_market_daily("0", date)
    frames, _ = prepare_frames(mkt, pool)
    sig = generate_signals(frames, pool)
    day = sig[sig["signal_date"] == date] if len(sig) else sig
    if not len(day):
        print(f"[结果] {date} 无满足降龙一掌三段式条件的信号")
        return
    out = os.path.join(OUTPUT_DIR, f"xianglong_signals_{date}.csv")
    day.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n[结果] {date} 信号 {len(day)} 条（次日开盘买入）→ {out}")
    cols = ["ts_code", "name", "l1_name", "l3_name", "k_days",
            "ret10", "rank_pct", "vol_ratio", "buy_date"]
    print(day[cols].to_string(index=False))


def mode_backtest(start: str, end: str, demo: bool = False,
                  portfolio: bool = False) -> None:
    """回测模式：默认纯信号模拟（无仓位控制），--portfolio 切换组合模式。"""
    if demo:
        mkt, pool = make_demo_data()
        tag = "DEMO模拟"
    else:
        pool = load_pool()
        mkt = load_market_daily()
        tag = f"{start}-{end}"
    frames, trade_dates = prepare_frames(mkt, pool, use_tdx_open=not demo)
    sig = generate_signals(frames, pool)
    if not len(sig):
        print("[结果] 窗口内无信号，回测结束")
        return
    dates = [d for d in trade_dates if start <= d <= end]
    if portfolio:
        eq, trades = run_backtest(frames, sig, trade_dates, start, end)
        bench = None if demo else load_benchmark(start, end)
        st = perf_stats(eq, trades, bench)
        print_stats(tag, st)
        # 明细落盘
        t_out = os.path.join(OUTPUT_DIR, f"xianglong_trades_{tag}.csv")
        e_out = os.path.join(OUTPUT_DIR, f"xianglong_equity_{tag}.csv")
        trades.to_csv(t_out, index=False, encoding="utf-8-sig")
        eq.to_csv(e_out, encoding="utf-8-sig")
        print(f"[输出] 交易明细 → {t_out}")
        print(f"[输出] 净值曲线 → {e_out}  (期末净值 {eq.iloc[-1] / INIT_CAPITAL:.4f})")
    else:
        sig_w = sig[sig["signal_date"] >= start].reset_index(drop=True)
        n_pending = int((sig_w["buy_date"] > end).sum()) if len(sig_w) else 0
        trades = simulate_signals(frames, sig_w, dates, start, end)
        print_signal_stats(tag, len(sig_w), trades)
        t_out = os.path.join(OUTPUT_DIR, f"xianglong_signal_trades_{tag}.csv")
        trades.to_csv(t_out, index=False, encoding="utf-8-sig")
        print(f"[输出] 信号明细 → {t_out}")
    if len(trades):
        cols = ["ts_code", "name", "signal_date", "buy_date", "sell_date",
                "hold_days", "ret_pct", "reason"]
        print("\n[最近10笔交易]")
        print(trades[cols].tail(10).to_string(index=False))


def main():
    ap = argparse.ArgumentParser(description="降龙一掌：主线龙头首次缩量回调低吸策略")
    ap.add_argument("--start", default="20240101", help="回测起始日 YYYYMMDD")
    ap.add_argument("--end", default="20991231", help="回测结束日 YYYYMMDD")
    ap.add_argument("--date", default=None, help="仅生成该日信号清单 YYYYMMDD")
    ap.add_argument("--demo", action="store_true", help="使用模拟数据自检")
    ap.add_argument("--portfolio", action="store_true",
                    help="组合回测模式（仓位上限+最多3只）；默认为纯信号模式")
    args = ap.parse_args()
    if args.date:
        mode_signal_only(args.date)
    else:
        end = args.end
        if not args.demo and end == "20991231":
            # 默认结束日 = 最新缓存日期
            files = glob.glob(os.path.join(CACHE_DIR, "daily_*.parquet"))
            files = [f for f in files if os.path.basename(f)[6:14].isdigit()]
            end = max(os.path.basename(f)[6:14] for f in files) if files else "20991231"
        mode_backtest(args.start, end, demo=args.demo, portfolio=args.portfolio)


if __name__ == "__main__":
    main()
