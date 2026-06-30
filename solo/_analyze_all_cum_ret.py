# -*- coding: utf-8 -*-
"""分析整个CSV所有信号股，从信号日到今天的累计涨幅，找规律"""
import os, time, pandas as pd
from pytdx.hq import TdxHq_API
from pytdx.config.hosts import hq_hosts

csv_path = r'D:\mystock\solo\trend_feature_output\entry_precision_20260629_224320_qualified.csv'
df = pd.read_csv(csv_path, dtype={'ts_code': str})
print(f'CSV共 {len(df)} 条信号记录, {df["ts_code"].nunique()} 只唯一股票')

# === Tushare 初始化 ===
try:
    import tushare as ts
    from dotenv import load_dotenv
    load_dotenv(r'D:\mystock\config\.env')
    ts.set_token(os.getenv('TUSHARE_TOKEN'))
    pro = ts.pro_api()
    print('Tushare 初始化成功')
except Exception as e:
    print(f'Tushare 初始化失败: {e}')
    pro = None

# === 名称映射 ===
name_map = {}
if pro:
    try:
        sb = pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name')
        for _, row in sb.iterrows():
            n = str(row['name']).strip()
            if n:
                name_map[str(row['ts_code']).strip()] = n
                name_map[str(row['symbol']).zfill(6)] = n
    except:
        pass

# === pytdx 获取今日实时价格 ===
print('\n连接行情服务器获取今日实时价格...')
api = TdxHq_API()
connected = False
for _, ip, port in hq_hosts:
    try:
        api.connect(ip, port)
        if api.get_security_bars(1, 0, '000001', 0, 1) is not None:
            connected = True
            print(f'已连接：{ip}:{port}')
            break
    except:
        continue

today_prices = {}
if connected:
    for ts_code in df['ts_code'].unique():
        ts_code = str(ts_code).strip()
        code6 = ts_code.split('.')[0].zfill(6)
        markets = [0, 1] if (code6[0] == '6' and not code6.startswith('688')) else [1, 0]
        for m in markets:
            try:
                data = api.get_security_quotes([(m, code6)])
                if data and data[0] and data[0].get('price', 0) > 0:
                    today_prices[ts_code] = data[0]['price']
                    break
            except:
                pass
    api.disconnect()
print(f'获取到 {len(today_prices)}/{df["ts_code"].nunique()} 只今日价格')

# === Tushare 获取信号日收盘价 ===
print('\n获取信号日收盘价（按股票分组，减少API调用）...')
sig_close_map = {}  # (ts_code, signal_date_str) -> close
if pro:
    grouped = df.groupby('ts_code')
    cnt = 0
    for ts_code, group in grouped:
        ts_code = str(ts_code).strip()
        min_date = str(int(group['signal_date'].min()))
        try:
            daily = pro.daily(ts_code=ts_code, start_date=min_date)
            if daily is not None and not daily.empty:
                for _, row in group.iterrows():
                    sd = str(int(row['signal_date']))
                    d = daily[daily['trade_date'] == sd]
                    if not d.empty:
                        sig_close_map[(ts_code, sd)] = d.iloc[0]['close']
        except Exception as e:
            pass
        cnt += 1
        if cnt % 30 == 0:
            print(f'  已处理 {cnt}/{len(grouped)} 只...')
        time.sleep(0.06)
    print(f'获取到 {len(sig_close_map)} 条信号日收盘价')
else:
    print('⚠️ Tushare 不可用，无法获取信号日收盘价')

# === 计算累计涨幅 ===
results = []
for _, row in df.iterrows():
    ts_code = str(row['ts_code']).strip()
    sd = str(int(row['signal_date']))
    key = (ts_code, sd)
    if key not in sig_close_map:
        continue
    if ts_code not in today_prices:
        continue
    sig_close = sig_close_map[key]
    today_price = today_prices[ts_code]
    cum_ret = (today_price - sig_close) / sig_close * 100
    results.append({
        'ts_code': ts_code,
        'signal_date': sd,
        'name': name_map.get(ts_code, ''),
        'entry_score': int(row['entry_score']),
        'consecutive_up': int(row['consecutive_up']),
        'pct_chg': float(row['pct_chg']),
        'vol_ratio': float(row['vol_ratio']),
        'rsi6': float(row['rsi6']),
        'return_1d': float(row['return_1d']),
        'return_5d': float(row['return_5d']),
        'return_10d': float(row['return_10d']),
        'sig_close': sig_close,
        'today_price': today_price,
        'cum_ret': cum_ret,
    })

print(f'\n有效样本: {len(results)} 条')

if len(results) == 0:
    print('无有效数据，退出。')
    exit(1)

# === 保存 ===
out_df = pd.DataFrame(results)
out_csv = r'D:\mystock\solo\trend_feature_output\signal_cum_ret_20260630.csv'
out_df.to_csv(out_csv, index=False, encoding='utf-8-sig')
print(f'已保存: {out_csv}')

# === 分析规律 ===
print('\n' + '=' * 70)
print('【按累计涨幅分组的因子对比】')
print('=' * 70)

rdf = pd.DataFrame(results)
# 分档
import numpy as np
rdf['bucket'] = pd.cut(rdf['cum_ret'], bins=[-100, 0, 10, 1000],
                        labels=['亏损', '微涨(0-10%)', '大涨(>10%)'])

for bucket in ['亏损', '微涨(0-10%)', '大涨(>10%)']:
    sub = rdf[rdf['bucket'] == bucket]
    if len(sub) == 0:
        continue
    print(f'\n【{bucket}】共{len(sub)}条')
    print(f'  评分均值: {sub["entry_score"].mean():.1f}  中位数: {sub["entry_score"].median():.0f}')
    print(f'  连涨天数均值: {sub["consecutive_up"].mean():.1f}')
    print(f'  信号日涨幅(pct_chg)均值: {sub["pct_chg"].mean():.2f}%')
    print(f'  量比(vol_ratio)均值: {sub["vol_ratio"].mean():.2f}')
    print(f'  RSI6均值: {sub["rsi6"].mean():.1f}')
    print(f'  return_1d均值: {sub["return_1d"].mean():.2f}%')

# 大涨组特征
print('\n' + '=' * 70)
print('【大涨组(>10%) 核心特征】')
big_win = rdf[rdf['cum_ret'] > 10]
if len(big_win) > 0:
    print(f'样本数: {len(big_win)}')
    print(f'  评分>=80 占比: {(big_win["entry_score"]>=80).sum()/len(big_win)*100:.0f}%')
    print(f'  RSI6<70 占比: {(big_win["rsi6"]<70).sum()/len(big_win)*100:.0f}%')
    print(f'  信号日涨幅<8% 占比: {(big_win["pct_chg"]<8).sum()/len(big_win)*100:.0f}%')
    print(f'  量比>=1.5 占比: {(big_win["vol_ratio"]>=1.5).sum()/len(big_win)*100:.0f}%')
    # 详细列表
    print(f'\n  大涨组明细（按累计涨幅降序）:')
    for _, r in big_win.sort_values('cum_ret', ascending=False).iterrows():
        print(f'    {r["ts_code"]} {r["name"]:8s}  评分{r["entry_score"]:3d}  '
              f'信号日{r["pct_chg"]:.1f}%  RSI{r["rsi6"]:.0f}  累计{r["cum_ret"]:+.1f}%')
else:
    print('（无大涨样本）')

print('\n完成。')
