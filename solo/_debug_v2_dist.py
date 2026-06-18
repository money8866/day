import os, sqlite3
from collections import defaultdict
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

BASE = r"D:\mystock\solo"
KLINE = os.path.join(BASE, "cache_daily")
DC_HOT = os.path.join(BASE, "cache_backbone_tushare", "dc_hot")
PORTFOLIO = os.path.join(BASE, "cache_backbone_tushare", "theme_portfolio.db")
THEMESCORE = os.path.join(BASE, "cache_backbone_tushare", "theme_trend_sentiment.db")

def is_mainboard(code):
    try:
        s = str(code).split(".")[0]
        if str(code).endswith(".SH") and s.startswith("6"): return True
        if str(code).endswith(".SZ") and (s.startswith("00") or s.startswith("30")): return True
    except: pass
    return False

def load_kline(code):
    p = os.path.join(KLINE, f"{code}.csv")
    if not os.path.exists(p): return None
    try:
        df = pd.read_csv(p)
        if df.empty or len(df) < 120: return None
        df["trade_date"] = df["trade_date"].astype(str)
        return df.sort_values("trade_date").reset_index(drop=True)
    except: return None

# portfolio
conn = sqlite3.connect(PORTFOLIO)
cur = conn.cursor()
cur.execute("SELECT trade_date FROM portfolio ORDER BY trade_date DESC LIMIT 1")
latest = cur.fetchone()[0]
cur.execute("SELECT ts_code, theme_name, mcap FROM portfolio WHERE trade_date = ?", (latest,))
portfolio = {r[0]: {"theme": r[1], "mcap": float(r[2])} for r in cur.fetchall()}
conn.close()

# theme scores
conn = sqlite3.connect(THEMESCORE)
cur = conn.cursor()
cur.execute("SELECT theme, composite_score, trend_score FROM theme_scores WHERE trade_date = ?", (latest,))
theme_data = {r[0]: {"score": float(r[1] or 0), "trend": float(r[2] or 0)} for r in cur.fetchall()}
conn.close()

# hot
hot = defaultdict(lambda: 0)
end = datetime.now()
for i in range(130):
    d = (end - timedelta(days=i)).strftime("%Y%m%d")
    p = os.path.join(DC_HOT, f"dc_hot_{d}.csv")
    if not os.path.exists(p): continue
    try:
        df = pd.read_csv(p)
        col = df.columns[0]
        for idx, row in df.iterrows():
            code = str(row[col]).strip()
            if not code or code.lower() in ["nan", "none", ""]: continue
            if "." not in code and len(code) == 6:
                code = (code + ".SH") if code.startswith("6") else (code + ".SZ")
            hot[code] += 1
    except: pass

# 计算每个指标的分布
stats = {
    "industry_strength": [], "bull_score": [], "recognition": [],
    "lps": [], "profit_yoy": [], "mcap": [], "avg_amt": []
}

samples = []
for code, info in portfolio.items():
    if not is_mainboard(code): continue
    kl = load_kline(code)
    if kl is None: continue

    n = len(kl)
    close = kl["close"].astype(float).values
    pct = kl["pct_chg"].astype(float).values
    amount = kl["amount"].astype(float).values / 100000.0
    last = n - 1

    ma5 = close[last-4:last+1].mean()
    ma10 = close[last-9:last+1].mean()
    ma20 = close[last-19:last+1].mean()
    ma60 = close[last-59:last+1].mean()
    ma120 = close[last-119:last+1].mean() if n >= 120 else ma60

    bull = sum([close[last]>ma5, ma5>ma10, ma10>ma20, ma20>ma60]) * 25

    def ret(w):
        s = max(0, last-w+1)
        return (close[last]/close[s]-1)*100 if close[s]>0 else 0

    r120 = ret(120)
    r60 = ret(60)

    zt = int(np.sum(pct[max(0,last-119):last+1] >= 9.5))
    avg_amt = amount[max(0,last-19):last+1].mean()
    dc_days = hot.get(code, 0)
    theme_info = theme_data.get(info["theme"], {})
    ts = theme_info.get("score", 0)
    tt = theme_info.get("trend", 0)

    top3 = min(120, int(dc_days*2.5 + zt*3))
    theme_rank_s = 50  # 默认中等排名

    industry = round(0.4*ts + 0.3*(0.7*ts+0.3*tt) + 0.3*min(100, top3*0.5+zt*2+dc_days*0.8), 1)
    attn = min(100, dc_days*0.8)
    cap = 100 if avg_amt>=50 else 80 if avg_amt>=20 else 60 if avg_amt>=10 else 40 if avg_amt>=5 else 20
    active = top3/120*100
    rec = round(0.30*theme_rank_s + 0.25*attn + 0.20*active + 0.25*cap, 1)
    lead = max(0, min(100, top3/120*100))
    mem = round(0.35*min(100,zt*8) + 0.35*min(100,int(zt*0.3+zt*0.5)*15) + 0.30*min(100,dc_days*2), 1)
    lps = round(0.35*lead + 0.25*mem + 0.20*(top3/120*100) + 0.20*lead, 1)
    py = 40 if r120>50 and bull>=75 else 25 if r120>20 and bull>=50 else 15

    stats["industry_strength"].append(industry)
    stats["bull_score"].append(bull)
    stats["recognition"].append(rec)
    stats["lps"].append(lps)
    stats["profit_yoy"].append(py)
    stats["mcap"].append(info["mcap"])
    stats["avg_amt"].append(avg_amt)

    samples.append({
        "code": code, "name": info["theme"], "mcap": info["mcap"],
        "industry": industry, "bull": bull, "rec": rec, "lps": lps, "py": py,
        "dc_days": dc_days, "zt": zt, "top3": top3, "avg_amt": avg_amt,
        "ts": ts, "theme_rank_s": theme_rank_s, "attn": attn, "active": active, "cap": cap,
        "lead": lead, "mem": mem
    })

print("=" * 100)
print("各指标分布统计")
print("=" * 100)
for k, v in stats.items():
    arr = np.array(v)
    print(f"{k:<20} min={arr.min():.1f}  p25={np.percentile(arr,25):.1f}  p50={np.percentile(arr,50):.1f}  p75={np.percentile(arr,75):.1f}  max={arr.max():.1f}")

# 找出通过所有过滤的数量
passed = 0
for s in samples:
    if s["industry"] >= 60 and s["bull"] >= 80 and s["rec"] >= 75 and s["lps"] >= 80 and s["py"] >= 30:
        passed += 1
print(f"\n通过所有强制过滤的股票: {passed} 只")

# 显示通过产业过滤的股票中，各项指标分布
print("\n通过产业强度>=60的股票中，各项指标分布:")
industry_passed = [s for s in samples if s["industry"] >= 60]
for k in ["bull_score", "recognition", "lps", "profit_yoy"]:
    arr = np.array(stats[k])
    print(f"  {k}: 通过={len([x for x in industry_passed if x[k.split('_')[0]] >= (80 if 'bull' in k else 75 if 'rec' in k else 80 if 'lps' in k else 30)])}")

# 显示 Top10 按 industry_strength 排序
industry_passed.sort(key=lambda x: x["industry"], reverse=True)
print("\n产业强度 Top10:")
print(f"{'代码':<12}{'主题':<16}{'产业强':<10}{'牛分':<8}{'识分':<8}{'LPS':<8}{'涨停':<8}{'热榜':<8}{'ts':<8}{'top3':<8}{'均额':<8}")
for s in industry_passed[:10]:
    print(f"{s['code']:<12}{s['name']:<16}{s['industry']:<10.1f}{s['bull']:<8.0f}{s['rec']:<8.1f}{s['lps']:<8.1f}{s['zt']:<8}{s['dc_days']:<8}{s['ts']:<8.1f}{s['top3']:<8}{s['avg_amt']:<8.1f}")
