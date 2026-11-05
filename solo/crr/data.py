# -*- coding: utf-8 -*-
"""
CRR V1.0 数据访问层

职责：把「现有 Tushare 本地缓存（SQLite + parquet）+ 本地 sli/industry DB」封装成
      上层模块可直接调用的原语，并统一执行三条硬约束：

  1. 只用本地缓存，禁止引入 TDX；不因接口缺失编造数据，缺失一律以 NaN/UNKNOWN 返回。
  2. as-of 防未来：行情 trade_date<=scan_date；财务 ann_date<=scan_date；
     SLI eval_date<=scan_date；sw_member 时点 in_date/out_date 过滤。
     **无 ann_date 的 parquet 家族（fina_indicator_q_/yoy_）不可作 as-of 依据，直接判 UNKNOWN。**
  3. 一次加载单一价格面板（日期×代码）复用于市场/行业/个股收益；深度财务/估值
     只对候选股按需读取，避免全市场 × 上万 parquet 的开销。

对外主要 API：
  股票池/日历   load_stock_basic / get_stock_pool / get_name_map / get_board
                get_trade_dates / last_trade_date_on_or_before
  价格面板      load_price_panel -> PricePanel
  市场聚合      build_market_aggregate(panel, name_map)
  行业          load_industry_map / resolve_industry_labels / build_industry_returns
  估值          load_valuation_history
  财务 as-of    load_fina_asof / load_fina_series / load_sli_asof / load_forecast
  parquet 深度  get_parquet_index / load_parquet_statements
  审计          audit_data_sources
"""
import os
import re
import glob
import sqlite3
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import (
    CACHE_DB_PATH, STOCK_BASIC_CSV, PARQUET_DIR, SLI_DB, INDUSTRY_DB,
    MARKET, MISPRICING, VALUATION, UNKNOWN, SCORE_CAP, PENDING_MODULES,
    DATA_SOURCES,
)

# ----------------------------------------------------------------------------
# 通用工具
# ----------------------------------------------------------------------------

def norm_date(x) -> str:
    """把 int/float/str/Timestamp 统一成 'YYYYMMDD' 字符串（等长可字典序比较）。"""
    s = str(x).strip()
    s = s.split(" ")[0]
    if s.endswith(".0"):
        s = s[:-2]
    s = "".join(ch for ch in s if ch.isdigit())
    return s[:8]


def _to_date_num(s) -> pd.Series:
    """任意日期列 -> float 数值（YYYYMMDD），非法为 NaN，便于 <= int(scan_date) 比较。

    兼容 float 型 20260828.0、带分隔符 2026-08-28、时间戳 2026-08-28 00:00:00：
    先去掉所有非数字字符，再统一截取前 8 位，避免 '.0' 变成多一位导致数值比
    8 位的 scan_date 大一个量级、被 as-of 全部误过滤。
    """
    cleaned = (pd.Series(s).astype(str)
               .str.replace(r"[^0-9]", "", regex=True)
               .str[:8]
               .replace("", np.nan))
    return pd.to_numeric(cleaned, errors="coerce")


def _read_sql(db_path: str, query: str, params=()) -> pd.DataFrame:
    """只读查询，显式关闭连接。"""
    con = sqlite3.connect(db_path, timeout=30.0)
    try:
        return pd.read_sql_query(query, con, params=params)
    finally:
        con.close()


def _table_cols(db_path: str, table: str) -> list:
    """PRAGMA 取真实列名；表不存在返回 []。用于动态列检测，避免硬编码列缺失报错。"""
    try:
        d = _read_sql(db_path, f"PRAGMA table_info({table})")
        return d["name"].tolist()
    except Exception:
        return []


def _pick(cols, wanted) -> list:
    """返回 wanted 中真实存在于 cols 的列（保持 wanted 顺序）。"""
    return [c for c in wanted if c in cols]


_TRADE_DATES_CACHE = None


def get_trade_dates(start_date, end_date) -> list:
    """交易日历：daily_cache 的 distinct trade_date（升序，字符串 YYYYMMDD）。"""
    global _TRADE_DATES_CACHE
    if _TRADE_DATES_CACHE is None:
        df = _read_sql(CACHE_DB_PATH, "SELECT DISTINCT trade_date FROM daily_cache ORDER BY trade_date")
        _TRADE_DATES_CACHE = [norm_date(d) for d in df["trade_date"].tolist()]
    s, e = norm_date(start_date), norm_date(end_date)
    return [d for d in _TRADE_DATES_CACHE if s <= d <= e]


def last_trade_date_on_or_before(date) -> str:
    d = norm_date(date)
    dates = get_trade_dates("19900101", d)
    return dates[-1] if dates else None


# ----------------------------------------------------------------------------
# 股票池 / 名称 / 板块
# ----------------------------------------------------------------------------

def load_stock_basic() -> pd.DataFrame:
    if os.path.exists(STOCK_BASIC_CSV):
        return pd.read_csv(STOCK_BASIC_CSV, dtype={"ts_code": str, "list_date": str})
    return pd.DataFrame(columns=["ts_code", "name", "industry", "list_date"])


def get_stock_pool(exclude_st: bool = True) -> pd.DataFrame:
    """A 股票池：沪深主板/创业板/科创板，排除北交所、退市、ST。附加 board 列。"""
    sb = load_stock_basic()
    if sb.empty:
        return sb
    pool = sb[sb["ts_code"].str.match(r"^(60|68|00|30)", na=False)].copy()
    pool = pool[~pool["ts_code"].str.endswith(".BJ", na=False)]
    if "name" in pool.columns:
        pool = pool[~pool["name"].astype(str).str.contains("退", na=False)]
        if exclude_st:
            pool = pool[~pool["name"].astype(str).str.upper().str.contains("ST", na=False)]
    pool["board"] = [get_board(c, n) for c, n in zip(pool["ts_code"], pool.get("name", ""))]
    return pool.reset_index(drop=True)


def get_name_map() -> dict:
    sb = load_stock_basic()
    if sb.empty:
        return {}
    return dict(zip(sb["ts_code"], sb["name"]))


def get_board(ts_code: str, name: str = "") -> str:
    """板块归类（用于涨跌停阈值代理）：main / gem_star / bj / st。"""
    if "ST" in str(name).upper():
        return "st"
    sym = str(ts_code).split(".")[0]
    if sym.startswith(("30", "68")):
        return "gem_star"
    if sym.startswith(("8", "4", "92")):
        return "bj"
    return "main"


# ----------------------------------------------------------------------------
# 价格面板（单次加载，市场/行业/个股收益复用）
# ----------------------------------------------------------------------------

_PANEL_COLS = ["pct_chg", "close", "amount", "vol"]


@dataclass
class PricePanel:
    """日期×代码的宽表。ret 为小数日收益(pct_chg/100)，pct_chg 为百分数(判涨跌停用)。"""
    dates: list
    codes: list
    ret: pd.DataFrame
    pct_chg: pd.DataFrame
    close: pd.DataFrame
    amount: pd.DataFrame
    vol: pd.DataFrame

    def slice_codes(self, codes) -> "PricePanel":
        cs = [c for c in codes if c in self.codes]
        return PricePanel(
            self.dates, cs,
            self.ret[cs], self.pct_chg[cs], self.close[cs],
            self.amount[cs], self.vol[cs],
        )


def load_price_panel(end_date, lookback: int = 320, codes=None) -> PricePanel:
    """加载 <=end_date 最近 lookback 个交易日的全市场宽表。

    复权收益口径：daily_cache.pct_chg 为相对 pre_close 的涨跌幅（含除权调整），
    逐日累乘即复权收益；此处保留日频小数收益 ret=pct_chg/100 供累加/回归使用。
    """
    end = norm_date(end_date)
    dates = get_trade_dates("19900101", end)
    if not dates:
        raise ValueError(f"daily_cache 无 <= {end} 的交易日")
    win = dates[-int(lookback):]
    start = win[0]
    df = _read_sql(
        CACHE_DB_PATH,
        "SELECT ts_code, trade_date, pct_chg, close, amount, vol FROM daily_cache "
        "WHERE trade_date>=? AND trade_date<=?",
        (start, end),
    )
    df["trade_date"] = df["trade_date"].map(norm_date)
    # 去重保护：同一 (trade_date, ts_code) 保留最后一条，避免 pivot 抛重复索引错
    df = df.drop_duplicates(subset=["trade_date", "ts_code"], keep="last")
    frames = {}
    for col in _PANEL_COLS:
        w = df.pivot(index="trade_date", columns="ts_code", values=col)
        w = w.reindex(index=win)
        w = w.astype("float64")
        frames[col] = w
    ret = frames["pct_chg"] / 100.0
    all_codes = list(frames["pct_chg"].columns)
    if codes is not None:
        keep = [c for c in codes if c in all_codes]
        frames = {k: v[keep] for k, v in frames.items()}
        ret = ret[keep]
        all_codes = keep
    return PricePanel(
        dates=win, codes=all_codes,
        ret=ret, pct_chg=frames["pct_chg"], close=frames["close"],
        amount=frames["amount"], vol=frames["vol"],
    )


# ----------------------------------------------------------------------------
# 市场日度聚合（供 market.py 状态机；派生窗口指标在 market.py 计算）
# ----------------------------------------------------------------------------

def build_market_aggregate(panel: PricePanel, name_map: dict,
                           codes=None) -> pd.DataFrame:
    """全 A 日度基础聚合。返回 index=trade_date 的 DataFrame：

      ret_mean    等权日收益(小数)
      ret_median  个股收益中位数(小数)
      down_ratio / up_ratio   下跌/上涨家数占比
      limit_down / limit_up   跌停/涨停家数（pct_chg 板块阈值代理）
      amount      全市场成交额(千元)
      n_eff       有效股票数(|pct_chg|<=clip 且非 NaN)

    异常 |pct_chg|>clip(31) 视为新股/复牌/异常，剔除不参与统计。
    """
    cfg = MARKET
    clip = cfg["limit_anomaly_clip"]
    p = panel if codes is None else panel.slice_codes(codes)
    pct = p.pct_chg
    valid = pct.where(pct.abs() <= clip)

    r = valid / 100.0
    n_eff = valid.notna().sum(axis=1)
    down = (valid < 0).sum(axis=1)
    up = (valid > 0).sum(axis=1)

    th_cfg = cfg["limit_board_thresholds"]
    thr = pd.Series(
        {c: th_cfg.get(get_board(c, name_map.get(c, "")), th_cfg["main"])
         for c in p.codes},
        dtype="float64",
    )
    ld = (valid.le(-thr, axis=1) & valid.abs().le(clip)).sum(axis=1)
    lu = (valid.ge(thr, axis=1) & valid.abs().le(clip)).sum(axis=1)

    n_eff_f = n_eff.astype("float64").replace(0, np.nan)
    out = pd.DataFrame({
        "ret_mean": r.mean(axis=1),
        "ret_median": r.median(axis=1),
        "down_ratio": down / n_eff_f,
        "up_ratio": up / n_eff_f,
        "limit_down": ld.astype("int64"),
        "limit_up": lu.astype("int64"),
        "amount": p.amount.sum(axis=1, min_count=1),
        "n_eff": n_eff,
    })
    out.index.name = "trade_date"
    return out


# ----------------------------------------------------------------------------
# 行业映射与自建行业收益
# ----------------------------------------------------------------------------

# SLI leaderboard_v2 中本模块实际用到的列（probe 已确认全部存在）
_SLI_COLS = [
    "ts_code", "name", "l1_name", "l2_name", "l3_name", "eval_date", "is_st",
    "roe", "roic", "roe_dt", "grossprofit_margin", "netprofit_margin",
    "gm_prev", "roe_prev", "or_yoy", "netprofit_yoy", "q_profit_yoy",
    "dt_netprofit_yoy", "ocf_to_profit", "total_assets", "revenue",
    "total_cogs", "n_income_attr_p", "pe_ttm", "pb", "total_mv",
]
_SLI_CACHE = None


def _sli_all() -> pd.DataFrame:
    """全表读 leaderboard_v2（约 4.7万行，可全内存），动态取列交集，缓存。"""
    global _SLI_CACHE
    if _SLI_CACHE is None:
        cols = _table_cols(SLI_DB, "leaderboard_v2")
        use = _pick(cols, _SLI_COLS)
        if "ts_code" not in use:
            _SLI_CACHE = pd.DataFrame(columns=["ts_code", "eval_date"])
            return _SLI_CACHE
        df = _read_sql(SLI_DB, f"SELECT {','.join(use)} FROM leaderboard_v2")
        df["eval_date_num"] = _to_date_num(df["eval_date"]) if "eval_date" in df else np.nan
        _SLI_CACHE = df
    return _SLI_CACHE


def load_industry_map(scan_date) -> pd.DataFrame:
    """ts_code -> 申万行业。优先 SLI l1/l2/l3（eval_date<=scan_date 最新快照），
    缺失部分回退 sw_member 申万一级（按时点 in_date/out_date 过滤）。

    返回列 [ts_code, l1, l2, l3, source]；source ∈ {sli, sw_l1, UNKNOWN}。
    """
    sd = int(norm_date(scan_date))
    sli = _sli_all()
    sli_map = pd.DataFrame(columns=["l1", "l2", "l3", "source"])
    if not sli.empty and {"l1_name", "l2_name", "l3_name"}.issubset(sli.columns):
        ok = sli[sli["eval_date_num"].notna() & (sli["eval_date_num"] <= sd)].copy()
        if not ok.empty:
            latest = (ok.sort_values("eval_date_num")
                      .groupby("ts_code", as_index=False).tail(1)
                      .set_index("ts_code"))
            sli_map = latest[["l1_name", "l2_name", "l3_name"]].copy()
            sli_map.columns = ["l1", "l2", "l3"]
            sli_map["source"] = "sli"

    # sw_member 时点过滤 + 申万一级行业名（回退）
    sw_map = pd.DataFrame(columns=["l1"])
    try:
        sw = _read_sql(
            INDUSTRY_DB,
            "SELECT m.con_code AS ts_code, m.in_date, m.out_date, "
            "i.industry_name AS l1, i.level "
            "FROM sw_member m JOIN sw_industry i ON m.index_code=i.index_code",
        )
        if not sw.empty:
            sw["in_num"] = _to_date_num(sw["in_date"])
            sw["out_num"] = _to_date_num(sw["out_date"])
            eff = sw[(sw["in_num"].notna()) & (sw["in_num"] <= sd) &
                     (sw["out_num"].isna() | (sw["out_num"] > sd))]
            eff = eff.sort_values("in_num").groupby("ts_code", as_index=False).tail(1)
            sw_map = eff.set_index("ts_code")[["l1"]]
    except Exception:
        sw_map = pd.DataFrame(columns=["l1"])

    # 合并：SLI 为主，逐列用 sw_l1 补 l1
    all_codes = sorted(set(sli_map.index) | set(sw_map.index))
    out = pd.DataFrame(index=pd.Index(all_codes, name="ts_code"))
    out["l1"] = sli_map["l1"].reindex(out.index) if "l1" in sli_map else UNKNOWN
    out["l2"] = sli_map["l2"].reindex(out.index) if "l2" in sli_map else UNKNOWN
    out["l3"] = sli_map["l3"].reindex(out.index) if "l3" in sli_map else UNKNOWN
    out["source"] = sli_map["source"].reindex(out.index) if "source" in sli_map else UNKNOWN
    sw_l1 = sw_map["l1"].reindex(out.index) if "l1" in sw_map else pd.Series(dtype=object)
    need_l1 = out["l1"].isna() | (out["l1"].astype(str).isin(["", "nan", UNKNOWN]))
    out.loc[need_l1, "l1"] = sw_l1[need_l1].fillna(UNKNOWN)
    out.loc[need_l1 & sw_l1.notna(), "source"] = "sw_l1"
    for c in ("l1", "l2", "l3"):
        out[c] = out[c].fillna(UNKNOWN).replace("", UNKNOWN)
    out["source"] = out["source"].fillna(UNKNOWN).replace("", UNKNOWN)
    return out.reset_index()


def resolve_industry_labels(industry_map: pd.DataFrame, level: str = "l2") -> dict:
    """从 load_industry_map 结果生成 ts_code -> 行业标签。

    优先 level 指定级别（默认申万二级 l2），逐级回退 l1，全 UNKNOWN 则跳过该股。
    返回 dict[ts_code] -> label（label 带级别前缀，避免不同级别同名混淆）。
    """
    order = {"l2": ["l2", "l1"], "l1": ["l1", "l2"], "l3": ["l3", "l2", "l1"]}.get(level, ["l2", "l1"])
    labels = {}
    if industry_map is None or industry_map.empty:
        return labels
    for row in industry_map.itertuples(index=False):
        d = row._asdict() if hasattr(row, "_asdict") else {}
        code = d.get("ts_code")
        if not code:
            continue
        for lv in order:
            v = d.get(lv)
            if v is not None and str(v) not in ("", "nan", UNKNOWN):
                labels[code] = f"{lv}:{v}"
                break
    return labels


def build_industry_returns(panel: PricePanel, label_by_code: dict,
                           min_members: int = None):
    """自建行业日收益：每个行业取成分股当日 pct_chg 中位数（小数收益）。

    返回 (ind_ret, stock_ind_ret)：
      ind_ret        DataFrame[trade_date × 行业label]，成分数 < min_members 的行业剔除
      stock_ind_ret  DataFrame，与 panel.ret 同形状，每列 = 该股所属行业当日收益；
                     无行业归属或行业被剔除则为 NaN（mispricing 中据此跳过/回退）。
    """
    if min_members is None:
        min_members = MISPRICING["min_industry_members"]
    r = panel.ret
    groups = {}
    for c, lab in label_by_code.items():
        if c in r.columns and lab:
            groups.setdefault(lab, []).append(c)

    ind_ret = pd.DataFrame(index=r.index)
    valid_labels = set()
    for lab, codes in groups.items():
        if len(codes) >= min_members:
            ind_ret[lab] = r[codes].median(axis=1)
            valid_labels.add(lab)
    ind_ret.index.name = "trade_date"

    stock_ind_ret = pd.DataFrame(np.nan, index=r.index, columns=r.columns)
    for lab, codes in groups.items():
        if lab in valid_labels:
            for c in codes:
                stock_ind_ret[c] = ind_ret[lab]
    return ind_ret, stock_ind_ret


# ----------------------------------------------------------------------------
# 估值历史（daily_basic_cache 大表，必须 SQL WHERE 过滤，按股分组）
# ----------------------------------------------------------------------------

_VAL_COLS = ["ts_code", "trade_date", "pe_ttm", "pb", "total_mv", "ps", "dv_ttm",
             "circ_mv", "turnover_rate", "dv_ratio"]


def load_valuation_history(ts_codes, scan_date, years: int = None) -> dict:
    """按候选股读取 <=scan_date 最近 years 年的 daily_basic_cache 估值序列。

    返回 dict[ts_code] -> DataFrame[trade_date, pe_ttm, pb, total_mv, ...]（升序）。
    大表按 500 代码一批 IN 查询，避免单次 SQL 过长；缺列自动忽略。
    """
    if years is None:
        years = VALUATION["percentile_years"]
    sd = norm_date(scan_date)
    dates = get_trade_dates("19900101", sd)
    need = int(years * 250)
    win = dates[-need:] if len(dates) > need else dates
    if not win:
        return {}
    start = win[0]
    cols = _table_cols(CACHE_DB_PATH, "daily_basic_cache")
    use = _pick(cols, _VAL_COLS)
    if "ts_code" not in use:
        return {}
    codes = [c for c in dict.fromkeys(ts_codes)]
    parts = []
    for i in range(0, len(codes), 500):
        batch = codes[i:i + 500]
        ph = ",".join("?" * len(batch))
        q = (f"SELECT {','.join(use)} FROM daily_basic_cache "
             f"WHERE trade_date>=? AND trade_date<=? AND ts_code IN ({ph})")
        parts.append(_read_sql(CACHE_DB_PATH, q, (start, sd, *batch)))
    if not parts:
        return {}
    df = pd.concat(parts, ignore_index=True)
    if df.empty:
        return {}
    df["trade_date"] = df["trade_date"].map(norm_date)
    for c in ("pe_ttm", "pb", "total_mv", "ps", "dv_ttm", "circ_mv", "turnover_rate"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    out = {}
    for code, g in df.groupby("ts_code"):
        out[code] = g.sort_values("trade_date").reset_index(drop=True)
    return out


# ----------------------------------------------------------------------------
# 财务 as-of（fina_indicator_cache / SLI 均小表，全表读入内存后 as-of 过滤）
# ----------------------------------------------------------------------------

_FINA_CACHE = None


def _fina_all() -> pd.DataFrame:
    global _FINA_CACHE
    if _FINA_CACHE is None:
        cols = _table_cols(CACHE_DB_PATH, "fina_indicator_cache")
        if "ts_code" not in cols:
            _FINA_CACHE = pd.DataFrame(columns=["ts_code", "ann_date_num", "end_date_num"])
            return _FINA_CACHE
        df = _read_sql(CACHE_DB_PATH, f"SELECT {','.join(cols)} FROM fina_indicator_cache")
        df["ann_date_num"] = _to_date_num(df["ann_date"]) if "ann_date" in df else np.nan
        df["end_date_num"] = _to_date_num(df["end_date"]) if "end_date" in df else np.nan
        for c in cols:
            if c not in ("ts_code", "ann_date", "end_date"):
                df[c] = pd.to_numeric(df[c], errors="coerce") if df[c].dtype == object else df[c]
        _FINA_CACHE = df
    return _FINA_CACHE


def load_fina_series(ts_codes, scan_date, n: int = 8) -> pd.DataFrame:
    """每只候选股 ann_date<=scan_date 的最近 n 期财务指标（按 end_date 降序取 n 再升序返回）。

    用于「最近三季度业绩变化」「经营现金流连续为负」等 as-of 时序判断。
    返回长表，含 ts_code / ann_date / end_date / ann_date_num / end_date_num / 各指标列。
    """
    sd = int(norm_date(scan_date))
    fina = _fina_all()
    if fina.empty:
        return fina.copy()
    codes = set(ts_codes)
    df = fina[fina["ts_code"].isin(codes) &
              fina["ann_date_num"].notna() & (fina["ann_date_num"] <= sd)].copy()
    if df.empty:
        return df
    # 同一报告期多次公告取 ann_date 最新一条
    df = (df.sort_values(["end_date_num", "ann_date_num"])
            .drop_duplicates(subset=["ts_code", "end_date_num"], keep="last"))
    df = (df.sort_values(["ts_code", "end_date_num"], ascending=[True, False])
            .groupby("ts_code", as_index=False).head(n))
    return df.sort_values(["ts_code", "end_date_num"]).reset_index(drop=True)


def load_fina_asof(ts_codes, scan_date) -> pd.DataFrame:
    """每只候选股 ann_date<=scan_date 最新一期财务指标（一行/股）。"""
    return (load_fina_series(ts_codes, scan_date, n=1)
            .drop_duplicates(subset=["ts_code"], keep="last")
            .reset_index(drop=True))


def load_sli_asof(ts_codes, scan_date) -> pd.DataFrame:
    """每只候选股 eval_date<=scan_date 最新 SLI 快照（一行/股），提供 roic/扣非/毛利趋势等增强。"""
    sd = int(norm_date(scan_date))
    sli = _sli_all()
    if sli.empty:
        return sli.copy()
    codes = set(ts_codes)
    df = sli[sli["ts_code"].isin(codes) &
             sli["eval_date_num"].notna() & (sli["eval_date_num"] <= sd)].copy()
    if df.empty:
        return df
    df = (df.sort_values("eval_date_num")
            .groupby("ts_code", as_index=False).tail(1)
            .reset_index(drop=True))
    return df


# ----------------------------------------------------------------------------
# 业绩预告 as-of（forecast_vip_*.parquet，按抓取快照日期选最新 <= scan_date 的一份）
# ----------------------------------------------------------------------------

def load_forecast(scan_date, ts_codes=None) -> pd.DataFrame:
    """读取 <=scan_date 的业绩预告（取文件名尾部快照日期 <= scan_date 的最新一份）。

    返回列 [ts_code, ann_date, end_date, type, p_change_min, p_change_max, ...]，
    按 (ts_code, end_date) 取 ann_date 最新一条；as-of 过滤 ann_date<=scan_date。
    本地仅覆盖 20260630 中报预告，其余报告期无数据 → 由上层判 UNKNOWN。
    """
    sd = norm_date(scan_date)
    files = glob.glob(os.path.join(PARQUET_DIR, "forecast_vip_*.parquet"))
    if not files:
        return pd.DataFrame(columns=["ts_code", "ann_date", "end_date", "type",
                                    "p_change_min", "p_change_max"])
    best, best_snap = None, ""
    for f in files:
        nums = re.findall(r"(\d{8})", os.path.basename(f))
        snap = nums[-1] if nums else ""
        if snap <= sd and snap >= best_snap:
            best, best_snap = f, snap
    if best is None:
        return pd.DataFrame(columns=["ts_code", "ann_date", "end_date", "type",
                                    "p_change_min", "p_change_max"])
    df = pd.read_parquet(best)
    if "ann_date" not in df.columns:
        return pd.DataFrame(columns=["ts_code", "ann_date", "end_date", "type",
                                    "p_change_min", "p_change_max"])
    df["ann_date"] = df["ann_date"].map(norm_date)
    df = df[df["ann_date"] <= sd].copy()
    if ts_codes is not None:
        df = df[df["ts_code"].isin(set(ts_codes))]
    for c in ("p_change_min", "p_change_max", "net_profit_min", "net_profit_max"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "end_date" in df.columns:
        df["end_date"] = df["end_date"].map(norm_date)
    # 同一报告期多次预告（修正/更新）取 ann_date 最新一条
    df = df.sort_values(["ts_code", "end_date", "ann_date"])
    df = df.drop_duplicates(subset=["ts_code", "end_date"], keep="last")
    return df.reset_index(drop=True)


# ----------------------------------------------------------------------------
# parquet 深度三表/四表（income/cashflow/balance/fina_indicator）
# ----------------------------------------------------------------------------

_PARQUET_FAMILIES = ("income", "cashflow", "balance", "fina_indicator")
_PARQUET_INDEX_CACHE = None
_CODE_RE = re.compile(r"(\d{6})[._](SH|SZ|BJ)")


def _classify_parquet(base: str):
    """按文件名前缀归类 family；返回 (family, series) 或不参与(None)。
    series: dot=<code>.<EX>_<ENDDATE>（按报告期分文件）；underline=<code>_<EX>_<YEAR>（完整历史）。
    fina_indicator_q_ / _yoy_ 家族无 ann_date，不可作 as-of 依据，直接排除。
    """
    if base.startswith(("fina_indicator_q_", "fina_indicator_yoy_")):
        return None, None
    if base.startswith("income_"):
        fam = "income"
    elif base.startswith("cashflow_"):
        fam = "cashflow"
    elif base.startswith("balance"):
        fam = "balance"
    elif base.startswith("fina_indicator_"):
        fam = "fina_indicator"
    else:
        return None, None
    series = "dot" if re.search(r"\d{6}\.(SH|SZ|BJ)", base) else "underline"
    return fam, series


def get_parquet_index() -> pd.DataFrame:
    """扫描 parquet 目录，仅按文件名解析索引（不逐文件读 schema，避免上万次 IO）。

    返回 DataFrame[family, ts_code, series, path]；带模块级缓存。
    深度财务读取时据此定位候选股文件，避免对 19 万+ 文件反复 glob。
    """
    global _PARQUET_INDEX_CACHE
    if _PARQUET_INDEX_CACHE is not None:
        return _PARQUET_INDEX_CACHE
    rows = []
    if os.path.isdir(PARQUET_DIR):
        for path in glob.glob(os.path.join(PARQUET_DIR, "*.parquet")):
            base = os.path.basename(path)[:-len(".parquet")]
            fam, series = _classify_parquet(base)
            if fam is None:
                continue
            m = _CODE_RE.search(base)
            if not m:
                continue
            rows.append((fam, f"{m.group(1)}.{m.group(2)}", series, path))
    _PARQUET_INDEX_CACHE = pd.DataFrame(
        rows, columns=["family", "ts_code", "series", "path"])
    return _PARQUET_INDEX_CACHE


def load_parquet_statements(ts_codes, scan_date) -> dict:
    """按候选股读取深度财务 parquet，做「文件级 has-ann_date 判定 + as-of 过滤 + 报告期去重」。

    返回 dict[ts_code] -> dict[family] -> DataFrame（按 end_date 升序，含各绝对额列）。
    关键约束：只有含有效 ann_date 的文件才可作 as-of 依据；某 family 无此类文件则不返回
    （= UNKNOWN，由 quality 层降权），绝不臆造 as-of 依据。
    同一 (ts_code, family) 若同时有完整历史(underline)与报告期(dot)文件，仅保留有 ann_date 者。
    """
    idx = get_parquet_index()
    sd = int(norm_date(scan_date))
    codes = list(dict.fromkeys(ts_codes))
    out = {c: {} for c in codes}
    if idx.empty:
        return out
    for code in codes:
        sub = idx[idx["ts_code"] == code]
        if sub.empty:
            continue
        for fam in _PARQUET_FAMILIES:
            paths = sub.loc[sub["family"] == fam, "path"].tolist()
            if not paths:
                continue
            good = []
            for p in paths:
                try:
                    d = pd.read_parquet(p)
                except Exception:
                    continue
                if "ts_code" not in d.columns:
                    continue
                d = d[d["ts_code"].astype(str) == code].reset_index(drop=True)
                if d.empty or "ann_date" not in d.columns:
                    continue
                ann = _to_date_num(d["ann_date"].values)
                if not ann.notna().any():
                    continue
                d["ann_date_num"] = ann
                good.append(d)
            if not good:
                continue  # 该 family 无可信 as-of 依据 → UNKNOWN
            df = pd.concat(good, ignore_index=True)
            df["end_date_num"] = (_to_date_num(df["end_date"].values)
                                  if "end_date" in df.columns else np.nan)
            df = df[df["ann_date_num"].notna() & (df["ann_date_num"] <= sd)]
            if df.empty:
                continue
            df = (df.sort_values(["end_date_num", "ann_date_num"])
                    .drop_duplicates(subset=["end_date_num"], keep="last")
                    .sort_values("end_date_num").reset_index(drop=True))
            out[code][fam] = df
    return out


# ----------------------------------------------------------------------------
# 数据源审计（--audit 输出；如实实测 + 列出 UNKNOWN / 代理项）
# ----------------------------------------------------------------------------

def _probe_table(db_path: str, table: str) -> dict:
    """实测单表是否存在、行数、列数、各日期列范围；不臆造。"""
    cols = _table_cols(db_path, table)
    if not cols:
        return {"exists": False}
    con = sqlite3.connect(db_path, timeout=30.0)
    try:
        n = int(pd.read_sql_query(
            f"SELECT COUNT(*) AS n FROM {table}", con)["n"].iloc[0])
        info = {"exists": True, "rows": n, "n_cols": len(cols), "cols": cols}
        for dc in ("trade_date", "ann_date", "end_date", "eval_date"):
            if dc in cols:
                q = pd.read_sql_query(
                    f'SELECT MIN("{dc}") AS a, MAX("{dc}") AS b FROM {table}', con)
                info[dc + "_min"] = q["a"].iloc[0]
                info[dc + "_max"] = q["b"].iloc[0]
        return info
    finally:
        con.close()


def audit_data_sources(scan_date=None) -> dict:
    """实测各 DB 表存在/行数/日期范围 + parquet 家族文件数；列出全部 UNKNOWN 与代理项。

    供 report.py 渲染「数据源完整性」清单，满足用户「首先检查本地数据库、缓存和字段完整性」
    与「缺失字段标记 UNKNOWN 并降低置信度」的要求。
    """
    all_dates = get_trade_dates("19900101", "20991231")
    sd = norm_date(scan_date) if scan_date else (all_dates[-1] if all_dates else UNKNOWN)

    tables = {}
    for t in ("daily_cache", "daily_basic_cache", "fina_indicator_cache",
              "index_daily_cache", "stk_factor_pro", "adj_factor_cache",
              "factor_snapshot_cache", "index_member_cache", "cache_meta"):
        tables[f"stock_data.db/{t}"] = _probe_table(CACHE_DB_PATH, t)
    for t in ("leaderboard_v2", "leaderboard", "data_quality"):
        tables[f"sli.db/{t}"] = _probe_table(SLI_DB, t)
    for t in ("sw_industry", "sw_member", "etf_holdings"):
        tables[f"industry_holdings.db/{t}"] = _probe_table(INDUSTRY_DB, t)

    parquet_files = {}
    if os.path.isdir(PARQUET_DIR):
        for pat in ("income_*", "cashflow_*", "balance*", "fina_indicator_*",
                    "fina_indicator_q_*", "fina_indicator_yoy_*",
                    "forecast_vip_*", "express_vip_*", "actual_vip_*",
                    "limit_list_d_*", "sw_daily_*", "suspend_d_*"):
            parquet_files[pat] = len(glob.glob(os.path.join(PARQUET_DIR, pat + ".parquet")))

    idx = get_parquet_index()
    family_counts = (idx.groupby("family").size().to_dict()
                     if not idx.empty else {})

    unknowns = [
        "EV/EBITDA 历史分位：无企业价值/EBITDA 字段，ev_ebitda_pct 恒 UNKNOWN（按可用权重归一化）",
        "有息负债：无字段，interest_debt 子项 UNKNOWN",
        "重大客户流失：无字段，相关否决项无证据不触发",
        "重大监管及经营风险：无字段，相关否决项无证据不触发",
        "逐日盈利一致预期：无字段，forecast_downgrade / drop_vs_downgrade 仅用业绩预告快照代理",
        "suspend_d：本地无文件，停牌状态 UNKNOWN（以近20日交易天数近似，仅提示）",
        "sw_daily：本地无文件，行业收益改由成分股 pct_chg 中位数自建",
        "express_vip / actual_vip：覆盖极低（快报约62行），判 UNKNOWN",
        "fina_indicator_q_ / _yoy_ 家族：无 ann_date，不可作 as-of 依据，判 UNKNOWN 不读取",
    ]
    proxies = [
        "涨跌停家数 = pct_chg 板块阈值代理（主板9.8/创业科创19.8/北交29.8/ST4.8），|pct_chg|>31 剔除异常",
        "市场收益 = daily_cache 全A等权 pct_chg/100 累乘（非指数）",
        "行业收益 = 成分股当日 pct_chg 中位数自建（申万二级优先、一级回退）",
        "复权收益 = pct_chg/100 逐日累乘（含除权调整，不额外用 adj_factor）",
        "业绩预告 = 按文件名快照日期选 <=scan_date 最新一份，仅覆盖 20260630 中报预告",
    ]
    return {
        "scan_date": sd,
        "paths": {
            "cache_db": CACHE_DB_PATH, "stock_basic": STOCK_BASIC_CSV,
            "parquet_dir": PARQUET_DIR, "sli_db": SLI_DB, "industry_db": INDUSTRY_DB,
        },
        "tables": tables,
        "parquet_files": parquet_files,
        "parquet_index_families": family_counts,
        "data_sources": DATA_SOURCES,
        "unknowns": unknowns,
        "proxies": proxies,
        "score_cap": SCORE_CAP,
        "pending_modules": PENDING_MODULES,
    }