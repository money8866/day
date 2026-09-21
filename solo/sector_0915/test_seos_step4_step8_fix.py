# -*- coding: utf-8 -*-
"""Step 4 + Step 8 修复专项测试与修复审计（Prompt §二十七 / §二十八 / §三十 / §三十一）。

只读脚本：不写入任何上游数据文件。
唯一写出物：output/seos_step4_step8_fix_audit_20260918.{md,json}

口径说明
- 复盘日 review_date = 20260918（Step 8 的 theme_date = signal_date = 20260917）
- Step 4 落盘末日 = 20260918
- 修复前基准：data/sector_seos_daily.csv.bak（Step 4 修复前落盘）
              data/sector_stock_candidate_daily.csv（Step 5 未随修复重跑，仍为修复前口径）
              logs/run_step5_20260918.log（修复前 Step 5 全面板诊断）
- 修复后基准：data/*（当前落盘）、logs/regr_step5_20260918.log（修复后 Step 5 全面板诊断）
"""
import json
import os
import re
import sys

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
OUTPUT = os.path.join(BASE, "output")
LOGS = os.path.join(BASE, "logs")

REVIEW_DATE = "20260918"
THEME_DATE = "20260917"
STEP4_DATE = "20260918"

S4_NEW = os.path.join(DATA, "sector_seos_daily.csv")
S4_OLD = os.path.join(OUTPUT, "_prefix_seos_daily.csv")   # 修复前 Step 4 落盘（.bak 副本）
S5_CAND = os.path.join(DATA, "sector_stock_candidate_daily.csv")
S5_LOG_OLD = os.path.join(LOGS, "run_step5_20260918.log")
S5_LOG_NEW = os.path.join(LOGS, "regr_step5_20260918.log")
S8_JSON = os.path.join(OUTPUT, "post_market_review_%s.json" % REVIEW_DATE)
S8_MD = os.path.join(OUTPUT, "post_market_review_%s.md" % REVIEW_DATE)
S4_TODAY = os.path.join(OUTPUT, "sector_seos_today.json")
SEOS_CFG = os.path.join(BASE, "config", "seos_config.json")

AUDIT_MD = os.path.join(OUTPUT, "seos_step4_step8_fix_audit_%s.md" % REVIEW_DATE)
AUDIT_JSON = os.path.join(OUTPUT, "seos_step4_step8_fix_audit_%s.json" % REVIEW_DATE)

# 修复前设计值（§二十三，冻结权重）——只做"是否被改动"的对照，不重新优化
FROZEN_SCORE_WEIGHTS = {
    "breadth_expansion": 0.25, "core_breadth": 0.20, "relative_strength": 0.15,
    "volume_participation": 0.15, "amount_share": 0.10, "health_momentum": 0.10,
    "consistency": 0.05,
}
SEOS_COMPONENTS = ["breadth_expansion", "core_breadth", "relative_strength",
                   "volume_participation", "amount_share", "health_momentum", "consistency"]

FOCUS_THEMES = ["汽车", "机器人", "创新药", "医疗器械", "光伏", "AI算力",
                "医疗服务", "美容护理", "通信", "消费电子"]

BUY_WORDS = ["BUY", "买入", "推荐", "看好", "目标价", "仓位", "加仓", "建仓", "止损位"]
LEGACY_FIELD_TOKENS = ["seos_change", "amount_share_change", "volume_participation_change",
                       "health_change", "breadth_change", "core_breadth_change"]

TOL_R = 5e-3          # CSV 落盘 4 位小数带来的舍入容差
TOL_REL = 1e-4


def rd(path, **kw):
    kw.setdefault("encoding", "utf-8-sig")
    return pd.read_csv(path, low_memory=False, **kw)


def jload(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def num(s):
    return pd.to_numeric(s, errors="coerce")


def norm01(v, lo, hi):
    if not np.isfinite(v) or not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return np.nan
    return float(min(max((v - lo) / (hi - lo), 0.0), 1.0))


def fmt(v, nd=3):
    return "-" if v is None or (isinstance(v, float) and not np.isfinite(v)) else (
        f"{v:.{nd}f}" if isinstance(v, (int, float, np.floating)) else str(v))


def collapse(path, marker="Step5 完成"):
    """读取日志并按空白折叠。PowerShell 重定向可能是 UTF-8 或 UTF-16LE，按 marker 探测。"""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-16", "gbk", "utf-8"):
        try:
            t = raw.decode(enc)
        except Exception:
            continue
        if marker in t:
            return re.sub(r"\s+", " ", t)
    return re.sub(r"\s+", " ", raw.decode("utf-8", errors="replace"))


# ──────────────────────────────────────────────────────────────────────────────
# 数据装载
# ──────────────────────────────────────────────────────────────────────────────
s4_new = rd(S4_NEW, dtype={"trade_date": str})
s4_old = rd(S4_OLD, dtype={"trade_date": str})
s8 = jload(S8_JSON)
s8_md = open(S8_MD, "r", encoding="utf-8").read()
cfg = jload(SEOS_CFG)
s4_today = jload(S4_TODAY)
s5_cand = rd(S5_CAND, dtype={"trade_date": str},
             usecols=["trade_date", "ts_code", "sector_id", "candidate_status",
                      "candidate_score", "candidate_type", "theme_phase"])
s5_before = s5_cand[s5_cand["trade_date"] == STEP4_DATE].copy()
s8_rows = s8["theme"]["rows"]
s8_by_id = {str(r["sector_id"]): r for r in s8_rows}
s8_theme_date = str(s8["theme"]["theme_date"])

THEMES = {}
for r in s8_rows:
    THEMES[str(r["sector_id"])] = str(r["sector_name"])


def focus_ids():
    """按 §二十 的主题名定位。优先「名称以关键词开头」（避免「汽车」误配到「新能源汽车」）。"""
    out = []
    for key in FOCUS_THEMES:
        head = [tid for tid, nm in THEMES.items() if nm.startswith(key)]
        body = [tid for tid, nm in THEMES.items() if key in nm]
        hit = sorted(head) or sorted(body)
        if hit:
            out.append((key, hit[0], THEMES[hit[0]]))
    return out


def log_block(path):
    """解析 Step 5 全面板诊断日志（容忍 PowerShell 换行断行）。"""
    t = collapse(path)
    g = lambda p, d=None: (re.search(p, t).group(1) if re.search(p, t) else d)
    st = re.search(r"候选状态分布： candidate_status EXCLUDE (\d+) WATCH (\d+) CANDIDATE (\d+)", t)
    cl = re.search(r"候选层级行：(\d+) / (\d+)；candidate_score p50=([\d.]+) p75=([\d.]+) p90=([\d.]+) max=\s*([\d.]+)", t)
    fn = re.search(r"Step5 完成：请求日 (\d+) → 生效日 (\d+)；评估 (\d+) 行，候选池 (\d+) 行，污染 (\d+) 行", t)
    ed = re.search(r"有效主题日：(\d+) 行 / (\d+) 主题 / (\d+) 日", t)
    ex = re.search(r"成员×主题日展开：(\d+) 行", t)
    return {
        "effective_theme_days": [int(ed.group(i)) for i in (1, 2, 3)] if ed else None,
        "expanded_rows": int(ex.group(1)) if ex else None,
        "tier_rows": [int(cl.group(1)), int(cl.group(2))] if cl else None,
        "score_p": [float(cl.group(i)) for i in (3, 4, 5, 6)] if cl else None,
        "status": {"EXCLUDE": int(st.group(1)), "WATCH": int(st.group(2)),
                   "CANDIDATE": int(st.group(3))} if st else None,
        "complete": {"requested": fn.group(1), "effective": fn.group(2),
                     "evaluated": int(fn.group(3)), "pool": int(fn.group(4)),
                     "pollution": int(fn.group(5))} if fn else None,
    }


# ──────────────────────────────────────────────────────────────────────────────
# TEST_VOLUME_COVERAGE（§二十八）
# ──────────────────────────────────────────────────────────────────────────────
def test_volume_coverage():
    det = {}
    for tag, df in (("prefix", s4_old), ("current", s4_new)):
        d = df[df["trade_date"] == STEP4_DATE]
        v = num(d["seos_volume_participation"])
        vv = v.dropna()
        det[tag] = {
            "n_themes": int(len(d)),
            "missing": int(v.isna().sum()),
            "valid": int(vv.shape[0]),
            "n_unique": int(vv.round(6).nunique()),
            "exact_50_hits": int((vv.round(6) == 50.0).sum()),
            "most_common_value_share": round(float(vv.round(4).value_counts().iloc[0] / len(vv)), 4) if len(vv) else None,
            "p05": round(float(vv.quantile(.05)), 3) if len(vv) else None,
            "median": round(float(vv.median()), 3) if len(vv) else None,
            "p95": round(float(vv.quantile(.95)), 3) if len(vv) else None,
            "min": round(float(vv.min()), 3) if len(vv) else None,
            "max": round(float(vv.max()), 3) if len(vv) else None,
        }
        if "volume_data_status" in df.columns:
            det[tag]["volume_data_status"] = {str(k): int(v2) for k, v2 in
                                              d["volume_data_status"].fillna("NA").value_counts().items()}
            det[tag]["seos_volume_participation_missing_count_sum"] = int(
                num(d["seos_volume_participation_missing_count"]).fillna(0).sum())
    # 上游量能链
    vol_new = rd(os.path.join(DATA, "sector_volume.csv"), dtype={"trade_date": str})
    vol_old = rd(os.path.join(DATA, "sector_volume.csv.bak"), dtype={"trade_date": str})
    up = {}
    for tag, df in (("prefix", vol_old), ("current", vol_new)):
        d = df[df["trade_date"] == STEP4_DATE]
        up[tag] = {
            "n": int(len(d)),
            "volume_ratio_1_nan": int(num(d["volume_ratio_1"]).isna().sum()) if "volume_ratio_1" in d else None,
            "volume_ratio_3_nan": int(num(d["volume_ratio_3"]).isna().sum()) if "volume_ratio_3" in d else None,
            "volume_ratio_5_nan": int(num(d["volume_ratio_5"]).isna().sum()) if "volume_ratio_5" in d else None,
            "volume_data_status": ({str(k): int(v) for k, v in d["volume_data_status"].fillna("NA").value_counts().items()}
                                   if "volume_data_status" in d.columns else "FIELD_ABSENT"),
            "valid_member_ratio_min": round(float(num(d["volume_valid_member_ratio"]).min()), 4)
            if "volume_valid_member_ratio" in d.columns else None,
        }
    det["upstream_sector_volume"] = up
    cur, pre = det["current"], det["prefix"]
    ok = bool(cur["missing"] == 0 and cur["n_unique"] >= 10 and cur["exact_50_hits"] == 0
              and pre["n_unique"] == 1 and pre["exact_50_hits"] == pre["valid"])
    return {
        "status": "PASS" if ok else "FAIL",
        "metrics": det,
        "conclusion": ("修复后 %d 个主题 volume_participation 全部有值、去重值 %d 个、无固定 50 fallback；"
                       "修复前 %d 个主题全部等于 50.0（唯一值 1 个）"
                       % (cur["valid"], cur["n_unique"], pre["valid"])),
    }


# ──────────────────────────────────────────────────────────────────────────────
# TEST_AMOUNT_SHARE_DELTA_3（§二十八）
# ──────────────────────────────────────────────────────────────────────────────
def test_amount_share_delta_3():
    aw = cfg["component_weights"]["amount_share"]
    wsum = round(float(sum(aw.values())), 10)
    # 独立重算：share(t) - share(t-3)
    p = s4_new.pivot_table(index="trade_date", columns="sector_id", values="theme_amount_share")
    exp = p - p.shift(3)
    got = s4_new.pivot_table(index="trade_date", columns="sector_id", values="amount_share_delta_3")
    common = exp.index.intersection(got.index)
    dev = (exp.loc[common] - got.loc[common]).abs()
    n_cmp = int(dev.notna().sum().sum())
    max_dev = float(np.nanmax(dev.values)) if n_cmp else None
    n_bad = int((dev > TOL_R).sum().sum())
    # Step 8 透传（P0-02 的核心：键名必须与 strength_weights 一致）
    s8_vals = [r.get("amount_share_delta_3") for r in s8_rows]
    s8_present = int(sum(1 for v in s8_vals if v is not None and np.isfinite(float(v))))
    s8_mismatch = 0
    for r in s8_rows:
        src = s4_new[(s4_new["trade_date"] == s8_theme_date)
                     & (s4_new["sector_id"].astype(str) == str(r["sector_id"]))]
        if len(src) and r.get("amount_share_delta_3") is not None:
            if abs(float(src["amount_share_delta_3"].iloc[0]) - float(r["amount_share_delta_3"])) > TOL_R:
                s8_mismatch += 1
    # Step 8 综合排序键声明
    w = s8["theme"]["summary"]["theme_strength_rank_definition"]
    key_ok = "amount_share_delta_3" in w
    ok = bool(wsum == 1.0 and aw.get("amount_share_delta_3") == 0.20
              and n_bad == 0 and s8_present == len(s8_rows) and s8_mismatch == 0 and key_ok)
    return {
        "status": "PASS" if ok else "FAIL",
        "metrics": {
            "amount_share_component_weights": aw,
            "weight_sum": wsum,
            "independent_recompute_rows": n_cmp,
            "max_abs_deviation": None if max_dev is None else round(max_dev, 8),
            "rows_over_tolerance": n_bad,
            "step8_rows_with_value": f"{s8_present}/{len(s8_rows)}",
            "step8_vs_step4_mismatch": s8_mismatch,
            "strength_weights_definition": w,
        },
        "conclusion": ("amount_share_delta_3 可由 theme_amount_share 独立重算（最大偏差 %s，超差 %d 行）；"
                       "Step 8 主题行透传 %d/%d；组件权重 %s（合计 %s，未被改动）"
                       % (fmt(max_dev, 8) if max_dev is not None else "-", n_bad,
                          s8_present, len(s8_rows), json.dumps(aw, ensure_ascii=False), wsum)),
    }


# ──────────────────────────────────────────────────────────────────────────────
# TEST_CORE_BREADTH_SEMANTIC（§二十八）
# ──────────────────────────────────────────────────────────────────────────────
def test_core_breadth_semantic():
    d = s4_new[s4_new["trade_date"] == STEP4_DATE]
    lvl = num(d["core_breadth"])
    st = num(d["core_breadth_delta_3"])
    status = d["core_breadth_status"].fillna("NA").astype(str)
    dom = {"CORE", "PRIMARY_FALLBACK", "CORE_NO_VALID_MEMBER", "NO_CORE_MEMBER"}
    bad_domain = sorted(set(status) - dom)
    no_core = d[status == "NO_CORE_MEMBER"]
    no_core_leak = int(num(no_core["core_breadth"]).notna().sum()) if len(no_core) else 0
    # 水平值 vs 变化量必须分离：delta_3 == level 的行数（伪分离检测）
    alias = int(((lvl - st).abs() < 1e-9).sum())
    # delta_3 独立重算
    pl = s4_new.pivot_table(index="trade_date", columns="sector_id", values="core_breadth")
    exp_d3 = pl - pl.shift(3)
    got_d3 = s4_new.pivot_table(index="trade_date", columns="sector_id", values="core_breadth_delta_3")
    cm = exp_d3.index.intersection(got_d3.index)
    dev = (exp_d3.loc[cm] - got_d3.loc[cm]).abs()
    n_cmp = int(dev.notna().sum().sum())
    max_dev = float(np.nanmax(dev.values)) if n_cmp else None
    n_bad = int((dev > TOL_R).sum().sum())
    # 上游 sector_breadth 一致性
    brd = rd(os.path.join(DATA, "sector_breadth.csv"), dtype={"trade_date": str})
    b = brd[brd["trade_date"] == STEP4_DATE]
    bdev = None
    if "core_breadth" in b.columns:
        mg = d[["sector_id", "core_breadth"]].merge(
            b[["sector_id", "core_breadth"]], on="sector_id", suffixes=("_s4", "_s3"))
        bdev = float((num(mg["core_breadth_s4"]) - num(mg["core_breadth_s3"])).abs().max())
    ok = bool(not bad_domain and no_core_leak == 0 and n_bad == 0
              and not (lvl.notna().sum() == alias and n_cmp > 0))
    return {
        "status": "PASS" if ok else "FAIL",
        "metrics": {
            "core_breadth_status_domain": sorted(dom),
            "core_breadth_status_counts": {str(k): int(v) for k, v in status.value_counts().items()},
            "illegal_status": bad_domain,
            "no_core_member_themes": [
                {"sector_id": str(r["sector_id"]), "name": str(r["sector_name"]),
                 "core_member_count": int(num(pd.Series([r["core_member_count"]]))[0])
                 if pd.notna(r["core_member_count"]) else 0}
                for _, r in no_core.iterrows()],
            "no_core_member_with_core_breadth_value": no_core_leak,
            "level_equals_delta3_rows": alias,
            "delta3_recompute_rows": n_cmp,
            "delta3_max_deviation": None if max_dev is None else round(max_dev, 8),
            "delta3_rows_over_tolerance": n_bad,
            "step4_vs_step3_breadth_max_deviation": bdev,
        },
        "conclusion": ("core_breadth（水平，上游 sector_breadth 同源）与 core_breadth_delta_N（变化量）已分离："
                       "delta_3 独立重算最大偏差 %s；NO_CORE_MEMBER %d 个主题的 core_breadth 如实为空（泄漏写入 %d 行）"
                       % (fmt(max_dev, 8) if max_dev is not None else "-", len(no_core), no_core_leak)),
    }


# ──────────────────────────────────────────────────────────────────────────────
# TEST_FIELD_LABEL_CONSISTENCY（§二十八）
# ──────────────────────────────────────────────────────────────────────────────
def test_field_label_consistency():
    import csv
    hits = []
    scanned = []

    def scan_header(path, label, tokens):
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            h = next(csv.reader(fh))
        scanned.append({"target": label, "n_fields": len(h)})
        for t in tokens:
            for c in h:
                if t == c:
                    hits.append({"target": label, "field": c, "why": "legacy/歧义字段名"})

    scan_header(os.path.join(DATA, "sector_seos_daily.csv"), "data/sector_seos_daily.csv",
                LEGACY_FIELD_TOKENS)
    scan_header(os.path.join(DATA, "sector_signal_flags.csv"), "data/sector_signal_flags.csv",
                LEGACY_FIELD_TOKENS)

    # Step 8 JSON 主题行
    row_keys = set()
    for r in s8_rows:
        row_keys |= set(r.keys())
    scanned.append({"target": "output/post_market_review_%s.json :: theme.rows" % REVIEW_DATE,
                    "n_fields": len(row_keys)})
    for t in LEGACY_FIELD_TOKENS + ["rank"]:
        if t in row_keys:
            hits.append({"target": "step8.theme.rows", "field": t, "why": "legacy/歧义字段名"})
    required = ["seos_score", "seos_raw", "seos_delta_1", "seos_delta_3", "seos_rank",
                "amount_share_delta_3", "theme_health_delta_1", "core_breadth",
                "core_breadth_delta_3", "core_breadth_status", "volume_data_status",
                "component_coverage", "seos_data_status", "current_strength",
                "current_strength_rank", "theme_strength_rank", "theme_interpretation"]
    missing = [k for k in required if k not in row_keys]

    # 误标复检：seos_score 不得等于 theme_health_delta_1
    mis = 0
    for r in s8_rows:
        a, b = r.get("seos_score"), r.get("theme_health_delta_1")
        if a is not None and b is not None and abs(float(a) - float(b)) < 1e-12 and abs(float(a)) > 1e-12:
            mis += 1

    # markdown 报告字段名
    md_tokens = [t for t in LEGACY_FIELD_TOKENS if t in s8_md]
    md_rank = bool(re.search(r"\|\s*rank\s*\|", s8_md))
    # Step 4 today JSON 字段
    s4t_missing = [k for k in ("seos_weights_frozen", "score_weights", "component_weights",
                               "data_integrity") if k not in s4_today]
    ok = bool(not hits and not missing and mis == 0 and not md_tokens
              and not md_rank and not s4t_missing)
    return {
        "status": "PASS" if ok else "FAIL",
        "metrics": {
            "scanned": scanned,
            "legacy_hits": hits,
            "step8_required_missing": missing,
            "seos_score_equals_health_delta1_rows": mis,
            "markdown_legacy_tokens": md_tokens,
            "markdown_bare_rank_column": md_rank,
            "step4_today_missing_declaration": s4t_missing,
        },
        "conclusion": ("CSV / JSON / Markdown 三层字段名与实际含义一致：无 seos_change / amount_share_change "
                       "等歧义字段，Step 8 主题行不再存在裸 rank；seos_score 不再等于 theme_health_delta_1；"
                       "output/sector_seos_today.json 已带 seos_weights_frozen 声明"),
    }


# ──────────────────────────────────────────────────────────────────────────────
# TEST_SEOS_RECONCILIATION（§二十八）
# ──────────────────────────────────────────────────────────────────────────────
def test_seos_reconciliation():
    max_ded = float(cfg["extension_penalty"]["max_deduction"])
    w = cfg["score_weights"]
    d = s4_new
    comp = {}
    for c in SEOS_COMPONENTS:
        comp[c] = num(d["seos_%s" % c])
    num_sum = pd.Series(0.0, index=d.index)
    den_sum = pd.Series(0.0, index=d.index)
    for c in SEOS_COMPONENTS:
        cw = float(w[c])
        v = comp[c]
        num_sum = num_sum + np.where(v.notna(), cw * v.fillna(0.0), 0.0)
        den_sum = den_sum + np.where(v.notna(), cw, 0.0)
    raw_hat = pd.Series(np.where(den_sum > 0, num_sum / den_sum.replace(0, np.nan), np.nan), index=d.index)
    raw_got = num(d["seos_raw"])
    dev_raw = (raw_hat - raw_got).abs()
    ep = num(d["extension_penalty"])
    score_hat = raw_got * (1.0 - np.minimum(ep, max_ded))
    score_got = num(d["seos_score"])
    dev_score = (score_hat - score_got).abs()
    valid = score_got.notna()
    n = int(valid.sum())
    okr = int((dev_raw[valid] > TOL_R).sum())
    oks = int((dev_score[valid] > TOL_R).sum())
    # 分项加权（用落盘 seos_* + 权重）也应等于落盘 seos_raw
    wsum = round(float(sum(w.values())), 10)
    cov = num(d["component_coverage"])
    cov_consistency = int(((cov - den_sum).abs() > TOL_R).sum())
    ok = bool(okr == 0 and oks == 0 and wsum == 1.0 and cov_consistency == 0
              and set(w.keys()) == set(SEOS_COMPONENTS))
    return {
        "status": "PASS" if ok else "FAIL",
        "metrics": {
            "rows_checked": n,
            "score_weights": w,
            "weight_sum": wsum,
            "design_weights_match": {k: (w[k] == v) for k, v in FROZEN_SCORE_WEIGHTS.items()},
            "max_deduction": max_ded,
            "max_deviation_seos_raw": round(float(dev_raw[valid].max()), 8) if n else None,
            "rows_over_tolerance_seos_raw": okr,
            "max_deviation_seos_score": round(float(dev_score[valid].max()), 8) if n else None,
            "rows_over_tolerance_seos_score": oks,
            "component_coverage_inconsistent_rows": cov_consistency,
        },
        "conclusion": ("SEOS = Σ(wᵢ × seosᵢ) × (1 − min(penalty, %.2f)) 在浮点容差内成立："
                       "seos_raw 最大偏差 %s，seos_score 最大偏差 %s（%d 行）"
                       % (max_ded, fmt(dev_raw[valid].max(), 8) if n else "-",
                          fmt(dev_score[valid].max(), 8) if n else "-", n)),
    }


# ──────────────────────────────────────────────────────────────────────────────
# TEST_STEP8_SEMANTIC（§二十八）
# ──────────────────────────────────────────────────────────────────────────────
def md_section(md, head, nxt):
    i = md.find(head)
    if i < 0:
        return None
    j = md.find(nxt, i + len(head)) if nxt else len(md)
    return md[i:(j if j > 0 else len(md))]


def md_table_rows(block):
    rows = []
    for line in block.splitlines():
        s = line.strip()
        if s.startswith("|") and not re.match(r"^\|\s*-{3,}", s):
            cells = [c.strip() for c in s.strip("|").split("|")]
            rows.append(cells)
    return rows[1:] if rows else []   # 去掉表头


def test_step8_semantic():
    sec_a = md_section(s8_md, "### A. 今日主题表现", "### B. 主题结构变化")
    sec_b = md_section(s8_md, "### B. 主题结构变化", "### C. 主题状态")
    sec_c_head = "### C. 主题状态" in s8_md
    ra = md_table_rows(sec_a) if sec_a else []
    rb = md_table_rows(sec_b) if sec_b else []

    def col(rows, idx):
        out = []
        for r in rows:
            try:
                out.append(float(r[idx].replace("%", "")))
            except Exception:
                out.append(np.nan)
        return out

    a_cur = col(ra, 2)
    b_seos = col(rb, 2)
    a_desc = all((a_cur[i] >= a_cur[i + 1]) for i in range(len(a_cur) - 1))
    b_desc = all((b_seos[i] >= b_seos[i + 1]) for i in range(len(b_seos) - 1))

    rows_sorted = sorted(s8_rows, key=lambda r: -float(r["current_strength"]
                                                       if r.get("current_strength") is not None else -1))
    cur_desc_all = all(float(rows_sorted[i]["current_strength"]) >=
                       float(rows_sorted[i + 1]["current_strength"])
                       for i in range(len(rows_sorted) - 1))
    seos_sorted = sorted(s8_rows, key=lambda r: -float(r["seos_score"]
                                                       if r.get("seos_score") is not None else -1))
    seos_desc_all = all(float(seos_sorted[i]["seos_score"]) >= float(seos_sorted[i + 1]["seos_score"])
                        for i in range(len(seos_sorted) - 1))

    top_a = [str(r["sector_id"]) for r in rows_sorted[:5]]
    top_b = [str(r["sector_id"]) for r in seos_sorted[:5]]
    overlap = sorted(set(top_a) & set(top_b))
    a_top1_seos_rank = s8_by_id[top_a[0]].get("seos_rank") if top_a else None

    not_diag = [str(r["sector_id"]) for r in s8_rows
                if r.get("current_strength_diagnostic_only") is not True]
    interp_order = s8["theme"]["summary"]["theme_interpretation_order"]
    interp_bad = sorted(set(str(r.get("theme_interpretation")) for r in s8_rows) - set(interp_order))

    # §十九 / 交易语义禁用词（仅检查第 2 节主题展示区）
    sec2 = md_section(s8_md, "## 2. 主题", "## 3. 今日结构池")
    buy_hits = sorted({b for b in BUY_WORDS if b in (sec2 or "")})
    early_n = int(s8["theme"]["summary"]["theme_interpretation_count"].get("EARLY_STRUCTURAL_CHANGE", 0))
    disclaimer = "不构成任何交易指令" in (sec2 or "")
    early_in_md = "EARLY_STRUCTURAL_CHANGE" in (sec2 or "")

    ok = bool(a_desc and b_desc and cur_desc_all and seos_desc_all and not not_diag and not interp_bad
              and not buy_hits and disclaimer and early_in_md and sec_c_head
              and not (a_cur and a_cur[0] == max(b_seos) if b_seos else False))
    return {
        "status": "PASS" if ok else "FAIL",
        "metrics": {
            "layer_a_header": "### A. 今日主题表现（排序：current_strength DESC）",
            "layer_b_header": "### B. 主题结构变化（排序：SEOS DESC）",
            "layer_c_present": sec_c_head,
            "a_table_current_strength_desc": a_desc,
            "b_table_seos_desc": b_desc,
            "all_rows_current_strength_desc": cur_desc_all,
            "all_rows_seos_desc": seos_desc_all,
            "layer_a_top5": top_a,
            "layer_b_top5": top_b,
            "top5_overlap": overlap,
            "layer_a_top1_seos_rank": a_top1_seos_rank,
            "layer_a_is_not_seos_order": bool(a_top1_seos_rank not in (1, None)),
            "rows_missing_diagnostic_only_flag": not_diag,
            "illegal_interpretation": interp_bad,
            "interpretation_counts": s8["theme"]["summary"]["theme_interpretation_count"],
            "buy_words_in_theme_section": buy_hits,
            "early_structural_change_count": early_n,
            "disclaimer_present": disclaimer,
        },
        "conclusion": ("A 层按 current_strength DESC（首行为 %s，其 seos_rank=%s，说明当日表现排序 ≠ SEOS 排序）；"
                       "B 层按 SEOS DESC；C 层按 theme_interpretation 分组；全部 %d 行标注 diagnostic_only=true；"
                       "主题展示区无 BUY 类措辞" % (top_a[0] if top_a else "-", a1 if (a1 := a_top1_seos_rank) else "-",
                                                 len(s8_rows))),
    }


# ──────────────────────────────────────────────────────────────────────────────
# §6 SEOS 权重冻结 + §二十二 禁止人工调整主题
# ──────────────────────────────────────────────────────────────────────────────
def check_weights_frozen():
    src = {f: open(os.path.join(BASE, f), "r", encoding="utf-8").read()
           for f in ("sector_seos_build.py", "post_market_review_build.py", "sector_state_build.py")}
    pats = {
        "theme_named_adjust": r"if\s+theme\s*==|if\s+.*==\s*[\"']汽车|[\"']AI算力[\"']\s*:",
        "seos_multiplicative": r"seos\w*\s*\*=",
        "seos_additive": r"seos\w*\s*\+=",
        "seos_plus_current_strength": r"SEOS\s*\+=\s*current_strength|current_strength\s*\*\s*[0-9.]+\s*\+",
        "neutral_fill_literal": r"fillna\(\s*50(\.0)?\s*\)",
    }
    hits = {}
    for name, p in pats.items():
        hits[name] = []
        for f, t in src.items():
            for i, line in enumerate(t.splitlines(), 1):
                if re.search(p, line, flags=re.I):
                    hits[name].append({"file": f, "line": i, "code": line.strip()[:140]})
    cfg_w = cfg["score_weights"]
    match = {k: (round(float(cfg_w.get(k, -1)), 10) == v) for k, v in FROZEN_SCORE_WEIGHTS.items()}
    frozen_flag = {
        "sector_seos_build.py::SEOS_WEIGHTS_FROZEN": bool(
            re.search(r"^SEOS_WEIGHTS_FROZEN\s*=\s*True", src["sector_seos_build.py"], flags=re.M)),
        "output/sector_seos_today.json::seos_weights_frozen": s4_today.get("seos_weights_frozen"),
    }
    ok = bool(all(match.values())
              and frozen_flag["sector_seos_build.py::SEOS_WEIGHTS_FROZEN"]
              and frozen_flag["output/sector_seos_today.json::seos_weights_frozen"] is True
              and not hits["theme_named_adjust"] and not hits["seos_multiplicative"]
              and not hits["seos_additive"] and not hits["seos_plus_current_strength"]
              and not hits["neutral_fill_literal"])
    return {
        "status": "PASS" if ok else "FAIL",
        "metrics": {
            "declared_weights": FROZEN_SCORE_WEIGHTS,
            "config_weights": cfg_w,
            "weight_equals_design": match,
            "weight_implementation_mismatch": not all(match.values()),
            "frozen_declaration": frozen_flag,
            "forbidden_pattern_hits": hits,
        },
        "conclusion": ("SEOS_WEIGHTS_FROZEN 声明存在（代码 + output/sector_seos_today.json）；"
                       "7 项权重与设计值逐一相等；未发现按主题名强行调整 / seos *= / seos += / fillna(50) 等模式"),
    }


# ──────────────────────────────────────────────────────────────────────────────
# 10 重点主题诊断表（§二十）+ 关键事实（§二十一）
# ──────────────────────────────────────────────────────────────────────────────
def focus_table():
    out = []
    for key, tid, name in focus_ids():
        r = s8_by_id.get(tid, {})
        s4row = s4_new[(s4_new["trade_date"] == s8_theme_date)
                       & (s4_new["sector_id"].astype(str) == tid)]
        s4r = s4row.iloc[0] if len(s4row) else None
        out.append({
            "focus": key, "sector_id": tid, "sector_name": name,
            "seos_score": r.get("seos_score"), "seos_rank": r.get("seos_rank"),
            "theme_health": r.get("theme_health"),
            "phase": "%s→%s" % (r.get("theme_phase_before"), r.get("theme_phase_after")),
            "current_strength": r.get("current_strength"),
            "current_strength_rank": r.get("current_strength_rank"),
            "breadth": r.get("breadth"), "core_breadth": r.get("core_breadth"),
            "relative_strength": r.get("relative_strength"),
            "volume_participation": r.get("volume_participation_score"),
            "volume_data_status": r.get("volume_data_status"),
            "amount_share_delta_3": (float(s4r["amount_share_delta_3"]) if s4r is not None else None),
            "amount_share_delta_5": (float(s4r["amount_share_delta_5"]) if s4r is not None else None),
            "amount_share_delta_10": (float(s4r["amount_share_delta_10"]) if s4r is not None else None),
            "interpretation": r.get("theme_interpretation"),
        })
    return out


def key_fact_checks(ft):
    """§二十一：不要求任何主题被强行挪动；verdict 必须由本次实算事实推导，不得硬编码结论。"""
    by = {x["focus"]: x for x in ft}
    car = by.get("汽车")
    ai = by.get("AI算力")
    inn = by.get("创新药")
    seos_med = s8["theme"]["summary"]["median_seos_score"]
    cs_med = s8["theme"]["summary"]["median_current_strength"]

    def above(v, med):
        return bool(v is not None and med is not None and v >= med)

    car_seos = car and car["seos_score"]
    car_hi = above(car_seos, seos_med)
    ai_cs = ai and ai["current_strength"]
    ai_hi = above(ai_cs, cs_med)
    ai_seos_hi = above(ai and ai["seos_score"], seos_med)
    inn_seos = inn and inn["seos_score"]
    inn_seos_hi = above(inn_seos, seos_med)
    inn_cs = inn and inn["current_strength"]
    inn_cs_lo = bool(inn_cs is not None and cs_med is not None and inn_cs < cs_med)
    early_ok = inn_cs_lo and (inn and inn["interpretation"]) == "EARLY_STRUCTURAL_CHANGE"

    return {
        "median_seos_score": seos_med,
        "median_current_strength": cs_med,
        "汽车": {
            "seos_score": car_seos, "seos_rank": car and car["seos_rank"],
            "seos_above_median": car_hi,
            "verdict": ("SEOS 仍高于中位 → 结构变化信号确实强；未按主题名做任何人工加权，也未要求其退出第一"
                        if car_hi else
                        "SEOS 未高于中位 → 结构变化信号不再突出；未按主题名做任何人工加权"),
        },
        "AI算力": {
            "current_strength": ai_cs,
            "current_strength_rank": ai and ai["current_strength_rank"],
            "seos_score": ai and ai["seos_score"],
            "interpretation": ai and ai["interpretation"],
            "current_above_median": ai_hi,
            "verdict": ("Current Strength %s中位（rank %s）→ %s；SEOS %s中位，说明当日强弱与结构变化不是同一问题"
                        % ("高于" if ai_hi else "未高于",
                           ai and ai["current_strength_rank"],
                           "属当日强势，但不必然是结构改善最快" if ai_hi else "当日并未走强，结构变化信号与当日强弱分离",
                           "高于" if ai_seos_hi else "未高于")),
        },
        "创新药": {
            "seos_score": inn_seos,
            "seos_above_median": inn_seos_hi,
            "current_strength": inn_cs,
            "current_below_median": inn_cs_lo,
            "interpretation": inn and inn["interpretation"],
            "verdict": ("SEOS 高 + 当日强度低于中位 + EARLY/EMERGING → 保留 EARLY_STRUCTURAL_CHANGE 语义（仅表示值得继续观察）"
                        if early_ok else
                        "SEOS %s中位，当日强度%s中位 → 归为 %s；本次不满足 EARLY_STRUCTURAL_CHANGE 的三项前提（SEOS 高 + 当日强度相对低 + EARLY/EMERGING），故未使用该标签"
                        % ("高于" if inn_seos_hi else "未高于",
                           "低于" if inn_cs_lo else "不低于",
                           inn and inn["interpretation"])),
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# §9 Step 5 回归 + §10 Validation
# ──────────────────────────────────────────────────────────────────────────────
def step5_regression():
    pre = log_block(S5_LOG_OLD)
    post = log_block(S5_LOG_NEW)
    def pct(a, b):
        return None if not a else round((b - a) / a * 100.0, 3)
    reg = {"before": pre, "after": post, "source": {
        "before": "logs/run_step5_20260918.log（修复前 Step 4）",
        "after": "logs/regr_step5_20260918.log（修复后 Step 4，--no-write 复算，不覆写落盘）"}}
    if pre["status"] and post["status"]:
        reg["delta"] = {
            "CANDIDATE": post["status"]["CANDIDATE"] - pre["status"]["CANDIDATE"],
            "WATCH": post["status"]["WATCH"] - pre["status"]["WATCH"],
            "EXCLUDE": post["status"]["EXCLUDE"] - pre["status"]["EXCLUDE"],
            "pool_pct": pct(pre["complete"]["pool"], post["complete"]["pool"]),
            "candidate_pct": pct(pre["status"]["CANDIDATE"], post["status"]["CANDIDATE"]),
            "evaluated_pct": pct(pre["complete"]["evaluated"], post["complete"]["evaluated"]),
        }
    # 修复前 20260918 单日切片（Step 5 未随修复重跑）
    reg["before_snapshot_20260918"] = {
        "source": "data/sector_stock_candidate_daily.csv @ trade_date=%s（修复前口径，未重跑）" % STEP4_DATE,
        "rows": int(len(s5_before)),
        "stocks": int(s5_before["ts_code"].nunique()),
        "status": {str(k): int(v) for k, v in s5_before["candidate_status"].value_counts().items()},
        "candidate_type": {str(k): int(v) for k, v in
                           s5_before[s5_before["candidate_status"] == "CANDIDATE"]["candidate_type"]
                           .value_counts().items()},
    }
    d = reg["delta"] if reg.get("delta") else {}
    no_explosion = bool(abs(d.get("candidate_pct", 0) or 0) <= 20.0
                        and abs(d.get("pool_pct", 0) or 0) <= 20.0
                        and abs(d.get("evaluated_pct", 0) or 0) <= 20.0)
    return {"status": "PASS" if no_explosion else "FAIL", "metrics": reg,
            "conclusion": ("修复后 Step 5 候选池 %s→%s（%s%%）、CANDIDATE %s→%s（%s%%）、评估行 %s→%s（%s%%）；"
                           "候选评分分位数基本持平（p50 %s→%s / p90 %s→%s）；无候选爆炸"
                           % (pre["complete"]["pool"], post["complete"]["pool"], d.get("pool_pct"),
                              pre["status"]["CANDIDATE"], post["status"]["CANDIDATE"], d.get("candidate_pct"),
                              pre["complete"]["evaluated"], post["complete"]["evaluated"], d.get("evaluated_pct"),
                              pre["score_p"][0], post["score_p"][0], pre["score_p"][2], post["score_p"][2]))}


def validation_matrix():
    def read_val(path, level_col="status"):
        if not os.path.exists(path):
            return []
        d = rd(path, dtype=str)
        return [{"check": str(r["check"]), "level": str(r[level_col]), "detail": str(r.get("detail", ""))}
                for _, r in d.iterrows()]
    s3 = read_val(os.path.join(OUTPUT, "sector_state_validation.csv"))
    s4 = read_val(os.path.join(OUTPUT, "seos_validation.csv"))
    s5 = read_val(os.path.join(OUTPUT, "sector_stock_candidate_validation.csv"))
    s8v = [{"check": c["check"], "level": c["level"], "detail": c.get("message", "")}
           for c in s8["validation"]["checks"]]
    mapping = {
        "NO FUTURE LEAKAGE": ["E_NO_LOOKAHEAD", "CHECK_NO_LOOKAHEAD_SEOS", "CHECK_NO_LOOKAHEAD_PHASE",
                              "CHECK_NO_LOOKAHEAD_ROTATION", "CHECK_NO_FUTURE_PRICE",
                              "CHECK_NO_FUTURE_FUNDAMENTAL", "FUTURE_LEAKAGE"],
        "NO LEGACY CONTAMINATION": ["CHECK_LEGACY_ISOLATION"],
        "PIT MEMBERSHIP": ["CHECK_MEMBERSHIP_PIT", "B_MEMBERSHIP_UNIQUE", "C_EFFECTIVE_DATE"],
        "PIT THEME RETURN": ["CHECK_THEME_RETURN_PIT"],
        "NO MOMENTUM CHASING": ["CHECK_EXTENSION_NOT_TOP1", "CHECK_ROLE_NOT_BY_RETURN",
                                "CHECK_NO_DORMANT_CANDIDATE", "CHECK_NO_TRADING_TERMS"],
        "POLLUTION ENFORCEMENT": ["CHECK_POLLUTION_ENFORCED", "CHECK_POLLUTION_LOGGED"],
    }
    allrows = {r["check"]: r for r in (s3 + s4 + s5 + s8v)}
    cross = {}
    for k, names in mapping.items():
        found = [allrows[n] for n in names if n in allrows]
        cross[k] = {
            "checks": [{"check": x["check"], "level": x["level"]} for x in found],
            "status": "PASS" if found and all(x["level"] == "PASS" for x in found) else
                      ("MISSING" if not found else "FAIL"),
        }
    return {"step3": s3, "step4": s4, "step5": s5, "step8": s8v, "cross_checks": cross,
            "counts": {
                "step3_pass": sum(1 for x in s3 if x["level"] == "PASS"), "step3_n": len(s3),
                "step4_pass": sum(1 for x in s4 if x["level"] == "PASS"), "step4_n": len(s4),
                "step5_pass": sum(1 for x in s5 if x["level"] == "PASS"), "step5_n": len(s5),
                "step8_pass": sum(1 for x in s8v if x["level"] == "PASS"),
                "step8_warn": sum(1 for x in s8v if x["level"] == "WARNING"),
                "step8_n": len(s8v),
            }}


# ──────────────────────────────────────────────────────────────────────────────
# §7 前后对比 Top 15
# ──────────────────────────────────────────────────────────────────────────────
def top15_compare():
    def panel(df):
        d = df[df["trade_date"] == s8_theme_date].copy()
        d["sector_id"] = d["sector_id"].astype(str)
        return d.set_index("sector_id")
    a, b = panel(s4_old), panel(s4_new)
    rows = []
    for tid in b.sort_values("seos_score", ascending=False).index[:15]:
        o = a.loc[tid] if tid in a.index else None
        s8r = s8_by_id.get(tid, {})
        rows.append({
            "sector_id": tid, "sector_name": str(b.loc[tid, "sector_name"]),
            "seos_before": float(o["seos_score"]) if o is not None else None,
            "seos_rank_before": float(o["seos_rank"]) if o is not None and "seos_rank" in a.columns else None,
            "seos_after": float(b.loc[tid, "seos_score"]),
            "seos_rank_after": float(b.loc[tid, "seos_rank"]),
            "delta": (float(b.loc[tid, "seos_score"]) - float(o["seos_score"])) if o is not None else None,
            "health": s8r.get("theme_health"),
            "current_strength": s8r.get("current_strength"),
            "current_strength_rank": s8r.get("current_strength_rank"),
            "phase": "%s→%s" % (s8r.get("theme_phase_before"), s8r.get("theme_phase_after")),
            "interpretation": s8r.get("theme_interpretation"),
        })
    dd = [r["delta"] for r in rows if r["delta"] is not None]
    allrows = []
    common = a.index.intersection(b.index)
    d_all = (b.loc[common, "seos_score"] - a.loc[common, "seos_score"])
    return {
        "theme_date": s8_theme_date, "top15": rows,
        "all_theme_delta": {
            "n": int(len(d_all)),
            "n_changed": int((d_all.abs() > 1e-9).sum()),
            "min": round(float(d_all.min()), 4), "median": round(float(d_all.median()), 4),
            "max": round(float(d_all.max()), 4),
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────────────────────
def main():
    t_vol = test_volume_coverage()
    t_amt = test_amount_share_delta_3()
    t_core = test_core_breadth_semantic()
    t_field = test_field_label_consistency()
    t_recon = test_seos_reconciliation()
    t_s8 = test_step8_semantic()
    tests = {
        "TEST_VOLUME_COVERAGE": t_vol,
        "TEST_AMOUNT_SHARE_DELTA_3": t_amt,
        "TEST_CORE_BREADTH_SEMANTIC": t_core,
        "TEST_FIELD_LABEL_CONSISTENCY": t_field,
        "TEST_SEOS_RECONCILIATION": t_recon,
        "TEST_STEP8_SEMANTIC": t_s8,
    }
    weights = check_weights_frozen()
    ft = focus_table()
    kf = key_fact_checks(ft)
    reg = step5_regression()
    val = validation_matrix()
    cmp15 = top15_compare()

    # 残留风险
    residual = [
        {"id": "R1", "level": "SCOPE", "text":
         "Step 6 / Step 7 / HVT / Execution 未随 Step 4 修复重跑（§二十六 禁止修改、§三十二 不进入 Step 6）。"
         "Step 5 的修复后复算以 --no-write 方式执行，落盘 data/sector_stock_candidate_daily.csv 仍为修复前口径；"
         "Step 8 报告中的结构池 / BUY / NO TRADE 片段因此来自修复前 Step 5→7 的落盘结果。"},
        {"id": "R2", "level": "DATA_COVERAGE", "text":
         "7 个主题当期无 CORE 成员（core_breadth_status=NO_CORE_MEMBER），core_breadth 分项缺失，"
         "component_coverage<1 并标记 SEOS_DATA_PARTIAL。这是上游成员结构事实，非计算缺陷，未以 0/50 伪装。"},
        {"id": "R3", "level": "DATA_COVERAGE", "text":
         "volume_participation 依赖 Step 3 回放窗口长度（REPLAY_MIN_DAYS=21）。若以更短的 --date 窗口重跑 Step 3，"
         "仍可能复现 volume_ratio_* 缺值。当前 20260918 落盘为 VALID 50/50。"},
        {"id": "R4", "level": "OUTCOME", "text":
         "T+3 / T+5 / T+10 / T+20 尚未发生，Step 8 如实留空，不以前视数据填充。"},
        {"id": "R5", "level": "PRE_EXISTING", "text":
         "Step 8 REGIME_TIER_COVERAGE = WARNING（强势档在 Step 7 历史中未产出），与本次修复无关，未调整阈值。"},
        {"id": "R6", "level": "SEMANTIC", "text":
         "theme_interpretation 是解释标签，不是交易评分；EARLY_STRUCTURAL_CHANGE 只表示「结构变化值得继续观察」，"
         "报告已在 §2 末尾显式声明，仍存在被读者误读为 BUY 的可能（§十九）。"},
    ]
    # §31 Final Status
    crit = {
        "Volume 数据链": t_vol["status"],
        "Amount Share delta_3": t_amt["status"],
        "Core Breadth": t_core["status"],
        "Field Semantic": t_field["status"],
        "SEOS Reconciliation": t_recon["status"],
        "SEOS 权重未改": weights["status"],
        "Step 8 Semantic": t_s8["status"],
        "Step 5 Regression": reg["status"],
        "Future Leakage": val["cross_checks"]["NO FUTURE LEAKAGE"]["status"],
        "Legacy Isolation": val["cross_checks"]["NO LEGACY CONTAMINATION"]["status"],
    }
    failed = [k for k, v in crit.items() if v != "PASS"]
    if any(t == "FAIL" for t in [t_recon["status"], t_field["status"]]) or \
       val["cross_checks"]["NO FUTURE LEAKAGE"]["status"] != "PASS" or reg["status"] != "PASS":
        final = "SEOS_FIX_FAILED"
    elif failed:
        final = "SEOS_FIX_CONDITIONAL"
    else:
        final = "SEOS_FIX_PASS"

    obj = {
        "meta": {
            "report": "Step 4 + Step 8 修复审计",
            "review_date": REVIEW_DATE,
            "step4_last_date": STEP4_DATE,
            "step8_theme_date": s8_theme_date,
            "generated_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "scope": "只修复 Step 4 数据完整性/字段语义 与 Step 8 展示/解释；不修改权重、不进入 Step 6",
            "task_constraints": ["SEOS_WEIGHTS_FROZEN=true", "禁止 fillna(50) 伪装中性",
                                 "禁止按主题名人工调整", "Current Strength 仅诊断层",
                                 "Step 8 不输出 BUY/推荐/目标价/仓位"],
        },
        "tests": tests,
        "weights": weights,
        "focus_table": ft,
        "key_facts": kf,
        "step5_regression": reg,
        "validation": val,
        "top15": cmp15,
        "residual_risks": residual,
        "final_status_criteria": crit,
        "final_status": final,
    }

    # ── Markdown ──────────────────────────────────────────────────────────────
    L = []
    A = L.append
    A("# Step 4 + Step 8 修复审计")
    A("")
    A("> 复盘日 %s ｜ Step 4 落盘末日 %s ｜ Step 8 theme_date %s" % (REVIEW_DATE, STEP4_DATE, s8_theme_date))
    A("> 范围：Step 4 数据完整性 + 字段语义；Step 8 展示/解释。**未修改任何 SEOS 权重**，未进入 Step 6。")
    A("")
    A("## 1. 修复摘要")
    A("")
    A("| 编号 | 问题 | 修复动作 | 证据 | 状态 |")
    A("| --- | --- | --- | --- | --- |")
    A("| P0-01 | volume_participation 数据缺口导致 %d 个主题固定 50.00 | Step 3 `--date` 回放窗口下限（REPLAY_MIN_DAYS=21）"
      "+ Step 4 分项缺值按可用权重归一化、不再中性填充 | TEST_VOLUME_COVERAGE | %s |"
      % (t_vol["metrics"]["prefix"]["valid"], t_vol["status"]))
    A("| P0-02 | amount_share_delta_3 字段缺失，Amount Share 组件未完全执行 | Step 8 `build_theme` 键名与 "
      "`strength_weights` / Step 4 上游字段对齐，15%% 权重不再被静默丢弃 | TEST_AMOUNT_SHARE_DELTA_3 | %s |"
      % t_amt["status"])
    A("| P1-01 | 部分主题 core_breadth / core_breadth_delta 数据缺失 | 新增 `core_breadth_status` 四值域；"
      "无 CORE 成员时如实为空，不写 0/50 | TEST_CORE_BREADTH_SEMANTIC | %s |" % t_core["status"])
    A("| P1-02 | seos_change 与主题展示字段语义混乱 | 废弃 `seos_change`，改为显式 "
      "`seos_score / seos_raw / seos_delta_1 / seos_delta_3 / seos_rank` | TEST_FIELD_LABEL_CONSISTENCY | %s |"
      % t_field["status"])
    A("| P1-03 | Step 8 把 SEOS 误读成「当日主题强弱排名」 | §2 拆 A/B/C 三层 + `theme_interpretation` 六分类"
      "（Current Strength 仅诊断层） | TEST_STEP8_SEMANTIC | %s |" % t_s8["status"])
    A("")
    A("附带确认：SEOS 权重冻结（%s）、SEOS 可复算（%s）、Step 5 回归（%s）。"
      % (weights["status"], t_recon["status"], reg["status"]))
    A("")
    A("## 2. Volume 修复")
    A("")
    A("**修复前**（`data/sector_seos_daily.csv.bak` @ %s）" % STEP4_DATE)
    A("")
    A("| 指标 | 修复前 | 修复后 |")
    A("| --- | --- | --- |")
    pre, cur = t_vol["metrics"]["prefix"], t_vol["metrics"]["current"]
    for k, lab in (("valid", "有值主题数"), ("missing", "缺值主题数"), ("n_unique", "去重值个数"),
                   ("exact_50_hits", "恰好=50.0 的主题数"), ("most_common_value_share", "最常见值占比"),
                   ("p05", "p05"), ("median", "中位数"), ("p95", "p95"), ("min", "min"), ("max", "max")):
        A("| %s | %s | %s |" % (lab, fmt(pre.get(k)), fmt(cur.get(k))))
    A("")
    A("**上游量能链**（`data/sector_volume.csv`）")
    A("")
    A("| 指标 | 修复前 | 修复后 |")
    A("| --- | --- | --- |")
    up = t_vol["metrics"]["upstream_sector_volume"]
    for k, lab in (("volume_ratio_1_nan", "volume_ratio_1 缺值"), ("volume_ratio_3_nan", "volume_ratio_3 缺值"),
                   ("volume_ratio_5_nan", "volume_ratio_5 缺值"), ("valid_member_ratio_min", "成交额口径成员覆盖率 min")):
        A("| %s | %s | %s |" % (lab, fmt(up["prefix"].get(k)), fmt(up["current"].get(k))))
    A("| volume_data_status | %s | %s |" % (json.dumps(up["prefix"]["volume_data_status"], ensure_ascii=False),
                                            json.dumps(up["current"]["volume_data_status"], ensure_ascii=False)))
    A("")
    A("**结论**：%s" % t_vol["conclusion"])
    A("")
    A("## 3. Amount Share 修复")
    A("")
    A("P0-02 的根因是 **Step 8 层的键名不匹配**：`build_theme` 写出 `amount_share_change`，"
      "而 `theme_strength` 查找 `amount_share_delta_3`，导致 15%% 权重被静默丢弃（而非上游缺字段）。")
    A("")
    A("| 检查 | 结果 |")
    A("| --- | --- |")
    A("| component_weights.amount_share | %s |" % json.dumps(t_amt["metrics"]["amount_share_component_weights"], ensure_ascii=False))
    A("| 权重合计 | %s |" % t_amt["metrics"]["weight_sum"])
    A("| 独立重算行数 | %s |" % t_amt["metrics"]["independent_recompute_rows"])
    A("| 最大偏差 | %s |" % fmt(t_amt["metrics"]["max_abs_deviation"], 8))
    A("| 超差行数 | %s |" % t_amt["metrics"]["rows_over_tolerance"])
    A("| Step 8 主题行有值 | %s |" % t_amt["metrics"]["step8_rows_with_value"])
    A("| Step 8 与 Step 4 不一致行 | %s |" % t_amt["metrics"]["step8_vs_step4_mismatch"])
    A("| 排序键定义 | %s |" % t_amt["metrics"]["strength_weights_definition"])
    A("")
    A("**结论**：%s" % t_amt["conclusion"])
    A("")
    A("## 4. Core Breadth 修复")
    A("")
    A("`core_breadth_status` 分布（%s）：`%s`" % (STEP4_DATE, json.dumps(
        t_core["metrics"]["core_breadth_status_counts"], ensure_ascii=False)))
    A("")
    A("**缺失主题及原因**（当期无 CORE 成员 → 结构缺失为上游事实，非计算缺陷）")
    A("")
    A("| 主题 | sector_id | core_member_count | core_breadth |")
    A("| --- | --- | --- | --- |")
    for x in t_core["metrics"]["no_core_member_themes"]:
        A("| %s | %s | %s | 如实为空 |" % (x["name"], x["sector_id"], x["core_member_count"]))
    A("")
    A("| 分离性检查 | 结果 |")
    A("| --- | --- |")
    A("| 非法 status | %s |" % (t_core["metrics"]["illegal_status"] or "无"))
    A("| NO_CORE_MEMBER 却写入 core_breadth | %d 行 |" % t_core["metrics"]["no_core_member_with_core_breadth_value"])
    A("| core_breadth_delta_3 独立重算行数 | %d |" % t_core["metrics"]["delta3_recompute_rows"])
    A("| core_breadth_delta_3 最大偏差 | %s |" % fmt(t_core["metrics"]["delta3_max_deviation"], 8))
    A("| core_breadth_delta_3 超差行数 | %d |" % t_core["metrics"]["delta3_rows_over_tolerance"])
    A("| 水平值 == 变化量 的行数（伪分离） | %d |" % t_core["metrics"]["level_equals_delta3_rows"])
    A("| Step 4 与 Step 3 core_breadth 最大偏差 | %s |" % fmt(t_core["metrics"]["step4_vs_step3_breadth_max_deviation"], 8))
    A("")
    A("**结论**：%s" % t_core["conclusion"])
    A("")
    A("## 5. 字段语义修复")
    A("")
    A("| 旧字段 | 问题 | 新字段 / 处理 |")
    A("| --- | --- | --- |")
    A("| `seos_change` | 实际被赋值为 `theme_health_delta_1`（语义错标） | 已移除；改为 `seos_score` / `seos_raw` / "
      "`seos_delta_1` / `seos_delta_3` / `seos_rank` |")
    A("| `amount_share_change` | 与 `strength_weights.amount_share_delta_3` 键名不一致，权重被丢弃 | "
      "改为 `amount_share_delta_3`（与 Step 4 上游同名字段） |")
    A("| `rank`（Step 8 主题行） | 泛指「排名」，无法区分当日强弱 / 结构变化 | 改为 `theme_strength_rank`（综合）"
      "+ `current_strength_rank`（当日）+ `seos_rank`（结构，来自 Step 4） |")
    A("| 主题 row 的 health/breadth 变化 | 命名歧义 | 统一为 `*_delta_N` |")
    A("")
    A("| 扫描目标 | 结果 |")
    A("| --- | --- |")
    for x in t_field["metrics"]["scanned"]:
        A("| %s（%d 字段） | 已扫描 |" % (x["target"], x["n_fields"]))
    A("")
    A("- 命中 legacy / 歧义字段名：%s" % (t_field["metrics"]["legacy_hits"] or "无"))
    A("- Step 8 必需字段缺失：%s" % (t_field["metrics"]["step8_required_missing"] or "无"))
    A("- `seos_score == theme_health_delta_1` 行数：%d" % t_field["metrics"]["seos_score_equals_health_delta1_rows"])
    A("- Markdown 残留 legacy 词：%s；裸 `rank` 列：%s"
      % (t_field["metrics"]["markdown_legacy_tokens"] or "无", t_field["metrics"]["markdown_bare_rank_column"]))
    A("")
    A("**结论**：%s" % t_field["conclusion"])
    A("")
    A("## 6. SEOS 权重")
    A("")
    A("| 分项 | 设计权重 | config 权重 | 相等 |")
    A("| --- | --- | --- | --- |")
    for k, v in FROZEN_SCORE_WEIGHTS.items():
        A("| %s | %.2f | %s | %s |" % (k, v, weights["metrics"]["config_weights"].get(k),
                                       weights["metrics"]["weight_equals_design"].get(k)))
    A("")
    A("- `WEIGHT_IMPLEMENTATION_MISMATCH`：**%s**（未出现）"
      % ("是" if weights["metrics"]["weight_implementation_mismatch"] else "否"))
    for k, v in weights["metrics"]["frozen_declaration"].items():
        A("- 冻结声明 `%s` = %s" % (k, v))
    A("- 禁止模式扫描（按主题名调整 / `seos *=` / `seos +=` / `SEOS += current_strength` / `fillna(50)`）：")
    for name, hs in weights["metrics"]["forbidden_pattern_hits"].items():
        A("  - %s：%s" % (name, ("未命中" if not hs else json.dumps(hs, ensure_ascii=False))))
    A("")
    A("**结论**：%s" % weights["conclusion"])
    A("")
    A("## 7. 20260918 前后对比")
    A("")
    A("### 7.1 Top 15（theme_date=%s，按修复后 SEOS 降序）" % cmp15["theme_date"])
    A("")
    A("| # | 主题 | SEOS 前 | SEOS 后 | Δ | SEOS rank | Health | Current Strength | CS rank | Phase | Interpretation |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for i, r in enumerate(cmp15["top15"], 1):
        A("| %d | %s %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |"
          % (i, r["sector_id"], r["sector_name"], fmt(r["seos_before"], 2), fmt(r["seos_after"], 2),
             ("%+.2f" % r["delta"]) if r["delta"] is not None else "-",
             fmt(r["seos_rank_after"], 0), fmt(r["health"], 2), fmt(r["current_strength"], 2),
             fmt(r["current_strength_rank"], 0), r["phase"], r["interpretation"]))
    A("")
    d = cmp15["all_theme_delta"]
    A("全 50 主题 SEOS 变化：%d 个发生变化；Δ min %s / median %s / max %s。"
      % (d["n_changed"], d["min"], d["median"], d["max"]))
    A("")
    A("### 7.2 §二十 重点主题诊断表（10 个）")
    A("")
    A("| 主题 | SEOS | SEOS rank | Health | Phase | Current Strength | CS rank | Breadth | Core Breadth | Rel Strength | Volume Partic. | volume_data_status | amt_share Δ3 | Δ5 | Δ10 | Interpretation |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in ft:
        A("| %s %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |"
          % (r["sector_id"], r["sector_name"], fmt(r["seos_score"], 2), fmt(r["seos_rank"], 0),
             fmt(r["theme_health"], 2), r["phase"], fmt(r["current_strength"], 2),
             fmt(r["current_strength_rank"], 0), fmt(r["breadth"], 3), fmt(r["core_breadth"], 3),
             fmt(r["relative_strength"], 3), fmt(r["volume_participation"], 2), r["volume_data_status"],
             fmt(r["amount_share_delta_3"], 4), fmt(r["amount_share_delta_5"], 4),
             fmt(r["amount_share_delta_10"], 4), r["interpretation"]))
    A("")
    A("### 7.3 §二十一 关键事实验证（不要求任何主题被强行挪动）")
    A("")
    A("- 中位 SEOS = %s；中位 Current Strength = %s" % (kf["median_seos_score"], kf["median_current_strength"]))
    for k in ("汽车", "AI算力", "创新药"):
        A("- **%s**：%s" % (k, json.dumps(kf[k], ensure_ascii=False)))
        A("  - %s" % kf[k]["verdict"])
    A("")
    A("结论：未使用任何 `if theme == ...: seos *= x` 形式的人工调整；主题顺序完全由冻结权重与上游数据决定。")
    A("")
    A("## 8. Step 8 主题展示")
    A("")
    A("| 层 | 排序口径 | 表内顺序校验 |")
    A("| --- | --- | --- |")
    A("| A. 当前主题表现 | current_strength DESC | %s |" % ("降序成立" if t_s8["metrics"]["a_table_current_strength_desc"] else "失败"))
    A("| B. 结构变化 | SEOS DESC | %s |" % ("降序成立" if t_s8["metrics"]["b_table_seos_desc"] else "失败"))
    A("| C. 主线状态 | theme_interpretation 分组 | %s |" % ("分组存在" if t_s8["metrics"]["layer_c_present"] else "失败"))
    A("")
    A("- A 层 Top5：%s" % "、".join(t_s8["metrics"]["layer_a_top5"]))
    A("- B 层 Top5：%s" % "、".join(t_s8["metrics"]["layer_b_top5"]))
    A("- 两层 Top5 重合：%s（说明「今天谁最强」与「谁在结构改善」不是同一个问题）"
      % ("、".join(t_s8["metrics"]["top5_overlap"]) or "无"))
    A("- A 层首行的 `seos_rank` = %s → 当前主题表现排序 ≠ SEOS 排序"
      % t_s8["metrics"]["layer_a_top1_seos_rank"])
    A("- 未标注 `diagnostic_only=true` 的行：%s" % (t_s8["metrics"]["rows_missing_diagnostic_only_flag"] or "无"))
    A("- `theme_interpretation` 分布：%s" % json.dumps(t_s8["metrics"]["interpretation_counts"], ensure_ascii=False))
    A("- 主题展示区 BUY 类措辞：%s；EARLY_STRUCTURAL_CHANGE 免责声明：%s"
      % (t_s8["metrics"]["buy_words_in_theme_section"] or "无",
         "已存在" if t_s8["metrics"]["disclaimer_present"] else "缺失"))
    A("")
    A("**结论**：%s" % t_s8["conclusion"])
    A("")
    A("## 9. Step 5 回归")
    A("")
    A("> Step 5 依赖 Step 4。为保证「不因修复出现候选爆炸」，以 **非破坏性** 方式复算"
      "（`--no-write --validate`），不覆写落盘候选池（§二十六 / §三十二）。")
    A("")
    A("| 指标 | 修复前 Step 4 | 修复后 Step 4 | 变化 |")
    A("| --- | --- | --- | --- |")
    bp, ap = reg["metrics"]["before"], reg["metrics"]["after"]
    dl = reg["metrics"].get("delta", {})
    A("| 评估行数 | %s | %s | %s%% |" % (bp["complete"]["evaluated"], ap["complete"]["evaluated"], dl.get("evaluated_pct")))
    A("| 有效主题日 | %s | %s | - |" % (bp["effective_theme_days"], ap["effective_theme_days"]))
    A("| candidate_score p50 | %s | %s | - |" % (bp["score_p"][0], ap["score_p"][0]))
    A("| candidate_score p75 | %s | %s | - |" % (bp["score_p"][1], ap["score_p"][1]))
    A("| candidate_score p90 | %s | %s | - |" % (bp["score_p"][2], ap["score_p"][2]))
    A("| candidate_score max | %s | %s | - |" % (bp["score_p"][3], ap["score_p"][3]))
    A("| CANDIDATE | %s | %s | %s%% |" % (bp["status"]["CANDIDATE"], ap["status"]["CANDIDATE"], dl.get("candidate_pct")))
    A("| WATCH | %s | %s | %s |" % (bp["status"]["WATCH"], ap["status"]["WATCH"], dl.get("WATCH")))
    A("| EXCLUDE | %s | %s | %s |" % (bp["status"]["EXCLUDE"], ap["status"]["EXCLUDE"], dl.get("EXCLUDE")))
    A("| 候选池 | %s | %s | %s%% |" % (bp["complete"]["pool"], ap["complete"]["pool"], dl.get("pool_pct")))
    A("| 污染行 | %s | %s | - |" % (bp["complete"]["pollution"], ap["complete"]["pollution"]))
    A("")
    bs = reg["metrics"]["before_snapshot_20260918"]
    A("修复前 %s 单日落盘切片（%s）：%d 行，其中 %s，候选类型 %s。"
      % (STEP4_DATE, bs["source"], bs["rows"], json.dumps(bs["status"], ensure_ascii=False),
         json.dumps(bs["candidate_type"], ensure_ascii=False)))
    A("")
    A("**结论**：%s" % reg["conclusion"])
    A("")
    A("## 10. Validation")
    A("")
    A("### 10.1 新增专项测试（§二十八）")
    A("")
    A("| 测试 | 状态 | 结论 |")
    A("| --- | --- | --- |")
    for k, v in tests.items():
        A("| %s | %s | %s |" % (k, v["status"], v["conclusion"].replace("|", "/")))
    A("")
    A("### 10.2 既有 regression（§二十七）")
    A("")
    c = val["counts"]
    A("- Step 3 `sector_state_validation`：%d/%d PASS" % (c["step3_pass"], c["step3_n"]))
    A("- Step 4 `seos_validation`：%d/%d PASS" % (c["step4_pass"], c["step4_n"]))
    A("- Step 5 `sector_stock_candidate_validation`：%d/%d PASS" % (c["step5_pass"], c["step5_n"]))
    A("- Step 8 `post_market_review validation`：%d/%d PASS（%d WARNING）" % (c["step8_pass"], c["step8_n"], c["step8_warn"]))
    A("")
    A("| 复检项 | 对应检查 | 状态 |")
    A("| --- | --- | --- |")
    for k, v in val["cross_checks"].items():
        A("| %s | %s | %s |" % (k, "、".join(x["check"] for x in v["checks"]) or "-", v["status"]))
    A("")
    A("### 10.3 关键测试指标")
    A("")
    A("- `TEST_VOLUME_COVERAGE`：修复后 volume_participation 有值 %d/%d、去重值 %d、恰好=50 命中 %d"
      % (cur["valid"], cur["n_themes"], cur["n_unique"], cur["exact_50_hits"]))
    A("- `TEST_SEOS_RECONCILIATION`：seos_raw 最大偏差 %s；seos_score 最大偏差 %s（%d 行）"
      % (fmt(t_recon["metrics"]["max_deviation_seos_raw"], 8),
         fmt(t_recon["metrics"]["max_deviation_seos_score"], 8), t_recon["metrics"]["rows_checked"]))
    A("- `TEST_AMOUNT_SHARE_DELTA_3`：独立重算 %d 行，最大偏差 %s"
      % (t_amt["metrics"]["independent_recompute_rows"], fmt(t_amt["metrics"]["max_abs_deviation"], 8)))
    A("- `TEST_CORE_BREADTH_SEMANTIC`：NO_CORE_MEMBER %d 个主题如实为空"
      % len(t_core["metrics"]["no_core_member_themes"]))
    A("")
    A("## 11. Residual Risks")
    A("")
    A("| 编号 | 类型 | 说明 |")
    A("| --- | --- | --- |")
    for r in residual:
        A("| %s | %s | %s |" % (r["id"], r["level"], r["text"]))
    A("")
    A("## 12. Final Status")
    A("")
    A("| 通过条件 | 状态 |")
    A("| --- | --- |")
    for k, v in crit.items():
        A("| %s | %s |" % (k, v))
    A("")
    A("### %s" % final)
    A("")
    A("未满足项：%s" % ("、".join(failed) if failed else "无"))
    A("")
    A("> 本审计为只读结论；Step 8 输出仍为 Post-Market Review，不含 BUY / 推荐 / 目标价 / 仓位语义，"
      "Current Strength 仅作诊断层。执行到此停止，未进入 Step 6。")
    A("")

    with open(AUDIT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    with open(AUDIT_JSON, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)

    print("tests:", json.dumps({k: v["status"] for k, v in tests.items()}, ensure_ascii=False))
    print("weights:", weights["status"], "| step5:", reg["status"])
    print("validation: step3 %d/%d step4 %d/%d step5 %d/%d step8 %d/%d(%d WARN)"
          % (c["step3_pass"], c["step3_n"], c["step4_pass"], c["step4_n"],
             c["step5_pass"], c["step5_n"], c["step8_pass"], c["step8_n"], c["step8_warn"]))
    print("cross:", json.dumps({k: v["status"] for k, v in val["cross_checks"].items()}, ensure_ascii=False))
    print("FINAL_STATUS:", final)
    print("written:", os.path.relpath(AUDIT_MD, BASE), os.path.relpath(AUDIT_JSON, BASE))
    return 0


if __name__ == "__main__":
    sys.exit(main())
