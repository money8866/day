# -*- coding: utf-8 -*-
"""
A股 Sector Intelligence & T60/T120 中长线布局分析引擎 V1.0
──────────────────────────────────────────────────────────────
数据口径（本地只读缓存，无网络依赖）：
  * 行情:      stock_data.db / stk_factor_pro  (qfq收盘/涨跌幅/量比/PE/PB/总市值/成交额)
  * 行业归属:  sli/cache/classify_SW2021_L1/L2/L3.parquet + members_*.parquet
               (申万2021：L3→L2→L1 链式上卷, 口径=L1 31行业)
  * 个股档案:  sli/cache/stock_basic.parquet   (代码/名称)
  * 财务:      stock_data.db / fina_indicator_cache  (季度, 取最近两期端日)
  * 大盘锚点:  cache_daily/parquet/index_daily_*.parquet (000001.SH/000300.SH 快照)
  * 资金面:    无逐笔主力净流 → 以成交额/量能比作"量能代理"，报告内显式标注

用法: python -X utf8 sector_intelligence.py [YYYYMMDD]
输出: report_daily/sector_intel_T120_{end}.md
"""
import os, sys, re, json, glob, sqlite3
import numpy as np
import pandas as pd

import stock_cache as sc

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = r"D:\mystock\cache_daily\stock_data.db"
CACHE = os.path.join(ROOT, "sli", "cache")
PARQUET_DIRS = [r"D:\mystock\cache_daily\parquet", CACHE,
                os.path.join(ROOT, "multi_factor_picker", "cache")]
REPORT_DIR = os.path.join(ROOT, "report_daily")

END = "20260904"
for a in sys.argv[1:]:
    if a.isdigit() and len(a) == 8:
        END = a
START = "20251110"              # 前推足够 120+ 交易日
LAGS = [1, 3, 5, 10, 20, 60, 120]
MV_MIN_YI = 80.0                # 个股候选最低市值(亿元) —— 用户约定
FIELDS = ["ts_code", "trade_date", "close_qfq", "pct_chg", "amount",
          "total_mv", "volume_ratio", "pe_ttm", "pb"]

def is_gem(c): return c.startswith(("300", "301", "688"))
def is_st(n):  return isinstance(n, str) and ("ST" in n.upper() or "退" in n)
def p_rank(s):
    s = pd.to_numeric(s, errors="coerce")
    return s.rank(pct=True) * 100.0
def p_rank_desc(s):
    s = pd.to_numeric(s, errors="coerce")
    return (1.0 - s.rank(pct=True).fillna(0.5)) * 100.0
def clip0(x):
    if pd.isna(x):
        return 0.0
    return max(0.0, min(100.0, float(x)))

# ══════════════════════════════════════════════════════════ 数据层
def load_sw_members():
    L1 = pd.read_parquet(os.path.join(CACHE, "classify_SW2021_L1.parquet"))
    L2 = pd.read_parquet(os.path.join(CACHE, "classify_SW2021_L2.parquet"))
    L3 = pd.read_parquet(os.path.join(CACHE, "classify_SW2021_L3.parquet"))
    mf = glob.glob(os.path.join(CACHE, "members_*.parquet"))
    members = pd.read_parquet(sorted(mf)[-1])
    L1NAME = dict(zip(L1["industry_code"], L1["industry_name"]))
    l2l1 = dict(zip(L2["industry_code"], L2["parent_code"]))
    l3l2 = dict(zip(L3["index_code"], L3["parent_code"]))
    l3_to_l1 = {}
    for idx, p2 in l3l2.items():
        p1 = l2l1.get(p2)
        if p1 in L1NAME:
            l3_to_l1[idx] = L1NAME[p1]
    cur = members[members["out_date"].isna()]
    cur = cur[cur["index_code"].isin(l3_to_l1) & ~cur["con_code"].str.endswith(".BJ")]
    cur = cur.copy()
    cur["ind"] = cur["index_code"].map(l3_to_l1)
    cur = cur.dropna(subset=["ind"]).drop_duplicates("con_code")
    return cur.set_index("con_code")["ind"].to_dict(), sorted(L1NAME.values())

def load_basic():
    b = pd.read_parquet(os.path.join(CACHE, "stock_basic.parquet"))
    return dict(zip(b["ts_code"], b["name"]))

def load_panel(codes):
    df = sc.fetch_hist_range(START, END, ts_codes=list(codes), cols=FIELDS)
    df = df[df["ts_code"].isin(codes)]
    for c in ["close_qfq", "amount", "total_mv", "pct_chg", "pe_ttm", "pb", "volume_ratio"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

def load_fina(codes):
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    df = pd.read_sql("SELECT ts_code,end_date,netprofit_yoy,or_yoy,roe FROM fina_indicator_cache",
                     conn)
    conn.close()
    return df[df["ts_code"].isin(codes)]

def load_index_series(codes):
    """汇总各 index_daily_*.parquet 快照(目录优先级序) → 拼接 → 升序 → 取 ≤END。

    本地 parquet 的 trade_date 为字符串且多数按降序存储，若直接 .loc[:END]
    会得到空 Series；且单一文件快照可能只覆盖到中间某日(如 multi_factor 目录
    旧快照)，因此按目录优先级拼接所有候选、按日期去重后再排序切片。
    """
    pat = re.compile(r"index_daily_(.+?)_(\d{8})_(\d{8})\.parquet$")
    files_by_code = {c: [] for c in codes}
    for d in PARQUET_DIRS:
        for f in sorted(glob.glob(os.path.join(d, "*.parquet"))):
            m = pat.search(os.path.basename(f))
            if not m:
                continue
            g1 = m.group(1)
            code = g1.replace("_", ".") if "." not in g1 else g1
            if code in files_by_code:
                files_by_code[code].append(f)
    out = {}
    for code in codes:
        if not files_by_code[code]:
            out[code] = None
            continue
        try:
            parts = []
            for f in files_by_code[code]:
                df = pd.read_parquet(f)[["trade_date", "close"]].dropna()
                df["trade_date"] = df["trade_date"].astype(str)
                parts.append(df)
            s = pd.concat(parts, ignore_index=True).drop_duplicates("trade_date", keep="last")
            s = s.set_index("trade_date")["close"].sort_index()
            out[code] = s.loc[:END]
        except Exception:
            out[code] = None
    return out

# ══════════════════════════════════════════════════════════ 主流程
def main():
    ind_map, l1_names = load_sw_members()
    name_map = load_basic()
    codes = sorted(ind_map.keys())
    print(f"[1] 申万L1成分 {len(codes)} / {len(l1_names)} 行业")

    panel = load_panel(codes)
    panel = panel[panel["total_mv"] > 0]
    dates = sorted(panel["trade_date"].unique())
    if not dates:
        print(f"DATA_INSUFFICIENT: 区间 {START}~{END} 无本地行情记录（检查 cache_daily 是否已更新到 {END}）。")
        sys.exit(2)
    last = dates[-1]
    if END > last:
        print(f"[注意] 请求 END={END} 超出本地最新交易日 {last}，按 {last} 生成报告。")
    if len(dates) < 125:
        print(f"DATA_INSUFFICIENT: 本地仅 {len(dates)} 个交易日（<125），不足以计算 T120 级指标；"
              f"请把 END 后移到最近日期，或确认 START 前的缓存覆盖。")
        sys.exit(2)
    print(f"[2] 行情 {len(panel):,} 行 / {len(dates)} 交易日 ({dates[0]}~{last})")

    # —— 宽表面板 ——
    P = panel.pivot_table(index="trade_date", columns="ts_code", values="close_qfq").sort_index()
    MV = panel.pivot_table(index="trade_date", columns="ts_code", values="total_mv").sort_index()
    PE = panel.pivot_table(index="trade_date", columns="ts_code", values="pe_ttm").sort_index()
    PB = panel.pivot_table(index="trade_date", columns="ts_code", values="pb").sort_index()
    L1COL = pd.Index(P.columns).map(ind_map)
    # 行业日度 cap-weighted 收益
    R = {k: P.pct_change(k) for k in LAGS}
    def cw_ind(Rk):
        v = Rk.notna()
        num = (Rk * MV).where(v)
        den = MV.where(v)
        lab = pd.Series(ind_map).reindex(num.columns)   # 与 num 列对齐
        # pandas>=2.1 移除了 groupby(axis=1)，用转置分组
        return (num.T.groupby(lab).sum().T / den.T.groupby(lab).sum().T)
    cwr = {k: cw_ind(R[k]) for k in LAGS}
    idx_lv = (1 + cwr[1]).cumprod()                      # cap 指数点位(复利链)

    # ─────────── 行业指标表 ───────────
    ind = pd.DataFrame(index=idx_lv.columns)
    for k in LAGS:
        ind[f"ret{k}"] = cwr[k].loc[last] * 100.0
    for n in (5, 20, 60):
        ind[f"ma{n}x"] = (idx_lv.loc[last] / idx_lv.rolling(n).mean().loc[last] - 1) * 100.0
    ind["slope20"] = (idx_lv.iloc[-1] / idx_lv.iloc[-6] - 1) * 100.0          # 近5日
    ind["slope60"] = (idx_lv.iloc[-1] / idx_lv.iloc[-11] - 1) * 100.0         # 近10日
    ind["accel"] = ind["ret5"] - ind["ret20"] * 0.25

    # 今日个股级指标（挂到行业）
    today = panel[panel["trade_date"] == last].copy()
    today["ind"] = today["ts_code"].map(ind_map)
    n_stk = today.groupby("ind")["ts_code"].count()
    up_td = today[today["pct_chg"] > 0].groupby("ind")["ts_code"].count()
    th_up = today["ts_code"].apply(lambda c: 19.4 if is_gem(c) else 9.7)
    lim_up = today[(today["pct_chg"] >= th_up)].groupby("ind")["ts_code"].count()
    ind["n_stock"] = n_stk.reindex(ind.index).fillna(0)
    ind["up_today"] = up_td.reindex(ind.index).fillna(0)
    ind["lim_up"] = lim_up.reindex(ind.index).fillna(0)
    ind["up_today_ratio"] = ind["up_today"] / ind["n_stock"] * 100.0

    # 5日扩散
    r5_st = R[5].loc[last]
    up5 = (r5_st > 0).groupby(L1COL).sum()
    n5 = r5_st.notna().groupby(L1COL).sum()
    ind["up5_ratio"] = (up5 / n5.replace(0, np.nan) * 100.0).fillna(0)

    # 行业量能（成交额口径 千元）
    amt_ind = panel.groupby(["trade_date", panel["ts_code"].map(ind_map)])["amount"].sum().unstack(fill_value=0.0)
    amt_ma5 = amt_ind.rolling(5).mean()
    amt_ma20 = amt_ind.rolling(20).mean()
    ind["amt_today"] = amt_ind.loc[last]
    ind["amt_ratio_v20"] = (amt_ind.loc[last] / amt_ma20.loc[last].replace(0, np.nan)) * 100.0
    ind["amt_ratio_v5"] = (amt_ind.loc[last] / amt_ma5.loc[last].replace(0, np.nan)) * 100.0
    ind["amt_trend"] = (amt_ind.iloc[-5:].mean() / amt_ind.iloc[-11:-5].mean().replace(0, np.nan) - 1) * 100.0

    # 前5权重集中度(最新市值)
    mvl = MV.iloc[-1]
    def top5(i):
        s = mvl[[c for c in mvl.index if ind_map[c] == i]].dropna()
        return s.nlargest(5).sum() / s.sum() * 100.0 if len(s) else np.nan
    ind["top5_con"] = [top5(i) for i in ind.index]

    # 估值：正PB行业中位 & 历史分位；正PE行业中位
    lab_col = pd.Series(ind_map).reindex(PB.columns)
    pb_pos = PB.where(PB > 0)
    pb_med = pb_pos.T.groupby(lab_col).median().T
    ind["pb_cur"] = pb_med.loc[last]
    ind["pb_hist_pct"] = [ (pb_med[c].dropna() <= pb_med[c].loc[last]).mean() * 100.0
                           if pb_med[c].notna().loc[last] and len(pb_med[c].dropna()) > 20 else np.nan
                           for c in pb_med.columns]
    pe_pos = PE.where(PE > 0)
    pe_med = pe_pos.T.groupby(lab_col.reindex(PE.columns)).median().T
    ind["pe_cur"] = pe_med.loc[last]

    # 财务：最近两期端日，行业中位
    fin = load_fina(codes)
    qs = sorted(fin["end_date"].unique())[-2:]
    fin_cur = fin[fin["end_date"] == qs[-1]].copy()
    fin_prv = fin[fin["end_date"] == qs[-2]].copy()
    fin_cur["ind"] = fin_cur["ts_code"].map(ind_map)
    fin_prv["ind"] = fin_prv["ts_code"].map(ind_map)
    for col in ["netprofit_yoy", "or_yoy", "roe"]:
        ind[f"med_{col}"] = fin_cur.groupby("ind")[col].median().reindex(ind.index)
    ind["med_npyoy_chg"] = (ind["med_netprofit_yoy"]
                            - fin_prv.groupby("ind")["netprofit_yoy"].median().reindex(ind.index))

    # 相对强度（相对全A等权）
    mk_ew = {k: R[k].mean(axis=1) for k in LAGS}
    for k in (5, 20, 60, 120):
        ind[f"rs{k}"] = ind[f"ret{k}"] - mk_ew[k].loc[last] * 100.0

    # 龙头：行业内 60日动量×市值×量比 复合
    ls = pd.DataFrame({"r60": R[60].loc[last], "mv": mvl,
                       "vr": panel[panel["trade_date"] == last].set_index("ts_code")["volume_ratio"]})
    ls = ls.dropna(subset=["mv"])
    ls["ind"] = ls.index.map(ind_map)
    for f in ("r60", "mv", "vr"):
        ls[f"{f}_p"] = ls.groupby("ind")[f].rank(pct=True)
    ls["score"] = ls["r60_p"] * 0.5 + ls["mv_p"] * 0.3 + ls["vr_p"] * 0.2
    leaders = {}
    for i in ind.index:
        sub = ls[ls["ind"] == i]
        sub2 = sub[sub["mv"] >= MV_MIN_YI * 1e4]
        if not sub2.empty:
            sub = sub2
        if sub.empty:
            leaders[i] = None
            continue
        sub = sub.sort_values("score", ascending=False)
        top = sub.iloc[0]
        chg = P[top.name].diff().iloc[-10:]
        leaders[i] = (top.name, top["score"] * 100.0, top["mv"],
                      (chg > 0).mean() * 100.0 if chg.notna().sum() else np.nan)
    ldf = pd.DataFrame([(i, v[0], v[1], v[3]) for i, v in leaders.items() if v],
                       columns=["ind", "leader", "lscore", "lpersist"]).set_index("ind")
    ind = ind.join(ldf, how="left")

    # ═══════════ 三套评分 ═══════════
    def R100(s): return p_rank(s).fillna(50)
    # SHORT
    ind["SHORT"] = (R100(ind["amt_trend"]) * .15 + R100(ind["ret3"] * .5 + ind["ret5"] * .5) * .30
                    + R100(ind["lim_up"]) * .10 + R100(ind["up5_ratio"]) * .15
                    + R100(ind["lscore"]).fillna(50) * .15 + R100(ind["up_today_ratio"]) * .15)
    # MID
    ind["MID"] = (R100(ind["ma5x"] * .4 + ind["ma20x"] * .6) * .20 + R100(ind["rs20"]) * .20
                  + R100(ind["up5_ratio"]) * .10 + R100(ind["lpersist"]).fillna(50) * .15
                  + R100(ind["amt_ratio_v20"] * .5 + ind["amt_trend"] * .5) * .15 + R100(ind["ret20"]) * .20)
    # LONG
    earn = (R100(ind["med_netprofit_yoy"]) * .5 + R100(ind["med_or_yoy"]) * .3
            + R100(ind["med_roe"]) * .2)
    jq = R100(ind["med_netprofit_yoy"] + ind["med_npyoy_chg"])
    ind["LONG"] = (earn * .20 + jq * .10
                   + R100(ind["slope60"] * .5 + ind["rs60"] * .5) * .10
                   + R100(ind["ret60"] * .4 + ind["ret120"] * .6) * .15
                   + R100(ind["rs120"]) * .10
                   + p_rank(100 - ind["pb_hist_pct"]).fillna(50) * .10
                   + R100(ind["amt_ratio_v20"]) * .10
                   + R100(ind["lscore"]).fillna(50) * .15)

    # ═══════════ 生命周期 ═══════════
    def lifecycle(r):
        if r["ret60"] < -15 and r["slope60"] < -5:
            return "DECLINE"
        if r["ret120"] > 10 and r["ma20x"] < -4 and r["ret20"] < -3 and r["lim_up"] < 1 and r["rs20"] < 0:
            return "DISTRIBUTION"
        if r["LONG"] >= 72 and r["MID"] >= 60 and r["SHORT"] >= 50 and r["amt_trend"] > 0:
            return "ACCELERATION"
        if r["LONG"] >= 68 and r["ret60"] > 5:
            return "MATURE"
        if r["rs20"] < -8 and r["ret20"] < -8 and r["slope60"] > -3:
            return "RECOVERY"
        if r["SHORT"] >= 55 and r["rs5"] > -2 and r["LONG"] >= 55:
            return "EARLY"
        return "DECLINE" if r["rs60"] < -10 else "MATURE"
    ind["LIFECYCLE"] = ind.apply(lifecycle, axis=1)

    # ═══════════ 市场状态 ═══════════
    mkt = compute_market(panel, P, dates, last)

    # ═══════════ 报告 ═══════════
    aux = {"ind_map": ind_map, "P": P, "R": R, "MV": MV, "PE": PE, "mvl": mvl}
    build_report(mkt, ind, name_map, dates, last, aux)

# ══════════════════════════════════════════════════════════ 市场状态
def compute_market(panel, P, dates, last):
    prev = dates[-2]
    d = panel[panel["trade_date"] == last].dropna(subset=["pct_chg"])
    amt_all = panel.groupby("trade_date")["amount"].sum()
    amt = amt_all.iloc[-1]
    up = int((d["pct_chg"] > 0).sum()); dn = int((d["pct_chg"] < 0).sum())
    limu = int((d["pct_chg"] >= d["ts_code"].apply(lambda c: 19.4 if is_gem(c) else 9.7)).sum())
    limd = int((d["pct_chg"] <= d["ts_code"].apply(lambda c: -19.4 if is_gem(c) else -9.7)).sum())
    r5 = P.pct_change(5).iloc[-1]
    up_ratio5 = float((r5 > 0).mean() * 100)
    # 市值分层今日表现
    dm = d.set_index("ts_code")["total_mv"]
    dchg = d.set_index("ts_code")["pct_chg"]
    b = pd.DataFrame({"mv": dm, "chg": dchg}).dropna()
    cuts = pd.cut(b["mv"], [0, 3e5, 1e6, 3e6, 1e7, 1e11],
                  labels=["微盘<30亿", "小盘30-100", "中盘100-300", "大盘300-1000", "超大盘>1000亿"])
    style = b.groupby(cuts, observed=True)["chg"].agg(["mean", lambda x: (x > 0).mean() * 100])
    style.columns = ["chg", "up_ratio"]
    style["chg"] = style["chg"] / 100.0
    # 指数锚点
    bench = {}
    for c, s in load_index_series(["000001.SH", "000300.SH"]).items():
        if s is None or len(s) < 70:
            bench[c] = None
            continue
        s = s.dropna()
        def rret(n):
            return s.iloc[-1] / s.iloc[-1 - n] - 1 if len(s) > n else np.nan
        bench[c] = {"close": float(s.iloc[-1]), "r1": rret(1), "r20": rret(20),
                    "r60": rret(60), "vs_ma20": float(s.iloc[-1] / s.iloc[-21:].mean() - 1)}
    # 状态判定
    upr = up / (up + dn)
    r20 = bench["000001.SH"]["r20"] if bench["000001.SH"] else 0.0
    if limd >= 30 or (upr < 0.32 and r20 < -0.03):
        regime, risk = "系统性风险", "高"
    elif limd >= 12 or (upr < 0.38 and r20 < -0.02):
        regime, risk = "结构性退潮", "中高"
    elif limu >= 70 and upr > 0.62 and r20 > 0.02:
        regime, risk = "强势上升", "低"
    elif limu >= 35 and upr > 0.55:
        regime, risk = "震荡偏强", "中"
    elif 0.42 < upr < 0.6 and abs(r20) < 0.04:
        regime, risk = "震荡", "中"
    elif upr > 0.5:
        regime, risk = "震荡偏强", "中"
    else:
        regime, risk = "震荡偏弱", "中高"
    vol = "放量" if amt / amt_all.iloc[-21:-1].mean() > 1.1 else ("缩量" if amt / amt_all.iloc[-21:-1].mean() < 0.9 else "平量")
    return {"last": last, "prev": prev, "amt": amt, "amt_ma20": float(amt_all.iloc[-21:-1].mean()),
            "up": up, "dn": dn, "limu": limu, "limd": limd, "up_ratio5": up_ratio5,
            "style": style, "bench": bench, "regime": regime, "risk": risk, "vol": vol,
            "prev_date": prev}

# ══════════════════════════════════════════════════════════ 报告
def build_report(mkt, ind, name_map, dates, last, aux):
    os.makedirs(REPORT_DIR, exist_ok=True)
    W = lambda s="": out.append(s)
    out = []
    P = aux["P"]
    pct = lambda x: f"{x*100:+.2f}%" if pd.notna(x) else "-"
    F = lambda x: f"{x:.1f}" if pd.notna(x) else "-"

    W(f"# A股 Sector Intelligence · T60/T120 布局分析（{last}）")
    W("")
    n_ind = int(P.columns.map(aux["ind_map"]).nunique())
    n_stk = len(P.columns)
    W(f"> 口径：申万2021·L1（{n_ind}行业，{n_stk}只成分，不含北交所）｜市值加权为主｜行业资金=量能代理（本地无主力净流数据，已标注 DATA_INSUFFICIENT 处不编造）")
    W("")
    W("## 1. MARKET REGIME")
    W("")
    b1 = mkt["bench"].get("000001.SH"); b3 = mkt["bench"].get("000300.SH")
    amt_yi = mkt["amt"] / 1e5           # amount 单位千元 → 亿元
    amt_yi20 = mkt["amt_ma20"] / 1e5
    W("| 项目 | 数值 |")
    W("| -- | -- |")
    if b1:  W(f"| 上证指数 | {b1['close']:.2f}　日{pct(b1['r1'])} / 20日{pct(b1['r20'])} / 60日{pct(b1['r60'])} / vsMA20 {pct(b1['vs_ma20'])} |")
    else:   W("| 上证指数 | DATA_INSUFFICIENT |")
    if b3:  W(f"| 沪深300 | {b3['close']:.2f}　日{pct(b3['r1'])} / 20日{pct(b3['r20'])} / 60日{pct(b3['r60'])} |")
    else:   W("| 沪深300 | DATA_INSUFFICIENT |")
    W(f"| 成交额(申万样本{n_stk}只) | {amt_yi:,.0f} 亿元（20日均 {amt_yi20:,.0f}） |")
    W(f"| 涨/跌(申万样本) | {mkt['up']} / {mkt['dn']}（上涨占比 {mkt['up']/(mkt['up']+mkt['dn'])*100:.1f}%） |")
    W(f"| 近似涨停/跌停 | {mkt['limu']} / {mkt['limd']}（10/20cm 阈值近似） |")
    W(f"| 5日上涨占比 | {mkt['up_ratio5']:.1f}% |")
    st = mkt["style"]
    stxt = "；".join(f"{i} {r['chg']*100:+.2f}%(上涨{r['up_ratio']:.0f}%)" for i, r in st.iterrows())
    W(f"| 风格分层(今日) | {stxt} |")
    W("")
    W(f"**市场状态：{mkt['regime']}**　｜　风险等级：{mkt['risk']}　｜　量能：{mkt['vol']}")
    W("")
    big_i = [x for x in st.index if "大盘" in str(x) or "超大盘" in str(x)]
    sml_i = [x for x in st.index if "小盘" in str(x) or "微盘" in str(x)]
    big = st.loc[big_i, "chg"].mean() if big_i else np.nan
    sml = st.loc[sml_i, "chg"].mean() if sml_i else np.nan
    diff = big - sml
    W("**内部结构检查：**")
    if pd.notna(diff):
        W(f"- 大盘({big*100:+.2f}%) vs 小盘({sml*100:+.2f}%) 差异 {diff*100:+.1f}pct → " +
          ("**权重护盘/结构分化**：指数掩盖中小盘风险" if diff > 0.4 else
           "**小盘强于大盘**，指数失真偏保守" if diff < -0.4 else "结构分化不大"))
    eff = "好" if mkt["up_ratio5"] > 55 and mkt["limu"] > 40 else ("一般" if mkt["up_ratio5"] > 45 else "差")
    W(f"- 涨停 {mkt['limu']} / 跌停 {mkt['limd']}；5日上涨占比 {mkt['up_ratio5']:.0f}% → 赚钱效应：{eff}")
    W("**核心解释：**")
    upr_now = mkt["up"] / (mkt["up"] + mkt["dn"]) * 100.0
    sents = [
        f"① 市场为「{mkt['regime']}」（风险 {mkt['risk']}、{mkt['vol']}），今日上涨占比 {upr_now:.0f}%、5日上涨占比 {mkt['up_ratio5']:.0f}%，赚钱效应{'弱（涨停不足、追涨容错低）' if eff == '差' else '一般' if eff == '一般' else '较强'}。"]
    if pd.notna(diff):
        if diff > 0.4:
            sents.append(f"② 中小盘明显弱于大盘({diff*100:+.1f}pct)，属「权重护盘/结构分化」，指数读数掩盖了中小盘与主题股的下行风险。")
        elif diff < -0.4:
            sents.append(f"② 小盘明显强于大盘({-diff*100:.1f}pct)，指数读数偏保守，结构性机会更多在小市值/主题一端。")
        else:
            sents.append(f"② 大小盘差异仅 {abs(diff)*100:.1f}pct，普涨/普跌为主，无极端风格切换，指数读数基本反映真实宽度。")
    sents.append(f"③ 涨停 {mkt['limu']} / 跌停 {mkt['limd']}（10/20cm近似），5日上涨占比 {mkt['up_ratio5']:.0f}%，市场宽度偏{'健康' if mkt['up_ratio5'] > 50 else '弱'}，短线容错{'较高' if mkt['limu'] > 40 else '较低'}。")
    sents.append("④ " + ("风险等级偏高：不适合 T+5 追高与 T120 一次性满仓，只保留趋势健康方向的回踩分批。" if mkt["risk"] in ("高", "中高")
                 else "风险等级可控：可维持既有节奏，但单一行业仓位建议 ≤20%，并留失效止损。"))
    for s_ in sents:
        W(f"- {s_}")
    W("> 注：炸板率/连板高度/主力净流入本地无数据 → DATA_INSUFFICIENT；此处只依据宽度、量能、风格分层给出事实性解释，不猜测资金去向。")
    W("")
    W("## 2. SECTOR STRUCTURE")
    W("")
    def classify(r):
        sc, mc, lc = r["SHORT"], r["MID"], r["LONG"]
        if sc >= 62 and mc >= 58 and lc >= 60:             return "A"   # 短中长共振
        if lc >= 65 and sc < 60 and r["ret60"] > 0:        return "B"   # 长强短弱·等回调
        if sc >= 62 and lc < 58:                           return "C"   # 短线脉冲·禁T120
        if lc < 45 and mc < 40 and sc < 55:                return "D"   # 全面走弱·回避
        return "X"                                                       # 观察
    CLS_NAME = {"A": "A·短中长共振", "B": "B·长强短弱",
                "C": "C·短线脉冲", "D": "D·回避", "X": "观察"}
    ind["_CLS"] = ind.apply(classify, axis=1)
    def pos(r): return CLS_NAME[r["_CLS"]]
    W("| 行业 | Short | Mid | Long | 周期 | 量能20d | 扩散5d | 定位 |")
    W("| -- | --: | --: | --: | -- | --: | --: | -- |")
    for nm, r in ind.sort_values("SHORT", ascending=False).iterrows():
        W(f"| {nm} | {r['SHORT']:.0f} | {r['MID']:.0f} | {r['LONG']:.0f} | {r['LIFECYCLE']} | "
          f"{r['amt_ratio_v20']:.0f}% | {r['up5_ratio']:.0f}% | {pos(r)} |")
    W("")
    W("### TOP10 中长期（LONG 排序）")
    W("")
    W("| 行业 | Long | Mid | 60日 | 120日 | 净利yoy中位 | ROE | PB分位 | 周期 |")
    W("| -- | --: | --: | --: | --: | --: | --: | --: | -- |")
    for nm, r in ind.sort_values("LONG", ascending=False).head(10).iterrows():
        W(f"| {nm} | {r['LONG']:.0f} | {r['MID']:.0f} | {r['ret60']:+.1f}% | {r['ret120']:+.1f}% | "
          f"{F(r['med_netprofit_yoy'])} | {F(r['med_roe'])} | {F(r['pb_hist_pct'])} | {r['LIFECYCLE']} |")
    W("")
    W("### 退潮/最弱（20日动量倒序）")
    W("")
    W("| 行业 | Short | Mid | Long | 20日 | 60日 | 周期 |")
    W("| -- | --: | --: | --: | --: | --: | -- |")
    for nm, r in ind.sort_values("ret20").head(8).iterrows():
        W(f"| {nm} | {r['SHORT']:.0f} | {r['MID']:.0f} | {r['LONG']:.0f} | {r['ret20']:+.1f}% | {r['ret60']:+.1f}% | {r['LIFECYCLE']} |")
    W("")
    W("## 3. ROTATION ANALYSIS（主线切换）")
    W("")
    # 旧主线 = 「曾经强但近期转弱」的前期主线：120日大涨且60日转负 / 或60日强而20日转负
    oldm = ind[((ind["ret120"] > 10) & (ind["ret60"] < -3))
                | ((ind["ret60"] > 8) & (ind["ret20"] < -2))]
    if oldm.empty:
        oldm = ind.sort_values("ret120", ascending=False).head(3)
    oldm = oldm.sort_values("ret120", ascending=False).head(4)
    # 新主线候选 = 短线强(资金量能趋势放大, 非单日脉冲)
    newm = ind[(ind["SHORT"] >= 62) & (ind["amt_trend"] > 0)]
    newm = newm.sort_values("SHORT", ascending=False).head(5) if len(newm) > 5 else newm.sort_values("SHORT", ascending=False)
    W("**候选新主线（短线+资金趋势放大）：** " + ("；".join(
        f"{x} S{r['SHORT']:.0f}/L{r['LONG']:.0f}｜量能5d趋势{r['amt_trend']:+.0f}%｜扩散{r['up5_ratio']:.0f}%"
        for x, r in newm.iterrows()) if len(newm) else "无"))
    W("**旧主线候选（曾经强势、近期转弱：120日大涨后退潮 或 60日仍强但20日短线转弱）：** " + ("；".join(
        f"{x} 120日{r['ret120']:+.1f}%→60日{r['ret60']:+.1f}%｜现S{r['SHORT']:.0f}｜{r['LIFECYCLE']}"
        for x, r in oldm.iterrows()) if len(oldm) else "无"))
    overlap = set(newm.index) & set(oldm.index)
    confirm, confirmed_by = False, ""
    for nm in newm.index:
        if nm in oldm.index:
            continue
        r = ind.loc[nm]
        if r["amt_trend"] > 3 and r["up5_ratio"] > 55 and r["ret5"] > 0:
            confirm, confirmed_by = True, nm
            break
    if not confirm and not oldm.empty and not newm.empty and not overlap:
        confirm = True
        confirmed_by = "（旧主线全面走弱）"
    state = "ROTATION_CONFIRMED" if confirm else "ROTATION_WATCH"
    W("")
    W(f"**主线切换判定：`{state}`**" + (f"（依据：{confirmed_by}）" if confirmed_by else ""))
    W("> 判定标准：新方向需同时满足「5日动量>0＋5日量能趋势放大＋5日扩散≥55%」且与旧主线错位；单日领涨不计。"
      "旧主线需60日动量转负或20日动量衰减方视为退潮。**注意：若切换发生在 LONG<60 的行业之间，属于『交易性高低切换』而非基本面主线更替，只影响 T+5/T+20，不进 T120 核心。**")
    W("")
    # ───────── 时间维度分类 ─────────
    W("## 4. 时间维度分类（A/B/C/D）")
    W("")
    A = ind[ind["_CLS"] == "A"].sort_values("LONG", ascending=False)
    B = ind[ind["_CLS"] == "B"].sort_values("LONG", ascending=False)
    C = ind[ind["_CLS"] == "C"].sort_values("SHORT", ascending=False)
    D = ind[ind["_CLS"] == "D"].sort_values("LONG")
    W("**A类·短中长共振（最优先，T20/T60/T120候选）：** " + ("、".join(A.index) if len(A) else "无"))
    W("**B类·长期强/短线弱（T60/T120 等回调重点池）：** " + ("、".join(B.index) if len(B) else "无"))
    W("**C类·短线脉冲（只允许 T+5/T+20，禁入 T120 核心）：** " + ("、".join(C.index) if len(C) else "无"))
    W("**D类·短中长均弱（回避）：** " + ("、".join(D.index) if len(D) else "无"))
    W("> A/B/C/D 分类只回答『行业时间结构』，不替代第5节 T120 十项硬门槛与最终复核。")
    W("")

    # ───────── T120 十项判据 ─────────
    W("## 5. T120 行业判据（十项硬门槛）")
    W("")
    def t120(nm, r):
        ck = []
        ck.append(("LONG≥75", r["LONG"] >= 75, F(r["LONG"])))
        ck.append(("60/120趋势不向下", not (r["ret60"] < -3 and r["ret120"] < -3), f"{r['ret60']:+.0f}/{r['ret120']:+.0f}"))
        ck.append(("RS前40%", r["rs120"] >= ind["rs120"].quantile(0.6), f"{r['rs120']:+.1f}pct"))
        ck.append(("盈利趋势稳定", (r["med_npyoy_chg"] >= -15) or pd.isna(r["med_netprofit_yoy"]), f"yoy{F(r['med_netprofit_yoy'])} 环{F(r['med_npyoy_chg'])}"))
        ck.append(("周期非退潮/派发", r["LIFECYCLE"] not in ("DECLINE", "DISTRIBUTION"), r["LIFECYCLE"]))
        ck.append(("龙头未破坏", pd.notna(r.get("lpersist")) and r["lpersist"] >= 40, f"{F(r.get('lpersist'))}%"))
        ck.append(("量能非单日脉冲", r["amt_ratio_v20"] < 300 and r["amt_trend"] > -20, f"v20 {r['amt_ratio_v20']:.0f}% t {r['amt_trend']:+.0f}%"))
        ck.append(("估值合理", (pd.isna(r["pb_hist_pct"]) or r["pb_hist_pct"] <= 85) or r["med_netprofit_yoy"] >= 30, f"PB{r['pb_hist_pct']:.0f}%"))
        ck.append(("长期逻辑在(60日>0)", r["ret60"] > 0, f"{r['ret60']:+.1f}%"))
        ck.append(("市场允许", mkt["regime"] not in ("系统性风险",), mkt["regime"]))
        return ck
    res = {nm: t120(nm, r) for nm, r in ind.iterrows()}
    def n_ok(ch): return sum(b for _, b, _ in ch)
    tag = {nm: ("CORE" if n_ok(ch) >= 8 else
                "WATCH" if n_ok(ch) >= 6 else "AVOID")
           for nm, ch in res.items()}
    W("### T120_CORE（≥8/10 达标候选池；不等于可直接建仓，须再过「最终核心复核」）")
    W("")
    core_rows = [(nm, ch, tag[nm]) for nm, ch in res.items() if tag[nm] == "CORE"]
    if core_rows:
        W("| 行业 | 满足 | Long | Mid | 周期 | 60日 | 120日 | 量能20d | 缺口项 |")
        W("| -- | --: | --: | --: | -- | --: | --: | --: | -- |")
        for nm, ch, _ in sorted(core_rows, key=lambda x: -n_ok(x[1])):
            r = ind.loc[nm]
            miss = "、".join(c[0] for c in ch if not c[1])[:60] or "-"
            W(f"| {nm} | {n_ok(ch)}/10 | {r['LONG']:.0f} | {r['MID']:.0f} | {r['LIFECYCLE']} | "
              f"{r['ret60']:+.1f}% | {r['ret120']:+.1f}% | {r['amt_ratio_v20']:.0f}% | {miss} |")
    else:
        W("（当前市场无 8/10 达标行业）")
    W("")
    W("### T120_WATCH（6~7/10）")
    W("")
    watch_rows = [(nm, ch) for nm, ch in res.items() if tag[nm] == "WATCH"]
    if watch_rows:
        W("| 行业 | 满足 | Long | 周期 | 缺口项 |")
        W("| -- | --: | --: | -- | -- |")
        for nm, ch in sorted(watch_rows, key=lambda x: -n_ok(x[1]))[:12]:
            r = ind.loc[nm]
            miss = "、".join(c[0] for c in ch if not c[1])[:70]
            W(f"| {nm} | {n_ok(ch)}/10 | {r['LONG']:.0f} | {r['LIFECYCLE']} | {miss} |")
    W("")
    W("### T120_AVOID（≤5/10）")
    W("")
    avoid = sorted([nm for nm, ch in res.items() if tag[nm] == "AVOID"],
                   key=lambda x: -ind.loc[x, "LONG"])[:12]
    W("、".join(avoid) if avoid else "无")
    W("")
    W("### 最终核心复核（≥8/10 基础上加严：LONG≥60 & MID≥60 & 60日动量>0 & 周期非退潮/派发）")
    W("")
    core_names = [nm for nm, t in tag.items()
                  if t == "CORE" and ind.loc[nm, "LONG"] >= 60 and ind.loc[nm, "MID"] >= 60
                  and ind.loc[nm, "ret60"] > 0
                  and ind.loc[nm, "LIFECYCLE"] not in ("DECLINE", "DISTRIBUTION")]
    core_names.sort(key=lambda x: -ind.loc[x, "LONG"])
    if core_names:
        W("、".join(f"{nm}(L{ind.loc[nm,'LONG']:.0f}/M{ind.loc[nm,'MID']:.0f}/60d{ind.loc[nm,'ret60']:+.1f}%)"
                    for nm in core_names))
        W("")
        W("> 达标候选池中未进最终核心的行业（缺口见上表），多为 LONG<60（盈利/估值/长期趋势不足）或 MID<60（中期趋势未共振）："
          "短线强≠可T120，暂只观察不建仓。")
    else:
        W("无——当前无同时满足强趋势+盈利+估值的行业，T60/T120 以观望为主。")
    W("")

    # ───────── T+5 / T+20 ─────────
    W("## 6. T+5 短线池（只做情绪量能，禁入T120）")
    W("")
    t5 = C.sort_values("SHORT", ascending=False)
    if len(t5):
        for nm, r in t5.head(8).iterrows():
            tag_c = "｜⚠M高=资金仍在趋势跟随，拥挤与情绪风险更大" if r["MID"] >= 65 else ""
            W(f"- **{nm}** S{r['SHORT']:.0f}/M{r['MID']:.0f}/L{r['LONG']:.0f}｜{r['LIFECYCLE']}｜5日{r['ret5']:+.1f}%｜涨停{r['lim_up']:.0f}｜扩散{r['up5_ratio']:.0f}%{tag_c}")
        W("> 为什么可短不可长：本类 LONG<58，盈利趋势/估值/产业逻辑未达中长期门槛；T+5 只做情绪与量能扩散，走弱即离场，绝不移仓为 T60/T120 仓位。")
    else:
        W("（当前无符合 C 类定义的纯情绪行业）")
    W("")
    W("## 7. T+20 趋势池")
    W("")
    t20 = ind[(ind["MID"] >= 60) & (ind["ret20"] > 1)].sort_values("MID", ascending=False)
    for nm, r in t20.head(8).iterrows():
        note = {"C": "｜⚠短线脉冲属性，趋势单/低吸仓，禁上T120",
                "A": "｜A类共振，趋势最顺"}.get(r["_CLS"], "")
        W(f"- **{nm}** M{r['MID']:.0f}/L{r['LONG']:.0f}｜{r['LIFECYCLE']}｜20日{r['ret20']:+.1f}%｜量能20d {r['amt_ratio_v20']:.0f}%｜龙头 {r.get('leader') or '-'}{note}")
    W("")
    W("## 8. 中长线布局策略")
    W("")
    def ext(nm):
        return ind.loc[nm, "ret5"] + ind.loc[nm, "ret20"]
    W("### 第一梯队·T120 可分批建仓（最终核心）")
    if core_names:
        for nm in core_names:
            r = ind.loc[nm]
            extra = "｜短线已急拉，回踩MA20/MA60再分批" if ext(nm) > 12 else "｜当前位置可小仓位起步，回踩再加"
            W(f"- **{nm}**（L{r['LONG']:.0f}/M{r['MID']:.0f}｜{r['LIFECYCLE']}｜60日{r['ret60']:+.1f}%｜PB分位{F(r['pb_hist_pct'])}｜龙头{r.get('leader') or '-'}{extra}）")
    else:
        W("- 无")
    W("")
    second = [nm for nm in B.index if nm not in core_names][:5]
    W("### 第二梯队·高质量回调窗口（B类·长强短弱，等缩量企稳）")
    W("> 回调质量判定：60日动量>0 前提下，量能收缩(5日量能趋势<0或20日量能比<100%)且指数未破MA60=HIGH；放量下跌或破MA60=中/低。")
    if second:
        for nm in second:
            r = ind.loc[nm]
            pq = "LOW"
            if r["ret60"] > 0:
                if r["ma60x"] < -6 or (r["amt_ratio_v20"] > 150 and r["ret20"] < 0):
                    pq = "LOW"
                else:
                    pq = "HIGH" if (r["amt_trend"] < -3 or r["amt_ratio_v20"] < 100) else "MID"
            pq_txt = {"HIGH": "高质量调整(缩量回调，回踩可分批)",
                      "MID": "中性回调(量能未充分收缩，等企稳再分批)",
                      "LOW": "量价恶化(放量下跌/破位，暂放弃)"}[pq]
            W(f"- **{nm}**（L{r['LONG']:.0f}/M{r['MID']:.0f}｜60日{r['ret60']:+.1f}%｜120日{r['ret120']:+.1f}%｜量能20d {r['amt_ratio_v20']:.0f}%）：**回调质量={pq}**，{pq_txt}；放量破MA60=逻辑破坏，全部放弃")
    else:
        W("- 无")
    W("")
    third = ind[(ind["LONG"] >= 50) & (ind["_CLS"] == "X")
                & (~ind.index.isin(core_names)) & (~ind.index.isin(B.index))].sort_values("LONG", ascending=False)
    W("### 第三梯队·行业确认中（趋势/扩散未修复，仅观察）")
    if len(third):
        for nm, r in third.head(5).iterrows():
            warn = ""
            if r["ret60"] < 0:
                warn = "（60日动量<0：下行/派发途中，反弹≠高质量回调，**勿抄底**）"
            W(f"- {nm}（L{r['LONG']:.0f}/M{r['MID']:.0f}｜{r['LIFECYCLE']}｜60日{r['ret60']:+.1f}%）：行业指数重上MA20且量能回补后再评估{warn}")
    else:
        W("- 无")
    W("")
    W("### 暂不布局（短线强但T120不达标 / 回避）")
    for nm, r in t5.head(5).iterrows():
        W(f"- {nm}（S{r['SHORT']:.0f}但LONG {r['LONG']:.0f}<58 → 只做T+5/T+20，不碰T60/T120）")
    if avoid:
        W(f"- 回避类：{'、'.join(avoid[:6])}")
    W("")

    # ───────── 反证 ─────────
    W("## 9. T120_CORE 反证")
    W("")
    W("> 反证原则：只列数据可支撑的证据；失效条件给出可执行阈值。")
    W("")
    for nm in core_names[:6]:
        r = ind.loc[nm]
        W(f"### {nm}")
        W(f"- 数据：Long {r['LONG']:.0f}｜60日 {r['ret60']:+.1f}%｜120日 {r['ret120']:+.1f}%｜PB分位 {F(r['pb_hist_pct'])}｜净利yoy中位 {F(r['med_netprofit_yoy'])}｜ROE {F(r['med_roe'])}｜量能20d {r['amt_ratio_v20']:.0f}%｜前5集中度 {F(r['top5_con'])}")
        W("- **为什么看好（数据3条）：**")
        W(f"  1. 盈利/景气：行业中位净利增速 {F(r['med_netprofit_yoy'])}%、营收增速 {F(r['med_or_yoy'])}%、ROE {F(r['med_roe'])}（环比 {F(r['med_npyoy_chg'])}pct）")
        W(f"  2. 中长期趋势：120日 {r['ret120']:+.1f}%、60日斜率 {F(r['slope60'])}、指数站上MA60({F(r['ma60x'])}%)")
        W(f"  3. 资金结构：量能20d {r['amt_ratio_v20']:.0f}%、量能趋势 {r['amt_trend']:+.1f}%、60日超额 {r['rs60']:+.1f}pct；龙头 {r.get('leader') or '-'}")
        W("- **最大风险（数据2条）：**")
        W("  - " + (f"估值已处历史 {r['pb_hist_pct']:.0f}% 高分位，若盈利环比转负则双杀" if (pd.notna(r['pb_hist_pct']) and r['pb_hist_pct'] > 75) else "估值尚在中低分位，主要风险来自情绪/拥挤度"))
        W(f"  - 量能异动：若单日量能 >20日均 {r['amt_ratio_v20']:.0f}% 且扩散 {r['up5_ratio']:.0f}% 掉头向下 → 短钱撤离/均值回归")
        W("- **失效条件（明确阈值）：** 行业指数收盘跌破 MA20 且 5日动量转负 → 减半仓；**跌破 MA60 且 20日动量<0 持续3个交易日** → 判定趋势破坏，撤出并转 WATCH/AVOID。")
        W("")
    # ───────── 个股 ─────────
    W("## 10. T120 核心行业个股绑定")
    W("")
    W("> 候选规则：成分股、市值≥80亿、非ST非北交所；评分=盈利30%+景气20%+地位15%+趋势15%+资金10%+估值10%；类型=LEADER(行业龙头)/CORE_MID(中军)/SECONDARY(二线)。")
    W("> 操作列即当前建议：状态=破位/WAIT 的行虽为高分候选，但趋势未修复，只跟踪不买入；买点一律等「回踩不破 MA20/MA60」确认。")
    W("")
    stk_rows = pick_stocks(core_names, ind, name_map, aux)
    if stk_rows:
        W("| 行业 | 股票 | 代码 | 类型 | T120分 | 行业LONG | 现价 | MA20 | MA60 | MA120 | 前高(60d) | 状态 | 操作 |")
        W("| -- | -- | -- | -- | --: | --: | --: | --: | --: | --: | --: | -- | -- |")
        W("\n".join(stk_rows))
    else:
        W("（当前最终核心池为空）")
    W("")
    W("## 11. 一句话结论")
    W("")
    core_top = "、".join(core_names[:4]) if core_names else "无（环境不允许T120建仓）"
    t5_top = "、".join(t5.index[:3]) if len(t5) else "无"
    t20_top = "、".join(t20.index[:4]) if len(t20) else "无"
    old_top = "、".join(oldm.index[:2]) if len(oldm) else "无明显旧主线"
    new_top = "、".join(newm.index[:3]) if len(newm) else "无明显新方向"
    W("> 【市场结论】")
    if mkt["risk"] in ("高", "中高"):
        risk_note = "赚钱效应偏弱/结构分化，不适合重仓追涨"
    elif mkt["risk"] == "中":
        risk_note = "风险中性，以分批/回踩方式参与为宜"
    else:
        risk_note = "风险偏低，可顺势提高仓位但保留纪律性止损"
    W(f"> 当前属于 **{mkt['regime']}** 市场（风险等级 {mkt['risk']}，量能{mkt['vol']}），{risk_note}。")
    W(f"> 最强方向：**{core_top}**；正在切换方向：由 **{old_top}** 的退潮向 **{new_top}** 的**交易性高低切换**（若新方向 LONG<60 仅为短线轮动，不构成 T120 主线更替）。")
    W(f"> T+5 重点：{t5_top}（仅情绪/量能博弈，禁T120）；T+20 重点：{t20_top}（趋势跟随，脉冲类只做低吸不做重仓）。")
    W(f"> T+60/T+120 重点布局：**{core_top}** 按个股操作列回踩分批（详见第9节反证与第10节个股表）。")
    W(f"> 当前最应避免：①把短线脉冲（{t5_top}）当 T120 配置；②在 **{old_top}** 退潮初期抢反弹抄底；③追已急拉行业高位股。")
    W(f"> 当前建议仓位：**{'中性偏防御 ≤40%' if mkt['risk'] in ('高','中高') else '中性 40-60%' if mkt['risk']=='中' else '进攻 60-80%'}**。")
    W("")
    W("> 核心原则：短线看资金和情绪，中线看趋势和扩散，长线看行业景气、盈利和估值；先判断行业，再判断个股；先判断生命周期，再决定买入周期。")
    W("")

    body = "\n".join(out)
    # 全角分隔：手机大字号下避免长直线占行过宽
    body = body.replace("\n---\n", "\n────────────────────────────\n")
    fp = os.path.join(REPORT_DIR, f"sector_intel_T120_{last}.md")
    with open(fp, "w", encoding="utf-8") as f:
        f.write(body)
    print("SAVED:", fp)
    print(body[:500])

# ══════════════════════════════════════════════════════════ 个股
def pick_stocks(core_list, ind, name_map, aux):
    """为 CORE 行业挑 top3：行业一致排名后回填价格/均线/状态/操作。"""
    rows = []
    if not core_list:
        return rows
    P, R, MV, PE, ind_map = aux["P"], aux["R"], aux["MV"], aux["PE"], aux["ind_map"]
    MVmin = MV_MIN_YI * 1e4                      # 万元
    fin = load_fina(list(P.columns))
    qs = sorted(fin["end_date"].unique())
    fq = fin[fin["end_date"] == qs[-1]].set_index("ts_code")
    last = P.index[-1]
    # min_periods<窗口：容忍停牌日缺口，避免个别缺失把整段均线打成 NaN
    ma20 = P.rolling(20, min_periods=10).mean().iloc[-1]
    ma60 = P.rolling(60, min_periods=30).mean().iloc[-1]
    ma120 = P.rolling(120, min_periods=60).mean().iloc[-1]
    hi60 = P.shift(1).iloc[-60:].max()
    def fm(x):
        return "-" if pd.isna(x) else f"{x:.2f}"

    for nm in core_list:
        mem = [c for c in P.columns if ind_map[c] == nm]
        base = pd.DataFrame({
            "r60": R[60].loc[last].reindex(mem),
            "r120": R[120].loc[last].reindex(mem),
            "mv": MV.loc[last].reindex(mem),
            "price": P.loc[last].reindex(mem),
            "ma20": ma20.reindex(mem), "ma60": ma60.reindex(mem), "ma120": ma120.reindex(mem),
            "hi60": hi60.reindex(mem),
            "pe": PE.loc[last].reindex(mem),
        })
        base = base[base["mv"].notna() & (base["mv"] >= MVmin) & base["price"].notna()
                    & base["ma20"].notna()]
        keep = [not is_st(name_map.get(c, "")) for c in base.index]
        base = base[keep]
        if base.empty:
            continue
        base["np"] = fq["netprofit_yoy"].reindex(base.index)
        base["or"] = fq["or_yoy"].reindex(base.index)
        base["roe"] = fq["roe"].reindex(base.index)
        # 行业内部标准化 0-100
        base["rank_np"] = p_rank(base["np"])
        base["rank_or"] = p_rank(base["or"])
        base["rank_roe"] = p_rank(base["roe"])
        base["rank_r60"] = p_rank(base["r60"])
        base["rank_r120"] = p_rank(base["r120"])
        base["rank_mv"] = p_rank(base["mv"])
        base["rank_trend"] = p_rank(base["r120"] * 0.6 + base["r60"] * 0.4)
        # 估值：行业内正PE越小越好（便宜高分）
        base["pe_ok"] = base["pe"].where(base["pe"] > 0, np.nan)
        base["val_s"] = p_rank_desc(base["pe_ok"]).fillna(50)
        base["extend"] = base["price"] / base["ma20"] - 1
        # 子项(共80%)：盈利30 地位15 趋势15 资金10 估值10
        base["profit_s"] = base["rank_np"] * 0.5 + base["rank_or"] * 0.25 + base["rank_roe"] * 0.25
        base["pos_s"] = base["rank_mv"] * 0.6 + base["rank_r60"].fillna(0) * 0.4
        base["fund_s"] = base["rank_r120"].fillna(50) * 0.5 + base["rank_mv"] * 0.5   # 资金=流动性+趋势代理
        base["internal"] = (base["profit_s"] * 0.30 + base["pos_s"] * 0.15
                            + base["rank_trend"] * 0.15 + base["fund_s"] * 0.10
                            + base["val_s"] * 0.10) / 0.80
        base["score"] = base["internal"] * 0.80 + ind.loc[nm, "LONG"] * 0.20
        base = base.sort_values("score", ascending=False)
        lscore = ind.loc[nm, "LONG"]
        leader_code = ind.loc[nm].get("leader")
        for j, (code, s) in enumerate(base.head(3).iterrows()):
            price = s["price"]
            state, op = "观察", "WATCH"
            if s["extend"] > 0.30 or s["r60"] > 0.60:
                state = "已拉升/拥挤"
                op = "BUY_ON_PULLBACK：等回踩MA20-前高区再分批"
            elif s["extend"] > 0 and price >= s["ma60"]:
                state = "趋势走强"
                op = "BUY_ON_PULLBACK：回踩MA20不破分批；破MA20先观望"
            elif price >= s["ma120"] and s["r60"] > 0:
                state = "回踩蓄势"
                op = "WATCH→突破60日线后再纳入；守住MA120=质量回踩"
            elif price < s["ma120"]:
                state = "破位"
                op = "WAIT：趋势未修复，禁入"
            if j == 0 and code == leader_code:
                typ = "LEADER"
            elif j == 0:
                typ = "CORE_MID"
            else:
                typ = "SECONDARY"
            rows.append(
                f"| {nm} | {name_map.get(code, code)} | {code} | {typ} | {clip0(s['score']):.0f} | {lscore:.0f} | "
                f"{price:.2f} | {fm(s['ma20'])} | {fm(s['ma60'])} | {fm(s['ma120'])} | {fm(s['hi60'])} | "
                f"{state} | {op} |")
    return rows

if __name__ == "__main__":
    main()
