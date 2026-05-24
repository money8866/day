# -*- coding: utf-8 -*-
"""V3本地半年回测 - 纯技术+基本面，无热点板块过滤（基准测试）"""
import sys; sys.stdout.reconfigure(encoding='utf-8')
import os, pickle, struct, time
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

BASE_DIR = r'C:\Users\kongx\mystock'
TDX_SH    = r'C:\new_tdx\vipdoc\sh\lday'
TDX_SZ    = r'C:\new_tdx\vipdoc\sz\lday'
HOLD      = 5
STOP      = -0.05

# ---------- 通达信日线解析（V4版，修复顺序） ----------
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
            records.append({'trade_date': date_str, 'open': o/100.0, 'high': h/100.0,
                             'low': l/100.0, 'close': c/100.0, 'amount': amt, 'vol': vol})
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
            code = fname[:-4]
            if code.startswith('sh'): ts = code[2:] + '.SH'
            elif code.startswith('sz'): ts = code[2:] + '.SZ'
            else: continue
            index[ts] = os.path.join(tdx_dir, fname)
    return index

def get_df(ts_code, cutoff_date_str, tdx_index, lookback=150):
    """获取指定日期及之前的日线（本地）"""
    path = tdx_index.get(ts_code)
    if not path: return None
    df = parse_day_file(path)
    if df is None: return None
    avail = df[df['trade_date'] < cutoff_date_str]
    if len(avail) < 65: return None
    return avail.tail(lookback + 30)

# ---------- V3技术评分 ----------
def tech_score(c, v, h, l):
    ma5  = c.rolling(5).mean().iloc[-1]
    ma10 = c.rolling(10).mean().iloc[-1]
    ma20 = c.rolling(20).mean().iloc[-1]
    ma60 = c.rolling(60).mean().iloc[-1]
    price = c.iloc[-1]
    sc = 0
    if ma5 > ma10 > ma20: sc += 25
    if ma20 > ma60: sc += 20
    vr = v / v.rolling(20).mean()
    if vr.iloc[-3:].mean() > 1.3: sc += 15
    high_21 = h.iloc[-21:-1].max()
    if price > high_21 * 0.98: sc += 15
    pos120 = (price - l.rolling(120).min().iloc[-1]) / \
             (h.rolling(120).max().iloc[-1] - l.rolling(120).min().iloc[-1]) * 100
    if pos120 < 70: sc += 10
    pct5 = (price / c.iloc[-6] - 1) * 100
    if 3 < pct5 < 20: sc += 10
    rh = h.iloc[-45:-5].max(); rl = l.iloc[-45:-5].min()
    if rl > 0 and (rh - rl) / rl * 100 < 25: sc += 5
    return sc

# ---------- 基本面评分（简化） ----------
def fin_score(fin_dict):
    gm    = fin_dict.get('grossprofit_margin') or 0
    nm    = fin_dict.get('netprofit_margin') or 0
    op    = fin_dict.get('op_yoy') or 0
    debt  = fin_dict.get('debt_to_assets') or 0
    roe   = fin_dict.get('roe') or 0
    s = 0
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

# ---------- 主程序 ----------
def main():
    import tushare as ts
    env = Path(os.path.join(BASE_DIR, '.env')).read_text()
    for line in env.splitlines():
        if line.startswith('TUSHARE_TOKEN='):
            ts.set_token(line.split('=', 1)[1].strip())
    pro = ts.pro_api()

    print('='*80')
    print('V3 本地半年回测 (无热点板块过滤)  |  2024-11-25 ~ 2026-05-22')
    print('='*80')

    tdx_index = build_tdx_index()
    print(f'通达信文件: {len(tdx_index)}')

    # 交易日历
    cal = pro.trade_cal(exchange='SSE', is_open=1,
                         start_date='20241125', end_date='20260522')
    dates = sorted(cal['cal_date'].tolist())
    print(f'交易日: {dates[0]} ~ {dates[-1]} ({len(dates)}天)')

    # 预缓存基本面（fin_cache_v4.pkl）
    fin_cache = {}
    cache_path = os.path.join(BASE_DIR, 'fin_cache_v4.pkl')
    if os.path.exists(cache_path):
        fin_cache = pickle.load(open(cache_path, 'rb'))
        print(f'基本面缓存: {len(fin_cache)} 只股')
    else:
        print('[WARN] 无 fin_cache_v4.pkl，基本面全0分')

    repeat_tracker = defaultdict(int)
    holdings = {}    # {ts_code: {'buy_price', 'buy_date', 'days_held', 'stopped'}}
    trades = []
    wins = losses = stops = 0

    for di, td in enumerate(dates):
        td_dt  = datetime.strptime(td, '%Y%m%d')
        td_str = td_dt.strftime('%Y-%m-%d')

        # --- 处理持仓 ---
        sell_list = []
        for stk, info in list(holdings.items()):
            info['days_held'] += 1
            buy_dt  = datetime.strptime(info['buy_date'], '%Y%m%d')
            days_h  = (td_dt - buy_dt).days

            if info.get('stopped') or days_h >= HOLD:
                sell_list.append(stk)

        for stk in sell_list:
            info = holdings.pop(stk)
            df = get_df(stk, td_str, tdx_index, lookback=5)
            sell_p = df.iloc[-1]['close'] if df is not None else info['buy_price']
            ret = (sell_p / info['buy_price']) - 1
            trades.append({
                'buy_date': info['buy_date'], 'sell_date': td,
                'ts_code': stk, 'buy_price': info['buy_price'],
                'sell_price': sell_p, 'return': ret,
                'type': 'stop' if info.get('stopped') else 'normal'
            })
            if ret > 0: wins += 1
            else:
                losses += 1
                if info.get('stopped'): stops += 1

        # --- 选股（仅最后HOLD天前）---
        if di >= len(dates) - HOLD: continue

        picks = []
        for ts_code, path in tdx_index.items():
            df = get_df(ts_code, td_str, tdx_index)
            if df is None: continue
            c = df['close']; h = df['high']; l = df['low']; v = df['vol']
            price = c.iloc[-1]
            if price <= 0 or price > 300: continue

            sc = tech_score(c, v, h, l)
            if sc < 60: continue

            fin = fin_cache.get(ts_code, {})
            fs  = fin_score(fin)
            repeat_tracker[ts_code] += 1

            picks.append({
                'ts_code': ts_code, 'close': price,
                'tech_score': sc, 'fin_score': fs,
                'repeat': repeat_tracker[ts_code]
            })

        # 二次入选 + 基本面≥20
        a = [p for p in picks if p['repeat'] >= 2 and p['fin_score'] >= 20]
        a.sort(key=lambda x: (x['fin_score'], x['repeat']), reverse=True)

        for pick in a[:3]:
            if pick['ts_code'] in holdings: continue
            holdings[pick['ts_code']] = {
                'buy_price': pick['close'], 'buy_date': td,
                'days_held': 0, 'stopped': False
            }

        # --- 日志 ---
        if di % 30 == 0 or di == len(dates) - 1:
            total = wins + losses
            wr    = f'{wins/total*100:.1f}%' if total else '-'
            cr    = f'{sum(t["return"] for t in trades)*100:.1f}%' if trades else '0%'
            print(f'  {td} | {len(trades):3d}笔 | 胜{wins}负{losses}(止{stops}) '
                  f'| 胜率{wr} | 累计{cr}')

    # --- 汇总 ---
    total = wins + losses
    rets  = [t['return'] for t in trades]
    print('\n' + '='*80)
    print(f'V3 本地半年回测结果')
    print(f'总交易: {len(trades)} 笔 | 胜: {wins} | 负: {losses} | 止损: {stops}')
    print(f'胜率:   {wins/total*100:.1f}%' if total else '胜率: N/A')
    print(f'均收:   {np.mean(rets)*100:.2f}%' if rets else '均收: N/A')
    print(f'累计:   {sum(rets)*100:.1f}%')
    print(f'最大:   {max(rets)*100:.1f}% | 最小: {min(rets)*100:.1f}%')
    if rets:
        pos = sorted(rets)
        print(f'P10:   {pos[int(len(pos)*0.1)]:.2f}% | P50: {pos[len(pos)//2]*100:.2f}%')

    out = pd.DataFrame(trades)
    out_path = os.path.join(BASE_DIR, 'backtest_v3_local_halfyear.csv')
    out.to_csv(out_path, index=False, encoding='utf-8-sig')
    print(f'\n明细已保存: {out_path}')

if __name__ == '__main__':
    main()
