# -*- coding: utf-8 -*-
"""
CRR V1.0 模块二：错杀程度（Mispricing / Return Attribution）

职责（纯特征工程 + 候选初筛，不占评分）：
  1. 多窗口复权收益 ret5 / ret10 / ret20 / ret60（主窗口 primary_window=20）。
  2. 双因子收益归因：把主窗口下跌分解为「系统性 / 行业性 / 个股特异」三块，
     并计算解释份额 sys_share / ind_share / specific_share。
  3. 残差 z-score：用过去一年日残差波动，衡量主窗口累计残差（错杀）的统计显著性。
  4. 按 min_drop 初筛候选；specific_share / data_completeness 供 engine 门控使用
     （DECISION.buy_max_specific_share / min_data_completeness 不在本模块判断）。

模型（一阶双因子，不臆造复杂度）：
  M_t    = 全A等权日收益（data.build_market_aggregate 的 ret_mean，与模块一同源）
  I_t    = 该股所属行业日收益（申万分类自建等权中位数，见 data.build_industry_returns）
  Iex_t  = I_t - M_t                     行业相对全市场的超额收益（第二因子）
  r_i,t  = alpha + beta_m*M_t + beta_i*Iex_t + eps_t      （beta_window=120 估计）

  主窗口 W=primary_window 的日简单收益一阶分解（在同一批有效交易日内严格可加）：
    Sys  = beta_m * Σ M_t
    Ind  = beta_i * Σ Iex_t
    Spec = Σ r_i,t - Sys - Ind           （含 alpha 与残差，即个股特异抛售）
    Σ r_i,t = Sys + Ind + Spec
  解释份额用绝对贡献归一：share_x = |contrib_x| / (|Sys| + |Ind| + |Spec|)。

  说明：Iex = I-M 是「行业超额」而非严格 Gram-Schmidt 正交化；M 与 Iex 可能残留
  相关性，但对一阶贡献分解足够，且避免为每股额外做一次正交回归。行业因子缺失
  （无归属或成分 < min_industry_members）时回退单因子 r = alpha + beta_m*M + eps，
  此时 Ind=0、ind_share=0、beta_i=UNKNOWN，完整性降级，绝不编造行业因子。

as-of 防未来：
  * panel 一律截断到 trade_date <= scan_date（含外部传入的复用面板）；
  * 所有窗口都是「以 scan_date 为终点向回看」的后缀，前缀截断不影响更早结果；
  * 行业映射经 data.load_industry_map(scan_date)，SLI eval_date / sw_member in_date
    均已做时点过滤；
  * 无未来财务/行情数据参与。

已知口径差异（如实披露，不静默掩盖）：
  * ret{w} 用 (1 + pct_chg/100) 逐日累乘（停牌日按 0 收益跳过）；归因分解用日
    简单收益一阶加总。二者量级接近但口径不同，不可直接相减等同。
  * Beta/alpha 用 scan_date 前 beta_window 交易日估计，残差日波动用同一组参数在
    过去 vol_window 交易日计算——属于「用当前模型参数标准化历史」，未做逐日滚动
    Beta；不含 scan_date 之后数据，但残差波动窗口早于 Beta 估计窗口的部分属样本外
    标准化，report 应照实标注。

评分边界：模块二不打分。SCORE_CAP=55 与 PENDING_MODULES 见 config，engine/report 读取。

对外 API：
  compute_mispricing_panel(scan_date, panel, ...) -> (features, meta)
  select_candidates(features, cfg=None)          -> DataFrame
  build_mispricing(scan_date, ...)               -> dict（engine 主入口）
"""
import numpy as np
import pandas as pd

from . import data as D
from .config import MISPRICING, UNKNOWN


# ----------------------------------------------------------------------------
# 内部工具
# ----------------------------------------------------------------------------

def _asof_panel(panel, scan_date, lookback):
    """把（可能被复用的）价格面板截断到 <= scan_date 的最近 lookback 个交易日。

    外部传入的 panel 未必以 scan_date 为终点，必须显式截断，否则窗口后缀会读到
    scan_date 之后的行情，违反 as-of 硬约束。
    """
    sd = D.norm_date(scan_date)
    keep = [d for d in panel.dates if d <= sd][-int(lookback):]
    if not keep:
        raise ValueError(f"价格面板无 <= {sd} 的交易日")
    return D.PricePanel(
        dates=keep,
        codes=panel.codes,
        ret=panel.ret.loc[keep],
        pct_chg=panel.pct_chg.loc[keep],
        close=panel.close.loc[keep],
        amount=panel.amount.loc[keep],
        vol=panel.vol.loc[keep],
    )


def _window_return(seg):
    """seg: (w, N) 日收益矩阵 -> (N,) 窗口复权收益 (1+r) 累乘 - 1。

    停牌/无记录的 NaN 日按 0 收益跳过（等价于 (1+r)=1）；整列无数据返回 NaN。
    pct_chg/100 的下界远大于 -1，无需对 1+r 做额外裁剪。
    """
    finite = np.isfinite(seg)
    cnt = finite.sum(axis=0)
    prod = np.nanprod(np.where(finite, 1.0 + seg, 1.0), axis=0)
    return np.where(cnt > 0, prod - 1.0, np.nan)


def _share(a, b, c):
    """三项绝对贡献归一 -> (sa, sb, sc, ok)。分母为 0 时全部 NaN 且 ok=False。"""
    aa, bb, cc = np.abs(a), np.abs(b), np.abs(c)
    denom = aa + bb + cc
    ok = np.isfinite(denom) & (denom > 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        sa = np.where(ok, aa / denom, np.nan)
        sb = np.where(ok, bb / denom, np.nan)
        sc = np.where(ok, cc / denom, np.nan)
    return sa, sb, sc, ok


# ----------------------------------------------------------------------------
# 主计算
# ----------------------------------------------------------------------------

def compute_mispricing_panel(scan_date, panel, name_map=None,
                             industry_map=None, cfg=None):
    """全市场逐股错杀特征。返回 (features, meta)。

    features: DataFrame，index=ts_code；含多窗口收益、beta、归因份额、残差 z-score、
              data_completeness、unknown_fields、drop_ok 等（详见文件头字段清单）。
    meta:     dict，本模块特有的口径/代理/缺口说明与统计，供 report 展示。
    """
    cfg = cfg or MISPRICING
    windows = tuple(cfg["windows"])
    pw = int(cfg["primary_window"])
    bw = int(cfg["beta_window"])
    vw = int(cfg["vol_window"])
    min_bars = int(cfg["min_beta_bars"])
    z_clip = float(cfg["z_clip"])

    max_look = max(vw, bw, max(windows))
    lookback = max_look + 10
    p = _asof_panel(panel, scan_date, lookback)
    sd = D.norm_date(scan_date)

    codes = list(p.ret.columns)
    n = len(codes)
    R = p.ret.to_numpy(dtype="float64")          # (T, N) 个股日收益
    T = R.shape[0]

    # ---- 市场因子：全A等权日收益（与模块一同源）----
    if name_map is None:
        name_map = D.get_name_map()
    agg = D.build_market_aggregate(p, name_map)
    M = agg["ret_mean"].reindex(p.ret.index).to_numpy(dtype="float64")   # (T,)

    # ---- 行业因子：as-of 映射 -> 自建行业收益 -> 行业超额 Iex = I - M ----
    if industry_map is None:
        industry_map = D.load_industry_map(sd)
    label_by_code = D.resolve_industry_labels(industry_map, level="l2")
    _, stock_ind_ret = D.build_industry_returns(p, label_by_code)
    IEX = stock_ind_ret.sub(
        pd.Series(M, index=p.ret.index), axis=0
    ).reindex(index=p.ret.index, columns=p.ret.columns).to_numpy(dtype="float64")  # (T, N)

    # 展示用行业名 / 来源（map 可能缺失部分代码 -> UNKNOWN）
    im = industry_map.set_index("ts_code") if not industry_map.empty else None

    def _industry_of(c):
        if im is None or c not in im.index:
            return UNKNOWN
        row = im.loc[c]
        for lv in ("l2", "l1"):
            v = row.get(lv) if hasattr(row, "get") else None
            if v is not None and str(v) not in ("", "nan", UNKNOWN):
                return str(v)
        return UNKNOWN

    def _source_of(c):
        if im is None or c not in im.index or "source" not in im.columns:
            return UNKNOWN
        v = im.loc[c, "source"]
        return UNKNOWN if v is None or str(v) in ("", "nan") else str(v)

    # ---- 多窗口收益 ----
    ret_by_w = {}
    ret_bars = {}
    for w in windows:
        seg = R[-int(w):, :] if T >= w else R
        cnt = np.isfinite(seg).sum(axis=0)
        ret_by_w[w] = _window_return(seg)
        ret_bars[w] = cnt
    mkt_ret = {w: _window_return(M[-int(w):].reshape(-1, 1))[0]
               for w in windows if T >= w}

    # ---- 逐股 OLS + 归因 + 残差 z ----
    out = {k: np.full(n, np.nan) for k in (
        "beta_m", "beta_i", "alpha", "r2", "beta_bars",
        "sys_contrib", "ind_contrib", "spec_contrib", "total_contrib",
        "sys_share", "ind_share", "specific_share",
        "resid_sigma", "resid_vol_bars", "resid_z",
    )}
    model = np.array([UNKNOWN] * n, dtype=object)
    attribution_ok = np.zeros(n, dtype=bool)
    resid_ok = np.zeros(n, dtype=bool)
    beta_ok = np.zeros(n, dtype=bool)
    unknown_fields = [[] for _ in range(n)]

    bseg = slice(max(0, T - bw), T)      # beta 估计窗口
    wseg = slice(max(0, T - pw), T)      # 主归因窗口
    vseg = slice(max(0, T - vw), T)      # 残差波动窗口

    mb = M[bseg]
    for j in range(n):
        yb = R[bseg, j]
        xb = IEX[bseg, j]
        mask = np.isfinite(yb) & np.isfinite(mb)
        # 行业超额在 beta 窗口是否有足够有效样本：决定双因子还是单因子
        two = int((np.isfinite(xb) & mask).sum()) >= min_bars
        if two:
            mask = mask & np.isfinite(xb)
        nbar = int(mask.sum())
        out["beta_bars"][j] = nbar
        if nbar < min_bars:
            model[j] = UNKNOWN
            unknown_fields[j].append("beta")
            continue

        if two:
            X = np.column_stack([np.ones(nbar), mb[mask], xb[mask]])
        else:
            X = np.column_stack([np.ones(nbar), mb[mask]])
        yv = yb[mask]
        try:
            beta, _, _, _ = np.linalg.lstsq(X, yv, rcond=None)
        except np.linalg.LinAlgError:
            model[j] = UNKNOWN
            unknown_fields[j].append("beta")
            continue
        beta_ok[j] = True
        model[j] = "two_factor" if two else "single_factor"
        alpha, bm = float(beta[0]), float(beta[1])
        bi = float(beta[2]) if two else np.nan
        out["alpha"][j], out["beta_m"][j], out["beta_i"][j] = alpha, bm, bi
        if not two:
            unknown_fields[j].append("industry_factor")

        pred = X @ beta
        ss_res = float(((yv - pred) ** 2).sum())
        ss_tot = float(((yv - yv.mean()) ** 2).sum())
        out["r2"][j] = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan

        # ---- 主窗口一阶归因（同一批有效日，严格可加）----
        yw = R[wseg, j]
        mw = M[wseg]
        xw = IEX[wseg, j]
        wm = np.isfinite(yw) & np.isfinite(mw)
        if two:
            wm = wm & np.isfinite(xw)
        if wm.any():
            sm = float(mw[wm].sum())
            sx = float(xw[wm].sum()) if two else 0.0
            sy = float(yw[wm].sum())
            sysc = bm * sm
            indc = bi * sx if two else 0.0
            spec = sy - sysc - indc
            out["sys_contrib"][j] = sysc
            out["ind_contrib"][j] = indc
            out["spec_contrib"][j] = spec
            out["total_contrib"][j] = sy
            sa, sb, sc, ok = _share(sysc, indc, spec)
            out["sys_share"][j], out["ind_share"][j], out["specific_share"][j] = sa, sb, sc
            attribution_ok[j] = bool(ok)
            if not ok:
                unknown_fields[j].append("attribution")
        else:
            unknown_fields[j].append("attribution")

        # ---- 残差波动（过去一年）+ 主窗口累计残差 z ----
        yvv = R[vseg, j]
        mvv = M[vseg]
        xvv = IEX[vseg, j]
        fitted = alpha + bm * mvv + (bi * xvv if two else 0.0)
        eps = yvv - fitted
        ev = eps[np.isfinite(eps) & np.isfinite(yvv)]
        if len(ev) >= min_bars:
            sigma = float(ev.std(ddof=1))
            out["resid_sigma"][j] = sigma
            out["resid_vol_bars"][j] = len(ev)
            if sigma > 0 and wm.any():
                ew = yw - (alpha + bm * mw + (bi * xw if two else 0.0))
                cum_eps = float(ew[wm].sum())
                weff = int(wm.sum())
                z = cum_eps / (sigma * np.sqrt(weff))
                out["resid_z"][j] = float(np.clip(z, -z_clip, z_clip))
                resid_ok[j] = True
            else:
                unknown_fields[j].append("resid_z")
        else:
            unknown_fields[j].append("resid_sigma")

    # ---- 组装 features ----
    f = pd.DataFrame(index=pd.Index(codes, name="ts_code"))
    f["name"] = [name_map.get(c, UNKNOWN) for c in codes]
    f["industry"] = [_industry_of(c) for c in codes]
    f["industry_source"] = [_source_of(c) for c in codes]
    for w in windows:
        f[f"ret{w}"] = ret_by_w[w]
        f[f"ret{w}_bars"] = ret_bars[w]
    f[f"mkt_ret{pw}"] = mkt_ret.get(pw, np.nan)

    for k in ("beta_m", "beta_i", "alpha", "r2", "beta_bars",
              "sys_contrib", "ind_contrib", "spec_contrib", "total_contrib",
              "sys_share", "ind_share", "specific_share",
              "resid_sigma", "resid_vol_bars", "resid_z"):
        f[k] = out[k]
    f["systematic_share"] = f["sys_share"] + f["ind_share"]   # 非个股特异部分
    f["model"] = model
    f["beta_ok"] = beta_ok
    f["attribution_ok"] = attribution_ok
    f["resid_ok"] = resid_ok

    # dominant 标记：份额无效时填 UNKNOWN，不臆造「False = 不主导」
    def _dominant(col, thr):
        s = f[col]
        return [UNKNOWN if not np.isfinite(v) else bool(v >= thr) for v in s]

    f["sys_dominant"] = _dominant("sys_share", cfg["sys_dominant"])
    f["ind_dominant"] = _dominant("ind_share", cfg["ind_dominant"])
    f["specific_dominant"] = _dominant("specific_share", cfg["specific_dominant"])

    # 跌幅初筛标记（engine 的 BUY 门控另有 buy_max_specific_share / 完整性要求）
    prim = f[f"ret{pw}"]
    f["drop_ok"] = (prim <= -float(cfg["min_drop"])) & prim.notna()

    # ---- data_completeness：透明的信息项均值（不做隐式加权）----
    ret_quality = np.mean(
        [np.clip(ret_bars[w] / float(w), 0.0, 1.0) for w in windows], axis=0
    )
    industry_quality = np.where(model == "two_factor", 1.0,
                         np.where(model == "single_factor", 0.5, 0.0))
    completeness = np.mean(np.vstack([
        ret_quality,
        beta_ok.astype(float),
        np.where(model == "two_factor", 1.0, np.where(model == "single_factor", 0.5, 0.0)),
        attribution_ok.astype(float),
        resid_ok.astype(float),
    ]), axis=0)
    f["data_completeness"] = completeness
    # 窗口数据不足 / 行业缺失等如实写入逐股 unknown_fields
    for w in windows:
        short = ret_bars[w] < w
        for j in np.flatnonzero(short):
            unknown_fields[j].append(f"ret{w}_bars")
    f["unknown_fields"] = [
        ";".join(sorted(set(u))) if u else "" for u in unknown_fields
    ]
    # industry_quality 已并入 completeness，此处仅保留避免未使用告警的显式引用
    _ = industry_quality

    meta = {
        "scan_date": sd,
        "panel_start": p.dates[0],
        "panel_end": p.dates[-1],
        "panel_bars": T,
        "lookback": lookback,
        "windows": windows,
        "primary_window": pw,
        "beta_window": bw,
        "vol_window": vw,
        "min_beta_bars": min_bars,
        "n_total": n,
        "n_two_factor": int((model == "two_factor").sum()),
        "n_single_factor": int((model == "single_factor").sum()),
        "n_unknown_beta": int((model == UNKNOWN).sum()),
        "market_ret_windows": {str(w): float(v) for w, v in mkt_ret.items()},
        "model_note": (
            "r = alpha + beta_m*M + beta_i*(I-M) + eps；主窗口日简单收益一阶分解 "
            "Σr = Sys + Ind + Spec；份额按 |贡献| 归一"
        ),
        "proxies": [
            "market_factor = data.build_market_aggregate 全A等权日收益(ret_mean)，与模块一同源",
            "industry_factor = SLI/sw_member 申万分类自建行业收益中位数，非官方行业指数（本地无全量 sw_daily）",
            "industry_excess = 行业收益 - 全A收益（一阶双因子，未做严格 Gram-Schmidt 正交化）",
        ],
        "unknown_notes": [
            "Beta/alpha 用 scan_date 前 beta_window 交易日估计；残差日波动用同一组参数在过去 vol_window 交易日计算——"
            "属于『用当前模型参数标准化历史』，未做逐日滚动 Beta；不含 scan_date 之后数据，"
            "但残差窗口早于 Beta 估计窗口的部分属样本外标准化，report 应照实标注",
            "ret{w} 为 (1+pct_chg/100) 逐日累乘（停牌日按 0 收益跳过）；归因分解为日简单收益一阶加总，"
            "两者口径不同，不可直接相减等同",
            "无行业归属或行业成分 < min_industry_members → 单因子回退：ind_share=0、beta_i=UNKNOWN、完整性降级",
            "份额分母为 0 / 残差样本不足 / beta 样本不足 → 相应字段 NaN 或 UNKNOWN，不编造",
            "模块二不占评分；抛压衰竭、资金回流/趋势修复、模块五/六未定义，见 config.PENDING_MODULES",
        ],
        "asof_guards": [
            "panel 截断到 trade_date <= scan_date（含外部复用面板）",
            "行业映射经 load_industry_map(scan_date)：SLI eval_date / sw_member in_date-out_date 时点过滤",
            "全部窗口为以 scan_date 为终点的后缀，无未来财务/行情/行业数据",
        ],
    }
    return f, meta


def select_candidates(features, cfg=None, universe=None):
    """按主窗口跌幅 + 归因主导性 + 股票池筛选候选。

    三道门槛：
      1) 跌幅门槛：ret{primary_window} <= -min_drop（跌幅已知）；
      2) 归因门槛：sys_dominant 或 ind_dominant 为真 —— 即跌幅主要由系统性
         或行业性因素贡献（CRR 的核心前提：「因系统性/恐慌抛售导致过度下跌」）。
         个股特异主导的下跌多由公司自身利空造成，不属于错杀，不进候选；
         归因不可判（UNKNOWN）时同样不进候选（不臆造）；
      3) 股票池：universe 给定时只保留池内代码（默认由 build_mispricing 传入
         data.get_stock_pool()：沪深主板/创业板/科创板，排除北交所、退市、ST）。

    剩余门控（specific_share 上限、data_completeness 下限）由 engine 依据
    DECISION 决定，本模块不提前剔除，也不强制每日都有候选。
    """
    cfg = cfg or MISPRICING
    pw = int(cfg["primary_window"])
    col = f"ret{pw}"
    if col not in features.columns:
        return features.iloc[0:0].copy()
    ok = (features[col] <= -float(cfg["min_drop"])) & features[col].notna()
    out = features[ok].copy()
    if out.empty:
        return out
    if universe is not None:
        keep = out.index.astype(str).isin(set(universe))
        out = out[keep]
        if out.empty:
            return out
    dom = []
    for c in ("sys_dominant", "ind_dominant"):
        if c in out.columns:
            dom.append(out[c].astype(str).str.lower().eq("true"))
    if dom:
        sys_ok = dom[0]
        ind_ok = dom[1] if len(dom) > 1 else sys_ok
        out = out[(sys_ok | ind_ok)]
    return out


def build_mispricing(scan_date, panel=None, name_map=None,
                     industry_map=None, cfg=None):
    """engine 主入口：加载（或复用）面板 -> 逐股特征 -> 候选初筛 -> meta。

    返回 dict：scan_date / features(全市场) / candidates / meta。
    """
    cfg = cfg or MISPRICING
    windows = tuple(cfg["windows"])
    lookback = max(int(cfg["vol_window"]), int(cfg["beta_window"]),
                   max(windows)) + 10
    if panel is None:
        panel = D.load_price_panel(D.norm_date(scan_date), lookback=lookback)

    features, meta = compute_mispricing_panel(
        scan_date, panel, name_map=name_map, industry_map=industry_map, cfg=cfg
    )
    universe = D.get_stock_pool()["ts_code"].astype(str).tolist()
    candidates = select_candidates(features, cfg=cfg, universe=universe)
    meta["n_candidates"] = int(len(candidates))
    meta["n_drop_pass"] = int(
        (features[f"ret{int(cfg['primary_window'])}"]
         <= -float(cfg["min_drop"])).sum())
    meta["universe"] = "data.get_stock_pool()：沪深主板/创业板/科创板，排除北交所、退市、ST"
    meta["candidate_filters"] = (
        f"跌幅 ret{int(cfg['primary_window'])}<=-{cfg['min_drop']} "
        "+ 归因主导(系统或行业) + 股票池"
    )
    return {
        "scan_date": D.norm_date(scan_date),
        "features": features,
        "candidates": candidates,
        "meta": meta,
    }
