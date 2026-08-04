# -*- coding: utf-8 -*-
"""回测验证"主升后回调"择时因子：分数越高，未来上涨概率越大？
样本: 全量名单(20260803, 市值20~300亿) x 2026-01~07 滚动日
指标: Spearman 秩相关 + 十分位单调性（5/10/20日未来收益）
"""
import pandas as pd
import numpy as np
import glob
from scipy.stats import spearmanr

np.seterr(all='ignore')

# ── 1. 样本池：今日全量名单 ──
all_df = pd.read_csv(r"d:\mystock\solo\report_daily\treasure_hunt_all_20260803.csv")
codes = all_df['ts_code'].tolist()
print("全量名单股票数:", len(codes))


def pb_factor(rally_gain, pullback, vol5, pct_ma20, ma20_slope, ma20, ma60, pct_high, chg1, neg):
    s1 = 30 if rally_gain >= 50 else 24 if rally_gain >= 35 else 18 if rally_gain >= 25 else 10 if rally_gain >= 18 else 0
    s2 = 25 if 8 <= pullback <= 18 else 18 if (5 <= pullback < 8 or 18 < pullback <= 25) else 10 if (3 <= pullback < 5 or 25 < pullback <= 35) else 0
    s3 = 20 if 0.4 <= vol5 < 0.8 else 12 if 0.8 <= vol5 < 1.0 else 6 if 1.0 <= vol5 < 1.3 else 0 if vol5 >= 1.5 else 8 if vol5 < 0.4 else 2
    s4 = (5 if pct_ma20 > 0 else 2 if pct_ma20 > -3 else 0) + (5 if ma20_slope > 0 else 0) + (5 if ma20 > 0 and ma60 > 0 and ma20 > ma60 else 0)
    s5 = 10 if pct_high > 25 else 6 if pct_high > 10 else 2
    pen = min(20.0, (15.0 if chg1 <= -9.5 else 0) + (8.0 if neg and vol5 > 1.3 else 0))
    return max(0.0, min(100.0, s1 + s2 + s3 + s4 + s5 - pen))


samples = []
n_stock = 0
for code in codes:
    files = glob.glob(r"D:\mystock\cache_daily\treasure_daily_%s_*.parquet" % code.replace('.', '_'))
    if not files:
        continue
    df = pd.read_parquet(files[0])
    df = df.sort_values('trade_date').reset_index(drop=True)
    n = len(df)
    if n < 80:
        continue
    n_stock += 1
    o = df['open'].values.astype(float)
    h = df['high'].values.astype(float)
    l = df['low'].values.astype(float)
    c = df['close'].values.astype(float)
    v = df['vol'].values.astype(float)
    pc = df['pct_chg'].values.astype(float)

    s = pd.Series(c)
    ma20_s = s.rolling(20).mean().values
    ma60_s = s.rolling(60).mean().values
    ma10_s = s.rolling(10).mean().values
    ma20_prev5 = pd.Series(c).rolling(20).mean().shift(5).values
    high120 = pd.Series(h).rolling(120, min_periods=1).max().values
    low60 = pd.Series(l).rolling(60, min_periods=60).min().values

    # 主升浪参考高 = 60日最低点之后最高价
    rally_high = np.full(n, np.nan)
    for i in range(59, n):
        w = l[i - 59:i + 1]
        li = i - 59 + int(np.argmin(w))
        rally_high[i] = h[li:i + 1].max()

    for i in range(59, n - 20):
        if np.isnan(ma60_s[i]) or np.isnan(rally_high[i]) or rally_high[i] <= 0:
            continue
        ma20v = ma20_s[i]
        if np.isnan(ma20v) or ma20v <= 0:
            continue
        pct_ma20 = (c[i] - ma20v) / ma20v * 100
        pct_high = (high120[i] - c[i]) / high120[i] * 100 if high120[i] > 0 else 999
        slope = (ma20v - ma20_prev5[i]) / ma20_prev5[i] * 100 if not np.isnan(ma20_prev5[i]) and ma20_prev5[i] > 0 else 0
        vol5 = v[i] / np.mean(v[i - 5:i]) if np.mean(v[i - 5:i]) > 0 else 1.0
        rally_gain = (rally_high[i] - low60[i]) / low60[i] * 100 if low60[i] > 0 else 0
        pullback = (rally_high[i] - c[i]) / rally_high[i] * 100
        neg = bool(c[i] < o[i])
        score = pb_factor(rally_gain, pullback, vol5, pct_ma20, slope, ma20v, ma60_s[i], pct_high, pc[i], neg)
        f5 = (c[i + 5] / c[i] - 1) * 100
        f10 = (c[i + 10] / c[i] - 1) * 100
        f20 = (c[i + 20] / c[i] - 1) * 100
        samples.append((code, df['trade_date'].iloc[i], score, rally_gain, pullback, f5, f10, f20, pc[i],
                        pc[i - 1], l[i], l[i - 1], c[i] > o[i], vol5, ma10_s[i]))

res = pd.DataFrame(samples, columns=['code', 'date', 'score', 'rally_gain', 'pullback', 'f5', 'f10', 'f20', 'chg1',
                                     'chg_prev', 'low_today', 'low_prev', 'yang', 'vol5', 'ma10'])
print("有效样本:", len(res), " 涉及股票:", n_stock)
res.to_csv(r"d:\mystock\solo\_pullback_samples.csv", index=False)
print("样本已保存 _pullback_samples.csv")


def stat(df, label, col='f20'):
    if len(df) < 100:
        print(f"  [{label}] 样本太少({len(df)})")
        return
    rho, p = spearmanr(df['score'], df[col])
    print(f"  [{label}] n={len(df)}  Spearman(rho={rho:+.3f}, p={p:.2e})  得分均{df['score'].mean():.1f}")
    # 十分位单调性
    df = df.copy()
    try:
        df['dec'] = pd.qcut(df['score'], 10, labels=False, duplicates='drop')
    except Exception:
        return
    g = df.groupby('dec')[col].agg(['mean', 'count'])
    win = (df[col] > 0).mean()
    print(f"  十分位(score低→高) 未来20日均收益%: " + " ".join(f"{x:.1f}" for x in g['mean']))
    print(f"  十分位20日上涨概率%: " + " ".join(f"{x:.0f}" for x in g['mean'].index.map(
        lambda k: df[df['dec'] == k].eval(col + ' > 0').mean() * 100)))


print("\n════ 全样本（未过滤，含无主升结构日） ════")
for col in ['f5', 'f10', 'f20']:
    stat(res, col, col)

print("\n════ 主升结构子集（rally_gain≥25%）变体分析 ════")
sub = res[res['rally_gain'] >= 25].copy()
print("  子集样本:", len(sub), " 占全样本 %.1f%%" % (100 * len(sub) / len(res)))


def variant(name, mask, sub=sub):
    seg = sub[mask]
    if len(seg) < 100:
        print(f"  [{name}] n={len(seg)} 样本不足")
        return
    rho20, p = spearmanr(seg['score'], seg['f20'])
    rho10, _ = spearmanr(seg['score'], seg['f10'])
    rho5, _ = spearmanr(seg['score'], seg['f5'])
    print(f"  [{name}] n={len(seg):>6}  20日胜率={100 * (seg['f20'] > 0).mean():.1f}%  "
          f"20日={seg['f20'].mean():+.2f}%  10日={seg['f10'].mean():+.2f}%  5日={seg['f5'].mean():+.2f}%  "
          f"rho(5/10/20)={rho5:+.3f}/{rho10:+.3f}/{rho20:+.3f}")


print("\n  各企稳确认门槛（在主升结构内）:")
variant("基准(无门槛)", sub['score'] >= 0)
variant("当日阳线", sub['yang'])
variant("缩量(当日量/前5日<1.0)", sub['vol5'] < 1.0)
variant("当日阳线+缩量", sub['yang'] & (sub['vol5'] < 1.0))
variant("未创新低(当日低>=昨低)", sub['low_today'] >= sub['low_prev'])
variant("阳线+未创新低", sub['yang'] & (sub['low_today'] >= sub['low_prev']))
variant("回撤8~18经典二波", (sub['pullback'] >= 8) & (sub['pullback'] <= 18))
variant("经典+阳线+缩量", (sub['pullback'] >= 8) & (sub['pullback'] <= 18) & sub['yang'] & (sub['vol5'] < 1.0))
variant("深度回撤25~40(均值回归)", (sub['pullback'] > 25) & (sub['pullback'] <= 40))
variant("深度回撤+阳线", (sub['pullback'] > 25) & (sub['pullback'] <= 40) & sub['yang'])

print("\n════ 评分段胜率（基准子集） ════")
for lo, hi in [(40, 60), (60, 70), (70, 80), (80, 90), (90, 101)]:
    seg = sub[(sub['score'] >= lo) & (sub['score'] < hi)]
    if len(seg) == 0:
        continue
    print(f"  回调分[{lo},{hi}): n={len(seg):>6}  20日胜率={100 * (seg['f20'] > 0).mean():.1f}%  "
          f"20日均收益={seg['f20'].mean():+.2f}%  10日={seg['f10'].mean():+.2f}%  5日={seg['f5'].mean():+.2f}%")

# 000533 顺钠股份 7/31 检查
r533 = res[(res['code'] == '000533.SZ') & (res['date'] == '20260731')]
if len(r533):
    r = r533.iloc[0]
    print(f"\n000533 20260731: 回调分={r['score']:.1f} 主升{r['rally_gain']:.1f}% 回撤{r['pullback']:.1f}% "
          f"阳线={r['yang']} → 未来5日{r['f5']:+.1f}% 10日{r['f10']:+.1f}% 20日{r['f20']:+.1f}%")
