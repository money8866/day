# -*- coding: utf-8 -*-
"""
V4 半年回测 - 本地通达信日线（优化版，无API限频）
回测区间: 20241125 ~ 20260522 (约6个月)
优化: 批量预缓存Tushare数据，避免逐天API调用
"""
import sys; sys.stdout.reconfigure(encoding='utf-8')
import os, struct, pickle
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

BASE_DIR = r'C:\Users\kongx\mystock'
TDX_SH = r'C:\new_tdx\vipdoc\sh\lday'
TDX_SZ = r'C:\new_tdx\vipdoc\sz\lday'

def parse_day_file(path):
    if not os.path.exists(path): return None
    records = []
    with open(path, 'rb') as f:
        while True:
            data = f.read(32)
            if len(data) < 32: break
            date, o, h, l, c = struct.unpack('<IIIII', data[:20])
            amt, vol = struct.unpack('<dI', data[20:32])
            if date == 0: break
            date_str = f'{date//10000:04d}-{date%10000//100:02d}-{date%100:02d}'
            records.append({'trade_date': date_str, 'open': o/100.0, 'high': h/100.0, 'low': l/100.0, 'close': c/100.0, 'amount': amt, 'vol': vol})
    if not records: return None
    df = pd.DataFrame(records)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    return df.sort_values('trade_date').reset_index(drop=True)

def build_tdx_index():
    index = {}
    for tdx_dir in [TDX_SH, TDX_SZ]:
        if not os.path.exists(tdx_dir): continue
        for fname in os.listdir(tdx_dir):
            if not fname.endswith('.day'): continue
            code = fname.replace('.day', '')
            if code.startswith('sh'): ts = code[2:] + '.SH'
            elif code.startswith('sz'): ts = code[2:] + '.SZ'
            else: continue
            index[ts] = os.path.join(tdx_dir, fname)
    return index

def detect_entry_type(price, ma5, ma10, ma20, ma60, high_21):
    TOL = 0.03; FAR = 1.10
    if ma5*(1-TOL) <= price <= ma5*(1+TOL): return 'pullback_ma5', 10
    if ma10*(1-TOL) <= price <= ma10*(1+TOL): return 'pullback_ma10', 5
    if price > high_21 * 0.98:
        if price <= ma5 * 1.05: return 'breakout_near_ma', 0
        return 'breakout', -5
    if price > ma5 * FAR: return 'far_away', -10
    return 'unknown', 0

def ai_financial_score(fin, pe, sector_count=1):
    gm = fin.get('grossprofit_margin') or 0; nm = fin.get('netprofit_margin') or 0
    op_yoy = fin.get('op_yoy') or 0; debt = fin.get('debt_to_assets') or 0
    ocf = fin.get('ocf_to_or'); roe = fin.get('roe') or 0
    s = 0
    gm_s = 5 if gm > 50 else (4 if gm > 40 else (3 if gm > 30 else (2 if gm > 20 else 1)))
    nm_s = 3 if nm > 20 else (2 if nm > 15 else (1 if nm > 10 else 0))
    s += gm_s + nm_s
    if op_yoy > 100: g_s = 5
    elif op_yoy > 50: g_s = 6
    elif op_yoy > 30: g_s = 5
    elif op_yoy > 10: g_s = 4
    elif op_yoy > 0: g_s = 2
    elif op_yoy > -10: g_s = 1
    else: g_s = 0
    roe_s = 2 if roe > 10 else (1 if roe > 5 else 0)
    s += g_s + roe_s
    debt_s = 5 if debt < 20 else (4 if debt < 30 else (3 if debt < 40 else (2 if debt < 50 else (1 if debt < 60 else 0))))
    ocf_s = 3 if (ocf is not None and ocf > 0.15) else (2 if (ocf is not None and ocf > 0.05) else (1 if (ocf is not None and ocf > 0) else (-2 if (ocf is not None and ocf <= 0) else 0)))
    s += debt_s + ocf_s
    peg = pe / op_yoy if op_yoy > 0 else 999
    peg_s = 6 if peg < 0.5 else (5 if peg < 1 else (4 if peg < 1.5 else (3 if peg < 2 else (2 if peg < 3 else 1))))
    pe_s = -1 if pe > 80 else 0
    s += peg_s + pe_s
    return max(0, min(s, 30))

_fin_cache_file = os.path.join(BASE_DIR, 'fin_cache_v4.pkl')
def load_fin_cache(): return pickle.load(open(_fin_cache_file, 'rb')) if os.path.exists(_fin_cache_file) else {}
def save_fin_cache(cache): pickle.dump(cache, open(_fin_cache_file, 'wb'))

import tushare as ts

def get_fin(ts_code):
    cache = load_fin_cache()
    if ts_code in cache: return cache[ts_code]
    try:
        env = Path(os.path.join(BASE_DIR, '.env')).read_text()
        for line in env.splitlines():
            if line.startswith('TUSHARE_TOKEN='): ts.set_token(line.split('=', 1)[1].strip())
        pro = ts.pro_api()
        fi = pro.fina_indicator(ts_code=ts_code, period='20260331', fields='ts_code,roe,grossprofit_margin,netprofit_margin,debt_to_assets,op_yoy,ocf_to_or')
        d = fi.iloc[0].to_dict() if len(fi) > 0 else {}
        cache[ts_code] = d; save_fin_cache(cache)
        import time; time.sleep(0.05)
        return d
    except: return {}

def get_hot_sectors_fallback(trade_date, sw_df, top_n=8):
    try:
        env = Path(os.path.join(BASE_DIR, '.env')).read_text()
        for line in env.splitlines():
            if line.startswith('TUSHARE_TOKEN='): ts.set_token(line.split('=', 1)[1].strip())
        pro = ts.pro_api()
        df = pro.daily(trade_date=trade_date, fields='ts_code,close,pct_chg,amount')
        if df.empty: return []
    except: return []
    if sw_df.empty or 'l2_name' not in sw_df.columns: return []
    sw_merge = sw_df[['ts_code', 'l2_name']].dropna(subset=['l2_name'])
    df = df.merge(sw_merge, on='ts_code', how='left').dropna(subset=['l2_name'])
    if df.empty: return []
    ip = df.groupby('l2_name').agg(
        avg_pct=('pct_chg','mean'), total_amount=('amount','sum'),
        stock_count=('ts_code','count'), up_ratio=('pct_chg', lambda x:(x>0).mean()),
        limit_up=('pct_chg', lambda x:(x>=9.5).sum())).reset_index()
    ip = ip[ip['stock_count'] >= 5]
    ip['score'] = ip['avg_pct']*1.5 + ip['limit_up']*3 + ip['up_ratio']*8 + np.log1p(ip['total_amount']/1e8)*2
    top = ip.sort_values('score', ascending=False).head(top_n)
    hot = []
    for _, r in top.iterrows():
        ind = r['l2_name']
        stocks = set(sw_df[sw_df['l2_name'] == ind]['ts_code'].dropna().unique().tolist())
        hot.append({'name': ind, 'score': r['score'], 'stocks': stocks})
    return hot

PE_MAX = 100; MV_MIN, MV_MAX = 100, 500; MIN_TECH_SCORE = 60; FIN_SCORE_MIN = 20
MIN_REPEAT = 2; HOLD = 5; STOP = -0.05

def main():
    print('='*80)
    print('V4 half-year backtest - local TongDaXin (optimized)')
    print('='*80)

    tdx_index = build_tdx_index()
    print(f'TDX files: {len(tdx_index)}')

    end = '20260522'; start = '20241125'

    env = Path(os.path.join(BASE_DIR, '.env')).read_text()
    for line in env.splitlines():
        if line.startswith('TUSHARE_TOKEN='): ts.set_token(line.split('=', 1)[1].strip())
    pro = ts.pro_api()
    cal = pro.trade_cal(exchange='SSE', is_open=1, start_date=start, end_date=end)
    dates = sorted(cal['cal_date'].tolist())
    print(f'Period: {dates[0]}~{dates[-1]} ({len(dates)} trade days)')

    sw = os.path.join(BASE_DIR, 'cache_daily', 'sw_map.csv')
    sw_df = pd.read_csv(sw, dtype=str) if os.path.exists(sw) else pd.DataFrame()
    print(f'SW map: {len(sw_df)} rows')

    # --- 批量预缓存所有daily数据 ---
    print('Pre-caching daily_basic for all trading days...')
    daily_basic_cache = {}
    # Tushare限制单次查询日期范围，分批
    BATCH = 30
    for bi in range(0, len(dates), BATCH):
        batch = dates[bi:bi+BATCH]
        for td in batch:
            try:
                df = pro.daily_basic(trade_date=td, fields='ts_code,close,pe,total_mv')
                if not df.empty:
                    df['mv_yi'] = df['total_mv'] / 10000
                    daily_basic_cache[td] = df
            except:
                pass
        print(f'  {dates[bi]}~{dates[min(bi+BATCH-1, len(dates)-1)]}: cached {len(daily_basic_cache)}/{len(dates)} days')
    print(f'Daily basic cached: {len(daily_basic_cache)}/{len(dates)} days')

    fin_cache = load_fin_cache()
    print(f'Fin cache: {len(fin_cache)}')

    repeat_tracker = defaultdict(int)
    all_trades = []
    skipped_breakout = 0; skipped_no_file = 0; skipped_no_basic = 0

    for i in range(0, len(dates) - HOLD, 1):
        td = dates[i]
        td_dt = datetime.strptime(td, '%Y%m%d')
        td_str = td_dt.strftime('%Y-%m-%d')

        hot = get_hot_sectors_fallback(td, sw_df, top_n=8)
        if not hot: continue
        all_ss = set().union(*[h['stocks'] for h in hot])

        if td not in daily_basic_cache:
            skipped_no_basic += 1
            continue
        basic = daily_basic_cache[td]
        cands = basic[(basic['mv_yi'] >= MV_MIN) & (basic['mv_yi'] <= MV_MAX)]
        cands = cands[~cands['ts_code'].str.startswith(('8','4','9'))]
        cands = cands[(cands['pe'] > 0) & (cands['pe'] <= PE_MAX)]
        cands = cands[cands['ts_code'].isin(all_ss)]

        day_picks = []
        for _, row in cands.iterrows():
            tc = row['ts_code']; pe = row['pe']; mv = row['mv_yi']
            tdx_path = tdx_index.get(tc)
            if not tdx_path: skipped_no_file += 1; continue
            df = parse_day_file(tdx_path)
            if df is None or len(df) < 60: continue
            df = df.set_index('trade_date')
            avail = df.index[df.index < td_str]
            if len(avail) == 0: continue
            cutoff = avail[-1]
            df_s = df.loc[:cutoff]
            if len(df_s) < 60: continue
            c = df_s['close']; h = df_s['high']; l = df_s['low']; v = df_s['vol']
            ma5_v = c.rolling(5).mean().iloc[-1]; ma10_v = c.rolling(10).mean().iloc[-1]
            ma20_v = c.rolling(20).mean().iloc[-1]; ma60_v = c.rolling(60).mean().iloc[-1]
            price = c.iloc[-1]
            sc = 0
            if ma5_v > ma10_v > ma20_v: sc += 25
            if ma20_v > ma60_v: sc += 20
            vr = v / v.rolling(20).mean()
            if vr.iloc[-3:].mean() > 1.3: sc += 15
            high_21 = h.iloc[-21:-1].max()
            if price > high_21 * 0.98: sc += 15
            pos120 = (price - l.rolling(120).min().iloc[-1]) / (h.rolling(120).max().iloc[-1] - l.rolling(120).min().iloc[-1]) * 100
            if pos120 < 70: sc += 10
            pct5 = (price / c.iloc[-6] - 1) * 100
            if 3 < pct5 < 20: sc += 10
            rh = h.iloc[-45:-5].max(); rl = l.iloc[-45:-5].min()
            if rl > 0 and (rh - rl) / rl * 100 < 25: sc += 5
            if sc < MIN_TECH_SCORE: continue
            entry_type, entry_bonus = detect_entry_type(price, ma5_v, ma10_v, ma20_v, ma60_v, high_21)
            sc += entry_bonus
            sec_cnt = sum(1 for hs in hot if tc in hs['stocks'])
            if tc not in fin_cache: fin_cache[tc] = get_fin(tc)
            fin = fin_cache[tc]; fin_s = ai_financial_score(fin, pe, sec_cnt)
            repeat_tracker[tc] += 1
            day_picks.append({'ts_code': tc, 'close': price, 'pe': pe, 'mv_yi': mv, 'tech_score': sc, 'fin_score': fin_s, 'sector_count': sec_cnt, 'repeat': repeat_tracker[tc], 'pct_5d': round(pct5, 2), 'entry_type': entry_type})

        if not day_picks: continue
        try:
            sb = pro.stock_basic(fields='ts_code,name')
            nm = dict(zip(sb['ts_code'], sb['name']))
            for p in day_picks: p['name'] = nm.get(p['ts_code'], p['ts_code'])
        except: pass

        a_picks = [p for p in day_picks if p['repeat'] >= MIN_REPEAT and p['fin_score'] >= FIN_SCORE_MIN]
        a_picks.sort(key=lambda x: (x['fin_score'], x['repeat'], x['sector_count']), reverse=True)

        for pick in a_picks[:3]:
            tc = pick['ts_code']; orig_buy = pick['close']; entry_type = pick['entry_type']
            actual_buy_price = orig_buy; pullback_wait = 0; actual_buy_date = td_dt

            if entry_type in ('breakout', 'far_away', 'breakout_near_ma'):
                tdx_path = tdx_index.get(tc)
                if not tdx_path: continue
                df2 = parse_day_file(tdx_path)
                if df2 is None: continue
                df2 = df2.set_index('trade_date')
                found_pb = None
                for day_offset in range(1, 6):
                    check_date = (td_dt + timedelta(days=day_offset)).strftime('%Y-%m-%d')
                    avail_dates = df2.index[df2.index >= check_date]
                    if len(avail_dates) == 0: continue
                    cb = avail_dates[0]; cp = df2.loc[cb, 'close']
                    hist = df2.loc[:cb]
                    if len(hist) < 10: continue
                    ma5_c = hist['close'].rolling(5).mean().iloc[-1]; ma10_c = hist['close'].rolling(10).mean().iloc[-1]
                    tol = 0.03
                    if (ma5_c*(1-tol) <= cp <= ma5_c*(1+tol) or ma10_c*(1-tol) <= cp <= ma10_c*(1+tol)):
                        found_pb = (cb, cp); break
                if found_pb is None: skipped_breakout += 1; continue
                actual_buy_date, actual_buy_price = found_pb
                pullback_wait = (actual_buy_date - td_dt).days
                if isinstance(actual_buy_date, pd.Timestamp): actual_buy_date = actual_buy_date.to_pydatetime()

            tdx_path = tdx_index.get(tc)
            if not tdx_path: continue
            df2 = parse_day_file(tdx_path)
            if df2 is None: continue
            df2 = df2.set_index('trade_date')

            exit_df = df2.loc[actual_buy_date:] if not isinstance(actual_buy_date, datetime) else df2.loc[actual_buy_date:]
            stopped = False; sell = actual_buy_price; actual_hold = min(HOLD, len(exit_df) - 1)
            for j in range(1, len(exit_df)):
                if exit_df.iloc[j]['low'] / actual_buy_price - 1 <= STOP:
                    sell = actual_buy_price * (1 + STOP); actual_hold = j; stopped = True; break
            if not stopped: sell = exit_df.iloc[min(HOLD, len(exit_df) - 1)]['close']
            ret = (sell / actual_buy_price - 1) * 100
            max_gain = (exit_df['high'].max() / actual_buy_price - 1) * 100
            max_dd = (exit_df['low'].min() / actual_buy_price - 1) * 100
            exit_date = exit_df.index[actual_hold]
            if isinstance(exit_date, pd.Timestamp): exit_date = exit_date.strftime('%Y-%m-%d')
            buy_date_str = actual_buy_date.strftime('%Y-%m-%d') if isinstance(actual_buy_date, datetime) else str(actual_buy_date)
            all_trades.append({'date': td, 'actual_buy_date': buy_date_str, 'exit_date': exit_date, 'name': pick['name'], 'ts_code': tc, 'orig_buy': round(orig_buy, 2), 'actual_buy': round(actual_buy_price, 2), 'sell': round(sell, 2), 'pullback_wait': pullback_wait, 'return_pct': round(ret, 2), 'max_gain': round(max_gain, 2), 'max_dd': round(max_dd, 2), 'stopped': stopped, 'fin_score': pick['fin_score'], 'tech_score': pick['tech_score'], 'sector_count': pick['sector_count'], 'repeat': pick['repeat'], 'pe': pick['pe'], 'entry_type': entry_type})

        if (i + 1) % 20 == 0: print(f'  [{i+1}/{len(dates)}] {td} trades={len(all_trades)} skip_pb={skipped_breakout} no_basic={skipped_no_basic}')

    save_fin_cache(fin_cache)

    if not all_trades: print('No trades'); return

    rdf = pd.DataFrame(all_trades)
    total = len(rdf); wins = (rdf['return_pct'] > 0).sum(); wr = wins / total * 100
    avg_ret = rdf['return_pct'].mean()
    cum = (1 + rdf['return_pct'] / 100).prod() - 1
    avg_w = rdf[rdf['return_pct'] > 0]['return_pct'].mean() if wins else 0
    losses = total - wins; avg_l = abs(rdf[rdf['return_pct'] <= 0]['return_pct'].mean()) if losses else 1
    plr = avg_w / avg_l if avg_l > 0 else 999

    print('='*80)
    print('V4 backtest result (local TDX, half year)')
    print('='*80)
    print(f'Period: {dates[0]}~{dates[-1]} ({len(dates)} days)')
    print(f'Skip: breakout={skipped_breakout}, no_basic={skipped_no_basic}, no_file={skipped_no_file}')
    print(f'Trades: {total} | Win rate: {wr:.1f}%')
    print(f'Avg ret: {avg_ret:+.2f}% | Compound: {cum*100:+.2f}%')
    print(f'PLR: {plr:.2f} (avg_w={avg_w:.2f}%/avg_l={avg_l:.2f}%)')
    print(f'Stopped: {int(rdf["stopped"].sum())} ({rdf["stopped"].mean()*100:.0f}%)')
    print(f'Avg wait pullback: {rdf["pullback_wait"].mean():.1f} days')

    print('\nEntry type vs return:')
    for et in sorted(rdf['entry_type'].unique()):
        sub = rdf[rdf['entry_type'] == et]
        pb = f'wait{sub["pullback_wait"].mean():.0f}d' if sub['pullback_wait'].mean() > 0 else 'buy_now'
        print(f'  {et}: {len(sub)} trades, avg={sub["return_pct"].mean():+.2f}%, wr={(sub["return_pct"]>0).mean()*100:.0f}%, {pb}')

    print('\nFin score vs return:')
    for lo, hi, lb in [(24, 30, '>=24'), (20, 23, '20-23')]:
        sub = rdf[(rdf['fin_score'] >= lo) & (rdf['fin_score'] <= hi)]
        if len(sub) > 0: print(f'  {lb}: {len(sub)} trades, avg={sub["return_pct"].mean():+.2f}%, wr={(sub["return_pct"]>0).mean()*100:.0f}%')

    print('\nRepeat count vs return:')
    for rp in sorted(rdf['repeat'].unique()):
        sub = rdf[rdf['repeat'] == rp]
        print(f'  {rp}x: {len(sub)} trades, avg={sub["return_pct"].mean():+.2f}%, wr={(sub["return_pct"]>0).mean()*100:.0f}%')

    print('\nTop 5 winners:')
    for _, r in rdf.nlargest(5, 'return_pct').iterrows():
        pb = f'wait{r["pullback_wait"]}d' if r['pullback_wait'] > 0 else ''
        print(f'  {r["name"]} {r["date"]}->{r["exit_date"]} {r["return_pct"]:+.2f}% buy={r["actual_buy"]}(orig={r["orig_buy"]}) {pb} fin={r["fin_score"]}')

    print('\nTop 5 losers:')
    for _, r in rdf.nsmallest(5, 'return_pct').iterrows():
        st = '!' if r['stopped'] else ' '
        pb = f'wait{r["pullback_wait"]}d' if r['pullback_wait'] > 0 else ''
        print(f'  {r["name"]} {r["date"]}->{r["exit_date"]} {r["return_pct"]:+.2f}% buy={r["actual_buy"]} {pb} fin={r["fin_score"]} PE={r["pe"]:.0f}{st}')

    print(f'\nCompare: V3 wr=57.8% plr=1.66 cum=+133.7%')
    print(f'         V4 wr={wr:.0f}% plr={plr:.2f} cum={cum*100:+.1f}%')

    out = os.path.join(BASE_DIR, 'backtest_v4_local_halfyear.csv')
    rdf.to_csv(out, index=False, encoding='utf-8-sig')
    print(f'\nOutput: {out}')

if __name__ == '__main__':
    main()