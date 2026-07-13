# -*- coding: utf-8 -*-
import os, sys, datetime
import pandas as pd
import numpy as np
import tushare as ts

sys.stdout.reconfigure(encoding='utf-8')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
pro = ts.pro_api()

today = datetime.date.today().strftime('%Y%m%d')
yesterday = (datetime.date.today() - datetime.timedelta(days=1)).strftime('%Y%m%d')

print("=== 今日市场情绪分析 ===")
print("时间: %s 10:36\n" % today)

# ── 1. 今日涨跌停统计 ──
print("--- 涨跌停统计 ---")
try:
    # 沪市涨跌停
    zt_sh = pro.limit_list_hs(trade_date=today, ex='SH', field='trade_date,ts_code,name,close,pct_chg,amp,reason')
    zt_sz = pro.limit_list_hs(trade_date=today, ex='SZ', field='trade_date,ts_code,name,close,pct_chg,amp,reason')
    zt_total = pd.concat([zt_sh, zt_sz], ignore_index=True) if zt_sh is not None and len(zt_sh) > 0 else pd.DataFrame()
    
    zt_df = zt_total[zt_total['pct_chg'] >= 9.9] if len(zt_total) > 0 else pd.DataFrame()
    dt_df = zt_total[zt_total['pct_chg'] <= -9.9] if len(zt_total) > 0 else pd.DataFrame()
    
    print("  涨停: %d 家" % len(zt_df))
    print("  跌停: %d 家" % len(dt_df))
    
    if len(zt_df) > 0:
        print("  涨停股 TOP5:")
        top_zt = zt_df.nlargest(5, 'pct_chg')
        for _, r in top_zt.iterrows():
            print("    +%.1f%%  %s(%s)  原因:%s" % (r['pct_chg'], r['name'], r['ts_code'], r.get('reason','')[:20]))
    
    if len(dt_df) > 0:
        print("  跌停股 TOP5:")
        top_dt = dt_df.nsmallest(5, 'pct_chg')
        for _, r in top_dt.iterrows():
            print("    %.1f%%  %s(%s)  原因:%s" % (r['pct_chg'], r['name'], r['ts_code'], r.get('reason','')[:20]))
except Exception as e:
    print(" 涨跌停接口: %s" % e)

# ── 2. 今日指数行情 ──
print("\n--- 今日指数行情 ---")
idx_codes = [
    ('000001.SH', '上证指数'),
    ('399001.SZ', '深证成指'),
    ('399006.SZ', '创业板指'),
    ('399300.SZ', '沪深300'),
    ('932000.CSI', '中证2000'),
    ('000852.SH', '中证1000'),
]

idx_data = []
for code, name in idx_codes:
    try:
        df_d = pro.index_daily(ts_code=code, trade_date=today)
        df_y = pro.index_daily(ts_code=code, trade_date=yesterday)
        if df_d is not None and len(df_d) > 0:
            today_row = df_d.iloc[0]
            if df_y is not None and len(df_y) > 0:
                yes_row = df_y.iloc[0]
                pct = (float(today_row['close']) - float(yes_row['close'])) / float(yes_row['close']) * 100
            else:
                pct = float(today_row.get('pct_chg', 0))
            idx_data.append({
                'name': name,
                'close': float(today_row['close']),
                'pct': pct,
                'high': float(today_row.get('high', today_row['close'])),
                'low': float(today_row.get('low', today_row['close'])),
                'vol': float(today_row.get('vol', 0)),
                'amount': float(today_row.get('amount', 0)),
            })
            pct_str = '+%.2f' % pct if pct >= 0 else '%.2f' % pct
            print("  %s: %.2f  %s%%  高=%.2f 低=%.2f" % (name, today_row['close'], pct_str, idx_data[-1]['high'], idx_data[-1]['low']))
    except Exception as e:
        print("  %s: 获取失败 - %s" % (name, e))

# ── 3. 近期趋势 ──
print("\n--- 指数趋势(近20日MA) ---")
for code, name in idx_codes:
    try:
        start = (datetime.date.today() - datetime.timedelta(days=40)).strftime('%Y%m%d')
        df_h = pro.index_daily(ts_code=code, start_date=start, end_date=today)
        if df_h is not None and len(df_h) >= 10:
            df_h = df_h.sort_values('trade_date').reset_index(drop=True)
            df_h['close'] = df_h['close'].astype(float)
            ma5 = df_h['close'].tail(5).mean()
            ma10 = df_h['close'].tail(10).mean()
            ma20 = df_h['close'].tail(20).mean() if len(df_h) >= 20 else ma10
            last = df_h.iloc[-1]['close']
            
            trend = '多头' if last > ma5 > ma10 > ma20 else ('空头' if last < ma5 < ma10 < ma20 else '震荡')
            
            # 5日涨跌
            pct5 = (df_h.iloc[-1]['close'] - df_h.iloc[-6]['close']) / df_h.iloc[-6]['close'] * 100 if len(df_h) >= 6 else 0
            pct10 = (df_h.iloc[-1]['close'] - df_h.iloc[-11]['close']) / df_h.iloc[-11]['close'] * 100 if len(df_h) >= 11 else 0
            
            print("  %s: 收=%.2f  MA5=%.2f MA10=%.2f MA20=%.2f  5日%+.1f%% 10日%+.1f%%  [%s]" % (
                name, last, ma5, ma10, ma20, pct5, pct10, trend))
    except Exception as e:
        print("  %s: 趋势失败 - %s" % (name, e))

# ── 4. 市场宽度 ──
print("\n--- 市场宽度(上涨/下跌家数) ---")
try:
    # 用涨跌停比代表市场宽度
    zt_cnt = len(zt_df) if 'zt_df' in dir() and len(zt_df) > 0 else 0
    dt_cnt = len(dt_df) if 'dt_df' in dir() and len(dt_df) > 0 else 0
    width_score = zt_cnt - dt_cnt
    if zt_cnt + dt_cnt > 0:
        zt_ratio = zt_cnt / (zt_cnt + dt_cnt) * 100
        print("  涨停 %d / 跌停 %d = %.0f%%  宽度分=%+d" % (zt_cnt, dt_cnt, zt_ratio, width_score))
    else:
        print("  今日涨跌停数据暂未更新")
except Exception as e:
    print("  %s" % e)

# ── 5. 情绪综合评分 ──
print("\n--- 情绪综合评分 ---")
try:
    # 基于指数涨跌 + 涨跌停比 + 趋势计算情绪分
    if len(idx_data) >= 4:
        # 主要看创业板和中小盘
        csi2k = next((d for d in idx_data if '2000' in d['name']), None)
        cyb = next((d for d in idx_data if '创业' in d['name']), None)
        sh = next((d for d in idx_data if '上证' in d['name']), None)
        
        score = 50  # 基准
        
        if sh and sh['pct'] > 0: score += 5
        if sh and sh['pct'] < 0: score -= 5
        
        if csi2k:
            if csi2k['pct'] > 0: score += 5
            if csi2k['pct'] < 0: score -= 8
            if csi2k['pct'] < -2: score -= 10
        
        if cyb:
            if cyb['pct'] > 1: score += 5
            if cyb['pct'] < 0: score -= 5
        
        if zt_cnt > 50: score += 10
        if zt_cnt > 100: score += 10
        if zt_cnt < 30: score -= 10
        if dt_cnt > 30: score -= 10
        if dt_cnt > 50: score -= 15
        
        score = max(0, min(100, score))
        
        if score >= 70: label = '多头情绪'
        elif score >= 55: label = '偏多'
        elif score >= 45: label = '中性'
        elif score >= 30: label = '偏空'
        else: label = '空头情绪'
        
        print("  综合情绪分: %d/100  [%s]" % (score, label))
        
        # 详细分解
        parts = []
        if sh: parts.append('上证%+d' % sh['pct'])
        if csi2k: parts.append('中证2000%+d' % csi2k['pct'])
        if cyb: parts.append('创业板%+d' % cyb['pct'])
        parts.append('涨停%d' % zt_cnt)
        parts.append('跌停%d' % dt_cnt)
        print("  构成: %s" % '  |  '.join(parts))
except Exception as e:
    print("  情绪评分失败: %s" % e)

print("\n完成")
