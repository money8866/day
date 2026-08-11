import sys
sys.path.insert(0, r'd:\mystock\solo')
import stock_cache as sc
pro = sc._get_pro()
df = pro.stk_factor_pro(trade_date='20260811', fields='ts_code')
print('Tushare 当日已算行数:', 0 if df is None or df.empty else len(df))
