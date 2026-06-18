import os, json, sqlite3
from collections import defaultdict
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_TUSHARE = os.path.join(BASE_DIR, "cache_backbone_tushare")
KLINE_CACHE = os.path.join(BASE_DIR, "cache_daily")
DC_HOT_DIR = os.path.join(CACHE_TUSHARE, "dc_hot")
PORTFOLIO_DB = os.path.join(CACHE_TUSHARE, "theme_portfolio.db")
THEME_SCORE_DB = os.path.join(CACHE_TUSHARE, "theme_trend_sentiment.db")

def is_mainboard(code):
    try:
        symbol = str(code).split(".")[0]
        if str(code).endswith(".SH") and symbol.startswith("6"):
            return True
        if str(code).endswith(".SZ") and (symbol.startswith("00") or symbol.startswith("30")):
            return True
    except:
        pass
    return False

def load_kline(code):
    path = os.path.join(KLINE_CACHE, f"{code}.csv")
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
        if df.empty or len(df) < 120:
            return None
        df["trade_date"] = df["trade_date"].astype(str)
        df = df.sort_values("trade_date").reset_index(drop=True)
        return df
    except:
        return None

# 读取 portfolio
conn = sqlite3.connect(PORTFOLIO_DB)
cur = conn.cursor()
cur.execute("SELECT DISTINCT trade_date FROM portfolio ORDER BY trade_date DESC LIMIT 1")
latest = cur.fetchone()[0]
cur.execute("SELECT ts_code, name, theme_name, mcap FROM portfolio WHERE trade_date = ? LIMIT 20", (latest,))
portfolio = list(cur.fetchall())
conn.close()

# 读取主题评分
conn = sqlite3.connect(THEME_SCORE_DB)
cur = conn.cursor()
cur.execute("SELECT theme, composite_score, trend_score, rank FROM theme_scores WHERE trade_date = ?", (latest,))
theme_data = {r[0]: {"composite_score": float(r[1] or 0), "trend_score": float(r[2] or 0), "rank": int(r[3] or 0)} for r in cur.fetchall()}
conn.close()

# 读取热榜
stock_hot = defaultdict(lambda: {"dc_days": 0})
import os as os_module
from datetime import datetime, timedelta
end = datetime.now()
for i in range(130):
    d = (end - timedelta(days=i)).strftime("%Y%m%d")
    path = os.path.join(DC_HOT_DIR, f"dc_hot_{d}.csv")
    if not os.path.exists(path):
        continue
    try:
        df = pd.read_csv(path)
        code_col = df.columns[0]
        for idx, row in df.iterrows():
            code = str(row[code_col]).strip()
            if not code or code.lower() in ["nan", "none", ""]:
                continue
            if "." not in code and len(code) == 6:
                code = (code + ".SH") if code.startswith("6") else (code + ".SZ")
            stock_hot[code]["dc_days"] += 1
    except:
        pass

# 分析前20只股票的各项指标分布
results = []
for ts_code, name, theme_name, mcap in portfolio[:20]:
    if not is_mainboard(ts_code):
        continue
    kline = load_kline(ts_code)
    if kline is None:
        continue

    n = len(kline)
    close = kline["close"].astype(float).values
    pct = kline["pct_chg"].astype(float).values
    amount = kline["amount"].astype(float).values / 100000.0
    last = n - 1

    ma5 = close[last-4:last+1].mean()
    ma10 = close[last-9:last+1].mean()
    ma20 = close[last-19:last+1].mean()
    ma60 = close[last-59:last+1].mean()
    ma120 = close[last-119:last+1].mean() if n >= 120 else ma60

    bull = sum([close[last] > ma5, ma5 > ma10, ma10 > ma20, ma20 > ma60]) * 25
    bias_ma20 = (close[last] / ma20 - 1) * 100
    bias_ma60 = (close[last] / ma60 - 1) * 100

    def ret(offset, window):
        s = max(0, last - offset - window + 1)
        e = last - offset
        if close[s] == 0:
            return 0.0
        return (close[e] / close[s] - 1) * 100

    ret_120 = ret(0, 120)
    ret_60 = ret(0, 60)

    zt_120 = int(np.sum(pct[max(0,last-119):last+1] >= 9.5))

    avg_amt_20 = amount[max(0,last-19):last+1].mean()

    hot = stock_hot.get(ts_code, {"dc_days": 0})["dc_days"]

    theme_info = theme_data.get(theme_name, {})
    theme_score = theme_info.get("composite_score", 0)
    theme_trend = theme_info.get("trend_score", 0)
    theme_rank = theme_info.get("rank", 0)

    # 各项过滤条件
    industry_strength = 0.4*theme_score + 0.3*theme_score*0.8 + 0.3*min(100, zt_120*3 + hot*1.5)
    theme_rank_score = max(0, 100 - (theme_rank - 1) * 8) if theme_rank > 0 else 30
    attention_score = min(100, hot * 0.8)
    top3_days = min(120, hot * 6)
    active_score = top3_days / 120 * 100
    if avg_amt_20 < 5: cap = 20
    elif avg_amt_20 < 10: cap = 40
    elif avg_amt_20 < 20: cap = 60
    elif avg_amt_20 < 50: cap = 80
    else: cap = 100
    recognition = 0.30*theme_rank_score + 0.25*attention_score + 0.20*active_score + 0.25*cap

    dt_score = min(100, int(zt_120 * 0.3 + zt_120 * 0.5) * 15)
    hot_score = min(100, hot * 2)
    zt_score = min(100, zt_120 * 8)
    memory = 0.35*zt_score + 0.35*dt_score + 0.30*hot_score
    lead = max(0, min(100, top3_days / 120 * 100))
    rel_days = min(120, hot * 3)
    lps = 0.35*lead + 0.25*memory + 0.20*(rel_days/120*100) + 0.20*lead

    if ret_120 > 50 and bull >= 75:
        profit_yoy = 40.0
    elif ret_120 > 20 and bull >= 50:
        profit_yoy = 25.0
    else:
        profit_yoy = 15.0

    results.append({
        "name": name, "theme": theme_name,
        "bull": bull, "bias_20": bias_ma20, "bias_60": bias_ma60,
        "ret_60": ret_60, "ret_120": ret_120,
        "zt_120": zt_120, "hot": hot, "avg_amt": avg_amt_20,
        "theme_score": theme_score, "theme_rank": theme_rank,
        "industry": industry_strength,
        "theme_rank_s": theme_rank_score, "attn": attention_score, "active": active_score, "cap": cap,
        "recognition": recognition,
        "lead": lead, "memory": memory, "lps": lps,
        "profit_yoy": profit_yoy,
        "filter_bull": bull < 80,
        "filter_rec": recognition < 75,
        "filter_lps": lps < 80,
        "filter_ind": industry_strength < 70,
        "filter_py": profit_yoy < 30,
    })

print("=" * 140)
print(f"{'名称':<10}{'主题':<12}{'牛分':<6}{'乖离20':<8}{'乖离60':<8}{'ret60':<8}{'ret120':<8}{'zt':<5}{'热榜':<5}{'均额':<8}")
print(f"{'产业强':<8}{'排名的分':<8}{'曝光分':<8}{'活跃分':<8}{'容量分':<8}{'辨识度':<8}{'领导分':<8}{'记忆分':<8}{'LPS':<8}{'利润增速':<8}")
print(f"{'[过滤]牛<80':<12}{'[过滤]识<75':<12}{'[过滤]LPS<80':<12}{'[过滤]产业<70':<12}{'[过滤]利润<30':<12}")
print("-" * 140)
for r in results:
    filters = []
    if r["filter_bull"]: filters.append("牛<80")
    if r["filter_rec"]: filters.append("识<75")
    if r["filter_lps"]: filters.append("LPS<80")
    if r["filter_ind"]: filters.append("产业<70")
    if r["filter_py"]: filters.append("利润<30")
    print(f"{r['name']:<10}{r['theme']:<12}{r['bull']:<6.0f}{r['bias_20']:<8.1f}{r['bias_60']:<8.1f}{r['ret_60']:<8.1f}{r['ret_120']:<8.1f}{r['zt_120']:<5}{r['hot']:<5}{r['avg_amt']:<8.1f}")
    print(f"{r['industry']:<8.1f}{r['theme_rank_s']:<8.1f}{r['attn']:<8.1f}{r['active']:<8.1f}{r['cap']:<8.0f}{r['recognition']:<8.1f}{r['lead']:<8.1f}{r['memory']:<8.1f}{r['lps']:<8.1f}{r['profit_yoy']:<8.0f}")
    print(f"  过滤: {', '.join(filters) if filters else '全部通过'}")
    print()
