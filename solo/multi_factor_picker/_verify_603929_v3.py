# -*- coding: utf-8 -*-
"""用pytdx获取6/24实时行情验证"""
import os, sys, time
sys.path.insert(0, r'D:\mystock')
if 'TUSHARE_TOKEN' not in os.environ:
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
            break

from pymupdf import open as fitz_open
import pandas as pd

# 1. 从PDF提取信号股
PDF_PATH = r'D:\mystock\solo\multi_factor_picker\output\wave2_pattern_bull_stocks_20260624.pdf'
doc = fitz_open(PDF_PATH)
all_rows = []
for page in doc:
    tables = page.find_tables()
    for table in tables.tables:
        data = table.extract()
        if data and len(data[0]) > 5:
            for row in data[1:]:
                all_rows.append(row)
doc.close()
print(f'信号股总数: {len(all_rows)}')

# 2. 用pytdx获取实时行情
from pytdx.hq import TdxHq_API

api = TdxHq_API()
servers = [
    ('218.6.170.47', 7709),
    ('123.125.108.14', 7709),
    ('180.153.18.170', 7709),
]

connected = False
for host, port in servers:
    try:
        if api.connect(host, port):
            connected = True
            print(f'连接: {host}:{port}')
            break
    except:
        continue

if not connected:
    print('pytdx连接失败')
    sys.exit(1)

results = []
for i, row in enumerate(all_rows):
    ts_code = row[0].strip()
    try:
        score = float(row[3]) if row[3] else 0
    except:
        score = 0
    pattern = row[2]
    try:
        wave1_gain = float(row[4].replace('+','')) if row[4] else 0
    except:
        wave1_gain = 0
    try:
        pullback_pct = float(row[5]) if row[5] else 0
    except:
        pullback_pct = 0
    
    # pytdx market参数
    market = 0 if ts_code.startswith('6') else 1
    code_num = ts_code[:6]
    
    try:
        df_quote = api.to_df(api.get_security_bars(8, market, code_num, 0, 1))  # 8=日线
        if df_quote is not None and len(df_quote) > 0:
            r = df_quote.iloc[0]
            close = r['close']
            pct = r.get('price', close)  # 有些字段名不同
            # 计算涨跌幅
            pre_close = r.get('open', close)  # 近似
            
            # 涨停判断
            if ts_code.startswith('68') or ts_code.startswith('30'):
                is_limit = r.get('pct_chg', 0) >= 19.9
            else:
                is_limit = r.get('pct_chg', 0) >= 9.9
            
            results.append({
                'ts_code': ts_code, 'score': score, 'pattern': pattern,
                'wave1_gain': wave1_gain, 'pullback_pct': pullback_pct,
                'close': close, 'pct_chg': r.get('pct_chg', 0),
                'is_limit': is_limit,
            })
    except Exception as e:
        pass
    
    time.sleep(0.05)

api.disconnect()

# 3. 用Tushare获取6/24日线（如果已更新）
import tushare as ts
ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

results2 = []
for i, row in enumerate(all_rows):
    ts_code = row[0].strip()
    try:
        score = float(row[3]) if row[3] else 0
    except:
        score = 0
    pattern = row[2]
    try:
        wave1_gain = float(row[4].replace('+','')) if row[4] else 0
    except:
        wave1_gain = 0
    try:
        pullback_pct = float(row[5]) if row[5] else 0
    except:
        pullback_pct = 0
    
    try:
        df_t = pro.daily(ts_code=ts_code, start_date='20260624', end_date='20260624')
        if len(df_t) > 0:
            r = df_t.iloc[0]
            pct = r['pct_chg']
            if ts_code.startswith('68') or ts_code.startswith('30'):
                is_limit = pct >= 19.9
            else:
                is_limit = pct >= 9.9
            results2.append({
                'ts_code': ts_code, 'score': score, 'pattern': pattern,
                'wave1_gain': wave1_gain, 'pullback_pct': pullback_pct,
                'close': r['close'], 'pct_chg': pct, 'is_limit': is_limit,
            })
        time.sleep(0.06)
    except:
        pass

print(f'pytdx获取: {len(results)}只, Tushare获取: {len(results2)}只')

# 用Tushare数据（更准确）
df_r = pd.DataFrame(results2) if results2 else pd.DataFrame(results)
if df_r.empty:
    print('无6/24行情数据，用pytdx的last bar')
    df_r = pd.DataFrame(results)

if df_r.empty:
    print('无数据')
    sys.exit(1)

print(f'\n=== 全量验证：82只信号股 6/24表现 ===')

# 按共振评分分档
print(f'\n--- 按共振评分分档 ---')
for label, lo, hi in [('[5-12]低评分', 5, 12), ('[13-17]中评分', 13, 17), ('[18-23]高评分', 18, 99)]:
    sub = df_r[(df_r['score'] >= lo) & (df_r['score'] <= hi)]
    if len(sub) > 0:
        lc = int(sub['is_limit'].sum())
        avg = sub['pct_chg'].mean()
        up = (sub['pct_chg'] > 0).sum()
        print(f'{label}: {len(sub)}只 | 均涨{avg:+.2f}% | 上涨{up}只({up/len(sub)*100:.0f}%) | 涨停{lc}只({lc/len(sub)*100:.1f}%)')

# 涨停股
limit_s = df_r[df_r['is_limit']]
if len(limit_s) > 0:
    print(f'\n--- 涨停股 ---')
    for _, r in limit_s.sort_values('score', ascending=False).iterrows():
        print(f"  {r['ts_code']} 评分{r['score']:.0f} {r['pattern']} 一波+{r['wave1_gain']:.1f}% 涨{r['pct_chg']:+.2f}%")

# 相关性
corr_s = df_r['score'].corr(df_r['pct_chg']) if len(df_r) > 2 else 0
corr_w = df_r['wave1_gain'].corr(df_r['pct_chg']) if len(df_r) > 2 else 0
print(f'\n共振评分-涨幅相关: {corr_s:.3f}')
print(f'一波涨幅-涨幅相关: {corr_w:.3f}')

# 按一波涨幅分档
print(f'\n--- 按一波涨幅分档 ---')
for label, lo, hi in [('[20-30]小一波', 20, 30), ('[30-50]中一波', 30, 50), ('[50+]大一波', 50, 999)]:
    sub = df_r[(df_r['wave1_gain'] >= lo) & (df_r['wave1_gain'] < hi)]
    if len(sub) > 0:
        lc = int(sub['is_limit'].sum())
        avg = sub['pct_chg'].mean()
        print(f'{label}: {len(sub)}只 | 均涨{avg:+.2f}% | 涨停{lc}只({lc/len(sub)*100:.1f}%)')

# 亚翔集成单独分析
print(f'\n=== 603929 亚翔集成 重点分析 ===')
yaxx = df_r[df_r['ts_code'] == '603929.SH']
if len(yaxx) > 0:
    r = yaxx.iloc[0]
    print(f"评分: {r['score']:.0f} | 一波涨幅: +{r['wave1_gain']:.1f}% | 调整: {r['pullback_pct']:.1f}% | 6/24涨: {r['pct_chg']:+.2f}%")
    print(f'共振评分仅12分但涨停，关键原因：')
    
    # 看一波涨幅是否特别大
    same_wave1 = df_r[df_r['wave1_gain'] >= r['wave1_gain']]
    print(f'  一波涨幅≥{r["wave1_gain"]:.1f}%的信号股: {len(same_wave1)}只')
    if len(same_wave1) > 0:
        print(f'  这些股6/24均涨: {same_wave1["pct_chg"].mean():+.2f}%')
