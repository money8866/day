# -*- coding: utf-8 -*-
"""
T+3 综合实盘二筛 · 每日回填后下一步程序  V1.0
================================================================================
定位
    每日盘后的下一步：backfill_daily_cache.py 完成回填 且 各策略已落库之后，
    读当日 stock_pick 全部策略候选 -> 机械层打分 -> 硬性否决 -> R/R -> 四档分级
    -> 产出 JSON 结构化结果 + Markdown 报告。规格来源：《T+3 AI 综合实盘二筛 Prompt V1.0》

硬约束（贯穿全脚本）
    1. 只读 picks_db/stock_picks.db 与 cache_daily/stock_data.db，绝不写入任何库。
    2. 不 import 任何原策略模块（hvt_bull / w7_second_wave_engine / trade_execution_engine /
       market_regime_v3 / sli / sector_0915 ...），本程序自带全部逻辑，可单独运行。
    3. 全部阈值来自 config/thresholds.json，脚本内不硬编码业务阈值。
    4. 原策略 score / action 只作展示，绝不参与 T+3 评分，也绝不回写修改。
    5. 资讯/公告层（Prompt §3/§16/§34）由 t3_news_ai.py 承担：东方财富公告接口取真实公告，
       交 DeepSeek 打分（config.news.enabled 控制）。任一环节失败一律回落 DATA_MISSING +
       中性分，绝不编造、绝不阻塞主流程；模型只允许依据给定材料打分。

用法
    python t3_screen_build.py                       # 取 stock_pick 最新交易日
    python t3_screen_build.py --date 20260922
    python t3_screen_build.py --date 20260922 --live   # 叠加新浪实时行情
    python t3_screen_build.py --no-ai                # 关闭资讯层（等价 config.news.enabled=false）
    python t3_screen_build.py --validate            # 仅做数据自检，不出报告
    python t3_screen_build.py --print               # 控制台打印报告正文
================================================================================
"""
import argparse
import json
import logging
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime

import numpy as np

from t3_news_ai import run_news_ai

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
CFG_PATH = os.path.join(CONFIG_DIR, "thresholds.json")

SEP_H = u"═" * 60
SEP_L = u"─" * 60

log = logging.getLogger("t3_screen")


# ────────────────────────────── 基础设施 ──────────────────────────────
def load_cfg(path=CFG_PATH):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def setup_logging(log_dir):
    os.makedirs(log_dir, exist_ok=True)
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    fh = logging.FileHandler(os.path.join(log_dir, "t3_screen_%s.log" % datetime.now().strftime("%Y%m%d")),
                             encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    return log


def pick_band(bands, x):
    """按 [lo, hi) 取档，越界回落最后一档。"""
    if x is None:
        return None
    for b in bands:
        if b["lo"] <= x < b["hi"]:
            return b
    return bands[-1]


def fnum(x, nd=2, dash="-"):
    return dash if x is None else ("%.*f" % (nd, x))


# ────────────────────────────── 数据层（只读） ──────────────────────────────
class Store(object):
    def __init__(self, picks_db, market_db):
        self.picks = sqlite3.connect(picks_db)
        self.picks.row_factory = sqlite3.Row
        self.mkt = sqlite3.connect(market_db)
        self.mkt.row_factory = sqlite3.Row

    def close(self):
        try:
            self.picks.close()
        except Exception:
            pass
        try:
            self.mkt.close()
        except Exception:
            pass

    # --- 候选池 ---
    def latest_pick_date(self):
        r = self.picks.execute("SELECT MAX(pick_date) FROM stock_pick").fetchone()
        return r[0] if r and r[0] else None

    def candidates(self, date):
        return self.picks.execute("""
            SELECT s.strategy_id, COALESCE(st.strategy_name, '') AS strategy_name,
                   s.ts_code, s.stock_name, s.signal, s.action, s.score, s.rank_no,
                   s.close, s.pct_chg, s.industry, s.reason,
                   s.stop_price, s.target_price, s.indicators, s.details
            FROM stock_pick s
            LEFT JOIN strategy st ON st.strategy_id = s.strategy_id
            WHERE s.pick_date = ?
            ORDER BY s.strategy_id, s.rank_no
        """, (date,)).fetchall()

    def industry_map(self, lookback_days=250):
        """用历史 stock_pick 构建 ts_code -> 行业 映射（行业字段仅在 stock_pick 中）。"""
        rows = self.picks.execute("""
            SELECT ts_code, industry FROM stock_pick
            WHERE industry IS NOT NULL AND industry <> ''
            ORDER BY pick_date DESC
        """).fetchall()
        m = {}
        for r in rows:
            if r["ts_code"] not in m:
                m[r["ts_code"]] = r["industry"]
        return m

    # --- 行情 ---
    def bars(self, code, n):
        rows = self.mkt.execute("""
            SELECT trade_date, open, high, low, close, pre_close, pct_chg, vol, amount
            FROM daily_cache WHERE ts_code = ? ORDER BY trade_date DESC LIMIT ?
        """, (code, n)).fetchall()
        return list(reversed(rows))

    def daily_basic(self, code, date):
        return self.mkt.execute("""
            SELECT turnover_rate, volume_ratio, total_mv, circ_mv, pe_ttm, pb, ps
            FROM daily_basic_cache WHERE ts_code = ? AND trade_date = ?
        """, (code, date)).fetchone()

    def fina(self, code):
        return self.mkt.execute("""
            SELECT ann_date, end_date, grossprofit_margin, netprofit_yoy, or_yoy,
                   ocf_to_or, roe, debt_to_assets
            FROM fina_indicator_cache WHERE ts_code = ? ORDER BY end_date DESC LIMIT 1
        """, (code,)).fetchone()

    def market_pct(self, date):
        rows = self.mkt.execute("SELECT ts_code, pct_chg FROM daily_cache WHERE trade_date = ?", (date,)).fetchall()
        return {r["ts_code"]: r["pct_chg"] for r in rows}

    def index_series(self, code, n=70):
        rows = self.mkt.execute("""
            SELECT trade_date, close, pct_chg FROM index_daily_cache
            WHERE ts_code = ? ORDER BY trade_date DESC LIMIT ?
        """, (code, n)).fetchall()
        return list(reversed(rows))


# ────────────────────────────── 实时行情（可选） ──────────────────────────────
def sina_quotes(codes, cfg):
    out = {}
    batch = cfg["sina"]["batch"]
    for i in range(0, len(codes), batch):
        chunk = codes[i:i + batch]
        lst = []
        for c in chunk:
            n, suf = c.split(".")
            lst.append(("sh" if suf.upper() == "SH" else "sz") + n)
        url = "https://hq.sinajs.cn/list=" + ",".join(lst)
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn",
                                                   "User-Agent": "Mozilla/5.0"})
        try:
            raw = urllib.request.urlopen(req, timeout=cfg["sina"]["timeout"]).read().decode("gbk", "ignore")
        except Exception as e:
            log.warning("实时行情取数失败(%d/%d): %s", i, len(codes), e)
            continue
        for line in raw.split("\n"):
            if "=" not in line or '"' not in line:
                continue
            key = line.split("=")[0].split("_")[-1].strip()
            vals = line.split('"')[1].split(",")
            if len(vals) < 32:
                continue
            code = key[2:] + (".SH" if key.startswith("sh") else ".SZ")
            try:
                out[code] = {"name": vals[0], "open": float(vals[1]), "pre_close": float(vals[2]),
                             "price": float(vals[3]), "high": float(vals[4]), "low": float(vals[5]),
                             "vol": float(vals[8]), "amount": float(vals[9]),
                             "date": vals[30], "time": vals[31]}
            except Exception:
                pass
    return out


# ────────────────────────────── 指标 ──────────────────────────────
def ma(x, n):
    return float(np.mean(x[-n:])) if len(x) >= n else None


def rsi(cl, n=14):
    if len(cl) < n + 1:
        return None
    dl = np.diff(cl[-(n + 1):])
    up = dl[dl > 0].sum()
    dn = -dl[dl < 0].sum()
    if dn == 0:
        return 100.0
    return float(100 - 100 / (1 + (up / n) / (dn / n)))


def atr14(b):
    if len(b) < 15:
        return None
    tr = []
    for i in range(len(b) - 14, len(b)):
        h, l, pc = float(b[i]["high"]), float(b[i]["low"]), float(b[i - 1]["close"])
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    return float(np.mean(tr))


# ────────────────────────────── 市场环境 §4 ──────────────────────────────
def market_state(store, date, cfg):
    mc = cfg["market"]
    idx = []
    for it in mc["indices"]:
        rows = store.index_series(it["code"], 70)
        if len(rows) < 25:
            idx.append({"code": it["code"], "name": it["name"], "role": it["role"], "missing": True})
            continue
        cl = [float(r["close"]) for r in rows]
        m20 = float(np.mean(cl[-20:]))
        rec = {
            "code": it["code"], "name": it["name"], "role": it["role"], "missing": False,
            "close": cl[-1],
            "ret5": (cl[-1] / cl[-6] - 1) * 100 if len(cl) > 6 else None,
            "ret20": (cl[-1] / cl[-21] - 1) * 100 if len(cl) > 21 else None,
            "ret60": (cl[-1] / cl[0] - 1) * 100 if len(cl) > 2 else None,
            "dist_ma20": (cl[-1] / m20 - 1) * 100,
            "slope20": (m20 / float(np.mean(cl[-25:-5])) - 1) * 100 if len(cl) >= 25 else None,
            "last_date": rows[-1]["trade_date"],
        }
        idx.append(rec)

    pm = store.market_pct(date)
    vals = [v for v in pm.values() if v is not None]
    up = sum(1 for x in vals if x > 0)
    dn = sum(1 for x in vals if x < 0)
    breadth = (up / float(up + dn)) if (up + dn) else None

    avail = [r for r in idx if not r.get("missing") and r.get("ret20") is not None]
    ret20 = [r["ret20"] for r in avail]
    ret5 = [r["ret5"] for r in avail if r.get("ret5") is not None]
    ctx = {
        "breadth": breadth,
        "breadth_pct": breadth * 100 if breadth is not None else None,
        "idx20_min": min(ret20) if ret20 else None,
        "idx20_max": max(ret20) if ret20 else None,
        "idx20_spread": (max(ret20) - min(ret20)) if ret20 else None,
        "idx5_min": min(ret5) if ret5 else None,
    }

    state, why = None, None
    for rule in mc["rules"]:
        w = rule["when"]
        ok = True
        if "breadth_below" in w:
            ok = ok and ctx["breadth"] is not None and ctx["breadth"] < w["breadth_below"]
        if "breadth_above" in w:
            ok = ok and ctx["breadth"] is not None and ctx["breadth"] > w["breadth_above"]
        if "idx5_min_below" in w:
            ok = ok and ctx["idx5_min"] is not None and ctx["idx5_min"] < w["idx5_min_below"]
        if "idx20_min_above" in w:
            ok = ok and ctx["idx20_min"] is not None and ctx["idx20_min"] > w["idx20_min_above"]
        if "idx20_min_below" in w:
            ok = ok and ctx["idx20_min"] is not None and ctx["idx20_min"] < w["idx20_min_below"]
        if "idx20_max_above" in w:
            ok = ok and ctx["idx20_max"] is not None and ctx["idx20_max"] > w["idx20_max_above"]
        if "idx20_spread_above" in w:
            ok = ok and ctx["idx20_spread"] is not None and ctx["idx20_spread"] > w["idx20_spread_above"]
        if "idx20_abs_below" in w:
            ok = ok and ctx["idx20_max"] is not None and abs(ctx["idx20_max"]) < w["idx20_abs_below"]
        if ok:
            state, why = rule["state"], rule["desc"]
            break
    if state is None:
        state, why = mc["default_state"], "无规则命中，取默认"

    return {
        "state": state,
        "state_cn": mc["state_cn"].get(state, state),
        "why": why,
        "score": mc["score_by_state"].get(state, 0.0),
        "breadth_up": up, "breadth_dn": dn, "breadth_total": len(vals),
        "breadth_ratio": breadth,
        "indices": idx,
        "ctx": ctx,
    }


def sector_table(store, date, cfg):
    """板块强度：以 stock_pick 历史行业映射为成员表，按当日成分平均涨跌幅衡量。"""
    ind_map = store.industry_map()
    pm = store.market_pct(date)
    buckets = {}
    for code, ind in ind_map.items():
        if code in pm and pm[code] is not None:
            buckets.setdefault(ind, []).append(pm[code])
    rows = []
    for ind, arr in buckets.items():
        if len(arr) < cfg["sector"]["min_members"]:
            continue
        avg = float(np.mean(arr))
        band = pick_band(cfg["sector"]["bands"], avg)
        rows.append({"industry": ind, "members": len(arr), "avg_pct": avg,
                     "score": band["pts"], "tag": band["tag"]})
    rows.sort(key=lambda x: -x["avg_pct"])
    return rows, {r["industry"]: r for r in rows}


# ────────────────────────────── 分项评分 §18 ──────────────────────────────
def score_trend(g, c):
    tc = c["trend"]
    t = 0.0
    flags = []
    if g["m5"] and g["m10"] and g["m20"]:
        if g["m5"] > g["m10"] > g["m20"]:
            t += tc["ma_align"]["full_bull"]
        elif g["m5"] > g["m20"] and g["m10"] > g["m20"]:
            t += tc["ma_align"]["partial_bull"]
        elif g["m5"] > g["m20"]:
            t += tc["ma_align"]["weak_bull"]
        else:
            flags.append("均线空头")
    if g["close"] and g["m20"] and g["close"] > g["m20"]:
        t += tc["above_ma20_pts"]
    else:
        flags.append("破MA20")
    if g["slope20"] is not None:
        if g["slope20"] > tc["slope20"]["strong"]:
            t += tc["slope20"]["pts_strong"]
        elif g["slope20"] > tc["slope20"]["mid"]:
            t += tc["slope20"]["pts_mid"]
        else:
            flags.append("MA20下行")
    if g["m60"] and g["close"] and g["close"] > g["m60"]:
        t += tc["above_ma60_pts"]
    dm = g["dist_ma20"]
    if dm is not None:
        d = tc["dist_ma20"]
        t += d["pts_good"] if dm < d["good_below"] else (d["pts_ok"] if dm < d["ok_below"] else d["pts_bad"])
        if dm > d["flag_above"]:
            flags.append("远离MA20(+%.0f%%)" % dm)
    r = g["rsi"]
    if r is not None:
        rc = tc["rsi"]
        if rc["ideal_low"] <= r <= rc["ideal_high"]:
            t += rc["pts_ideal"]
        elif rc["soft_low"] <= r < rc["ideal_low"]:
            t += rc["pts_soft"]
        elif r > rc["overbought"]:
            t += rc["pts_overbought"]
            flags.append("RSI超买%.0f" % r)
        elif r < rc["weak_below"]:
            flags.append("RSI弱%.0f" % r)
    t = max(0.0, min(tc["max"], t * tc["max"] / tc["raw_max"]))
    return t, flags


def score_volume(g, c):
    vc = c["volume"]
    v = 0.0
    flags = []
    vr = g["vr"]
    if vr is not None:
        band = pick_band(vc["bands"], vr)
        v += band["pts"]
        if band.get("flag"):
            flags.append("%s×%.1f" % (band["flag"], vr))
    v += vc["base_pts"]
    dh = g["dist_h20"]
    if dh is not None:
        if dh > vc["dist_h20"]["near_above"]:
            v += vc["dist_h20"]["pts_near"]
        elif dh > vc["dist_h20"]["mid_above"]:
            v += vc["dist_h20"]["pts_mid"]
    pos = g["pos"]
    if pos is not None:
        v += vc["pos"]["pts_high"] if pos > vc["pos"]["high"] else vc["pos"]["pts_low"]
    return max(0.0, min(vc["max"], v)), flags


def score_entry(g, c):
    ec = c["entry"]
    e = 0.0
    flags = []
    gap = g["gap"]
    if gap is not None:
        band = pick_band(ec["gap_bands"], gap)
        e += band["pts"]
        if gap >= 2.0:
            flags.append("%s%.1f%%" % (band["tag"], gap))
    p = g["chg_pct"]
    if p is not None:
        if p <= -11.0:
            flags.append("当日重挫%.1f%%" % p)
        else:
            band = pick_band(ec["pct_bands"], p)
            e += band["pts"]
            if band.get("flag"):
                flags.append("%s%.1f%%" % (band["flag"], p))
    dh = g["dist_h20"]
    if dh is not None:
        d = ec["dist_h20"]
        e += d["pts_in"] if d["lo"] < dh < d["hi"] else d["pts_out"]
    if g["dist_ma20"] is not None:
        d = ec["dist_ma20_abs"]
        e += d["pts_in"] if abs(g["dist_ma20"]) < d["limit"] else d["pts_out"]
    return max(0.0, min(ec["max"], e)), flags


def score_fina(g, c):
    fc = c["fina"]
    f = 0.0
    flags = []
    npyoy = g["npyoy"]
    if npyoy is not None:
        if npyoy > 0:
            f += fc["npyoy"]["pts"]
        if npyoy < fc["npyoy"]["flag_below"]:
            flags.append("净利同比%.0f%%" % npyoy)
    if g["oryoy"] is not None and g["oryoy"] > 0:
        f += fc["oryoy"]["pts"]
    if g["roe"] is not None:
        f += fc["roe"]["pts_good"] if g["roe"] > fc["roe"]["good"] else (fc["roe"]["pts_soft"] if g["roe"] > 0 else 0.0)
    if g["gpm"] is not None:
        f += fc["gpm"]["pts_good"] if g["gpm"] > fc["gpm"]["good"] else fc["gpm"]["pts_soft"]
    if g["ocf"] is not None:
        if g["ocf"] > fc["ocf"]["good"]:
            f += fc["ocf"]["pts_good"]
        if g["ocf"] < fc["ocf"]["flag_below"]:
            flags.append("经营现金流转负")
    if g["pe"] is not None and g["pe"] < 0:
        flags.append("PE为负(亏损)")
    f = max(0.0, min(fc["max"], f * fc["max"] / fc["raw_max"]))
    return f, flags


def score_risk(flags, c):
    rc = c["risk"]
    rk = rc["max"]
    for fl in flags:
        if any(k in fl for k in rc["penalty"]["severe"]):
            rk -= rc["penalty"]["severe_pts"]
        elif any(k in fl for k in rc["penalty"]["moderate"]):
            rk -= rc["penalty"]["moderate_pts"]
        else:
            rk -= rc["penalty"]["light_pts"]
    return max(0.0, min(rc["max"], rk))


def build_plan(g, c, cand):
    """买入区 / 不追价 / 止损 / T+3目标 / R/R（§12 §13 §20 §25）

     T+3 口径说明：不直接用策略的中长期止损（会到 -10% 量级，导致 R/R 系统性偏低
    而误杀全部高分票）。止损按短周期口径：stop_pct = clip(k×ATR/参考价, min, max)，
    再受「不得高于买区下沿下方 N%」与「策略止损更紧则采用」两条约束；
    目标取「ATR 推算」与「策略 target_price」中较保守者，并以 max_upside_pct 封顶。
    """
    ec = c["entry"]["zone"]
    rr = c["rr"]
    px = g["ref_px"]
    if not px:
        return None
    entry_low = px * (1 + ec["low_pct"] / 100.0)
    entry_high = px * (1 + ec["high_pct"] / 100.0)
    chase = entry_high * (1 + ec["chase_limit_pct"] / 100.0)
    atr = g["atr"] or (px * 0.03)

    atr_pct = atr / px * 100.0
    stop_pct = min(max(rr["stop_atr_k"] * atr_pct, rr["min_downside_pct"]), rr["max_downside_pct"])
    stop = px * (1 - stop_pct / 100.0)
    stop_src = "ATR×%.1f(%.2f%%)" % (rr["stop_atr_k"], stop_pct)
    ceil_stop = entry_low * (1 + rr["stop_below_entry_pct"] / 100.0)
    if stop > ceil_stop:
        stop, stop_src = ceil_stop, "买区下沿约束"
    if cand["stop_price"] and stop < float(cand["stop_price"]) < px:
        stop, stop_src = float(cand["stop_price"]), "策略止损(更紧)"

    hi_atr = px + atr * rr["t3_target_high_mult"]
    lo_atr = px + atr * rr["t3_target_low_mult"]
    cap = px * (1 + rr["max_upside_pct"] / 100.0)
    strat_t = cand["target_price"]
    if strat_t and strat_t > px * (1 + rr["min_upside_pct"] / 100.0):
        t3_low, target_src = min(float(strat_t), hi_atr), "策略/ATR取保守"
    else:
        t3_low, target_src = lo_atr, "ATR"
    if t3_low > cap:
        t3_low, target_src = cap, target_src + "(上行封顶)"
    t3_high = max(t3_low, min(hi_atr, cap))
    upside = (t3_low / px - 1) * 100.0
    downside = (px / stop - 1) * 100.0 if stop and stop > 0 else None
    ratio = (upside / downside) if (downside and downside > 0) else None
    return {
        "ref_px": px, "entry_low": entry_low, "entry_high": entry_high, "chase_limit": chase,
        "stop": stop, "stop_src": stop_src, "target": t3_low, "target_src": target_src,
        "t3_target_low": t3_low, "t3_target_high": t3_high,
        "upside_pct": upside, "downside_pct": downside, "rr": ratio,
    }


def structural_filter(code, name, c):
    """结构性排除：ST/*ST 与北交所（项目硬约束）。"""
    f = c.get("filters", {})
    hits = []
    if f.get("exclude_st") and name and ("ST" in name.upper()):
        hits.append({"code": "FILTER-ST", "reason": "ST/*ST 股不参与执行层"})
    if f.get("exclude_bj") and code.endswith(".BJ"):
        hits.append({"code": "FILTER-BJ", "reason": "北交所标的不参与执行层"})
    return hits


def check_veto(g, plan, state, flags, c):
    v = c["veto"]
    out = []
    if not v["enabled"]:
        return out
    if v["market_risk_off"] and state == "RISK_OFF":
        out.append({"code": "§19-E", "reason": "市场 RISK_OFF，全局禁止买入"})
    if v["break_structure"] and g["close"] and g["m20"]:
        if g["close"] < g["m20"] and (g["slope20"] is not None and g["slope20"] < 0):
            out.append({"code": "§19-B", "reason": "跌破关键结构位（收盘<MA20 且 MA20 下行）"})
    if v["break_stop"] and plan and g["ref_px"] and plan["stop"] and g["ref_px"] <= plan["stop"]:
        out.append({"code": "§19-B", "reason": "已跌破止损位 %.2f" % plan["stop"]})
    if v["far_from_zone"] and plan:
        far = plan["entry_high"] * (1 + c["entry"]["zone"]["veto_far_pct"] / 100.0)
        if g["ref_px"] > far:
            out.append({"code": "§19-A", "reason": "距合理买区 +%.1f%%（上限 +%.0f%%），禁追"
                        % ((g["ref_px"] / plan["entry_high"] - 1) * 100, c["entry"]["zone"]["veto_far_pct"])})
    vr = g["vr"] or 0
    chg = g["chg_pct"]
    dh60 = g["dist_h60"]
    if v["high_volume_stall"] and chg is not None and dh60 is not None:
        if vr > v["volume_spike_vr"] and v["stall_chg_lo"] <= chg <= v["stall_chg_hi"] and dh60 > v["stall_dist_h60_above"]:
            out.append({"code": "§19-D", "reason": "高位巨量滞涨（量比%.1f / 涨跌%.1f%% / 距60日高%.1f%%）" % (vr, chg, dh60)})
    if v["high_volume_drop"] and chg is not None and dh60 is not None:
        if vr > v["volume_spike_vr"] and chg <= v["drop_chg_below"] and dh60 > v["stall_dist_h60_above"]:
            out.append({"code": "§19-D", "reason": "高位放量下跌（量比%.1f / 涨跌%.1f%%）" % (vr, chg)})
    if v["fina_severe"] and g["npyoy"] is not None and g["npyoy"] < v["fina_npyoy_below"]:
        out.append({"code": "§15", "reason": "财务异常：净利同比 %.0f%%（阈值 %.0f%%）" % (g["npyoy"], v["fina_npyoy_below"])})
    if v["rr_below_min"] and plan and plan["rr"] is not None and plan["rr"] < c["rr"]["min"]:
        out.append({"code": "§19-F", "reason": "T+3 预期 R/R %.2f < %.2f" % (plan["rr"], c["rr"]["min"])})
    return out


def classify(score100, plan, veto, flags, state, c, news_severe=()):
    g = c["gates"]
    if veto:
        return "AVOID", "命中硬性否决 " + "、".join(x["code"] for x in veto)
    if state == "RISK_OFF":
        return "AVOID", "市场 RISK_OFF"
    rr = plan["rr"] if plan else None
    severe = [f for f in flags if any(k in f for k in c["risk"]["penalty"]["severe"])]
    if score100 >= g["buy"]:
        if news_severe:
            return "CONDITIONAL_BUY", "分数达标但存在资讯/公告风险：%s（需人工核验后方可执行）" % "、".join(news_severe)
        if g["buy_reject_severe_flags"] and severe:
            return "CONDITIONAL_BUY", "分数达标但存在硬风险标记：%s" % "、".join(severe)
        if rr is None or rr < g["buy_min_rr"]:
            return "CONDITIONAL_BUY", "R/R %s 未达 BUY 门槛 %.1f" % (fnum(rr), g["buy_min_rr"])
        return "BUY", "评分 %.1f 达标且 R/R %.2f ≥ %.1f" % (score100, rr, g["buy_min_rr"])
    if score100 >= g["conditional"]:
        return "CONDITIONAL_BUY", "机械评分 %.1f 达 CONDITIONAL 门槛 %.1f" % (score100, g["conditional"])
    if score100 >= g["wait"]:
        return "WAIT", "机械评分 %.1f 达 WAIT 门槛 %.1f，结构未成熟" % (score100, g["wait"])
    return "AVOID", "机械评分 %.1f 低于 WAIT 门槛 %.1f" % (score100, g["wait"])


# ────────────────────────────── 主流程 ──────────────────────────────
LEVEL_ORDER = ["BUY", "CONDITIONAL_BUY", "WAIT", "AVOID"]
LEVEL_CN = {"BUY": "TODAY BUY", "CONDITIONAL_BUY": "CONDITIONAL BUY", "WAIT": "WAIT", "AVOID": "AVOID"}


def run(cfg, date=None, live=False, validate=False):
    picks_db = cfg["paths"]["picks_db"]
    market_db = cfg["paths"]["market_db"]
    out_dir = os.path.join(BASE_DIR, cfg["paths"]["output_dir"])
    log_dir = os.path.join(BASE_DIR, cfg["paths"]["log_dir"])
    os.makedirs(out_dir, exist_ok=True)
    setup_logging(log_dir)

    store = Store(picks_db, market_db)
    try:
        if date is None:
            date = store.latest_pick_date()
        if not date:
            log.error("stock_pick 无任何记录，终止")
            return 1
        cands = store.candidates(date)
        log.info("交易日 %s | 候选 %d 条 | 策略 %d 个", date, len(cands),
                 len({r["strategy_id"] for r in cands}))
        if not cands:
            log.error("该交易日无候选票，终止")
            return 1

        # ---------- 数据自检 ----------
        codes = sorted({r["ts_code"] for r in cands})
        probe = store.bars(codes[0], cfg["data"]["bars_lookback"])
        pm = store.market_pct(date)
        idx_probe = store.index_series(cfg["market"]["indices"][0]["code"], 70)
        log.info("自检 | daily_cache(%s) %d 条 | 当日全市场 %d 只 | index_daily_cache %d 条 | fina %s",
                 codes[0], len(probe), len(pm), len(idx_probe),
                 "有" if store.fina(codes[0]) else "无")
        if validate:
            miss = [c for c in codes if len(store.bars(c, 40)) < cfg["data"]["min_bars"]]
            log.info("自检 | 行情不足 %d 只：%s", len(miss), ",".join(miss[:20]) or "无")
            log.info("自检 | 最近库内交易日 %s", pm and store.mkt.execute(
                "SELECT MAX(trade_date) FROM daily_cache").fetchone()[0])
            return 0

        # ---------- 市场环境 ----------
        mkt = market_state(store, date, cfg)
        log.info("市场状态 %s（%s）| 广度 %d/%d=%s | 指数20日区间 %s",
                 mkt["state"], mkt["why"], mkt["breadth_up"], mkt["breadth_dn"],
                 fnum((mkt["breadth_ratio"] or 0) * 100, 1) + "%",
                 (fnum(mkt["ctx"]["idx20_min"], 1) + "~" + fnum(mkt["ctx"]["idx20_max"], 1)))

        sec_rows, sec_map = sector_table(store, date, cfg)
        log.info("板块强度表 %d 个（成员>=%d）", len(sec_rows), cfg["sector"]["min_members"])

        # ---------- 实时行情（可选） ----------
        rt = {}
        if live or cfg["sina"]["enabled"]:
            rt = sina_quotes(codes, cfg)
            log.info("实时行情 %d/%d 只", len(rt), len(codes))

        # ---------- 逐票评分 ----------
        ind_map = store.industry_map()
        groups = {}
        for r in cands:
            groups.setdefault(r["ts_code"], []).append(r)

        ctxs = []
        for code, rows in groups.items():
            first = rows[0]
            b = store.bars(code, cfg["data"]["bars_lookback"])
            if len(b) < cfg["data"]["min_bars"]:
                log.warning("跳过 %s %s：行情不足(%d)", code, first["stock_name"], len(b))
                continue
            cl = [float(x["close"]) for x in b]
            hi = [float(x["high"]) for x in b]
            lo = [float(x["low"]) for x in b]
            vol = [float(x["vol"]) for x in b]
            last = b[-1]
            close = cl[-1]
            m5, m10, m20, m60 = ma(cl, 5), ma(cl, 10), ma(cl, 20), ma(cl, 60)
            m20_prev = float(np.mean(cl[-25:-5])) if len(cl) >= 25 else None
            slope20 = (m20 / m20_prev - 1) * 100 if (m20 and m20_prev) else None
            h20, l20 = max(hi[-20:]), min(lo[-20:])
            if len(hi) >= 60:
                h60, l60 = max(hi[-60:]), min(lo[-60:])
            else:
                h60, l60 = max(hi), min(lo)
            pos = (close - l60) / (h60 - l60) * 100 if h60 > l60 else None
            v20 = float(np.mean(vol[-20:]))
            vr = (vol[-1] / v20) if v20 else None
            bb = store.daily_basic(code, date)
            fi = store.fina(code)
            q = rt.get(code) if rt else None
            if q and q.get("price"):
                ref_px = q["price"]
                gap = (q["open"] / q["pre_close"] - 1) * 100 if q.get("pre_close") else None
                chg = (q["price"] / q["pre_close"] - 1) * 100 if q.get("pre_close") else None
            else:
                ref_px = close
                pre = float(last["pre_close"]) if last["pre_close"] else None
                gap = (float(last["open"]) / pre - 1) * 100 if (pre and last["open"]) else None
                chg = float(last["pct_chg"]) if last["pct_chg"] is not None else None

            ind = {}
            try:
                ind = json.loads(first["indicators"]) if first["indicators"] else {}
            except Exception:
                ind = {}
            industry = first["industry"] or ind_map.get(code, "")

            g = {
                "code": code, "name": first["stock_name"], "close": close, "ref_px": ref_px,
                "gap": gap, "chg_pct": chg,
                "m5": m5, "m10": m10, "m20": m20, "m60": m60, "slope20": slope20,
                "dist_ma20": (close / m20 - 1) * 100 if m20 else None,
                "h20": h20, "l20": l20, "h60": h60, "l60": l60,
                "dist_h20": (close / h20 - 1) * 100 if h20 else None,
                "dist_h60": (close / h60 - 1) * 100 if h60 else None,
                "pos": pos, "vr": vr, "v20": v20,
                "atr": atr14(b), "rsi": rsi(cl),
                "turnover": bb["turnover_rate"] if bb else None,
                "volr_basic": bb["volume_ratio"] if bb else None,
                "mv": bb["total_mv"] if bb else None,
                "pe": bb["pe_ttm"] if bb else None, "pb": bb["pb"] if bb else None,
                "ps": bb["ps"] if bb else None,
                "roe": fi["roe"] if fi else None, "npyoy": fi["netprofit_yoy"] if fi else None,
                "oryoy": fi["or_yoy"] if fi else None, "gpm": fi["grossprofit_margin"] if fi else None,
                "ocf": fi["ocf_to_or"] if fi else None, "end_date": fi["end_date"] if fi else None,
            }

            t, ft = score_trend(g, cfg)
            v, fv = score_volume(g, cfg)
            e, fe = score_entry(g, cfg)
            fn, ff = score_fina(g, cfg)
            flags = ft + fv + fe + ff
            rk = score_risk(flags, cfg)

            sec = sec_map.get(industry)
            sec_score = sec["score"] if sec else cfg["sector"]["missing_score"]
            mech = mkt["score"] + sec_score + t + v + e + fn + rk
            strategies = [{"strategy_id": r["strategy_id"], "strategy_name": r["strategy_name"],
                           "signal": r["signal"], "action": r["action"], "raw_score": r["score"],
                           "rank_no": r["rank_no"], "reason": r["reason"], "indicators": ind} for r in rows]
            bonus = min(cfg["resonance"]["max_bonus"],
                        max(0.0, (len(strategies) - 1) * cfg["resonance"]["per_extra_strategy"]))

            plan = build_plan(g, cfg, first)
            veto = structural_filter(code, first["stock_name"], cfg)
            veto += check_veto(g, plan, mkt["state"], flags, cfg)

            ctxs.append({
                "code": code, "name": first["stock_name"], "industry": industry,
                "g": g, "strategies": strategies, "flags": flags, "plan": plan, "veto": veto,
                "bonus": bonus, "mech": mech, "sec_detail": sec,
                "scores": {"market": mkt["score"], "sector": sec_score, "trend": t,
                           "volume": v, "entry": e, "fina": fn, "risk": rk},
                "price": {"close": close, "ref_px": ref_px, "gap_pct": gap, "chg_pct": chg,
                          "live": bool(q)},
                "tech": {"m5": m5, "m10": m10, "m20": m20, "m60": m60, "slope20": slope20,
                         "dist_ma20": g["dist_ma20"], "h20": h20, "l20": l20, "h60": h60, "l60": l60,
                         "dist_h20": g["dist_h20"], "dist_h60": g["dist_h60"], "pos": pos,
                         "vr": vr, "v20": v20, "atr": g["atr"], "rsi": g["rsi"],
                         "turnover": g["turnover"], "volr_basic": g["volr_basic"]},
                "fina": {"mv": g["mv"], "pe": g["pe"], "pb": g["pb"], "ps": g["ps"],
                         "roe": g["roe"], "npyoy": g["npyoy"], "oryoy": g["oryoy"],
                         "gpm": g["gpm"], "ocf": g["ocf"], "end_date": g["end_date"]},
            })

        # ---------- 资讯/公告层（东财公告 + DeepSeek，失败自动回落 DATA_MISSING） ----------
        news_cfg = cfg["news"]
        news_map = run_news_ai(ctxs, cfg, log) if news_cfg["enabled"] else {}
        if news_cfg["enabled"]:
            ok = sum(1 for v in news_map.values() if v["status"] == news_cfg["status_ai"])
            log.info("资讯层 | 生效 %d/%d 只（其余为 %s/%s，按中性 %.1f 分计）",
                     ok, len(ctxs), news_cfg["status_no_ann"], news_cfg["status_missing"],
                     news_cfg["missing_default_score"])

        # ---------- 汇总总分 + 四档分级 ----------
        records = []
        for c in ctxs:
            nw = news_map.get(c["code"]) or {
                "status": news_cfg["status_missing"], "score": news_cfg["missing_default_score"],
                "flags": [], "summary": "", "items": [], "model": None}
            news_score = float(nw["score"])
            news_all_flags = list(nw.get("flags") or [])
            severe_news = [f for f in news_all_flags if f in news_cfg["risk_severe_flags"]]
            flags = c["flags"] + severe_news
            mech = c["mech"]
            total = min(100.0, mech + news_score + c["bonus"])
            level, reason = classify(total, c["plan"], c["veto"], flags, mkt["state"], cfg, severe_news)

            records.append({
                "code": c["code"], "name": c["name"], "industry": c["industry"],
                "strategies": c["strategies"], "resonance": len(c["strategies"]),
                "resonance_bonus": c["bonus"],
                "price": c["price"], "tech": c["tech"], "fina": c["fina"],
                "scores": {"market": round(c["scores"]["market"], 2),
                           "sector": round(c["scores"]["sector"], 2),
                           "trend": round(c["scores"]["trend"], 2),
                           "volume": round(c["scores"]["volume"], 2),
                           "entry": round(c["scores"]["entry"], 2),
                           "fina": round(c["scores"]["fina"], 2),
                           "risk": round(c["scores"]["risk"], 2),
                           "news": round(news_score, 2), "news_status": nw["status"],
                           "resonance_bonus": c["bonus"],
                           "mech_subtotal": round(mech, 2), "mech_max": 90.0, "total": round(total, 2)},
                "sector_detail": c["sec_detail"],
                "flags": flags,
                "veto": c["veto"],
                "action_level": level,
                "decision_reason": reason,
                "plan": c["plan"],
                "news": {"status": nw["status"], "score": round(news_score, 2),
                         "flags": news_all_flags, "summary": nw.get("summary") or "",
                         "model": nw.get("model"), "items": nw.get("items") or [],
                         "query_suggest": news_cfg["query_template"].format(name=c["name"])},
            })

        records.sort(key=lambda r: -r["scores"]["total"])

        # ---------- 四档分级 + 输出限额 §27 ----------
        buckets = {k: [] for k in LEVEL_ORDER}
        for r in records:
            buckets[r["action_level"]].append(r)
        overflow = []
        max_buy = cfg["output"]["max_buy"]
        if len(buckets["BUY"]) > max_buy:
            overflow = buckets["BUY"][max_buy:]
            buckets["BUY"] = buckets["BUY"][:max_buy]
            for r in overflow:
                r["action_level"] = "CONDITIONAL_BUY"
                r["decision_reason"] += "；BUY 名额已满(%d)，降级观察" % max_buy
                buckets["CONDITIONAL_BUY"].append(r)
            buckets["CONDITIONAL_BUY"].sort(key=lambda r: -r["scores"]["total"])
        max_cond = cfg["output"]["max_conditional"]
        if len(buckets["CONDITIONAL_BUY"]) > max_cond:
            over2 = buckets["CONDITIONAL_BUY"][max_cond:]
            buckets["CONDITIONAL_BUY"] = buckets["CONDITIONAL_BUY"][:max_cond]
            for r in over2:
                r["action_level"] = "WAIT"
                r["decision_reason"] += "；CONDITIONAL BUY 名额已满(%d)，转 WAIT" % max_cond
                buckets["WAIT"].append(r)
            buckets["WAIT"].sort(key=lambda r: -r["scores"]["total"])

        summary = {k: len(v) for k, v in buckets.items()}
        log.info("分级结果 BUY=%d CONDITIONAL=%d WAIT=%d AVOID=%d",
                 summary["BUY"], summary["CONDITIONAL_BUY"], summary["WAIT"], summary["AVOID"])

        # ---------- 输出 JSON ----------
        payload = {
            "meta": {"version": cfg["version"], "spec": cfg["spec"], "date": date,
                     "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     "mode": "live+sina" if rt else "offline(after-backfill)",
                     "candidate_rows": len(cands), "unique_stocks": len(records),
                     "strategy_count": len({r["strategy_id"] for r in cands}),
                     "news_enabled": bool(news_cfg["enabled"]),
                     "news_provider": ("%s 公告 + %s(%s)" % (u"东方财富", news_cfg["provider"],
                                                             news_cfg["model"]))
                                      if news_cfg["enabled"] else u"未接入",
                     "config": CFG_PATH},
            "market": mkt,
            "sectors": sec_rows,
            "summary": summary,
            "buckets": {k: [r["code"] for r in buckets[k]] for k in LEVEL_ORDER},
            "news_pending": [{"code": r["code"], "name": r["name"], "query": r["news"]["query_suggest"]}
                             for r in records
                             if r["news"]["status"] not in (news_cfg["status_ai"],
                                                            news_cfg["status_no_ann"])],
            "records": records,
            "disclaimer": _disclaimer(cfg, records),
        }
        jpath = os.path.join(out_dir, "t3_screen_%s.json" % date)
        with open(jpath, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1, default=str)

        md = render_md(payload, cfg)
        mpath = os.path.join(out_dir, "t3_report_%s.md" % date)
        with open(mpath, "w", encoding="utf-8") as f:
            f.write(md)

        log.info("已输出 -> %s", jpath)
        log.info("已输出 -> %s", mpath)
        if cfg.get("_print"):
            print(md)
        return 0
    finally:
        store.close()


# ────────────────────────────── 报告渲染 ──────────────────────────────
def _disclaimer(cfg, records):
    n = cfg["news"]
    D = []
    if n["enabled"]:
        ai = sum(1 for r in records if r["news"]["status"] == n["status_ai"])
        noa = sum(1 for r in records if r["news"]["status"] == n["status_no_ann"])
        D.append(u"资讯/公告层已接入：公告取自东方财富公告接口（真实披露，近 %d 天），"
                 u"由 %s(%s) 按给定材料打分；模型被禁止编造未提供的资讯或事件。"
                 % (n["ann_days"], n["provider"], n["model"]))
        D.append(u"资讯层覆盖：模型已打分 %d 只 / 无近期公告 %d 只 / 缺失 %d 只；后两类均按中性 %.1f 分计入。"
                 % (ai, noa, len(records) - ai - noa, n["missing_default_score"]))
        D.append(u"任一环节失败（无 key、公告接口异常、超时、返回非 JSON）自动回落 DATA_MISSING + 中性分，不阻塞主流程。")
    else:
        D.append(u"资讯/公告层未接入（config.news.enabled=false），全部记录 news.status=DATA_MISSING，"
                 u"按中性 %s 分计入总分。" % n["missing_default_score"])
    D.append(u"机械层满分 90（市场10+板块10+趋势15+量价15+买点20+基本10+风控10），资讯 10 分为模型分/中性占位。")
    D.append(u"原策略 score/action 仅作展示，未参与 T+3 评分，本程序不写任何数据库。")
    D.append(u"T+3 目标价来自策略 target_price 或 ATR 推算，非投资建议。")
    return D


def _news_cell(r, cfg):
    """资讯分展示：已打分显示数值，否则显示状态"""
    n = r["news"]
    if n["status"] in (cfg["news"]["status_ai"], cfg["news"]["status_no_ann"]):
        return "%.1f" % n["score"]
    return n["status"]


def _news_line(r, cfg):
    """资讯层单行长文本"""
    n, nc = r["news"], cfg["news"]
    if n["status"] == nc["status_ai"]:
        return u"%.1f/10　%s　标签：%s　模型：%s" % (
            n["score"], n["summary"] or "-",
            u"、".join(n["flags"]) if n["flags"] else u"无", n["model"] or "-")
    if n["status"] == nc["status_no_ann"]:
        return u"%.1f/10（近 %d 天无公告，按中性分计）" % (n["score"], nc["ann_days"])
    return u"%s（未获取，按中性分计；建议检索词「%s」）" % (n["status"], n["query_suggest"])


def _row(r):
    p = r["plan"] or {}
    return ("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
        r["name"], r["code"], r["industry"] or "-",
        ",".join(s["strategy_id"] for s in r["strategies"]),
        fnum(r["price"]["ref_px"]), fnum(r["scores"]["total"], 1),
        ("%.2f~%.2f" % (p["entry_low"], p["entry_high"])) if p else "-",
        fnum(p.get("stop")) if p else "-",
        fnum(p.get("rr")) if p else "-",
    ))


def _detail(idx, r, cfg):
    p = r["plan"] or {}
    t = r["tech"]
    f = r["fina"]
    s = r["scores"]
    nw = r["news"]
    L = []
    L.append(u"### %d. %s %s　[T+3 %.1f 分 / %s]" % (idx, r["name"], r["code"], s["total"], r["action_level"]))
    L.append(u"")
    L.append(u"- 策略来源：%s" % "；".join(
        "%s(原分%s/rank%s/信号%s%s)" % (x["strategy_id"], x["raw_score"], x["rank_no"], x["signal"],
                                    "/动作%s" % x["action"] if x["action"] else "") for x in r["strategies"]))
    L.append(u"- 多策略共振：%d 个策略（加分 +%.1f，上限 %.1f）" % (r["resonance"], s["resonance_bonus"], 5.0))
    L.append(u"- 板块：%s %s" % (r["industry"] or "DATA_MISSING",
                              ("(板块均值 %+.2f%%, %d 只, %s)" % (r["sector_detail"]["avg_pct"],
                                                               r["sector_detail"]["members"],
                                                               r["sector_detail"]["tag"])) if r["sector_detail"] else "(成分不足，按中性计)"))
    L.append(u"- 价格：收盘 %.2f　参考价 %.2f　今开涨幅 %s　当日涨跌 %s" % (
        t and r["price"]["close"], r["price"]["ref_px"],
        (fnum(r["price"]["gap_pct"], 2) + "%") if r["price"]["gap_pct"] is not None else "-",
        (fnum(r["price"]["chg_pct"], 2) + "%") if r["price"]["chg_pct"] is not None else "-"))
    L.append(u"- 均线：MA5 %s / MA10 %s / MA20 %s / MA60 %s　MA20斜率 %s%%" % (
        fnum(t["m5"]), fnum(t["m10"]), fnum(t["m20"]), fnum(t["m60"]), fnum(t["slope20"], 2)))
    L.append(u"- 位置：距20日高 %s%%　距60日高 %s%%　60日区间位置 %s%%　量比 %s　换手 %s%%　RSI %s" % (
        fnum(t["dist_h20"], 2), fnum(t["dist_h60"], 2), fnum(t["pos"], 0),
        fnum(t["vr"]), fnum(t["turnover"], 1), fnum(t["rsi"], 0)))
    L.append(u"- 基本面(%s)：PE %s / PB %s / 净利同比 %s%% / 营收同比 %s%% / ROE %s / 毛利率 %s / 经营现金流占比 %s" % (
        f["end_date"] or "-", fnum(f["pe"]), fnum(f["pb"]), fnum(f["npyoy"], 1), fnum(f["oryoy"], 1),
        fnum(f["roe"], 1), fnum(f["gpm"], 1), fnum(f["ocf"], 3)))
    L.append(u"- 分项：市场 %.1f/10　板块 %.1f/10　趋势 %.1f/15　量价 %.1f/15　买点 %.1f/20　基本 %.1f/10　风控 %.1f/10　资讯 %s/10" % (
        s["market"], s["sector"], s["trend"], s["volume"], s["entry"], s["fina"], s["risk"],
        _news_cell(r, cfg)))
    if p:
        L.append(u"- 买入区：%.2f ~ %.2f　不追价上限：%.2f" % (p["entry_low"], p["entry_high"], p["chase_limit"]))
        L.append(u"- 止损：%.2f（%s）　T+3 目标：%.2f ~ %.2f（%s）" % (
            p["stop"], p["stop_src"], p["t3_target_low"], p["t3_target_high"], p["target_src"]))
        L.append(u"- R/R：%s（上行 %s%% / 下行 %s%%）" % (fnum(p["rr"]), fnum(p["upside_pct"], 2), fnum(p["downside_pct"], 2)))
    L.append(u"- 风险标记：%s" % ("；".join(r["flags"]) if r["flags"] else "无"))
    if r["veto"]:
        L.append(u"- 硬性否决：%s" % "；".join("%s %s" % (v["code"], v["reason"]) for v in r["veto"]))
    L.append(u"- 决策依据：%s" % r["decision_reason"])
    L.append(u"- 资讯公告：%s" % _news_line(r, cfg))
    if nw["items"]:
        for it in nw["items"][:5]:
            L.append(u"  - %s %s" % (it.get("date") or "-", it.get("title") or ""))
    L.append(u"")
    return L


def render_md(pl, cfg):
    m = pl["market"]
    L = []
    L.append(u"# T+3 综合实盘二筛报告 · %s" % pl["meta"]["date"])
    L.append(u"")
    L.append(u"> 生成时间 %s　|　模式 %s　|　候选 %d 条 / %d 只 / %d 个策略" % (
        pl["meta"]["generated_at"], pl["meta"]["mode"], pl["meta"]["candidate_rows"],
        pl["meta"]["unique_stocks"], pl["meta"]["strategy_count"]))
    L.append(u"> 规格来源 %s（%s）" % (pl["meta"]["spec"], pl["meta"]["version"]))
    L.append(u"")

    L.append(u"## 一、市场环境")
    L.append(u"")
    L.append(u"**状态：%s（%s）**　判定依据：%s　市场分 %.1f/10" % (
        m["state"], m["state_cn"], m["why"], m["score"]))
    L.append(u"")
    L.append(u"当日广度：上涨 %d / 下跌 %d（共 %d），上涨占比 %s%%" % (
        m["breadth_up"], m["breadth_dn"], m["breadth_total"], fnum((m["breadth_ratio"] or 0) * 100, 1)))
    L.append(u"")
    L.append(u"| 指数 | 收盘 | 5日 | 20日 | 60日 | 距MA20 | MA20斜率 |")
    L.append(u"| --- | --- | --- | --- | --- | --- | --- |")
    for it in m["indices"]:
        if it.get("missing"):
            L.append(u"| %s | DATA_MISSING | - | - | - | - | - |" % it["name"])
            continue
        L.append(u"| %s | %.2f | %s%% | %s%% | %s%% | %s%% | %s%% |" % (
            it["name"], it["close"], fnum(it["ret5"], 2), fnum(it["ret20"], 2), fnum(it["ret60"], 2),
            fnum(it["dist_ma20"], 2), fnum(it["slope20"], 2)))
    L.append(u"")

    L.append(u"## 二、板块强度（成员≥%d，按当日成分均涨幅）" % cfg["sector"]["min_members"])
    L.append(u"")
    L.append(u"| 板块 | 成员 | 均涨幅 | 板块分 | 判定 |")
    L.append(u"| --- | --- | --- | --- | --- |")
    for s in pl["sectors"][:15]:
        L.append(u"| %s | %d | %+.2f%% | %.1f | %s |" % (s["industry"], s["members"], s["avg_pct"], s["score"], s["tag"]))
    if not pl["sectors"]:
        L.append(u"| DATA_MISSING | - | - | - | 板块成分不足 |")
    L.append(u"")

    L.append(u"## 三、今日结论汇总")
    L.append(u"")
    L.append(u"| 档位 | 数量 | 名额上限 |")
    L.append(u"| --- | --- | --- |")
    L.append(u"| TODAY BUY | %d | %d |" % (pl["summary"]["BUY"], cfg["output"]["max_buy"]))
    L.append(u"| CONDITIONAL BUY | %d | %d |" % (pl["summary"]["CONDITIONAL_BUY"], cfg["output"]["max_conditional"]))
    L.append(u"| WAIT | %d | 不限 |" % pl["summary"]["WAIT"])
    L.append(u"| AVOID | %d | 不限 |" % pl["summary"]["AVOID"])
    L.append(u"")
    if pl["summary"]["BUY"] == 0:
        L.append(u"**TODAY BUY = 0**（无标的通过全部门槛，不凑数）")
        L.append(u"")

    order = ["BUY", "CONDITIONAL_BUY", "WAIT", "AVOID"]
    roman = ["四", "五", "六", "七"]
    for i, lv in enumerate(order):
        recs = [r for r in pl["records"] if r["action_level"] == lv]
        L.append(u"## %s、%s（%d）" % (roman[i], LEVEL_CN[lv], len(recs)))
        L.append(u"")
        if not recs:
            L.append(u"无。")
            L.append(u"")
            continue
        L.append(u"| 名称 | 代码 | 板块 | 策略 | 参考价 | T+3分 | 买入区 | 止损 | R/R |")
        L.append(u"| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for r in recs:
            L.append(_row(r))
        L.append(u"")
        if lv in ("BUY", "CONDITIONAL_BUY"):
            for j, r in enumerate(recs, 1):
                L.extend(_detail(j, r, cfg))
        else:
            for r in recs:
                why = r["decision_reason"]
                if r["veto"]:
                    why = "；".join("%s %s" % (v["code"], v["reason"]) for v in r["veto"])
                L.append(u"- %s %s（%.1f 分）：%s" % (r["name"], r["code"], r["scores"]["total"], why))
            L.append(u"")

    L.append(u"## 八、%s" % (u"资讯/公告核验（%s）" % pl["meta"]["news_provider"]
                             if pl["meta"].get("news_enabled") else u"资讯/公告待补清单（未接入）"))
    L.append(u"")
    if pl["meta"].get("news_enabled"):
        nc = cfg["news"]
        L.append(u"公告取自东方财富公告接口（真实披露，近 %d 天），由 %s 按给定材料打分；模型被禁止引入任何未提供的资讯或事件。"
                 % (nc["ann_days"], nc["model"]))
        L.append(u"")
        L.append(u"**执行前置条件：BUY 档位必须人工复核公告原文（减持、解禁、监管、重大合同、业绩预告等）；"
                 u"若本次核验出现资讯风险标签，程序已自动将其从 BUY 降为 CONDITIONAL BUY。**")
        L.append(u"")
        key_recs = [r for r in pl["records"] if r["action_level"] in ("BUY", "CONDITIONAL_BUY")]
        L.append(u"### 8.1 可执行档位核验结果（BUY / CONDITIONAL BUY）")
        L.append(u"")
        if key_recs:
            L.append(u"| 名称 | 代码 | 档位 | 资讯分 | 状态 | 摘要 | 标签 |")
            L.append(u"| --- | --- | --- | --- | --- | --- | --- |")
            for r in key_recs:
                n = r["news"]
                L.append(u"| %s | %s | %s | %s | %s | %s | %s |" % (
                    r["name"], r["code"], LEVEL_CN[r["action_level"]], _news_cell(r, cfg), n["status"],
                    n["summary"] or u"-", u"、".join(n["flags"]) or u"-"))
        else:
            L.append(u"无。")
        L.append(u"")
        risk_recs = [r for r in pl["records"]
                     if r["action_level"] not in ("BUY", "CONDITIONAL_BUY") and r["news"]["flags"]]
        if risk_recs:
            L.append(u"### 8.2 其余档位中带资讯标签的标的（供参考）")
            L.append(u"")
            L.append(u"| 名称 | 代码 | 档位 | 资讯分 | 摘要 | 标签 |")
            L.append(u"| --- | --- | --- | --- | --- | --- |")
            for r in risk_recs:
                n = r["news"]
                L.append(u"| %s | %s | %s | %s | %s | %s |" % (
                    r["name"], r["code"], LEVEL_CN[r["action_level"]], _news_cell(r, cfg),
                    n["summary"] or u"-", u"、".join(n["flags"]) or u"-"))
            L.append(u"")
        if pl["news_pending"]:
            L.append(u"### 8.3 资讯层未获取清单（按中性 %.1f 分计，建议 AI 会话人工补齐）" % nc["missing_default_score"])
            L.append(u"")
            L.append(u"| 名称 | 代码 | 建议检索词 |")
            L.append(u"| --- | --- | --- |")
            for n in pl["news_pending"]:
                L.append(u"| %s | %s | %s |" % (n["name"], n["code"], n["query"]))
            L.append(u"")
    else:
        L.append(u"以下标的资讯层未接入，需在 AI 会话中检索后回填，回填前其 10 分按中性 %.1f 计。"
                 % cfg["news"]["missing_default_score"])
        L.append(u"")
        L.append(u"**执行前置条件：CONDITIONAL BUY 及以上档位的标的，必须先补齐资讯/公告核验（减持、解禁、监管、"
                 u"重大合同、业绩预告等），核验通过后方可执行；未核验前一律视为 WAIT。**")
        L.append(u"")
        L.append(u"| 名称 | 代码 | 建议检索词 |")
        L.append(u"| --- | --- | --- |")
        for n in pl["news_pending"]:
            L.append(u"| %s | %s | %s |" % (n["name"], n["code"], n["query"]))
        L.append(u"")

    L.append(u"## 九、数据说明与免责")
    L.append(u"")
    for d in pl["disclaimer"]:
        L.append(u"- %s" % d)
    L.append(u"- 本报告不构成投资建议，交易决策与风险自担。")
    L.append(u"")
    return u"\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="T+3 综合实盘二筛 · 回填后下一步程序")
    ap.add_argument("--date", help="交易日 YYYYMMDD，默认取 stock_pick 最新交易日")
    ap.add_argument("--live", action="store_true", help="叠加新浪实时行情（盘中用）")
    ap.add_argument("--no-ai", dest="no_ai", action="store_true",
                    help="关闭资讯层（东财公告 + DeepSeek），回落 DATA_MISSING 中性分")
    ap.add_argument("--validate", action="store_true", help="仅做数据自检，不产出报告")
    ap.add_argument("--print", dest="do_print", action="store_true", help="控制台打印报告正文")
    ap.add_argument("--config", default=CFG_PATH, help="阈值配置文件路径")
    a = ap.parse_args()
    cfg = load_cfg(a.config)
    cfg["_print"] = a.do_print
    if a.no_ai:
        cfg["news"]["enabled"] = False
    return run(cfg, date=a.date, live=a.live, validate=a.validate)


if __name__ == "__main__":
    sys.exit(main())
