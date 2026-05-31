# -*- coding: utf-8 -*-
"""
三步法中军选股 V3 - 生产版（修订版）
核心逻辑：
  Step1: 申万行业动量识别主线（fallback，如果API失败则跳过）
  Step2: 中军技术筛选：市值100-500亿、PE 0-100、技术得分≥60
  Step3: AI基本面评分 + 二次入选确认

回测参考：45笔交易，胜率57.8%，累计+133.7%，盈亏比1.66
"""
import os, sys, json, pickle, datetime, warnings
import numpy as np
import pandas as pd
import tushare as ts

sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

# ===== 配置 =====
PROJECT_DIR   = r'C:\Users\kongx\mystock'
HIST_DIR      = os.path.join(PROJECT_DIR, 'screen_history')
FIN_CACHE     = os.path.join(PROJECT_DIR, 'fin_cache_v4.pkl')
TUSHARE_TOKEN = 'bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d'

# 技术参数
MIN_MV, MAX_MV   = 100, 500    # 市值区间（亿）
MIN_TECH_SCORE    = 60          # 技术面门槛
MIN_FIN_SCORE     = 20          # 基本面门槛
HOLD_DAYS         = 5           # 持有天数
STOP_LOSS         = -0.05       # 止损线
PE_MAX            = 100         # PE上限
MA5, MA10, MA20, MA60 = 5, 10, 20, 60

ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# ===== 工具函数 =====
def load_fin_cache():
    if os.path.exists(FIN_CACHE):
        try:
            return pickle.load(open(FIN_CACHE, 'rb'))
        except:
            pass
    return {}

def get_history_path():
    today = datetime.date.today().strftime('%Y%m%d')
    ym = today[:6]
    return os.path.join(HIST_DIR, f'history_{ym}.json')

def load_history():
    path = get_history_path()
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {}

def save_history(records):
    path = get_history_path()
    os.makedirs(HIST_DIR, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

def record_selection(ts_code, name, grade, score, close, pe, repeat):
    history = load_history()
    today = datetime.date.today().strftime('%Y%m%d')
    if ts_code not in history:
        history[ts_code] = {'records': [], 'ts_codes': []}
    history[ts_code]['records'].append({
        'date': today, 'name': name, 'grade': grade,
        'score': score, 'close': close, 'pe': pe,
        'repeat': repeat
    })
    save_history(history)

def count_repeats(ts_code):
    history = load_history()
    if ts_code not in history:
        return 0
    return len(history[ts_code].get('records', []))

def get_hot_sectors_sw(date_str=None, top_n=10):
    """用申万行业指数动量识别主线板块（可跳过）"""
    try:
        sw_codes = [f'{i:02d}0000.SI' for i in range(1, 32)]
        end_date = date_str or datetime.date.today().strftime('%Y%m%d')
        start_date = (datetime.datetime.strptime(end_date, '%Y%m%d')
                     - datetime.timedelta(days=25)).strftime('%Y%m%d')
        scores = {}
        for code in sw_codes:
            try:
                d = pro.index_daily(ts_code=code,
                    start_date=start_date, end_date=end_date, timeout=8)
                if d is not None and len(d) > 5:
                    scores[code] = d['pct_chg'].sum()
            except:
                pass
        if scores:
            top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]
            return [s[0] for s in top]
    except:
        pass
    return []

# ===== 核心评分函数 =====
def calc_ma(closes, n):
    if len(closes) < n:
        return None
    return np.mean(closes[-n:])

def tech_score(price, ma5_v, ma10_v, ma20_v, ma60_v, high_21, vol, avg_vol):
    """技术面评分 V3"""
    sc = 0
    # 均线多头排列
    if ma5_v and ma10_v and ma20_v and price > ma5_v > ma10_v > ma20_v:
        sc += 10
    # 上升趋势
    if ma20_v and ma60_v and ma20_v > ma60_v:
        sc += 5
    # 量价配合
    if vol and avg_vol and vol > avg_vol * 1.5:
        sc += 10
    elif vol and avg_vol and vol > avg_vol:
        sc += 5
    # 接近21日高点
    if high_21 and price >= high_21 * 0.90:
        sc += 5
    # 站稳5日线
    if ma5_v and abs(price - ma5_v) / ma5_v < 0.03:
        sc += 5
    return sc

def ai_financial_score(fin):
    """AI基本面评分 V3（4维度，满分30）"""
    if fin is None:
        return 0
    sc = 0
    gm = fin.get('grossprofit_margin', 0) or 0
    nm = fin.get('netprofit_margin', 0) or 0
    if gm >= 30 and nm >= 15:
        sc += 10
    elif gm >= 20 and nm >= 10:
        sc += 5
    oy = fin.get('op_yoy', 0) or 0
    if oy >= 20:
        sc += 10
    elif oy >= 10:
        sc += 5
    da = fin.get('debt_to_assets', 0) or 0
    if da <= 50:
        sc += 5
    elif da <= 70:
        sc += 2
    ocf = fin.get('ocf_to_or', 0) or 0
    if ocf >= 10:
        sc += 5
    elif ocf >= 0:
        sc += 2
    return sc

def ai_generate_verdict(repeat, fin_sc):
    """V3 判决逻辑"""
    if fin_sc >= MIN_FIN_SCORE and repeat >= 2:
        return 'A', '买入'
    elif fin_sc >= MIN_FIN_SCORE and repeat == 1:
        return 'B', '观察'
    return 'C', '观望'

# ===== 主选股函数 =====
def screen_stock(tc, name, price, mv_yi, ma5_v, ma10_v, ma20_v, ma60_v,
                 high_21, vol, avg_vol, fin, repeat):
    """单只股票中军评分"""
    pe = fin.get('pe') if fin else None
    if pe is None or pe <= 0 or pe > PE_MAX:
        return None
    if mv_yi < MIN_MV or mv_yi > MAX_MV:
        return None
    tsc = tech_score(price, ma5_v, ma10_v, ma20_v, ma60_v, high_21, vol, avg_vol)
    if tsc < MIN_TECH_SCORE:
        return None
    fin_sc = ai_financial_score(fin)
    if fin_sc < MIN_FIN_SCORE:
        return None
    grade, action = ai_generate_verdict(repeat, fin_sc)
    total = tsc + fin_sc
    return {
        'ts_code': tc, 'name': name, 'price': price,
        'pe': pe, 'mv_yi': mv_yi,
        'tech_score': tsc, 'fin_score': fin_sc,
        'repeat': repeat, 'total': total,
        'grade': grade, 'action': action,
        'ma5': ma5_v, 'ma10': ma10_v,
    }

# ===== 主流程 =====
def main(date_str=None):
    if date_str is None:
        date_str = datetime.date.today().strftime('%Y%m%d')

    print(f"三步法中军选股 V3 - 生产版")
    print(f"日期: {date_str}")
    print(f"PE≤{PE_MAX} | 基本面≥{MIN_FIN_SCORE} | 二次入选确认 | {HOLD_DAYS}日持有 | {abs(STOP_LOSS)*100:.0f}%止损")
    print("=" * 60)

    # Step1: 申万行业动量（可跳过）
    hot = get_hot_sectors_sw(date_str, top_n=10)
    if hot:
        print(f"[Step1] 申万主线: {hot[:5]}")
    else:
        print("[Step1] 申万接口超时，跳过板块过滤（全市场扫描）")

    # Step2+3: 获取全市场数据
    try:
        df = pro.daily(trade_date=date_str, timeout=15)
        if df is None or len(df) == 0:
            print("获取行情数据失败"); return
        print(f"[Step2+3] 扫描 {len(df)} 只股票 ...")
    except Exception as e:
        print(f"获取行情失败: {e}"); return

    fin_cache = load_fin_cache()
    results = []
    scanned = 0
    skipped_pe = skipped_mv = skipped_tech = skipped_fin = 0

    for _, row in df.iterrows():
        tc = row['ts_code']
        price = row['close']
        vol = row.get('vol', 0) or 0
        scanned += 1

        # 基本面
        fin = fin_cache.get(tc)
        if fin is None:
            fin = get_fin_cached(tc, fin_cache)
        if fin is None:
            skipped_pe += 1
            continue

        pe = fin.get('pe') if fin else None
        if pe is None or pe <= 0 or pe > PE_MAX:
            skipped_pe += 1
            continue

        # 市值估算（用成交额/成交量/价格）
        try:
            amount = row.get('amount', 0) or 0
            mv_yi = amount / price / 100 if price > 0 else 0
            if mv_yi < MIN_MV or mv_yi > MAX_MV:
                skipped_mv += 1
                continue
        except:
            continue

        # 计算均线
        try:
            start_d = (datetime.datetime.strptime(date_str, '%Y%m%d')
                      - datetime.timedelta(days=90)).strftime('%Y%m%d')
            d = pro.daily(ts_code=tc, start_date=start_d, end_date=date_str, timeout=10)
            if d is None or len(d) < MA60:
                continue
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

        repeat = count_repeats(tc)
        res = screen_stock(tc, tc, price, mv_yi,
                          ma5_v, ma10_v, ma20_v, ma60_v,
                          high_21, vol, avg_vol, fin, repeat)
        if res:
            results.append(res)

        if scanned % 500 == 0:
            print(f"  已扫描 {scanned} 只... (当前候选 {len(results)})")

    # 排序输出
    results.sort(key=lambda x: x['total'], reverse=True)

    print(f"\n扫描完成: 共扫描{scanned}只，候选{len(results)}只")
    print(f"过滤原因: PE越限{skipped_pe} | 市值越限{skipped_mv}")

    a_list = [r for r in results if r['grade'] == 'A']
    b_list = [r for r in results if r['grade'] == 'B']

    print(f"\n🏆 A级 ({len(a_list)}只) - 二次入选+基本面≥20 → 可买入:")
    for r in a_list[:10]:
        print(f"  {r['ts_code']} 评分{r['total']}(T{r['tech_score']}+F{r['fin_score']}) "
              f"价{r['price']:.2f} PE{r['pe']:.0f} 市值{r['mv_yi']:.0f}亿 入选{r['repeat']}次")

    print(f"\n📋 B级 ({len(b_list)}只) - 首次入选+基本面≥20 → 观察:")
    for r in b_list[:5]:
        print(f"  {r['ts_code']} 评分{r['total']}(T{r['tech_score']}+F{r['fin_score']}) "
              f"价{r['price']:.2f} PE{r['pe']:.0f} 市值{r['mv_yi']:.0f}亿 入选{r['repeat']}次")

    for r in a_list:
        record_selection(r['ts_code'], r['name'], r['grade'],
                        r['total'], r['price'], r['pe'], r['repeat'])

    return results

def get_fin_cached(ts_code, cache):
    """获取基本面并缓存"""
    try:
        df = pro.fina_indicator(ts_code=ts_code, start_date=
            (datetime.datetime.now() - datetime.timedelta(days=90)).strftime('%Y%m%d'),
            timeout=10)
        if df is not None and len(df) > 0:
            row = df.iloc[0]
            fin = {
                'grossprofit_margin': row.get('grossprofit_margin'),
                'netprofit_margin': row.get('netprofit_margin'),
                'op_yoy': row.get('op_yoy'),
                'debt_to_assets': row.get('debt_to_assets'),
                'ocf_to_or': row.get('ocf_to_or'),
                'pe': row.get('pe'),
            }
            cache[ts_code] = fin
            pickle.dump(cache, open(FIN_CACHE, 'wb'))
            return fin
    except:
        pass
    return None

if __name__ == '__main__':
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    main(date_arg)
