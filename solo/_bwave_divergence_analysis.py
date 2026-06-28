"""
底背离信号历史回测分析
统计底背离信号的胜率、盈亏比、平均收益
"""
import os, sys, sqlite3
from datetime import datetime
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bwave_strategy import get_data

DB = r'D:\mystock\cache_daily\stock_data.db'


def load_all_stocks() -> list:
    candidates = [
        r"D:\mystock\solo\multi_factor_picker\output",
        r"D:\mystock\report_daily",
    ]
    csv_path = None
    for base_dir in candidates:
        if not os.path.isdir(base_dir):
            continue
        files = sorted([f for f in os.listdir(base_dir) if f.startswith('bull_stocks_') and f.endswith('.csv')], reverse=True)
        if files:
            csv_path = os.path.join(base_dir, files[0])
            break
        fixed = os.path.join(base_dir, "bull_stocks_qualified.csv")
        if os.path.exists(fixed):
            csv_path = fixed
            break
    if csv_path is None or not os.path.exists(csv_path):
        conn = sqlite3.connect(DB)
        sql = "SELECT DISTINCT ts_code FROM stk_factor_pro ORDER BY ts_code"
        df = pd.read_sql(sql, conn)
        conn.close()
        return df['ts_code'].tolist()
    df = pd.read_csv(csv_path, encoding='utf-8-sig')
    def normalize(c):
        c = str(c).strip().upper()
        if '.' in c:
            return c
        if c.startswith(('6', '9')):
            return c + '.SH'
        return c + '.SZ'
    return [normalize(c) for c in df['code'].tolist()]


def detect_all_awaves(df: pd.DataFrame) -> list:
    """找最近250天内所有A浪候选（去重后）"""
    if len(df) < 130:
        return []
    start_idx = max(0, len(df) - 250)
    lows = []
    highs = []
    for i in range(start_idx, len(df) - 1):
        if df.iloc[i]['close'] <= df.iloc[i - 1]['close'] and df.iloc[i]['close'] <= df.iloc[i + 1]['close']:
            lows.append(i)
        if df.iloc[i]['close'] >= df.iloc[i - 1]['close'] and df.iloc[i]['close'] >= df.iloc[i + 1]['close']:
            highs.append(i)

    best_by_start = {}
    for a_start in lows:
        if a_start < start_idx:
            continue
        best_gain = 0
        best_awave = None
        for a_end in highs:
            if a_end <= a_start + 20 or a_end > a_start + 60:
                continue
            if a_end >= len(df) - 5:
                continue
            sp = df.iloc[a_start]['close']
            ep = df.iloc[a_end]['close']
            if sp <= 0:
                continue
            gain = (ep / sp - 1) * 100
            if gain < 60:
                continue
            ma20_s = df.iloc[a_start:a_end + 1]['ma_bfq_20'].values
            ma20_up = sum(1 for i in range(1, len(ma20_s)) if ma20_s[i] > ma20_s[i - 1] and ma20_s[i] > 0)
            if ma20_up / max(len(ma20_s) - 1, 1) < 0.6:
                continue
            above = sum(1 for i in range(a_start, a_end + 1) if df.iloc[i]['close'] > df.iloc[i]['ma_bfq_20'] > 0)
            a_vol = df.iloc[a_start:a_end + 1]['vol'].mean()
            v40 = df.iloc[max(0, a_start - 40):a_start]['vol'].mean()
            if above / max(a_end - a_start, 1) < 0.6 or a_vol / v40 < 1.3:
                continue
            if gain > best_gain:
                best_gain = gain
                best_awave = {
                    'start_idx': a_start, 'end_idx': a_end,
                    'start_price': round(sp, 2), 'end_price': round(ep, 2),
                    'gain': round(gain, 1), 'duration': a_end - a_start,
                    'avg_vol': a_vol,
                }
        if best_awave:
            best_by_start[a_start] = best_awave

    return list(best_by_start.values())


def detect_all_bwaves(df: pd.DataFrame, awave: dict) -> list:
    a_end = awave['end_idx']
    a_high = awave['end_price']
    a_duration = awave['duration']
    a_avg_vol = awave['avg_vol']
    search_end = min(a_end + a_duration * 2 + 10, len(df) - 5)
    results = []
    for b_low in range(a_end + int(a_duration * 0.6), search_end + 1):
        if b_low >= len(df):
            break
        seg = df.iloc[a_end:b_low + 1]
        real_low_idx = seg['close'].idxmin()
        low_price = df.loc[real_low_idx, 'close']
        drop = (a_high - low_price) / a_high * 100
        if drop < 15 or drop > 45:
            continue
        b_duration = real_low_idx - a_end
        if b_duration < a_duration * 0.6:
            continue
        r10v = df.iloc[max(real_low_idx - 9, a_end):real_low_idx + 1]['vol'].mean()
        vs = r10v / a_avg_vol if a_avg_vol > 0 else 0
        if vs > 0.8:
            continue
        ma120 = df.iloc[real_low_idx]['ma_120']
        if ma120 > 0 and low_price < ma120 * 0.95:
            continue
        results.append({
            'start_idx': a_end, 'low_idx': real_low_idx,
            'low_price': round(low_price, 2), 'drop': round(drop, 1),
            'duration': b_duration, 'vol_shrink_ratio': round(vs, 2),
        })
    return results


def find_divergence_at_bwave(df: pd.DataFrame, awave: dict, bwave: dict) -> list:
    a_high = awave['end_price']
    seg = df.iloc[bwave['start_idx']:]
    if len(seg) < 15:
        return []
    ts_code = df.iloc[0].get('_code', '') if '_code' in df.columns else ''

    low_indices = []
    for i in range(1, len(seg) - 1):
        if (seg.iloc[i]['close'] <= seg.iloc[i - 1]['close'] and
                seg.iloc[i]['close'] <= seg.iloc[i + 1]['close']):
            low_indices.append(bwave['start_idx'] + i)
    if len(low_indices) < 2:
        return []

    results = []
    for j in range(1, len(low_indices)):
        p1, p2 = low_indices[j - 1], low_indices[j]
        p1c, p2c = df.iloc[p1]['close'], df.iloc[p2]['close']
        p1d, p2d = df.iloc[p1]['macd_dif_bfq'], df.iloc[p2]['macd_dif_bfq']

        if not (p2c <= p1c * 1.005 and p2d > p1d * 1.01):
            continue
        if p2c < bwave['low_price'] * 0.95:
            continue

        p1r, p2r = df.iloc[p1]['rsi_bfq_6'], df.iloc[p2]['rsi_bfq_6']
        dr = (p2d / p1d - 1) * 100 if p1d > 0 else 0
        d2h = (a_high / p2c - 1) * 100 if p2c > 0 else 0

        d_score = int(30 + min(20, int(dr)) + (10 if p2r > p1r else 0) + (5 if d2h < 10 else 0))
        if d_score < 40:
            continue

        ep = float(df.iloc[p2]['close'])
        if ep <= 0:
            continue
        rets = {}
        for w in [1, 2, 3, 5, 10, 15, 20]:
            fi = min(p2 + w, len(df) - 1)
            rets[f'ret_{w}d'] = round((float(df.iloc[fi]['close']) / ep - 1) * 100, 2)

        results.append({
            'ts_code': ts_code, 'signal_date': str(df.iloc[p2]['trade_date']),
            'p1_date': str(df.iloc[p1]['trade_date']),
            'p1_close': round(p1c, 2), 'p2_close': round(p2c, 2),
            'p1_dif': round(p1d, 2), 'p2_dif': round(p2d, 2),
            'dist_to_a_high': round(d2h, 1), 'rsi6': round(p2r, 1),
            'dif_gain': d_score, **rets,
        })
    return results


def scan_divergence_all_days(ts_code: str) -> list:
    df = get_data(ts_code)
    if df is None or len(df) < 250:
        return []
    df['_code'] = ts_code
    results = []
    for aw in detect_all_awaves(df):
        for bw in detect_all_bwaves(df, aw):
            results.extend(find_divergence_at_bwave(df, aw, bw))
    return results


def main():
    stocks = load_all_stocks()
    print(f"\n  底背离信号历史回测分析 (共{len(stocks)}只股票)", flush=True)
    print(f"  {'='*50}", flush=True)
    print(f"  扫描所有交易日，寻找底背离信号...", flush=True)

    all_signals = []
    total = len(stocks)
    for i, ts_code in enumerate(stocks):
        if (i + 1) % 30 == 0:
            print(f"  [{i+1}/{total}] 已发现{len(all_signals)}个信号 ({datetime.now().strftime('%H:%M:%S')})", flush=True)
        try:
            sigs = scan_divergence_all_days(ts_code)
            all_signals.extend(sigs)
        except Exception:
            continue

    print(f"\n  {'='*60}", flush=True)
    print(f"  共发现 {len(all_signals)} 个底背离信号", flush=True)
    print(f"  {'='*60}", flush=True)

    if not all_signals:
        print("  无信号可分析", flush=True)
        return

    df = pd.DataFrame(all_signals)

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trend_feature_output', f'divergence_analysis_{ts}.csv')
    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f"  全量数据: {csv_path}", flush=True)

    periods = [('ret_1d','1日'),('ret_3d','3日'),('ret_5d','5日'),('ret_10d','10日'),('ret_15d','15日'),('ret_20d','20日')]

    print(f"\n  {'='*60}", flush=True)
    print(f"  底背离信号胜率分析", flush=True)
    print(f"  {'='*60}", flush=True)
    print(f"  {'周期':>6}  {'信号数':>8}  {'均值%':>8}  {'中位%':>8}  {'胜率':>8}  {'盈亏比':>8}  {'max%':>8}  {'min%':>8}")
    print(f"  {'-'*72}", flush=True)

    for col, lbl in periods:
        r = df[col].dropna()
        if len(r) == 0:
            continue
        w = r[r > 0]
        l = r[r <= 0]
        wr = len(w) / len(r) * 100
        aw = w.mean() if len(w) > 0 else 0
        al = abs(l.mean()) if len(l) > 0 else 1
        pr = aw / al if al > 0 else 0
        print(f"  {lbl:>6}  {len(r):>8}  {r.mean():>7.2f}%  {r.median():>7.2f}%  {wr:>6.1f}%  {pr:>7.2f}  {r.max():>7.2f}%  {r.min():>7.2f}%", flush=True)

    print(f"\n  按底背离强度(dif_gain)分组 (ret_5d):", flush=True)
    for lo, hi in [(40,50),(50,60),(60,70),(70,200)]:
        sub = df[(df['dif_gain'] >= lo) & (df['dif_gain'] < hi)]
        r = sub['ret_5d'].dropna()
        if len(r) < 3:
            continue
        w = r[r > 0]
        l = r[r <= 0]
        print(f"    [{lo}-{hi}分]  {len(r)}例  均{r.mean():>6.2f}%  胜率{len(w)/len(r)*100:>5.1f}%  盈亏比{w.mean()/abs(l.mean()):.2f}" if len(l) > 0 else f"    [{lo}-{hi}分]  {len(r)}例  均{r.mean():>6.2f}%  胜率{len(w)/len(r)*100:>5.1f}%", flush=True)

    print(f"\n  距A高%对胜率影响 (ret_5d):", flush=True)
    for lo, hi in [(0,5),(5,10),(10,20),(20,50),(50,200)]:
        sub = df[(df['dist_to_a_high'] >= lo) & (df['dist_to_a_high'] < hi)]
        r = sub['ret_5d'].dropna()
        if len(r) < 3:
            continue
        w = r[r > 0]
        print(f"    距A高[{lo:>3}-{hi:>3}%]  {len(r):>4}例  均{r.mean():>6.2f}%  胜率{len(w)/len(r)*100:>5.1f}%", flush=True)

    sd = df.sort_values('ret_10d')
    print(f"\n  最佳5例:", flush=True)
    for _, r in sd.tail(5).iterrows():
        print(f"    {r['ts_code']:>10} {r['signal_date']} +5d={r['ret_5d']:>6.2f}% +10d={r['ret_10d']:>6.2f}% +20d={r['ret_20d']:>6.2f}% d={r['dif_gain']}", flush=True)
    print(f"  最差5例:", flush=True)
    for _, r in sd.head(5).iterrows():
        print(f"    {r['ts_code']:>10} {r['signal_date']} +5d={r['ret_5d']:>6.2f}% +10d={r['ret_10d']:>6.2f}% +20d={r['ret_20d']:>6.2f}% d={r['dif_gain']}", flush=True)


if __name__ == '__main__':
    main()
