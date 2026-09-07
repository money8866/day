# -*- coding: utf-8 -*-
"""
IGE 数据层
==========
纯读取 SLI 既有 parquet 缓存（cache-first，不请求 API）：
- 申万 L1/L2/L3 分类与成分宇宙
- 财务（income_ / fina_ind_ 快照，ann_date<=快照日 防未来函数）
- 日行情（daily_*，pct_chg 复利链还原真实累计收益）
- SLI_V2 龙头面板（龙头加权依据，可选）

约定：所有金额单位沿用 Tushare 原值；增长率为百分数(%)。
"""
from __future__ import annotations

import glob
import logging
import os
from typing import Optional

import numpy as np
import pandas as pd

from .config import FINANCIAL_PERIODS, Q_PAIRS, SLI_CACHE_DIR

logger = logging.getLogger("ige.data")

# 财务用期集合：主窗口4期 + 同比基期4期（共8期，均为已缓存文件）
FIN_NEED = sorted({p for pair in Q_PAIRS for p in pair})

_mem: dict[str, pd.DataFrame] = {}


def _read_cache(key: str) -> pd.DataFrame:
    if key in _mem:
        return _mem[key]
    path = os.path.join(SLI_CACHE_DIR, f"{key}.parquet")
    if not os.path.exists(path):
        raise FileNotFoundError(f"缺少 SLI 缓存文件: {path}")
    df = pd.read_parquet(path)
    _mem[key] = df
    return df


def _load_members(end_date: str) -> pd.DataFrame:
    """申万成分快照：优先 members_{end_date}，缺失回退最近 <=end_date 快照。

    申万成分调整为低频事件，行情/财务仍按 end_date 计算，仅在行业归属上
    使用最近成分快照（回退时告警注明实际成分日期）。
    """
    exact = os.path.join(SLI_CACHE_DIR, f"members_{end_date}.parquet")
    if os.path.exists(exact):
        return pd.read_parquet(exact)
    cands = []
    for p in glob.glob(os.path.join(SLI_CACHE_DIR, "members_*.parquet")):
        d = os.path.basename(p)[len("members_"):-len(".parquet")]
        if d.isdigit() and len(d) == 8 and d <= end_date:
            cands.append((d, p))
    if not cands:
        raise FileNotFoundError(f"缺少 members 缓存（<= {end_date}）: {exact}")
    cands.sort()
    d, p = cands[-1]
    logger.warning("无 members_%s 快照，申万成分回退 members_%s（行情按 %s 计算）",
                   end_date, d, end_date)
    return pd.read_parquet(p)


# ── 通用工具 ──────────────────────────────────────────

def _dstr(s) -> pd.Series:
    return pd.Series(s).fillna("").astype(str)


def _latest_revision(df: pd.DataFrame) -> pd.DataFrame:
    """同一 (ts_code,end_date) 保留 update_flag 最新、其次 ann_date 最新的版本。"""
    if df is None or df.empty:
        return df
    df = df.copy()
    df["_uf"] = pd.to_numeric(df.get("update_flag"), errors="coerce").fillna(0.0)
    df["_an"] = _dstr(df["ann_date"])
    df = df.sort_values(["ts_code", "end_date", "_uf", "_an"])
    return df.groupby(["ts_code", "end_date"], as_index=False).tail(1)


def _num(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


# ── 行业分类 ──────────────────────────────────────────

def load_classify() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """读取申万 L1/L2/L3（SW2021），统一列：index_code/industry_code/industry_name/parent_code。"""
    out = {}
    for lv in ("L1", "L2", "L3"):
        df = _read_cache(f"classify_SW2021_{lv}").copy()
        for c in ("index_code", "industry_code", "industry_name", "parent_code", "is_pub"):
            if c not in df.columns:
                df[c] = np.nan
        df = df[["index_code", "industry_code", "industry_name", "parent_code", "is_pub"]]
        df["industry_code"] = _dstr(df["industry_code"])
        df["parent_code"] = _dstr(df["parent_code"])
        df["industry_name"] = df["industry_name"].fillna("").astype(str)
        if "is_pub" in df.columns:
            df = df[df["is_pub"].astype(int) == 1]
        out[lv] = df.reset_index(drop=True)
    return out["L1"], out["L2"], out["L3"]


def build_universe(end_date: str, cut_date: Optional[str] = None) -> pd.DataFrame:
    """构建 申万三级行业宇宙（剔除 ST / 北交所 / 上市过短）。

    返回列：ts_code, name, list_date, market, is_st,
            l3_index_code, l3_code, l3_name,
            l2_code, l2_name, l1_code, l1_name
    """
    l1, l2, l3 = load_classify()
    basic = _read_cache("stock_basic")
    members = _load_members(end_date)

    # L3 → L2 → L1 父子链：必须用数字 industry_code 连接（index_code 为 .SI 指数码，不同级不对齐）
    def _ic(s):
        return pd.to_numeric(s, errors="coerce")

    l2m = l2.rename(columns={"industry_code": "l2_code", "industry_name": "l2_name",
                             "parent_code": "l2_parent"})
    l1m = l1.rename(columns={"industry_code": "l1_code", "industry_name": "l1_name"})
    cls = l3[["index_code", "industry_code", "industry_name", "parent_code"]].rename(
        columns={"industry_code": "l3_code", "industry_name": "l3_name"})
    cls = cls.merge(l2m[["l2_code", "l2_name", "l2_parent"]],
                    left_on="parent_code", right_on="l2_code", how="left")
    cls = cls.merge(l1m[["l1_code", "l1_name"]],
                    left_on="l2_parent", right_on="l1_code", how="left")
    cls = cls.drop(columns=["parent_code", "l2_parent"])
    for c in ("l3_code", "l2_code", "l1_code"):
        cls[c] = cls[c].astype("Int64").astype(str)   # 110101 → "110101"

    m = members.rename(columns={"con_code": "ts_code"}).copy()
    m["in_date"] = _dstr(m["in_date"])
    m["out_date"] = _dstr(m["out_date"])
    m = m[(m["in_date"] <= end_date) &
          ((m["out_date"] == "") | (m["out_date"] > end_date))]

    uni = m.merge(cls[["index_code", "l3_code", "l3_name", "l2_code", "l2_name",
                       "l1_code", "l1_name"]],
                  on="index_code", how="left")
    uni = uni.drop(columns=["index_code"]).dropna(subset=["l3_code", "l2_code", "l1_code"])

    b = basic[["ts_code", "name", "market", "list_date"]].copy()
    b["list_date"] = _dstr(b["list_date"])
    uni = uni.merge(b, on="ts_code", how="left")

    # 清洗：剔除北交所、ST、上市过短
    uni = uni[~uni["ts_code"].str.endswith(".BJ")]
    uni["is_st"] = uni["name"].fillna("").str.contains("ST", na=False)
    uni = uni[~uni["is_st"]]
    if cut_date:
        uni = uni[(uni["list_date"] == "") | (uni["list_date"] <= cut_date)]

    uni = uni.drop_duplicates(subset=["ts_code"]).reset_index(drop=True)
    return uni


# ── 交易日 ────────────────────────────────────────────

def trade_dates(end_date: str) -> list[str]:
    """交易日历中 <= end_date 的全部交易日（升序）。

    精确覆盖文件缺失时（补跑晚于既有日历文件的快照日），合并全部 trade_cal 缓存：
    保证历史起点完整，避免误用只有近端窗口的日历导致 120 日回溯窗口变空。
    """
    exact = os.path.join(SLI_CACHE_DIR, f"trade_cal_20240101_{end_date}.parquet")
    if os.path.exists(exact):
        paths = [exact]
    else:
        paths = sorted(glob.glob(os.path.join(SLI_CACHE_DIR, "trade_cal_*.parquet")))
        if not paths:
            raise FileNotFoundError("缺少 trade_cal 缓存")
        logger.warning("无 trade_cal_20240101_%s 精确日历，改用 %d 个 trade_cal 并集",
                       end_date, len(paths))
    parts = []
    for path in paths:
        df = pd.read_parquet(path)
        if "cal_date" not in df.columns:
            continue
        df["cal_date"] = _dstr(df["cal_date"])
        df["is_open"] = pd.to_numeric(df.get("is_open"), errors="coerce")
        parts.append(df[["cal_date", "is_open"]])
    if not parts:
        raise FileNotFoundError("trade_cal 缓存无有效日期列")
    cal = pd.concat(parts, ignore_index=True).drop_duplicates(subset=["cal_date"])
    dates = sorted(cal.loc[cal["is_open"] == 1, "cal_date"].unique())
    return [d for d in dates if d <= end_date]


# ── 财务（每股 · 逐报告期，ann_date 防未来函数）──────────

def load_finance(end_date: str) -> pd.DataFrame:
    """每股最近 4 个报告期（q0..q3）财务字段（ann_date<=end_date 且取最新修订）。

    返回每股一行，字段：
      rev_i / rev_ly_i     营收金额、同比基期金额（i=0..3）
      np_i  / np_ly_i      归母净利金额、同比基期金额
      or_yoy_i / np_yoy_i  fina 指标（营业总收入/归母净利同比增速 %）
      gm_i                 fina 指标 毛利率(%)
    金额缺失不影响增速（有 or_yoy/np_yoy 时以 fina 为准，见 factors）。
    """
    fina_parts, inc_parts = [], []
    for p in FIN_NEED:
        fdf = _read_cache(f"fina_ind_{p}")
        if len(fdf):
            fina_parts.append(fdf)
        idf = _read_cache(f"income_{p}")
        if len(idf):
            inc_parts.append(idf)
    fina = pd.concat(fina_parts, ignore_index=True) if fina_parts else pd.DataFrame()
    inc = pd.concat(inc_parts, ignore_index=True) if inc_parts else pd.DataFrame()

    def _snap(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame()
        df = _latest_revision(df)
        keep = [c for c in cols if c in df.columns]
        if not keep:
            return pd.DataFrame()
        df = df[keep]
        df = df[_dstr(df["ann_date"]) <= end_date]
        return df.copy()

    fina = _snap(fina, ["ts_code", "end_date", "ann_date", "update_flag",
                        "or_yoy", "netprofit_yoy", "dt_netprofit_yoy",
                        "grossprofit_margin", "netprofit_margin"])
    inc = _snap(inc, ["ts_code", "end_date", "ann_date", "update_flag",
                      "revenue", "n_income_attr_p"])

    # fina / inc 都以 (ts_code,end_date) 为唯一键 → 宽表
    fw = fina.pivot_table(index="ts_code", columns="end_date", values=[
        c for c in ("or_yoy", "grossprofit_margin", "netprofit_margin") if c in fina.columns],
        aggfunc="first")
    fw.columns = [f"{a}_{b}" for a, b in fw.columns]          # or_yoy_20260630 ...
    # np_yoy：netprofit_yoy 优先，缺失用 dt_netprofit_yoy
    if "netprofit_yoy" in fina.columns:
        fw2 = fina.pivot_table(index="ts_code", columns="end_date",
                               values="netprofit_yoy", aggfunc="first")
        fw2.columns = [f"netprofit_yoy_{b}" for b in fw2.columns]
        fw = fw.join(fw2, how="left")
    if "dt_netprofit_yoy" in fina.columns:
        fw3 = fina.pivot_table(index="ts_code", columns="end_date",
                               values="dt_netprofit_yoy", aggfunc="first")
        fw3.columns = [f"dt_{b}" for b in fw3.columns]
        fw = fw.join(fw3, how="left")

    iw = inc.pivot_table(index="ts_code", columns="end_date",
                         values=["revenue", "n_income_attr_p"], aggfunc="first")
    iw.columns = [f"{a}_{b}" for a, b in iw.columns]          # revenue_20260630 ...

    out = fw.join(iw, how="outer").reset_index()
    out["ts_code"] = out["ts_code"].astype(str)

    rows: list[dict] = []
    for _, r in out.iterrows():
        d: dict = {"ts_code": r["ts_code"]}
        for i, (p, p_ly) in enumerate(Q_PAIRS):
            d[f"rev_{i}"] = _num(r.get(f"revenue_{p}"))
            d[f"rev_ly_{i}"] = _num(r.get(f"revenue_{p_ly}"))
            d[f"np_{i}"] = _num(r.get(f"n_income_attr_p_{p}"))
            d[f"np_ly_{i}"] = _num(r.get(f"n_income_attr_p_{p_ly}"))
            d[f"or_yoy_{i}"] = _num(r.get(f"or_yoy_{p}"))
            d[f"np_yoy_{i}"] = _num(r.get(f"netprofit_yoy_{p}"))
            if pd.isna(d[f"np_yoy_{i}"]):
                d[f"np_yoy_{i}"] = _num(r.get(f"dt_{p}"))
            d[f"gm_{i}"] = _num(r.get(f"grossprofit_margin_{p}"))
        rows.append(d)
    fin = pd.DataFrame(rows)
    return fin


# ── 行情（每日快照 → 累计收益指数）──────────────────────

def load_prices(end_date: str, start_date: str) -> tuple[list[str], pd.DataFrame]:
    """读取 [start_date, end_date] 全市场日线，构建每股累计收益指数 pivot。

    用 pct_chg 复利链还原累计收益（pct_chg 基于除权后 pre_close，
    可避免分红/拆股对固定窗口收益比的失真）。返回 (交易日升序, pivot 行=日期 列=ts_code)。
    """
    pattern = os.path.join(SLI_CACHE_DIR, "daily_*.parquet")
    files = []
    for p in glob.glob(pattern):
        base = os.path.basename(p)
        d = base.replace("daily_", "").replace(".parquet", "")
        if d.isdigit() and len(d) == 8 and start_date <= d <= end_date:
            files.append((d, p))
    files.sort()
    if not files:
        raise FileNotFoundError(f"无 {start_date}~{end_date} 日线缓存")
    parts = []
    for _, p in files:
        df = pd.read_parquet(p, columns=["ts_code", "trade_date", "pct_chg"])
        parts.append(df)
    daily = pd.concat(parts, ignore_index=True)
    daily["pct_chg"] = _num(daily["pct_chg"]).fillna(0.0)
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily["trade_date"] = daily["trade_date"].astype(str)

    daily = daily.sort_values(["ts_code", "trade_date"])
    daily["px"] = (1.0 + daily["pct_chg"] / 100.0)
    daily["px"] = daily.groupby("ts_code")["px"].cumprod()
    dates = sorted(daily["trade_date"].unique())
    piv = daily.pivot_table(index="trade_date", columns="ts_code", values="px")
    piv = piv.reindex(dates)
    return dates, piv


# ── SLI_V2 龙头面板（可选，用于 F2 龙头识别）─────────────

def load_sli_panel(end_date: str) -> Optional[pd.DataFrame]:
    """读取最新 SLI_V2 快照中的龙头身份/得分。失败返回 None（降级为营收龙头）。"""
    try:
        import sys
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # d:\mystock\solo
        if root not in sys.path:
            sys.path.insert(0, root)
        from sli.reader import get_panel
        panel = get_panel(asof=end_date)
        cols = ["ts_code", "sli_v2", "leader_type_v2", "l3_code"]
        cols = [c for c in cols if c in panel.columns]
        out = panel[cols].copy()
        out["ts_code"] = out["ts_code"].astype(str)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("SLI_V2 面板不可用（%s），龙头将按营收规模选取", exc)
        return None
