# -*- coding: utf-8 -*-
"""
IGE v1.2 因子层
===============
F1 Demand / F2 ProfitElasticity / F3 Acceleration / F4 Supply / F5 Market
三级(L3)→二级(L2)→一级(L1) 行业聚合 → winsorize(1%~99%) + percentile rank 0~100
行业生命周期分类 + 修正值；行业类型/状态/加速确认标签；分行业解释模板。
v1.1：igemix 不再二次 PercentileRank；F5 增补 盈利加速→超额收益 传导β。
v1.2（微调）：仅新增 IGE_MOM（成分趋势动量）/ IGE_PERSISTENCE（增长持续性）
     每层原始列（pg2/pg3、gm1~gm3）与判定函数；不改动五因子权重与基础评分。
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .config import (BETA_MIN_OBS, CONF_MULT, CORE_IGE_EFFECTIVE,
                     CORE_LIFECYCLE, CORE_MOM, CORE_PERS, CORE_SIA,
                     CYCLICAL_HIGH_ELASTICITY_IGE, CYCLICAL_HIGH_ELASTICITY_PERS,
                     HIGH_IGE, LEADER_SIA, LEADER_MAX, LEADER_MIN,
                     LIFECYCLE_ADJ, LOW_IGE_BOUND, MAX_ELASTIC_ABS,
                     MIN_ELASTIC_REV_YOY, MOM_BANDS, MOM_STRONG, ONLY_SIA_LOW,
                     PERS_BANDS, PERS_GOOD, Q_PAIRS, RANK_HIGH_IGE,
                     RANK_HIGH_MOM, RANK_HIGH_PERS, RANK_SIA_HIGH,
                     RANK_SIA_LOW, RANK_TIER, SAMPLE_FULL, SAMPLE_MIX,
                     STRUCTURAL_ELASTICITY_IGE, TOP_SIA, TRAP_IGE_MIX,
                     W_ACCEL, W_DA, W_DEMAND, W_DG, W_IPE, W_IPE_BROAD,
                     W_MA, W_MARKET, W_MKT_BETA, W_MOM_MARGIN, W_MOM_MKT,
                     W_MOM_PROF, W_MOM_REV, W_PA, W_PERS_GM, W_PERS_PROF,
                     W_PERS_REV, W_PERS_XR, W_PROFIT_EL, W_RA, W_SLOPE,
                     W_SUPPLY, W_XR120, W_XR20, W_XR60, WINSOR_HI, WINSOR_LO)

logger = logging.getLogger("ige.factors")


# ── 小工具 ────────────────────────────────────────────

def _num(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def winsorize(s: pd.Series, lo: float = WINSOR_LO, hi: float = WINSOR_HI) -> pd.Series:
    v = s.astype(float).copy()
    q = v.quantile([lo / 100.0, hi / 100.0])
    if pd.notna(q.iloc[0]):
        v = v.clip(q.iloc[0], q.iloc[1])
    return v


def pct_rank(s: pd.Series) -> pd.Series:
    """横截面 percentile rank 0~100（NaN 保留）。"""
    return s.rank(pct=True, method="average", na_option="keep") * 100.0


def pr_col(s: pd.Series) -> pd.Series:
    """winsorize → percentile rank（因子标准化的标准步骤）。"""
    return pct_rank(winsorize(s))


def band_of(vals: pd.Series, bands: list[tuple[float, str]],
            na: str = "NA") -> pd.Series:
    """按 (下限, 名称) 降序档位表映射成标签；NaN → na。"""
    v = _num(vals)
    out = pd.Series(bands[-1][1], index=v.index, dtype=object)
    for thr, name in bands[::-1]:            # 升序遍历，后写覆盖小档
        out = out.where(~(v >= thr), name)
    return out.where(v.notna(), na)


def _pos_frac(tab: pd.DataFrame, cols: list[str], thr: float) -> pd.Series:
    """一列或多列中 >thr 的有值占比 ×100（v1.2 持续性子分量的基础）。"""
    v = pd.concat([_num(tab[c]) for c in cols], axis=1)
    ok = v.notna().sum(axis=1)
    pos = (v > thr).sum(axis=1)
    return (pos * 100.0 / ok).where(ok > 0)


def _gm_continuity(r: pd.Series) -> float:
    """毛利率持续性：相邻报告期行业中位数毛利率环比未明显走弱(降幅<0.5pp)的比例×100。"""
    seq = [r.get(c, np.nan) for c in ("mgm", "gm1", "gm2", "gm3")]
    up = []
    for a, b in zip(seq[:-1], seq[1:]):
        if pd.notna(a) and pd.notna(b):
            up.append(1.0 if a >= b - 0.5 else 0.0)
    return 100.0 * float(np.mean(up)) if up else float("nan")


def weighted_blend(df: pd.DataFrame, cols: list[str], weights: list[float],
                   keep_na: bool = False) -> pd.Series:
    """对若干列按权重混合；某列 NaN 时在其余可用列间重新归一化权重（fail-soft）。"""
    out = pd.Series(np.nan, index=df.index, dtype=float)
    wsum_df = pd.DataFrame(0.0, index=df.index, columns=cols)
    for c, w in zip(cols, weights):
        col = _num(df[c])
        if keep_na:
            wsum_df[c] = w
        else:
            wsum_df[c] = np.where(col.notna(), w, 0.0)
        part = col * w
        out = out.add(part, fill_value=0.0)
    denom = wsum_df.sum(axis=1)
    res = out / denom.replace(0.0, np.nan)
    return res


# ── 每股基础指标（供 SIA / 行业聚合）────────────────────

def stock_metrics(fin: pd.DataFrame) -> pd.DataFrame:
    """为每股计算最近4报告期增速 (rg/pg) 与毛利率，供行业聚合使用。

    rg_i / pg_i：fina 指标优先（or_yoy / np_yoy），金额缺失或 fina 为空时
    用收入/净利金额 YoY 兜底（基期金额>0 才有效）。
    """
    df = fin.copy()
    for i in range(4):
        rev, rev_ly = _num(df[f"rev_{i}"]), _num(df[f"rev_ly_{i}"])
        np_, np_ly = _num(df[f"np_{i}"]), _num(df[f"np_ly_{i}"])

        rg_amt = pd.Series(np.nan, index=df.index)
        ok = rev.notna() & rev_ly.notna() & (rev_ly > 0)
        rg_amt[ok] = (rev[ok] / rev_ly[ok] - 1.0) * 100.0

        pg_amt = pd.Series(np.nan, index=df.index)
        ok = np_.notna() & np_ly.notna() & (np_ly > 0)
        pg_amt[ok] = (np_[ok] / np_ly[ok] - 1.0) * 100.0

        rg = _num(df[f"or_yoy_{i}"]).copy()
        df[f"rg_{i}"] = rg.where(rg.notna(), rg_amt)
        pg = _num(df[f"np_yoy_{i}"]).copy()
        df[f"pg_{i}"] = pg.where(pg.notna(), pg_amt)
        df[f"gm_{i}"] = _num(df[f"gm_{i}"])
        df[f"rev_{i}"] = rev
        df[f"np_{i}"] = np_
        df[f"rev_ly_{i}"] = rev_ly
        df[f"np_ly_{i}"] = np_ly
    return df


# ── 行业聚合 ──────────────────────────────────────────

def _agg_yoy(g: pd.DataFrame, cur: str, ly: str, med: str) -> float:
    """行业口径增速：优先金额加总 YoY（成员>=3），否则个股增速中位数兜底。"""
    cur_v, ly_v = _num(g[cur]), _num(g[ly])
    mask = cur_v.notna() & ly_v.notna() & (ly_v > 0) & (cur_v > 0)
    k = int(mask.sum())
    if k >= 3:
        denom = float(ly_v[mask].sum())
        numer = float(cur_v[mask].sum())
        if denom > 0:
            return (numer / denom - 1.0) * 100.0
    med = _num(g[med])
    if med.notna().any():
        return float(med.median())
    return float("nan")


def _elasticity(g: pd.DataFrame) -> pd.Series:
    """每股利润弹性 = pg / rg（要求营收增速>=MIN、利润增速有值，异常值截断）。"""
    rg = _num(g["rg_0"])
    pg = _num(g["pg_0"])
    el = pd.Series(np.nan, index=g.index)
    ok = rg.notna() & pg.notna() & (rg >= MIN_ELASTIC_REV_YOY)
    el[ok] = pg[ok] / rg[ok]
    el = el.clip(-MAX_ELASTIC_ABS, MAX_ELASTIC_ABS)
    return el


def _rev_latest(g: pd.DataFrame) -> pd.Series:
    """每股最近可得营收金额（q0 缺失则依次回退到 q1..q3）。"""
    out = pd.Series(np.nan, index=g.index, dtype=float)
    for i in range(4):
        v = _num(g[f"rev_{i}"])
        out = out.where(out.notna(), v)
    return out


def _price_mean(g: pd.DataFrame, col: str) -> float:
    v = _num(g[col])
    if v.notna().any():
        return float(v.mean())
    return float("nan")


# ── 生命周期 ──────────────────────────────────────────

def classify_lifecycle(dg0: float, dg1: float, pg0: float, pg1: float,
                       macc: float) -> tuple[str, float]:
    """行业生命周期分类（v1 判定规则，配合 LIFECYCLE_ADJ 调整值）。

    ACCELERATION 最优先（收入↑ 利润↑↑ 毛利↑ → +10）；
    CONTRACTION -20 / DECELERATION -10 / EARLY_EXPANSION +5 / MATURE_GROWTH 0。
    数据不足时给 MATURE_GROWTH(0)，避免人为高分或过低分。
    """
    adj = LIFECYCLE_ADJ["MATURE_GROWTH"]
    label = "MATURE_GROWTH"
    if pd.isna(dg0) and pd.isna(pg0):
        return label, float(adj)

    pa = (pg0 - pg1) if (pd.notna(pg0) and pd.notna(pg1)) else np.nan
    da = (dg0 - dg1) if (pd.notna(dg0) and pd.notna(dg1)) else np.nan
    macc = macc if pd.notna(macc) else 0.0

    if pd.notna(pg0) and pd.notna(dg0):
        if dg0 < 0 and pg0 < 0:
            label, adj = "CONTRACTION", LIFECYCLE_ADJ["CONTRACTION"]
        elif dg0 < 0 and pg0 >= 0:            # 收入降、利润升（修复/一次性）
            if macc > 0:
                label, adj = "EARLY_EXPANSION", LIFECYCLE_ADJ["EARLY_EXPANSION"]
            else:
                label, adj = "DECELERATION", LIFECYCLE_ADJ["DECELERATION"]
        elif pg0 < 0:                          # 收入增、利润降 → 负经营杠杆
            label, adj = "DECELERATION", LIFECYCLE_ADJ["DECELERATION"]
        else:                                  # 收入↑ 利润↑
            if (pd.notna(pa) and pa <= -2.0):
                label, adj = "DECELERATION", LIFECYCLE_ADJ["DECELERATION"]
            elif (pd.notna(pa) and pa > 2.0) and ((pd.notna(da) and da > 0) or macc > 0.3):
                label, adj = "ACCELERATION", LIFECYCLE_ADJ["ACCELERATION"]
            elif dg0 >= MIN_ELASTIC_REV_YOY and pg0 > dg0 * 1.3:
                label, adj = "EARLY_EXPANSION", LIFECYCLE_ADJ["EARLY_EXPANSION"]
            else:
                label, adj = "MATURE_GROWTH", LIFECYCLE_ADJ["MATURE_GROWTH"]
    elif pd.notna(pg0):
        label, adj = (("DECELERATION", LIFECYCLE_ADJ["DECELERATION"]) if pg0 < 0
                      else ("MATURE_GROWTH", LIFECYCLE_ADJ["MATURE_GROWTH"]))
    elif pd.notna(dg0):
        label, adj = (("CONTRACTION", LIFECYCLE_ADJ["CONTRACTION"]) if dg0 < -5
                      else ("MATURE_GROWTH", LIFECYCLE_ADJ["MATURE_GROWTH"]))
    return label, float(adj)


# ── 行业层级打分表 ─────────────────────────────────────

def build_level_table(wide: pd.DataFrame, code_col: str, sli: pd.DataFrame | None,
                      bench: dict[str, float]) -> pd.DataFrame:
    """对一个层级（code_col ∈ l1_code/l2_code/l3_code）聚合出全部原始指标，再打分。

    返回（code 为索引）：
      原始列 n_total/n_q0/dg0..3/pg0/pg1/ra/pa/macc/mgm/el_lead/el_broad/
            mr20/mr60/mr120/xr20/xr60/xr120
      打分列 demand_score/profit_elasticity_score/acceleration_score/
            supply_demand_score/market_elasticity_score/ige_raw/ige
    """
    # 每股聚合指标一次性算好（避免每组重复计算）
    base = wide[["ts_code", code_col]].copy()
    g = wide.groupby(code_col)
    out: list[dict] = []

    for code, idx in g.groups.items():
        sub = wide.loc[idx]
        sub = sub.copy()
        el = _elasticity(sub)
        sub["_el"] = el
        rgl = _rev_latest(sub)
        sub["_rgl"] = rgl

        dg = [(_agg_yoy(sub, f"rev_{i}", f"rev_ly_{i}", f"rg_{i}")) for i in range(4)]
        pg = [(_agg_yoy(sub, f"np_{i}", f"np_ly_{i}", f"pg_{i}")) for i in range(4)]

        # 各报告期行业毛利率中位数（v1.2 持续性/动量用，纯原始列，不进五因子）
        gm_med = []
        for i in range(4):
            gv = _num(sub[f"gm_{i}"])
            gm_med.append(float(gv.median()) if gv.notna().any() else np.nan)
        # macc 沿用 v1.1 语义：行业内个股毛利率环比(中位数口径)（不进五因子新列）
        gm0_s, gm1_s = _num(sub["gm_0"]), _num(sub["gm_1"])
        macc_df = (gm0_s - gm1_s)
        macc_df = macc_df[macc_df.notna()]

        # 龙头选取：SLI_V2 得分高者优先，其次营收规模（先排序后取位置切片，
        # 避免 merge 重置 index 后 elv 标签错位）
        if sli is not None:
            sub = sub.merge(sli[["ts_code", "sli_v2"]], on="ts_code", how="left",
                            suffixes=("", "_sli"))
        else:
            sub["sli_v2"] = np.nan
        sub["sli_v2"] = _num(sub["sli_v2"])
        sub = sub.sort_values(["sli_v2", "_rgl"], ascending=[False, False])
        elv = _num(sub["_el"])
        n_el = int(elv.notna().sum())
        k = max(1, min(LEADER_MAX, n_el)) if n_el else 0
        lead_el = elv.iloc[:k].dropna() if k else pd.Series(dtype=float)

        row: dict = {
            "code": code,
            "n_total": int(len(sub)),
            "n_q0": int(elv.notna().sum()),
            "dg0": dg[0], "dg1": dg[1], "dg2": dg[2], "dg3": dg[3],
            "pg0": pg[0], "pg1": pg[1], "pg2": pg[2], "pg3": pg[3],
            "ra": (dg[0] - dg[1]) if (pd.notna(dg[0]) and pd.notna(dg[1])) else np.nan,
            "pa": (pg[0] - pg[1]) if (pd.notna(pg[0]) and pd.notna(pg[1])) else np.nan,
            "macc": float(macc_df.median()) if len(macc_df) else np.nan,
            "mgm": gm_med[0],
            "gm1": gm_med[1], "gm2": gm_med[2], "gm3": gm_med[3],
            "el_lead": float(lead_el.median()) if len(lead_el) else np.nan,
            "el_broad": float(elv.median()) if n_el else np.nan,
            "mr20": _price_mean(sub, "w20"),
            "mr60": _price_mean(sub, "w60"),
            "mr120": _price_mean(sub, "w120"),
            # v1.1 F5 传导β：行业内截面回归 盈利加速(pa) → 个股超额收益(e)。
            # 时间序列数据源不足时的截面代理（Spec §9），口径在代码注释中如实标注。
            "beta": np.nan,
            "beta_obs": 0,
        }
        for wk in ("w20", "w60", "w120"):
            mr = row[f"mr{wk[1:]}"]
            row[f"xr{wk[1:]}"] = (mr - bench.get(f"b{wk[1:]}", np.nan)) \
                if pd.notna(mr) and pd.notna(bench.get(f"b{wk[1:]}")) else np.nan

        # β = slope(e_i ~ pa_i)：个股 w120 超额收益（缺失回退 w60）对个股利润加速的截面斜率
        pa_i = _num(sub["pg_0"]) - _num(sub["pg_1"])
        e120 = _num(sub["w120"]) - bench.get("b120", np.nan)
        e60 = _num(sub["w60"]) - bench.get("b60", np.nan)
        e_i = e120.where(e120.notna(), e60)
        m = pa_i.notna() & e_i.notna()
        n_beta = int(m.sum())
        if n_beta >= BETA_MIN_OBS:
            x = pa_i[m].astype(float).clip(-150.0, 150.0)
            y = e_i[m].astype(float).clip(-80.0, 300.0)
            if len(x) >= BETA_MIN_OBS and x.nunique() > 1:
                row["beta"] = float(np.polyfit(x, y, 1)[0])
                row["beta_obs"] = int(len(x))
        out.append(row)

    tab = pd.DataFrame(out).set_index("code")
    for c in tab.columns:
        tab[c] = _num(tab[c])

    # 增长趋势斜率：对最近4期增速做线性回归。
    # 按时间顺序（dg3 最旧 → dg0 最新）排列，斜率为正 = 增速向当前加速。
    def _slope_of(r: pd.Series, pref: str) -> float:
        y = [r[f"{pref}{i}"] for i in (3, 2, 1, 0)]
        if any(pd.isna(v) for v in y):
            return float("nan")
        x = np.arange(4, dtype=float)
        return float(np.polyfit(x, np.asarray(y, dtype=float), 1)[0])

    def _slope(r: pd.Series) -> float:
        return _slope_of(r, "dg")

    tab["dslope"] = tab.apply(_slope, axis=1)
    # v1.2：利润增速 4 期斜率（IGE_MOM 利润分量；纯原始列，不进 F3 打分）
    tab["pslope"] = tab.apply(lambda r: _slope_of(r, "pg"), axis=1)

    # ── 五因子打分（各分量先 winsorize + percentile rank，再加权）──
    tab["_p_dg"] = pr_col(tab["dg0"])
    tab["_p_da"] = pr_col(tab["ra"])
    tab["_p_slope"] = pr_col(tab["dslope"])
    tab["demand_score"] = weighted_blend(tab, ["_p_dg", "_p_da", "_p_slope"],
                                         [W_DG, W_DA, W_SLOPE])

    tab["_p_el_lead"] = pr_col(tab["el_lead"])
    tab["_p_el_broad"] = pr_col(tab["el_broad"])
    tab["profit_elasticity_score"] = weighted_blend(
        tab, ["_p_el_lead", "_p_el_broad"], [W_IPE, W_IPE_BROAD])

    tab["_p_ra"] = pr_col(tab["ra"])
    tab["_p_pa"] = pr_col(tab["pa"])
    tab["_p_macc"] = pr_col(tab["macc"])
    tab["acceleration_score"] = weighted_blend(tab, ["_p_ra", "_p_pa", "_p_macc"],
                                               [W_RA, W_PA, W_MA])

    # F4 供需价格弹性：本期无产品价格数据源 → 以行业毛利率水平作为传导代理（fail-soft）
    tab["supply_demand_score"] = pr_col(tab["mgm"])

    # F5 行业股价弹性 = (1-W_MKT_BETA) × 窗口超额收益分位 + W_MKT_BETA × 传导β分位。
    # β 缺失（样本不足/无价格数据）时 fail-soft，自动回到纯窗口口径。
    tab["_p_xr20"] = pr_col(tab["xr20"])
    tab["_p_xr60"] = pr_col(tab["xr60"])
    tab["_p_xr120"] = pr_col(tab["xr120"])
    tab["_p_beta"] = pr_col(tab["beta"])
    tab["_mkt_win"] = weighted_blend(
        tab, ["_p_xr20", "_p_xr60", "_p_xr120"], [W_XR20, W_XR60, W_XR120])
    tab["market_elasticity_score"] = weighted_blend(
        tab, ["_mkt_win", "_p_beta"], [1.0 - W_MKT_BETA, W_MKT_BETA])

    tab["ige_raw"] = weighted_blend(
        tab,
        ["demand_score", "profit_elasticity_score", "acceleration_score",
         "supply_demand_score", "market_elasticity_score"],
        [W_DEMAND, W_PROFIT_EL, W_ACCEL, W_SUPPLY, W_MARKET])
    # v1.1：ige_mix 是生命周期调整之前的基础行业弹性，禁止再做一次横截面
    # PercentileRank（§10 重要规则）。保留同值 ige_raw 供审计。
    tab["ige"] = tab["ige_raw"].copy()

    # ── v1.2 逐级动量/持续性原始分（mom_raw / pers_raw，0~100）──────────
    # IGE_MOM（成分趋势动量，用户确认口径）：营收4期斜率 + 利润4期斜率 +
    # 毛利率环比(macc) + 60D-20D超额动能，各 winsorize→横截面分位后加权。
    # 仅新增输出，不触碰五因子任何分数。
    tab["_p_dslope"] = pr_col(tab["dslope"])
    tab["_p_pslope"] = pr_col(tab["pslope"])
    tab["mom_mkt"] = _num(tab["xr60"]) - _num(tab["xr20"])
    tab["_p_mom_mkt"] = pr_col(tab["mom_mkt"])
    tab["mom_raw"] = weighted_blend(
        tab, ["_p_dslope", "_p_pslope", "_p_macc", "_p_mom_mkt"],
        [W_MOM_REV, W_MOM_PROF, W_MOM_MARGIN, W_MOM_MKT])

    # IGE_PERSISTENCE（§5 权重 30/30/20/20）：营收/利润 4 期持续为正占比 +
    # 毛利率相邻期环比未明显走弱 + 60D/120D 超额收益持续为正占比。
    tab["rev_cont"] = _pos_frac(tab, ["dg0", "dg1", "dg2", "dg3"], 0.0)
    tab["prof_cont"] = _pos_frac(tab, ["pg0", "pg1", "pg2", "pg3"], 0.0)
    tab["gm_cont"] = tab.apply(_gm_continuity, axis=1)
    tab["xr_cont"] = _pos_frac(tab, ["xr60", "xr120"], 0.0)
    tab["pers_raw"] = weighted_blend(
        tab, ["rev_cont", "prof_cont", "gm_cont", "xr_cont"],
        [W_PERS_REV, W_PERS_PROF, W_PERS_GM, W_PERS_XR])

    drop = ["_p_dg", "_p_da", "_p_slope", "_p_el_lead", "_p_el_broad",
            "_p_ra", "_p_pa", "_p_macc", "_p_xr20", "_p_xr60", "_p_xr120",
            "_mkt_win", "_p_beta",
            "_p_dslope", "_p_pslope", "_p_mom_mkt", "mom_mkt"]
    tab = tab.drop(columns=[c for c in drop if c in tab.columns])
    return tab


def build_all_levels(wide: pd.DataFrame, sli: pd.DataFrame | None,
                     bench: dict[str, float]) -> dict[str, pd.DataFrame]:
    """对 L1/L2/L3 三级分别构建行业打分表。"""
    out = {}
    for lv, col in (("L3", "l3_code"), ("L2", "l2_code"), ("L1", "l1_code")):
        tab = build_level_table(wide, col, sli, bench)
        out[lv] = tab
        logger.info("层级 %s：行业数 %d", lv, len(tab))
    return out


# ── 样本收缩权重 ──────────────────────────────────────

def shrink_weights(n: float) -> tuple[float, float, float]:
    """三级样本收缩：N>=15 → (1,0,0)；10<=N<15 → (0.7,0.3,0)；N<10 → (0.4,0.4,0.2)。"""
    if n >= SAMPLE_FULL:
        return 1.0, 0.0, 0.0
    if n >= SAMPLE_MIX:
        return 0.7, 0.3, 0.0
    return 0.4, 0.4, 0.2


# ── v1.1 行业类型 / 状态 / 加速确认 / 置信度档位 ─────────

# L1 名称 → 行业弹性类型（行业天然属性，解释用，不参与打分）。
# 未命中模板的行业（家电/食品饮料/医药/机械/公用等）按增长兜底。
_L1_TYPE = {
    "煤炭": "PRICE_DRIVEN", "石油石化": "PRICE_DRIVEN",
    "有色金属": "PRICE_DRIVEN", "钢铁": "PRICE_DRIVEN",
    "基础化工": "CYCLICAL", "建筑材料": "CYCLICAL",
    "交通运输": "CYCLICAL", "农林牧渔": "CYCLICAL", "轻工制造": "CYCLICAL",
    "电子": "TECHNOLOGY", "计算机": "TECHNOLOGY", "通信": "TECHNOLOGY",
    "非银金融": "FINANCIAL_BETA", "银行": "FINANCIAL_BETA",
}


def industry_type_of(df: pd.DataFrame) -> pd.Series:
    """IndustryElasticityType：GROWTH / CYCLICAL / TECHNOLOGY / FINANCIAL_BETA /
    PRICE_DRIVEN / DEFENSIVE / LOW_ELASTICITY。

    需要列：l1_name, dg0, pg0。L1 模板优先；其余按收入/利润增速兜底。
    """
    def _s(x) -> str:
        return "" if pd.isna(x) else str(x).strip()

    l1 = df["l1_name"].map(_s)
    base = l1.map(_L1_TYPE)                     # NaN = 未命中模板
    need = base.isna()
    out = pd.Series("DEFENSIVE", index=df.index, dtype=object)
    out[~need] = base[~need]
    dg = _num(df["dg0"])
    pg = _num(df["pg0"])
    for idx in df.index[need]:
        d, p = dg.get(idx, np.nan), pg.get(idx, np.nan)
        if l1.get(idx, "") == "医药生物":
            # 医药按研发成长口径：需求不差即算成长（产品/订单驱动），
            # 需求与利润双降才算低弹性
            if pd.notna(d) and d >= 0.0:
                out[idx] = "GROWTH"
            elif pd.notna(p) and p >= 0.0:
                out[idx] = "DEFENSIVE"
            else:
                out[idx] = "LOW_ELASTICITY"
        elif pd.notna(d) and d >= 10.0:
            out[idx] = "GROWTH"
        elif pd.isna(d) and pd.notna(p) and p < 0:
            out[idx] = "LOW_ELASTICITY"
        elif pd.isna(d) and pd.notna(p):
            out[idx] = "DEFENSIVE"
        elif pd.isna(d):
            out[idx] = "DEFENSIVE"
        elif pd.notna(p) and p >= 15.0:
            out[idx] = "GROWTH"                  # 收入温和但利润高增：利润驱动型成长
        elif d >= 0.0:
            out[idx] = "DEFENSIVE"
        elif pd.notna(p) and p > 0:
            out[idx] = "GROWTH"                  # 收入降但利润修复型：边际改善
        else:
            out[idx] = "LOW_ELASTICITY"
    return out.astype(str)


def industry_state_of(lifecycle: pd.Series, ige_mix: pd.Series) -> pd.Series:
    """IndustryElasticityState：由生命周期 + igemix 判定。
    ELASTICITY_TRAP：igemix 高但生命周期 DECELERATION/CONTRACTION。
    需要列：industry_lifecycle, ige_mix。"""
    lc = lifecycle.fillna("MATURE_GROWTH").astype(str)
    mix = _num(ige_mix)
    trap = mix >= TRAP_IGE_MIX
    s = lc.map({
        "ACCELERATION": "ACCELERATING",
        "EARLY_EXPANSION": "EXPANSION",
        "MATURE_GROWTH": "STABLE",
        "DECELERATION": "DECELERATING",
        "CONTRACTION": "CONTRACTION",
    }).fillna("STABLE").astype(str)
    mask = lc.isin(["DECELERATION", "CONTRACTION"]) & trap
    s = s.where(~mask, "ELASTICITY_TRAP")
    return s


def acceleration_confirm_of(df: pd.DataFrame) -> pd.Series:
    """ACCELERATION_CONFIRM：Revenue↑ + Profit↑↑ + GrossMargin↑ 三者同时成立。
    纯状态标签，不修改任何分数（Spec §7 特别奖励）。NaN 视为不成立。"""
    dg0, pg0, pg1, macc = (_num(df[c]) for c in ("dg0", "pg0", "pg1", "macc"))
    pa = pg0 - pg1
    rev = dg0 > 0.0
    prof = (pg0 > 0.0) & (pa > 0.0)
    gm = macc > 0.0
    return (rev & prof & gm).fillna(False)


def confidence_label(n: float) -> str:
    """IndustryConfidence 档位：N3>=15→HIGH；10<=N3<15→MEDIUM；N3<10→LOW。"""
    if pd.isna(n):
        return "LOW"
    if n >= SAMPLE_FULL:
        return "HIGH"
    if n >= SAMPLE_MIX:
        return "MEDIUM"
    return "LOW"


# ── v1.2 行业类型微调 / 机会类型 / 最严格行业门槛 / 分层排序 ────────
# 全部为纯标签，不改动五因子与 IGE_ADJ。规则集中在 config，阈值可配。

_TECH_GROWTH = ("TECHNOLOGY", "GROWTH")
_CYCL_PRICE = ("CYCLICAL", "PRICE_DRIVEN")


def structural_elasticity_of(df: pd.DataFrame) -> pd.Series:
    """STRUCTURAL_ELASTICITY 标记（§6 类型微调）：仅 TECHNOLOGY/GROWTH，
    IGE_ADJ≥75 且生命周期/陷阱未否定。结构性弹性 → 回踩可布局。
    纯标记，不改分数。"""
    typ = df["industry_elasticity_type"].fillna("").astype(str)
    lc = df["industry_lifecycle"].fillna("").astype(str)
    st = df["industry_elasticity_state"].fillna("").astype(str)
    return (typ.isin(_TECH_GROWTH)
            & (_num(df["ige_adj"]) >= STRUCTURAL_ELASTICITY_IGE)
            & ~lc.isin(["DECELERATION", "CONTRACTION"])
            & (st != "ELASTICITY_TRAP")).fillna(False)


def cyclical_high_elasticity_of(df: pd.DataFrame) -> pd.Series:
    """CYCLICAL_HIGH_ELASTICITY 标记（§6 类型微调）：CYCLICAL/PRICE_DRIVEN
    且 IGE_ADJ≥80。周期高弹性只做弹性交易，不做结构持有。"""
    typ = df["industry_elasticity_type"].fillna("").astype(str)
    return (typ.isin(_CYCL_PRICE)
            & (_num(df["ige_adj"]) >= CYCLICAL_HIGH_ELASTICITY_IGE)).fillna(False)


def cyclical_low_persistence_of(df: pd.DataFrame) -> pd.Series:
    """周期高弹性但持续性不足警告（§6）：周期高弹性 & IGE_PERSISTENCE<50
    时，即便 IGE_ADJ 高也必须标注，防把价格弹性误读为可持续增长。"""
    che = cyclical_high_elasticity_of(df)
    return (che & (_num(df["ige_persistence"]) < CYCLICAL_HIGH_ELASTICITY_PERS)
            ).fillna(False)


def opportunity_type_of(df: pd.DataFrame) -> pd.Series:
    """IGE_OPPORTUNITY_TYPE：在静态行业弹性类型之上叠加 动量/持续性/陷阱，
    回答"当前这份弹性是什么性质的机会"。np.select 首命优先（陷阱/类型微调
    最优先），阈值全部来自 config，不改任何分数。"""
    typ = df["industry_elasticity_type"].fillna("").astype(str)
    st = df["industry_elasticity_state"].fillna("").astype(str)
    lc = df["industry_lifecycle"].fillna("").astype(str)
    adj = _num(df["ige_adj"])
    mom = _num(df["ige_mom"])
    pers = _num(df["ige_persistence"])

    trap = st == "ELASTICITY_TRAP"
    decel = lc.isin(["DECELERATION", "CONTRACTION"])
    cycl_high = typ.isin(_CYCL_PRICE) & (adj >= CYCLICAL_HIGH_ELASTICITY_IGE)
    struct = (typ.isin(_TECH_GROWTH) & (adj >= STRUCTURAL_ELASTICITY_IGE)
              & ~decel & ~trap)
    hi = adj >= HIGH_IGE                    # 70：与分层/封顶口径一致（≥70 即"高 IGE"档）
    # 说明：MOMENTUM/PERSISTENT/DECELERATING_HIGH 均以 ≥70 为高 IGE 界，
    # 与 RANK_TIER 分层一致；STRUCTURAL 仍按 ≥75、CYCLICAL_HIGH 按 ≥80 微调。

    cond = [
        trap & (adj >= HIGH_IGE),                 # 名义高但下滑 → 陷阱
        cycl_high & ~trap,                        # 周期/价格驱动高弹（含低速续，见独立标记）
        struct & ~trap,                           # 科技/成长 结构性高弹
        hi & decel,                               # 高位回落
        hi & (mom >= MOM_STRONG),                 # 高位+动能向上
        hi & (pers >= PERS_GOOD),                 # 高位+高持续性
        adj >= HIGH_IGE,                          # 70~80 增长弹性
        adj >= LOW_IGE_BOUND,                     # 50~70 中性弹性
        adj.notna(),                              # <50 低弹性
    ]
    choice = ["ELASTICITY_TRAP", "CYCLICAL_HIGH_ELASTICITY",
              "STRUCTURAL_ELASTICITY", "DECELERATING_HIGH_ELASTICITY",
              "MOMENTUM_ELASTICITY", "PERSISTENT_ELASTICITY",
              "HIGH_ELASTICITY", "MODERATE_ELASTICITY", "LOW_ELASTICITY"]
    return pd.Series(np.select(cond, choice, default="DATA_INSUFFICIENT"),
                     index=df.index)


def rocket_core_of(df: pd.DataFrame, sia_col: str | None = "sia") -> pd.Series:
    """T120_ROCKET_CORE（§12 最严格行业门槛；§7 陷阱强制 FALSE）：
    行业档 = IGE_EFFECTIVE≥75 & IGE_MOM≥60 & IGE_PERSISTENCE≥60 &
    生命周期∈(EARLY_EXPANSION,ACCELERATION) & 非 ELASTICITY_TRAP；
    股票档（传 sia_col）再加 个股 SIA≥75。"""
    lc = df["industry_lifecycle"].fillna("").astype(str)
    st = df["industry_elasticity_state"].fillna("").astype(str)
    m = ((_num(df["ige_effective"]) >= CORE_IGE_EFFECTIVE)
         & (_num(df["ige_mom"]) >= CORE_MOM)
         & (_num(df["ige_persistence"]) >= CORE_PERS)
         & lc.isin(CORE_LIFECYCLE)
         & (st != "ELASTICITY_TRAP"))
    if sia_col is not None:
        m = m & (_num(df[sia_col]) >= CORE_SIA)
    return m.fillna(False)


def rank_tier(df: pd.DataFrame, sia_col: str | None = "sia") -> tuple[pd.Series, pd.Series]:
    """§13 分层排序（不再简单按 IGE_ADJ DESC）。
    行业档传 sia_col=None：行业自身即参照，SIA 视为高。
    返回 (tier 0~5, 理由)。tier 越小优先级越高；0=不满足高 IGE 前提。"""
    adj = _num(df["ige_adj"])
    mom = _num(df["ige_mom"])
    pers = _num(df["ige_persistence"])
    lc = df["industry_lifecycle"].fillna("").astype(str)
    st = df["industry_elasticity_state"].fillna("").astype(str)
    decel = lc.isin(["DECELERATION", "CONTRACTION"]) | (st == "ELASTICITY_TRAP")
    if sia_col is not None:
        sia_high = _num(df[sia_col]) >= RANK_SIA_HIGH
    else:
        sia_high = pd.Series(True, index=df.index)

    high = adj >= RANK_HIGH_IGE
    m5 = high & decel
    m1 = high & ~decel & sia_high & (mom >= RANK_HIGH_MOM)
    m2 = high & ~decel & sia_high & (pers >= RANK_HIGH_PERS) \
        & (mom < RANK_HIGH_MOM)
    m3 = high & ~decel & sia_high & ~(m1 | m2)
    m4 = high & ~decel & ~sia_high & ~(m1 | m2 | m3)

    tier = pd.Series(0, index=df.index, dtype=int)
    tier[m5] = 5
    tier[m1] = 1
    tier[m2] = 2
    tier[m3] = 3
    tier[m4] = 4
    reason = pd.Series("IGE_ADJ<70 不参与分层", index=df.index, dtype=object)
    for t in range(1, 6):
        reason[tier == t] = RANK_TIER[t]
    reason[adj.isna()] = "IGE_ADJ缺失"
    return tier, reason


# ── 行业专属解释（按弹性类型 / 行业板块定制模板）──────────

_LIFECYCLE_CN = {
    "EARLY_EXPANSION": "景气由负转正、盈利开始改善，处于扩张早期",
    "ACCELERATION": "收入/利润加速、毛利改善、超额收益增强，景气处于加速段",
    "MATURE_GROWTH": "仍增长但增速平稳、弹性趋常，处于成熟增长",
    "DECELERATION": "收入/利润/加速度均向下，即便历史弹性高也需降交易权重",
    "CONTRACTION": "收入与利润双降，处于低当前弹性收缩状态",
}

_TYPE_CN = {
    "GROWTH": "成长型：需求/份额扩张推动收入利润同向高增，弹性来自渗透率提升",
    "CYCLICAL": "周期型：价格/供需→毛利→利润传导，盈利随价格同向放大、波动大",
    "TECHNOLOGY": "科技型：需求→收入→利润→股价传导，弹性来自产品周期与技术渗透",
    "FINANCIAL_BETA": "金融贝塔型：弹性主要来自市场风险偏好/成交量/信用周期，"
                      "利润弹性不与制造/科技直接可比（解释以市场弹性为主）",
    "PRICE_DRIVEN": "商品价格驱动型：产品价格为主要矛盾，价格↑→毛利↑→利润↑↑，盯价格拐点",
    "DEFENSIVE": "防御型：需求稳定但弹性有限，景气传导到利润与股价偏平",
    "LOW_ELASTICITY": "低弹性型：增长与利润弹性均低，对景气改善不敏感",
}

_MED_TXT = ("医药型：研发投入→产品/订单→收入→利润，关注新品放量与订单兑现节奏，"
            "不套用制造/周期模板")
_SUPPLY_NOTE = "F4未接入真实产品价格（PRICE_DATA_MISSING），以毛利率水平作供需/价格传导代理。"


def typed_explain(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """按 类型+生命周期 生成 explain_label / explain_text。
    需要列：l1_name, industry_elasticity_type, industry_lifecycle,
            industry_elasticity_state, industry_confidence, industry_sample_n。"""
    labels: list[str] = []
    texts: list[str] = []
    for _, r in df.iterrows():
        t = str(r.get("industry_elasticity_type", "") or "DEFENSIVE")
        st = str(r.get("industry_elasticity_state", "") or "STABLE")
        lc = str(r.get("industry_lifecycle", "") or "MATURE_GROWTH")
        l1 = str(r.get("l1_name", "") or "")
        n = r.get("industry_sample_n", np.nan)
        conf = str(r.get("industry_confidence", "") or confidence_label(n))

        # 医药生物（含 CXO/医疗研发外包等）用独立解释模板（Spec §19）
        tpl = _MED_TXT if l1 == "医药生物" else _TYPE_CN.get(t, _TYPE_CN["DEFENSIVE"])
        text = (f"{t}行业，{_LIFECYCLE_CN.get(lc, '生命周期待定')}。"
                f"解释：{tpl}。")
        if conf != "HIGH":
            text += f"三级成分样本不足（N={n if pd.notna(n) else 'NA'}），"
            text += "igemix采用L3/L2/L1混合，置信度" + conf + "。"
        if t in ("PRICE_DRIVEN", "CYCLICAL"):
            text += _SUPPLY_NOTE
        labels.append(f"{t}|{st}")
        texts.append(text)
    return pd.Series(labels, index=df.index), pd.Series(texts, index=df.index)
