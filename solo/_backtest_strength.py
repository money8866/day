# -*- coding: utf-8 -*-
"""
回测验证：右侧启动强度因子 是否具备"排名越前，上涨概率越大"的单调性
因子逻辑（满分100）：
  1. 启动新鲜度 30分 — 站上MA20天数越短越好（1-3天满分，>25天低分）
  2. 趋势拐点   25分 — MA20斜率刚由负转正最优（0-1%满分，>3%陡峭追高扣分）
  3. 评分加速   20分 — 5日评分提升幅度，小步提升(0-5)最优，暴增(>30)扣分
  4. 空间约束   15分 — 距120日高15-30%最优，<5%透支或>60%超跌均降权
  5. 量价健康   10分 — 站上MA20后缩量整理或温和放量
检验：按因子值分10组(decile)，考察各组未来5/10/20日收益是否单调递减，
      Spearman相关系数是否显著为正。
"""
import pandas as pd
import numpy as np
import sys, os, glob
from scipy.stats import spearmanr
sys.path.insert(0, r"d:\mystock\solo")
import treasure_hunter as th

np.seterr(all='ignore')
csv_path = r"d:\mystock\solo\report_daily\treasure_hunt_20260803.csv"
df_pool = pd.read_csv(csv_path)
print(f"入围股票池: {len(df_pool)} 只\n")


def midterm_strength(row, days_above_ma20, delta_score):
    """
    右侧回踩确认强度因子（满分100）
    经典右侧买点：突破站上MA20后，回踩MA20不破（缩量+趋势支撑）
    1. 站上MA20新鲜度  20分 — 站上1-10天内有效，越久越衰减
    2. 回踩到位       30分 — 收盘距MA20 0~+4%为最优回踩区，离得远=追高
    3. 趋势支撑       25分 — MA20斜率刚转正(0~2%)最优；MA20>MA60加分
    4. 缩量回调       15分 — 量比0.5~1.0（缩量回调），放量回调减分
    5. 空间未透支     10分 — 距120日高>15%才有向上空间
    """
    s = 0.0
    # 1. 站上MA20新鲜度 20
    if days_above_ma20 >= 1:
        if days_above_ma20 <= 5:
            s += 20
        elif days_above_ma20 <= 10:
            s += 15
        elif days_above_ma20 <= 20:
            s += 8
        else:
            s += 2
    # 2. 回踩到位 30：收盘距MA20在0~+4%（回踩到支撑附近）
    pct_ma20 = row['pct_below_ma20']  # 正=在MA20上方
    if pct_ma20 >= 0:
        if pct_ma20 <= 1.5:
            s += 30       # 贴着MA20（回踩到位）
        elif pct_ma20 <= 4:
            s += 24       # 回踩区上沿
        elif pct_ma20 <= 8:
            s += 14       # 离MA20偏远，追高
        elif pct_ma20 <= 15:
            s += 6
        else:
            s += 2        # 远离均线，透支
    # 3. 趋势支撑 25：MA20斜率刚转正
    slope = row['ma20_slope']
    if 0 < slope <= 1.0:
        s += 18
    elif 1.0 < slope <= 2.5:
        s += 13
    elif slope <= 0:
        s += 4   # 走平/向下
    else:
        s += 6   # 陡峭
    # MA20 > MA60 多头排列（+7）
    if row['ma20'] > row['ma60']:
        s += 7
    # 4. 缩量回调 15
    vr = row['volume_ratio']
    if 0.5 <= vr < 1.0:
        s += 15   # 缩量回调，卖压枯竭
    elif 1.0 <= vr < 1.3:
        s += 8
    elif vr >= 1.5:
        s += 0    # 放量回调，警惕出货
    elif vr < 0.5:
        s += 6    # 极度缩量（可能流动性枯竭）
    # 5. 空间未透支 10
    ph = row['pct_from_120d_high']
    if ph > 15:
        s += 10
    elif ph > 8:
        s += 5
    return min(100.0, s)


def build_row_at(slice_df):
    if len(slice_df) < 60:
        return None
    closes = slice_df['close'].astype(float).values
    current_close = float(closes[-1])
    recent_120 = slice_df.tail(120)
    high_120 = float(recent_120['high'].max())
    pct_from_high = (high_120 - current_close) / high_120 * 100 if high_120 > 0 else 999
    recent_60 = slice_df.tail(60)
    low_60 = float(recent_60['low'].min())
    pct_from_60d_low = (current_close - low_60) / low_60 * 100 if low_60 > 0 else 999
    ma20 = slice_df['close'].rolling(20).mean()
    ma60 = slice_df['close'].rolling(60).mean()
    ma20_val = float(ma20.dropna().iloc[-1])
    ma60_val = float(ma60.dropna().iloc[-1])
    pct_below_ma20 = (current_close - ma20_val) / ma20_val * 100
    pct_below_ma60 = (current_close - ma60_val) / ma60_val * 100
    ma20_prev = float(ma20.dropna().iloc[-6]) if len(ma20.dropna()) >= 6 else ma20_val
    ma20_slope = (ma20_val - ma20_prev) / ma20_prev * 100
    volumes = slice_df['vol'].astype(float).values
    vol_5d = np.mean(volumes[-5:]); vol_20d = np.mean(volumes[-20:])
    volume_ratio = vol_5d / vol_20d if vol_20d > 0 else 1.0
    close_s = pd.Series(closes)
    dif = close_s.ewm(span=12, adjust=False).mean() - close_s.ewm(span=26, adjust=False).mean()
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = (dif - dea) * 2
    golden = bool(dif.iloc[-1] > dea.iloc[-1] and dif.iloc[-2] <= dea.iloc[-2])
    recent_chg_1d = (closes[-1]/closes[-2]-1)*100 if len(closes) >= 2 and closes[-2] > 0 else 0
    recent_chg_2d = (closes[-1]/closes[-3]-1)*100 if len(closes) >= 3 and closes[-3] > 0 else 0
    last = slice_df.iloc[-1]
    is_neg = bool(last['close'] < last['open'])
    return {
        'current_close': current_close, 'pct_from_120d_high': pct_from_high,
        'ma20_slope': ma20_slope, 'rsi_14': 50,
        'pct_below_ma20': pct_below_ma20, 'pct_below_ma60': pct_below_ma60,
        'volume_ratio': volume_ratio, 'pct_from_60d_low': pct_from_60d_low,
        'ma20': ma20_val, 'ma60': ma60_val,
        'macd_dif': float(dif.iloc[-1]), 'macd_dea': float(dea.iloc[-1]),
        'macd_hist': float(hist.iloc[-1]), 'macd_golden_cross': golden,
        'days_since_60d_low': 5, 'recent_chg_1d': recent_chg_1d,
        'recent_chg_2d': recent_chg_2d, 'is_negative_day': is_neg,
    }


def fwd_ret(pct_arr, start, n):
    seg = pct_arr[start:start+n]
    if len(seg) < n:
        return None
    return float(np.prod(1 + seg / 100) - 1)


records = []
processed = 0
for _, sr in df_pool.iterrows():
    code, name = sr['ts_code'], sr['name']
    code_key = code.replace('.', '_')
    files = glob.glob(rf"D:\mystock\cache_daily\treasure_daily_{code_key}_*.parquet")
    if not files:
        continue
    files.sort(key=os.path.getmtime, reverse=True)
    try:
        daily = pd.read_parquet(files[0])
    except Exception:
        continue
    daily = daily.sort_values('trade_date').reset_index(drop=True)
    if len(daily) < 100:
        continue

    closes = daily['close'].astype(float).values
    pct_chg = daily['pct_chg'].astype(float).values if 'pct_chg' in daily.columns else np.zeros(len(daily))
    ma20_series = daily['close'].rolling(20).mean()
    n = len(daily)

    # 预计算每个时点的基础评分（用于delta）
    score_arr = {}
    os_arr = {}
    for i in range(60, n - 20):
        row = build_row_at(daily.iloc[:i+1])
        if row is None:
            continue
        score_arr[i] = th._compute_midterm_buy_score(row)['中线买点总分']
        os_arr[i] = th._compute_oversold_score(row)['超跌总分']

    for i in range(60, n - 20):
        if i not in score_arr or (i - 5) not in score_arr:
            continue
        row = build_row_at(daily.iloc[:i+1])
        if row is None:
            continue
        # 连续站上MA20天数
        above = closes[:i+1] > ma20_series.values[:i+1]
        d = 0
        for j in range(i, -1, -1):
            if above[j] and not np.isnan(ma20_series.values[j]):
                d += 1
            else:
                break
        delta_score = score_arr[i] - score_arr[i-5]
        strength = midterm_strength(row, d, delta_score)
        f5 = fwd_ret(pct_chg, i+1, 5)
        f10 = fwd_ret(pct_chg, i+1, 10)
        f20 = fwd_ret(pct_chg, i+1, 20)
        if f5 is None or f10 is None or f20 is None:
            continue
        records.append({
            'code': code, 'name': name, 'date': daily.iloc[i]['trade_date'],
            'strength': strength, 'score': score_arr[i], 'delta': delta_score,
            'oversold': os_arr.get(i, 50),
            'f5': f5, 'f10': f10, 'f20': f20,
        })
    processed += 1

res = pd.DataFrame(records)
print(f"成功回测: {processed} 只, 样本 {len(res)} 个, 区间 {res['date'].min()}~{res['date'].max()}")
print(f"因子分布: min={res['strength'].min():.1f} max={res['strength'].max():.1f} "
      f"均值={res['strength'].mean():.1f} 中位={res['strength'].median():.1f}")

# ── Spearman ──
print("\n" + "=" * 70)
print("  Spearman 相关系数（强度因子 vs 未来收益）")
print("=" * 70)
for col, label in [('f5', '未来5日'), ('f10', '未来10日'), ('f20', '未来20日')]:
    rho, p = spearmanr(res['strength'], res[col])
    print(f"  {label}: rho={rho:+.4f}  p={p:.2e}  {'✓显著' if p < 0.05 else '✗不显著'}")

# ── 十分位组检验（关键：验证单调性） ──
print("\n" + "=" * 70)
print("  十分位组检验（D1=最高分 ... D10=最低分）")
print("=" * 70)
res['decile'] = pd.qcut(res['strength'].rank(method='first'), 10, labels=False) + 1
rows = []
for d in range(1, 11):
    grp = res[res['decile'] == d]
    rows.append({
        'D': d,
        'n': len(grp),
        'avg': grp['strength'].mean(),
        'p5': (grp['f5'] > 0).mean() * 100,
        'm5': grp['f5'].mean() * 100,
        'p10': (grp['f10'] > 0).mean() * 100,
        'm10': grp['f10'].mean() * 100,
        'p20': (grp['f20'] > 0).mean() * 100,
        'm20': grp['f20'].mean() * 100,
    })
tbl = pd.DataFrame(rows)
print(f"{'D':>3}{'样本':>6}{'均分':>7}{'5日涨率':>9}{'5日均收':>9}{'10日涨率':>10}{'10日均收':>10}{'20日涨率':>10}{'20日均收':>10}")
for _, r in tbl.iterrows():
    print(f"{int(r['D']):>3}{int(r['n']):>6}{r['avg']:>7.1f}"
          f"{r['p5']:>8.1f}%{r['m5']:>+8.2f}%{r['p10']:>9.1f}%{r['m10']:>+9.2f}%"
          f"{r['p20']:>9.1f}%{r['m20']:>+9.2f}%")

# ── 单调性检验：D1(高) vs D10(低) 及 D1-D5 vs D6-D10 ──
print("\n" + "=" * 70)
print("  单调性检验")
print("=" * 70)
top = res[res['decile'] <= 3]
bot = res[res['decile'] >= 8]
for col, label in [('f5', '5日'), ('f10', '10日'), ('f20', '20日')]:
    print(f"  {label}: 前30%涨率 {(top[col]>0).mean()*100:>5.1f}%/均收 {top[col].mean()*100:+6.2f}%"
          f"  vs  后30%涨率 {(bot[col]>0).mean()*100:>5.1f}%/均收 {bot[col].mean()*100:+6.2f}%")

# ── 对照：超跌评分 相关性（判断是否为市场环境影响） ──
print("\n" + "=" * 70)
print("  对照实验：超跌评分 相关性（同区间）")
print("=" * 70)
for col, label in [('f5', '未来5日'), ('f10', '未来10日'), ('f20', '未来20日')]:
    rho, p = spearmanr(res['oversold'], res[col])
    print(f"  超跌分 vs {label}: rho={rho:+.4f}  p={p:.2e}  {'✓显著' if p < 0.05 else '✗不显著'}")

# 超跌分十分位
print("\n  超跌分十分位（D1=最高超跌分）:")
res['os_dec'] = pd.qcut(res['oversold'].rank(method='first'), 10, labels=False) + 1
os_rows = []
for d in range(1, 11):
    grp = res[res['os_dec'] == d]
    os_rows.append({'D': d, 'n': len(grp), 'avg': grp['oversold'].mean(),
                    'p10': (grp['f10'] > 0).mean() * 100, 'm10': grp['f10'].mean() * 100,
                    'p20': (grp['f20'] > 0).mean() * 100, 'm20': grp['f20'].mean() * 100})
print(f"{'D':>3}{'样本':>6}{'均超跌分':>9}{'10日涨率':>10}{'10日均收':>10}{'20日涨率':>10}{'20日均收':>10}")
for r in os_rows:
    print(f"{r['D']:>3}{r['n']:>6}{r['avg']:>9.1f}{r['p10']:>9.1f}%{r['m10']:>+9.2f}%"
          f"{r['p20']:>9.1f}%{r['m20']:>+9.2f}%")

# ── 组合：超跌分 ≥60 且 强度分排序 ──
print("\n" + "=" * 70)
print("  组合筛选（超跌≥60 的样本内，按强度分十分位）")
print("=" * 70)
sub = res[res['oversold'] >= 60].copy()
if len(sub) > 200:
    sub['dec2'] = pd.qcut(sub['strength'].rank(method='first'), 10, labels=False) + 1
    print(f"  样本: {len(sub)}")
    for col, label in [('f5', '5日'), ('f10', '10日'), ('f20', '20日')]:
        rho, p = spearmanr(sub['strength'], sub[col])
        print(f"  {label}: rho={rho:+.4f}  p={p:.2e}  {'✓显著' if p < 0.05 else '✗不显著'}")
    print("\n  组合内十分位（D1=强度最高）:")
    print(f"{'D':>3}{'样本':>6}{'均强度':>8}{'5日涨率':>9}{'5日均收':>9}{'10日涨率':>10}{'10日均收':>10}{'20日涨率':>10}{'20日均收':>10}")
    for d in range(1, 11):
        grp = sub[sub['dec2'] == d]
        print(f"{d:>3}{len(grp):>6}{grp['strength'].mean():>8.1f}"
              f"{(grp['f5']>0).mean()*100:>8.1f}%{grp['f5'].mean()*100:>+8.2f}%"
              f"{(grp['f10']>0).mean()*100:>9.1f}%{grp['f10'].mean()*100:>+9.2f}%"
              f"{(grp['f20']>0).mean()*100:>9.1f}%{grp['f20'].mean()*100:>+9.2f}%")
else:
    print(f"  样本不足: {len(sub)}")

# ── 阈值敏感性 ──
print("\n" + "=" * 70)
print("  阈值敏感性：不同超跌门槛下，强度分排序相关性")
print("=" * 70)
print(f"{'超跌门槛':>8}{'样本':>7}{'5日rho':>10}{'10日rho':>11}{'20日rho':>11}")
for thr in [40, 50, 60, 70, 80]:
    s = res[res['oversold'] >= thr]
    if len(s) < 100:
        print(f"{thr:>8}{len(s):>7}  (样本不足)")
        continue
    r5, _ = spearmanr(s['strength'], s['f5'])
    r10, _ = spearmanr(s['strength'], s['f10'])
    r20, _ = spearmanr(s['strength'], s['f20'])
    print(f"{thr:>8}{len(s):>7}{r5:>+10.4f}{r10:>+11.4f}{r20:>+11.4f}")

print("\n验证完成")
