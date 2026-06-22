import sys, os, pandas as pd
os.chdir(r'd:\mystock\solo')
sys.path.insert(0, r'd:\mystock\solo')
from tushare_quant import pro, calculate_short_term_win_score

code = '002747.SZ'
name = '埃斯顿'

# 查6月3日附近的日线收盘价（验证当天涨幅）
df_daily = pro.daily(ts_code=code, start_date='20260601', end_date='20260610')
if df_daily is not None and not df_daily.empty:
    df_daily = df_daily.sort_values('trade_date')
    print('=== 日线收盘 ===')
    for _, row in df_daily.iterrows():
        print(f"  {row['trade_date']} 收={row['close']:.2f} 涨幅={row['pct_chg']:+.2f}%")

# 查 stk_factor_pro 历史数据（取6月3日附近的技术指标）
print('\n=== stk_factor_pro 技术指标 ===')
df_pro = pro.stk_factor_pro(ts_code=code, start_date='20260601')
if df_pro is not None and not df_pro.empty:
    df_pro = df_pro.sort_values('trade_date')
    print(f"共 {len(df_pro)} 条，日期范围: {df_pro['trade_date'].min()} ~ {df_pro['trade_date'].max()}")
    print('\n6月2日~6月5日关键指标:')
    key_dates = ['20260602','20260603','20260604','20260605']
    for _, row in df_pro.iterrows():
        if str(row['trade_date']) in key_dates:
            print(f"\n  {row['trade_date']}:")
            print(f"    close={row['close']:.2f} vol={row['vol']:.0f}")
            print(f"    MA5={row.get('ma_bfq_5','?')} MA10={row.get('ma_bfq_10','?')} MA20={row.get('ma_bfq_20','?')} MA60={row.get('ma_bfq_60','?')}")
            print(f"    MACD={row.get('macd_bfq','?')} DIF={row.get('macd_dif_bfq','?')} DEA={row.get('macd_dea_bfq','?')}")
            print(f"    RSI6={row.get('rsi_bfq_6','?')} RSI12={row.get('rsi_bfq_12','?')} RSI24={row.get('rsi_bfq_24','?')}")
            print(f"    KDJ_K={row.get('kdj_k_bfq','?')} KDJ_D={row.get('kdj_d_bfq','?')} KDJ_J={row.get('kdj_j_bfq','?')}")
            print(f"    BOLL_upper={row.get('boll_upper_bfq','?')} BOLL_mid={row.get('boll_mid_bfq','?')} BOLL_lower={row.get('boll_lower_bfq','?')}")
            print(f"    ATR={row.get('atr_bfq','?')}")
else:
    print('stk_factor_pro 无数据')

# 用6月3日数据计算短线胜率
print('\n=== 埃斯顿 20260603 短线胜率 ===')
# 直接用6月3日的技术指标数据构造输入
if df_pro is not None and not df_pro.empty:
    df_pro = df_pro.sort_values('trade_date')
    # 找6月3日数据
    row = df_pro[df_pro['trade_date'].astype(str) == '20260603']
    if not row.empty:
        r = row.iloc[0]
        print(f"6月3日指标: close={r['close']} MA5={r.get('ma_bfq_5','?')} MA10={r.get('ma_bfq_10','?')} MA20={r.get('ma_bfq_20','?')} RSI6={r.get('rsi_bfq_6','?')}")
        
        # 用算法计算（用历史日期环境）
        result = calculate_short_term_win_score(code, pro)
        print(f"\n当前最新短线胜率(20260618): {result['win_score']} | {result['stage']} | {result['signal']}")
        print(f"拆解: {result['breakdown']}")
        print("\n注: 20260603的历史技术指标需在历史环境计算，以上为当前参考")
