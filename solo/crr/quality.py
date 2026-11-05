# -*- coding: utf-8 -*-
"""
CRR V1.0 模块三：基本面质量（Quality）30 分 + VETO 否决

职责（只对模块二初筛出的候选股做深度财务评估，占评分 30 分）：
  1. 逐股提取 12 个质量子项，按 config.QUALITY_WEIGHTS 加权；
  2. 缺失子项「不记 0、不臆造」，按可用权重重新归一：
         q_score        = 30 * Σ(w_i * s_i) / Σ(w_i, s_i 已知)
         q_completeness = Σ(w_i, s_i 已知) / 30
     其中 s_i ∈ [0,1] 为该子项打分，缺失为 NaN；全部缺失时 q_score = NaN；
  3. 命中 config.VETO 任一条即标记 q_veto=True（由 engine 输出 NO TRADE）；
     「无数据不触发」，缺口写入 q_veto_unknown / q_unknown_fields；
  4. 金融股（VETO.financial_l1）豁免 debt_to_assets 相关项（不评分、不否决）。

打分映射（如实披露，绝不冒充用户口径、绝不编造底层数据）：
  * 用户仅定义到「30 分 + 12 子项权重 + VETO 绝对阈值」，未给每个子项的
    「多少算好」阈值。本模块把每个子项的原始值线性映射为 0~1 分，映射锚点
    集中在 _SCORING_ANCHORS，属于模块内部可调参数，非用户给定规格、非实测数据，
    meta.scoring_anchors / meta.proxies / meta.unknown_notes 逐项披露。
  * 采用「绝对锚点」而非横截面分位：使分数具备绝对意义，多数平庸/劣质股自然得低分，
    从而可能出现大量 NO TRADE，符合「不强制每日交易」的目标，也与
    DECISION.buy_score_ratio 的绝对门槛口径一致。
  * VETO 用 config 给定的绝对阈值守红线；打分做绝对刻度，二者分工明确。

各子项数据来源分层（全部经 data.py 的 as-of API，禁止绕过）：
  roe                     fina_indicator_cache.roe(%) → sli.roe
  roic                    sli.roic(%) → parquet fina_indicator_.roic
  revenue_growth          fina.or_yoy(%) → sli.or_yoy
  deducted_profit_growth  sli.dt_netprofit_yoy(%) → parquet profit_dedt 同比
                          → parquet n_income_attr_p 同比（净利代理，需披露）
  ocf_match               sli.ocf_to_profit(%) → parquet cashflow/income 同期比
  gross_margin            fina.grossprofit_margin(%) → sli.grossprofit_margin
                          （parquet fina_indicator_.gross_margin 是毛利额，非比率，禁用）
  gross_margin_trend      sli.grossprofit_margin - sli.gm_prev → fina 序列相邻期差
  debt_to_assets          fina.debt_to_assets(%) → parquet fina_indicator_.debt_to_assets
  interest_debt           无字段 → 恒 UNKNOWN（与 data.audit_data_sources 口径一致）
  goodwill_ratio          parquet balance.goodwill / total_hldr_eqy_exc_min_int
                          （goodwill 为 NULL 判 UNKNOWN，不可当 0）
  recent_3q               fina 序列最近 3 期 netprofit_yoy 均值（回退 or_yoy）
  forecast_change         forecast_vip p_change_min/max 中值（%）→ 无预告 UNKNOWN

as-of 防未来：
  * fina  ann_date <= scan_date（load_fina_asof / load_fina_series）
  * sli   eval_date <= scan_date（load_sli_asof）
  * parquet 文件级 has-ann_date 判定 + ann_date <= scan_date（load_parquet_statements）
  * forecast 快照文件 <= scan_date 且 ann_date <= scan_date（load_forecast）
  * 行业映射经 load_industry_map(scan_date)（SLI eval_date / sw_member in-out_date 时点过滤）
  * 无 ann_date 的 parquet 家族（fina_indicator_q_/yoy_）由 data 层直接判 UNKNOWN 不读取
  * 现金流为年内累计口径：判定「连续 N 季 OCF 为负」必须同年内差分得单季，
    且相邻报告期月份差 <=4，否则该单季判 UNKNOWN；Q1 本身即单季值。

评分边界：模块三仅 30 分；SCORE_CAP=55 与 PENDING_MODULES 见 config，engine/report 读取。
列名一律加 q_ 前缀，避免 engine 合并时覆盖模块二 data_completeness / unknown_fields。

对外 API：
  compute_quality(ts_codes, scan_date, ...) -> (quality_df, meta)
  build_quality(scan_date, candidates, ...)  -> dict（engine 主入口）
"""
import numpy as np
import pandas as pd

from . import data as D
from .config import QUALITY_WEIGHTS, VETO, UNKNOWN, SCORE_CAP, PENDING_MODULES


# ----------------------------------------------------------------------------
# 内部打分锚点（非用户给定规格、非实测数据；见文件头「打分映射」说明）
#   (lo, hi, higher_better)：
#     higher_better=True  -> lo 记 0 分、hi 记 1 分，线性 clip
#     higher_better=False -> lo 记 1 分、hi 记 0 分，线性 clip（值越小越好）
# ----------------------------------------------------------------------------
_SCORING_ANCHORS = {
    "roe":                     (0.0, 20.0, True),    # %，累计口径未年化
    "roic":                    (0.0, 15.0, True),    # %
    "revenue_growth":          (-10.0, 30.0, True),  # 营收同比 %
    "deducted_profit_growth":  (-20.0, 30.0, True),  # 扣非净利同比 %
    "ocf_match":               (0.0, 100.0, True),   # 经营现金流/净利 %
    "gross_margin":            (0.0, 50.0, True),    # 毛利率 %
    "gross_margin_trend":      (-5.0, 5.0, True),    # 毛利率变动（百分点）
    "debt_to_assets":          (20.0, 70.0, False),  # 资产负债率 %，越低越好
    "interest_debt":           (0.0, 1.0, False),    # 恒 UNKNOWN，锚点仅占位
    "goodwill_ratio":          (0.0, 0.30, False),   # 商誉/净资产，越低越好
    "recent_3q":               (-20.0, 30.0, True),  # 近 3 季净利同比均值 %
    "forecast_change":         (-50.0, 50.0, True),  # 预告净利变动中值 %
}

ITEMS = tuple(QUALITY_WEIGHTS.keys())            # 12 项，顺序稳定
TOTAL_W = float(sum(QUALITY_WEIGHTS.values()))  # = 30


# ----------------------------------------------------------------------------
# 内部工具
# ----------------------------------------------------------------------------

def _coerce(v):
    """任意标量 -> 有限 float；None/pd.NA/str/inf/NaN 一律 NaN。"""
    if v is None:
        return np.nan
    try:
        f = float(v)
    except (TypeError, ValueError):
        return np.nan
    return f if np.isfinite(f) else np.nan


def _f(row, col):
    """安全取标量 float；row 为 Series/dict/None，缺失或非法一律 NaN。"""
    if row is None:
        return np.nan
    try:
        v = row.get(col, np.nan) if hasattr(row, "get") else np.nan
    except (KeyError, TypeError, ValueError):
        return np.nan
    if isinstance(v, str) and str(v) in ("", "nan", UNKNOWN):
        return np.nan
    return _coerce(v)


def _first(pairs):
    """按优先级取第一个有限值；pairs=[(value, source_tag), ...]。

    返回 (value or NaN, source_tag or UNKNOWN)。缺失不记 0、不臆造。
    """
    for v, tag in pairs:
        f = _coerce(v)
        if np.isfinite(f):
            return f, tag
    return np.nan, UNKNOWN


def _row(frame, code):
    """从 as-of 单行表（index=ts_code）取某股一行；无则 None。"""
    if frame is None or frame.empty or code not in frame.index:
        return None
    r = frame.loc[code]
    return r.iloc[-1] if isinstance(r, pd.DataFrame) else r


def _latest(df):
    """取按 end_date_num 升序的 parquet 表最后一行；空则 None。"""
    if df is None or len(df) == 0:
        return None
    return df.iloc[-1]


def _lin(s, lo, hi, higher_better):
    """向量化线性打分 -> [0,1]；NaN 原样保留（缺失不记 0）。"""
    v = pd.to_numeric(s, errors="coerce").astype("float64")
    z = (v - lo) / (hi - lo) if higher_better else (hi - v) / (hi - lo)
    return z.clip(0.0, 1.0)          # NaN 经运算仍为 NaN


def _month_gap(a, b):
    """两个 YYYYMMDD 数值之间的月份差（b - a）；非法返回 NaN。"""
    if not (np.isfinite(a) and np.isfinite(b)):
        return np.nan
    ia, ib = int(a), int(b)
    return ((ib // 10000) * 12 + (ib // 100) % 100) - \
           ((ia // 10000) * 12 + (ia // 100) % 100)


def _yoy_from_parquet(df, col):
    """parquet 累计口径同比：最新报告期 vs 去年同月同日 (ed-10000)，返回 %。"""
    if df is None or df.empty or col not in df.columns or "end_date_num" not in df.columns:
        return np.nan
    d = df[["end_date_num", col]].copy()
    d[col] = pd.to_numeric(d[col], errors="coerce")
    d = d.dropna(subset=["end_date_num"])
    if d.empty:
        return np.nan
    cur_row = d.iloc[-1]
    ed = float(cur_row["end_date_num"])
    cur = cur_row[col]
    prev = d[d["end_date_num"] == ed - 10000.0]
    if prev.empty:
        return np.nan
    pv = prev.iloc[-1][col]
    if not (np.isfinite(cur) and np.isfinite(pv)) or pv == 0:
        return np.nan
    return (cur - pv) / abs(pv) * 100.0


def _single_quarter_cf(df, col="n_cashflow_act"):
    """累计现金流 -> [(end_date_num, 单季值 or NaN)]；Q1 为单季，其余同年内差分。

    parquet 现金流为年内累计口径（Q1 重置）。只有相邻报告期月份差在 (0,4] 内的
    差分才可信；序列起点非 Q1、跨年、跳期一律 NaN（UNKNOWN），绝不把累计数当单季。
    """
    if df is None or df.empty or col not in df.columns:
        return []
    out, prev_val, prev_num = [], np.nan, np.nan
    for _, r in df.iterrows():
        ed = _f(r, "end_date_num")
        v = _f(r, col)
        if not np.isfinite(ed):
            continue
        month = (int(ed) // 100) % 100
        if not np.isfinite(v):
            sq = np.nan
        elif month == 3:                       # Q1 / 0331 本身即单季
            sq = v
        else:
            gap = _month_gap(prev_num, ed)
            sq = (v - prev_val
                  if np.isfinite(prev_val) and np.isfinite(gap) and 0 < gap <= 4
                  else np.nan)
        out.append((ed, sq))
        prev_val, prev_num = v, ed
    return out


def _ocf_veto(cf_df, n):
    """返回 (veto:bool, unknown:bool)；最近 n 个单季全 <0 才否决，数据不足不触发。"""
    n = int(n)
    seq = _single_quarter_cf(cf_df)
    tail = seq[-n:] if len(seq) >= n else []
    if len(tail) == n and all(np.isfinite(v) for _, v in tail):
        return all(v < 0 for _, v in tail), False
    return False, True


def _ocf_match_fallback(cf_df, inc_df):
    """同报告期 经营现金流 / |归母净利| * 100（累计对累计，口径一致）。

    净利 <= 0 时比率无经济含义 -> UNKNOWN；两表无共同报告期 -> UNKNOWN。
    """
    if (cf_df is None or inc_df is None or cf_df.empty or inc_df.empty
            or "n_cashflow_act" not in cf_df.columns
            or "n_income_attr_p" not in inc_df.columns):
        return np.nan
    a = cf_df[["end_date_num", "n_cashflow_act"]].copy()
    b = inc_df[["end_date_num", "n_income_attr_p"]].copy()
    a["n_cashflow_act"] = pd.to_numeric(a["n_cashflow_act"], errors="coerce")
    b["n_income_attr_p"] = pd.to_numeric(b["n_income_attr_p"], errors="coerce")
    a = a.dropna(subset=["end_date_num"]).drop_duplicates("end_date_num", keep="last")
    b = b.dropna(subset=["end_date_num"]).drop_duplicates("end_date_num", keep="last")
    m = a.merge(b, on="end_date_num", how="inner").sort_values("end_date_num")
    if m.empty:
        return np.nan
    r = m.iloc[-1]
    ocf, ni = _coerce(r["n_cashflow_act"]), _coerce(r["n_income_attr_p"])
    if not (np.isfinite(ocf) and np.isfinite(ni)) or ni <= 0:
        return np.nan
    return ocf / ni * 100.0


def _goodwill_ratio(bal_df):
    """最近一期 goodwill / total_hldr_eqy_exc_min_int；goodwill 为 NULL 判 UNKNOWN。"""
    if (bal_df is None or bal_df.empty
            or "goodwill" not in bal_df.columns
            or "total_hldr_eqy_exc_min_int" not in bal_df.columns):
        return np.nan
    d = bal_df[["end_date_num", "goodwill", "total_hldr_eqy_exc_min_int"]].copy()
    for c in ("goodwill", "total_hldr_eqy_exc_min_int"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["goodwill", "total_hldr_eqy_exc_min_int"])
    if d.empty:
        return np.nan
    if "end_date_num" in d.columns:
        d = d.sort_values("end_date_num")
    eq = float(d.iloc[-1]["total_hldr_eqy_exc_min_int"])
    if eq <= 0:
        return np.nan
    return float(d.iloc[-1]["goodwill"]) / eq


def _recent_3q(fs):
    """fina 序列最近 3 期同比均值 -> (value, tag)；不足 3 期回退 or_yoy。"""
    if fs is None or fs.empty or "end_date_num" not in fs.columns:
        return np.nan, UNKNOWN
    for col, tag in (("netprofit_yoy", "fina_series.netprofit_yoy"),
                     ("or_yoy", "fina_series.or_yoy_proxy")):
        if col not in fs.columns:
            continue
        d = fs[["end_date_num", col]].copy()
        d[col] = pd.to_numeric(d[col], errors="coerce")
        d = (d.dropna(subset=[col]).sort_values("end_date_num")
               .drop_duplicates("end_date_num", keep="last"))
        if len(d) >= 3:
            v = _coerce(d[col].iloc[-3:].mean())
            return (v, tag) if np.isfinite(v) else (np.nan, UNKNOWN)
    return np.nan, UNKNOWN


def _gm_trend_fallback(fs):
    """fina 序列相邻两期毛利率差（百分点）；报告期不连续（>4 月）判 UNKNOWN。"""
    if fs is None or fs.empty or "grossprofit_margin" not in fs.columns:
        return np.nan
    d = fs[["end_date_num", "grossprofit_margin"]].copy()
    d["grossprofit_margin"] = pd.to_numeric(d["grossprofit_margin"], errors="coerce")
    d = (d.dropna().sort_values("end_date_num")
           .drop_duplicates("end_date_num", keep="last"))
    if len(d) < 2:
        return np.nan
    a, b = d.iloc[-2], d.iloc[-1]
    gap = _month_gap(_coerce(a["end_date_num"]), _coerce(b["end_date_num"]))
    if not (np.isfinite(gap) and 0 < gap <= 4):
        return np.nan
    return _coerce(float(b["grossprofit_margin"]) - float(a["grossprofit_margin"]))


def _forecast_mid(row):
    """预告净利变动中值；单侧缺失取单侧值；两侧都缺 -> UNKNOWN。"""
    lo, hi = _f(row, "p_change_min"), _f(row, "p_change_max")
    if np.isfinite(lo) and np.isfinite(hi):
        return (lo + hi) / 2.0
    return lo if np.isfinite(lo) else (hi if np.isfinite(hi) else np.nan)


def _is_financial(l1, name, fin_set):
    """金融股识别：优先申万 l1；l1 为 UNKNOWN 时用公司名关键词兜底（代理，需披露）。"""
    s = "" if l1 is None else str(l1)
    if s in fin_set:
        return True
    if s not in ("", "nan", UNKNOWN):
        return False
    n = "" if name is None else str(name)
    return any(k in n for k in ("银行", "证券", "保险"))


# ----------------------------------------------------------------------------
# 主计算
# ----------------------------------------------------------------------------

def compute_quality(ts_codes, scan_date, industry_map=None):
    """逐候选股计算 12 项质量原始值/子项分、加权归一 30 分、VETO 标记。

    返回 (quality_df, meta)。quality_df index=ts_code，列全部 q_ 前缀：
      q_{item}_raw / q_{item}_s      12 项原始值与 0~1 子项分（NaN = UNKNOWN）
      q_score / q_completeness       30 分制得分与加权数据完整度
      q_veto / q_veto_reasons       是否命中否决、命中原因（分号串）
      q_veto_unknown                因缺数据无法判定的否决规则（分号串）
      q_unknown_fields              缺失子项与不可判定规则（分号串，sorted unique）
      q_is_financial                是否按金融股豁免 debt_to_assets
    """
    sd = D.norm_date(scan_date)
    codes = list(dict.fromkeys(ts_codes))
    n = len(codes)

    # ---- 全部经 data 层 as-of API 载入（禁止绕过）----
    fina = D.load_fina_asof(codes, sd)
    fa = fina.set_index("ts_code") if not fina.empty else None

    fs = D.load_fina_series(codes, sd, n=6)
    fs_by_code = ({c: g for c, g in fs.groupby("ts_code")}
                  if not fs.empty else {})

    sli = D.load_sli_asof(codes, sd)
    sl = sli.set_index("ts_code") if not sli.empty else None

    fc = D.load_forecast(sd, codes)
    if not fc.empty:
        fc = (fc.sort_values(["ts_code", "end_date", "ann_date"])
                .drop_duplicates(subset=["ts_code"], keep="last")
                .set_index("ts_code"))
    else:
        fc = None

    stmts = D.load_parquet_statements(codes, sd)
    imap = industry_map if industry_map is not None else D.load_industry_map(sd)
    im = imap.set_index("ts_code") if not imap.empty else None
    name_map = D.get_name_map()
    fin_set = set(VETO["financial_l1"])

    records = []
    source_counter = {it: {} for it in ITEMS}
    n_forecast = 0
    for code in codes:
        f_row = _row(fa, code)
        s_row = _row(sl, code)
        c_row = _row(fc, code)
        i_row = _row(im, code)
        fseries = fs_by_code.get(code)
        fam = stmts.get(code) or {}
        inc = fam.get("income")
        cf = fam.get("cashflow")
        bal = fam.get("balance")
        fi = fam.get("fina_indicator")

        # 金融股识别：申万 l1 优先，UNKNOWN 时用公司名关键词兜底（代理，meta 披露）
        l1 = i_row.get("l1", UNKNOWN) if i_row is not None else UNKNOWN
        is_fin = _is_financial(l1, name_map.get(code, ""), fin_set)

        raw, tags = {}, {}

        raw["roe"], tags["roe"] = _first([
            (_f(f_row, "roe"), "fina.roe"),
            (_f(s_row, "roe"), "sli.roe"),
        ])
        raw["roic"], tags["roic"] = _first([
            (_f(s_row, "roic"), "sli.roic"),
            (_f(_latest(fi), "roic"), "parquet.fina_indicator.roic"),
        ])
        raw["revenue_growth"], tags["revenue_growth"] = _first([
            (_f(f_row, "or_yoy"), "fina.or_yoy"),
            (_f(s_row, "or_yoy"), "sli.or_yoy"),
        ])
        raw["deducted_profit_growth"], tags["deducted_profit_growth"] = _first([
            (_f(s_row, "dt_netprofit_yoy"), "sli.dt_netprofit_yoy"),
            (_yoy_from_parquet(fi, "profit_dedt"), "parquet.fina_indicator.profit_dedt_yoy"),
            (_yoy_from_parquet(inc, "n_income_attr_p"),
             "parquet.income.n_income_attr_p_yoy_proxy"),
        ])
        raw["ocf_match"], tags["ocf_match"] = _first([
            (_f(s_row, "ocf_to_profit"), "sli.ocf_to_profit"),
            (_ocf_match_fallback(cf, inc), "parquet.cashflow_income.ocf_to_profit"),
        ])
        raw["gross_margin"], tags["gross_margin"] = _first([
            (_f(f_row, "grossprofit_margin"), "fina.grossprofit_margin"),
            (_f(s_row, "grossprofit_margin"), "sli.grossprofit_margin"),
        ])
        gm, gm_prev = _f(s_row, "grossprofit_margin"), _f(s_row, "gm_prev")
        gm_delta = (gm - gm_prev
                    if np.isfinite(gm) and np.isfinite(gm_prev) else np.nan)
        raw["gross_margin_trend"], tags["gross_margin_trend"] = _first([
            (gm_delta, "sli.gm_delta"),
            (_gm_trend_fallback(fseries), "fina_series.grossprofit_margin_delta"),
        ])
        if is_fin:
            # 金融股豁免：不评分、不否决、不计入归一分母
            raw["debt_to_assets"], tags["debt_to_assets"] = np.nan, "financial_exempt"
        else:
            raw["debt_to_assets"], tags["debt_to_assets"] = _first([
                (_f(f_row, "debt_to_assets"), "fina.debt_to_assets"),
                (_f(_latest(fi), "debt_to_assets"),
                 "parquet.fina_indicator.debt_to_assets"),
            ])
        # 有息负债本地无字段：恒 UNKNOWN（不臆造、不记 0）
        raw["interest_debt"], tags["interest_debt"] = np.nan, "no_field"
        raw["goodwill_ratio"], tags["goodwill_ratio"] = _first([
            (_goodwill_ratio(bal), "parquet.balance.goodwill_ratio"),
        ])
        r3, r3_tag = _recent_3q(fseries)
        raw["recent_3q"], tags["recent_3q"] = r3, r3_tag
        raw["forecast_change"], tags["forecast_change"] = _first([
            (_forecast_mid(c_row), "forecast.p_change_mid"),
        ])

        for it in ITEMS:
            t = tags[it] or UNKNOWN
            source_counter[it][t] = source_counter[it].get(t, 0) + 1
        if c_row is not None:
            n_forecast += 1

        # ---- VETO：无数据不触发，缺口写 q_veto_unknown ----
        reasons, veto_unknown = [], []
        ftype_raw = c_row.get("type", None) if c_row is not None else None
        ftype_s = "" if ftype_raw is None else str(ftype_raw).strip()
        if ftype_s.lower() in ("", "nan", "none", UNKNOWN.lower()):
            ftype_s = ""
        if ftype_s:
            if ftype_s in VETO["forecast_loss_types"]:
                reasons.append("forecast_type")
        else:
            veto_unknown.append("forecast_type")

        pmax = _f(c_row, "p_change_max")
        if np.isfinite(pmax):
            if pmax < VETO["forecast_big_drop_pct"]:
                reasons.append("forecast_big_drop")
        else:
            veto_unknown.append("forecast_big_drop")

        ocf_v, ocf_u = _ocf_veto(cf, VETO["ocf_negative_quarters"])
        if ocf_v:
            reasons.append("ocf_negative_quarters")
        if ocf_u:
            veto_unknown.append("ocf_negative_quarters")

        rev = raw["revenue_growth"]
        if np.isfinite(rev):
            if rev < VETO["revenue_shrink_pct"]:
                reasons.append("revenue_shrink")
        else:
            veto_unknown.append("revenue_shrink")

        if not is_fin:
            d2a = raw["debt_to_assets"]
            if np.isfinite(d2a):
                if d2a > VETO["debt_to_assets"]:
                    reasons.append("debt_to_assets")
            else:
                veto_unknown.append("debt_to_assets")

        gw = raw["goodwill_ratio"]
        if np.isfinite(gw):
            if gw > VETO["goodwill_ratio"]:
                reasons.append("goodwill_ratio")
        else:
            veto_unknown.append("goodwill_ratio")

        rec = {"ts_code": code, "q_is_financial": bool(is_fin)}
        for it in ITEMS:
            rec[f"q_{it}_raw"] = raw[it]
        rec["q_veto"] = len(reasons) > 0
        rec["q_veto_reasons"] = ";".join(sorted(set(reasons)))
        rec["q_veto_unknown"] = ";".join(sorted(set(veto_unknown)))
        unk = []
        for it in ITEMS:
            if not np.isfinite(raw[it]):
                if it == "debt_to_assets" and is_fin:
                    unk.append("q:debt_to_assets(financial_exempt)")
                elif it == "interest_debt":
                    unk.append("q:interest_debt(no_field)")
                else:
                    unk.append(f"q:{it}")
        unk += [f"veto:{r}" for r in sorted(set(veto_unknown))]
        rec["q_unknown_fields"] = ";".join(sorted(set(unk)))
        records.append(rec)

    df = (pd.DataFrame(records) if records else
          pd.DataFrame(columns=["ts_code", "q_is_financial"]
                       + [f"q_{it}_raw" for it in ITEMS]
                       + ["q_veto", "q_veto_reasons", "q_veto_unknown",
                          "q_unknown_fields"]))
    df = df.set_index("ts_code")

    # ---- 向量化线性打分：raw -> s（NaN 保留，缺失不记 0）----
    w = pd.Series(QUALITY_WEIGHTS, dtype="float64")
    num = pd.Series(0.0, index=df.index)
    den = pd.Series(0.0, index=df.index)
    for it in ITEMS:
        lo, hi, hb = _SCORING_ANCHORS[it]
        s = _lin(df[f"q_{it}_raw"], lo, hi, hb)
        df[f"q_{it}_s"] = s
        num = num + s.fillna(0.0) * float(w[it])
        den = den + s.notna().astype("float64") * float(w[it])
    # 权重和 == 模块三满分 30；全部缺失(den==0) -> q_score = NaN
    df["q_score"] = TOTAL_W * num / den.where(den > 0)
    df["q_completeness"] = den / TOTAL_W

    n_rows = len(df)
    item_coverage = {it: int(df[f"q_{it}_s"].notna().sum()) for it in ITEMS}
    meta = {
        "module": "quality",
        "scan_date": sd,
        "n_codes": int(n),
        "module_full_score": TOTAL_W,          # 30（模块三满分）
        "score_cap": SCORE_CAP,                # 55（全局上限，本模块只到 30）
        "pending_modules": list(PENDING_MODULES),
        "weights": dict(QUALITY_WEIGHTS),
        "scoring_anchors": {
            it: {"lo": a[0], "hi": a[1], "higher_better": a[2]}
            for it, a in _SCORING_ANCHORS.items()
        },
        "scoring_note": (
            "绝对锚点线性打分（锚点为模块内部可调参数，非用户给定规格、非实测数据）："
            "s_i=clip((raw-lo)/(hi-lo))∈[0,1]，higher_better=False 时取 (hi-v)/(hi-lo)；"
            "q_score=30*Σ(w_i*s_i)/Σ(w_i,s_i已知)；q_completeness=Σ(w_i已知)/30；"
            "缺失子项不记 0、不臆造，按可用权重重新归一；全部缺失 q_score=NaN"
        ),
        "item_coverage": item_coverage,         # 各子项可用股数（s 非 NaN）
        "source_coverage": source_counter,      # 各子项取值来源计数（含 UNKNOWN/代理标签）
        "n_forecast": int(n_forecast),
        "n_veto": int(df["q_veto"].sum()) if n_rows else 0,
        "n_financial": int(df["q_is_financial"].sum()) if n_rows else 0,
        "n_score_unknown": int(df["q_score"].isna().sum()),
        "mean_completeness": float(df["q_completeness"].mean()) if n_rows else 0.0,
        "veto_rules": {k: (list(v) if isinstance(v, tuple) else v)
                       for k, v in VETO.items()},
        "proxies": [
            "金融股识别：申万 l1 优先；l1 为 UNKNOWN 时用公司名关键词(银行/证券/保险)兜底",
            "SLI l1_name 实测仅『银行』『非银金融』，保险/证券/多元金融并入非银金融，覆盖度照实标注",
            "deducted_profit_growth 回退链：sli.dt_netprofit_yoy → parquet profit_dedt 同比 → parquet n_income_attr_p 同比(净利代理)",
            "ocf_match 回退：sli.ocf_to_profit 缺失时用 parquet cashflow/income 同报告期 经营现金流/|归母净利|",
            "gross_margin_trend：sli (grossprofit_margin-gm_prev) → fina 序列相邻报告期毛利差(月份差<=4)",
            "recent_3q：fina 序列最近 3 期 netprofit_yoy 均值，不足 3 期回退 or_yoy",
            "forecast_change：forecast_vip p_change_min/max 中值（单侧缺失取单侧）",
            "parquet 现金流为年内累计口径：连续 N 季 OCF 为负须同年内差分得单季且相邻月差<=4，Q1 本身即单季",
        ],
        "unknown_notes": [
            "interest_debt(有息负债) 本地无字段 → 恒 UNKNOWN，按可用权重归一，不记 0",
            "parquet fina_indicator_.gross_margin 为毛利额(绝对值)非比率 → 毛利率改用 fina.grossprofit_margin / sli",
            "parquet balance.goodwill 大量为 NULL → 不可当 0，判 UNKNOWN",
            "无 ann_date 的 parquet 家族(fina_indicator_q_/yoy_)由 data 层直接判 UNKNOWN 不读取",
            "forecast_vip 本地仅覆盖 20260630 中报预告，其余报告期无预告 → forecast_change/forecast_type UNKNOWN",
            "金融股豁免 debt_to_assets 打分与否决（不计入归一分母）",
            "VETO 无数据不触发，缺口写入 q_veto_unknown / q_unknown_fields",
            "模块三仅占 30 分；模块四 25 分 + 其余 45 分/抛压衰竭/资金回流/模块五六见 PENDING_MODULES，不参与本模块评分",
        ],
        "asof_guards": [
            "fina: ann_date <= scan_date (load_fina_asof / load_fina_series)",
            "sli: eval_date <= scan_date (load_sli_asof)",
            "parquet: 文件级 has-ann_date 判定 + ann_date <= scan_date (load_parquet_statements)",
            "forecast: 文件名快照日期 <= scan_date 且 ann_date <= scan_date (load_forecast)",
            "行业映射: load_industry_map(scan_date) 时点过滤",
            "输出列统一 q_ 前缀，避免 engine 合并时覆盖模块二 data_completeness / unknown_fields",
        ],
    }
    return df, meta


def build_quality(scan_date, candidates, industry_map=None):
    """engine 主入口：从模块二候选股提取代码 → 逐股质量打分与 VETO → meta。

    candidates 可为：build_mispricing 返回的 dict（取 'candidates'）、
    DataFrame（index=ts_code 或含 ts_code 列）、或代码序列。
    返回 dict：scan_date / quality(DataFrame, index=ts_code) / meta。
    """
    if isinstance(candidates, dict):
        candidates = candidates.get("candidates", candidates.get("features"))
    if isinstance(candidates, pd.DataFrame):
        if "ts_code" in candidates.columns:
            codes = candidates["ts_code"].astype(str).tolist()
        else:
            codes = candidates.index.astype(str).tolist()
    elif candidates is None:
        codes = []
    else:
        codes = list(candidates)

    quality, meta = compute_quality(codes, scan_date, industry_map=industry_map)
    return {
        "scan_date": D.norm_date(scan_date),
        "quality": quality,
        "meta": meta,
    }
