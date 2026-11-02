# -*- coding: utf-8 -*-
"""行业 ETF 主线识别（V10.2）

核心思路
--------
「一波大行情必有一个 ETF 成为主线」—— 用行业 ETF 的真实走势识别主线，
替代 theme_scores.db 的主题打分。

为什么用 ETF 而不是主题打分：
  1. **覆盖完整**：theme_scores.db 只有 20260724 起（64 天），
     ETF 行情可回溯数年（1410 个交易日级别）。
  2. **口径客观**：ETF 净值是市场真实成交价格，不受题材热度主观打分影响。
  3. **可回测**：ETF 走势是市场事实，不是模型输出 → 可做样本外检验。

★ 关键数据陷阱：ETF 会做**份额折算**（拆分/合并），导致 close 序列跳变 50~75%。
  实测 515880.SH 全年 close 法收益 -86.26%，但实际只有 -15.93% —— 用 close
  计算会得出完全错误的结论。
  → 本模块一律用 **pct_chg 累乘**构造复权净值序列（nav），不用 close。

主线判定维度（每个维度独立，可配置）
  1. 相对强度  —— 行业 ETF 相对全市场（用中证全指或等权 ETF 组合）的超额
  2. 趋势持续  —— nav 站上 MA20/MA60，且近 N 日收益为正
  3. 资金流入  —— 成交额相对近期均值放大
  4. 上涨宽度  —— 行业内多只 ETF 同向（不是单只孤军）
"""

import os
import sys
import json
import sqlite3
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ETF_DB = os.path.join(BASE_DIR, "cache_daily", "etf_daily.db")
CONFIG_PATH = os.path.join(BASE_DIR, "config_v102_etf_mainline.json")


# ============================================================
# 行业 ETF 池
# ============================================================
# 申万一级行业 → 行业 ETF（每行业多只，用于计算「上涨宽度」）。
# 选取原则：2021 年前上市（保证 2026 全年有完整行情）+ 成交额靠前（流动性）。
# 代码来源：tushare fund_basic(market='E') + fund_daily 实测流动性验证。
ETF_POOL = {
    "银行":       ["512800.SH", "515020.SH", "512700.SH"],
    "非银金融":   ["512000.SH", "512880.SH", "510200.SH", "512070.SH"],
    "医药生物":   ["512010.SH", "159929.SZ", "512170.SH", "159992.SZ"],
    "食品饮料":   ["512690.SH", "159928.SZ", "515170.SH", "512600.SH"],
    "电力设备":   ["515790.SH", "516160.SH", "159806.SZ"],
    "电子":       ["159997.SZ", "159995.SZ", "515260.SH", "512480.SH"],
    "计算机":     ["512720.SH", "159998.SZ", "512930.SH"],
    "通信":       ["515880.SH", "515050.SH", "159994.SZ"],
    "传媒":       ["512980.SH", "159805.SZ"],
    "机械设备":   ["516960.SH", "159886.SZ", "561280.SH"],
    "有色金属":   ["512400.SH", "159876.SZ", "561330.SH"],
    "基础化工":   ["516020.SH", "159870.SZ"],
    "国防军工":   ["512660.SH", "512810.SH", "512680.SH"],
    "汽车":       ["516110.SH", "515030.SH"],
    "家用电器":   ["159996.SZ", "561120.SH"],
    "房地产":     ["512200.SH", "159768.SZ"],
    "建筑建材":   ["516970.SH"],
    "农林牧渔":   ["159825.SZ", "159827.SZ", "159865.SZ"],
    "煤炭":       ["515220.SH", "168204.SZ"],
    "石油石化":   ["561360.SH", "159697.SZ"],
    "交通运输":   ["159666.SZ", "516510.SH"],
    "商贸零售":   ["159766.SZ", "561130.SH"],
    "社会服务":   ["159869.SZ", "517110.SH"],
    "美容护理":   ["159718.SZ"],
    "环保":       ["512580.SH", "159628.SZ"],
    "钢铁":       ["515210.SH", "516150.SH", "168203.SZ"],
    "轻工制造":   ["561070.SZ"],
    "纺织服饰":   ["516130.SH"],
    "公用事业":   ["561560.SH", "159611.SZ"],
}
# ★ 行业归属说明（全部经 tushare fund_basic 逐一核对 benchmark 修正）：
#   159611.SZ = 中证全指电力公用事业ETF → 公用事业
#   512660.SH = 中证军工ETF → 国防军工（原先误放在有色金属）
#   516970.SH = 中证基建工程ETF → 建筑建材
#   561360.SH = 中证油气产业ETF → 石油石化（**不是**煤炭）
#   561330.SH = 中证有色金属矿业主题ETF → 有色金属
#   159806.SZ = 中证新能源汽车ETF → 汽车
# ★ 已剔除宽基：159791.SZ（沪深300 ESG基准）、159611 早期误配等。
# ★ 建筑材料与建筑装饰成分高度重叠（同一批基建股票），合并为「建筑建材」，
#   避免同一 ETF 被两个行业重复计数、污染各自的 nav 与宽度。

# ETF 名称（用于报告展示）
ETF_NAMES = {
    "512800.SH": "银行ETF", "515020.SH": "银行ETF", "512700.SH": "银行ETF",
    "512000.SH": "证券ETF", "512880.SH": "证券ETF", "510200.SH": "证券ETF",
    "512070.SH": "保险ETF",
    "512010.SH": "医药ETF", "159929.SZ": "医药ETF", "512170.SH": "医疗ETF",
    "159992.SZ": "创新药ETF",
    "512690.SH": "酒ETF", "159928.SZ": "消费ETF", "515170.SH": "消费电子ETF",
    "512600.SH": "消费ETF",
    "515790.SH": "光伏ETF", "159611.SZ": "电池ETF", "516160.SH": "新能源ETF",
    "159997.SZ": "电子ETF", "159995.SZ": "芯片ETF", "515260.SH": "电子50ETF",
    "512480.SH": "半导体ETF",
    "512720.SH": "计算机ETF", "159998.SZ": "计算机ETF", "512930.SH": "人工智能ETF",
    "515880.SH": "通信设备ETF", "515050.SH": "5GETF", "159994.SZ": "5G通信ETF",
    "512980.SH": "传媒ETF", "159805.SZ": "传媒ETF",
    "516960.SH": "机械ETF", "159886.SZ": "工程机械ETF",
    "512400.SH": "有色ETF", "159876.SZ": "有色ETF", "512660.SH": "军工ETF",
    "516020.SH": "化工ETF", "159870.SZ": "化工ETF",
    "512810.SH": "军工ETF", "512680.SH": "军工ETF",
    "516110.SH": "汽车ETF", "159806.SZ": "新能源车ETF", "515030.SH": "新能源车ETF",
    "159996.SZ": "家电ETF", "561120.SH": "家电ETF",
    "512200.SH": "房地产ETF", "159768.SZ": "地产ETF",
    "516970.SH": "基建ETF", "159791.SZ": "基建ETF",
    "159825.SZ": "农业ETF", "159827.SZ": "农业ETF", "159865.SZ": "养殖ETF",
    "515220.SH": "煤炭ETF", "561360.SH": "能源ETF", "159697.SZ": "能源ETF",
    "159666.SZ": "基建ETF", "516510.SH": "物流ETF",
    "159766.SZ": "商贸ETF", "561130.SH": "商业ETF",
    "159869.SZ": "旅游ETF", "517110.SH": "教育ETF",
    "159718.SZ": "美妆ETF", "512580.SH": "环保ETF", "159628.SZ": "环保ETF",
    "515210.SH": "钢铁ETF", "516150.SH": "钢铁ETF", "561070.SZ": "轻工ETF",
    "516130.SH": "纺织ETF", "561560.SH": "公用事业ETF",
    "561330.SH": "有色矿业ETF", "168204.SZ": "煤炭指数",
    "168203.SZ": "钢铁指数", "561360.SH": "油气产业ETF",
    "159806.SZ": "新能源车ETF", "159697.SZ": "能源ETF",
    "516970.SH": "基建ETF",
}


def all_codes():
    return sorted(set(c for v in ETF_POOL.values() for c in v))


def industry_of(code):
    for ind, codes in ETF_POOL.items():
        if code in codes:
            return ind
    return None


# ============================================================
# 配置
# ============================================================
DEFAULT_CONFIG = {
    "_note": "V10.3 ETF 主线识别阈值。均为初始风控配置，改动需重跑历史检验。",
    "relative_strength": {
        "_note": "相对强度：行业 nav 相对基准（等权全行业 ETF 组合）的超额",
        "min_excess_20d": 0.0,
        "min_excess_60d": 5.0,
        "rank_top_n": 8,
        "_v103_note": "★ V10.3：min_excess_20d 2.0→0.0、min_excess_60d 3.0→5.0。"
                      "依据：2026 全年 4984 个「行业×交易日」样本，用未来 20 日相对基准超额 >5% "
                      "作事实标签，参数网格搜索 + 前后半段分割验证。"
                      "现口径在**验证段**（后 89 天）F1=24.4，旧口径仅 13.3（严重过拟合退化）。",
    },
    "trend": {
        "_note": "趋势：站上均线**或**回撤未破（相对口径），且近期收益为正",
        "ma20_window": 20,
        "ma60_window": 60,
        "require_above_ma20": True,
        "require_above_ma60": False,
        "min_ret5": -99.0,
        "min_ret20": -99.0,
        "max_drawdown_from_high_pct": -12.0,
        "_ret_note": "★ V10.3：min_ret5 / min_ret20 由 0.0 / 1.0 改为 -99（不限制）。\n"
                     "  依据：验证段 F1 从 10.8 提升到 24.4。\n"
                     "  原因：绝对收益是绝对量指标，主线判定应由相对强度主导；\n"
                     "  2026-06-10 电子行业 ex60=37.7%（全年最强）但 ret5=-3.65%、ret20=+0.07%，\n"
                     "  被绝对门槛否掉 → 漏判整个半导体行情的启动段。",
        "_v103_note": "★ V10.3 核心修正：趋势条件由「必须站上 MA20」改为「站上 MA20 **或** "
                      "距 20 日高点回撤 ≤ -12%」。\n"
                      "  起因：2026 上半年半导体行情（512480.SH 从 4/2 的 132 涨到 6/30 的 281，"
                      "+112.7%）在 6/1~6/12 期间 ex60 高达 27~38%（全年最强），"
                      "但 5 月冲高抬高 MA20，导致连续 8 个交易日站不上 → 整段被判「无主线」，"
                      "漏掉这波最赚钱的行情。\n"
                      "  依据：验证段 dd20 阈值在 -8~-18 区间 F1 稳定在 23~24.4，取 -12 居中。",
        "min_bonus_score": 0,
        "_bonus_note": "★ V10.3：量能与宽度降级为**加分项**（0/1/2 分），"
                       "min_bonus_score=0 表示不再作为否决条件。\n"
                       "  实测：amt_ratio>=1.15 作硬门槛 → 验证段 F1 从 24.4 掉到 5.9；"
                       "above20_ratio>=0.5 作硬门槛 → 掉到 14.2。\n"
                       "  原因：量能/宽度是「确认」信号而非「识别」信号，主线启动初期往往缩量。\n"
                       "  另：etf_count=1 的行业准确率 37%，反而高于 4 只 ETF 的 26% —— "
                       "ETF 数量反映的是行业 ETF 供给密度，不是主线质量。",
    },
    "money_flow": {
        "_note": "资金：成交额相对近期均值的放大倍数（加分项）",
        "amt_window": 20,
        "min_amt_ratio": 1.15,
    },
    "breadth": {
        "_note": "宽度：同行业内多只 ETF 的趋势一致性（加分项）",
        "min_same_dir_ratio": 0.6,
        "min_above20_ratio": 0.5,
        "_above20_note": "★ V10.2：改用「站上 MA20 的 ETF 占比」作为宽度主指标。当日同涨口径因行业内 ETF 成分不同而过于苛刻（实测强势主线期常只有 0~0.5），会把 4 月通信、5 月电子这类真主线判掉。★ V10.3：降级为加分项。",
        "min_industry_etf_count": 2,
    },
    "mainline": {
        "_note": "主线成立条件",
        "min_days_strong": 2,
        "min_industries": 1,
        "require_excess": True,
    },
    "volatility": {
        "_note": "市场波动率代理 —— 用于组合风险预算的止损距离（不依赖选股数据）",
        "lookback": 60,
        "stop_multiple": 2.5,
        "min_stop_distance": 0.03,
        "max_stop_distance": 0.12,
        "fallback_stop_distance": 0.05,
        "_fallback_note": "ETF 数据完全不可用时的兜底止损距离。",
        "_note_detail": "止损距离 = 行业 ETF 日均绝对涨幅中位数 × stop_multiple，并夹在 [min,max] 之间。实测 2026 全年 70 只 ETF 的近60日日均绝对涨幅中位 1.21%（区间 0.63%~2.58%）。",
    },
}


def load_config(path: str = None) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    p = path or CONFIG_PATH
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                user = json.load(f)
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except Exception:
            pass
    return cfg


# ============================================================
# 数据缓存
# ============================================================
def _etf_db():
    os.makedirs(os.path.dirname(ETF_DB), exist_ok=True)
    conn = sqlite3.connect(ETF_DB, timeout=30.0)
    conn.execute("""CREATE TABLE IF NOT EXISTS etf_daily (
        ts_code TEXT, trade_date TEXT,
        close REAL, pct_chg REAL, amount REAL, vol REAL,
        nav REAL,
        PRIMARY KEY (ts_code, trade_date))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS etf_meta (
        key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)""")
    conn.commit()
    return conn


def fetch_etf_history(start_date: str = "20210101", end_date: str = None,
                       codes=None, progress=True) -> int:
    """从 tushare 拉取行业 ETF 行情并落库。

    ★ 用 pct_chg 累乘构造 nav（复权净值），不直接用 close ——
      ETF 份额折算会让 close 跳变 50~75%。
    """
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(BASE_DIR), "config", ".env"))
    import tushare as ts

    ts.set_token(os.getenv("TUSHARE_TOKEN"))
    pro = ts.pro_api()
    end_date = end_date or datetime.datetime.now().strftime("%Y%m%d")
    codes = codes or all_codes()

    conn = _etf_db()
    ok = 0
    for i, code in enumerate(codes):
        try:
            d = pro.fund_daily(ts_code=code, start_date=start_date, end_date=end_date)
        except Exception as e:
            print(f"  {code} 拉取失败: {e}")
            continue
        if d is None or len(d) == 0:
            continue
        d = d.sort_values("trade_date").reset_index(drop=True)
        # ★ 复权净值：pct_chg 累乘
        nav = 100.0 * (1.0 + d["pct_chg"].fillna(0.0) / 100.0).cumprod()
        rows = []
        for j in range(len(d)):
            rows.append((
                code, str(d["trade_date"].iloc[j]),
                float(d["close"].iloc[j]) if d["close"].iloc[j] is not None else None,
                float(d["pct_chg"].iloc[j]) if d["pct_chg"].iloc[j] is not None else None,
                float(d["amount"].iloc[j]) if d["amount"].iloc[j] is not None else None,
                float(d["vol"].iloc[j]) if d["vol"].iloc[j] is not None else None,
                float(nav.iloc[j]),
            ))
        conn.executemany(
            "INSERT OR REPLACE INTO etf_daily "
            "(ts_code, trade_date, close, pct_chg, amount, vol, nav) "
            "VALUES (?,?,?,?,?,?,?)", rows)
        conn.commit()
        ok += 1
        if progress and (i + 1) % 10 == 0:
            print(f"  已拉取 {i+1}/{len(codes)}")
    conn.execute("INSERT OR REPLACE INTO etf_meta (key, value, updated_at) VALUES (?,?,?)",
                 ("last_fetch", end_date, datetime.datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return ok


_PANEL_CACHE = {}


def load_etf_panel(codes=None) -> pd.DataFrame:
    """读取全部 ETF 的 nav 面板，返回 pivot 表（行=日期，列=代码）。

    ★ 进程内缓存：全年回放会对每个交易日调用一次，
      每次全量读 SQLite 68 只 × 1410 天 ≈ 96k 行 → 回放直接超时。
    """
    key = "all" if codes is None else ",".join(sorted(codes))
    if key in _PANEL_CACHE:
        return _PANEL_CACHE[key]
    conn = _etf_db()
    codes = codes or all_codes()
    q = ",".join("?" * len(codes))
    df = pd.read_sql_query(
        f"SELECT ts_code, trade_date, nav, amount, pct_chg FROM etf_daily "
        f"WHERE ts_code IN ({q}) ORDER BY trade_date", conn, params=codes)
    conn.close()
    if df.empty:
        return pd.DataFrame()
    df["trade_date"] = df["trade_date"].astype(str)
    _PANEL_CACHE[key] = df
    return df


# ============================================================
# 主线识别
# ============================================================
_SERIES_CACHE = {}
_RESULT_CACHE = {}


_VOL_CACHE = {}


def estimate_market_volatility(trade_date: str = None, cfg: dict = None,
                               panel: pd.DataFrame = None) -> dict:
    """估算市场典型波动幅度（用于组合风险预算的止损距离代理）。

    ★ 为什么需要它：
      组合风险预算要算「各持仓潜在止损损失之和」，原本依赖**个股止损价**
      （来自选股库）。一旦选股数据缺失或滞后，风险预算就无法计算，
      进而让仓位建议失真 —— 这正是把选股数据移出仓位计算的关键一环。

    ★ 代理口径：用**行业 ETF 的日均绝对涨跌幅**（真实波幅 proxy）
      作为「一只持仓的典型止损距离」。
      2026 全年实测：70 只行业 ETF 的近 60 日日均绝对涨幅
      中位 1.21%、区间 0.63%~2.58%。
      止损距离取其 2.5 倍（个股止损通常设在 2~3 倍日均波幅之外）。

    返回：{'stop_distance': 0.06, 'daily_range': 0.024, 'sample': 70, ...}
    """
    cfg = cfg or load_config()
    if panel is None:
        panel = load_etf_panel()
    if panel.empty:
        return {"stop_distance": None, "daily_range": None, "sample": 0,
                "note": "ETF 面板为空，无法估算波动率"}

    td = str(trade_date) if trade_date else panel["trade_date"].max()
    # ★ 结果缓存：全年回放会对每个交易日调用多次，
    #   每次都要 groupby 70 只 ETF → 回放直接超时。
    cache_key = td
    if cache_key in _VOL_CACHE:
        return _VOL_CACHE[cache_key]

    sub = panel[panel["trade_date"] <= td].copy()
    if sub.empty:
        return {"stop_distance": None, "daily_range": None, "sample": 0,
                "note": f"{td} 之前无 ETF 数据"}

    lookback = int(cfg.get("volatility", {}).get("lookback", 60))
    mult = float(cfg.get("volatility", {}).get("stop_multiple", 2.5))
    floor = float(cfg.get("volatility", {}).get("min_stop_distance", 0.03))
    cap = float(cfg.get("volatility", {}).get("max_stop_distance", 0.12))

    sub = sub.sort_values(["ts_code", "trade_date"])
    absr = sub.groupby("ts_code", group_keys=False).apply(
        lambda g: g["pct_chg"].abs().tail(lookback).mean(), include_groups=False)
    absr = absr.dropna()
    if absr.empty:
        return {"stop_distance": None, "daily_range": None, "sample": 0,
                "note": "ETF 波动率样本不足"}

    daily_range = float(absr.median()) / 100.0
    sd = min(max(daily_range * mult, floor), cap)
    out = {
        "stop_distance": round(sd, 4),
        "daily_range": round(daily_range, 4),
        "sample": int(len(absr)),
        "p25": round(float(absr.quantile(0.25)) / 100.0, 4),
        "p75": round(float(absr.quantile(0.75)) / 100.0, 4),
        "note": f"ETF 日均绝对涨幅中位 {daily_range*100:.2f}%，×{mult} 作为典型止损距离",
    }
    _VOL_CACHE[cache_key] = out
    return out


def compute_industry_series(panel: pd.DataFrame, cfg: dict):
    """按行业聚合 ETF 序列，返回每个行业的 nav / 成交额 / 宽度面板。

    行业 nav = 该行业所有 ETF nav 的**等权平均**（消除单只规模差异）。
    """
    if panel.empty:
        return {}
    cache_key = (id(panel), len(panel))
    if cache_key in _SERIES_CACHE:
        return _SERIES_CACHE[cache_key]
    out = {}
    rows = []
    for code, g in panel.groupby("ts_code"):
        rows.append((code, industry_of(code), g))
    by_ind = {}
    for code, ind, g in rows:
        if not ind:
            continue
        by_ind.setdefault(ind, []).append(g)

    dates = sorted(panel["trade_date"].unique())
    idx = pd.Index(dates)

    for ind, gs in by_ind.items():
        navs = []
        amts = []
        for g in gs:
            s = g.set_index("trade_date")
            navs.append(s["nav"].reindex(idx))
            amts.append(s["amount"].reindex(idx))
        nav_df = pd.concat(navs, axis=1)
        amt_df = pd.concat(amts, axis=1)
        nav_df.columns = [f"etf{i}" for i in range(nav_df.shape[1])]
        amt_df.columns = [f"etf{i}" for i in range(amt_df.shape[1])]

        ind_nav = nav_df.mean(axis=1)                       # 行业等权净值
        ind_amt = amt_df.sum(axis=1)                        # 行业总成交额
        # 宽度：当日上涨的 ETF 占比
        up_ratio = (nav_df.pct_change() > 0).mean(axis=1)
        # 宽度趋势：行业 nav 站上 MA20 的 ETF 占比
        above20 = (nav_df > nav_df.rolling(20, min_periods=5).mean()).mean(axis=1)

        out[ind] = pd.DataFrame({
            "trade_date": idx,
            "nav": ind_nav.values,
            "amount": ind_amt.values,
            "up_ratio": up_ratio.values,
            "above20_ratio": above20.values,
            "etf_count": nav_df.shape[1],
        })
    _SERIES_CACHE[cache_key] = out
    return out


def detect_mainlines(trade_date: str, cfg: dict = None,
                     panel: pd.DataFrame = None) -> dict:
    """识别当日主线行业。

    返回：{'industries': [...], 'details': {...}, 'missing': bool}
    """
    cfg = cfg or load_config()
    if panel is None:
        panel = load_etf_panel()
    if panel.empty:
        return {"industries": [], "details": {}, "missing": True,
                "note": "ETF 行情缓存为空，请先运行 fetch_etf_history()"}

    # ★ 结果缓存：同一交易日重复调用时直接返回
    #   （V10.1 一天内会多次调用 check_mainline，回放 198 天 × 多次调用
    #     每次重算全历史 → 实测 SIGTERM 超时）
    cache_key = (str(trade_date), id(panel))
    if cache_key in _RESULT_CACHE:
        return _RESULT_CACHE[cache_key]

    series = compute_industry_series(panel, cfg)
    if not series:
        return {"industries": [], "details": {}, "missing": True,
                "note": "无行业 ETF 数据"}

    td = str(trade_date)
    rs = cfg["relative_strength"]
    tr = cfg["trend"]
    mf = cfg["money_flow"]
    bd = cfg["breadth"]

    # ---- 基准：所有行业 ETF 等权组合 ----
    all_nav = []
    all_amt = []
    for ind, s in series.items():
        sub = s[s["trade_date"] <= td]
        if len(sub) < tr["ma60_window"]:
            continue
        all_nav.append(sub["nav"].values)
        all_amt.append(sub["amount"].values)
    if not all_nav:
        return {"industries": [], "details": {}, "missing": True,
                "note": f"{td} 之前数据不足"}
    minlen = min(len(a) for a in all_nav)
    bench_nav = np.mean([a[-minlen:] for a in all_nav], axis=0)

    def _ma(arr, w):
        return float(np.mean(arr[-w:])) if len(arr) >= w else None

    def _ret(arr, n):
        return (arr[-1] / arr[-1 - n] - 1) * 100 if len(arr) > n else None

    bench_ma20 = _ma(bench_nav, tr["ma20_window"])
    bench_ret20 = _ret(bench_nav, 20)
    bench_ret60 = _ret(bench_nav, 60)

    details = {}
    for ind, s in series.items():
        sub = s[s["trade_date"] <= td]
        if len(sub) < tr["ma60_window"]:
            continue
        nav = sub["nav"].values
        amt = sub["amount"].values
        cur = nav[-1]

        ma20 = _ma(nav, tr["ma20_window"])
        ma60 = _ma(nav, tr["ma60_window"])
        ret5 = _ret(nav, 5)
        ret20 = _ret(nav, 20)
        ret60 = _ret(nav, 60)
        ex20 = (ret20 - bench_ret20) if (ret20 is not None and bench_ret20 is not None) else None
        ex60 = (ret60 - bench_ret60) if (ret60 is not None and bench_ret60 is not None) else None

        amt_ma = _ma(amt, mf["amt_window"])
        amt_ratio = (amt[-1] / amt_ma) if (amt_ma and amt_ma > 0) else None

        up_ratio = float(sub["up_ratio"].iloc[-1])
        above20 = float(sub["above20_ratio"].iloc[-1])
        etf_count = int(sub["etf_count"].iloc[-1])

        # ★ V10.3 距 20 日高点的回撤（相对趋势口径）
        hi20 = float(np.max(nav[-20:])) if len(nav) >= 20 else None
        dd20 = ((cur / hi20 - 1) * 100) if hi20 else None

        # ---------- 条件逐项判定（V10.3 重构）----------
        # ★ 核心修正：趋势条件从「绝对」改为「相对」。
        #   原实现：c_trend 要求 nav > MA20（绝对位置条件）。
        #   问题：主线在启动初期/加速段常常**尚未站上 MA20**——
        #     2026 上半年半导体行情（512480.SH 从 4/2 的 132 涨到 6/30 的 281，
        #     +112.7%）在 6/1~6/12 期间 ex60 高达 27~38%（全年最强），
        #     但因为 5 月冲高后 MA20 被抬高，nav 连续 8 个交易日站不上 MA20，
        #     → 整段被判「无主线」，漏掉了这波最赚钱的行情。
        #   新口径：只要相对强度达标，且**距 20 日高点的回撤在容忍范围内**，
        #     即认定趋势成立（趋势未破）。回撤容忍度由配置给出。
        c_trend = True
        if tr.get("require_above_ma20") and ma20 is not None:
            # 站上 MA20 **或** 回撤未超过容忍度（相对口径）
            dd_ok = (dd20 is not None and dd20 >= tr.get("max_drawdown_from_high_pct", -12.0))
            c_trend = c_trend and (cur > ma20 or dd_ok)
        if tr.get("require_above_ma60") and ma60 is not None:
            c_trend = c_trend and cur > ma60
        # ★ V10.3：ret5 / ret20 的绝对收益门槛**取消**（默认 -99 = 不限制）。
        #   依据（验证段 F1，前后半段分割）：
        #     ret5>=0 & ret20>=1  → 验证 F1 10.8
        #     不限制              → 验证 F1 24.4
        #   原因：近 5/20 日绝对收益是**绝对量**指标，而主线判定应由
        #   **相对强度**（相对基准的超额）主导。在震荡市里绝对收益噪声极大：
        #   2026-06-10 电子行业 ex60=37.7%（全年最强）却 ret5=-3.65%、ret20=+0.07%
        #   → 被绝对门槛否掉，整段半导体行情漏判。
        #   绝对收益已由「回撤容忍 + 相对强度」充分覆盖。
        if ret5 is not None and tr.get("min_ret5", -99) > -90:
            c_trend = c_trend and ret5 >= tr["min_ret5"]
        if ret20 is not None and tr.get("min_ret20", -99) > -90:
            c_trend = c_trend and ret20 >= tr["min_ret20"]

        c_rs = True
        if ex20 is not None:
            c_rs = c_rs and ex20 >= rs["min_excess_20d"]
        if ex60 is not None:
            c_rs = c_rs and ex60 >= rs["min_excess_60d"]

        # ★ V10.3：量能降级为**加分项**，不再是否决项。
        #   实测（2026 全年 4984 个「行业×交易日」样本，用未来 20 日相对基准
        #   超额 >5% 作事实标签）：把 amt_ratio>=1.15 作为硬门槛会让验证段
        #   F1 从 24.4 掉到 5.9 —— 因为主线启动初期往往缩量。
        #   原因：量能是「确认」信号，不是「识别」信号。
        c_money = (amt_ratio is not None and amt_ratio >= mf["min_amt_ratio"])

        # ★ V10.3：宽度同样降级为**加分项**。
        #   实测：把 above20_ratio>=0.5 作为硬门槛，验证段 F1 从 24.4 掉到 14.2。
        #   反直觉数据：etf_count=1（只有一只 ETF 的行业）的准确率 37%，
        #   反而高于 4 只 ETF 的 26% —— ETF 数量反映的是**行业 ETF 供给密度**，
        #   不是主线质量，原「防单只孤军」的假设在数据上不成立。
        c_breadth = True
        if etf_count >= bd["min_industry_etf_count"]:
            c_breadth = above20 >= bd["min_above20_ratio"]
        else:
            c_breadth = False

        # ★ V10.3 最终判定：相对强度 + 趋势（相对口径）为**必要条件**；
        #   量能与宽度作为**加分项**，达到 bonus 阈值即通过。
        #   这样既保留「放量确认」的加分，又不会因单日缩量而漏掉主线。
        bonus = 0
        if c_money: bonus += 1
        if c_breadth: bonus += 1
        need_bonus = int(tr.get("min_bonus_score", 0))
        passed = c_trend and c_rs and (bonus >= need_bonus)
        details[ind] = {
            "industry": ind,
            "nav": round(float(cur), 3),
            "ret5": round(ret5, 2) if ret5 is not None else None,
            "ret20": round(ret20, 2) if ret20 is not None else None,
            "ret60": round(ret60, 2) if ret60 is not None else None,
            "excess20": round(ex20, 2) if ex20 is not None else None,
            "excess60": round(ex60, 2) if ex60 is not None else None,
            "above_ma20": (ma20 is not None and cur > ma20),
            "above_ma60": (ma60 is not None and cur > ma60),
            "dd20": round(dd20, 2) if dd20 is not None else None,
            "bonus_score": bonus,
            "amt_ratio": round(amt_ratio, 2) if amt_ratio is not None else None,
            "up_ratio": round(up_ratio, 3),
            "above20_ratio": round(above20, 3),
            "etf_count": etf_count,
            "c_trend": c_trend, "c_rs": c_rs,
            "c_money": c_money, "c_breadth": c_breadth,
            "passed": passed,
        }

    # ---- 排名 + 主线判定 ----
    def _rank_key(ind):
        d = details[ind]
        return (d["excess20"] if d["excess20"] is not None else -999,
                d["excess60"] if d["excess60"] is not None else -999)

    ranked = sorted(details.keys(), key=_rank_key, reverse=True)
    top_n = rs["rank_top_n"]
    qualified = []
    for ind in ranked:
        d = details[ind]
        if not d["passed"]:
            continue
        # 必须进入相对强度前 N（主线必须是相对强势的少数行业）
        if ranked.index(ind) < top_n:
            qualified.append(ind)

    ml = cfg["mainline"]
    # 主线持续天数（days_strong）参与判定：
    #   主线必须是**持续**的强势行业，而不是单日脉冲。
    #   但允许「首日建仓」—— 即 days_strong 达标（含首日=1）的都算候选，
    #   再按相对强度排序取前 N。
    for ind in qualified:
        details[ind]["days_strong"] = _count_strong_days(
            series[ind], details[ind], td, cfg, bench_nav=bench_nav)

    min_days = int(ml.get("min_days_strong", 1))
    qualified = [ind for ind in qualified
                 if details[ind].get("days_strong", 1) >= min_days]

    industries = qualified[:max(1, int(ml.get("min_industries", 1)))]

    result = {
        "industries": industries,
        "details": details,
        "missing": False,
        "note": "",
        "bench": {
            "ret20": round(bench_ret20, 2) if bench_ret20 is not None else None,
            "ret60": round(bench_ret60, 2) if bench_ret60 is not None else None,
        },
    }
    _RESULT_CACHE[cache_key] = result
    return result


_STRONG_DAYS_CACHE = {}


def _count_strong_days(ind_series: pd.DataFrame, detail: dict,
                       trade_date: str, cfg: dict,
                       bench_nav=None) -> int:
    """回溯该行业连续满足主线条件的天数（days_strong）。

    ★ 窗口长度必须 ≥ max(ma60_window, amt_window) + 缓冲，
      否则循环第一步就因「历史不足」break，days_strong 恒为 1
      （原实现用 tail(30) 而 ma60_window=60 → 必然恒 1）。
    """
    cache_key = (id(ind_series), str(trade_date))
    if cache_key in _STRONG_DAYS_CACHE:
        return _STRONG_DAYS_CACHE[cache_key]
    ma20w = cfg["trend"]["ma20_window"]
    ma60w = cfg["trend"]["ma60_window"]
    amtw = cfg["money_flow"]["amt_window"]
    need = max(ma60w, amtw) + 25
    sub = ind_series[ind_series["trade_date"] <= trade_date].tail(need)
    if len(sub) < max(ma60w, amtw) + 1:
        return 1
    nav = sub["nav"].values
    amt = sub["amount"].values
    # ★ bench_nav 与 nav 对齐：bench_nav 是「截止 td 的全量」，
    #   而 nav 是「末尾 need 行」→ 必须同步截尾，否则索引错位。
    if bench_nav is not None:
        bench_nav = np.asarray(bench_nav)[-len(nav):]
    tr = cfg["trend"]
    rs = cfg["relative_strength"]
    mf = cfg["money_flow"]
    dd_tol = float(tr.get("max_drawdown_from_high_pct", -12.0))
    ret5_min = float(tr.get("min_ret5", -99.0))
    ret20_min = float(tr.get("min_ret20", -99.0))
    ex20_min = float(rs.get("min_excess_20d", 0.0))
    ex60_min = float(rs.get("min_excess_60d", 5.0))

    # ★ V10.3：days_strong 的回溯必须与主判定**同口径**。
    #   原实现用旧的绝对条件（nav<=MA20 立即 break + ret5/ret20 门槛 + 量能门槛），
    #   与 V10.3 的「相对强度 + 回撤容忍」判定脱节 →
    #   2026-06-01 半导体在 MA20 下方整理时被判 days_strong=1（连续计数清零），
    #   明明是同一波行情（4/30 起连续 25 天）却被切成两段。
    #   现改为：趋势用相对口径（站上 MA20 **或** 回撤未破），
    #   相对强度用「与当日 detail 相同的方向」（超额为正且达标则不中断），
    #   量能/宽度作为加分项不中断连续性。
    days = 0
    for i in range(len(nav) - 1, -1, -1):
        if i < max(ma60w, amtw):
            break
        ma20 = float(np.mean(nav[max(0, i - ma20w + 1):i + 1]))
        amt_ma = float(np.mean(amt[max(0, i - amtw + 1):i + 1]))
        ret20 = (nav[i] / nav[i - 20] - 1) * 100 if i >= 20 else None
        ret5 = (nav[i] / nav[i - 5] - 1) * 100 if i >= 5 else None
        hi20 = float(np.max(nav[max(0, i - 19):i + 1]))
        dd20 = (nav[i] / hi20 - 1) * 100

        # 趋势（相对口径）：站上 MA20 或 回撤未破
        trend_ok = (nav[i] > ma20) or (dd20 >= dd_tol)
        if not trend_ok:
            break
        # 绝对收益门槛（V10.3 已默认取消，-99 表示不限制）
        if ret5_min > -90 and ret5 is not None and ret5 < ret5_min:
            break
        if ret20_min > -90 and ret20 is not None and ret20 < ret20_min:
            break
        # 相对强度（逐日重算，与主判定同口径）：
        #   历史日的行业收益 - 历史日的基准收益 >= ex60_min。
        #   bench_nav 由调用方通过 detail 传入（与 detect_mainlines 同一基准），
        #   保证回溯与主判定用**同一个基准序列**。
        if bench_nav is not None and i >= 60:
            br60 = (bench_nav[i] / bench_nav[i - 60] - 1) * 100
            br20 = (bench_nav[i] / bench_nav[i - 20] - 1) * 100
            r60_i = (nav[i] / nav[i - 60] - 1) * 100
            r20_i = (nav[i] / nav[i - 20] - 1) * 100
            ex60_i = r60_i - br60
            ex20_i = r20_i - br20
            if ex60_i < ex60_min and ex20_i < ex20_min:
                break        # 相对强度已明显转弱 → 连续计数中断
        days += 1
    out = max(1, days)
    _STRONG_DAYS_CACHE[cache_key] = out
    return out


# ============================================================
# CLI
# ============================================================
def main():
    import argparse
    ap = argparse.ArgumentParser(description="行业 ETF 主线识别")
    ap.add_argument("trade_date", nargs="?", help="交易日 YYYYMMDD")
    ap.add_argument("--fetch", action="store_true", help="先拉取 ETF 行情")
    ap.add_argument("--start", default="20210101", help="拉取起始日")
    ap.add_argument("--top", type=int, default=10, help="显示前 N 个行业")
    args = ap.parse_args()

    if args.fetch:
        print("拉取行业 ETF 行情…")
        n = fetch_etf_history(start_date=args.start)
        print(f"✓ 完成，{n} 只 ETF")

    panel = load_etf_panel()
    if panel.empty:
        print("ETF 缓存为空，请先 --fetch")
        return
    print(f"ETF 面板: {panel['trade_date'].nunique()} 个交易日 × "
          f"{panel['ts_code'].nunique()} 只")

    td = args.trade_date or panel["trade_date"].max()
    cfg = load_config()
    r = detect_mainlines(td, cfg, panel)

    print()
    print("=" * 74)
    print(f"ETF 主线识别  {td}")
    print("=" * 74)
    print(f"基准（行业等权）: 20日 {r['bench'].get('ret20')}%  60日 {r['bench'].get('ret60')}%")
    print()
    if r["industries"]:
        print(f"★ 主线行业（{len(r['industries'])} 个）: {', '.join(r['industries'])}")
    else:
        print("★ 无主线行业")
    print()
    print(f"{'行业':<10}{'超额20':>8}{'超额60':>8}{'ret20':>8}{'量比':>7}"
          f"{'宽度':>7}{'连续':>6}  判定")
    print("-" * 74)
    ranked = sorted(r["details"].keys(),
                    key=lambda i: (r["details"][i]["excess20"]
                                   if r["details"][i]["excess20"] is not None else -999),
                    reverse=True)[:args.top]
    for ind in ranked:
        d = r["details"][ind]
        flags = "".join(["T" if d["c_trend"] else "·",
                         "R" if d["c_rs"] else "·",
                         "M" if d["c_money"] else "·",
                         "B" if d["c_breadth"] else "·"])
        ds = d.get("days_strong", "")
        mark = "★" if ind in r["industries"] else " "
        print(f"{mark}{ind:<9}{d['excess20'] if d['excess20'] is not None else 0:>8.1f}"
              f"{d['excess60'] if d['excess60'] is not None else 0:>8.1f}"
              f"{d['ret20'] if d['ret20'] is not None else 0:>8.1f}"
              f"{d['amt_ratio'] if d['amt_ratio'] is not None else 0:>7.2f}"
              f"{d['up_ratio']:>7.2f}{str(ds):>6}  {flags}")


if __name__ == "__main__":
    main()
