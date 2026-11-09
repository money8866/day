# -*- coding: utf-8 -*-
"""
CRR V1.0 决策引擎（Engine）

职责：把四个模块串成一条流水线，产出每只候选股的最终结论
      BUY / WATCH / NO TRADE（用户明确要求：只输出这三种结论，不强制每日交易）。

流水线：
  模块一 market      市场暴跌识别与状态（不占分）→ 门控：非扫描态直接全部 NO TRADE
  模块二 mispricing  错杀特征与候选初筛（不占分）→ 提供归因份额与数据完整度
  模块三 quality     基本面质量 30 分 + VETO 否决
  模块四 valuation   估值错杀 25 分 + 伪低估值识别
  engine            合并打分 → 置信度 → 结论

打分与门控（阈值全部来自 config.DECISION / config.CONFIDENCE / config.SCORE_CAP）：
  total_score  = q_score + v_score                （满分 55 = SCORE_CAP）
  score_ratio  = total_score / SCORE_CAP
  completeness = (q_completeness*30 + v_completeness*25) / 55
                 —— 55 分权重中有多少是被真实数据支撑的
  coverage     = mean(模块二 data_completeness, q_completeness, v_completeness)
                 —— 三模块证据广度的等权均值
  confidence   = 0.50*completeness + 0.20*state_confidence + 0.30*coverage

  BUY   需同时满足：score_ratio>=0.68、confidence>=0.55、
                     completeness>=min_data_completeness、非 VETO、
                     非伪低估值、specific_share<=buy_max_specific_share
  WATCH 需同时满足：score_ratio>=0.45、confidence>=0.34、
                     completeness>=min_data_completeness、非 VETO
  其余一律 NO TRADE

硬约束（不因工程便利而放松）：
  * 市场状态非扫描态（SELLOFF/PANIC/SELLING_EXHAUSTION/RECOVERY）→ 不启动深度
    模块，全市场 NO TRADE，结论为「非暴跌窗口」；这是模块一的门控职责。
  * 任一模块数据缺失 → 该项不记 0、不臆造，按可用权重归一（各模块内部已处理），
    engine 不做二次填补。
  * 结论只有三种，不输出「建议建仓比例」「目标价」等用户未定义的内容。
  * 未实现模块（抛压衰竭量化、资金回流与趋势修复、剩余 45 分、模块五/六）
    见 config.PENDING_MODULES，engine 不代其打分、不以任何方式折算进总分。

对外 API：
  run_scan(scan_date, ...)  -> dict（results DataFrame + 结论分组 + meta）
"""
import numpy as np
import pandas as pd

from . import data as D
from . import market as MKT
from . import mispricing as MP
from . import quality as Q
from . import valuation as V
from .config import (
    DECISION, CONFIDENCE, SCORE_CAP, PENDING_MODULES, UNKNOWN, SCAN_STATES,
)

BUY = "BUY"
WATCH = "WATCH"
NO_TRADE = "NO TRADE"

_ACTION_CN = {BUY: "买入", WATCH: "观察", NO_TRADE: "不交易"}


# ----------------------------------------------------------------------------
# 内部工具
# ----------------------------------------------------------------------------

def _f(v):
    """任意标量 -> 有限 float；非法一律 NaN。"""
    if v is None:
        return np.nan
    try:
        x = float(v)
    except (TypeError, ValueError):
        return np.nan
    return x if np.isfinite(x) else np.nan


def _num(x, nd=2):
    """数值 -> 字符串；NaN -> UNKNOWN。"""
    v = _f(x)
    return UNKNOWN if not np.isfinite(v) else f"{v:.{nd}f}"


def _series(df, col, default=0.0):
    """取列并转 float64；列缺失时返回全 default 的 Series（不做臆造填补）。"""
    if col not in df.columns:
        return pd.Series(default, index=df.index, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce").fillna(default)


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------

def run_scan(scan_date, panel=None, name_map=None, industry_map=None,
             top_n=None, cfg_market=None, cfg_mispricing=None,
             cfg_valuation=None):
    """跑一次完整扫描。返回 dict。

    返回结构：
      scan_date / market / results(DataFrame) / buy / watch / no_trade /
      summary / meta
    """
    sd = D.norm_date(scan_date)
    top_n = int(top_n or DECISION["top_n"])

    # ---- 模块一：市场状态（门控）----
    if panel is None:
        panel = D.load_price_panel(sd, lookback=320)
    if name_map is None:
        name_map = D.get_name_map()
    if industry_map is None:
        industry_map = D.load_industry_map(sd)

    timeline = MKT.build_state_timeline(panel, name_map, cfg=cfg_market)
    mkt = MKT.detect_market_state(sd, panel=panel, timeline=timeline,
                                  name_map=name_map, cfg=cfg_market)
    is_scan = bool(mkt.get("is_scan_state"))
    state = str(mkt.get("state", UNKNOWN))
    state_conf = _f(mkt.get("state_confidence"))

    meta = {
        "module": "engine",
        "scan_date": sd,
        "market_state": state,
        "is_scan_state": is_scan,
        "scan_states": list(SCAN_STATES),
        "score_cap": SCORE_CAP,
        "pending_modules": list(PENDING_MODULES),
        "decision_rules": dict(DECISION),
        "confidence_weights": dict(CONFIDENCE),
        "confidence_note": (
            "confidence = w_completeness*completeness + w_state*state_confidence + w_coverage*coverage；"
            "completeness = (q_completeness*30 + v_completeness*25)/55（55 分权重的真实数据占比）；"
            "coverage = mean(模块二 data_completeness, q_completeness, v_completeness)（三模块证据广度等权均值）；"
            "state_confidence 取模块一状态明确度。以上口径为 engine 内部定义，非用户给定阈值"
        ),
        "score_note": (
            "total_score = q_score + v_score，满分 55 = SCORE_CAP（模块三 30 + 模块四 25）；"
            "score_ratio = total_score / SCORE_CAP；其余 45 分未定义，见 pending_modules"
        ),
    }

    # ---- 非暴跌窗口：不启动深度模块，全市场 NO TRADE ----
    if not is_scan:
        empty = pd.DataFrame(columns=["ts_code", "decision", "decision_cn",
                                      "decision_reason", "total_score"])
        meta["gate"] = (
            f"市场状态 {state} 不属于扫描态 {list(SCAN_STATES)}，未启动深度模块；"
            "全部 NO TRADE（不强制每日交易）"
        )
        return {
            "scan_date": sd,
            "market": mkt,
            "results": empty,
            "buy": empty.copy(),
            "watch": empty.copy(),
            "no_trade": empty.copy(),
            "summary": {
                "n_candidates": 0, "n_scored": 0,
                "n_buy": 0, "n_watch": 0, "n_no_trade": 0,
                "mean_total_score": None, "max_total_score": None,
                "gate_blocked": True,
            },
            "meta": meta,
        }

    # ---- 模块二：错杀特征 + 候选初筛 ----
    mp = MP.build_mispricing(sd, panel=panel, name_map=name_map,
                             industry_map=industry_map, cfg=cfg_mispricing)
    candidates = mp["candidates"]
    codes = (candidates.index.astype(str).tolist()
             if not candidates.empty else [])

    # ---- 模块三：基本面质量 30 分 + VETO ----
    qres = Q.build_quality(sd, codes, industry_map=industry_map)
    quality = qres["quality"]

    # ---- 模块四：估值错杀 25 分 + 伪低估值 ----
    vres = V.build_valuation(sd, candidates, features=mp["features"],
                             cfg=cfg_valuation)
    valuation = vres["valuation"]

    # ---- 合并 ----
    res = candidates[[c for c in ("name", "industry", "ret20", "ret60",
                                  "sys_share", "ind_share", "specific_share",
                                  "resid_z", "data_completeness")
                      if c in candidates.columns]].copy()
    res = res.join(quality, how="left").join(valuation, how="left")

    q_comp = _series(res, "q_completeness")
    v_comp = _series(res, "v_completeness")
    q_score = pd.to_numeric(_series(res, "q_score", default=np.nan),
                             errors="coerce")
    v_score = pd.to_numeric(_series(res, "v_score", default=np.nan),
                             errors="coerce")

    total = q_score + v_score
    res["total_score"] = total
    res["score_ratio"] = total / float(SCORE_CAP)

    completeness = (q_comp * 30.0 + v_comp * 25.0) / float(SCORE_CAP)
    res["completeness"] = completeness

    m2_comp = _series(res, "data_completeness")
    coverage = pd.concat([m2_comp, q_comp, v_comp], axis=1).mean(axis=1)
    res["coverage"] = coverage

    if not np.isfinite(state_conf):
        state_conf = 0.0
    res["confidence"] = (CONFIDENCE["w_completeness"] * completeness
                         + CONFIDENCE["w_state"] * state_conf
                         + CONFIDENCE["w_coverage"] * coverage)

    # ---- 逐股判定 ----
    decisions, reasons = [], []
    for code, row in res.iterrows():
        dec, why = _decide(row)
        decisions.append(dec)
        reasons.append(why)
    res["decision"] = decisions
    res["decision_cn"] = [_ACTION_CN.get(d, d) for d in decisions]
    res["decision_reason"] = reasons

    res = res.sort_values(["total_score", "confidence"],
                           ascending=[False, False], na_position="last")
    res["rank"] = np.arange(1, len(res) + 1)

    # ---- top_n 名额：BUY 只保留达标股中总分最高的 top_n 只 ----
    # 用户要求「不强制每日交易」；若无名额约束，暴跌日可能有上百只同时达标，
    # 输出失去可操作性。超出名额的达标股降级为 WATCH，并在理由中写明原因。
    # 名额只从「已通过全部 BUY 门槛」的股票中分配，被否决/伪低估值的股票不占名额。
    res["decision_capped"] = False
    buy_ok = res[res["decision"] == BUY]
    if len(buy_ok) > top_n:
        over = buy_ok.index[top_n:]
        res.loc[over, "decision"] = WATCH
        res.loc[over, "decision_cn"] = _ACTION_CN[WATCH]
        res.loc[over, "decision_reason"] = (
            "总分达标但超出 BUY 名额 top_n="
            + str(top_n)
            + "（按总分排序靠后），降级为观察")
        res.loc[over, "decision_capped"] = True

    # ---- 分组输出 ----
    buy = res[res["decision"] == BUY]
    watch = res[res["decision"] == WATCH]
    no_trade = res[res["decision"] == NO_TRADE]

    meta.update({
        "gate": f"市场状态 {state}（扫描态），启动深度模块",
        "n_candidates": len(codes),
        "n_scored": int(total.notna().sum()),
        "n_buy": int(len(buy)),
        "n_watch": int(len(watch)),
        "n_no_trade": int(len(no_trade)),
        "n_veto": int(res["q_veto"].sum()) if "q_veto" in res.columns else 0,
        "n_pseudo_value": int(res["v_pseudo_value"].sum())
        if "v_pseudo_value" in res.columns else 0,
        "n_capped_to_watch": int(res["decision_capped"].sum())
        if "decision_capped" in res.columns else 0,
        "top_n": top_n,
        "top_n_note": (
            f"BUY 名额 top_n={top_n}：总分达标但排名靠后的标的降级为 WATCH，"
            "以保持「不强制每日交易」与输出可操作性"
        ),
        "n_score_unknown": int(total.isna().sum()),
        "mean_completeness": float(completeness.mean()) if len(res) else 0.0,
        "mean_confidence": float(res["confidence"].mean()) if len(res) else 0.0,
        "module_meta": {
            "market": mkt,
            "mispricing": mp["meta"],
            "quality": qres["meta"],
            "valuation": vres["meta"],
        },
        "veto_rules": dict(Q.VETO) if hasattr(Q, "VETO") else {},
        "valuation_meta": vres["meta"],
    })

    return {
        "scan_date": sd,
        "market": mkt,
        "results": res,
        "buy": buy,
        "watch": watch,
        "no_trade": no_trade,
        "summary": {
            "n_candidates": len(res),
            "n_scored": int(total.notna().sum()),
            "n_buy": int(len(buy)),
            "n_watch": int(len(watch)),
            "n_no_trade": int(len(no_trade)),
            "mean_total_score": _num(total.mean()) if len(res) else UNKNOWN,
            "max_total_score": _num(total.max()) if len(res) else UNKNOWN,
            "gate_blocked": False,
        },
        "meta": meta,
    }


# ----------------------------------------------------------------------------
# 单股判定
# ----------------------------------------------------------------------------

def _decide(row):
    """单股判定 -> (decision, reason)。规则全部来自 config.DECISION。"""
    total = _f(row.get("total_score"))
    ratio = _f(row.get("score_ratio"))
    conf = _f(row.get("confidence"))
    comp = _f(row.get("completeness"))
    veto = bool(row.get("q_veto", False))
    veto_reasons = str(row.get("q_veto_reasons", "") or "")
    pseudo = bool(row.get("v_pseudo_value", False))
    spec = _f(row.get("specific_share"))

    # 1) 否决优先：命中 VETO 一律 NO TRADE（无数据不触发，缺口由模块三写入 q_veto_unknown）
    if veto:
        return NO_TRADE, f"基本面否决（{veto_reasons}）"

    # 2) 分数缺失：无任何可用评分 -> 不交易（不猜测）
    if not np.isfinite(total) or not np.isfinite(ratio):
        return NO_TRADE, "评分数据不足（total_score 缺失）"

    # 3) 数据完整度门控：低于阈值一律 NO TRADE
    if not np.isfinite(comp) or comp < DECISION["min_data_completeness"]:
        return NO_TRADE, (f"数据完整度 {comp:.2f} < 门槛 "
                          f"{DECISION['min_data_completeness']:.2f}")

    # 4) 伪低估值：PE 便宜但盈利预期下修 -> 假便宜，不得 BUY
    if pseudo:
        if (np.isfinite(ratio) and ratio >= DECISION["watch_score_ratio"]
                and np.isfinite(conf) and conf >= DECISION["watch_min_confidence"]):
            return WATCH, ("伪低估值（PE 便宜但盈利预期下修），降级为观察，"
                           f"score_ratio={ratio:.2f} confidence={conf:.2f}")
        return NO_TRADE, "伪低估值（PE 便宜但盈利预期下修）"

    # 5) 个股特异抛售占比过高：不是系统性错杀，不得 BUY
    spec_high = (np.isfinite(spec)
                 and spec > DECISION["buy_max_specific_share"])

    # 6) BUY 门槛
    if (ratio >= DECISION["buy_score_ratio"]
            and np.isfinite(conf) and conf >= DECISION["buy_min_confidence"]
            and not spec_high):
        why = (f"score_ratio={ratio:.2f}>= {DECISION['buy_score_ratio']:.2f}，"
               f"confidence={conf:.2f}>= {DECISION['buy_min_confidence']:.2f}")
        return BUY, why

    # 7) WATCH 门槛
    if (ratio >= DECISION["watch_score_ratio"]
            and np.isfinite(conf) and conf >= DECISION["watch_min_confidence"]):
        why = (f"score_ratio={ratio:.2f}>= {DECISION['watch_score_ratio']:.2f}，"
               f"confidence={conf:.2f}>= {DECISION['watch_min_confidence']:.2f}")
        if spec_high:
            why += f"；但个股特异抛售占比 {spec:.2f} > {DECISION['buy_max_specific_share']:.2f}，不得 BUY"
        return WATCH, why

    # 8) 其余 NO TRADE
    if ratio < DECISION["watch_score_ratio"]:
        return NO_TRADE, (f"score_ratio={ratio:.2f} < 观察门槛 "
                          f"{DECISION['watch_score_ratio']:.2f}")
    return NO_TRADE, (f"confidence={_num(conf)} < 观察门槛 "
                      f"{DECISION['watch_min_confidence']:.2f}")


# ----------------------------------------------------------------------------
# 便捷入口
# ----------------------------------------------------------------------------

def scan(scan_date=None, **kwargs):
    """扫描指定日期；scan_date 缺省取本地缓存的最新交易日。"""
    if scan_date is None:
        sd = D.last_trade_date_on_or_before("20991231")
        if sd is None:
            raise ValueError("本地 daily_cache 无可用交易日")
    else:
        sd = D.norm_date(scan_date)
    return run_scan(sd, **kwargs)


def run_audit(scan_date=None) -> dict:
    """数据源审计（--audit）：实测本地 DB/parquet 字段与已知缺口。"""
    return D.audit_data_sources(scan_date)
