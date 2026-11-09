# -*- coding: utf-8 -*-
"""
CRR V1.0 模块四：估值错杀（Valuation）25 分 + 伪低估值识别

职责（只对候选股计算估值层面的错杀程度，占评分 25 分）：
  1. 逐股提取 6 个估值子项，按 config.VALUATION_WEIGHTS 加权；
  2. 缺失子项「不记 0、不臆造」，按可用权重重新归一：
         v_score        = 25 * Σ(w_i * s_i) / Σ(w_i, s_i 已知)
         v_completeness = Σ(w_i, s_i 已知) / 25
     其中 s_i ∈ [0,1] 为该子项打分，缺失为 NaN；全部缺失时 v_score = NaN；
  3. 识别「伪低估值」（v_pseudo_value）：PE 便宜度分位高（看起来便宜）
     但盈利预期在下修 —— 低 PE 来自盈利即将/正在下滑，是假便宜，不构成估值修复逻辑。
     标记只做提示，是否降级由 engine 依据 DECISION 决定，本模块不否决。
  4. ev_ebitda_pct 本地无字段 → 恒 UNKNOWN，按可用权重归一（分母最大 22）。

子项口径（全部经 data.py 的 as-of API，禁止绕过）：
  pe_ttm_pct        当前 pe_ttm 在最近 N 年历史中的「便宜度分位」
                    = 历史中比当前 PE 更贵的样本占比（越高越便宜）；样本不足判 UNKNOWN；
                    当前 pe_ttm > pe_cheap_cap（60）→ 该项记 0 分（判定明确，非缺失）
  pb_pct            同上，用 pb；pb > pb_cheap_cap（12）→ 记 0 分
  ev_ebitda_pct     本地无 EV/EBITDA 字段 → 恒 UNKNOWN（不臆造、不记 0）
  fair_value_gap    合理市值缺口(%) = (历史 PE 中枢 × 当前隐含 TTM 盈利 − 当前市值) / 当前市值
                    当前隐含 TTM 盈利 = total_mv / pe_ttm（口径与 PE_TTM 自洽）
  forecast_downgrade 业绩预告净利同比变动中值(%)（下修越小越好）
                    —— 本地无逐日盈利一致预期、无历史预告版本序列，无法计算真正的
                       「预期修正」，故以预告同比变动作代理，并在 meta 如实披露
  drop_vs_downgrade 股价跌幅(%) − 盈利下修幅度(百分点)
                    跌幅取模块二主窗口复权收益 ret{primary_window}（负值取反）

打分映射（如实披露，绝不冒充用户口径、绝不编造底层数据）：
  * 用户仅定义到「25 分 + 6 子项权重」，未给每个子项「多少算好」的阈值。
    本模块把每个子项线性映射为 0~1 分，锚点集中在 _SCORING_ANCHORS，
    属模块内部可调参数、非用户给定规格、非实测数据，
    meta.scoring_anchors / meta.proxies / meta.unknown_notes 逐项披露。
  * pe/pb 子项采用「历史分位（便宜度）」而非绝对 PE 阈值作为主刻度：
    盈利在周期高位时绝对 PE 低但并不便宜，分位更稳健；
    绝对上限（PE>60 / PB>12）仅作为「明确判定为不便宜」的封顶规则。

as-of 防未来：
  * 估值历史 trade_date <= scan_date（load_valuation_history）
  * 业绩预告 ann_date <= scan_date 且快照文件日期 <= scan_date（load_forecast）
  * 跌幅来自模块二，模块二已保证窗口为以 scan_date 为终点的后缀
  * 无未来行情/财务/预告数据参与

评分边界：模块四仅 25 分；SCORE_CAP=55 与 PENDING_MODULES 见 config，engine/report 读取。
列名一律加 v_ 前缀，避免 engine 合并时覆盖模块二/三的同名字段。

对外 API：
  compute_valuation(ts_codes, scan_date, ...) -> (valuation_df, meta)
  build_valuation(scan_date, candidates, ...) -> dict（engine 主入口）
"""
import numpy as np
import pandas as pd

from . import data as D
from .config import (
    VALUATION_WEIGHTS, VALUATION, MISPRICING, UNKNOWN, SCORE_CAP, PENDING_MODULES,
)


# ----------------------------------------------------------------------------
# 内部打分锚点（非用户给定规格、非实测数据；见文件头「打分映射」说明）
#   (lo, hi, higher_better)：
#     higher_better=True  -> lo 记 0 分、hi 记 1 分，线性 clip
#     higher_better=False -> lo 记 1 分、hi 记 0 分，线性 clip（值越小越好）
# ----------------------------------------------------------------------------
_SCORING_ANCHORS = {
    "pe_ttm_pct":         (0.0, 1.0, True),    # 便宜度分位，越高越便宜
    "pb_pct":             (0.0, 1.0, True),    # 便宜度分位，越高越便宜
    "ev_ebitda_pct":      (0.0, 1.0, True),    # 恒 UNKNOWN，锚点仅占位
    "fair_value_gap":     (-50.0, 50.0, True),  # 合理市值缺口 %
    "forecast_downgrade": (-50.0, 50.0, True),  # 预告净利同比变动中值 %（越高=下修越小）
    "drop_vs_downgrade":  (-30.0, 40.0, True),  # 跌幅% − 盈利下修(百分点)
}

ITEMS = tuple(VALUATION_WEIGHTS.keys())             # 6 项，顺序稳定
TOTAL_W = float(sum(VALUATION_WEIGHTS.values()))    # = 25


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


def _row(frame, code):
    """从 as-of 单行表（index=ts_code）取某股一行；无则 None。"""
    if frame is None or frame.empty or code not in frame.index:
        return None
    r = frame.loc[code]
    return r.iloc[-1] if isinstance(r, pd.DataFrame) else r


def _lin(s, lo, hi, higher_better):
    """向量化线性打分 -> [0,1]；NaN 原样保留（缺失不记 0）。"""
    v = pd.to_numeric(s, errors="coerce").astype("float64")
    z = (v - lo) / (hi - lo) if higher_better else (hi - v) / (hi - lo)
    return z.clip(0.0, 1.0)          # NaN 经运算仍为 NaN


def _positive_series(hist, col):
    """估值历史中的正有效值序列（PE/PB <= 0 无经济含义，过滤掉）。"""
    if hist is None or col not in getattr(hist, "columns", []):
        return np.array([], dtype="float64")
    v = pd.to_numeric(hist[col], errors="coerce").to_numpy(dtype="float64")
    return v[np.isfinite(v) & (v > 0)]


def _cheap_pct(hist_vals, cur):
    """当前值在历史分布中的「便宜度分位」：历史中比当前更贵的样本占比。

    当前值越低 -> 越多历史值比它贵 -> 分位越高（越便宜）。
    样本不足 min_obs 或当前值非正 -> NaN（UNKNOWN）。
    """
    n = len(hist_vals)
    if n < 1 or not np.isfinite(cur) or cur <= 0:
        return np.nan
    return float((hist_vals > cur).sum()) / float(n)


def _forecast_mid(row):
    """预告净利同比变动中值；单侧缺失取单侧值；两侧都缺 -> UNKNOWN。"""
    lo, hi = _f(row, "p_change_min"), _f(row, "p_change_max")
    if np.isfinite(lo) and np.isfinite(hi):
        return (lo + hi) / 2.0
    return lo if np.isfinite(lo) else (hi if np.isfinite(hi) else np.nan)


# ----------------------------------------------------------------------------
# 主计算
# ----------------------------------------------------------------------------

def compute_valuation(ts_codes, scan_date, ret_map=None, cfg=None):
    """逐候选股计算 6 项估值原始值/子项分、加权归一 25 分、伪低估值标记。

    参数
      ts_codes  候选股代码列表
      scan_date 扫描基准日（交易日）
      ret_map   dict[ts_code -> 主窗口复权收益（小数，负值为下跌）]，
                来自模块二 features；缺该股或未提供 -> drop_vs_downgrade 判 UNKNOWN

    返回 (valuation_df, meta)。valuation_df index=ts_code，列全部 v_ 前缀：
      v_{item}_raw / v_{item}_s   6 项原始值与 0~1 子项分（NaN = UNKNOWN）
      v_score / v_completeness    25 分制得分与加权数据完整度
      v_pseudo_value              是否伪低估值（PE 便宜 + 盈利下修）
      v_pseudo_value_reason       命中原因
      v_unknown_fields            缺失子项（分号串，sorted unique）
      v_pe_ttm / v_pb / v_total_mv / v_val_date  最新一期估值快照（展示用）
      v_pe_cheap_pct / v_pb_cheap_pct / v_hist_pe_n / v_hist_pb_n  分位与样本量
      v_pe_median_hist / v_pb_median_hist                          历史中枢
      v_fair_mv / v_forecast_mid / v_drop_pct / v_downgrade_pct      中间量
    """
    cfg = cfg or VALUATION
    sd = D.norm_date(scan_date)
    codes = list(dict.fromkeys(ts_codes))
    n = len(codes)

    years = int(cfg["percentile_years"])
    min_obs = int(cfg["percentile_min_obs"])
    pe_cap = float(cfg["pe_cheap_cap"])
    pb_cap = float(cfg["pb_cheap_cap"])
    pseudo_thr = float(cfg["pseudo_value_pe_pct"])
    pw = int(MISPRICING["primary_window"])
    ret_col = f"ret{pw}"

    # ---- 全部经 data 层 as-of API 载入（禁止绕过）----
    hist = D.load_valuation_history(codes, sd, years=years)

    fc = D.load_forecast(sd, codes)
    if not fc.empty:
        fc = (fc.sort_values(["ts_code", "end_date", "ann_date"])
                .drop_duplicates(subset=["ts_code"], keep="last")
                .set_index("ts_code"))
    else:
        fc = None

    ret_map = ret_map or {}

    records = []
    source_counter = {it: {} for it in ITEMS}
    n_forecast = 0
    for code in codes:
        h = hist.get(code)
        cur = None
        if h is not None and not h.empty:
            cur = h.iloc[-1]          # load_valuation_history 已按 trade_date 升序

        pe_now = _f(cur, "pe_ttm")
        pb_now = _f(cur, "pb")
        mv_now = _f(cur, "total_mv")
        val_date = ""
        if cur is not None and "trade_date" in getattr(cur, "index", []):
            val_date = str(cur.get("trade_date", "") or "")

        pe_hist = _positive_series(h, "pe_ttm")
        pb_hist = _positive_series(h, "pb")
        n_pe, n_pb = len(pe_hist), len(pb_hist)
        pe_ok = n_pe >= min_obs
        pb_ok = n_pb >= min_obs

        # ---- pe/pb 便宜度分位 + 历史中枢 ----
        pe_cheap = _cheap_pct(pe_hist, pe_now) if pe_ok else np.nan
        pb_cheap = _cheap_pct(pb_hist, pb_now) if pb_ok else np.nan
        pe_med = float(np.median(pe_hist)) if pe_ok else np.nan
        pb_med = float(np.median(pb_hist)) if pb_ok else np.nan

        # 绝对上限：PE/PB 过高 -> 明确判定「不算便宜」-> 记 0 分（非缺失）
        pe_cap_hit = bool(np.isfinite(pe_now) and pe_now > pe_cap)
        pb_cap_hit = bool(np.isfinite(pb_now) and pb_now > pb_cap)

        # ---- 合理市值缺口 ----
        fair_mv, gap = np.nan, np.nan
        if np.isfinite(pe_now) and pe_now > 0 and np.isfinite(mv_now) and mv_now > 0 \
                and np.isfinite(pe_med):
            ttm_profit = mv_now / pe_now          # 与 PE_TTM 自洽的隐含 TTM 盈利
            fair_mv = pe_med * ttm_profit
            gap = (fair_mv - mv_now) / mv_now * 100.0

        # ---- 盈利预告（预期变化代理）----
        c_row = _row(fc, code)
        p_mid = _forecast_mid(c_row)
        if c_row is not None:
            n_forecast += 1

        # ---- 跌幅（模块二主窗口） vs 盈利下修幅度 ----
        ret = _coerce(ret_map.get(code, np.nan))
        drop_pct = (-ret * 100.0) if np.isfinite(ret) else np.nan
        downgrade_pct = (-p_mid) if (np.isfinite(p_mid) and p_mid < 0) else (
            0.0 if np.isfinite(p_mid) else np.nan)
        dvg = (drop_pct - downgrade_pct) \
            if (np.isfinite(drop_pct) and np.isfinite(downgrade_pct)) else np.nan

        raw = {
            "pe_ttm_pct": pe_cheap,
            "pb_pct": pb_cheap,
            "ev_ebitda_pct": np.nan,          # 本地无 EV/EBITDA 字段 -> 恒 UNKNOWN
            "fair_value_gap": gap,
            "forecast_downgrade": p_mid,
            "drop_vs_downgrade": dvg,
        }
        # 来源标签需与「是否真的拿到值」一致：历史够但当前 PE<=0/缺失 也是 UNKNOWN
        if not pe_ok:
            pe_tag = "insufficient_history"
        elif not (np.isfinite(pe_now) and pe_now > 0):
            pe_tag = "current_pe_invalid(<=0_or_missing)"
        else:
            pe_tag = "daily_basic_cache.pe_ttm_percentile"
        if not pb_ok:
            pb_tag = "insufficient_history"
        elif not (np.isfinite(pb_now) and pb_now > 0):
            pb_tag = "current_pb_invalid(<=0_or_missing)"
        else:
            pb_tag = "daily_basic_cache.pb_percentile"
        if np.isfinite(gap):
            gap_tag = "daily_basic_cache.pe_median_x_implied_ttm_profit"
        elif not pe_ok:
            gap_tag = "insufficient_history"
        elif not (np.isfinite(pe_now) and pe_now > 0):
            gap_tag = "current_pe_invalid(<=0_or_missing)"
        else:
            gap_tag = "no_market_cap"
        tags = {
            "pe_ttm_pct": pe_tag,
            "pb_pct": pb_tag,
            "ev_ebitda_pct": "no_field",
            "fair_value_gap": gap_tag,
            "forecast_downgrade": ("forecast.p_change_mid"
                                   if np.isfinite(p_mid) else "no_forecast"),
            "drop_vs_downgrade": ("mispricing.ret20_minus_forecast_downgrade"
                                  if np.isfinite(dvg) else "unavailable"),
        }
        for it in ITEMS:
            t = tags[it] or UNKNOWN
            source_counter[it][t] = source_counter[it].get(t, 0) + 1

        # ---- 伪低估值：看起来便宜（PE 便宜度分位高）+ 盈利预期下修 ----
        pseudo = bool(np.isfinite(pe_cheap) and pe_cheap > pseudo_thr
                      and np.isfinite(p_mid) and p_mid < 0)
        pseudo_reason = ("pe_cheap_pct>%.2f+forecast_downgrade" % pseudo_thr) if pseudo else ""

        rec = {"ts_code": code}
        for it in ITEMS:
            rec[f"v_{it}_raw"] = raw[it]
        rec["v_pe_cap_hit"] = pe_cap_hit
        rec["v_pb_cap_hit"] = pb_cap_hit
        rec["v_pe_ttm"] = pe_now
        rec["v_pb"] = pb_now
        rec["v_total_mv"] = mv_now
        rec["v_val_date"] = val_date
        rec["v_pe_cheap_pct"] = pe_cheap
        rec["v_pb_cheap_pct"] = pb_cheap
        rec["v_hist_pe_n"] = n_pe
        rec["v_hist_pb_n"] = n_pb
        rec["v_pe_median_hist"] = pe_med
        rec["v_pb_median_hist"] = pb_med
        rec["v_fair_mv"] = fair_mv
        rec["v_forecast_mid"] = p_mid
        rec["v_drop_pct"] = drop_pct
        rec["v_downgrade_pct"] = downgrade_pct
        rec["v_pseudo_value"] = pseudo
        rec["v_pseudo_value_reason"] = pseudo_reason
        unk = [f"v:{it}" for it in ITEMS if not np.isfinite(raw[it])]
        rec["v_unknown_fields"] = ";".join(sorted(set(unk)))
        records.append(rec)

    df = (pd.DataFrame(records) if records else
          pd.DataFrame(columns=["ts_code"] + [f"v_{it}_raw" for it in ITEMS]
                       + ["v_pe_cap_hit", "v_pb_cap_hit", "v_pe_ttm", "v_pb",
                          "v_total_mv", "v_val_date", "v_pe_cheap_pct",
                          "v_pb_cheap_pct", "v_hist_pe_n", "v_hist_pb_n",
                          "v_pe_median_hist", "v_pb_median_hist", "v_fair_mv",
                          "v_forecast_mid", "v_drop_pct", "v_downgrade_pct",
                          "v_pseudo_value", "v_pseudo_value_reason",
                          "v_unknown_fields"]))
    df = df.set_index("ts_code")

    # ---- 向量化线性打分：raw -> s（NaN 保留，缺失不记 0）----
    w = pd.Series(VALUATION_WEIGHTS, dtype="float64")
    num = pd.Series(0.0, index=df.index)
    den = pd.Series(0.0, index=df.index)
    for it in ITEMS:
        lo, hi, hb = _SCORING_ANCHORS[it]
        s = _lin(df[f"v_{it}_raw"], lo, hi, hb)
        if it == "pe_ttm_pct":
            # PE 超绝对上限 -> 明确判定不便宜 -> 记 0 分（历史样本不足仍为 NaN）
            s = s.mask(df["v_pe_cap_hit"] & s.notna(), 0.0)
        elif it == "pb_pct":
            s = s.mask(df["v_pb_cap_hit"] & s.notna(), 0.0)
        df[f"v_{it}_s"] = s
        num = num + s.fillna(0.0) * float(w[it])
        den = den + s.notna().astype("float64") * float(w[it])
    # 权重和 == 模块四满分 25；ev_ebitda_pct 恒 NaN -> 实际分母最大 22
    df["v_score"] = TOTAL_W * num / den.where(den > 0)
    df["v_completeness"] = den / TOTAL_W

    n_rows = len(df)
    item_coverage = {it: int(df[f"v_{it}_s"].notna().sum()) for it in ITEMS}
    meta = {
        "module": "valuation",
        "scan_date": sd,
        "n_codes": int(n),
        "module_full_score": TOTAL_W,          # 25（模块四满分）
        "score_cap": SCORE_CAP,                # 55（全局上限，本模块只到 25）
        "pending_modules": list(PENDING_MODULES),
        "weights": dict(VALUATION_WEIGHTS),
        "scoring_anchors": {
            it: {"lo": a[0], "hi": a[1], "higher_better": a[2]}
            for it, a in _SCORING_ANCHORS.items()
        },
        "scoring_note": (
            "绝对锚点线性打分（锚点为模块内部可调参数，非用户给定规格、非实测数据）："
            "s_i=clip((raw-lo)/(hi-lo))∈[0,1]，higher_better=False 时取 (hi-v)/(hi-lo)；"
            "v_score=25*Σ(w_i*s_i)/Σ(w_i,s_i已知)；v_completeness=Σ(w_i已知)/25；"
            "缺失子项不记 0、不臆造，按可用权重重新归一；全部缺失 v_score=NaN；"
            "ev_ebitda_pct 恒 UNKNOWN -> 实际可用权重上限 22"
        ),
        "percentile_years": years,
        "percentile_min_obs": min_obs,
        "primary_window": pw,
        "item_coverage": item_coverage,         # 各子项可用股数（s 非 NaN）
        "source_coverage": source_counter,      # 各子项取值来源计数（含 UNKNOWN/代理标签）
        "n_forecast": int(n_forecast),
        "n_pe_cap_hit": int(df["v_pe_cap_hit"].sum()) if n_rows else 0,
        "n_pb_cap_hit": int(df["v_pb_cap_hit"].sum()) if n_rows else 0,
        "n_pseudo_value": int(df["v_pseudo_value"].sum()) if n_rows else 0,
        "n_score_unknown": int(df["v_score"].isna().sum()),
        "mean_completeness": float(df["v_completeness"].mean()) if n_rows else 0.0,
        "pseudo_value_rule": (
            "v_pseudo_value = (PE 便宜度分位 > %.2f) 且 (业绩预告净利同比变动中值 < 0)；"
            "含义：低 PE 来自盈利下滑，是假便宜，不构成估值修复逻辑" % pseudo_thr
        ),
        "proxies": [
            "pe/pb 便宜度分位：daily_basic_cache 近 %d 年日频序列，当前值在正值样本中的『历史更贵占比』" % years,
            "历史中枢取正值 PE/PB 的中位数（抗极端值），当前隐含 TTM 盈利 = total_mv / pe_ttm（与 PE_TTM 自洽）",
            "绝对上限封顶：PE_TTM > %.0f 或 PB > %.1f 时该项记 0 分（判定明确，非缺失），计入归一分母" % (pe_cap, pb_cap),
            "forecast_downgrade：forecast_vip p_change_min/max 中值（单侧缺失取单侧）作为『盈利预期变化』代理",
            "drop_vs_downgrade：跌幅取模块二 ret%d（复权累乘口径，负值取反），盈利下修幅度 = max(0, -预告同比变动)" % pw,
            "伪低估值判据：PE 便宜度分位高 + 预告盈利下修",
        ],
        "unknown_notes": [
            "EV/EBITDA 本地无字段（无 EV、无息负债）→ ev_ebitda_pct 恒 UNKNOWN，按可用权重归一，不记 0",
            "本地无逐日盈利一致预期，也无历史预告版本序列（load_forecast 已按 (ts_code,end_date) 取最新公告去重），"
            "无法计算真正的『盈利预期修正幅度』→ forecast_downgrade 以预告同比变动作代理，已在 proxies 披露",
            "forecast_vip 本地仅覆盖 20260630 中报预告，其余报告期无预告 → forecast_downgrade / drop_vs_downgrade 多为 UNKNOWN",
            "PE/PB 历史样本不足 %d 个交易日（上市时间短）→ 便宜度分位判 UNKNOWN，不以短样本近似" % min_obs,
            "PE_TTM <= 0（亏损）→ PE 便宜度分位与合理市值缺口均判 UNKNOWN；PB 分位仍可用",
            "fair_value_gap 与 pe_ttm_pct 同源于 PE 历史分布，存在口径重叠，已在 proxies 披露",
            "合理市值用『历史 PE 中枢 × 当前 TTM 盈利』近似，未做 DCF/可比公司/盈利正常化，属简化口径",
            "若盈利预期下修，历史 PE 中枢会高估合理市值 —— 该风险由 v_pseudo_value 标记提示，"
            "是否降级由 engine 依据 DECISION 决定，本模块不否决",
            "模块四仅占 25 分；其余 45 分/抛压衰竭/资金回流/模块五六见 PENDING_MODULES，不参与本模块评分",
        ],
        "asof_guards": [
            "估值历史: trade_date <= scan_date (load_valuation_history)",
            "业绩预告: 快照文件日期 <= scan_date 且 ann_date <= scan_date (load_forecast)",
            "跌幅来自模块二 ret%d：窗口为以 scan_date 为终点的后缀，无未来行情" % pw,
            "输出列统一 v_ 前缀，避免 engine 合并时覆盖模块二 data_completeness / unknown_fields",
        ],
    }
    return df, meta


def build_valuation(scan_date, candidates=None, features=None, cfg=None):
    """engine 主入口：从模块二候选股提取代码与跌幅 → 逐股估值打分 → meta。

    candidates 可为：build_mispricing 返回的 dict（取 'candidates'）、
    DataFrame（index=ts_code 或含 ts_code 列）、或代码序列。
    features 可选：模块二 features（DataFrame），用于取主窗口跌幅；
    若 candidates 本身是 features 的子集（含 ret{pw} 列）也可直接取到。
    返回 dict：scan_date / valuation(DataFrame, index=ts_code) / meta。
    """
    ret_col = f"ret{int(MISPRICING['primary_window'])}"
    ret_map = {}

    def _collect_ret(df):
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            return
        if ret_col not in df.columns:
            return
        idx = df.index.astype(str)
        for code, v in zip(idx, df[ret_col].tolist()):
            f = _coerce(v)
            if np.isfinite(f):
                ret_map[code] = f

    if isinstance(candidates, dict):
        feats = candidates.get("features")
        cand = candidates.get("candidates", feats)
        if feats is not None:
            _collect_ret(feats)
        if isinstance(cand, pd.DataFrame):
            _collect_ret(cand)
    elif isinstance(candidates, pd.DataFrame):
        _collect_ret(candidates)

    if features is not None:
        _collect_ret(features)

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

    valuation, meta = compute_valuation(codes, scan_date, ret_map=ret_map, cfg=cfg)
    return {
        "scan_date": D.norm_date(scan_date),
        "valuation": valuation,
        "meta": meta,
    }
