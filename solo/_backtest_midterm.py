# -*- coding: utf-8 -*-
"""
回测验证：中线右侧买点评分 与 未来上涨概率 是否正相关
方法：对入围股票池(118只)滚动计算过去60个交易日的中线买点评分，
      统计各分数档位的未来5/10/20日上涨概率与平均收益，并计算Spearman相关系数。
"""
import pandas as pd
import numpy as np
import sys, os, glob
from scipy.stats import spearmanr
sys.path.insert(0, r"d:\mystock\solo")
import treasure_hunter as th

np.seterr(all='ignore')

# ── 载入入围股票 ──
csv_path = r"d:\mystock\solo\report_daily\treasure_hunt_20260803.csv"
df_pool = pd.read_csv(csv_path)
print(f"入围股票池: {len(df_pool)} 只\n")


def build_row_at(slice_df):
    """用截至某日的日线切片计算指标"""
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
    """未来n日累计收益（用pct_chg复权涨跌幅累乘）"""
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

    n = len(daily)
    # 从第60根K线开始，到倒数第21根（留20天未来）
    for i in range(60, n - 20):
        slice_df = daily.iloc[:i+1]
        row = build_row_at(slice_df)
        if row is None:
            continue
        score = th._compute_midterm_buy_score(row)['中线买点总分']
        f5 = fwd_ret(pct_chg, i+1, 5)
        f10 = fwd_ret(pct_chg, i+1, 10)
        f20 = fwd_ret(pct_chg, i+1, 20)
        if f5 is None or f10 is None or f20 is None:
            continue
        records.append({
            'code': code, 'name': name, 'date': daily.iloc[i]['trade_date'],
            'score': score, 'f5': f5, 'f10': f10, 'f20': f20,
        })
    processed += 1

res = pd.DataFrame(records)
print(f"成功回测股票数: {processed}")
print(f"样本总数: {len(res)}")
print(f"回测区间: {res['date'].min()} ~ {res['date'].max()}")
print(f"评分分布: min={res['score'].min():.1f} max={res['score'].max():.1f} "
      f"均值={res['score'].mean():.1f} 中位数={res['score'].median():.1f}")

# ── Spearman 相关系数 ──
print("\n" + "=" * 70)
print("  Spearman 相关系数（评分 vs 未来收益）")
print("=" * 70)
for col, label in [('f5', '未来5日'), ('f10', '未来10日'), ('f20', '未来20日')]:
    rho, p = spearmanr(res['score'], res[col])
    print(f"  {label}: rho={rho:+.4f}  p值={p:.2e}  {'✓显著' if p < 0.05 else '✗不显著'}")

# ── 分档统计 ──
print("\n" + "=" * 70)
print("  分档统计：各评分档位的上涨概率与平均收益")
print("=" * 70)
bins = [0, 20, 40, 55, 65, 75, 101]
labels = ['0-20', '20-40', '40-55', '55-65', '65-75', '75-100']
res['band'] = pd.cut(res['score'], bins=bins, labels=labels, right=False)
print(f"{'档位':<8}{'样本':>6}{'5日涨率':>9}{'10日涨率':>10}{'20日涨率':>10}"
      f"{'5日均收益':>10}{'10日均收益':>11}{'20日均收益':>11}")
for lb in labels:
    band = res[res['band'] == lb]
    if len(band) == 0:
        print(f"{lb:<8}{0:>6}   (无样本)")
        continue
    p5 = (band['f5'] > 0).mean() * 100
    p10 = (band['f10'] > 0).mean() * 100
    p20 = (band['f20'] > 0).mean() * 100
    m5 = band['f5'].mean() * 100
    m10 = band['f10'].mean() * 100
    m20 = band['f20'].mean() * 100
    print(f"{lb:<8}{len(band):>6}{p5:>8.1f}%{p10:>9.1f}%{p20:>9.1f}%"
          f"{m5:>+9.2f}%{m10:>+10.2f}%{m20:>+10.2f}%")

# ── 二元检验：高分组 vs 低分组 ──
print("\n" + "=" * 70)
print("  高低分组对比（≥60分 vs <60分）")
print("=" * 70)
hi = res[res['score'] >= 60]
lo = res[res['score'] < 60]
for col, label in [('f5', '未来5日'), ('f10', '未来10日'), ('f20', '未来20日')]:
    ph = (hi[col] > 0).mean() * 100
    pl = (lo[col] > 0).mean() * 100
    mh = hi[col].mean() * 100
    ml = lo[col].mean() * 100
    print(f"  {label}: 高分(≥60)上涨概率 {ph:>5.1f}% / 均收益 {mh:+6.2f}%   |   "
          f"低分(<60)上涨概率 {pl:>5.1f}% / 均收益 {ml:+6.2f}%")

print("\n验证完成")
