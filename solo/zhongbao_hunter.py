# -*- coding: utf-8 -*-
"""
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃  中报猎手 — 叠加中报实际业绩，重新寻找"翻倍潜力"标的            ┃
┃                                                                ┃
┃  核心逻辑：以中报(2026H1)【实际披露业绩】为第一权重，            ┃
┃  叠加 市值弹性 + 估值消化(PEG) + 技术空间&右侧强度，             ┃
┃  重新对全市场小市值高壁垒股做翻倍潜力排序。                     ┃
┃                                                                ┃
┃  数据源：Tushare Pro                                           ┃
┃  中报实际业绩来源（按优先级）：                                 ┃
┃    S1 中报  fina_indicator(period=20260630)  实际披露全科目     ┃
┃    S2 快报  express(period=20260630)         实际披露关键数字   ┃
┃  翻倍潜力分 = 业绩主分×50% + 市值弹性×15% + 估值消化×15%      ┃
┃             + 技术空间&强度×20%                                 ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
"""

import os
import sys
import glob
import time
import argparse
from datetime import datetime

import numpy as np
import pandas as pd

import treasure_hunter as th

OUTPUT_DIR = th.OUTPUT_DIR


# ── 中报实际业绩采集 ───────────────────────────────────────────
def get_zhongbao_map() -> dict:
    """
    汇总中报实际业绩：优先 S1 中报(fina_indicator 20260630)，
    补 S2 快报(express 20260630 绝对净利/增速)。
    返回 ts_code -> {src, tr_yoy, netprofit_yoy, dt_netprofit_yoy,
                     grossprofit_margin, netprofit_margin, roe, rd,
                     n_income(元), revenue(元)}
    """
    zmap = {}

    # ── S1: 中报 fina_indicator 20260630（读缓存 parquet，当日扫描已刷新） ──
    fin_files = glob.glob(str(th.CACHE_DIR / "treasure_fin_ind_*.parquet"))
    for f in fin_files:
        code = os.path.basename(f).replace("treasure_fin_ind_", "").replace(".parquet", "").replace("_", ".")
        try:
            df = pd.read_parquet(f)
            if "end_date" not in df.columns or len(df) == 0:
                continue
            df = df[df["end_date"].astype(str).str.startswith("20260630")]
            if len(df) == 0:
                continue
            r = df.iloc[0]
            zmap[code] = {
                "src": "中报",
                "tr_yoy": _num(r.get("tr_yoy")),
                "netprofit_yoy": _num(r.get("netprofit_yoy")),
                "dt_netprofit_yoy": _num(r.get("dt_netprofit_yoy")),
                "grossprofit_margin": _num(r.get("grossprofit_margin")),
                "netprofit_margin": _num(r.get("netprofit_margin")),
                "roe": _num(r.get("roe")),
                "rd": _num(r.get("adminexp_of_gr")),
                "n_income": None,
                "revenue": None,
                "yoy_net_profit": None,
            }
        except Exception:
            continue

    # ── S2: 快报 express 20260630（批量，实际披露关键数字） ──
    import tushare as ts
    pro = ts.pro_api(th.TUSHARE_TOKEN)
    try:
        ex = th._ts_call(pro.express, period="20260630")
        if ex is not None and len(ex) > 0:
            for _, r in ex.iterrows():
                code = r["ts_code"]
                n_inc = _num(r.get("n_income"))
                rev = _num(r.get("revenue"))
                yoy = _num(r.get("yoy_net_profit"))
                if code in zmap:
                    # 中报优先：仅补齐中报缺失的绝对净利/营收/快报增速
                    if zmap[code].get("n_income") is None:
                        zmap[code]["n_income"] = n_inc
                    if zmap[code].get("revenue") is None:
                        zmap[code]["revenue"] = rev
                    if zmap[code].get("netprofit_yoy") is None and yoy is not None:
                        zmap[code]["netprofit_yoy"] = yoy
                else:
                    zmap[code] = {
                        "src": "快报",
                        "tr_yoy": None,
                        "netprofit_yoy": yoy,
                        "dt_netprofit_yoy": None,
                        "grossprofit_margin": None,
                        "netprofit_margin": None,
                        "roe": _num(r.get("diluted_roe")),
                        "rd": None,
                        "n_income": n_inc,
                        "revenue": rev,
                        "yoy_net_profit": yoy,
                    }
    except Exception as e:
        print(f"  [警告] express 获取失败: {str(e)[:100]}")

    # ── 中报来源补绝对值（income 归母净利） ──
    todo = [c for c, v in zmap.items() if v["src"] == "中报" and v.get("n_income") is None]
    for i, code in enumerate(todo):
        if (i + 1) % 50 == 0 or i == 0:
            print(f"  [中报绝对值 {i+1}/{len(todo)}] {code}")
        try:
            cache_key = f"zbin_{code.replace('.', '_')}"
            cached = th.load_cache_df(cache_key, 168)
            if cached is not None:
                if len(cached) > 0:
                    zmap[code]["n_income"] = _num(cached.iloc[0].get("n_income_attr_p"))
                    zmap[code]["revenue"] = _num(cached.iloc[0].get("revenue"))
                continue
            inc = th._ts_call(pro.income, ts_code=code, period="20260630",
                              fields="ts_code,end_date,revenue,n_income_attr_p,total_profit")
            if inc is not None and len(inc) > 0:
                th.save_cache_df(inc, cache_key)
                zmap[code]["n_income"] = _num(inc.iloc[0].get("n_income_attr_p"))
                zmap[code]["revenue"] = _num(inc.iloc[0].get("revenue"))
        except Exception:
            continue

    return zmap


def _num(x):
    """安全转 float，缺失/空返回 None"""
    try:
        if x is None or (isinstance(x, float) and np.isnan(x)):
            return None
        v = float(x)
        return None if (np.isnan(v) or np.isinf(v)) else v
    except Exception:
        return None


# ── 中报业绩主分（满分100） ────────────────────────────────────
def score_zhongbao(r: dict) -> float:
    """业绩主分：以中报实际增速与盈利质量为核心"""
    # 扣非增速（缺失则用净利增速兜底）
    dt = r.get("dt_netprofit_yoy")
    npg = r.get("netprofit_yoy")
    growth = dt if dt is not None else npg
    growth = 0.0 if growth is None else growth

    # 1) 扣非增速 45分
    if dt is not None:
        s1 = 45.0 if dt >= 150 else 42.0 if dt >= 100 else 36.0 if dt >= 60 \
            else 28.0 if dt >= 40 else 20.0 if dt >= 30 else 12.0 if dt >= 20 \
            else 6.0 if dt >= 0 else 0.0
    else:
        s1 = 38.0 if growth >= 150 else 34.0 if growth >= 100 else 28.0 if growth >= 60 \
            else 22.0 if growth >= 40 else 15.0 if growth >= 30 else 8.0 if growth >= 20 \
            else 4.0 if growth >= 0 else 0.0

    # 2) 净利增速 20分
    npg = 0.0 if npg is None else npg
    s2 = 20.0 if npg >= 150 else 18.0 if npg >= 100 else 14.0 if npg >= 60 \
        else 11.0 if npg >= 40 else 8.0 if npg >= 25 else 4.0 if npg >= 0 else 0.0

    # 3) 营收增速 15分
    tr = r.get("tr_yoy")
    tr = 0.0 if tr is None else tr
    s3 = 15.0 if tr >= 50 else 12.0 if tr >= 30 else 9.0 if tr >= 20 \
        else 6.0 if tr >= 10 else 3.0 if tr >= 0 else 0.0

    # 4) 盈利质量 15分（绝对中报净利，淘汰微基数翻倍）
    ni = r.get("n_income")
    if ni is None:
        s4 = 8.0  # 缺失时给中性分
    else:
        yi = ni / 1e8  # 元→亿
        s4 = 15.0 if yi >= 3 else 13.0 if yi >= 1 else 10.0 if yi >= 0.5 \
            else 7.0 if yi >= 0.3 else 4.0 if yi >= 0.15 else 1.0

    # 5) 壁垒 5分（毛利率）
    gm = r.get("grossprofit_margin")
    if gm is None:
        s5 = 2.0
    else:
        s5 = 5.0 if gm >= 50 else 4.0 if gm >= 40 else 3.0 if gm >= 30 \
            else 2.0 if gm >= 20 else 0.5

    return round(s1 + s2 + s3 + s4 + s5, 1)


# ── 技术面（复用 treasure 动量口径） ───────────────────────────
def compute_tech(code: str) -> dict:
    """距120日高 / MA20 / 量比 / 站上MA20天数 / 右侧强度"""
    res = {}
    try:
        daily = th.get_daily_by_code(code, days=180)
        if daily is None or len(daily) <= 60:
            return res
        daily = daily.sort_values("trade_date").reset_index(drop=True)
        closes = daily["close"].astype(float).values
        highs = daily["high"].astype(float).values
        volumes = daily["vol"].astype(float).values
        cur = float(closes[-1])

        high_120 = float(daily.tail(120)["high"].max())
        res["pct_from_120d_high"] = round((high_120 - cur) / high_120 * 100, 2) if high_120 > 0 else 999.0

        daily["ma20"] = daily["close"].rolling(20).mean()
        daily["ma60"] = daily["close"].rolling(60).mean()
        ma20 = float(daily["ma20"].dropna().iloc[-1]) if len(daily["ma20"].dropna()) > 0 else cur
        ma60 = float(daily["ma60"].dropna().iloc[-1]) if len(daily["ma60"].dropna()) > 0 else cur
        res["pct_below_ma20"] = round((cur - ma20) / ma20 * 100, 2) if ma20 > 0 else 0.0
        if len(daily["ma20"].dropna()) >= 6:
            m_prev = float(daily["ma20"].dropna().iloc[-6])
            res["ma20_slope"] = round((ma20 - m_prev) / m_prev * 100, 2) if m_prev > 0 else 0.0
        else:
            res["ma20_slope"] = 0.0
        res["ma20"] = round(ma20, 2)
        res["ma60"] = round(ma60, 2)

        if len(volumes) >= 20:
            v5, v20 = np.mean(volumes[-5:]), np.mean(volumes[-20:])
            res["volume_ratio"] = round(v5 / v20, 2) if v20 > 0 else 1.0
        else:
            res["volume_ratio"] = 1.0

        # 连续站上MA20天数
        days = 0
        ma20_arr = daily["ma20"].values
        for j in range(len(daily) - 1, -1, -1):
            if np.isnan(ma20_arr[j]) or closes[j] <= ma20_arr[j]:
                break
            days += 1
        res["days_above_ma20"] = days

        # 右侧回踩强度
        srow = {
            "days_above_ma20": days,
            "pct_below_ma20": res["pct_below_ma20"],
            "ma20_slope": res["ma20_slope"],
            "ma20": ma20,
            "ma60": ma60,
            "volume_ratio": res["volume_ratio"],
            "pct_from_120d_high": res["pct_from_120d_high"],
        }
        st = th._compute_rightside_strength(srow)
        res["rightside"] = st.get("右侧强度总分", 0.0)
        res["cur"] = cur
    except Exception:
        pass
    return res


# ── 综合评分 ──────────────────────────────────────────────────
def score_potential(r: dict) -> float:
    """翻倍潜力综合分（满分100）"""
    zs = r["业绩主分"]

    # 市值弹性 15分（越小翻倍越容易）
    mv = r["市值(亿)"]
    mv_s = 100.0 if mv <= 40 else 92.0 if mv <= 60 else 82.0 if mv <= 80 \
        else 66.0 if mv <= 120 else 45.0 if mv <= 200 else 28.0

    # 估值消化 15分（PEG）
    pe = r.get("pe_ttm")
    dt = r.get("dt_netprofit_yoy")
    npg = r.get("netprofit_yoy")
    g = dt if dt is not None else npg
    peg = r.get("PEG")
    if peg is not None and np.isfinite(peg) and pe is not None and pe > 0 and g is not None and g > 0:
        peg_s = 100.0 if peg <= 0.5 else 85.0 if peg <= 1 else 65.0 if peg <= 1.5 \
            else 45.0 if peg <= 2 else 25.0 if peg <= 3 else 10.0
    else:
        peg_s = 10.0

    # 技术空间&强度 20分（空间12 + 右侧强度8）
    ph = r.get("pct_from_120d_high", 999)
    sp_s = 12.0 if ph >= 25 else 10.0 if ph >= 15 else 7.0 if ph >= 5 else 4.0 if ph >= 0 else 1.0
    rs = r.get("rightside", 0)
    rs_s = 8.0 if rs >= 70 else 5.0 if rs >= 50 else 2.0 if rs >= 30 else 0.0

    total = zs * 0.50 + mv_s * 0.15 + peg_s * 0.15 + (sp_s + rs_s) * 1.0
    return round(total, 1)


# ── 主流程 ────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trade_date", default=None, help="交易日 YYYYMMDD，默认最近交易日")
    ap.add_argument("--top", type=int, default=20, help="展示前N只")
    ap.add_argument("--min_score", type=float, default=60.0, help="翻倍潜力分最低值")
    args = ap.parse_args()

    trade_date = args.trade_date or th.get_last_trade_date()
    print("=" * 70)
    print("  中报猎手 — 叠加中报实际业绩，重新寻找翻倍潜力股")
    print(f"  数据日期: {trade_date}")
    print("=" * 70)

    # ── Phase 1: 市值池 20~300亿 ──
    print("\n[Phase 1] 构建市值池(20~300亿)...")
    stocks = th.get_stock_list()
    basic = th.get_daily_basic(trade_date)
    if basic is None or len(basic) == 0:
        print("  [错误] daily_basic 无数据，检查交易日")
        return
    df = stocks.merge(basic[["ts_code", "total_mv", "pe_ttm", "close"]], on="ts_code", how="inner")
    df["市值(亿)"] = df["total_mv"] / 10000
    df["pe_ttm"] = df["pe_ttm"].apply(_num)
    # 排除北交所（.BJ 后缀 + 8/4/9开头.SZ老代码）
    df = df[~df["ts_code"].str.endswith(".BJ")].copy()
    df = df[~df["ts_code"].str.match(r"^(8\d{5}|4\d{5}|92\d{4})\.SZ$")].copy()
    pool = df[(df["市值(亿)"] >= 20) & (df["市值(亿)"] <= 300)].copy()
    print(f"  → 市值池: {len(pool)} 只")

    # ── Phase 2: 中报实际业绩 ──
    print("\n[Phase 2] 采集2026中报实际业绩（中报/快报）...")
    zmap = get_zhongbao_map()
    have = [c for c in zmap if c in set(pool["ts_code"])]
    print(f"  → 池内已披露中报/快报: {len(have)} 只（中报{sum(1 for c in have if zmap[c]['src']=='中报')}/快报{sum(1 for c in have if zmap[c]['src']=='快报')}）")

    rows = []
    for code in have:
        z = zmap[code]
        r = pool[pool["ts_code"] == code].iloc[0]
        row = {
            "ts_code": code,
            "name": r["name"],
            "来源": z["src"],
            "tr_yoy": z.get("tr_yoy"),
            "netprofit_yoy": z.get("netprofit_yoy"),
            "dt_netprofit_yoy": z.get("dt_netprofit_yoy"),
            "n_income(亿)": None if z.get("n_income") is None else round(z["n_income"] / 1e8, 3),
            "毛利率(%)": z.get("grossprofit_margin"),
            "ROE(%)": z.get("roe"),
            "研发(%)": z.get("rd"),
            "市值(亿)": round(r["市值(亿)"], 1),
            "pe_ttm": r["pe_ttm"],
        }
        row["业绩主分"] = score_zhongbao(z)
        rows.append(row)
    if not rows:
        print("  [错误] 无中报/快报数据")
        return
    data = pd.DataFrame(rows)

    # ── Phase 3: 硬过滤（增速达标 + 非微基数） ──
    def _g(r):
        dt = r.get("dt_netprofit_yoy")
        npg = r.get("netprofit_yoy")
        return dt if dt is not None else (npg if npg is not None else 0)
    data["增速基准"] = data.apply(_g, axis=1)
    passed = data[
        (data["增速基准"] >= 30) &                      # 扣非或净利增速≥30%
        (data["n_income(亿)"].fillna(0) >= 0.2)        # 中报净利≥2000万（防微基数）
    ].copy()
    print(f"  → 增速≥30%且净利≥0.2亿: {len(passed)} 只")

    # ── Phase 4: 技术面 + 综合分 ──
    print(f"\n[Phase 4] 技术面计算({len(passed)}只)...")
    t0 = time.time()
    tech_list = {}
    for i, code in enumerate(passed["ts_code"]):
        tech_list[code] = compute_tech(code)
        if (i + 1) % 20 == 0 or i == 0:
            print(f"  [{i+1}/{len(passed)}] {passed[passed['ts_code']==code]['name'].values[0] if len(passed) else ''}({code})")
    for k, v in tech_list.items():
        for fk, fv in v.items():
            passed.loc[passed["ts_code"] == k, fk] = fv
    passed["rightside"] = passed.get("rightside", 0).fillna(0)
    # PEG（向量化，避免 apply 写不回原表）
    passed["PEG"] = None
    _pe = pd.to_numeric(passed["pe_ttm"], errors="coerce")
    _g = pd.to_numeric(passed["dt_netprofit_yoy"], errors="coerce")
    _g = _g.fillna(pd.to_numeric(passed["netprofit_yoy"], errors="coerce"))
    _mask = (_pe > 0) & (_g > 0) & _pe.notna() & _g.notna()
    passed.loc[_mask, "PEG"] = (_pe[_mask] / _g[_mask]).round(2)
    passed["翻倍潜力分"] = passed.apply(score_potential, axis=1)
    passed = passed.sort_values("翻倍潜力分", ascending=False).reset_index(drop=True)

    # ── Phase 5: 输出 ──
    csv_path = os.path.join(OUTPUT_DIR, f"zhongbao_hunt_{trade_date}.csv")
    out_cols = ["name", "ts_code", "来源", "tr_yoy", "netprofit_yoy", "dt_netprofit_yoy",
                "n_income(亿)", "毛利率(%)", "ROE(%)", "市值(亿)", "pe_ttm", "PEG",
                "业绩主分", "翻倍潜力分", "pct_from_120d_high", "pct_below_ma20",
                "days_above_ma20", "rightside", "volume_ratio"]
    passed[out_cols].to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"\n完整CSV已保存: {csv_path}")

    top = passed[passed["翻倍潜力分"] >= args.min_score].head(args.top)
    print("\n" + "━" * 70)
    print(f"  翻倍潜力 TOP{len(top)}（中报实际业绩为主）")
    print("━" * 70)
    for i, r in top.iterrows():
        npi = f"{r['n_income(亿)']:.2f}亿" if pd.notna(r.get("n_income(亿)")) else "N/A"
        tr = f"{r['tr_yoy']:+.0f}%" if pd.notna(r.get("tr_yoy")) else "--"
        npg = f"{r['netprofit_yoy']:+.0f}%" if pd.notna(r.get("netprofit_yoy")) else "--"
        dt = f"{r['dt_netprofit_yoy']:+.0f}%" if pd.notna(r.get("dt_netprofit_yoy")) else "--"
        ph = f"{r.get('pct_from_120d_high', 0):.0f}%" if pd.notna(r.get("pct_from_120d_high")) else "--"
        ma20 = f"{r.get('pct_below_ma20', 0):+.1f}%" if pd.notna(r.get("pct_below_ma20")) else "--"
        rs = r.get("rightside", 0)
        print(f"  {i+1:>2}. {r['name']}({r['ts_code']}) [{r['来源']}]  潜力{r['翻倍潜力分']:.1f} | 业绩{r['业绩主分']:.1f}")
        peg_s = r.get("PEG")
        peg_s = f"{peg_s:.2f}" if isinstance(peg_s, (int, float)) and pd.notna(peg_s) else "--"
        print(f"     营收{tr} 净利{npg} 扣非{dt} | 中报净利{npi} | 市值{r['市值(亿)']:.0f}亿 | PEG{peg_s}")
        print(f"     距120日高{ph} 距MA20{ma20} 强度{rs:.0f} 站上MA20{r.get('days_above_ma20', 0)}日")

    print("\n" + "─" * 70)
    print("  数据说明：来源=中报/快报（实际披露）；增速为同比(2026H1 vs 2025H1)")
    print(f"  耗时 {time.time()-t0:.0f}s | 阈值说明: 增速≥30% 且 中报净利≥0.2亿")


if __name__ == "__main__":
    main()
