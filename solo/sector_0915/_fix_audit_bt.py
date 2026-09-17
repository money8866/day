# -*- coding: utf-8 -*-
"""P0/P1 修复后审计回测（独立实现，不复用 Step5 内部前向收益）：
  A. PIT：accessor 单元测试 + 合成动态成员 before/after 对照
  B. Opportunity Calibration：EARLY/EMERGING/CONFIRMING/STRONG 前向统计 + alpha vs random
  C. Diffusion：各 diffusion_pattern 的前向收益
  D. Cross-theme：主题数 / candidate_theme_count / primary_theme_confidence 分布
"""
import json
import math
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "output"
DB = r"D:\mystock\cache_daily\stock_data.db"
HZ = [3, 5, 10, 20]


def sec(t):
    print(f"\n{'='*78}\n{t}\n{'='*78}", flush=True)


# ──────────────────────────────────────────────────────────────────
# A. PIT
# ──────────────────────────────────────────────────────────────────
sec("A. PIT 成员口径验证")
import sector_stock_candidate_build as M  # noqa: E402

cfg = json.loads((ROOT / "config" / "sector_stock_candidate_config.json").read_text(encoding="utf-8"))
mb = M.load_membership()
print(f"成员表：{len(mb)} 行；is_static 分布={mb['is_static'].value_counts().to_dict()}")
print(f"effective_date 分布={mb['effective_date'].value_counts().to_dict()}")

# A1 accessor 单元测试（合成动态成员）
syn = pd.DataFrame({
    "ts_code": ["A.SZ", "B.SZ", "C.SZ", "D.SZ"],
    "sector_id": ["T01", "T01", "T01", "T01"],
    "is_static": [True, False, False, False],
    "effective_date": ["20260101", "20260701", "20260601", "20260101"],
    "membership_end_date": ["", "", "20260701", ""],
})
D = "20260630"
got = set(M.get_pit_membership(syn, D)["ts_code"])
print(f"A1 get_pit_membership(D={D}) -> {sorted(got)}  （期望 A,C,D：静态恒可见 + 06-01 生效未到期 + 01-01 生效无到期）")
a1_ok = got == {"A.SZ", "C.SZ", "D.SZ"}
got2 = set(M.get_pit_membership(syn, "20260715")["ts_code"])
print(f"A1 get_pit_membership(D=20260715) -> {sorted(got2)}  （期望 A,B,D：C 已过期，B 已生效）")
a1_ok = a1_ok and got2 == {"A.SZ", "B.SZ", "D.SZ"}
got3 = set(M.get_pit_membership(syn, "20260601")["ts_code"])
print(f"A1 get_pit_membership(D=20260601) -> {sorted(got3)}  （期望 A,C,D：B 未生效，C 当日生效）")
a1_ok = a1_ok and got3 == {"A.SZ", "C.SZ", "D.SZ"}
print(f"A1 结果：{'PASS' if a1_ok else 'FAIL'}")

# A2 合成动态成员 → 主题等权收益 before/after 差异（证明过滤非空转）
panel = M.load_theme_panel()
px = pd.read_csv(ROOT / "data" / "sector_stock_candidate_daily.csv", dtype={"trade_date": str},
                 usecols=["trade_date", "ts_code", "ret_5"], low_memory=False)
d0 = sorted(px["trade_date"].unique())[0]
syn2 = mb.copy()
mask = np.arange(len(syn2)) % 10 == 0          # 10% 成员改为「D 之后才生效」
syn2.loc[mask, "is_static"] = False
syn2.loc[mask, "effective_date"] = "20260901"
Dt = "20260630"
sl = px[px["trade_date"] == Dt]
full = (syn2.merge(sl, on="ts_code", how="inner").groupby("sector_id")["ret_5"].mean())
pit = (M.apply_pit_membership(syn2.merge(pd.DataFrame({"trade_date": [Dt] * len(sl)}), how="cross")
                              .assign(ts_code=lambda d: d["ts_code"]))
       if False else None)
j = M.apply_pit_membership(
    pd.DataFrame({"trade_date": [Dt]}).merge(
        syn2[["ts_code", "sector_id", "is_static", "effective_date"]], how="cross"))
pitdf = j.merge(sl, on="ts_code", how="inner").groupby("sector_id")["ret_5"].mean()
cmp_ = pd.DataFrame({"full_table": full, "pit": pitdf}).dropna()
cmp_["diff"] = cmp_["pit"] - cmp_["full_table"]
print(f"A2 合成 10% 未来成员、D={Dt}：{len(cmp_)} 个主题受影响；"
      f"|diff|>1e-9 的主题数={int((cmp_['diff'].abs() > 1e-9).sum())}；"
      f"mean|diff|={cmp_['diff'].abs().mean():.6f} max|diff|={cmp_['diff'].abs().max():.6f}")
print("   注：当前真实成员表 100% is_static=true，故 PIT 过滤对现有历史收益为空转（口径正确性修复）。")

# A3 结构性：全项目主题层聚合是否只有一条路径
src = (ROOT / "sector_stock_candidate_build.py").read_text(encoding="utf-8")
n_accessor = src.count("apply_pit_membership(") - src.count("def apply_pit_membership(")
n_merge_mb = src.count("merge(membership") + src.count("merge(mb)")
print(f"A3 代码扫描：apply_pit_membership 调用点={n_accessor}（唯一 accessor 入口）；"
      f"绕过 accessor 的 merge(membership/mb) 调用点={n_merge_mb}")
a3_ok = (n_accessor == 2 and n_merge_mb == 0)
print(f"A3 结果：{'PASS' if a3_ok else 'FAIL'}")

# ──────────────────────────────────────────────────────────────────
# 前向收益（独立实现）
# ──────────────────────────────────────────────────────────────────
sec("前向收益计算（独立：close × adj_factor）")
con = sqlite3.connect(DB)
dmin = __import__("pandas").read_sql_query("SELECT MIN(trade_date) a, MAX(trade_date) b FROM daily_cache", con).iloc[0]
d_start = str(min(dmin["a"], d0))
pxx = pd.read_sql_query(
    "SELECT ts_code,trade_date,close FROM daily_cache WHERE trade_date>=? AND trade_date<=?",
    con, params=(d_start, str(dmin["b"])))
aff = pd.read_sql_query(
    "SELECT ts_code,trade_date,adj_factor FROM adj_factor_cache WHERE trade_date>=? AND trade_date<=?",
    con, params=(d_start, str(dmin["b"])))
con.close()
for f in (pxx, aff):
    f["ts_code"] = f["ts_code"].astype(str)
    f["trade_date"] = f["trade_date"].astype(str)
print(f"行情 {len(pxx)} 行 / 复权因子 {len(aff)} 行；面板 {d0} ~ {px['trade_date'].max()}；库止 {dmin['b']}")
cw = pxx.pivot_table(index="trade_date", columns="ts_code", values="close", aggfunc="first")
aw = aff.pivot_table(index="trade_date", columns="ts_code", values="adj_factor", aggfunc="first")
cw = cw.ffill().bfill()
aw = aw.ffill().bfill()
adj = cw * aw
fwd = {}
for h in HZ:
    fwd[h] = (adj.shift(-h) / adj - 1.0).loc[sorted(px["trade_date"].unique())].stack()
fw = pd.DataFrame(fwd).rename(columns={h: f"_f{h}" for h in HZ})
fw.index.names = ["trade_date", "ts_code"]
fw = fw.reset_index()
print(f"前向收益面板 {len(fw)} 行")

sl = px.merge(fw, on=["trade_date", "ts_code"], how="left")
mbk = mb[["ts_code", "sector_id", "is_static", "effective_date"]].copy()
if "membership_end_date" in mb.columns:
    mbk["membership_end_date"] = mb["membership_end_date"]
dates = sorted(px["trade_date"].unique())
jm = M.apply_pit_membership(pd.DataFrame({"trade_date": dates}).merge(mbk, how="cross"))
jm = jm.merge(sl, on=["trade_date", "ts_code"], how="inner")
print(f"PIT 成员×行情 {len(jm)} 行")

th = jm.groupby(["trade_date", "sector_id"], sort=False).agg(
    **{f"tf{h}": (f"_f{h}", "mean") for h in HZ},
    n_mem=("_f5", "count")).reset_index()
# 当日全市场均值（随机等权组合的期望）
mkt = jm.groupby("trade_date", sort=False).agg(
    **{f"mk{h}": (f"_f{h}", "mean") for h in HZ},
    **{f"sd{h}": (f"_f{h}", "std") for h in HZ},
    n_pool=("_f5", "count")).reset_index()
th = th.merge(mkt, on="trade_date", how="left")
pnl = panel[["trade_date", "sector_id", "theme_phase"]].copy()
th = th.merge(pnl, on=["trade_date", "sector_id"], how="inner")
dl = pd.read_csv(ROOT / "data" / "sector_stock_candidate_daily.csv", dtype={"trade_date": str},
                 usecols=["trade_date", "sector_id", "diffusion_pattern", "diffusion_stage",
                          "theme_phase", "calibrated_opportunity_base", "calibration_status"],
                 low_memory=False).drop_duplicates(["trade_date", "sector_id"]).drop(columns=["theme_phase"])
th = th.merge(dl, on=["trade_date", "sector_id"], how="left")
print(f"主题×日 前向面板 {len(th)} 行；相位分布={th['theme_phase'].value_counts().to_dict()}")


def stats(g, tag):
    out = []
    for h in HZ:
        v = pd.to_numeric(g[f"tf{h}"], errors="coerce").dropna()
        if len(v) == 0:
            continue
        ex = pd.to_numeric(g[f"tf{h}"], errors="coerce") - pd.to_numeric(g[f"mk{h}"], errors="coerce")
        ex = ex.dropna()
        sd = pd.to_numeric(g[f"sd{h}"], errors="coerce").replace(0, np.nan)
        nm = pd.to_numeric(g["n_mem"], errors="coerce").replace(0, np.nan)
        z = (pd.to_numeric(g[f"tf{h}"], errors="coerce") - pd.to_numeric(g[f"mk{h}"], errors="coerce")) \
            / (sd / np.sqrt(nm))
        z = z.dropna()
        pct = float((0.5 * (1 + np.vectorize(math.erf)(z.values / math.sqrt(2)))).mean()) if len(z) else np.nan
        out.append({"tag": tag, "h": h, "n": len(v), "mean": v.mean(), "median": v.median(),
                    "win": (v > 0).mean(), "alpha_vs_random": ex.mean(), "rand_pct": pct})
    return out


# ──────────────────────────────────────────────────────────────────
# B. Opportunity Calibration
# ──────────────────────────────────────────────────────────────────
sec("B. Opportunity Calibration — 相位前向收益（主题×日 等权，PIT 成员）")
rows = []
for ph in ["EARLY", "EMERGING", "CONFIRMING", "STRONG"]:
    rows += stats(th[th["theme_phase"] == ph], ph)
# 目标相位：calibrated_opportunity_base >= min 的子集
gt = cfg["pullback_rules"]["theme_opportunity_gate"]["min_calibrated_opportunity_base"]
for lb, sub in [("CALIB>=%s" % gt, th[pd.to_numeric(th["calibrated_opportunity_base"], errors="coerce") >= gt]),
                ("EMERGING_CALIB>=%s" % gt,
                 th[(th["theme_phase"] == "EMERGING")
                    & (pd.to_numeric(th["calibrated_opportunity_base"], errors="coerce") >= gt)])]:
    rows += stats(sub, lb)
sb = pd.DataFrame(rows)
print(sb.to_string(index=False, float_format=lambda x: f"{x:.4f}"))

cal = M.compute_opportunity_calibration(panel, cfg)
lastd = sorted(cal["trade_date"].astype(str).unique())[-1]
cl = cal[cal["trade_date"].astype(str) == lastd]
rawmap = cfg["theme_opportunity"]["raw_phase_base_scores"]
print(f"\nB2 校准表（D={lastd}）：")
for _, r in cl.iterrows():
    ph = r["theme_phase"]
    print(f"  {ph:10s} raw_base={rawmap.get(ph):>5} alpha={r['calibration_alpha']:+.5f} "
          f"n={int(r['calibration_sample_size']):>4} t={r['calibration_t_stat']:+.2f} "
          f"status={r['calibration_status']:26s} mult={r['phase_quality_multiplier']:.2f} cap={r['calibration_base_cap']:.1f}")
print(f"\nB3 全样本 calibrated_opportunity_base 分布：\n"
      f"{pd.to_numeric(dl['calibrated_opportunity_base'],errors='coerce').describe().to_string()}")
print("B4 calibration_status 分布：" + str(dl["calibration_status"].value_counts().to_dict()))
em = th[th["theme_phase"] == "EMERGING"]
em_hi = em[pd.to_numeric(em["calibrated_opportunity_base"], errors="coerce") >= gt]
print(f"B5 EMERGING 主题日 n={len(em)}；其中 calibrated_base>={gt} 的 n={len(em_hi)}"
      f"（占比 {len(em_hi)/max(len(em),1):.1%}）")

# ──────────────────────────────────────────────────────────────────
# C. Diffusion
# ──────────────────────────────────────────────────────────────────
sec("C. Diffusion Pattern 前向收益（主题×日）")
rows = []
for p, g in th.groupby("diffusion_pattern", sort=False):
    if pd.isna(p) or str(p) in ("", "nan"):
        continue
    rows += stats(g, str(p))
sd_ = pd.DataFrame(rows).sort_values(["tag", "h"])
print(sd_.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
print("\nC2 diffusion_stage 分布：" + str(th["diffusion_stage"].value_counts().to_dict()))

# ──────────────────────────────────────────────────────────────────
# D. Cross-theme
# ──────────────────────────────────────────────────────────────────
sec("D. 跨主题污染分布（after）")
import pandas as _pd  # noqa: E402

dd = _pd.read_csv(ROOT / "data" / "sector_stock_candidate_daily.csv", dtype={"trade_date": str},
                  usecols=["trade_date", "ts_code", "sector_id", "candidate_status", "theme_overlap_count",
                           "candidate_theme_count", "primary_theme_confidence", "is_context_theme",
                           "cross_theme_pollution_flag", "cross_theme_score_penalty",
                           "multi_theme_crowding_flag"], low_memory=False)
st = dd.drop_duplicates(["trade_date", "ts_code"])
print("D1 股票×日 theme_overlap_count 分布：")
print(st["theme_overlap_count"].describe().to_string())
print("  分档：" + str(pd.cut(st["theme_overlap_count"], [0, 3, 5, 100], labels=["1-3", "4-5", ">=6"])
                        .value_counts().sort_index().to_dict()))
print(f"  >=6 主题股票×日 占比={float((st['theme_overlap_count']>=6).mean()):.4f}")
print("\nD2 candidate_status × 污染分级（股票×日口径）：")
piv = pd.crosstab(st["cross_theme_pollution_flag"], st["candidate_status"])
print(piv.to_string())
print("\nD3 candidate_theme_count 分布：" + str(st["candidate_theme_count"].describe().to_dict()))
print("D4 primary_theme_confidence 分布：" + str(st["primary_theme_confidence"].describe().to_dict()))
hi = st[st["cross_theme_pollution_flag"] == "HIGH"]
print(f"D5 HIGH 污染股票×日 n={len(hi)}；其中 CANDIDATE={int((hi['candidate_status']=='CANDIDATE').sum())}")
v = dd[dd["candidate_status"] == "CANDIDATE"]
print(f"D6 CANDIDATE 行 n={len(v)}；涉及股票×日 n={v.drop_duplicates(['trade_date','ts_code']).shape[0]}"
      f"（重复计分压缩比 {len(v)/max(v.drop_duplicates(['trade_date','ts_code']).shape[0],1):.3f}）")
print(f"D7 CANDIDATE 中 is_context_theme=True 行数={int(v['is_context_theme'].fillna(False).sum())}")
print(f"D8 cross_theme_score_penalty 分布：{pd.to_numeric(dd['cross_theme_score_penalty'],errors='coerce').describe().to_dict()}")
print(f"D9 multi_theme_crowding_flag 命中行={int((dd['multi_theme_crowding_flag'].astype(str).str.len()>0).sum())}")

pd.DataFrame(rows).to_csv(OUT / "_fix_audit_bt_rows.csv", index=False, encoding="utf-8-sig")
sb.to_csv(OUT / "_fix_audit_phase.csv", index=False, encoding="utf-8-sig")
sd_.to_csv(OUT / "_fix_audit_diffusion.csv", index=False, encoding="utf-8-sig")

# ──────────────────────────────────────────────────────────────────
# E. P0-02 机会校准层合成单元测试（需求 §六 / §八 的规则逐条断言）
# ──────────────────────────────────────────────────────────────────
sec("E. P0-02 Opportunity Calibration 合成单元测试")
base_cols = {"raw_opportunity_base": [85.0, 85.0, 85.0, 85.0, 85.0],
             "phase_quality_multiplier": [1.0, 0.8, 0.7, 1.0, 1.0],
             "calibration_base_cap": [100.0, 60.0, 60.0, 100.0, 100.0],
             "theme_phase": ["EMERGING", "DORMANT", "EMERGING", "STRONG", "CONFIRMING"],
             "breadth_delta_5": [0.05, 0.05, 0.05, -0.01, -0.01],
             "core_breadth_delta_5": [0.05, 0.05, 0.05, -0.01, -0.01],
             "relative_strength": [80.0, 80.0, 80.0, 30.0, 30.0],
             "theme_health": [80.0, 80.0, 80.0, 30.0, 30.0]}
syn = pd.DataFrame(base_cols)
res = M.apply_opportunity_calibration(syn, cfg)
got = res["calibrated_opportunity_base"].tolist()
cases = [
    ("alpha>0 且 mult=1.0 → 保持 raw", got[0], 85.0),
    ("alpha<=0（cap=60）→ base<=60", got[1], 60.0),
    ("样本不足（mult=0.7, cap=60）→ base<=60", got[2], 59.5),
    ("STRONG 缺 breadth/RS/health 支持 → cap 65", got[3], 65.0),
    ("CONFIRMING 缺支持 → cap 65", got[4], 65.0),
]
for name, g, exp in cases:
    ok = abs(g - exp) < 1e-9
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got={g:.4f} expect={exp:.4f}")
print(f"  support_count={res['calibration_support_count'].tolist()} "
      f"support_gate={res['calibration_support_gate'].tolist()}")
e_ok = all(abs(g - exp) < 1e-9 for _, g, exp in cases)
print(f"E 结果：{'PASS' if e_ok else 'FAIL'}")

print("\n完成。")
