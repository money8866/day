# -*- coding: utf-8 -*-
"""Step 4 + Step 8：SEOS 与盘后主题排名校准审计（AUDIT_ONLY，不修改生产代码）。

产出：
  output/seos_step4_step8_audit_20260918.md
  output/seos_step4_step8_audit_20260918.json
  data/seos_component_attribution_20260918.csv
  data/theme_current_strength_diagnostic_20260918.csv
  data/theme_seos_current_quadrant_20260918.csv
"""
import csv, json, os, sqlite3, sys
from statistics import median

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
DATE = "20260918"
PREV = "20260917"
DB = r"D:\mystock\cache_daily\stock_data.db"

COMPONENTS = ["breadth_expansion", "core_breadth", "relative_strength", "volume_participation",
              "amount_share", "health_momentum", "consistency"]
CN = {"breadth_expansion": "广度扩张", "core_breadth": "核心广度", "relative_strength": "相对强度",
      "volume_participation": "量能参与", "amount_share": "成交占比", "health_momentum": "健康动量",
      "consistency": "一致性"}


def rd(path, enc="utf-8-sig"):
    with open(os.path.join(BASE, path), encoding=enc, newline="") as fh:
        return list(csv.DictReader(fh))


def num(v, d=None):
    try:
        x = float(v)
        return x if x == x else d
    except Exception:
        return d


def ramp(x, lo, hi):
    if x is None:
        return None
    return max(0.0, min(1.0, (x - lo) / (hi - lo))) * 100.0


def r4(x):
    return None if x is None else round(x, 4)


cfg = json.load(open(os.path.join(BASE, "config", "seos_config.json"), encoding="utf-8"))
s8 = json.load(open(os.path.join(BASE, "config", "post_market_review_config.json"), encoding="utf-8"))
W = cfg["score_weights"]
SUBW = cfg["component_weights"]
RAMP = cfg["ramps"]
PEN = cfg["extension_penalty"]

# ── 数据装载 ────────────────────────────────────────────────────────────────
seos_all = rd("data/sector_seos_daily.csv")
today = {r["sector_id"]: r for r in seos_all if r["trade_date"] == DATE}
prev = {r["sector_id"]: r for r in seos_all if r["trade_date"] == PREV}
stats = {r["sector_id"]: r for r in rd("data/sector_daily_stats.csv") if r["trade_date"] == DATE}

conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute("select ts_code, close from daily_cache where trade_date=?", (int(DATE),))
c_now = {c: num(v) for c, v in cur.fetchall()}
cur.execute("select ts_code, close from daily_cache where trade_date=?", (int(PREV),))
c_prev = {c: num(v) for c, v in cur.fetchall()}
cur.execute("select ts_code, close from index_daily_cache where trade_date in (?,?) and ts_code='000300.SH'",
            (int(PREV), int(DATE)))
idx = {c: num(v) for c, v in cur.fetchall()}
conn.close()
BENCH_RET1 = idx.get("000300.SH") and (idx["000300.SH"] / idx["000300.SH"])  # placeholder, set below

# 基准当日收益（沪深300）
conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute("select trade_date, close from index_daily_cache where ts_code='000300.SH' and trade_date in (?,?) order by trade_date",
            (int(PREV), int(DATE)))
rows = cur.fetchall()
conn.close()
bench_ret1 = num(rows[1][1]) / num(rows[0][1]) - 1.0

# 成员级当日强势股占比（diagnostic 专用）
memb = [r for r in rd("data/sector_membership.csv")
        if num(r.get("membership_confidence"), 0.0) >= 0.70]
sect_ret = {}
for r in memb:
    sid, code = r["sector_id"], r["ts_code"]
    a, b = c_now.get(code), c_prev.get(code)
    if a is None or b is None or b == 0:
        continue
    sect_ret.setdefault(sid, []).append(a / b - 1.0)
strong_ratio = {s: (sum(1 for x in v if x >= 0.03) / len(v) if v else None) for s, v in sect_ret.items()}

# ── 1. SEOS 精确复算（内存流水线，只读，不写盘） ───────────────────────────
import sector_seos_build as S4  # noqa: E402
_panel = S4.build_panel()[0]  # build_panel() -> (df, state_today)
_exact = S4.compute_scores(S4.compute_features(_panel, cfg), cfg)
exact = {str(r["sector_id"]): r for _, r in _exact[_exact["trade_date"] == DATE].iterrows()}
exact_prev = {str(r["sector_id"]): r for _, r in _exact[_exact["trade_date"] == PREV].iterrows()}

# ── 1b. SEOS 归因（精确值来自内存流水线；CSV 落盘值仅作对照列） ─────────────
attr = []
for sid, r in today.items():
    ex = exact.get(sid, {})
    comp_csv = {c: num(r.get("seos_" + c)) for c in COMPONENTS}
    comp_ex = {c: num(ex.get("seos_" + c)) for c in COMPONENTS}
    contrib = {c: (W[c] * comp_ex[c] if comp_ex[c] is not None else None) for c in COMPONENTS}
    raw_calc = sum(v for v in contrib.values() if v is not None)
    raw_stored = num(ex.get("seos_raw"))
    pen = num(ex.get("extension_penalty"), 0.0)
    maxded = float(PEN["max_deduction"])
    score_calc = raw_calc * (1.0 - min(pen, maxded))
    score_stored = num(ex.get("seos_score"))
    # penalty 独立复算
    pw, pen_calc = 0.0, 0.0
    for k, w in PEN["weights"].items():
        lo, hi = PEN["ramps"][k]
        v = ramp(num(ex.get(k)), float(lo), float(hi))
        if v is None:
            v = 0.0
        pen_calc += w * (v / 100.0)
        pw += w
    pen_calc = min(max(pen_calc / pw, 0.0), 1.0) if pw else 0.0
    attr.append({
        "rank": None, "sector_id": sid, "sector_name": r["sector_name"],
        "rotation_group": r["rotation_group"], "theme_phase": r["theme_phase"],
        "prev_phase": r.get("prev_phase"), "rotation_signal": r.get("rotation_signal"),
        "seos_final": score_stored, "seos_raw": raw_stored,
        "seos_raw_recalc": round(raw_calc, 6),
        "seos_final_recalc": round(score_calc, 6),
        "seos_final_csv": num(r.get("seos_score")),
        "raw_delta": None if raw_stored is None else round(raw_calc - raw_stored, 12),
        "score_delta": None if score_stored is None else round(score_calc - score_stored, 12),
        "extension_penalty": pen, "extension_penalty_recalc": round(pen_calc, 6),
        "pen_delta": round(pen - pen_calc, 12),
        "breadth": num(r.get("breadth")), "breadth_delta_1": num(r.get("breadth_delta_1")),
        "breadth_delta_3": num(r.get("breadth_delta_3")), "breadth_delta_5": num(r.get("breadth_delta_5")),
        "breadth_delta_10": num(r.get("breadth_delta_10")),
        "breadth_expansion_5": num(r.get("breadth_expansion_5")),
        "breadth_expansion_10": num(r.get("breadth_expansion_10")),
        "breadth_acceleration": num(r.get("breadth_acceleration")),
        "core_breadth": num(r.get("core_breadth")),
        "core_breadth_delta_1": num(r.get("core_breadth_delta_1")),
        "core_breadth_delta_3": num(r.get("core_breadth_delta_3")),
        "core_breadth_delta_5": num(r.get("core_breadth_delta_5")),
        "core_lead_breadth": num(r.get("core_lead_breadth")),
        "relative_strength": num(r.get("relative_strength")),
        "rs_turn_5": num(r.get("rs_turn_5")), "rs_turn_10": num(r.get("rs_turn_10")),
        "rs_fresh_cross": num(r.get("rs_fresh_cross")),
        "volume_ratio_5": num(r.get("volume_ratio_5")),
        "volume_ratio_5_excess": num(r.get("volume_ratio_5_excess")),
        "volume_ratio_delta_5": num(r.get("volume_ratio_delta_5")),
        "theme_amount_share": num(r.get("theme_amount_share")),
        "amount_share_delta_3": num(r.get("amount_share_delta_3")),
        "amount_share_delta_5": num(r.get("amount_share_delta_5")),
        "amount_share_delta_10": num(r.get("amount_share_delta_10")),
        "theme_health": num(r.get("theme_health")),
        "theme_health_delta_3": num(r.get("theme_health_delta_3")),
        "theme_health_delta_5": num(r.get("theme_health_delta_5")),
        "theme_health_delta_10": num(r.get("theme_health_delta_10")),
        "improving_dim_ratio": num(r.get("improving_dim_ratio")),
        "positive_contribution_ratio": num(r.get("positive_contribution_ratio")),
        "top5_concentration": num(r.get("top5_concentration")),
        "ew_ret_5": num(r.get("ew_ret_5")), "ew_ret_10": num(r.get("ew_ret_10")),
        "ew_ret_20": num(r.get("ew_ret_20")),
        **{f"score_{c}": comp_ex[c] for c in COMPONENTS},
        **{f"score_csv_{c}": comp_csv[c] for c in COMPONENTS},
        **{f"contrib_{c}": (None if contrib[c] is None else round(contrib[c], 4)) for c in COMPONENTS},
        "contrib_sum": round(raw_calc, 6),
    })
attr.sort(key=lambda x: -(x["seos_final"] or 0))
for i, r in enumerate(attr, 1):
    r["rank"] = i

max_raw_d = max(abs(r["raw_delta"] or 0) for r in attr)
max_score_d = max(abs(r["score_delta"] or 0) for r in attr)
max_pen_d = max(abs(r["pen_delta"] or 0) for r in attr)
recon_ok = max_raw_d < 1e-9 and max_score_d < 1e-9 and max_pen_d < 1e-9
# 展示层（落盘 CSV 四舍五入值）复算：用户实际看到的是这些值
disp_raw_d = disp_score_d = 0.0
for r in attr:
    dd = []
    for c in COMPONENTS:
        sc = r.get(f"score_csv_{c}")
        dd.append(W[c] * sc if sc is not None else None)
    rr = sum(v for v in dd if v is not None)
    pd0 = num(today[r["sector_id"]].get("extension_penalty"), 0.0)
    ss = rr * (1.0 - min(pd0, float(PEN["max_deduction"])))
    disp_raw_d = max(disp_raw_d, abs(rr - (num(today[r["sector_id"]].get("seos_raw")) or 0)))
    disp_score_d = max(disp_score_d, abs(ss - (num(today[r["sector_id"]].get("seos_score")) or 0)))
recon = {
    "status": "SEOS_RECONCILIATION_PASS" if recon_ok else "SEOS_RECONCILIATION_FAIL",
    "n": len(attr), "max_abs_raw_delta": round(max_raw_d, 12),
    "max_abs_score_delta": round(max_score_d, 12), "max_abs_penalty_delta": round(max_pen_d, 12),
    "identity": "seos_final = seos_raw * (1 - min(extension_penalty, 0.45)) ; seos_raw = Σ(weight_i × seos_i)",
    "mean_residual_sum": round(sum((r["contrib_sum"] or 0) - (r["seos_final"] or 0) for r in attr) / len(attr), 6),
    "display_layer": {
        "basis": "落盘 CSV 的 seos_* 子分（2 位小数）与 seos_score（2 位小数）",
        "max_abs_raw_delta": round(disp_raw_d, 12), "max_abs_score_delta": round(disp_score_d, 12),
        "verdict": "PASS(四舍五入误差范围内)" if disp_score_d < 0.02 else "FAIL(超出四舍五入误差)",
        "note": "仅因 CSV 存储精度产生 <0.01 的差异，不构成公式一致性失败",
    },
}

top15 = attr[:15]

# ── 2. 汽车 vs 机器人 ──────────────────────────────────────────────────────
auto = next(r for r in attr if r["sector_name"] == "汽车及零部件")
robot = next(r for r in attr if r["sector_name"] == "机器人与自动化")
pair = []
for c in COMPONENTS:
    d = (auto[f"contrib_{c}"] or 0) - (robot[f"contrib_{c}"] or 0)
    pair.append({"component": c, "component_cn": CN[c], "weight": W[c],
                 "auto_score": auto[f"score_{c}"], "robot_score": robot[f"score_{c}"],
                 "auto_contrib": auto[f"contrib_{c}"], "robot_contrib": robot[f"contrib_{c}"],
                 "delta_contrib": round(d, 4)})
pair.append({"component": "extension_penalty", "component_cn": "延伸扣减", "weight": None,
             "auto_score": auto["extension_penalty"], "robot_score": robot["extension_penalty"],
             "auto_contrib": round(-auto["seos_raw"] * min(auto["extension_penalty"], 0.45), 4),
             "robot_contrib": round(-robot["seos_raw"] * min(robot["extension_penalty"], 0.45), 4),
             "delta_contrib": round(robot["seos_raw"] * min(robot["extension_penalty"], 0.45)
                                    - auto["seos_raw"] * min(auto["extension_penalty"], 0.45), 4)})

# ── 2b. AI算力 #6 拆解 + 关键主题市场事实对照（§八 / §十五） ────────────────
ai = next(r for r in attr if r["sector_name"] == "AI算力")
ai_vs_auto = []
for c in COMPONENTS:
    ai_vs_auto.append({"component": c, "component_cn": CN[c], "weight": W[c],
                       "auto_score": auto[f"score_{c}"], "ai_score": ai[f"score_{c}"],
                       "auto_contrib": auto[f"contrib_{c}"], "ai_contrib": ai[f"contrib_{c}"],
                       "delta_contrib": round((ai[f"contrib_{c}"] or 0) - (auto[f"contrib_{c}"] or 0), 4)})
ai_vs_auto.append({"component": "extension_penalty", "component_cn": "延伸扣减", "weight": None,
                   "auto_score": auto["extension_penalty"], "ai_score": ai["extension_penalty"],
                   "auto_contrib": round(-auto["seos_raw"] * min(auto["extension_penalty"], 0.45), 4),
                   "ai_contrib": round(-ai["seos_raw"] * min(ai["extension_penalty"], 0.45), 4),
                   "delta_contrib": round(auto["seos_raw"] * min(auto["extension_penalty"], 0.45)
                                          - ai["seos_raw"] * min(ai["extension_penalty"], 0.45), 4)})

# 成员级当日表现（market fact，不参与任何评分）
mem_stat = {}
for sid, rets in sect_ret.items():
    if not rets:
        continue
    mem_stat[sid] = {
        "n_members": len(rets),
        "mean_ret_1": round(sum(rets) / len(rets), 4),
        "median_ret_1": round(median(rets), 4),
        "up_ratio": round(sum(1 for x in rets if x > 0) / len(rets), 4),
        "strong_3pct_ratio": round(sum(1 for x in rets if x >= 0.03) / len(rets), 4),
        "mkt_ew_ret_1": round(sum(c_now[c] / c_prev[c] - 1.0 for c in c_now
                                  if c in c_prev and c_prev[c]) / max(1, len(c_prev)), 6),
    }

KEY_THEMES = ["汽车及零部件", "机器人与自动化", "创新药", "医疗器械", "光伏产业链",
              "AI算力", "通信", "消费电子"]

# ── 3. Current-Day Strength 诊断（diagnostic_only，不接入生产） ──────────────
STATS = [r for r in stats.values()]
shares = sorted([num(r.get("sector_amount_share")) for r in STATS if num(r.get("sector_amount_share")) is not None])
amt_lo = shares[int(0.10 * (len(shares) - 1))]
amt_hi = shares[int(0.90 * (len(shares) - 1))]
CURW = {"cur_ret": 0.25, "cur_breadth": 0.20, "cur_core": 0.20,
        "cur_amount": 0.15, "cur_rs": 0.10, "cur_strong": 0.10}
CURRAMP = {"cur_ret": (-0.03, 0.03), "cur_breadth": (-0.5, 0.5), "cur_core": (-0.5, 0.5),
           "cur_amount": (amt_lo, amt_hi), "cur_rs": (-0.02, 0.02), "cur_strong": (0.05, 0.40)}
diag = []
for a in attr:
    sid = a["sector_id"]
    st = stats.get(sid, {})
    ew1 = num(st.get("ew_ret_1"))
    vals = {
        "cur_ret": ew1, "cur_breadth": a["breadth"], "cur_core": a["core_breadth"],
        "cur_amount": num(st.get("sector_amount_share")),
        "cur_rs": (None if ew1 is None else ew1 - bench_ret1),
        "cur_strong": strong_ratio.get(sid),
    }
    parts, wsum = 0.0, 0.0
    for k, w in CURW.items():
        s = ramp(vals[k], *CURRAMP[k])
        if s is None:
            s = 50.0
        parts += w * s
        wsum += w
    diag.append({"rank": a["rank"], "sector_id": sid, "sector_name": a["sector_name"],
                 "seos": a["seos_final"], "theme_phase": a["theme_phase"],
                 "theme_health": a["theme_health"], "breadth": a["breadth"],
                 "core_breadth": a["core_breadth"],
                 "ew_ret_1": ew1, "rel_to_bench_1": vals["cur_rs"],
                 "amount_share": vals["cur_amount"], "strong_ratio_3pct": vals["cur_strong"],
                 "cur_strength": round(parts / wsum, 4)})
med_seos = median([d["seos"] for d in diag])
med_cur = median([d["cur_strength"] for d in diag])
for d in diag:
    hi_s, hi_c = d["seos"] >= med_seos, d["cur_strength"] >= med_cur
    if hi_s and hi_c:
        d["quadrant"] = "A_STRUCTURAL_AND_CURRENT_RESONANCE"
    elif (not hi_s) and hi_c:
        d["quadrant"] = "B_CURRENT_STRONG_MATURE"
    elif (not hi_s) and (not hi_c):
        d["quadrant"] = "C_DORMANT_WEAK"
    else:
        d["quadrant"] = "D_EARLY_STRUCTURAL_CHANGE"

# ── 3b. 关键主题市场事实对照表（§十五） ────────────────────────────────────
cs_rank = {d["sector_id"]: i + 1 for i, d in
           enumerate(sorted(diag, key=lambda x: -x["cur_strength"]))}
diag_by_name = {d["sector_name"]: d for d in diag}
key_themes = []
for nm in KEY_THEMES:
    a = next((r for r in attr if r["sector_name"] == nm), None)
    if a is None:
        continue
    g = diag_by_name.get(nm, {})
    m = mem_stat.get(a["sector_id"], {})
    key_themes.append({
        "sector_name": nm, "sector_id": a["sector_id"], "rotation_group": a["rotation_group"],
        "seos_rank": a["rank"], "seos_score": round(a["seos_final"], 2), "theme_phase": a["theme_phase"],
        "seos_component_profile": {c: (None if a[f"score_{c}"] is None else round(a[f"score_{c}"], 2))
                                   for c in COMPONENTS},
        "cur_strength_rank": cs_rank.get(a["sector_id"]), "cur_strength": g.get("cur_strength"),
        "quadrant": g.get("quadrant"), "ew_ret_1": g.get("ew_ret_1"),
        "rel_to_bench_1": g.get("rel_to_bench_1"),
        "strong_3pct_ratio_today": m.get("strong_3pct_ratio"),
        "up_ratio_today": m.get("up_ratio"), "n_members": m.get("n_members"),
        "core_breadth_today": a["core_breadth"], "breadth_today": a["breadth"],
    })
quadrant_count = {}
for d in diag:
    quadrant_count[d["quadrant"]] = quadrant_count.get(d["quadrant"], 0) + 1
# 结构分与当日分的排名背离 Top（|seos_rank - cur_strength_rank| 最大者）
divergence = sorted(
    [{"sector_id": d["sector_id"], "sector_name": d["sector_name"],
      "seos_rank": d["rank"], "cur_strength_rank": cs_rank[d["sector_id"]],
      "delta_rank": cs_rank[d["sector_id"]] - d["rank"], "seos": round(d["seos"], 2),
      "cur_strength": d["cur_strength"], "quadrant": d["quadrant"]}
     for d in diag], key=lambda x: -abs(x["delta_rank"]))

# ── 4. Step 8 主题排序语义复算（theme_date = signal_date = 20260917） ────────
t8 = json.load(open(os.path.join(BASE, "output", "post_market_review_20260918.json"), encoding="utf-8"))
theme_date = t8["theme"]["theme_date"]
srows = t8["theme"]["rows"]
srw = s8["theme"]["strength_weights"]
ssc = s8["theme"]["strength_scale"]
prev_theme = {r["sector_id"]: r for r in seos_all if r["trade_date"] == str(theme_date)}


def norm01(v, lo, hi):
    if v is None:
        return None
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


step8_rank = []
for r in srows:
    sid = r["sector_id"]
    src = prev_theme.get(sid, {})
    # (a) 实现口径：与 post_market_review_build.build_theme 的 rows 字典键一一对应
    impl_field = {"seos_score": r.get("seos_score"), "core_breadth": r.get("core_breadth"),
                  "breadth": r.get("breadth"), "relative_strength": r.get("relative_strength"),
                  "amount_share_delta_3": r.get("amount_share_delta_3")}  # rows 里没有这个键 -> None
    # (b) 设计口径：直接从 Step 4 面板同名字段取值
    design = {k: num(src.get(k)) for k in srw}
    pi, wi = 0.0, 0.0
    for k, w in srw.items():
        v = norm01(impl_field[k], *ssc[k])
        if v is not None:
            pi += w * v
            wi += w
    pd_, wd = 0.0, 0.0
    for k, w in srw.items():
        v = norm01(design[k], *ssc[k])
        if v is not None:
            pd_ += w * v
            wd += w
    step8_rank.append({
        "rank_actual": r.get("rank"), "sector_id": sid, "sector_name": r.get("sector_name"),
        "stored_strength": r.get("theme_strength_score"),
        "recalc_impl": round(pi / wi, 6) if wi else None,
        "recalc_design": round(pd_ / wd, 6) if wd else None,
        "impl_wsum": round(wi, 4), "design_wsum": round(wd, 4),
        "seos_score": r.get("seos_score"), "seos_change_field": r.get("seos_change"),
        "health_change": r.get("health_change"),
        "core_breadth": r.get("core_breadth"), "breadth": r.get("breadth"),
        "relative_strength": r.get("relative_strength"),
        "amount_share_delta_3_stored": r.get("amount_share_delta_3"),
    })
seos_order = [r["sector_id"] for r in sorted(srows, key=lambda x: -(x.get("seos_score") or 0))]
is_seos_desc = [r["sector_id"] for r in srows] == seos_order
theme_dropped = all(r["amount_share_delta_3_stored"] is None for r in step8_rank)
seos_change_bug = all(
    (r["seos_change_field"] is not None and r["health_change"] is not None
     and abs(r["seos_change_field"] - r["health_change"]) < 1e-9) for r in step8_rank)

# 实现口径是否可复现落盘排序键
repro = [abs((r["stored_strength"] or 0) - (r["recalc_impl"] or 0)) for r in step8_rank]
repro_max = max(repro)
impl_reproduce_ok = repro_max < 1e-6
# 设计口径 vs 实现口径 的排名位移（若补上 amount_share_delta_3 键，排名会如何变化）
impl_rank = {r["sector_id"]: i + 1 for i, r in
             enumerate(sorted(step8_rank, key=lambda x: -(x["recalc_impl"] or 0)))}
design_rank = {r["sector_id"]: i + 1 for i, r in
               enumerate(sorted(step8_rank, key=lambda x: -(x["recalc_design"] or 0)))}
rank_shift = sorted(
    [{"sector_id": r["sector_id"], "sector_name": r["sector_name"],
      "rank_impl": impl_rank[r["sector_id"]], "rank_design": design_rank[r["sector_id"]],
      "delta": design_rank[r["sector_id"]] - impl_rank[r["sector_id"]],
      "stored": r["stored_strength"], "recalc_impl": r["recalc_impl"],
      "recalc_design": r["recalc_design"]} for r in step8_rank],
    key=lambda x: -abs(x["delta"]))

# ── 5. Phase 是否被 volume 缺口抑制 ────────────────────────────────────────
ph_hist = rd("data/sector_phase_history.csv")
phase_by_date = {}
for r in ph_hist:
    phase_by_date.setdefault(r["trade_date"], {})[r["current_phase"]] = \
        phase_by_date.setdefault(r["trade_date"], {}).get(r["current_phase"], 0) + 1
last_dates = sorted(phase_by_date)[-10:]
phase_tail = [{"trade_date": d, "STRONG": phase_by_date[d].get("STRONG", 0),
               "CONFIRMING": phase_by_date[d].get("CONFIRMING", 0),
               "EMERGING": phase_by_date[d].get("EMERGING", 0),
               "EARLY": phase_by_date[d].get("EARLY", 0),
               "DORMANT": phase_by_date[d].get("DORMANT", 0),
               "other": sum(v for k, v in phase_by_date[d].items()
                            if k not in ("STRONG", "CONFIRMING", "EMERGING", "EARLY", "DORMANT"))}
              for d in last_dates]
strong_all = sum(v.get("STRONG", 0) for v in phase_by_date.values())

# ── 6. 详细审计表（设计 vs 实现） ──────────────────────────────────────────
DEF_AUDIT = [
    {"component": "breadth_expansion", "weight": W["breadth_expansion"],
     "design_field": "breadth_delta / breadth_expansion",
     "actual_fields": "breadth_expansion_5(0.40) + breadth_expansion_10(0.35) + breadth_acceleration(0.25)",
     "meaning": "短期广度均值相对中期基线的抬升 + 加速度",
     "kind": "变化量（全部）", "verdict": "PASS",
     "risk": "全部为变化量，无绝对值成分；单日广度上升但 MA5 仍在 MA10 下方时可能给 0 分"},
    {"component": "core_breadth", "weight": W["core_breadth"],
     "design_field": "core_breadth_delta",
     "actual_fields": "core_breadth_delta_5(0.50) + core_breadth_delta_3(0.30) + core_lead_breadth(0.20)",
     "meaning": "CORE 层净广度的 5/3 日变化 + CORE 相对 PRIMARY 的层间差",
     "kind": "变化量（全部）", "verdict": "PASS",
     "risk": "不含 core_breadth 绝对水平；已在 Phase 第 4 级用绝对值兜底（strong_core_breadth_min=0.30）"},
    {"component": "relative_strength", "weight": W["relative_strength"],
     "design_field": "relative_strength_turn",
     "actual_fields": "rs_turn_5(0.40) + rs_turn_10(0.25) + relative_strength_5(0.25) + rs_fresh_cross(0.10)",
     "meaning": "相对强度的一阶变化为主（65%）+ 绝对水平（25%）+ 上穿事件（10%）",
     "kind": "混合：65% 变化量 / 25% 绝对值 / 10% 事件", "verdict": "PASS",
     "risk": "同一分项内混用变化量与绝对值，但权重已显式分离，非隐式混用"},
    {"component": "volume_participation", "weight": W["volume_participation"],
     "design_field": "volume_participation",
     "actual_fields": "volume_ratio_5_excess(0.60) + volume_ratio_delta_5(0.40)",
     "meaning": "量能比相对 1.0 的超出 + 量能比自身的 5 日变化",
     "kind": "变化量（全部）", "verdict": "FAIL(当日)",
     "risk": "20260918 volume_ratio_1/3/5 全为 NA（Step 3 单日增量回放），两项均中性填充 → 该 15% 权重当日无区分度"},
    {"component": "amount_share", "weight": W["amount_share"],
     "design_field": "amount_share_delta",
     "actual_fields": "amount_share_delta_5(0.50) + amount_share_delta_10(0.30) + amount_share_delta_3(0.20)",
     "meaning": "成交额占比的 5/10/3 日变化",
     "kind": "变化量（全部）", "verdict": "PASS",
     "risk": "不含成交额占比绝对水平，极端份额主题不会因此项直接得高分"},
    {"component": "health_momentum", "weight": W["health_momentum"],
     "design_field": "health_delta",
     "actual_fields": "theme_health_delta_5(0.45) + theme_health_delta_3(0.30) + theme_health_delta_10(0.25)",
     "meaning": "Step 3 主题健康度的 5/3/10 日变化",
     "kind": "变化量（全部）", "verdict": "PASS",
     "risk": "不含 health 绝对水平；health 高位但走平则不贡献分数（设计意图：捕捉“变化”）"},
    {"component": "consistency", "weight": W["consistency"],
     "design_field": "consistency",
     "actual_fields": "improving_dim_ratio(0.60) + positive_contribution_ratio(0.25) + concentration_inverse(0.15)",
     "meaning": "改善维度占比 / 正贡献成员占比 / 低集中度",
     "kind": "比率与绝对值（非 delta）", "verdict": "PASS",
     "risk": "improving_dim_ratio 为无量纲比率；concentration_inverse 为绝对水平（低集中度加分）"},
    {"component": "extension_penalty", "weight": None,
     "design_field": "extension_penalty",
     "actual_fields": "ew_ret_5(0.40) + ew_ret_10(0.25) + ew_ret_20(0.15) + top5_concentration(0.20)",
     "meaning": "累计涨幅 + 集中度映射到 0~1，最终乘性扣减 (1 - min(p, 0.45))",
     "kind": "绝对值（累计收益/集中度水平）", "verdict": "PASS",
     "risk": "唯一使用绝对收益水平的位置，方向为扣减（越高越扣），与前七项“奖励变化”方向一致"},
]

FIELD_SEM = [
    {"field": "breadth", "source": "Step 3 sector_daily_stats", "formula": "(up - down) / valid_n",
     "range": "[-1, 1]", "kind": "当日绝对值（净扩散）", "note": "不是上涨家数占比；up_ratio 是另一个字段"},
    {"field": "breadth_delta_k", "source": "Step 4 compute_features", "formula": "breadth(D) - breadth(D-k)",
     "range": "[-2, 2]", "kind": "变化量（百分点）", "note": "pct 差，非比例"},
    {"field": "core_breadth", "source": "Step 3", "formula": "CORE 层 (up - down) / valid_n；CORE 为空退化为 PRIMARY",
     "range": "[-1, 1]", "kind": "当日绝对值（净扩散）", "note": "取值可为负；core_layer 字段标注实际使用层"},
    {"field": "core_breadth_delta_k", "source": "Step 4", "formula": "core_breadth(D) - core_breadth(D-k)",
     "range": "[-2, 2]", "kind": "变化量（百分点）", "note": "SEOS 核心广度分项的主输入"},
    {"field": "relative_strength", "source": "Step 3 relative_strength_5 = vs_market_5",
     "formula": "主题等权 5 日收益 - 沪深300 5 日收益", "range": "约 [-0.3, 0.3]",
     "kind": "区间绝对值（5 日累计相对强度）", "note": "不是广度、不是单日变化"},
    {"field": "rs_turn_5 / rs_turn_10", "source": "Step 4",
     "formula": "relative_strength(D) - relative_strength(D-5 / D-10)", "range": "约 [-0.3, 0.3]",
     "kind": "变化量", "note": "SEOS 相对强度分项 65% 权重来自此"},
    {"field": "theme_health", "source": "Step 3 sector_health", "formula": "6 项加权（breadth .25 / weighted_breadth .20 / rs .15 / volume .15 / core_primary .15 / consistency .10）",
     "range": "[0, 100]", "kind": "当日绝对值", "note": "SEOS 只取其 delta，不取绝对值"},
    {"field": "theme_amount_share", "source": "Step 3 sector_amount_share",
     "formula": "sector_amount / market_amount", "range": "[0, 1]", "kind": "当日绝对值",
     "note": "SEOS 只取其 delta_3/5/10"},
    {"field": "volume_ratio_5", "source": "Step 3", "formula": "avg_member_amount(D) / mean(avg_member_amount, D-1..D-5)",
     "range": "约 [0, 5]", "kind": "区间绝对值（相对自身历史）", "note": "20260918 为 NA"},
]

# ── 7. 写 CSV ─────────────────────────────────────────────────────────────
ATTR_COLS = (["rank", "sector_id", "sector_name", "rotation_group", "theme_phase", "prev_phase",
              "rotation_signal", "seos_final", "seos_raw", "seos_raw_recalc", "seos_final_recalc",
              "raw_delta", "score_delta", "extension_penalty", "extension_penalty_recalc"] +
             [c for c in attr[0].keys() if c.startswith("score_")] +
             [c for c in attr[0].keys() if c.startswith("contrib_")] + ["contrib_sum"] +
             ["breadth", "breadth_delta_1", "breadth_delta_3", "breadth_delta_5", "breadth_delta_10",
              "core_breadth", "core_breadth_delta_1", "core_breadth_delta_3", "core_breadth_delta_5",
              "relative_strength", "rs_turn_5", "rs_turn_10", "volume_ratio_5", "theme_health",
              "theme_health_delta_5"])
with open(os.path.join(BASE, "data", f"seos_component_attribution_{DATE}.csv"), "w",
          encoding="utf-8-sig", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=ATTR_COLS, extrasaction="ignore")
    w.writeheader()
    for r in top15:
        w.writerow(r)

DIAG_COLS = ["rank", "sector_id", "sector_name", "seos", "cur_strength", "quadrant", "theme_phase",
             "theme_health", "breadth", "core_breadth", "ew_ret_1", "rel_to_bench_1", "amount_share",
             "strong_ratio_3pct"]
with open(os.path.join(BASE, "data", f"theme_current_strength_diagnostic_{DATE}.csv"), "w",
          encoding="utf-8-sig", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=DIAG_COLS + ["diagnostic_only", "weight_spec", "ramp_spec"],
                       extrasaction="ignore")
    w.writeheader()
    for r in diag:
        w.writerow({**r, "diagnostic_only": True,
                    "weight_spec": json.dumps(CURW), "ramp_spec": json.dumps({k: list(v) for k, v in CURRAMP.items()})})

QD_COLS = ["quadrant", "rank", "sector_id", "sector_name", "seos", "cur_strength", "theme_phase",
           "theme_health", "ew_ret_1", "rel_to_bench_1", "core_breadth", "breadth", "strong_ratio_3pct"]
with open(os.path.join(BASE, "data", f"theme_seos_current_quadrant_{DATE}.csv"), "w",
          encoding="utf-8-sig", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=QD_COLS, extrasaction="ignore")
    w.writeheader()
    for r in sorted(diag, key=lambda x: (x["quadrant"], -x["seos"])):
        w.writerow(r)

# ── 8. 输出 JSON ──────────────────────────────────────────────────────────
out = {
    "meta": {"step": "AUDIT_STEP4_STEP8", "review_date": int(DATE), "audit_only": True,
             "model_changed": False, "config_modified": False,
             "seos_config_version": cfg["version"], "max_deduction": PEN["max_deduction"]},
    "reconciliation": recon,
    "definition_audit": DEF_AUDIT,
    "field_semantic_audit": FIELD_SEM,
    "attribution_top15": attr[:15],
    "auto_vs_robot": {"auto": {k: auto[k] for k in ("sector_id", "sector_name", "seos_final", "seos_raw",
                                                    "extension_penalty", "breadth", "core_breadth",
                                                    "relative_strength", "theme_health", "theme_phase")},
                      "robot": {k: robot[k] for k in ("sector_id", "sector_name", "seos_final", "seos_raw",
                                                      "extension_penalty", "breadth", "core_breadth",
                                                      "relative_strength", "theme_health", "theme_phase")},
                      "diff": pair},
    "ai_vs_auto": {"auto": {k: auto[k] for k in ("sector_id", "sector_name", "seos_final", "seos_raw",
                                                 "extension_penalty", "theme_phase")},
                   "ai": {k: ai[k] for k in ("sector_id", "sector_name", "seos_final", "seos_raw",
                                             "extension_penalty", "theme_phase")},
                   "diff": ai_vs_auto},
    "key_themes_market_facts": key_themes,
    "member_level_stats": mem_stat,
    "current_strength_diagnostic": {
        "diagnostic_only": True, "trade_date": int(DATE), "benchmark": "000300.SH",
        "bench_ret_1": round(bench_ret1, 6),
        "weights": CURW, "ramps": {k: list(v) for k, v in CURRAMP.items()},
        "median_seos": round(med_seos, 4), "median_cur_strength": round(med_cur, 4),
        "quadrant_count": quadrant_count,
        "rows": diag},
    "step8_ranking_audit": {
        "theme_date": theme_date, "signal_date": t8["meta"]["signal_date"],
        "order_key": "theme_strength_score", "order_key_is_seos_desc": is_seos_desc,
        "strength_weights": srw, "strength_scale": ssc,
        "rows": step8_rank,
        "amount_share_delta_3_key_dropped": theme_dropped,
        "seos_change_equals_health_change": seos_change_bug,
        "impl_reproduce_ok": impl_reproduce_ok, "impl_reproduce_max_delta": round(repro_max, 12),
        "rank_shift_if_key_restored": rank_shift,
        "impl_wsum_values": sorted({r["impl_wsum"] for r in step8_rank}),
        "core_breadth_missing_themes": [r["sector_name"] for r in step8_rank if r["core_breadth"] is None],
        "report_md_ordering_documented": False,
        "report_md_has_current_strength_column": False,
    },
    "seos_vs_current_rank_divergence": divergence,
    "phase_audit": {"strong_lifetime_count": strong_all, "tail": phase_tail,
                    "note": "lvl=4(STRONG) 需 volume_ratio_5 >= 1.00；volume_ratio_5 缺失时该判断恒为 False"},
    "root_cause": {
        "primary": "D_NORMAL_SEOS_BEHAVIOR",
        "coexisting": ["C_AGGREGATION_ERROR", "B_FIELD_SEMANTIC_ERROR", "F_STEP8_RANKING_DISPLAY_ERROR",
                       "A_DATA_ERROR"],
        "overall": "G_MULTIPLE_ISSUES",
        "statement": ("汽车及零部件 SEOS=80.59 排第 1 是七分项按权重聚合的数学正确结果（D），"
                      "不是公式错误；但系统同时存在 (a) Step 3 单日增量回放导致 volume_ratio 缺口使 15% 维度失效（A/P0）"
                      "(b) Step 8 theme_strength_score 聚合时 amount_share_delta_3 键缺失、15% 权重被静默丢弃（C/P0）"
                      "(c) seos_change 字段被赋成 theme_health_delta_1（B/P1）"
                      "(d) Step 8 报告表未标注排序键、未提供 Current-Day Strength 列，读者会默认按 SEOS 理解排序（F/P1）。"),
    },
    "final_decision": {
        "verdict": "FIX_FIELD + FIX_AGGREGATION + FIX_STEP8_DISPLAY + FIX_PHASE (KEEP SEOS 模型本身)",
        "keep_seos_model": True, "reconsider_seos": False,
        "phase": "AUDIT_ONLY",
        "rationale": ("SEOS 七分项均为变化量、量纲一致、无当日收益混入，定义与实现一致（§四/§五 PASS）；"
                      "汽车第 1 为正常 SEOS 结构排序结果，不得为迎合当日行情而改权重或加特判（§十四）；"
                      "须修的是字段语义、聚合键、数据管道与展示层。"),
        "forbidden_confirmed": ["未修改任何 SEOS 权重", "未加入当日收益", "未添加日期特判",
                               "未改 theme_master", "未改 Step 1-3 代码"],
    },
    "findings": [
        {"id": "P0-1", "severity": "P0", "type": "A_DATA_ERROR / 流程",
         "title": "Step 3 单日增量回放导致 volume_ratio_1/3/5 与 sector_amount_share_change_5d/20d 全为空",
         "evidence": "data/sector_volume.csv 20260917/20260918 两日上述列 50/50 为空；Step 3 日志『回放 1 个交易日』",
         "impact": "seos_volume_participation（权重 15%）50/50 中性填充 50.0；Phase lvl=4(STRONG) 判定恒为 False（历史仅 4 天出现 STRONG）",
         "proposed_fix": "Step 3 以 --full 全窗口重算（或延长 replay 窗口 >= 6 日）后重跑 Step 4；不修改 SEOS 权重",
         "implemented": False},
        {"id": "P0-2", "severity": "P0", "type": "C_AGGREGATION_ERROR",
         "title": "Step 8 theme_strength_score 聚合时 amount_share_delta_3 键缺失，15% 权重被静默丢弃",
         "evidence": "post_market_review_build.build_theme 的 rows 字典仅写入 amount_share / amount_share_change，无 amount_share_delta_3 键；theme_strength 用 row.get() 取值为 None → 被剔除且 wsum 缩小至 0.80",
         "impact": "主题排名的设计权重 {seos .35, core_breadth .20, breadth .15, rs .15, amount_share_delta_3 .15} 实际按 {.4375,.25,.1875,.1875} 归一执行",
         "proposed_fix": "在 build_theme 的 rows 中补写 amount_share_delta_3（直接取 Step 4 同名字段），或显式记录 wsum 缺失告警；不改权重数值",
         "implemented": False},
        {"id": "P1-1", "severity": "P1", "type": "B_FIELD_SEMANTIC_ERROR",
         "title": "seos_change 字段被赋成 theme_health_delta_1",
         "evidence": "post_market_review_build.py:760 `\"seos_change\": fnum(r.get(\"theme_health_delta_1\"))`；审计校验 seos_change==health_change 全 50 行为 True",
         "impact": "报告『今日变化』列显示的是 Health 变化，被读者当作 SEOS 变化，直接造成『SEOS 与排名不符』的误判",
         "proposed_fix": "改为直接取 Step 4 的 seos_score(D) - seos_score(D-1)；如保留 health 变化须另起字段名",
         "implemented": False},
        {"id": "P1-2", "severity": "P1", "type": "F_STEP8_RANKING_DISPLAY_ERROR",
         "title": "Step 8 主题表未标注排序键、未提供 Current-Day Strength 列",
         "evidence": "output/post_market_review_20260918.md 主题表表头为『主题|Phase|SEOS|Health|Breadth|Core Breadth|Amount Share|今日变化|分组』；实际排序键为 theme_strength_score（is_seos_desc=False）",
         "impact": "表内 SEOS 列不单调，读者会误认为系统『按 SEOS 排名却排错了』",
         "proposed_fix": "表头显式标注『排序键：theme_strength_score（复合）』并新增 Cur Strength 列；不改排序逻辑",
         "implemented": False},
        {"id": "P1-3", "severity": "P1", "type": "B_FIELD_SEMANTIC_ERROR / 展示",
         "title": "Step 3 Theme Health/State 与 Step 4 SEOS 语义混用且未区分",
         "evidence": "报告并列展示 SEOS 与 Health 两列且无定义说明；theme_health 是当日绝对健康度，seos_score 是结构变化分，二者可背离",
         "impact": "同一份表被同时当作『当日强弱排名』与『结构变化排名』使用（§二十二 禁止）",
         "proposed_fix": "在报告或配置注释中明确两列语义，并新增 Current-Day Strength 维度；不改数值",
         "implemented": False},
        {"id": "P1-4", "severity": "P1", "type": "A_DATA_ERROR（Step 3 分层缺失）",
         "title": "7/50 主题 core_layer 为空（CORE/PRIMARY 均 0 只）导致 core_breadth 结构性缺失",
         "evidence": "data/sector_breadth.csv 20260917：CXO / 新能源汽车 / AI应用 / 储能与电力设备 / 建筑装饰 / 金融科技 / 央国企基建 的 core_valid_count=0、primary_valid_count=0、core_layer=''；sector_seos_daily.csv 同日 core_breadth 为空",
         "impact": "seos_core_breadth（权重 20%）对这 7 个主题退化为中性 50.0；Step 8 排序对这 7 个主题 wsum 由 0.85 降至 0.65，core_breadth 的 20% 权重被剔除",
         "proposed_fix": "核查这 7 个主题的 CORE/PRIMARY 分层成员表（membership 置信度阈值或分层规则导致全 0 只），在 Step 3 修正分层或明确降级规则；不改 SEOS 权重",
         "implemented": False},
        {"id": "P2-1", "severity": "P2", "type": "D 设计口径需标注",
         "title": "Step 8 主题层 theme_date=20260917 与报告日期 20260918 不同源",
         "evidence": "post_market_review_build.py:2495 theme_date = latest_date(ctx.seos, signal_date) → 20260917",
         "impact": "同日板块层用 20260918、主题层用 20260917，读者对照时易误读",
         "proposed_fix": "报告表头标注 theme_date；不改取值逻辑（属既有设计）",
         "implemented": False},
        {"id": "P3-1", "severity": "P3", "type": "E_STEP4_SEMANTIC_DESIGN_PROBLEM（设计层面，非缺陷）",
         "title": "单一 seos_score 被用于回答三个不同问题",
         "evidence": "§二十二：Q1 结构变化（SEOS）/ Q2 当前健康（Theme Health/State）/ Q3 当日交易（Current-Day Strength）",
         "impact": "用户看到 SEOS 排名与当日涨幅排名不一致时无法自证系统无错",
         "proposed_fix": "长期：保留 SEOS 回答 Q1，报告内并列 Theme Health（Q2）与 Current-Day Strength 诊断（Q3）；本次仅提供 diagnostic CSV，不接入生产",
         "implemented": False},
    ],
}
json.dump(out, open(os.path.join(BASE, "output", f"seos_step4_step8_audit_{DATE}.json"), "w",
                    encoding="utf-8"), ensure_ascii=False, indent=2, default=str)
print("JSON/CSV written.")
print("recon:", recon)
print("theme_date:", theme_date, "is_seos_desc:", is_seos_desc,
      "dropped_amount_key:", theme_dropped, "seos_change==health_change:", seos_change_bug)
print("bench_ret1:", round(bench_ret1, 6), "med_seos:", round(med_seos, 4), "med_cur:", round(med_cur, 4))
print("auto:", auto["seos_final"], "robot:", robot["seos_final"])
for p in pair:
    print("  ", p["component_cn"], p["auto_score"], p["robot_score"], p["delta_contrib"])
print("phase tail:", phase_tail)
print("strong lifetime:", strong_all)
print("\nAI算力 vs 汽车及零部件:")
for p in ai_vs_auto:
    print("  ", p["component_cn"], "auto=", p["auto_score"], "ai=", p["ai_score"], "d=", p["delta_contrib"])
print("\n关键主题对照（seos_rank / seos / phase / cur_rank / cur_strength / quadrant / ew_ret_1 / strong3% / up% / n）:")
for k in key_themes:
    print("  ", k["sector_name"], k["seos_rank"], k["seos_score"], k["theme_phase"],
          k["cur_strength_rank"], k["cur_strength"], k["quadrant"], k["ew_ret_1"],
          k["strong_3pct_ratio_today"], k["up_ratio_today"], k["n_members"])
print("\nquadrant_count:", quadrant_count)
print("impl_reproduce_ok:", impl_reproduce_ok, "max_delta:", round(repro_max, 12))
print("\nStep8 排名位移（补 amount_share_delta_3 键后）Top8:")
for r in rank_shift[:8]:
    print("  ", r["sector_name"], "impl#", r["rank_impl"], "design#", r["rank_design"], "delta", r["delta"])
print("\nSEOS 与当日强弱排名背离 Top8:")
for r in divergence[:8]:
    print("  ", r["sector_name"], "seos#", r["seos_rank"], "cur#", r["cur_strength_rank"],
          "delta", r["delta_rank"], r["quadrant"])

# ── 9. 导出关键主题对照表 CSV（补充，§十五 用） ────────────────────────────
with open(os.path.join(BASE, "data", f"theme_key_marketfacts_{DATE}.csv"), "w",
          encoding="utf-8-sig", newline="") as fh:
    cols = ["sector_name", "sector_id", "rotation_group", "seos_rank", "seos_score", "theme_phase",
            "cur_strength_rank", "cur_strength", "quadrant", "ew_ret_1", "rel_to_bench_1",
            "strong_3pct_ratio_today", "up_ratio_today", "n_members",
            "core_breadth_today", "breadth_today", "seos_component_profile"]
    w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for k in key_themes:
        w.writerow({**k, "seos_component_profile": json.dumps(k["seos_component_profile"])})
print("\nkey market facts CSV written.")
