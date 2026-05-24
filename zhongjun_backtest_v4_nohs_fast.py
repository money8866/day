# -*- coding: utf-8 -*-
"""V4 半年回测 - 本地通达信日线（循环反转版，高速，无热点板块过滤）"""
import os, struct, pickle
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

BASE_DIR = r'C:\Users\kongx\mystock'
TDX_SH   = r'C:\new_tdx\vipdoc\sh\lday'
TDX_SZ   = r'C:\new_tdx\vipdoc\sz\lday'

# =========== 通达信解析 ===========
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
            records.append({'trade_date': date_str, 'close': c/100.0, 'high': h/100.0, 'low': l/100.0, 'vol': vol})
    if not records: return None
    df = pd.DataFrame(records)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    return df.sort_values('trade_date').reset_index(drop=True)

def build_tdx_index():
    idx = {}
    for d in [TDX_SH, TDX_SZ]:
        if not os.path.exists(d): continue
        for f in os.listdir(d):
            if not f.endswith('.day'): continue
            code = f.replace('.day', '')
            ts = (code[2:] + '.SH') if code.startswith('sh') else (code[2:] + '.SZ')
            idx[ts] = os.path.join(d, f)
    return idx

def detect_entry_type(price, ma5, ma10, ma20, ma60, high_21):
    TOL, FAR = 0.03, 1.10
    if ma5*(1-TOL) <= price <= ma5*(1+TOL): return 'pullback_ma5', 10
    if ma10*(1-TOL) <= price <= ma10*(1+TOL): return 'pullback_ma10', 5
    if price > high_21 * 0.98:
        if price <= ma5*1.05: return 'breakout_near_ma', 0
        return 'breakout', -5
    if price > ma5*FAR: return 'far_away', -10
    return 'unknown', 0

def ai_financial_score(fin, pe, sec_cnt):
    if fin is None: return 0
    gm = fin.get('grossprofit_margin', 0) or 0
    nm = fin.get('netprofit_margin', 0) or 0
    op = fin.get('op_yoy', 0) or 0
    debt = fin.get('debt_to_assets', 0) or 0
    roe = fin.get('roe', 0) or 0
    s = sec_cnt * 3
    if gm >= 40: s += 8
    elif gm >= 30: s += 4
    if nm >= 15: s += 8
    elif nm >= 8: s += 4
    if op >= 30: s += 8
    elif op >= 10: s += 4
    elif op >= 0: s += 2
    if debt <= 30: s += 5
    elif debt <= 50: s += 2
    if roe >= 15: s += 6
    elif roe >= 8: s += 3
    return s

def load_fin_cache():
    p = os.path.join(BASE_DIR, 'fin_cache_v4.pkl')
    return pickle.load(open(p, 'rb')) if os.path.exists(p) else {}

def get_fin(tc):
    import tushare as ts
    env = Path(os.path.join(BASE_DIR, '.env')).read_text()
    for line in env.splitlines():
        if line.startswith('TUSHARE_TOKEN='): ts.set_token(line.split('=', 1)[1].strip())
    pro = ts.pro_api()
    try:
        df = pro.fina_indicator(ts_code=tc, period='20260331',
            fields='ts_code,roe,grossprofit_margin,netprofit_margin,debt_to_assets,op_yoy,ocf_to_or')
        if not df.empty: return df.iloc[0].to_dict()
    except: pass
    return None

# =========== 常量 ===========
START = '20241125'; END = '20260522'
HOLD = 5; STOP = -0.05
MIN_TECH_SCORE = 60
FIN_SCORE_MIN = 20
MV_MIN = 10; MV_MAX = 5000
PE_MAX = 80
MIN_REPEAT = 2
STOP_COOLDOWN = 10   # days after stop-loss before can re-enter

# =========== 主程序 ===========
def main():
    import tushare as ts
    env = Path(os.path.join(BASE_DIR, '.env')).read_text()
    for line in env.splitlines():
        if line.startswith('TUSHARE_TOKEN='): ts.set_token(line.split('=', 1)[1].strip())
    pro = ts.pro_api()

    print('='*60)
    print('V4 half-year backtest - reversed loop (fast, no hot sector)')
    print(f'Period: {START}~{END}')
    print('='*60, flush=True)

    # 1. TDX 索引
    tdx_index = build_tdx_index()
    print(f'TDX files: {len(tdx_index)}', flush=True)

    # 2. 交易日历
    cal = pro.trade_cal(exchange='SSE', is_open=1, start_date=START, end_date=END)
    dates = sorted(cal['cal_date'].tolist())
    date_set = set(dates)
    print(f'Trade days: {len(dates)}', flush=True)

    # 3. 预缓存 daily_basic
    print('Caching daily_basic...', flush=True)
    db_cache = {}
    for i in range(0, len(dates), 30):
        batch = dates[i:i+30]
        for td in batch:
            try:
                df = pro.daily_basic(trade_date=td, fields='ts_code,close,pe,total_mv')
                if not df.empty:
                    df = df.copy()
                    df['mv_yi'] = df['total_mv'] / 10000
                    db_cache[td] = df
            except: pass
        print(f'  {batch[0]}~{batch[-1]}: {len(db_cache)}/{len(dates)}', flush=True)
    print(f'Daily basic cached: {len(db_cache)}/{len(dates)} days', flush=True)

    # 4. 预缓存 stock_basic
    try:
        sb = pro.stock_basic(fields='ts_code,name')
        nm_map = dict(zip(sb['ts_code'], sb['name']))
    except:
        nm_map = {}
    print(f'Stock names: {len(nm_map)}', flush=True)

    # 5. 财务缓存
    fin_cache = load_fin_cache()
    print(f'Fin cache: {len(fin_cache)}', flush=True)

    # 6. 构建每日候选（pe/mv 也存好）
    print('Building daily candidate index...', flush=True)
    daily_cands = {}    # td -> set of ts_code
    daily_pe    = {}    # (td, ts_code) -> pe
    daily_mv    = {}    # (td, ts_code) -> mv_yi

    for td in dates:
        if td not in db_cache: continue
        b = db_cache[td]
        rows = b[
            (b['mv_yi'] >= MV_MIN) & (b['mv_yi'] <= MV_MAX) &
            (~b['ts_code'].str.startswith(('8','4','9'))) &
            (b['pe'] > 0) & (b['pe'] <= PE_MAX)
        ]
        daily_cands[td] = set(rows['ts_code'])
        for _, r in rows.iterrows():
            daily_pe[(td, r['ts_code'])] = r['pe']
            daily_mv[(td, r['ts_code'])] = r['mv_yi']

    all_candidates = set()
    for s in daily_cands.values():
        all_candidates.update(s)
    print(f'Total unique candidates: {len(all_candidates)}', flush=True)

    # 7. 循环反转：每只股票只读一次文件
    repeat_count  = {}   # tc -> consecutive days count
    last_seen_dt  = {}   # tc -> last seen date
    last_stop_dt  = {}   # tc -> last stop-loss date

    all_trades = []
    skipped_breakout = 0; skipped_low_score = 0
    skipped_no_data = 0; skipped_cooldown = 0

    stock_list = sorted(all_candidates)
    total_stocks = len(stock_list)

    for idx_s, tc in enumerate(stock_list):
        if (idx_s + 1) % 200 == 0:
            print(f'  Stock {idx_s+1}/{total_stocks} trades={len(all_trades)}', flush=True)

        tdx_path = tdx_index.get(tc)
        if not tdx_path:
            skipped_no_data += 1; continue

        df_all = parse_day_file(tdx_path)
        if df_all is None or len(df_all) < 60:
            skipped_no_data += 1; continue

        df_all = df_all.set_index('trade_date').sort_index()
        all_idx_dates = df_all.index.tolist()  # list of Timestamp

        # 预计算所有技术指标
        c    = df_all['close'];  h = df_all['high'];  l = df_all['low'];  v = df_all['vol']
        ma5  = c.rolling(5).mean()
        ma10 = c.rolling(10).mean()
        ma20 = c.rolling(20).mean()
        ma60 = c.rolling(60).mean()
        v20  = v.rolling(20).mean()
        lo120 = l.rolling(120).min()
        hi120 = h.rolling(120).max()

        fin = fin_cache.get(tc)

        for di, td in enumerate(dates):
            td_dt  = datetime.strptime(td, '%Y%m%d')
            td_str = td_dt.strftime('%Y-%m-%d')
            td_ts  = pd.Timestamp(td_str)

            # 当天是否在候选池
            if td not in daily_cands or tc not in daily_cands[td]:
                continue

            # 找最近交易日 < td
            avail = [i for i, d in enumerate(all_idx_dates) if d < td_ts]
            if len(avail) < 60: continue
            last_i = avail[-1]

            price = c.iloc[last_i]; ma5_v = ma5.iloc[last_i]; ma10_v = ma10.iloc[last_i]
            ma20_v = ma20.iloc[last_i]; ma60_v = ma60.iloc[last_i]
            hi21   = h.iloc[max(0, last_i-21):last_i].max()
            vr     = (v.iloc[max(0,last_i-2):last_i+1] / v20.iloc[max(0,last_i-2):last_i+1]).mean() if not np.isnan(v20.iloc[last_i]) else 1.0
            pos120 = (price - lo120.iloc[last_i]) / max(hi120.iloc[last_i] - lo120.iloc[last_i], 0.001) * 100
            pct5   = (price / c.iloc[last_i-5] - 1)*100 if last_i >= 5 else 0
            hi45   = h.iloc[max(0,last_i-45):max(0,last_i-5)].max()
            lo45   = l.iloc[max(0,last_i-45):max(0,last_i-5)].min()
            pct45  = (hi45 - lo45) / lo45 * 100 if lo45 > 0 else 999

            sc = 0
            if ma5_v > ma10_v > ma20_v: sc += 25
            if ma20_v > ma60_v: sc += 20
            if vr > 1.3: sc += 15
            if price > hi21 * 0.98: sc += 15
            if pos120 < 70: sc += 10
            if 3 < pct5 < 20: sc += 10
            if pct45 < 25: sc += 5

            if sc < MIN_TECH_SCORE:
                skipped_low_score += 1; continue

            entry_type, entry_bonus = detect_entry_type(price, ma5_v, ma10_v, ma20_v, ma60_v, hi21)
            sc += entry_bonus

            # 冷却期
            if tc in last_stop_dt and (td_dt - last_stop_dt[tc]).days < STOP_COOLDOWN:
                skipped_cooldown += 1; continue

            # 二次入选
            if tc in last_seen_dt and (td_dt - last_seen_dt[tc]).days <= 40:
                repeat_count[tc] = repeat_count.get(tc, 0) + 1
            else:
                repeat_count[tc] = 1
            last_seen_dt[tc] = td_dt

            if repeat_count[tc] < MIN_REPEAT: continue

            fin_s = ai_financial_score(fin, daily_pe.get((td, tc), 0), 0)
            if fin_s < FIN_SCORE_MIN: continue

            # ===== 找买点 =====
            actual_buy_price = price; actual_buy_date = td_dt; pullback_wait = 0
            if entry_type in ('breakout', 'far_away', 'breakout_near_ma'):
                found_pb = None
                for offset in range(1, 6):
                    avail2 = [i for i, d in enumerate(all_idx_dates) if d >= td_ts + timedelta(days=offset)]
                    if not avail2: break
                    ni = avail2[0]; cp = c.iloc[ni]; m5 = ma5.iloc[ni]; m10 = ma10.iloc[ni]
                    tol = 0.03
                    if (m5*(1-tol) <= cp <= m5*(1+tol)) or (m10*(1-tol) <= cp <= m10*(1+tol)):
                        found_pb = (all_idx_dates[ni], cp); break
                if found_pb is None:
                    skipped_breakout += 1; continue
                actual_buy_date, actual_buy_price = found_pb
                pullback_wait = (actual_buy_date - td_dt).days
                if not isinstance(actual_buy_date, datetime): actual_buy_date = actual_buy_date.to_pydatetime()

            # ===== 找卖点 =====
            buy_avail = [i for i, d in enumerate(all_idx_dates) if d >= actual_buy_date]
            if not buy_avail: continue
            buy_idx = buy_avail[0]

            stop_triggered = False; sell_price = actual_buy_price; actual_hold = HOLD
            for j in range(1, min(HOLD + 1, len(all_idx_dates) - buy_idx)):
                idx2 = buy_idx + j
                if df_all.iloc[idx2]['low'] / actual_buy_price - 1 <= STOP:
                    sell_price = actual_buy_price * (1 + STOP)
                    actual_hold = j
                    stop_triggered = True
                    last_stop_dt[tc] = actual_buy_date
                    break
            if not stop_triggered:
                exit_idx2 = min(buy_idx + HOLD, len(all_idx_dates) - 1)
                sell_price = df_all.iloc[exit_idx2]['close']
                actual_hold = min(HOLD, len(all_idx_dates) - 1 - buy_idx)

            ret_pct = (sell_price / actual_buy_price - 1) * 100
            segment = df_all.iloc[buy_idx:buy_idx+actual_hold+1]
            max_gain = (segment['high'].max() / actual_buy_price - 1) * 100
            max_dd   = (segment['low'].min() / actual_buy_price - 1) * 100

            exit_ts  = all_idx_dates[min(buy_idx + actual_hold, len(all_idx_dates) - 1)]
            buy_str  = actual_buy_date.strftime('%Y-%m-%d')
            exit_str = exit_ts.strftime('%Y-%m-%d') if isinstance(exit_ts, pd.Timestamp) else str(exit_ts)

            all_trades.append({
                'date': td, 'actual_buy_date': buy_str, 'exit_date': exit_str,
                'name': nm_map.get(tc, tc), 'ts_code': tc,
                'orig_buy': round(price, 2), 'actual_buy': round(actual_buy_price, 2),
                'sell': round(sell_price, 2),
                'pullback_wait': pullback_wait,
                'return_pct': round(ret_pct, 2),
                'max_gain': round(max_gain, 2), 'max_dd': round(max_dd, 2),
                'stopped': stop_triggered,
                'fin_score': fin_s, 'tech_score': sc,
                'repeat': repeat_count[tc],
                'pe': round(daily_pe.get((td, tc), 0), 1),
                'entry_type': entry_type,
            })

    # 保存缓存
    pickle.dump(fin_cache, open(os.path.join(BASE_DIR, 'fin_cache_v4.pkl'), 'wb'))

    if not all_trades:
        print('No trades!'); return

    rdf = pd.DataFrame(all_trades)
    total = len(rdf); wins = (rdf['return_pct'] > 0).sum()
    wr = wins / total * 100
    avg_ret = rdf['return_pct'].mean()
    cum = (1 + rdf['return_pct'] / 100).prod() - 1
    avg_w = rdf[rdf['return_pct'] > 0]['return_pct'].mean() if wins else 0
    losses = total - wins
    avg_l = abs(rdf[rdf['return_pct'] <= 0]['return_pct'].mean()) if losses else 1
    plr = avg_w / avg_l if avg_l > 0 else 999

    print()
    print('='*60)
    print('V4 BACKTEST RESULT (reversed-loop, no-hot-sector)')
    print('='*60)
    print(f'Period: {START}~{END}  ({len(dates)} trade days)')
    print(f'Skip: breakout={skipped_breakout}, low_score={skipped_low_score},')
    print(f'       no_data={skipped_no_data}, cooldown={skipped_cooldown}')
    print(f'Trades: {total} | Win rate: {wr:.1f}%')
    print(f'Avg ret: {avg_ret:+.2f}% | Compound: {cum*100:+.2f}%')
    print(f'PLR: {plr:.2f}  (avg_w={avg_w:.2f}% / avg_l={avg_l:.2f}%)')
    print(f'Stopped: {int(rdf["stopped"].sum())} ({rdf["stopped"].mean()*100:.0f}%)')
    print(f'Max gain avg: {rdf["max_gain"].mean():+.2f}%')
    print(f'Max DD avg: {rdf["max_dd"].mean():+.2f}%')

    rdf.to_csv(os.path.join(BASE_DIR, 'backtest_v4_nohs.csv'), index=False, encoding='utf-8-sig')
    print(f'Saved: backtest_v4_nohs.csv')

if __name__ == '__main__':
    main()
