# -*- coding: utf-8 -*-
"""V3 TDX回测 - 历史入选+胜率收益率"""
import os, sys, json, pickle, datetime, warnings
import numpy as np
import tushare as ts

sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')
ts.set_token('bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d')
pro = ts.pro_api()

TDX_PATH  = r"C:\new_tdx\vipdoc"
FIN_CACHE = r'C:\Users\kongx\mystock\fin_cache_v4.pkl'
MA5, MA10, MA20, MA60 = 5, 10, 20, 60

# ===== TDX读取 =====
def parse_tdx_day_file(filepath):
    if not os.path.exists(filepath): return []
    data = []
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(32)
            if not chunk or len(chunk) < 32: break
            date_int = int.from_bytes(chunk[0:4], "little")
            dt = datetime.datetime.strptime(str(date_int), "%Y%m%d")
            data.append({
                "date": dt, "date_int": date_int,
                "open": int.from_bytes(chunk[4:8], "little") / 100,
                "high": int.from_bytes(chunk[8:12], "little") / 100,
                "low":  int.from_bytes(chunk[12:16], "little") / 100,
                "close":int.from_bytes(chunk[16:20], "little") / 100,
                "volume":int.from_bytes(chunk[20:24], "little"),
                "amount":int.from_bytes(chunk[24:28], "little") / 100.0,
            })
    data.sort(key=lambda x: x['date_int'])
    return data

def get_tdx_kline(ts_code, end_date_str, n_days=100):
    code = ts_code.split('.')[0]
    market = ts_code.split('.')[1].lower()
    filepath = os.path.join(TDX_PATH, market, "lday", f"{market}{code}.day")
    raw = parse_tdx_day_file(filepath)
    if not raw: return None
    end_dt = datetime.datetime.strptime(end_date_str, '%Y%m%d')
    filtered = [r for r in raw if r['date'] <= end_dt]
    if len(filtered) < MA60: return None
    return filtered[-n_days:]

def get_tdx_price_at(ts_code, date_str):
    """获取指定日期的收盘价（用于计算未来收益）"""
    code = ts_code.split('.')[0]
    market = ts_code.split('.')[1].lower()
    filepath = os.path.join(TDX_PATH, market, "lday", f"{market}{code}.day")
    raw = parse_tdx_day_file(filepath)
    if not raw: return None
    target_dt = datetime.datetime.strptime(date_str, '%Y%m%d')
    for r in raw:
        if r['date_int'] == int(date_str):
            return r['close']
    return None

# ===== 工具函数 =====
def calc_ma(closes, n):
    if len(closes) < n: return None
    return np.mean(closes[-n:])

def load_fin_cache():
    if os.path.exists(FIN_CACHE):
        try: return pickle.load(open(FIN_CACHE, 'rb'))
        except: pass
    return {}

def tech_score(price, ma5_v, ma10_v, ma20_v, ma60_v, high_21, vol, avg_vol):
    sc = 0
    if ma5_v and ma10_v and ma20_v and price > ma5_v > ma10_v > ma20_v: sc += 10
    if ma20_v and ma60_v and ma20_v > ma60_v: sc += 5
    if vol and avg_vol and vol > avg_vol * 1.5: sc += 10
    elif vol and avg_vol and vol > avg_vol: sc += 5
    if high_21 and price >= high_21 * 0.90: sc += 5
    if ma5_v and abs(price - ma5_v) / ma5_v < 0.03: sc += 5
    return sc

def fin_score_rough(mv_yi, pe):
    """简化基本面评分（不依赖财务数据）"""
    sc = 0
    if 100 <= mv_yi <= 300: sc += 5
    if 0 < pe <= 50: sc += 5
    elif 50 < pe <= 80: sc += 3
    elif 80 < pe <= 100: sc += 1
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

# ===== 获取历史交易日列表 =====
def get_trade_dates(n=250):
    """用上证指数TDX获取最近n个交易日"""
    path = os.path.join(TDX_PATH, "sh", "lday", "sh000001.day")
    raw = parse_tdx_day_file(path)
    dates = [r['date_int'] for r in raw]
    dates.sort()
    return dates[-n:]

# ===== 单日选股（不回测未来） =====
def screen_date(date_str, df_basic, fin_cache):
    """给定日期+当日basic数据，筛选股票"""
    results = []
    for _, row in df_basic.iterrows():
        tc = row['ts_code']
        pe = row.get('pe') or 0
        mv = row.get('total_mv', 0) or 0
        mv_yi = mv / 10000

        if pe <= 0 or pe > 100: continue
        if mv_yi < 100 or mv_yi > 500: continue

        code = tc.split('.')[0]
        market = tc.split('.')[1].lower()
        tdx_file = os.path.join(TDX_PATH, market, "lday", f"{market}{code}.day")
        if not os.path.exists(tdx_file): continue

        kdata = get_tdx_kline(tc, date_str, n_days=100)
        if not kdata or len(kdata) < MA60: continue

        closes = [r['close'] for r in kdata]
        vols   = [r['volume'] for r in kdata]
        price  = closes[-1]
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

        results.append({
            'ts_code': tc, 'price': price,
            'pe': pe, 'mv_yi': mv_yi,
            'tech_score': tsc, 'fin_score': fsc,
            'total': tsc + fsc, 'ma5': ma5_v
        })
    return results

# ===== 回测主程序 =====
def backtest():
    print("=" * 60)
    print("V3 TDX回测 - 历史入选+胜率收益率")
    print("=" * 60)

    # 获取历史交易日（最近120个，每5天选一次）
    all_dates = get_trade_dates(250)
    # 每5天一个检测点
    test_dates = all_dates[-120::5]
    print(f"回测区间: {str(all_dates[-120])[:8]} ~ {str(all_dates[-1])[:8]}")
    print(f"检测点数量: {len(test_dates)} 个")
    print()

    # 加载财务缓存
    fin_cache = load_fin_cache()
    print(f"财务缓存: {len(fin_cache)} 只股票")

    # 缓存daily_basic（不同日期可能不同，只缓存最近的）
    basic_cache = {}
    HOLD_DAYS = [5, 10, 20]  # 持有期

    all_entries = []  # 所有历史入选股
    progress = 0

    for date_int in test_dates:
        date_str = str(date_int)
        progress += 1
        sys.stdout.write(f"\r进度 {progress}/{len(test_dates)} ... 已入选 {len(all_entries)} 只")
        sys.stdout.flush()

        # 获取当日basic
        if date_str not in basic_cache:
            try:
                df = pro.daily_basic(trade_date=date_str, timeout=15)
                if df is not None and len(df) > 0:
                    basic_cache[date_str] = df
                else:
                    basic_cache[date_str] = None
            except:
                basic_cache[date_str] = None

        df_basic = basic_cache.get(date_str)
        if df_basic is None: continue

        hits = screen_date(date_str, df_basic, fin_cache)
        if not hits: continue

        # 计算未来收益
        for h in hits:
            rets = {}
            for hd in HOLD_DAYS:
                # 找hd天后的收盘价
                idx_list = [i for i, d in enumerate(all_dates) if d == date_int]
                if not idx_list: continue
                start_idx = idx_list[0]
                if start_idx + hd < len(all_dates):
                    future_date = all_dates[start_idx + hd]
                    fut_price = get_tdx_price_at(h['ts_code'], str(future_date))
                    if fut_price:
                        rets[f'hold{hd}'] = (fut_price - h['price']) / h['price'] * 100

            all_entries.append({
                'date': date_str,
                'ts_code': h['ts_code'],
                'price': h['price'],
                'pe': h['pe'],
                'mv_yi': h['mv_yi'],
                'tech_score': h['tech_score'],
                'fin_score': h['fin_score'],
                'total': h['total'],
                **rets
            })

    print(f"\n\n===== 回测结果 =====")
    print(f"总入选次数: {len(all_entries)} 只次")
    print(f"总入选股数: {len(set(e['ts_code'] for e in all_entries))} 只")
    print()

    # 统计各持有期
    for hd in HOLD_DAYS:
        col = f'hold{hd}'
        valid = [e for e in all_entries if col in e]
        if not valid: continue
        rets = [e[col] for e in valid]
        wins = sum(1 for r in rets if r > 0)
        win_rate = wins / len(rets) * 100
        avg_ret = np.mean(rets)
        pos_avg = np.mean([r for r in rets if r > 0]) if wins > 0 else 0
        neg_avg = np.mean([r for r in rets if r < 0]) if (len(rets)-wins) > 0 else 0
        max_ret = max(rets)
        min_ret = min(rets)

        print(f"─── 持有{hd}天 (样本{len(valid)}只次) ───")
        print(f"  胜率: {win_rate:.1f}%  ({wins}胜/{len(rets)-wins}负)")
        print(f"  平均收益: {avg_ret:+.2f}%")
        print(f"  上涨均: {pos_avg:+.2f}%  下跌均: {neg_avg:+.2f}%")
        print(f"  最大盈利: {max_ret:+.2f}%  最大亏损: {min_ret:+.2f}%")

    # 按总分分组统计
    print(f"\n─── 按总分分组 ───")
    for min_total in [55, 50, 45]:
        grp = [e for e in all_entries if e['total'] >= min_total and 'hold5' in e]
        if not grp: continue
        rets = [e['hold5'] for e in grp]
        wins = sum(1 for r in rets if r > 0)
        wr = wins/len(rets)*100
        avg = np.mean(rets)
        print(f"  总分≥{min_total}: {len(grp)}只次  胜率{wr:.1f}%  均收益{avg:+.2f}%")

    # 按基本面分组
    print(f"\n─── 按基本面分组 ───")
    for min_fin in [20, 17, 15]:
        grp = [e for e in all_entries if e['fin_score'] >= min_fin and 'hold5' in e]
        if not grp: continue
        rets = [e['hold5'] for e in grp]
        wins = sum(1 for r in rets if r > 0)
        wr = wins/len(rets)*100
        avg = np.mean(rets)
        print(f"  基本面≥{min_fin}: {len(grp)}只次  胜率{wr:.1f}%  均收益{avg:+.2f}%")

    # 保存详细结果
    out_path = r'C:\Users\kongx\mystock\_v3_backtest_detail.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(all_entries, f, ensure_ascii=False, indent=2)
    print(f"\n详细记录已保存: {out_path}")

if __name__ == '__main__':
    backtest()
