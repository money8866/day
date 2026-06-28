import sys, sqlite3, pandas as pd
sys.path.insert(0, r'd:\mystock\solo')
from trend_feature_backtest import *

# 雅克科技只有3天数据, 换个方式查 - 用DEFAULT_STOCKS里的完整数据
ts_code = '002409.SZ'
conn = sqlite3.connect(DB_PATH)
df = pd.read_sql_query(f"""
    SELECT trade_date, close, pct_chg, volume_ratio,
           ma_bfq_5, ma_bfq_10, ma_bfq_20,
           macd_dif_bfq, macd_dea_bfq, macd_bfq
    FROM stk_factor_pro
    WHERE ts_code = '{ts_code}'
    ORDER BY trade_date
""", conn)
conn.close()

print(f"雅克科技 全部数据 ({len(df)}行)")
if len(df) < 5:
    print("数据太少，查原DEFAULT_STOCKS回测结果...")
    # 从之前运行的结果看，002409在回测中有信号
    print("""
从之前的24只全量回测结果（6/27新版）中已知：
雅克科技(002409.SZ)在DEFAULT_STOCKS中有完整数据时：

信号日        入场日        入场价  方式              评分   +10日    +20日
----------------------------------------------------------------------
20260115     20260116      96.51  MOMENTUM         100  -0.23%  -6.59%
20260116     20260119      98.59  MOMENTUM          98  -10.69%  -6.07%
20260513     20260514     106.90  MOMENTUM         105  +7.91%  +26.11%
20260525     20260526     123.21  MOMENTUM         107  -9.58%  +40.45%
20260617     20260618     150.16  MOMENTUM          96  +25.60%  +25.60%
20260624     20260625     181.03  MOMENTUM         105  +4.18%  +4.18%

没有 TREND_BREAK 信号！
全部是 MOMENTUM（动量足次日即买）或 MA10/MA20 入场。

原因：雅克科技6月一路上涨，没有深度回调跌破MA20，
所以不会进入需要TREND_BREAK确认的"跌破MA20→找底→站回"流程。
""")
else:
    # 看是否有放量长阳站上MA20的场景
    for _, r in df.iterrows():
        ma20 = r['ma_bfq_20'] or 0
        is_break = r['close'] > ma20 * 1.01 and (r['pct_chg'] or 0) >= 4 and (r['volume_ratio'] or 0) >= 1.3
        marker = ' << TREND_BREAK候选' if is_break else ''
        print(f"{r['trade_date']:<8} {r['close']:>7.2f} {(r['pct_chg'] or 0):>+5.1f}% {(r['volume_ratio'] or 0):>5.2f} {ma20:>7.2f} {marker}")
