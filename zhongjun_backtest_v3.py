# -*- coding: utf-8 -*-
"""
中军选股 V3 回测脚本
回测区间: 40天 (HOLD=5, STOP=-5%)
数据源: Tushare
"""
import os, sys, json, pickle, datetime, warnings
import numpy as np
import pandas as pd
import tushare as ts

sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

PROJECT_DIR = r'C:\Users\kongx\mystock'
HIST_DIR    = os.path.join(PROJECT_DIR, 'screen_history')
FIN_CACHE   = os.path.join(PROJECT_DIR, 'fin_cache_v4.pkl')
TUSHARE_TOKEN = 'bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d'
HOLD_DAYS   = 5
STOP_LOSS   = -0.05
MIN_FIN     = 20
MIN_TECH    = 60
MA5, MA10, MA20, MA60 = 5, 10, 20, 60

ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# ===== 同 V3 生产版的评分函数 =====
def load_fin_cache():
    if os.path.exists(FIN_CACHE):
        try: return pickle.load(open(FIN_CACHE, 'rb'))
        except: pass
    return {}

def calc_ma(closes, n):
    if len(closes) < n: return None
    return np.mean(closes[-n:])

def load_histories():
    histories = {}
    if os.path.exists(HIST_DIR):
        for f in os.listdir(HIST_DIR):
            if f.startswith('history_') and f.endswith('.json'):
                try:
                    with open(os.path.join(HIST_DIR, f), 'r', encoding='utf-8') as fp:
                        histories.update(json.load(fp))
                except:
                    pass
    return histories

def count_repeats_from_history(ts_code, histories, before_date):
    count = 0
    if ts_code in histories:
        for rec in histories[ts_code].get('records', []):
            if rec.get('date', '') < before_date:
                count += 1
    return count

def tech_score(price, ma5_v, ma10_v, ma20_v, ma60_v, high_21, vol, avg_vol):
    sc = 0
    if ma5_v and ma10_v and ma20_v and price > ma5_v > ma10_v > ma20_v: sc += 10
    if ma20_v and ma60_v and ma20_v > ma60_v: sc += 5
    if vol and avg_vol and vol > avg_vol * 1.5: sc += 10
    elif vol and avg_vol and vol > avg_vol: sc += 5
    if high_21 and price >= high_21 * 0.90: sc += 5
    if ma5_v and abs(price - ma5_v) / ma5_v < 0.03: sc += 5
    return sc

def ai_financial_score(fin):
    if fin is None: return 0
    sc = 0
    gm = fin.get('grossprofit_margin', 0) or 0
    nm = fin.get('netprofit_margin', 0) or 0
    if gm >= 30 and nm >= 15: sc += 10
    elif gm >= 20 and nm >= 10: sc += 5
    oy = fin.get('op_yoy', 0) or 0
    if oy >= 20: sc += 10
    elif oy >= 10: sc += 5
    da = fin.get('debt_to_assets', 0) or 0
    if da <= 50: sc += 5
    elif da <= 70: sc += 2
    ocf = fin.get('ocf_to_or', 0) or 0
    if ocf >= 10: sc += 5
    elif ocf >= 0: sc += 2
    return sc

# ===== 回测主循环 =====
def backtest(start_date, end_date):
    print(f"V3 回测: {start_date} → {end_date} | HOLD={HOLD_DAYS}天 | STOP={STOP_LOSS*100:.0f}%")
    print("=" * 60)

    # 交易日历
    cal = pro.trade_cal(start_date=start_date, end_date=end_date)
    cal = cal[cal['is_open'] == 1].sort_values('cal_date')
    dates = cal['cal_date'].tolist()

    fin_cache = load_fin_cache()
    histories = {}
    trades, wins, stops = [], 0, 0
    total_pnl, total_win = 0, 0

    # 每次选股扫描窗口
    scan_dates = [d for d in dates if True]  # 每日扫描

    for i, date_str in enumerate(scan_dates):
        # 重建历史（仅用 date_str 之前的数据）
        histories = {}  # 简化：每日重置历史计数
        # 实际回测应维护累积历史，这里简化处理

        # 获取当日全市场
        try:
            df = pro.daily(trade_date=date_str)
            if df is None or len(df) == 0: continue
        except:
            continue

        candidates = []
        for _, row in df.iterrows():
            tc = row['ts_code']
            price = row['close']
            vol = row.get('vol', 0)
            pe = row.get('pe', 0) or 0

            if pe <= 0 or pe > 100: continue

            fin = fin_cache.get(tc)
            if fin is None: continue

            # 获取历史K线
            try:
                start_d = (datetime.datetime.strptime(date_str, '%Y%m%d')
                          - datetime.timedelta(days=90)).strftime('%Y%m%d')
                d = pro.daily(ts_code=tc, start_date=start_d, end_date=date_str)
                if d is None or len(d) < MA60: continue
                d = d.sort_values('trade_date')
                closes = d['close'].tolist()
                ma5_v  = calc_ma(closes, MA5)
                ma10_v = calc_ma(closes, MA10)
                ma20_v = calc_ma(closes, MA20)
                ma60_v = calc_ma(closes, MA60)
                high_21 = max(closes[-21:]) if len(closes) >= 21 else price
                avg_vol = np.mean(d['vol'].tolist()[-20:]) if len(d) >= 20 else vol
            except:
                continue

            tsc = tech_score(price, ma5_v, ma10_v, ma20_v, ma60_v, high_21, vol, avg_vol)
            if tsc < MIN_TECH: continue

            fin_sc = ai_financial_score(fin)
            if fin_sc < MIN_FIN: continue

            candidates.append((tc, price, tsc, fin_sc, date_str))

        if not candidates: continue

        # 选总分最高1只
        best = max(candidates, key=lambda x: x[2] + x[3])
        tc, entry_price, _, _, entry_date = best

        # 模拟持有
        entry_idx = dates.index(entry_date) if entry_date in dates else i
        exit_idx = min(entry_idx + HOLD_DAYS, len(dates) - 1)
        exit_date = dates[exit_idx]

        try:
            exit_df = pro.daily(ts_code=tc, trade_date=exit_date)
            if exit_df is None or len(exit_df) == 0:
                exit_df = pro.daily(ts_code=tc, trade_date=dates[min(exit_idx+1, len(dates)-1)])
            exit_price = exit_df.iloc[0]['close']
        except:
            exit_price = entry_price

        pnl = (exit_price - entry_price) / entry_price
        if pnl <= STOP_LOSS:
            stops += 1
            result = '止损'
        elif pnl > 0:
            wins += 1
            result = '盈利'
        else:
            result = '亏损'
        total_pnl += pnl
        total_win += max(pnl, 0)
        trades.append({
            'date': entry_date, 'ts_code': tc,
            'entry': entry_price, 'exit': exit_price,
            'pnl': f'{pnl*100:.1f}%', 'result': result
        })
        print(f"  {entry_date} {tc} 买入{entry_price:.2f} → {exit_date} 卖出{exit_price:.2f} {pnl*100:+.1f}% {result}")

    # 汇总
    n = len(trades)
    print(f"\n===== V3 回测汇总 =====")
    print(f"总交易: {n} 笔")
    if n > 0:
        print(f"胜率: {wins/n*100:.1f}%")
        print(f"止损: {stops} 笔")
        print(f"累计收益: {total_pnl*100:.1f}%")
        print(f"平均收益: {total_pnl/n*100:.2f}%")
        print(f"盈亏比: {total_win/max(total_pnl-total_win, 0.001):.2f}" if total_pnl > total_win else "N/A")
    return trades

if __name__ == '__main__':
    end = datetime.date.today().strftime('%Y%m%d')
    start = (datetime.datetime.now() - datetime.timedelta(days=50)).strftime('%Y%m%d')
    backtest(start, end)
