# -*- coding: utf-8 -*-
"""V3 TDX回测 v2 - 批量缓存+高效回测"""
import os, sys, json, pickle, datetime, warnings
import numpy as np
import tushare as ts
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')
ts.set_token('bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d')
pro = ts.pro_api()

TDX_PATH  = r"C:\new_tdx\vipdoc"
FIN_CACHE = r'C:\Users\kongx\mystock\fin_cache_v4.pkl'
MA5, MA10, MA20, MA60 = 5, 10, 20, 60

# ===== TDX批量读取 =====
def parse_tdx_day_file(filepath):
    if not os.path.exists(filepath): return []
    data = []
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(32)
            if not chunk or len(chunk) < 32: break
            date_int = int.from_bytes(chunk[0:4], "little")
            data.append({
                "date_int": date_int,
                "close": int.from_bytes(chunk[16:20], "little") / 100,
                "volume": int.from_bytes(chunk[20:24], "little"),
            })
    data.sort(key=lambda x: x['date_int'])
    return {r['date_int']: r for r in data}  # dict for O(1) lookup

def calc_ma(closes_list, n):
    if len(closes_list) < n: return None
    return np.mean(closes_list[-n:])

def tech_score(price, ma5_v, ma10_v, ma20_v, ma60_v, high_21, vol, avg_vol):
    sc = 0
    if ma5_v and ma10_v and ma20_v and price > ma5_v > ma10_v > ma20_v: sc += 10
    if ma20_v and ma60_v and ma20_v > ma60_v: sc += 5
    if vol and avg_vol and vol > avg_vol * 1.5: sc += 10
    elif vol and avg_vol and vol > avg_vol: sc += 5
    if high_21 and price >= high_21 * 0.90: sc += 5
    if ma5_v and abs(price - ma5_v) / ma5_v < 0.03: sc += 5
    return sc

def full_fin_score(tc, fin_cache):
    fin = fin_cache.get(tc)
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

def get_tdx_kline_from_cache(kdata_dict, end_date_int, n_days=100):
    """从缓存字典取数据（end_date_int之前的n_days条）"""
    dates_sorted = sorted(kdata_dict.keys())
    filtered = [d for d in dates_sorted if d <= end_date_int]
    if len(filtered) < MA60: return None
    recent = filtered[-n_days:]
    closes = [kdata_dict[d]['close'] for d in recent]
    vols = [kdata_dict[d]['volume'] for d in recent]
    return closes, vols

def get_future_price(kdata_dict, start_date_int, offset):
    """从缓存中找 start_date_int + offset 天后的收盘价"""
    dates_sorted = sorted(kdata_dict.keys())
    try:
        idx = dates_sorted.index(start_date_int)
        if idx + offset >= len(dates_sorted): return None
        return kdata_dict[dates_sorted[idx + offset]]['close']
    except (ValueError, IndexError): return None

def get_trade_dates(n=250):
    path = os.path.join(TDX_PATH, "sh", "lday", "sh000001.day")
    raw = []
    with open(path, "rb") as f:
        while True:
            chunk = f.read(32)
            if not chunk or len(chunk) < 32: break
            raw.append(int.from_bytes(chunk[0:4], "little"))
    raw.sort()
    return raw[-n:]

def load_fin_cache():
    if os.path.exists(FIN_CACHE):
        try: return pickle.load(open(FIN_CACHE, 'rb'))
        except: pass
    return {}

def main():
    print("=" * 60)
    print("V3 TDX回测 v2 - 批量缓存")
    print("=" * 60)

    all_dates = get_trade_dates(250)
    test_dates = all_dates[-120::5]
    print(f"回测区间: {str(all_dates[-120])[:8]} ~ {str(all_dates[-1])[:8]}")
    print(f"检测点: {len(test_dates)} 个  持有期: 5/10/20天")
    print()

    # Step1: 获取所有检测点日期的daily_basic（去重调用）
    unique_bdays = list(set(test_dates))
    basic_cache = {}
    print(f"[1/3] 获取daily_basic ({len(unique_bdays)}个日期)...")
    for i, d in enumerate(unique_bdays):
        ds = str(d)
        sys.stdout.write(f"\r  {i+1}/{len(unique_bdays)}")
        sys.stdout.flush()
        try:
            df = pro.daily_basic(trade_date=ds, timeout=15)
            if df is not None and len(df) > 0:
                basic_cache[d] = df[df['pe'] > 0]
        except: pass
    print(f"\n  成功获取: {len(basic_cache)} 个日期")

    # Step2: 收集所有需要的股票代码
    print("[2/3] 收集候选股票池...")
    needed = set()
    for d in test_dates:
        df = basic_cache.get(d)
        if df is None: continue
        sub = df[(df['pe'] > 0) & (df['pe'] <= 100) &
                 (df['total_mv']/10000 >= 100) & (df['total_mv']/10000 <= 500)]
        for tc in sub['ts_code']:
            code = tc.split('.')[0]
            market = tc.split('.')[1].lower()
            tdx_file = os.path.join(TDX_PATH, market, "lday", f"{market}{code}.day")
            if os.path.exists(tdx_file): needed.add(tc)
    print(f"  候选股票池: {len(needed)} 只")

    # Step3: 批量读取TDX数据
    print(f"[3/3] 批量读取TDX数据 ({len(needed)}只)...")
    tdx_cache = {}
    done = 0
    for tc in list(needed):
        code = tc.split('.')[0]
        market = tc.split('.')[1].lower()
        tdx_file = os.path.join(TDX_PATH, market, "lday", f"{market}{code}.day")
        kdata = parse_tdx_day_file(tdx_file)
        if kdata: tdx_cache[tc] = kdata
        done += 1
        if done % 200 == 0:
            sys.stdout.write(f"\r  已读 {done}/{len(needed)}")
            sys.stdout.flush()
    print(f"\n  有效TDX数据: {len(tdx_cache)} 只")

    # 加载财务缓存
    fin_cache = load_fin_cache()
    print(f"  财务缓存: {len(fin_cache)} 只")

    # ===== 回测 =====
    HOLD = [5, 10, 20]
    entries = []
    print("\n回测运行中...")
    for i, date_int in enumerate(test_dates):
        sys.stdout.write(f"\r  [{i+1}/{len(test_dates)}] 已入选 {len(entries)}")
        sys.stdout.flush()

        df = basic_cache.get(date_int)
        if df is None: continue

        df = df[(df['pe'] > 0) & (df['pe'] <= 100) &
                (df['total_mv']/10000 >= 100) & (df['total_mv']/10000 <= 500)]

        for _, row in df.iterrows():
            tc = row['ts_code']
            kdata = tdx_cache.get(tc)
            if not kdata: continue

            kline = get_tdx_kline_from_cache(kdata, date_int, n_days=100)
            if not kline: continue
            closes, vols = kline
            price = closes[-1]
            ma5_v  = calc_ma(closes, MA5)
            ma10_v = calc_ma(closes, MA10)
            ma20_v = calc_ma(closes, MA20)
            ma60_v = calc_ma(closes, MA60)
            high_21 = max(closes[-21:]) if len(closes) >= 21 else price
            avg_vol = np.mean(vols[-20:]) if len(vols) >= 20 else 0
            vol = vols[-1]

            tsc = tech_score(price, ma5_v, ma10_v, ma20_v, ma60_v, high_21, vol, avg_vol)
            if tsc < 30: continue
            fsc = full_fin_score(tc, fin_cache)
            if fsc < 15: continue

            rec = {
                'date': str(date_int), 'ts_code': tc, 'price': price,
                'pe': row['pe'], 'mv_yi': row['total_mv']/10000,
                'tech': tsc, 'fin': fsc, 'total': tsc+fsc
            }
            for hd in HOLD:
                fp = get_future_price(kdata, date_int, hd)
                if fp: rec[f'hd{hd}'] = (fp - price) / price * 100
            entries.append(rec)

    print(f"\n\n{'='*60}")
    print(f"回测结果  ({str(test_dates[0])[:8]}~{str(test_dates[-1])[:8]})")
    print(f"{'='*60}")
    print(f"总入选: {len(entries)}只次  不同股票: {len(set(e['ts_code'] for e in entries))}只")
    print()

    for hd in HOLD:
        col = f'hd{hd}'
        valid = [e for e in entries if col in e]
        if not valid: continue
        rets = [e[col] for e in valid]
        wins = sum(1 for r in rets if r > 0)
        wr = wins/len(rets)*100
        avg = np.mean(rets)
        pos_avg = np.mean([r for r in rets if r > 0]) if wins else 0
        neg_avg = np.mean([r for r in rets if r < 0]) if len(rets)-wins else 0
        print(f"── 持有{hd}天 ({len(valid)}样本) ──")
        print(f"  胜率 {wr:.1f}%  ({wins}胜/{len(rets)-wins}负)  "
              f"均收益{avg:+.2f}%  涨均{pos_avg:+.2f}%  跌均{neg_avg:+.2f}%")

    print(f"\n── 按总分分组 (持有5天) ──")
    for mt in [55, 50, 45, 40]:
        g = [e for e in entries if e['total']>=mt and 'hd5' in e]
        if not g: continue
        rets=[e['hd5'] for e in g]; wr=sum(1for r in rets if r>0)/len(rets)*100
        print(f"  总分≥{mt}: {len(g)}次  胜率{wr:.1f}%  均{np.mean(rets):+.2f}%")

    print(f"\n── 按基本面分组 (持有5天) ──")
    for mf in [22, 20, 17, 15]:
        g=[e for e in entries if e['fin']>=mf and 'hd5' in e]
        if not g: continue
        rets=[e['hd5'] for e in g]; wr=sum(1for r in rets if r>0)/len(rets)*100
        print(f"  基本面≥{mf}: {len(g)}次  胜率{wr:.1f}%  均{np.mean(rets):+.2f}%")

    out = r'C:\Users\kongx\mystock\_v3_backtest_detail.json'
    with open(out,'w',encoding='utf-8') as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    print(f"\n详细已存: {out}")

if __name__ == '__main__':
    main()
