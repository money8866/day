# -*- coding: utf-8 -*-
"""
V4 回测 - 本地通达信日线数据（无API限频）
路径: C:\new_tdx\vipdoc\sh\lday/*.day  |  sz\lday/*.day
格式: 32字节/条，小端序，日期=YYYYMMDD整数，价格=整数/100
"""
import sys; sys.stdout.reconfigure(encoding='utf-8')
import os, struct, pickle, json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict

BASE_DIR = r'C:\Users\kongx\mystock'
TDX_SH = r'C:\new_tdx\vipdoc\sh\lday'
TDX_SZ = r'C:\new_tdx\vipdoc\sz\lday'

# ============================================================
# 通达信日线解析（32字节/条，小端序）
# 0-3:   date (uint32 LE, YYYYMMDD)
# 4-7:   open  (uint32 LE / 100)
# 8-11:  high  (uint32 LE / 100)
# 12-15: low   (uint32 LE / 100)
# 16-19: close (uint32 LE / 100)
# 20-27: amount (double, 元)
# 28-31: vol   (uint32 LE, 股数)
# ============================================================
def parse_day_file(path):
    if not os.path.exists(path): return None
    records = []
    with open(path, 'rb') as f:
        data = f.read()
    step = 32
    for i in range(0, len(data), step):
        chunk = data[i:i+step]
        if len(chunk) < step: break
        date_int, o, h, l, c = struct.unpack('<5I', chunk[:20])
        if date_int == 0: break
        amt, vol = struct.unpack('<dI', chunk[20:32])
        date_str = f"{date_int//10000:04d}-{date_int%10000//100:02d}-{date_int%100:02d}"
        records.append({
            'trade_date': date_str,
            'open': o/100.0, 'high': h/100.0, 'low': l/100.0,
            'close': c/100.0, 'amount': amt, 'vol': vol
        })
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
            if code.startswith('sh'):
                ts = code[2:] + '.SH'
            elif code.startswith('sz'):
                ts = code[2:] + '.SZ'
            else:
                continue
            index[ts] = os.path.join(tdx_dir, fname)
    return index

def calc_ma(s, n): return s.rolling(n).mean()

def load_maps():
    cs = os.path.join(BASE_DIR, 'cache_daily', 'concept_stock_map.pkl')
    concept_map = pickle.load(open(cs, 'rb')) if os.path.exists(cs) else {}
    tm = os.path.join(BASE_DIR, 'theme_map.json')
    theme_map = json.load(open(tm, 'r', encoding='utf-8')) if os.path.exists(tm) else {}
    sc = os.path.join(BASE_DIR, 'cache_daily', 'stock_concept_map.pkl')
    stock_concept_map = pickle.load(open(sc, 'rb')) if os.path.exists(sc) else {}
    sw = os.path.join(BASE_DIR, 'cache_daily', 'sw_map.csv')
    sw_df = pd.read_csv(sw, dtype=str) if os.path.exists(sw) else pd.DataFrame()
    return concept_map, theme_map, stock_concept_map, sw_df

# ============================================================
# 评分函数
# ============================================================
def detect_entry_type(price, ma5, ma10, ma20, ma60, high_21):
    PULLBACK_TOLERANCE = 0.03
    FAR = 1.10
    if ma5*(1-PULLBACK_TOLERANCE) <= price <= ma5*(1+PULLBACK_TOLERANCE):
        return 'pullback_ma5', 10
    if ma10*(1-PULLBACK_TOLERANCE) <= price <= ma10*(1+PULLBACK_TOLERANCE):
        return 'pullback_ma10', 5
    if price > high_21 * 0.98:
        if price <= ma5 * 1.05: return 'breakout_near_ma', 0
        return 'breakout', -5
    if price > ma5 * FAR:
        return 'far_away', -10
    return 'unknown', 0

def ai_financial_score(fin, pe, sector_count=1):
    gm = fin.get('grossprofit_margin') or 0
    nm = fin.get('netprofit_margin') or 0
    op_yoy = fin.get('op_yoy') or 0
    debt = fin.get('debt_to_assets') or 0
    ocf = fin.get('ocf_to_or')
    roe = fin.get('roe') or 0
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

_fin_cache = {}
_fin_cache_file = os.path.join(BASE_DIR, 'fin_cache_v4.pkl')

def load_fin_cache():
    if os.path.exists(_fin_cache_file):
        return pickle.load(open(_fin_cache_file, 'rb'))
    return {}

def save_fin_cache(cache):
    pickle.dump(cache, open(_fin_cache_file, 'wb'))

import tushare as ts
from pathlib import Path

def get_fin(ts_code):
    cache = load_fin_cache()
    if ts_code in cache:
        return cache[ts_code]
    try:
        env = Path(os.path.join(BASE_DIR, '.env')).read_text()
        for line in env.splitlines():
            if line.startswith('TUSHARE_TOKEN='):
                ts.set_token(line.split('=', 1)[1].strip())
        pro = ts.pro_api()
        fi = pro.fina_indicator(ts_code=ts_code, period='20260331',
            fields='ts_code,roe,grossprofit_margin,netprofit_margin,debt_to_assets,op_yoy,ocf_to_or')
        d = fi.iloc[0].to_dict() if len(fi) > 0 else {}
        cache[ts_code] = d
        save_fin_cache(cache)
        import time; time.sleep(0.1)
        return d
    except:
        return {}

def get_hot_sectors_fallback(trade_date, concept_map, stock_concept_map, sw_df, top_n=8):
    try:
        env = Path(os.path.join(BASE_DIR, '.env')).read_text()
        for line in env.splitlines():
            if line.startswith('TUSHARE_TOKEN='):
                ts.set_token(line.split('=', 1)[1].strip())
        pro = ts.pro_api()
        df = pro.daily(trade_date=trade_date, fields='ts_code,close,pct_chg,amount')
        if df.empty: return []
    except: return []
    if sw_df.empty or 'l2_name' not in sw_df.columns: return []
    sw_merge = sw_df[['ts_code', 'l2_name']].dropna(subset=['l2_name'])
    df = df.merge(sw_merge, on='ts_code', how='left').dropna(subset=['l2_name'])
    if df.empty: return []
    ip = df.groupby('l2_name').agg(
        avg_pct=('pct_chg', 'mean'), total_amount=('amount', 'sum'),
        stock_count=('ts_code', 'count'), up_ratio=('pct_chg', lambda x: (x > 0).mean()),
        limit_up=('pct_chg', lambda x: (x >= 9.5).sum())
    ).reset_index()
    ip = ip[ip['stock_count'] >= 5]
    ip['score'] = ip['avg_pct']*1.5 + ip['limit_up']*3 + ip['up_ratio']*8 + np.log1p(ip['total_amount']/1e8)*2
    top = ip.sort_values('score', ascending=False).head(top_n)
    hot = []
    for _, r in top.iterrows():
        ind = r['l2_name']
        stocks = set(sw_df[sw_df['l2_name'] == ind]['ts_code'].dropna().unique().tolist())
        for cn, cl in concept_map.items():
            if ind in cn: stocks.update(cl)
        for tc, cs in stock_concept_map.items():
            if ind in str(cs): stocks.add(tc)
        hot.append({'name': ind, 'score': r['score'], 'momentum': 0, 'leader': '', 'stocks': stocks})
    return hot

# ============================================================
# 参数
# ============================================================
PE_MAX = 100
MV_MIN, MV_MAX = 100, 500
MIN_TECH_SCORE = 60
FIN_SCORE_MIN = 20
MIN_REPEAT = 2
HOLD = 5
STOP = -0.05

def main():
    print("=" * 80)
    print("V4 回测 - 本地通达信日线（无API限频）")
    print("=" * 80)

    # 1. 构建本地日线索引
    print("构建本地日线索引...")
    tdx_index = build_tdx_index()
    print(f"  通达信日线: {len(tdx_index)}只")

    # 2. 加载概念/行业映射
    print("加载概念/行业映射...")
    concept_map, theme_map, stock_concept_map, sw_df = load_maps()

    # 3. 生成交易日列表
    env = Path(os.path.join(BASE_DIR, '.env')).read_text()
    for line in env.splitlines():
        if line.startswith('TUSHARE_TOKEN='):
            ts.set_token(line.split('=', 1)[1].strip())
    pro = ts.pro_api()

    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=60)).strftime('%Y%m%d')
    cal = pro.trade_cal(exchange='SSE', is_open=1, start_date=start, end_date=end)
    dates = sorted(cal['cal_date'].tolist())
    print(f"  回测区间: {dates[0]} ~ {dates[-1]} ({len(dates)}天)")

    # 4. 财务缓存
    fin_cache = load_fin_cache()
    print(f"  财务缓存: {len(fin_cache)}只")

    repeat_tracker = defaultdict(int)
    all_trades = []
    skipped_breakout = 0
    skipped_no_file = 0
    skipped_no_data = 0

    for i in range(0, len(dates) - HOLD, 1):
        td = dates[i]
        td_dt = datetime.strptime(td, '%Y%m%d')
        td_str = td_dt.strftime('%Y-%m-%d')

        # Step1: 主线板块
        hot = get_hot_sectors_fallback(td, concept_map, stock_concept_map, sw_df, top_n=8)
        if not hot: continue
        all_ss = set().union(*[h['stocks'] for h in hot])

        # Step2: 市值/PE筛选
        try:
            basic = pro.daily_basic(trade_date=td, fields='ts_code,close,pe,total_mv')
        except: continue
        if basic.empty: continue
        basic['mv_yi'] = basic['total_mv'] / 10000
        cands = basic[(basic['mv_yi'] >= MV_MIN) & (basic['mv_yi'] <= MV_MAX)]
        cands = cands[~cands['ts_code'].str.startswith(('8', '4', '9'))]
        cands = cands[(cands['pe'] > 0) & (cands['pe'] <= PE_MAX)]
        cands = cands[cands['ts_code'].isin(all_ss)]

        day_picks = []
        for _, row in cands.iterrows():
            tc = row['ts_code']
            pe = row['pe']
            mv = row['mv_yi']

            tdx_path = tdx_index.get(tc)
            if not tdx_path:
                skipped_no_file += 1
                continue

            df = parse_day_file(tdx_path)
            if df is None or len(df) < 60: 
                skipped_no_data += 1
                continue

            # 取td及之前的日线
            df_s = df[df['trade_date'] <= td_str]
            if len(df_s) < 60: continue

            c = df_s['close']; h = df_s['high']; l = df_s['low']; v = df_s['vol']
            ma5_v = c.rolling(5).mean().iloc[-1]
            ma10_v = c.rolling(10).mean().iloc[-1]
            ma20_v = c.rolling(20).mean().iloc[-1]
            ma60_v = c.rolling(60).mean().iloc[-1]
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

            if tc not in fin_cache:
                fin_cache[tc] = get_fin(tc)
            fin = fin_cache[tc]
            fin_s = ai_financial_score(fin, pe, sec_cnt)

            repeat_tracker[tc] += 1

            day_picks.append({
                'ts_code': tc, 'close': price, 'pe': pe, 'mv_yi': mv,
                'tech_score': sc, 'fin_score': fin_s,
                'sector_count': sec_cnt, 'repeat': repeat_tracker[tc],
                'pct_5d': round(pct5, 2), 'entry_type': entry_type,
            })

        if not day_picks: continue

        try:
            sb = pro.stock_basic(fields='ts_code,name')
            nm = dict(zip(sb['ts_code'], sb['name']))
            for p in day_picks:
                p['name'] = nm.get(p['ts_code'], p['ts_code'])
        except: pass

        a_picks = [p for p in day_picks if p['repeat'] >= MIN_REPEAT and p['fin_score'] >= FIN_SCORE_MIN]
        a_picks.sort(key=lambda x: (x['fin_score'], x['repeat'], x['sector_count']), reverse=True)

        for pick in a_picks[:3]:
            tc = pick['ts_code']
            orig_buy = pick['close']
            entry_type = pick['entry_type']
            actual_buy_price = orig_buy
            actual_buy_date = td_dt
            pullback_wait = 0

            # V4核心: 等回档
            if entry_type in ('breakout', 'far_away', 'breakout_near_ma'):
                tdx_path = tdx_index.get(tc)
                if not tdx_path: continue
                df_all = parse_day_file(tdx_path)
                if df_all is None: continue

                found_pb = None
                for day_offset in range(1, 6):
                    check_dt = td_dt + timedelta(days=day_offset)
                    avail = df_all[df_all['trade_date'] >= check_dt]
                    if len(avail) == 0: continue
                    row_j = avail.iloc[0]
                    cp = row_j['close']
                    act_date = row_j['trade_date']

                    # 重算MA
                    hist = df_all[df_all['trade_date'] <= act_date]
                    if len(hist) < 10: continue
                    ma5_c = hist['close'].rolling(5).mean().iloc[-1]
                    ma10_c = hist['close'].rolling(10).mean().iloc[-1]
                    tol = 0.03
                    if (ma5_c*(1-tol) <= cp <= ma5_c*(1+tol) or
                        ma10_c*(1-tol) <= cp <= ma10_c*(1+tol)):
                        found_pb = (act_date, cp)
                        break

                if found_pb is None:
                    skipped_breakout += 1
                    continue
                actual_buy_date, actual_buy_price = found_pb
                pullback_wait = (actual_buy_date - td_dt).days

            # 找持仓结束日期
            tdx_path = tdx_index.get(tc)
            if not tdx_path: continue
            df_all = parse_day_file(tdx_path)
            if df_all is None: continue

            start_idx = df_all[df_all['trade_date'] >= actual_buy_date].index[0]
            exit_idx = min(start_idx + HOLD, len(df_all) - 1)
            exit_row = df_all.iloc[exit_idx]

            stopped = False
            sell = exit_row['close']
            for j_offset in range(1, exit_idx - start_idx + 1):
                row_j = df_all.iloc[start_idx + j_offset]
                if row_j['low'] / actual_buy_price - 1 <= STOP:
                    sell = actual_buy_price * (1 + STOP)
                    stopped = True
                    break

            ret = (sell / actual_buy_price - 1) * 100
            exit_high = df_all.iloc[start_idx:exit_idx+1]['high'].max()
            exit_low = df_all.iloc[start_idx:exit_idx+1]['low'].min()
            max_gain = (exit_high / actual_buy_price - 1) * 100
            max_dd = (exit_low / actual_buy_price - 1) * 100

            buy_str = actual_buy_date.strftime('%Y-%m-%d') if hasattr(actual_buy_date, 'strftime') else str(actual_buy_date)
            exit_str = exit_row['trade_date'].strftime('%Y-%m-%d') if hasattr(exit_row['trade_date'], 'strftime') else str(exit_row['trade_date'])

            all_trades.append({
                'date': td, 'actual_buy_date': buy_str,
                'exit_date': exit_str,
                'name': pick['name'], 'ts_code': tc,
                'orig_buy': round(orig_buy, 2), 'actual_buy': round(actual_buy_price, 2),
                'sell': round(sell, 2), 'pullback_wait': pullback_wait,
                'return_pct': round(ret, 2), 'max_gain': round(max_gain, 2),
                'max_dd': round(max_dd, 2), 'stopped': stopped,
                'fin_score': pick['fin_score'], 'tech_score': pick['tech_score'],
                'sector_count': pick['sector_count'], 'repeat': pick['repeat'],
                'pe': pick['pe'], 'entry_type': entry_type,
            })

        if (i + 1) % 5 == 0:
            print(f"  {td} | 交易{len(all_trades)}笔 | 跳过突破{skipped_breakout}笔 | 无文件{skipped_no_file}笔")

    # ============================================================
    # 统计
    # ============================================================
    if not all_trades:
        print("无交易"); return

    rdf = pd.DataFrame(all_trades)
    total = len(rdf)
    wins = (rdf['return_pct'] > 0).sum()
    wr = wins / total * 100
    avg_ret = rdf['return_pct'].mean()
    cum = (1 + rdf['return_pct'] / 100).prod() - 1
    avg_w = rdf[rdf['return_pct'] > 0]['return_pct'].mean() if wins else 0
    losses = total - wins
    avg_l = abs(rdf[rdf['return_pct'] <= 0]['return_pct'].mean()) if losses else 1
    plr = avg_w / avg_l if avg_l > 0 else 999

    print(f"\n{'='*80}")
    print(f"V4回测结果（本地通达信日线）")
    print(f"{'='*80}")
    print(f"区间: {dates[0]}~{dates[-1]} | 跳过突破{skipped_breakout}笔 | 无文件{skipped_no_file}笔")
    print(f"交易: {total}笔 | 胜率: {wr:.1f}%")
    print(f"均收: {avg_ret:+.2f}% | 复合累计: {cum*100:+.2f}%")
    print(f"盈亏比: {plr:.2f} (均盈{avg_w:.2f}%/均亏{avg_l:.2f}%)")
    print(f"止损: {rdf['stopped'].sum()}笔({rdf['stopped'].mean()*100:.0f}%)")
    print(f"等回档: {rdf['pullback_wait'].mean():.1f}天")

    print(f"\n📈 回档类型 vs 收益:")
    for et in sorted(rdf['entry_type'].unique()):
        sub = rdf[rdf['entry_type'] == et]
        pb = f"等{sub['pullback_wait'].mean():.0f}天" if sub['pullback_wait'].mean() > 0 else "当天买"
        print(f"  {et}: {len(sub)}笔 均收{sub['return_pct'].mean():+.2f}% 胜率{(sub['return_pct']>0).mean()*100:.0f}% {pb}")

    print(f"\n📈 基本面分 vs 收益:")
    for lo, hi, lb in [(24, 30, '>=24'), (20, 23, '20-23')]:
        sub = rdf[(rdf['fin_score'] >= lo) & (rdf['fin_score'] <= hi)]
        if len(sub) > 0:
            print(f"  {lb}: {len(sub)}笔 均收{sub['return_pct'].mean():+.2f}% 胜率{(sub['return_pct'] > 0).mean()*100:.0f}%")

    print(f"\n🔁 入选次数 vs 收益:")
    for rp in sorted(rdf['repeat'].unique()):
        sub = rdf[rdf['repeat'] == rp]
        print(f"  {rp}次: {len(sub)}笔 均收{sub['return_pct'].mean():+.2f}% 胜率{(sub['return_pct'] > 0).mean()*100:.0f}%")

    print(f"\n🏆 TOP5盈利:")
    for _, r in rdf.nlargest(5, 'return_pct').iterrows():
        pb = f"等{r['pullback_wait']}天" if r['pullback_wait'] > 0 else "当天买"
        print(f"  {r['name']} {r['date']}->{r['exit_date']} {r['return_pct']:+.2f}% "
              f"买{r['actual_buy']:.2f}(原{r['orig_buy']:.2f}) {pb} 基本面={r['fin_score']}")

    print(f"\n💀 TOP5亏损:")
    for _, r in rdf.nsmallest(5, 'return_pct').iterrows():
        st = "⚡" if r['stopped'] else ""
        pb = f"等{r['pullback_wait']}天" if r['pullback_wait'] > 0 else "当天买"
        print(f"  {r['name']} {r['date']}->{r['exit_date']} {r['return_pct']:+.2f}% "
              f"买{r['actual_buy']:.2f} {pb} 基本面={r['fin_score']} PE={r['pe']:.0f} {st}")

    print(f"\n📊 V3 vs V4:")
    print(f"  V3: 胜率57.8% 盈亏比1.66 累计+133.7%")
    print(f"  V4: 胜率{wr:.0f}% 盈亏比{plr:.2f} 累计{cum*100:+.1f}%")

    out = os.path.join(BASE_DIR, 'backtest_v4_local.csv')
    rdf.to_csv(out, index=False, encoding='utf-8-sig')
    print(f"\n💾 {out}")

if __name__ == '__main__':
    main()