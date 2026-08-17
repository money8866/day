# -*- coding: utf-8 -*-
"""绿灯B(条件信号) 8/11-8/14 跟踪涨幅复盘
信号日收盘 -> 最新收盘 累计收益 + 期间峰值
"""
import os
import time
import pandas as pd
import tushare as ts

# 加载 token（与 eld 一致）
_env_path = r"D:\mystock\config\.env"
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("TUSHARE_TOKEN="):
                os.environ["TUSHARE_TOKEN"] = line.split("=", 1)[1].strip()

TOKEN = os.environ.get('TUSHARE_TOKEN', '')
ts.set_token(TOKEN)
pro = ts.pro_api()

# 绿灯B 各日 top3 (v1.3.0 验证输出)
SIGNALS = {
    '20260811': ['菲利华', '铜冠铜箔', '贝达药业'],
    '20260812': ['佛塑科技', '兄弟科技', '联芸科技'],
    '20260813': ['梅雁吉祥', '联芸科技', '建新股份'],
    '20260814': ['华勤技术', '盈新发展', '茶花股份'],
}

def get_code(name):
    df = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name')
    m = df[df['name'] == name]
    if len(m) == 0:
        m = df[df['name'].str.contains(name, na=False)]
    return m['ts_code'].iloc[0] if len(m) > 0 else None

def get_daily(ts_code, start, end):
    # 120ms 限速
    time.sleep(0.15)
    df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
    return df.sort_values('trade_date')

rows = []
for sig_date, names in SIGNALS.items():
    for name in names:
        code = get_code(name)
        if code is None:
            rows.append({'信号日': sig_date, '名称': name, '代码': '未找到', '最新收盘': None})
            continue
        df = get_daily(code, '20260725', '20260818')
        if df.empty:
            rows.append({'信号日': sig_date, '名称': name, '代码': code, '最新收盘': None})
            continue
        sig_row = df[df['trade_date'] == sig_date]
        if sig_row.empty:
            rows.append({'信号日': sig_date, '名称': name, '代码': code, '最新收盘': None})
            continue
        sig_close = sig_row['close'].iloc[0]
        after = df[df['trade_date'] > sig_date]
        if after.empty:
            rows.append({'信号日': sig_date, '名称': name, '代码': code, '最新收盘': None})
            continue
        last_close = after['close'].iloc[-1]
        last_date = after['trade_date'].iloc[-1]
        peak = after['high'].max()
        cum_ret = (last_close / sig_close - 1) * 100
        peak_ret = (peak / sig_close - 1) * 100
        rows.append({
            '信号日': sig_date, '名称': name, '代码': code,
            '信号日收盘': round(sig_close, 2), '最新日期': last_date,
            '最新收盘': round(last_close, 2), '累计收益%': round(cum_ret, 1),
            '期间峰值%': round(peak_ret, 1),
        })

out = pd.DataFrame(rows)
print(out.to_string(index=False))
out.to_csv('report_daily/egpt_green_b_review.csv', index=False, encoding='utf-8-sig')
print('\n已保存: report_daily/egpt_green_b_review.csv')

# 汇总统计
ok = out[out['累计收益%'].notna()].copy()
if len(ok):
    print(f"\n有效样本 {len(ok)} 笔 | 胜率 {round((ok['累计收益%']>0).mean()*100,1)}% | "
          f"累计收益均值 {round(ok['累计收益%'].mean(),1)}% 中位 {round(ok['累计收益%'].median(),1)}% | "
          f"峰值均值 {round(ok['期间峰值%'].mean(),1)}%")
