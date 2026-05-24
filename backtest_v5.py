# -*- coding: utf-8 -*-
"""V5 极速回测 - 完全向量化版"""
import os, struct, pickle, sys, time, json
import numpy as np
import pandas as pd
from datetime import datetime
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
print('='*60, flush=True)
print('V5 策略 - RSI-2 + MA10均线支撑 (向量化极速版)', flush=True)
print('='*60, flush=True)

BASE_DIR = r'C:\Users\kongx\mystock'
TDX_SH = r'C:\new_tdx\vipdoc\sh\lday'
TDX_SZ = r'C:\new_tdx\vipdoc\sz\lday'
START, END = '20241125', '20260522'
HOLD = 4; STOP = -0.05
MV_MIN, MV_MAX, PE_MAX = 10, 5000, 80
MIN_FIN_SCORE = 5

t0 = time.time()

def parse_day(path):
    if not os.path.exists(path): return None
    recs = []
    with open(path, 'rb') as f:
        while True:
            d = f.read(32)
            if len(d) < 32: break
            date, o, h, l, c = struct.unpack('<IIIII', d[:20])
            amt, vol = struct.unpack('<dI', d[20:32])
            if date == 0: break
            recs.append((date, c/100.0, h/100.0, l/100.0, vol))
    if not recs: return None
    return recs

def ma(arr, n):
    r = np.full(len(arr), np.nan)
    for i in range(n-1, len(arr)):
        r[i] = np.mean(arr[i-n+1:i+1])
    return r

def rsi2_calc(close, low):
    """标准RSI-2计算：基于2日上涨/下跌的移动平均"""
    n = len(close)
    r = np.full(n, np.nan)
    if n < 2:
        return r
    # 计算每日涨跌
    deltas = np.diff(close)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    # 2周期简单移动平均
    for i in range(1, n-1):
        avg_gain = (gains[i] + gains[i-1]) / 2 if i > 0 else gains[i]
        avg_loss = (losses[i] + losses[i-1]) / 2 if i > 0 else losses[i]
        if avg_loss == 0:
            r[i+1] = 100.0
        elif avg_gain == 0:
            r[i+1] = 0.0
        else:
            rs = avg_gain / avg_loss
            r[i+1] = 100 - 100 / (1 + rs)
    return r

def fin_score(fin):
    if fin is None: return 0
    s = 0
    roe = fin.get('roe') or 0
    gm = fin.get('grossprofit_margin') or 0
    nm = fin.get('netprofit_margin') or 0
    debt = fin.get('debt_to_assets') or 0
    if roe >= 10: s += 15
    elif roe >= 5: s += 8
    elif roe > 0: s += 3
    if gm >= 30: s += 10
    elif gm >= 20: s += 5
    if nm >= 10: s += 8
    elif nm >= 5: s += 4
    if debt <= 40: s += 7
    elif debt <= 60: s += 3
    return s

# TDX 索引
tdx_idx = {}
for d in [TDX_SH, TDX_SZ]:
    if not os.path.exists(d): continue
    for f in os.listdir(d):
        if not f.endswith('.day'): continue
        code = f.replace('.day', '')
        ts = (code[2:]+'.SH') if code.startswith('sh') else (code[2:]+'.SZ')
        tdx_idx[ts] = os.path.join(d, f)
print(f'TDX文件: {len(tdx_idx)}', flush=True)

# Tushare
import tushare as ts
env = Path(os.path.join(BASE_DIR, '.env')).read_text()
for line in env.splitlines():
    if line.startswith('TUSHARE_TOKEN='): ts.set_token(line.split('=',1)[1].strip())
pro = ts.pro_api()

cal = pro.trade_cal(exchange='SSE', is_open=1, start_date=START, end_date=END)
dates = sorted(cal['cal_date'].tolist())
print(f'交易日: {len(dates)}', flush=True)

# daily_basic 候选
print('加载候选池...', flush=True)
db_cache = {}
for i in range(0, len(dates), 30):
    batch = dates[i:i+30]
    for td in batch:
        try:
            df = pro.daily_basic(trade_date=td, fields='ts_code,close,pe,total_mv')
            if not df.empty:
                df = df.copy(); df['mv_yi'] = df['total_mv'] / 10000
                db_cache[td] = df
        except: pass
    print(f'  {batch[0]}~{batch[-1]}: {len(db_cache)}/{len(dates)}', flush=True)

# stock_basic 名称
try:
    sb = pro.stock_basic(fields='ts_code,name')
    nm_map = dict(zip(sb['ts_code'], sb['name']))
except: nm_map = {}
print(f'名称: {len(nm_map)}', flush=True)

fin_cache = pickle.load(open(os.path.join(BASE_DIR, 'fin_cache_v4.pkl'), 'rb'))
print(f'财务缓存: {len(fin_cache)}', flush=True)

# 预建每日候选 ts_code 集合（set 查 O(1)）
daily_cand = {}
daily_pe = {}
for td, df in db_cache.items():
    rows = df[(df['mv_yi'] >= MV_MIN) & (df['mv_yi'] <= MV_MAX) &
              (~df['ts_code'].str.startswith(('8','4','9'))) &
              (df['pe'] > 0) & (df['pe'] <= PE_MAX)]
    daily_cand[td] = set(rows['ts_code'].tolist())
    for _, r in rows.iterrows():
        daily_pe[(td, r['ts_code'])] = r['pe']
all_tcs = set()
for s in daily_cand.values(): all_tcs.update(s)
print(f'候选股票: {len(all_tcs)}', flush=True)

# ===== 一次性预读所有股票 =====
print('预读股票数据...', flush=True)
all_data = {}
skipped = 0
cand_list = sorted(all_tcs)
N = len(cand_list)

for ki, tc in enumerate(cand_list):
    if (ki+1) % 500 == 0: print(f'  {ki+1}/{N}', flush=True)
    path = tdx_idx.get(tc)
    if not path:
        skipped += 1; continue
    recs = parse_day(path)
    if not recs or len(recs) < 120:
        skipped += 1; continue
    n = len(recs)
    dates_arr = np.array([r[0] for r in recs], dtype=np.int32)
    close_arr = np.array([r[1] for r in recs], dtype=np.float64)
    high_arr  = np.array([r[2] for r in recs], dtype=np.float64)
    low_arr   = np.array([r[3] for r in recs], dtype=np.float64)
    vol_arr   = np.array([r[4] for r in recs], dtype=np.float64)
    ma10 = ma(close_arr, 10); ma20 = ma(close_arr, 20); ma60 = ma(close_arr, 60)
    vma10 = ma(vol_arr, 10)
    rsi = rsi2_calc(close_arr, low_arr)
    lo120 = np.full(n, np.nan); hi120 = np.full(n, np.nan)
    for j in range(119, n):
        lo120[j] = np.min(low_arr[max(0,j-119):j+1])
        hi120[j] = np.max(high_arr[max(0,j-119):j+1])
    all_data[tc] = {
        'dates': dates_arr, 'c': close_arr, 'h': high_arr, 'l': low_arr,
        'v': vol_arr, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
        'vma10': vma10, 'rsi': rsi, 'lo120': lo120, 'hi120': hi120, 'n': n
    }

print(f'预读完成: {len(all_data)} 只 (skipped={skipped}) 耗时={time.time()-t0:.0f}s', flush=True)

# ===== 主回测 =====
print('回测中...', flush=True)
t1 = time.time()
all_trades = []
last_stop = defaultdict(lambda: 0)

# 预转换日期字符串为 int（YYYYMMDD）
def dt2int(y, m, d): return y*10000 + m*100 + d
def ts2int(ts): return int(ts)

# 日期列表转 int
dates_int = []
for d in dates:
    y = int(d[:4]); m = int(d[4:6]); dd = int(d[6:8])
    dates_int.append(dt2int(y, m, dd))

for di, td_int in enumerate(dates_int):
    if (di+1) % 60 == 0:
        elapsed = time.time() - t1
        print(f'  {di+1}/{len(dates)} trades={len(all_trades)} elapsed={elapsed:.0f}s', flush=True)
    td_str = dates[di]
    if td_str not in daily_cand: continue
    cand_set = daily_cand[td_str]

    for tc in cand_set:
        if tc not in all_data: continue
        d = all_data[tc]
        dates_arr = d['dates']; n = d['n']
        if n < 120: continue

        # 找最近 < td_int 的索引
        avail = np.searchsorted(dates_arr, td_int, side='left') - 1
        if avail < 60: continue

        price = d['c'][avail]
        m10 = d['ma10'][avail]; m20 = d['ma20'][avail]; m60 = d['ma60'][avail]
        if any(np.isnan(x) or x <= 0 for x in [price, m10, m20, m60]): continue

        # V5 条件
        # if not (m10 > m20 > m60): continue
        r = d['rsi'][avail]
        pullback_pct = abs(price - m10) / m10
        # debug: 看RSI分布
        if np.random.random() < 0.001:
            print(f'debug {tc} price={price:.2f} m10={m10:.2f} m20={m20:.2f} rsi2={r:.1f} pb={pullback_pct*100:.1f}%', flush=True)
        if r > 30: continue  # RSI-2 < 30 = 回档信号
        if pullback_pct > 0.08: continue  # 回撤到MA10±8%
        fs = fin_score(fin_cache.get(tc))
        # if fs < MIN_FIN_SCORE: continue  # debug: 跳过财务检查
        last_d = last_stop[tc]
        if td_int - last_d < 8: continue
        vr = d['v'][avail] / d['vma10'][avail] if (d['vma10'][avail] > 0 and not np.isnan(d['vma10'][avail])) else 1.0
        if vr < 0.7: continue

        # 找买点
        buy_price = price
        buy_idx_list = np.searchsorted(dates_arr, td_int, side='left')
        if buy_idx_list >= n: continue

        stop_hit = False; actual_hold = HOLD
        for j in range(1, min(HOLD+1, n - buy_idx_list)):
            if d['l'][buy_idx_list + j] / buy_price - 1 <= STOP:
                actual_hold = j; stop_hit = True
                sell_price = buy_price * (1 + STOP)
                last_stop[tc] = td_int
                break
        if not stop_hit:
            sell_price = d['c'][min(buy_idx_list + HOLD, n-1)]
        ret = (sell_price / buy_price - 1) * 100
        seg_h = d['h'][buy_idx_list:buy_idx_list+actual_hold+1]
        seg_l = d['l'][buy_idx_list:buy_idx_list+actual_hold+1]
        max_gain = (np.max(seg_h) / buy_price - 1) * 100
        max_dd = (np.min(seg_l) / buy_price - 1) * 100
        exit_idx = min(buy_idx_list + actual_hold, n-1)
        buy_str = f'{td_int//10000}-{td_int%10000//100:02d}-{td_int%100:02d}'
        exit_int = dates_arr[exit_idx]
        exit_str = f'{exit_int//10000}-{exit_int%10000//100:02d}-{exit_int%100:02d}'
        pe_val = daily_pe.get((td_str, tc), 0)

        all_trades.append({
            'date': td_str, 'buy_date': buy_str, 'exit_date': exit_str,
            'name': nm_map.get(tc, tc), 'ts_code': tc,
            'buy_price': round(buy_price, 2), 'sell_price': round(sell_price, 2),
            'return_pct': round(ret, 2),
            'max_gain': round(max_gain, 2), 'max_dd': round(max_dd, 2),
            'stopped': stop_hit,
            'fin_score': fs, 'rsi2': round(r, 1),
            'pullback_pct': round(pullback_pct*100, 1),
            'vol_ratio': round(vr, 2),
            'pe': round(pe_val, 1),
        })

# ===== 结果 =====
t_total = time.time() - t0
if not all_trades:
    print('无交易！')
    sys.exit()

rdf = pd.DataFrame(all_trades)
total = len(rdf); wins = (rdf['return_pct'] > 0).sum()
wr = wins / total * 100
avg_ret = rdf['return_pct'].mean()
cum = (1 + rdf['return_pct']/100).prod() - 1
avg_w = rdf[rdf['return_pct'] > 0]['return_pct'].mean()
losses = total - wins
avg_l = abs(rdf[rdf['return_pct'] <= 0]['return_pct'].mean()) if losses else 1
plr = avg_w / avg_l if avg_l > 0 else 999

print()
print('='*60)
print('V5 回测结果 - RSI-2 + MA10均线支撑')
print('='*60)
print(f'回测区间: {START}~{END}  ({len(dates)} 交易日)')
print(f'总耗时: {t_total:.0f}s')
print(f'交易次数: {total} | 胜率: {wr:.1f}%')
print(f'平均收益: {avg_ret:+.2f}% | 累计: {cum*100:+.2f}%')
print(f'PLR: {plr:.2f}  (盈利={avg_w:.2f}% / 亏损={avg_l:.2f}%)')
print(f'止损率: {rdf["stopped"].mean()*100:.0f}%')
print(f'最大单笔: {rdf["return_pct"].max():+.2f}% | 最小: {rdf["return_pct"].min():+.2f}%')
rsimean = rdf['rsi2'].mean(); pbmean = rdf['pullback_pct'].mean()
print('RSI-2均值: %.1f | 回撤均值: %.1f%%' % (rsimean, pbmean))
out = os.path.join(BASE_DIR, 'backtest_v5.csv')
rdf.to_csv(out, index=False, encoding='utf-8-sig')
print(f'已保存: {out}')
