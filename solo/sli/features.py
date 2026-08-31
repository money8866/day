# -*- coding: utf-8 -*-
"""
SLI 特征层
- 构建申万三级行业宇宙
- 行情特征（收益率/均线/斜率/成交额，支持多个时点评测）
- 时点财务快照（ann_date 防未来函数）
- 行业纯度（fina_mainbz 产品级关键词映射）
- 行业内横截面 percentile 标准化
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import numpy as np
import pandas as pd

from .config import INCLUDE_BJ, LIFECYCLE_OFFSETS

logger = logging.getLogger("sli.features")


# ── 行业纯度：聚合行黑名单 ─────────────────────────────

AGG_ROWS = {
    "产品", "其他", "其他业务", "其他业务收入", "其他主营", "合计", "合计特别调整",
    "主营", "主营业务", "主营收入", "主营业务收入", "营业成本", "主营成本", "主营业务成本",
    "其他业务成本", "营业收入", "分部间抵销", "抵销", "非主营",
}

# ── 行业关键词映射（产品名 → 该三级行业核心业务） ────────
# 未收录的行业自动用「行业名去后缀」作为关键词；匹配不到再退化为头部产品集中度。
CURATED_KEYWORDS = {
    "钛白粉": ["钛白粉", "二氧化钛", "钛白"],
    "白酒": ["白酒", "茅台酒", "系列酒", "酒"],
    "锂电池": ["电池", "电芯", "锂"],
    "电池化学品": ["电解液", "隔膜", "负极", "正极", "锂盐", "碳酸锂", "前驱体"],
    "光伏电池组件": ["光伏", "组件", "电池片", "硅片"],
    "光伏设备": ["光伏", "硅片设备", "电池片设备", "组件设备", "切片", "串焊", "镀膜"],
    "光伏辅材": ["光伏", "玻璃", "胶膜", "银浆", "支架"],
    "机器人": ["机器人", "机械手", "关节", "伺服", "减速器"],
    "医疗器械": ["医疗", "器械", "监护", "内镜", "影像", "体外诊断", "IVD", "超声", "治疗"],
    "医疗设备": ["医疗", "设备", "监护", "内镜", "影像", "超声", "治疗"],
    "医疗耗材": ["耗材", "导管", "支架", "注射", "敷料"],
    "印制电路板": ["印制电路板", "PCB", "覆铜板", "线路板"],
    "服务器": ["服务器", "算力", "机柜", "整机"],
    "计算机设备": ["服务器", "存储", "整机", "终端"],
    "工程机械整机": ["工程机械", "挖掘机", "装载机", "起重机", "泵车", "推土机", "压路机"],
    "工程机械器件": ["工程机械", "液压", "泵", "马达", "阀"],
    "航空发动机": ["航空发动机", "发动机", "叶片", "航发"],
    "航空装备": ["航空", "发动机", "起落架", "机体", "航电"],
    "汽车零部件": ["零部件", "饰件", "底盘", "电子", "连接器", "铸件", "内饰"],
    "半导体设备": ["半导体", "刻蚀", "薄膜", "光刻", "清洗", "离子注入", "量测"],
    "集成电路制造": ["晶圆", "制造", "代工"],
    "半导体材料": ["半导体", "硅片", "光刻胶", "靶材", "电子特气", "CMP"],
    "数字芯片设计": ["芯片", "集成电路", "SoC", "MCU"],
    "存储": ["存储", "闪存", "内存", "DRAM"],
    "光伏发电": ["光伏", "电站", "发电"],
    "风电设备": ["风电", "风机", "叶片", "塔筒", "轴承"],
    "农化制品": ["农药", "化肥", "复合肥", "除草剂", "杀虫剂"],
    "化学制剂": ["制剂", "药品", "注射液", "片剂"],
    "原料药": ["原料药", "API", "中间体"],
    "工业金属": ["铜", "铝", "锌", "铅"],
    "小金属": ["锆", "钛", "钼", "钨", "钒", "锑", "镁"],
    "特钢": ["特钢", "不锈钢", "合金"],
    "煤炭开采": ["煤炭", "原煤", "洗煤"],
    "能源金属": ["锂", "钴", "镍"],
    "消费电子零部件及组装": ["消费电子", "结构件", "连接器", "声学", "光学", "组装"],
    "面板": ["面板", "LCD", "OLED", "显示"],
    "通信设备": ["通信", "基站", "光模块", "光通信", "射频"],
    "软件开发": ["软件", "系统", "平台", "SaaS", "云"],
    "IT服务": ["IT", "服务", "集成", "运维"],
    "白酒Ⅲ": ["白酒", "茅台酒", "系列酒", "酒"],
}


def _strip_level(name: str) -> str:
    """去掉行业名中的 Ⅲ/Ⅱ/Ⅰ/Ⅳ 后缀。"""
    return re.sub(r"[ⅠⅡⅢⅣⅤ]+$", "", name).strip()


def build_keywords(l3_name: str) -> list[str]:
    """为三级行业生成产品匹配关键词。"""
    if l3_name in CURATED_KEYWORDS:
        return CURATED_KEYWORDS[l3_name]
    base = _strip_level(l3_name)
    if len(base) >= 2:
        return [base]
    return []


# ── 行业宇宙 ──────────────────────────────────────────

def build_universe(classify_l3: pd.DataFrame, members: pd.DataFrame,
                   basic: pd.DataFrame, end_date: str,
                   include_bj: bool = INCLUDE_BJ) -> pd.DataFrame:
    """构建 申万三级行业 → 当前成分股 的宇宙表。

    返回列：l3_code, l3_name, l2_name, l1_name, ts_code, name,
            list_date, market, is_st
    """
    if classify_l3.empty or members.empty or basic.empty:
        return pd.DataFrame()

    cls = classify_l3.rename(columns={
        "index_code": "l3_index_code", "industry_code": "l3_code",
        "industry_name": "l3_name",
    })
    # 公开行业才纳入
    cls = cls[cls.get("is_pub", pd.Series(1, index=cls.index)).astype(int) == 1] \
        if "is_pub" in cls.columns else cls

    m = members.rename(columns={"con_code": "ts_code", "index_code": "l3_index_code"})
    m = m.copy()
    m["in_date"] = m["in_date"].fillna("").astype(str)
    m["out_date"] = m["out_date"].fillna("").astype(str)
    # 截至 end_date 仍为成分（out_date 为空 = 当前成分）
    m = m[(m["in_date"] <= end_date) &
          ((m["out_date"] == "") | (m["out_date"] > end_date))]

    l3_cols = ["l3_index_code", "l3_code", "l3_name"]
    for extra in ("l2_name", "l1_name"):
        if extra in cls.columns:
            l3_cols.append(extra)
    uni = m.merge(cls[l3_cols], on="l3_index_code", how="left")
    if "l2_name" not in uni.columns:
        uni["l2_name"] = ""
    if "l1_name" not in uni.columns:
        uni["l1_name"] = ""
    uni = uni.dropna(subset=["l3_code"])
    uni = uni.drop_duplicates(subset=["l3_index_code", "ts_code"])

    b = basic.rename(columns={"industry": "sw_industry"})
    uni = uni.merge(b[["ts_code", "name", "market", "list_date"]], on="ts_code", how="left")

    if not include_bj:
        uni = uni[~uni["ts_code"].str.endswith(".BJ")]
    uni["is_st"] = uni["name"].fillna("").str.contains("ST", na=False)
    return uni.reset_index(drop=True)


# ── 行情特征 ──────────────────────────────────────────

class PriceFeatures:
    """全市场行情特征（宽表滚动计算），支持任意交易日切片评测。"""

    def __init__(self, daily: pd.DataFrame) -> None:
        if daily.empty:
            raise ValueError("daily 数据为空")
        self.dates = sorted(daily["trade_date"].astype(str).unique())
        self._daily = daily
        self._pivots: dict[str, pd.DataFrame] = {}
        self._rolls: dict[str, pd.DataFrame] = {}

    def _pivot(self, col: str) -> pd.DataFrame:
        if col not in self._pivots:
            d = self._daily[["trade_date", "ts_code", col]].copy()
            self._pivots[col] = d.pivot(index="trade_date", columns="ts_code", values=col)
            self._pivots[col] = self._pivots[col].sort_index()
        return self._pivots[col]

    def _roll(self, key: str) -> pd.DataFrame:
        if key not in self._rolls:
            raise KeyError(key)
        return self._rolls[key]

    def prepare(self) -> None:
        """预计算所有滚动统计（只算一次，多时点复用）。"""
        close = self._pivot("close")
        high = self._pivot("high")
        amount = self._pivot("amount")
        vol = self._pivot("vol")

        self._rolls["ma20"] = close.rolling(20, min_periods=20).mean()
        self._rolls["ma60"] = close.rolling(60, min_periods=60).mean()
        self._rolls["ma120"] = close.rolling(120, min_periods=120).mean()
        self._rolls["close_shift20"] = close.shift(20)
        self._rolls["close_shift60"] = close.shift(60)
        self._rolls["close_shift120"] = close.shift(120)
        self._rolls["ma20_shift5"] = self._rolls["ma20"].shift(5)
        self._rolls["ma60_shift20"] = self._rolls["ma60"].shift(20)
        self._rolls["amount20"] = amount.rolling(20, min_periods=10).mean()
        self._rolls["amount60"] = amount.rolling(60, min_periods=30).mean()
        self._rolls["high20"] = high.rolling(20, min_periods=20).max()
        self._rolls["vol20"] = vol.rolling(20, min_periods=10).mean()

    def nearest_date(self, date: str) -> Optional[str]:
        """返回 <= date 的最近交易日。"""
        for d in reversed(self.dates):
            if d <= date:
                return d
        return None

    def eval_at(self, date: str) -> pd.DataFrame:
        """在指定交易日计算全部行情特征（行=ts_code）。"""
        d = self.nearest_date(date)
        if d is None:
            return pd.DataFrame()
        close = self._pivot("close")
        c = close.loc[d]
        out = pd.DataFrame(index=c.index)
        out["close"] = c
        for span in (20, 60, 120):
            out[f"ret{span}"] = (c / self._rolls[f"close_shift{span}"].loc[d] - 1.0) * 100.0
        for w in (20, 60, 120):
            out[f"ma{w}"] = self._rolls[f"ma{w}"].loc[d]
        out["ma20_slope"] = (self._rolls["ma20"].loc[d] / self._rolls["ma20_shift5"].loc[d] - 1.0) * 100.0
        out["ma60_slope"] = (self._rolls["ma60"].loc[d] / self._rolls["ma60_shift20"].loc[d] - 1.0) * 100.0
        out["amount20"] = self._rolls["amount20"].loc[d]
        out["amount60"] = self._rolls["amount60"].loc[d]
        out["high20"] = self._rolls["high20"].loc[d]
        out["vol20"] = self._rolls["vol20"].loc[d]
        out["vol_today"] = self._pivot("vol").loc[d]
        out["eval_date"] = d
        return out


# ── 时点财务快照（防未来函数） ─────────────────────────

def _dstr(s) -> pd.Series:
    """字符串安全转换：NaN → ''（兼容 pandas 3.0 str 数据类型）。"""
    return pd.Series(s).fillna("").astype(str)


def _latest_revision(df: pd.DataFrame) -> pd.DataFrame:
    """同一 (ts_code, end_date) 只保留最新披露版本（update_flag 最大、其次 ann_date 最大）。"""
    if df.empty:
        return df
    df = df.copy()
    df["_uf"] = pd.to_numeric(df.get("update_flag"), errors="coerce").fillna(0.0)
    df["_an"] = _dstr(df["ann_date"])
    df = df.sort_values(["ts_code", "end_date", "_uf", "_an"])
    return df.groupby(["ts_code", "end_date"], as_index=False).tail(1)


def financial_snapshot(ind: pd.DataFrame, income: pd.DataFrame, balance: pd.DataFrame,
                       date: str) -> pd.DataFrame:
    """截至 date（ann_date <= date）每股最新报告期快照。

    返回：ts_code, end_date, ann_date + 全部指标/营收/总资产
    """
    def _snap(df: pd.DataFrame, suffix: str = "") -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        df = _latest_revision(df)
        df = df[_dstr(df["ann_date"]) <= date]
        if df.empty:
            return pd.DataFrame()
        df = df.copy()
        df["_end"] = _dstr(df["end_date"])
        df = df.sort_values(["ts_code", "_end", "_uf", "_an"])
        return df.groupby("ts_code", as_index=False).tail(1)

    a = _snap(ind, "")
    if a.empty:
        return pd.DataFrame()
    cols = ["ts_code", "end_date", "ann_date"]
    keep = [c for c in cols + [
        "roe", "roic", "grossprofit_margin", "netprofit_margin",
        "or_yoy", "netprofit_yoy", "ocf_to_profit", "rd_exp",
        "q_profit_yoy", "dt_netprofit_yoy", "roe_dt",
    ] if c in a.columns]
    a = a[keep]
    b = _snap(balance, "_b")
    if not b.empty and "total_assets" in b.columns:
        a = a.merge(b[["ts_code", "total_assets"]], on="ts_code", how="left")
    inc = _snap(income, "_i")
    if not inc.empty:
        i_cols = ["ts_code"] + [c for c in ["revenue", "operate_cost", "total_cogs", "n_income_attr_p"]
                                if c in inc.columns]
        a = a.merge(inc[i_cols], on="ts_code", how="left")
    return a


def prev_period_snapshot(ind: pd.DataFrame, date: str) -> pd.DataFrame:
    """每股截至 date 的上一报告期 ROE/毛利率（用于 ROE变化 / 毛利变化差分）。

    返回：ts_code, roe_prev, gm_prev
    防未来函数：按 ann_date <= date 过滤。只有一期数据时用自身作上一期（差分≈0）。
    """
    if ind is None or ind.empty:
        return pd.DataFrame()
    df = _latest_revision(ind)
    df = df[_dstr(df["ann_date"]) <= date].copy()
    if df.empty:
        return pd.DataFrame()
    df["_end"] = _dstr(df["end_date"])
    df = df.sort_values(["ts_code", "_end"])
    rows = []
    for code, g in df.groupby("ts_code"):
        r = g.iloc[-1]
        prev = g.iloc[-2] if len(g) >= 2 else r
        rows.append({"ts_code": code,
                     "roe_prev": pd.to_numeric(prev.get("roe"), errors="coerce"),
                     "gm_prev": pd.to_numeric(prev.get("grossprofit_margin"), errors="coerce")})
    return pd.DataFrame(rows)


def annual_moat(uni: pd.DataFrame, ind: pd.DataFrame, date: str) -> pd.DataFrame:
    """计算年度护城河 / 盈利持续领先年数。

    用最近 3 个年度（*1231 报告期）判定：
      sustained_moat_years  : 连续 N 年 毛利率>行业中位数 且 ROE>行业中位数 且 净利率>行业中位数
      sustained_profit_years: 连续 N 年 ROE>行业中位数
    返回：ts_code, sustained_moat_years, sustained_profit_years
    """
    if ind is None or ind.empty or uni is None or uni.empty:
        return pd.DataFrame()
    df = _latest_revision(ind)
    df = df[_dstr(df["ann_date"]) <= date].copy()
    if df.empty:
        return pd.DataFrame()
    df["_end"] = _dstr(df["end_date"])
    df = df[df["_end"].str.endswith("1231")]
    if df.empty:
        return pd.DataFrame()
    for c in ("roe", "grossprofit_margin", "netprofit_margin"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    uni_m = uni[["ts_code", "l3_code"]].drop_duplicates()
    df = df.merge(uni_m, on="ts_code", how="inner")
    df = df.dropna(subset=["roe"])
    if df.empty:
        return pd.DataFrame()
    for c in ("roe", "grossprofit_margin", "netprofit_margin"):
        med = df.groupby(["l3_code", "_end"])[c].transform("median")
        df[f"{c}_ok"] = df[c] > med
    df["moat_ok"] = df["roe_ok"] & df["grossprofit_margin_ok"] & df["netprofit_margin_ok"]
    df["profit_ok"] = df["roe_ok"]
    df = df.sort_values(["ts_code", "_end"])

    def _count_consecutive(flag_col: str, g: pd.DataFrame) -> int:
        n = 0
        for _, r in g.iloc[::-1].iterrows():
            if r[flag_col]:
                n += 1
            else:
                break
        return n

    rows = []
    for code, g in df.groupby("ts_code"):
        rows.append({"ts_code": code,
                     "sustained_moat_years": _count_consecutive("moat_ok", g),
                     "sustained_profit_years": _count_consecutive("profit_ok", g)})
    return pd.DataFrame(rows)


def growth_acceleration(ind: pd.DataFrame, date: str) -> pd.DataFrame:
    """每股截至 date 的最近 3 期报告序列（用于增速加速度）。

    返回：ts_code, end1..end3, g1..g3（g=单季利润增速，缺失时退化为累计扣非/营收增速）
    """
    if ind is None or ind.empty:
        return pd.DataFrame()
    df = _latest_revision(ind)
    df = df[_dstr(df["ann_date"]) <= date].copy()
    if df.empty:
        return pd.DataFrame()
    df["_end"] = _dstr(df["end_date"])

    def _g(row: pd.Series) -> float:
        for c in ("q_profit_yoy", "netprofit_yoy", "or_yoy"):
            v = pd.to_numeric(row.get(c), errors="coerce")
            if pd.notna(v):
                return float(v)
        return float("nan")

    df["g"] = df.apply(_g, axis=1)
    df = df.dropna(subset=["g"])
    df = df.sort_values(["ts_code", "_end"])
    rows = []
    for code, g in df.groupby("ts_code"):
        ends = g["_end"].tolist()
        vals = g["g"].tolist()
        if len(ends) >= 3:
            rows.append({"ts_code": code, "e1": ends[-3], "e2": ends[-2], "e3": ends[-1],
                         "g1": vals[-3], "g2": vals[-2], "g3": vals[-1]})
        elif len(ends) == 2:
            rows.append({"ts_code": code, "e1": ends[-2], "e2": ends[-1], "e3": "",
                         "g1": vals[-2], "g2": vals[-1], "g3": float("nan")})
    return pd.DataFrame(rows)


# ── 行业纯度 ──────────────────────────────────────────

def compute_purity(mainbz: pd.DataFrame, uni: pd.DataFrame, snapshot: pd.DataFrame) -> pd.DataFrame:
    """计算每只股票的行业纯度（基于产品级主营构成 + 行业关键词）。

    返回：ts_code, purity(%), purity_confidence(HIGH/MEDIUM/LOW), purity_top1(%)
    """
    l3_map = uni[["ts_code", "l3_name"]].drop_duplicates()
    if mainbz.empty:
        out = snapshot[["ts_code"]].copy()
        out["purity"] = 50.0
        out["purity_confidence"] = "LOW"
        out["purity_top1"] = 50.0
        return out

    mb = mainbz.copy()
    mb["bz_item"] = mb["bz_item"].fillna("").astype(str)
    mb["bz_sales"] = pd.to_numeric(mb["bz_sales"], errors="coerce")
    mb = mb[mb["bz_sales"].notna() & (mb["bz_sales"] > 0)]

    # 每股票取最新报告期的主营构成
    mb["_end"] = _dstr(mb["end_date"])
    mb = mb.sort_values(["ts_code", "_end"]).groupby("ts_code").tail(12)

    snap = snapshot[["ts_code", "end_date"]].copy()
    snap["snap_end"] = _dstr(snap["end_date"])
    snap = snap.merge(mb, on="ts_code", how="left", suffixes=("", "_mb"))

    rev_map = snapshot[["ts_code"]].copy()
    rev_map["revenue"] = pd.to_numeric(snapshot.get("revenue"), errors="coerce")
    rev_map = rev_map.set_index("ts_code")["revenue"]

    out_rows = []
    for code, grp in snap.groupby("ts_code"):
        l3 = l3_map.loc[l3_map["ts_code"] == code, "l3_name"]
        l3_name = l3.iloc[0] if len(l3) else ""
        kw = build_keywords(str(l3_name))
        # 优先当前快照期，其次历史期（merge 后可能引入 NaN，需排除）
        prod = grp[grp["bz_item"].apply(
            lambda x: isinstance(x, str) and x not in AGG_ROWS and x != "")]
        if prod.empty:
            out_rows.append({"ts_code": code, "purity": 50.0,
                             "purity_confidence": "LOW", "purity_top1": 50.0})
            continue
        total = rev_map.get(code, float("nan"))
        if pd.isna(total) or total <= 0:
            total = prod["bz_sales"].sum()
        matched = 0.0
        if kw:
            for _, r in prod.iterrows():
                if any(k in r["bz_item"] for k in kw):
                    matched += r["bz_sales"]
        top1 = prod["bz_sales"].max()
        top1_ratio = top1 / total * 100.0 if total > 0 else 0.0
        if kw and matched > 0:
            purity = min(100.0, matched / total * 100.0) if total > 0 else 50.0
            conf = "HIGH"
        else:
            # 关键词未命中：用头部产品集中度作为纯度代理（封顶70，避免高估）
            purity = min(70.0, top1_ratio)
            conf = "MEDIUM"
        out_rows.append({"ts_code": code, "purity": round(purity, 2),
                         "purity_confidence": conf, "purity_top1": round(top1_ratio, 2)})
    return pd.DataFrame(out_rows)


# ── 行业内 percentile 标准化 ──────────────────────────

def pct_rank_industry(df: pd.DataFrame, l3_col: str, metric_col: str) -> pd.Series:
    """行业内 percentile rank → 0~100。值越大排名越高。"""
    if metric_col not in df.columns:
        return pd.Series(float("nan"), index=df.index)
    return df.groupby(l3_col)[metric_col].rank(pct=True, na_option="keep") * 100.0


def industry_median(df: pd.DataFrame, l3_col: str, metric_col: str) -> pd.Series:
    """行业内中位数（按 ts_code 对齐）。"""
    med = df.groupby(l3_col)[metric_col].transform("median")
    return med
