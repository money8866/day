# -*- coding: utf-8 -*-
"""V3本地半年回测 - 基于通达信日线数据"""
import sys; sys.stdout.reconfigure(encoding='utf-8')
import os, pickle, time, struct
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict

BASE_DIR = r'C:\Users\kongx\mystock'
CACHE_DB  = os.path.join(BASE_DIR, 'cache_db')
TDX_SH    = r'C:\new_tdx\vipdoc\sh\lday'
TDX_SZ    = r'C:\new_tdx\vipdoc\sz\lday'

# ---------- 读取 .env ----------
env_path = os.path.join(BASE_DIR, '.env')
for line in open(env_path, encoding='utf-8'):
    if line.startswith('TUSHARE_TOKEN='):
        import tushare as ts; ts.set_token(line.split('=',1)[1].strip())
        pro = ts.pro_api(); break

# ---------- V3参数 ----------
PE_MAX         = 100
MV_MIN, MV_MAX = 100, 500
MIN_TECH_SCORE = 60
FIN_SCORE_MIN  = 20
MIN_REPEAT     = 2
HOLD_DAYS      = 5
STOP_LOSS      = -0.05

# ---------- 通达信日线解析 ----------
def parse_day_file(path):
    if not os.path.exists(path): return pd.DataFrame()
    with open(path, 'rb') as f:
        data = f.read()
    records = []
    for i in range(0, len(data), 36):
        chunk = data[i:i+36]
        if len(chunk) < 36: break
        date, open_, high, low, close, amount, vol, _ = struct.unpack('>IIII>dqII', chunk)
        if date == 0: break
        records.append({'trade_date': str(date), 'open': open_/100.0, 'high': high/100.0,
                         'low': low/100.0, 'close': close/100.0, 'amount': amount, 'vol': vol})
    return pd.DataFrame(records)

def build_tdx_index():
    index = {}
    for d in [TDX_SH, TDX_SZ]:
        if not os.path.exists(d): continue
        for fname in os.listdir(d):
            if not fname.endswith('.day'): continue
            ts_ = fname[:-4]
            # 6开头=SH, 0/3开头=SZ
            prefix = 'SH' if ts_.startswith('6') else 'SZ'
            index[prefix + ts_] = os.path.join(d, fname)
    return index

def get_tdx_data(ts_code, trade_date, lookback=150):
    """获取指定日期前N天日线（本地）"""
    idx = build_tdx_index()
    path = idx.get(ts_code)
    if not path: return pd.DataFrame()
    df = parse_day_file(path)
    if df.empty: return pd.DataFrame()
    df['trade_date'] = df['trade_date'].astype(str)
    df = df[df['trade_date'] <= trade_date].tail(lookback + 30).reset_index(drop=True)
    return df

# ---------- V3技术评分（无entry_type） ----------
def tech_score_v3(c, v, h, l):
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
    pos120 = (price - l.rolling(120).min().iloc[-1]) / (h.rolling(120).max().iloc[-1] - l.rolling(120).min().iloc[-1]) * 100
    if pos120 < 70: sc += 10
    pct5 = (price / c.iloc[-6] - 1) * 100
    if 3 < pct5 < 20: sc += 10
    rh = h.iloc[-45:-5].max(); rl = l.iloc[-45:-5].min()
    if rl > 0 and (rh - rl) / rl * 100 < 25: sc += 5
    return sc

# ---------- 基本面评分（简化，不调API） ----------
def fin_score_v4(fin_dict, pe, sec_cnt):
    gm = fin_dict.get('grossprofit_margin') or 0
    nm = fin_dict.get('netprofit_margin') or 0
    op = fin_dict.get('op_yoy') or 0
    debt = fin_dict.get('debt_to_assets') or 0
    roe = fin_dict.get('roe') or 0
    sc = 0
    if gm >= 40: sc += 8
    elif gm >= 30: sc += 4
    if nm >= 15: sc += 8
    elif nm >= 8: sc += 4
    if op >= 30: sc += 8
    elif op >= 10: sc += 4
    elif op >= 0: sc += 2
    if debt <= 30: sc += 5
    elif debt <= 50: sc += 2
    if roe >= 15: sc += 6
    elif roe >= 8: sc += 3
    if pe <= 50: sc += 5
    elif pe <= 80: sc += 2
    if sec_cnt >= 3: sc += 4
    elif sec_cnt >= 2: sc += 2
    elif sec_cnt >= 1: sc += 1
    return sc

# ---------- 主线板块读取 ----------
def load_hot_sectors(td):
    """从 cache_db/*.db 读取当天热点板块"""
    td8 = td  # YYYYMMDD
    hot = []
    for fname in os.listdir(CACHE_DB):
        if not fname.endswith('.db'): continue
        fpath = os.path.join(CACHE_DB, fname)
        df = pd.read_sql_query(
            f"SELECT name, stocks FROM blocks WHERE trade_date=? AND hot_rank<=5",
            sqlite3.connect(fpath), params=(td8,)
        )
        for _, row in df.iterrows():
            try: stocks = json.loads(row['stocks'])
            except: stocks = []
            hot.append({'name': row['name'], 'stocks': stocks})
    return hot

# ---------- 回测主循环 ----------
def backtest_halfyear():
    import sqlite3, json
    print("="*80)
    print("V3 本地半年回测 | 2024-11-25 ~ 2026-05-22")
    print("="*80)

    # 交易日历
    end_dt   = datetime(2026, 5, 22)
    start_dt = datetime(2024, 11, 25)
    dates = []
    cur = start_dt
    while cur <= end_dt:
        if cur.weekday() < 5:
            dates.append(cur.strftime('%Y%m%d'))
        cur += timedelta(days=1)
    print(f"交易日: {dates[0]} ~ {dates[-1]} ({len(dates)}天)")

    # 加载基本面缓存（从V4缓存复用）
    fin_cache = {}
    cache_path = os.path.join(BASE_DIR, 'fin_cache_v4.pkl')
    if os.path.exists(cache_path):
        fin_cache = pickle.load(open(cache_path, 'rb'))
        print(f"加载基本面缓存: {len(fin_cache)} 只股")
    else:
        print("[WARN] 无 fin_cache_v4.pkl，基本面用默认值0分")

    hot_cache = {}   # {date: [block_dict...]}
    tdx_index = build_tdx_index()
    print(f"通达信索引: {len(tdx_index)} 只股票")

    # 每日热点缓存
    for td in dates:
        hot = load_hot_sectors(td)
        hot_cache[td] = hot

    repeat_tracker = defaultdict(int)   # {stock: count}
    holdings = {}    # {stock: {'buy_price': x, 'sell_date': d}}
    trades = []
    daily_stats = defaultdict(list)  # {date: [trade...]}

    for day_idx, td in enumerate(dates):
        # 处理持仓卖出
        sells = []
        for stk, info in list(holdings.items()):
            info['days_held'] = info.get('days_held', 0) + 1
            td_dt = datetime.strptime(td, '%Y%m%d')
            buy_dt = datetime.strptime(info['buy_date'], '%Y%m%d')
            days_held = (td_dt - buy_dt).days

            if days_held >= HOLD_DAYS:
                # 持有满5天，按当天收盘卖
                sells.append(stk)
            elif info.get('stopped'):
                sells.append(stk)

        for stk in sells:
            info = holdings.pop(stk)
            buy_p = info['buy_price']
            # 找当天收盘
            df = get_tdx_data(stk, td, lookback=5)
            if df.empty:
                ret = 0.0
            else:
                ret = (df.iloc[-1]['close'] / buy_p - 1)
            trades.append({
                'buy_date': info['buy_date'], 'sell_date': td,
                'ts_code': stk, 'buy_price': buy_p,
                'sell_price': df.iloc[-1]['close'] if not df.empty else buy_p,
                'return': ret, 'type': 'stop' if info.get('stopped') else 'normal'
            })
            daily_stats[td].append({'stk': stk, 'ret': ret})

        # ---------- 选股 ----------
        hot = hot_cache.get(td, [])
        picks = []

        for ts_code, path in tdx_index.items():
            df = get_tdx_data(ts_code, td, lookback=150)
            if df.shape[0] < 65: continue
            c = df['close']; v = df['vol']; h = df['high']; l = df['low']
            price = c.iloc[-1]
            # PE/市值从当日热点数据推断（用固定区间过滤）
            if price <= 0: continue
            # 跳过高价股（约等于PE<100的逻辑）
            if price > 300: continue

            sc = tech_score_v3(c, v, h, l)
            if sc < MIN_TECH_SCORE: continue

            # 主线匹配
            sec_cnt = sum(1 for blk in hot if ts_code in blk['stocks'])
            # 基本面
            fin = fin_cache.get(ts_code, {})
            fin_s = fin_score_v4(fin, 50, sec_cnt)
            repeat_tracker[ts_code] += 1
            picks.append({
                'ts_code': ts_code, 'close': price,
                'tech_score': sc, 'fin_score': fin_s,
                'sector_count': sec_cnt, 'repeat': repeat_tracker[ts_code]
            })

        # 二次入选 + 基本面门槛
        a_picks = [p for p in picks
                   if p['repeat'] >= MIN_REPEAT and p['fin_score'] >= FIN_SCORE_MIN]
        a_picks.sort(key=lambda x: (x['fin_score'], x['repeat'], x['sector_count']), reverse=True)

        # 最多入3只
        for pick in a_picks[:3]:
            if pick['ts_code'] in holdings: continue
            holdings[pick['ts_code']] = {
                'buy_price': pick['close'], 'buy_date': td,
                'days_held': 0, 'stopped': False
            }

        # ---------- 日报 ----------
        if day_idx % 20 == 0 or day_idx == len(dates)-1:
            win  = sum(1 for t in trades if t['return'] > 0)
            loss = sum(1 for t in trades if t['return'] <= 0)
            stop_n = sum(1 for t in trades if t['type'] == 'stop')
            total_r = sum(t['return'] for t in trades)
            print(f"  {td} | 累计{trades.__len__()}笔 | 胜{win}负{loss}(止{stop_n}) | 累计收益{total_r*100:.1f}%")

    # ---------- 汇总 ----------
    print("\n" + "="*80)
    wins = [t for t in trades if t['return'] > 0]
    losses = [t for t in trades if t['return'] <= 0]
    stops = [t for t in trades if t['type'] == 'stop']
    print(f"V3 本地半年回测结果 ({dates[0]} ~ {dates[-1]})")
    print(f"总交易笔数: {len(trades)}")
    print(f"盈利: {len(wins)} | 亏损: {len(losses)} | 止损: {len(stops)}")
    if trades:
        rets = [t['return'] for t in trades]
        print(f"胜率: {len(wins)/len(trades)*100:.1f}%")
        print(f"平均收益: {np.mean(rets)*100:.2f}%")
        print(f"累计收益: {sum(rets)*100:.1f}%")
        print(f"最大单笔: {max(rets)*100:.1f}% | 最小: {min(rets)*100:.1f}%")
        # 保存
        out = pd.DataFrame(trades)
        out_path = os.path.join(BASE_DIR, 'backtest_v3_local_halfyear.csv')
        out.to_csv(out_path, index=False, encoding='utf-8-sig')
        print(f"\n明细: {out_path}")

if __name__ == '__main__':
    import sqlite3, json
    backtest_halfyear()
