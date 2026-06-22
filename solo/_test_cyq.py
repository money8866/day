"""
测试 cyq_chips / cyq_perf 接口（筹码分布）
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tushare_quant import pro, TRADE_DATE

ts_code = '000970.SZ'
test_date = '20260618'  # 用最近交易日测试

print(f"=== 测试 cyq_chips（{ts_code} / {test_date}） ===")
try:
    df_chips = pro.cyq_chips(ts_code=ts_code, trade_date=test_date)
    print(f"返回 {len(df_chips)} 行")
    if df_chips is not None and len(df_chips) > 0:
        print(df_chips.head(10).to_string())
        print(f"\n列名: {df_chips.columns.tolist()}")
        # 找最大密集峰
        top5 = df_chips.nlargest(5, 'percent')
        print(f"\n前5大筹码密集峰:")
        print(top5[['price','percent']].to_string())
    else:
        print("返回空表")
except Exception as e:
    print(f"ERROR: {e}")

print(f"\n=== 测试 cyq_perf（{ts_code} / {test_date}） ===")
try:
    df_perf = pro.cyq_perf(ts_code=ts_code, trade_date=test_date)
    print(f"返回 {len(df_perf)} 行")
    if df_perf is not None and len(df_perf) > 0:
        print(df_perf.head(3).to_string())
        print(f"\n列名: {df_perf.columns.tolist()}")
    else:
        print("返回空表")
except Exception as e:
    print(f"ERROR: {e}")
