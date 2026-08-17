# -*- coding: utf-8 -*-
"""8 月每日绿灯B(v1.3.2 口径) 复盘跟踪：信号日收盘 -> 8/17 累计收益 + 期间峰值"""
import os, sys, time, pandas as pd, tushare as ts

_env_path = r"D:\mystock\config\.env"
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("TUSHARE_TOKEN="):
                os.environ["TUSHARE_TOKEN"] = line.split("=", 1)[1].strip()
ts.set_token(os.environ.get('TUSHARE_TOKEN', ''))
pro = ts.pro_api()

REPORT_DIR = r"D:\mystock\solo\report_daily"
DATES = ['20260806', '20260807', '20260810', '20260811', '20260812', '20260813', '20260814']

def parse_growth(v):
    s = str(v).replace('%', '').replace('+', '').strip()
    try:
        return float(s)
    except Exception:
        return float('nan')

def extract_green_b(df):
    """复现 push_washout_recovery.py v1.3.2 的 green_b 过滤逻辑"""
    if '次日操作' not in df.columns:
        return pd.DataFrame()
    growth = pd.to_numeric(df['中报业绩亮点'].apply(parse_growth), errors='coerce')
    buy_mask = (
        (df['次日操作'] == '✅ 次日可买入') &
        (df['兑现冲击过滤'].astype(str).str.contains('✅', na=False)) &
        (pd.to_numeric(df.get('修正后评分'), errors='coerce').fillna(0) > 0) &
        (df['修正后胜率分级'].isin(['S', 'A', 'B']) | (growth.fillna(-1) > 0)) &
        (df['修正后胜率分级'] != 'E')
    )
    buy = df[buy_mask]
    cond_mask = df['次日操作'].astype(str).isin(['⚠️ 观察', '⚠️ 次日观察等回踩'])
    pullback_days = (pd.to_numeric(df['回踩天数'], errors='coerce').fillna(0)
                     if '回踩天数' in df.columns else pd.Series(0, index=df.index))
    green_b = df[
        cond_mask &
        (pullback_days >= 1) &
        (df['兑现冲击过滤'].astype(str).str.contains('✅', na=False)) &
        (pd.to_numeric(df.get('修正后评分'), errors='coerce').fillna(0) > 0) &
        (df['修正后胜率分级'].isin(['S', 'A', 'B']) & (growth.fillna(-1) > 0)) &
        (~df.index.isin(buy.index))
    ].sort_values('回踩买点分', ascending=False).head(3)
    return green_b

# 收集所有信号
signals = []
for d in DATES:
    path = os.path.join(REPORT_DIR, f'enhanced_timing_bull_all_{d}.csv')
    if not os.path.exists(path):
        continue
    df = pd.read_csv(path, encoding='utf-8-sig')
    gb = extract_green_b(df)
    if gb.empty:
        print(f'{d}: 绿灯B 为空')
        continue
    for _, r in gb.iterrows():
        signals.append({
            '信号日': d, '名称': r['名称'], '代码': r['代码'],
            '评级': r['修正后胜率分级'], '回踩买点分': r['回踩买点分'],
            '洗盘修复分': r['洗盘修复分'], '主题': r.get('主题', ''),
            '现价': r['现价'], 'VWAP': r['VWAP'] if 'VWAP' in df.columns else float('nan'),
            '回踩天数': r['回踩天数'] if '回踩天数' in df.columns else '',
        })
print(f'共 {len(signals)} 笔绿灯B 信号\n')

# 名称->代码缓存
basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name')
name2code = dict(zip(basic['name'], basic['ts_code']))

# 拉行情
rows = []
for s in signals:
    code = s['代码']
    if not isinstance(code, str) or '.' not in code:
        code = name2code.get(s['名称'], None)
    time.sleep(0.15)
    try:
        k = pro.daily(ts_code=code, start_date='20260720', end_date='20260818').sort_values('trade_date')
    except Exception as e:
        print(f"{s['信号日']} {s['名称']}: 拉取失败 {e}")
        continue
    sig = k[k['trade_date'] == s['信号日']]
    if sig.empty:
        print(f"{s['信号日']} {s['名称']}: 信号日无行情")
        continue
    sc = sig['close'].iloc[0]
    after = k[k['trade_date'] > s['信号日']]
    if after.empty:
        print(f"{s['信号日']} {s['名称']}: 无后续行情")
        continue
    lc = after['close'].iloc[-1]; ld = after['trade_date'].iloc[-1]
    peak = after['high'].max()
    rows.append({
        '信号日': s['信号日'], '名称': s['名称'], '评级': s['评级'],
        '回踩买点分': s['回踩买点分'], '主题': s['主题'],
        '信号日收盘': round(sc, 2), '最新日期': ld, '最新收盘': round(lc, 2),
        '累计收益%': round((lc / sc - 1) * 100, 1), '期间峰值%': round((peak / sc - 1) * 100, 1),
    })

out = pd.DataFrame(rows)
out.to_csv(os.path.join(REPORT_DIR, 'egpt_green_b_aug_review.csv'), index=False, encoding='utf-8-sig')
print(out.to_string(index=False))

if len(out):
    pos = (out['累计收益%'] > 0).sum()
    print(f"\n样本 {len(out)} | 胜率 {round(pos/len(out)*100,1)}% | "
          f"累计均值 {round(out['累计收益%'].mean(),1)}% 中位 {round(out['累计收益%'].median(),1)}% | "
          f"峰值均值 {round(out['期间峰值%'].mean(),1)}%")
    # 按日汇总
    g = out.groupby('信号日')['累计收益%'].agg(['mean', 'count', lambda x: (x > 0).sum()])
    g.columns = ['均值%', '笔数', '胜']
    print(g.round(1).to_string())
print('\n已保存: report_daily/egpt_green_b_aug_review.csv')
