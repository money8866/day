# -*- coding: utf-8 -*-
"""
CRR V1.0 模块一：市场暴跌识别（Market Crash Regime）

职责：
  1. 在 data.build_market_aggregate 的日度全A聚合之上，计算用户要求的窗口指标：
       全A 5/10/20日收益率、全市场下跌比例、跌停/涨停家数、成交额变化、
       市场波动率、个股收益中位数。
  2. 用「原始严重度分级 + 有记忆的状态机」把每个交易日映射到
       NORMAL / CORRECTION / SELLOFF / PANIC / SELLING_EXHAUSTION / RECOVERY。
  3. 只有 SELLOFF / PANIC / SELLING_EXHAUSTION / RECOVERY 启动重点扫描。

连续性约束（用户硬要求「不能因为单日反弹就认定暴跌结束」）：
  * 一旦触发暴跌基础信号即进入「暴跌事件段」，段内状态∈四个扫描态；
  * 段内单日反弹若未达到「修复/抛压衰竭」的连续确认天数，一律延续最近
    的 SELLOFF/PANIC 状态，不提前结束；
  * 退出暴跌段需同时满足：连续 exit_confirm_days 天无暴跌基础信号，且
    暴跌段已持续 >= min_crash_days 天。

数据边界：
  * 主市场收益 = daily_cache 全A等权 pct_chg/100 逐日累乘（非指数）；
  * 涨跌停家数 = pct_chg 板块阈值代理（data 层已按 |pct_chg|>31 剔除异常）；
  * 所有指标均为 as-of（<=当日），波动率基准只用过去窗口，绝不引入未来数据；
  * 历史不足 min_breadth_days 的交易日标 data_sufficient=False，状态不参与扫描。

对外 API：
  compute_market_features(agg)          -> 逐日窗口特征 + 原始严重度/信号
  classify_market_states(features)      -> 逐日状态机结果
  build_state_timeline(panel, ...)      -> 一次性算出全历史 timeline（供回测复用）
  detect_market_state(scan_date, ...)   -> 单日结果 dict（engine/report 主入口）
"""
import numpy as np
import pandas as pd

from . import data as D
from .config import MARKET, MARKET_STATES, SCAN_STATES, UNKNOWN

# ---- 实现常量（算法窗口，非用户可调阈值；用户阈值一律集中在 config.MARKET）----
_VOL_BASELINE_WINDOW = 120   # 波动率基准：过去120日的 vol20 均值
_VOL_BASELINE_MIN = 40       # 基准最少样本，不足则 vol_ratio=NaN（PANIC 少一信号，降置信）
_CONF_FULL_DAYS = 60.0       # 数据充足度达到 1.0 所需历史交易日

# 状态明确度（用于 engine 的置信度 state 分量；转折态低于暴跌/恐慌态）
_STATE_CONFIDENCE = {
    "PANIC": 0.95,
    "SELLOFF": 0.90,
    "SELLING_EXHAUSTION": 0.70,
    "RECOVERY": 0.72,
    "CORRECTION": 0.50,
    "NORMAL": 0.35,
}

# 用户要求的 7 类指标 + 若干派生展示项（供报告头部如实披露）
_METRIC_KEYS = (
    "ret5", "ret10", "ret20",                       # 全A 5/10/20日收益率
    "down_ratio", "down_ratio_ma5",                 # 全市场下跌比例（当日 / 近5日均值）
    "limit_down", "limit_up",                       # 跌停 / 涨停家数（代理）
    "amount", "amount_ratio20", "amount_chg5",      # 成交额 / 相对20日均额 / 近5日变化
    "volatility", "vol_ratio",                      # 20日波动率 / 相对基准倍数
    "ret_median", "ret_median_ma5",                 # 个股收益中位数（当日 / 近5日均值）
    "limit_down_peak5_prev", "n_eff",
)
_METRIC_INT_KEYS = {"limit_down", "limit_up", "n_eff", "limit_down_peak5_prev"}


def compute_market_features(agg: pd.DataFrame, cfg: dict = None) -> pd.DataFrame:
    """由日度全A聚合计算窗口指标、原始严重度分级与转折信号。

    输入：data.build_market_aggregate 的输出（index=trade_date）。
    输出：原列 + 以下列
      ret5/ret10/ret20          等权全A区间复权收益（(1+r)累乘-1，as-of）
      down_ratio_ma5/ma10       下跌比例均线（平滑单日噪音）
      up_ratio_ma5
      amount_ma20 / amount_ratio20 / amount_chg5 / amount_ma5_prev
      limit_down_peak5_prev / limit_down_ma5 / limit_up_ma5   （peak 用 shift(1) 防自证）
      volatility(vol20) / vol_ratio / vol_baseline_ok
      ret_median_ma5
      severity        0常态/1回调/2抛售/3恐慌；数据不足为 NaN
      is_recovery_signal / is_exhaust   转折信号（布尔）
      data_sufficient / confidence      数据充足度
    """
    cfg = cfg or MARKET
    f = agg.copy()
    f.index = [D.norm_date(x) for x in f.index]
    f = f.sort_index()
    n = len(f)

    r = pd.to_numeric(f["ret_mean"], errors="coerce")
    logr = np.log1p(r)
    # 区间收益：等权组合复权累乘；窗口不足 min_periods=w 返回 NaN（无未来数据）
    for w in cfg["windows"]:
        f[f"ret{w}"] = np.expm1(logr.rolling(w, min_periods=w).sum())

    dr = pd.to_numeric(f["down_ratio"], errors="coerce")
    ur = pd.to_numeric(f["up_ratio"], errors="coerce")
    f["down_ratio_ma5"] = dr.rolling(5, min_periods=3).mean()
    f["down_ratio_ma10"] = dr.rolling(10, min_periods=5).mean()
    f["up_ratio_ma5"] = ur.rolling(5, min_periods=3).mean()

    amt = pd.to_numeric(f["amount"], errors="coerce")
    f["amount_ma20"] = amt.rolling(cfg["amount_ma"], min_periods=5).mean()
    f["amount_ratio20"] = amt / f["amount_ma20"]
    f["amount_ma5_prev"] = amt.shift(1).rolling(5, min_periods=3).mean()
    f["amount_chg5"] = amt / amt.shift(5) - 1.0

    ld = pd.to_numeric(f["limit_down"], errors="coerce")
    lu = pd.to_numeric(f["limit_up"], errors="coerce")
    f["limit_down_peak5_prev"] = ld.shift(1).rolling(5, min_periods=3).max()
    f["limit_down_ma5"] = ld.rolling(5, min_periods=3).mean()
    f["limit_up_ma5"] = lu.rolling(5, min_periods=3).mean()

    vw = cfg["vol_window"]
    vol = r.rolling(vw, min_periods=vw).std()
    f["volatility"] = vol
    vol_base = vol.rolling(_VOL_BASELINE_WINDOW,
                           min_periods=_VOL_BASELINE_MIN).mean()
    f["vol_ratio"] = vol / vol_base
    f["vol_baseline_ok"] = vol_base.notna()

    rm = pd.to_numeric(f["ret_median"], errors="coerce")
    f["ret_median_ma5"] = rm.rolling(5, min_periods=3).mean()

    # ---- 数据充足度 / 置信度（历史不足的日期状态不参与扫描）----
    bars = np.arange(1, n + 1, dtype="float64")
    f["bars"] = bars
    f["data_sufficient"] = (
        (bars >= cfg["min_breadth_days"])
        & f["ret20"].notna() & f["down_ratio_ma5"].notna()
        & f["up_ratio_ma5"].notna() & (f["n_eff"] > 0)
    )
    hist_conf = np.minimum(1.0, bars / _CONF_FULL_DAYS)
    # vol_ratio 基准不足时不臆造（PANIC 少一个有效极端信号），相应降置信
    f["confidence"] = hist_conf * np.where(f["vol_baseline_ok"], 1.0, 0.85)

    # ---- 原始严重度分级（无记忆，纯当日 as-of 指标）----
    ret5, ret10, ret20 = f["ret5"], f["ret10"], f["ret20"]
    dr5 = f["down_ratio_ma5"]
    volr = f["vol_ratio"]
    ld = f["limit_down"]
    dr_day = f["down_ratio"]

    # PANIC 的三个极端信号：广度 / 跌停潮 / 波动放大；至少命中 2 个。
    # vol_baseline 不足时 vol_ratio=NaN，该项自然不计入（不编造）。
    sig_breadth = (dr_day >= cfg["th_panic_down_ratio"]).fillna(False)
    sig_limit = ((ld >= cfg["th_panic_limit_down"])
                 | (f["limit_down_ma5"] >= cfg["th_panic_limit_down"])).fillna(False)
    sig_vol = (volr >= cfg["th_panic_vol_ratio"]).fillna(False)
    extreme_cnt = (sig_breadth.astype("int64") + sig_limit.astype("int64")
                   + sig_vol.astype("int64"))

    panic = (ret5 <= cfg["th_panic_ret5"]) & (extreme_cnt >= 2)
    selloff = ((ret10 <= cfg["th_selloff_ret10"])
               | ((ret5 <= cfg["th_selloff_ret5"])
                  & (dr5 >= cfg["th_selloff_down_ratio"])))
    correction = ((ret20 <= cfg["th_correction_ret20"])
                  | (ret10 <= cfg["th_correction_ret10"]))

    sev = pd.Series(0.0, index=f.index)
    sev[correction.fillna(False)] = 1.0
    sev[selloff.fillna(False)] = 2.0
    sev[panic.fillna(False)] = 3.0
    sev[~f["data_sufficient"]] = np.nan
    f["severity"] = sev
    f["extreme_signal_count"] = extreme_cnt
    f["is_crash_base"] = (sev >= 2).fillna(False)

    # ---- 转折信号：仍处跌后，但卖压收缩 / 跌后转正 ----
    still_down = (ret10 <= 0.0) | (ret20 <= 0.0)
    f["amount_ratio5_prev"] = amt / f["amount_ma5_prev"]
    amt_shrink = f["amount_ratio5_prev"] <= cfg["th_exhaust_amount_ratio"]
    peak = f["limit_down_peak5_prev"]
    # 近5日无跌停潮时不阻碍衰竭判定；有峰值则要求跌停家数显著收缩
    limit_shrink = (peak.isna() | (peak <= 0)
                    | ((peak > 0) & (ld <= peak * cfg["th_exhaust_limit_shrink"])))
    f["is_exhaust"] = (
        still_down
        & (dr_day <= cfg["th_exhaust_down_ratio"])
        & amt_shrink & limit_shrink & f["data_sufficient"]
    ).fillna(False)

    f["is_recovery_signal"] = (
        still_down
        & (ret5 >= cfg["th_recovery_ret5"])
        & (f["up_ratio_ma5"] >= cfg["th_recovery_up_ratio"])
        & f["data_sufficient"]
    ).fillna(False)

    # 无记忆原始状态，供报告对比「状态机延续」的连续性价值
    f["raw_state"] = np.select(
        [sev >= 3, sev >= 2, sev >= 1, sev >= 0],
        ["PANIC", "SELLOFF", "CORRECTION", "NORMAL"], default=UNKNOWN)
    return f


# 事件段硬上限（算法保护：防转折信号长期为真导致段永不退出；非用户可调阈值）
_MAX_CRASH_SEGMENT_DAYS = 60


def classify_market_states(features: pd.DataFrame, cfg: dict = None) -> pd.DataFrame:
    """有记忆的状态机：把 compute_market_features 的逐日信号映射为 6 态。

    连续性硬约束（用户明确要求「不能因为单日反弹就认定暴跌结束」）：
      * severity>=2（is_crash_base）触发进入「暴跌事件段」；段内每天只能是
        SELLOFF / PANIC / SELLING_EXHAUSTION / RECOVERY 四个扫描态之一；
      * 段内单日反弹、或转折信号未达连续确认天数 → 延续最近的 SELLOFF/PANIC，
        绝不因为一天反弹就跳回 NORMAL/CORRECTION；
      * 转折态需连续确认：exhaust_confirm_days / recovery_confirm_days；
        转折确认当日重置「平静计数」，给修复窗口合理的存续期，避免刚确认
        就被退出逻辑截断；
      * 退出暴跌段需同时满足：连续 exit_confirm_days 天既无暴跌基础信号、
        也无转折确认，且段已持续 >= min_crash_days 天；
        另有 60 交易日硬上限，防转折信号长期为真导致段永不退出。

    逐日 as-of：只读取当日及之前已算出的信号列，不使用任何未来行。

    返回：features 的拷贝 + 列
      state               6 态之一；数据不足为 UNKNOWN
      in_crash_segment    是否在暴跌事件段内
      segment_id          事件段编号（段外为 0）
      segment_len         该日所在段的累计交易日数（段外 0）
      no_base_streak      用于退出判定的连续平静天数
      state_confidence    状态明确度（供 engine 置信度 state 分量）
      is_scan_state       state ∈ SCAN_STATES
    """
    cfg = cfg or MARKET
    f = features.copy().sort_index()
    n = len(f)

    sufficient = f["data_sufficient"].astype(bool).to_numpy()
    base = f["is_crash_base"].astype(bool).to_numpy() & sufficient
    sev = pd.to_numeric(f["severity"], errors="coerce").to_numpy(dtype="float64")
    exh_sig = f["is_exhaust"].astype(bool).to_numpy()
    rec_sig = f["is_recovery_signal"].astype(bool).to_numpy()
    raw = f["raw_state"].astype(str).to_numpy()

    states = np.empty(n, dtype=object)
    in_seg = np.zeros(n, dtype=bool)
    seg_ids = np.zeros(n, dtype="int64")
    seg_lens = np.zeros(n, dtype="int64")
    calm = np.zeros(n, dtype="int64")
    conf_arr = np.zeros(n, dtype="float64")

    seg_active = False
    seg_len = 0
    seg_id = 0
    last_crash = "SELLOFF"   # 段内最近的暴跌态，供单日反弹延续
    calm_run = 0             # 连续「无 base 且无转折确认」天数
    exh_run = 0
    rec_run = 0

    for i in range(n):
        if not sufficient[i]:
            # 历史不足（通常只在序列开头）：状态不可信，且不推进/破坏事件段
            states[i] = UNKNOWN
            continue

        if not seg_active:                 # 段外不保留任何段内记忆
            exh_run = rec_run = calm_run = 0

        exh_run = exh_run + 1 if exh_sig[i] else 0
        rec_run = rec_run + 1 if rec_sig[i] else 0
        turning = (rec_run >= cfg["recovery_confirm_days"]
                   or exh_run >= cfg["exhaust_confirm_days"])

        if base[i]:
            if not seg_active:
                seg_active = True
                seg_len = 1
                seg_id += 1
            else:
                seg_len += 1
            calm_run = exh_run = rec_run = 0
        elif seg_active:
            seg_len += 1
            calm_run = 0 if turning else calm_run + 1

        if seg_active:
            if base[i]:
                st = "PANIC" if sev[i] >= 3 else "SELLOFF"
                last_crash = st
            elif rec_run >= cfg["recovery_confirm_days"]:
                st = "RECOVERY"
            elif exh_run >= cfg["exhaust_confirm_days"]:
                st = "SELLING_EXHAUSTION"
            else:
                st = last_crash   # 单日反弹 / 转折未确认：延续暴跌态（硬约束）
            in_seg[i] = True
            seg_ids[i] = seg_id
            seg_lens[i] = seg_len
        else:
            st = raw[i] if raw[i] in MARKET_STATES else UNKNOWN

        states[i] = st
        calm[i] = calm_run if seg_active else 0
        conf_arr[i] = _STATE_CONFIDENCE.get(st, 0.0)

        # 退出：连续平静达标 + 段已够长；60 交易日硬上限兜底
        if seg_active and not base[i] and (
                (calm_run >= cfg["exit_confirm_days"]
                 and seg_len >= cfg["min_crash_days"])
                or seg_len > _MAX_CRASH_SEGMENT_DAYS):
            seg_active = False
            seg_len = 0
            calm_run = exh_run = rec_run = 0

    f["state"] = states
    f["in_crash_segment"] = in_seg
    f["segment_id"] = seg_ids
    f["segment_len"] = seg_lens
    f["no_base_streak"] = calm
    f["state_confidence"] = conf_arr
    f["is_scan_state"] = f["state"].isin(SCAN_STATES)
    return f


_TIMELINE_CACHE = {}


def build_state_timeline(panel, name_map: dict = None, codes=None,
                         cfg: dict = None) -> pd.DataFrame:
    """一次性计算全历史「特征 + 状态」timeline，供回测复用。

    输入 PricePanel（data.load_price_panel 输出）；name_map 缺省自动取
    data.get_name_map()。返回 index=trade_date 的 DataFrame，含
    compute_market_features 与 classify_market_states 的全部列。
    按 panel 指纹做模块级缓存，避免回测逐日重复加载/聚合；自定义 cfg 不缓存。
    """
    use_cache = cfg is None
    cfg = cfg or MARKET
    key = (tuple(panel.dates), len(panel.codes),
           None if codes is None else tuple(codes))
    if use_cache and key in _TIMELINE_CACHE:
        return _TIMELINE_CACHE[key]

    nm = name_map if name_map is not None else D.get_name_map()
    agg = D.build_market_aggregate(panel, nm, codes)
    if agg.empty:
        return pd.DataFrame()
    feat = compute_market_features(agg, cfg)
    out = classify_market_states(feat, cfg)

    if use_cache:
        if len(_TIMELINE_CACHE) >= 4:
            _TIMELINE_CACHE.pop(next(iter(_TIMELINE_CACHE)))
        _TIMELINE_CACHE[key] = out
    return out


def _as_value(x, as_int: bool = False):
    """标量 → 可 JSON 化值；NaN/None/空 → UNKNOWN（如实标注缺失，不编造）。"""
    if x is None or (isinstance(x, str) and x in ("", "nan", "None")):
        return UNKNOWN
    if isinstance(x, str):
        return x
    try:
        v = float(x)
    except (TypeError, ValueError):
        return UNKNOWN
    if not np.isfinite(v):
        return UNKNOWN
    return int(round(v)) if as_int else round(v, 6)


def _unknown_state_result(scan_date: str, reason: str) -> dict:
    """无可用 timeline / 无可用交易日时的诚实 UNKNOWN 返回。"""
    return {
        "scan_date": scan_date,
        "as_of": UNKNOWN,
        "state": UNKNOWN,
        "is_scan_state": False,
        "raw_state": UNKNOWN,
        "memory_extended": False,
        "in_crash_segment": False,
        "segment_id": 0,
        "segment_len": 0,
        "no_base_streak": 0,
        "severity": UNKNOWN,
        "data_sufficient": False,
        "confidence": 0.0,
        "state_confidence": 0.0,
        "metrics": {k: UNKNOWN for k in _METRIC_KEYS},
        "signals": {
            "is_crash_base": False,
            "is_exhaust": False,
            "is_recovery_signal": False,
            "extreme_signal_count": UNKNOWN,
        },
        "unknown_fields": list(_METRIC_KEYS),
        "notes": [reason],
    }


def detect_market_state(scan_date, panel=None, timeline=None,
                        name_map=None, cfg: dict = None) -> dict:
    """单日市场状态（engine/report 主入口）。

    返回 dict：state / is_scan_state / raw_state / memory_extended /
    segment 信息 / severity / confidence / state_confidence /
    data_sufficient / metrics（7 类窗口指标，缺失为 UNKNOWN）/
    signals / unknown_fields / notes。
    只使用 <= scan_date 的行，绝不引入未来数据。
    """
    d = D.norm_date(scan_date)
    if timeline is None:
        if panel is None:
            panel = D.load_price_panel(d)
        timeline = build_state_timeline(panel, name_map, cfg=cfg)
    if timeline is None or timeline.empty:
        return _unknown_state_result(
            d, "全市场聚合为空（daily_cache 无 <= 该日的数据）")

    hist = timeline.loc[timeline.index <= d]
    if hist.empty:
        return _unknown_state_result(d, "该日及之前无可用交易日")

    row = hist.iloc[-1]
    asof = str(hist.index[-1])

    metrics = {}
    unknown_fields = []
    for k in _METRIC_KEYS:
        v = _as_value(row.get(k), as_int=(k in _METRIC_INT_KEYS))
        metrics[k] = v
        if v == UNKNOWN:
            unknown_fields.append(k)

    state = str(row["state"])
    sufficient = bool(row["data_sufficient"])
    conf = _as_value(row.get("confidence"))
    state_conf = _as_value(row.get("state_confidence"))
    if conf == UNKNOWN:
        conf = 0.0
    if state_conf == UNKNOWN:
        state_conf = 0.0

    return {
        "scan_date": d,
        "as_of": asof,
        "state": state,
        "is_scan_state": bool(state in SCAN_STATES),
        "raw_state": str(row.get("raw_state", UNKNOWN)),
        "memory_extended": bool(
            state != row.get("raw_state")
            and state in ("SELLOFF", "PANIC")
        ),
        "in_crash_segment": bool(row["in_crash_segment"]),
        "segment_id": int(row["segment_id"]),
        "segment_len": int(row["segment_len"]),
        "no_base_streak": int(row["no_base_streak"]),
        "severity": _as_value(row.get("severity")),
        "data_sufficient": sufficient,
        "confidence": float(conf),
        "state_confidence": float(state_conf),
        "metrics": metrics,
        "signals": {
            "is_crash_base": bool(row.get("is_crash_base", False)),
            "is_exhaust": bool(row.get("is_exhaust", False)),
            "is_recovery_signal": bool(row.get("is_recovery_signal", False)),
            "extreme_signal_count": _as_value(
                row.get("extreme_signal_count"), as_int=True),
        },
        "unknown_fields": unknown_fields,
        "notes": (
            [] if sufficient else
            ["data_sufficient=False：历史预热不足或指标缺失，状态不参与扫描"]
        ),
    }