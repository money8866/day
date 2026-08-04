# -*- coding: utf-8 -*-
"""最终尽职诊断：连板基因 + 市场环境 两个维度下的"主升后回调"是否成立
在 _backtest_pullback.py 基础上补充：
  1. 连板基因：主升浪期间(60日低点后~信号日)最大连续涨停数
  2. 市场环境：信号日上证指数收盘 vs MA20(强/弱市)
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

# ── 2. 上证指数环境 ──
sh = pd.read_csv(r"d:\mystock\cache_daily\000001_SH.csv")
sh = sh.sort_values('trade_date').reset_index(drop=True)
sh['ma20'] = sh['close'].rolling(20).mean()
sh_env = dict(zip(sh['trade_date'].astype(str), (sh['close'] > sh['ma20']).astype(int)))
print("上证指数环境日期数:", len(sh_env))


def limit_up_std(code: str) -> float:
    """涨停阈值：主板10% 双创20%"""
    sym = code.split('.')[0]
    if sym.startswith(('300', '301', '688')):
        return 19.5
    return 9.8


def max_consec_limitup(lim: np.ndarray, lo: int, hi: int) -> int:
    """[lo, hi] 区间内最大连续涨停数"""
    if hi <= lo:
        return 0
    seg = lim[lo:hi]
    if not seg.any():
        return 0
    # 分组累计
    s = pd.Series(seg.astype(int))
    grp = (s == 0).cumsum()
    streaks = s.groupby(grp).cumsum()
    return int(streaks.max())


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
    files = sorted(files, key=lambda f: f.rsplit('_', 2)[-1].split('.')[0], reverse=True)
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
    dates = df['trade_date'].astype(str).tolist()

    ma20_s = pd.Series(c).rolling(20).mean().values
    ma60_s = pd.Series(c).rolling(60).mean().values
    ma20_prev5 = pd.Series(c).rolling(20).mean().shift(5).values
    high120 = pd.Series(h).rolling(120, min_periods=1).max().values
    low60 = pd.Series(l).rolling(60, min_periods=60).min().values
    lim = pc >= limit_up_std(code)

    # 主升浪参考高 = 60日最低点之后最高价；同时记录 low_pos
    rally_high = np.full(n, np.nan)
    low_pos_a = np.full(n, -1, dtype=int)
    for i in range(59, n):
        w = l[i - 59:i + 1]
        li = i - 59 + int(np.argmin(w))
        low_pos_a[i] = li
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
        # 连板基因：主升浪期间(60日低点后~信号日前)最大连续涨停
        lb = max_consec_limitup(lim, int(low_pos_a[i]), i)
        env = sh_env.get(dates[i], 0)
        samples.append((code, dates[i], score, rally_gain, pullback, f5, f10, f20, pc[i],
                        pc[i - 1], l[i], l[i - 1], c[i] > o[i], vol5, lb, env))

res = pd.DataFrame(samples, columns=['code', 'date', 'score', 'rally_gain', 'pullback', 'f5', 'f10', 'f20', 'chg1',
                                     'chg_prev', 'low_today', 'low_prev', 'yang', 'vol5', 'maxlb', 'env'])
print("有效样本:", len(res), " 涉及股票:", n_stock)

sub = res[res['rally_gain'] >= 25].copy()
print("主升结构子集:", len(sub), " 含连板(>=3):", (sub['maxlb'] >= 3).sum(),
      " 含连板(>=4):", (sub['maxlb'] >= 4).sum(), " 强势日占比:%.1f%%" % (100 * sub['env'].mean()))


def v(name, m, df=sub):
    seg = df[m]
    if len(seg) < 100:
        print("[%s] n=%d 样本不足" % (name, len(seg)))
        return
    r20, _ = spearmanr(seg['score'], seg['f20'])
    r10, _ = spearmanr(seg['score'], seg['f10'])
    r5, _ = spearmanr(seg['score'], seg['f5'])
    print("[%s] n=%-6d 20日胜率=%.1f%%  5日=%+.2f%% 10日=%+.2f%% 20日=%+.2f%%  rho(5/10/20)=%+.3f/%+.3f/%+.3f"
          % (name, len(seg), 100 * (seg['f20'] > 0).mean(),
             seg['f5'].mean(), seg['f10'].mean(), seg['f20'].mean(), r5, r10, r20))


print("\n════ 连板基因维度（主升结构内） ════")
v("无连板(0~1板)", sub['maxlb'] <= 1)
v("连板=2", sub['maxlb'] == 2)
v("连板>=3", sub['maxlb'] >= 3)
v("连板>=4", sub['maxlb'] >= 4)
v("连板>=5", sub['maxlb'] >= 5)
v("连板>=3 + 经典回撤8~18", (sub['maxlb'] >= 3) & (sub['pullback'] >= 8) & (sub['pullback'] <= 18))
v("连板>=3 + 经典 + 阳线缩量", (sub['maxlb'] >= 3) & (sub['pullback'] >= 8) & (sub['pullback'] <= 18) & sub['yang'] & (sub['vol5'] < 1.0))
v("连板>=4 + 回调末端企稳", (sub['maxlb'] >= 4) & (sub['chg1'] + sub['chg_prev'] < 0) & sub['yang'])
v("连板>=4 + 回调8~18", (sub['maxlb'] >= 4) & (sub['pullback'] >= 8) & (sub['pullback'] <= 18))

print("\n════ 市场环境维度（主升结构内） ════")
v("强势日(指数>MA20)", sub['env'] == 1)
v("弱势日(指数<MA20)", sub['env'] == 0)
v("强势日+连板>=3", (sub['env'] == 1) & (sub['maxlb'] >= 3))
v("强势日+连板>=3+经典回撤", (sub['env'] == 1) & (sub['maxlb'] >= 3) & (sub['pullback'] >= 8) & (sub['pullback'] <= 18))
v("弱势日+连板>=3", (sub['env'] == 0) & (sub['maxlb'] >= 3))

print("\n════ 连板股评分段胜率 ════")
lb3 = sub[sub['maxlb'] >= 3]
for lo, hi in [(50, 60), (60, 70), (70, 80), (80, 90), (90, 101)]:
    seg = lb3[(lb3['score'] >= lo) & (lb3['score'] < hi)]
    if len(seg) == 0:
        continue
    print("  连板>=3, 回调分[%d,%d): n=%-6d 20日胜率=%.1f%% 20日=%+.2f%% 5日=%+.2f%%" %
          (lo, hi, len(seg), 100 * (seg['f20'] > 0).mean(), seg['f20'].mean(), seg['f5'].mean()))

# 000533 7/31 检查
r533 = res[(res['code'] == '000533.SZ') & (res['date'] == '20260731')]
if len(r533):
    r = r533.iloc[0]
    print("\n000533 20260731: 回调分=%.1f 主升%.1f%% 回撤%.1f%% 连板=%d 环境=%s → 5日%+.1f%% 10日%+.1f%% 20日%+.1f%%"
          % (r['score'], r['rally_gain'], r['pullback'], r['maxlb'], '强' if r['env'] else '弱',
             r['f5'], r['f10'], r['f20']))
