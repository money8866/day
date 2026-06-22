import sys
import os
sys.path.insert(0, 'd:/mystock/solo')

from short_term_analyzer import *

# 手动获取两天的数据进行对比
ts_code = '002747.SZ'

# 获取因子数据
cache_file = os.path.join(CACHE_DIR, f'stk_pro_{ts_code}_{TRADE_DATE}.csv')
df = pd.read_csv(cache_file)
df['trade_date'] = df['trade_date'].astype(str)

# 筛选关键日期
dates = ['20260602', '20260603']
for date in dates:
    mask = df['trade_date'] == date
    if mask.any():
        row = df[mask].iloc[0]
        print(f'\n=== {date} ===')
        print(f'close: {row.get("close", 0):.2f}')
        print(f'open: {row.get("open", 0):.2f}')
        print(f'low: {row.get("low", 0):.2f}')
        print(f'ma10: {row.get("ma_bfq_10", 0):.2f}')
        print(f'ma20: {row.get("ma_bfq_20", 0):.2f}')
        print(f'ma60: {row.get("ma_bfq_60", 0):.2f}')
        print(f'boll_mid: {row.get("boll_mid_bfq", 0):.2f}')
        print(f'rsi_6: {row.get("rsi_bfq_6", 0):.2f}')
        print(f'dif: {row.get("macd_dif_bfq", 0):.4f}')
        print(f'kdj_j: {row.get("kdj_bfq", 0):.2f}')
        print(f'vol: {row.get("vol", 0):.0f}')
        
        # 计算二波评分的各个条件
        print(f'\n--- 二波条件判断 ---')
        
        # 1. 强股基因
        ma20 = float(row.get('ma_bfq_20', 0) or 0)
        ma60 = float(row.get('ma_bfq_60', 0) or 0)
        dif = float(row.get('macd_dif_bfq', 0) or 0)
        condition1 = ma20 > ma60 and dif > 0
        print(f'条件1(强股基因): ma20({ma20:.2f})>ma60({ma60:.2f})={ma20>ma60}, dif({dif:.4f})>0={dif>0} => {condition1}')
        
        # 2. 调整健康
        close = float(row.get('close', 0) or 0)
        ma10 = float(row.get('ma_bfq_10', 0) or 0)
        boll_mid = float(row.get('boll_mid_bfq', 0) or 0)
        rsi_6 = float(row.get('rsi_bfq_6', 50) or 50)
        condition2 = (boll_mid <= close <= ma10) and (45 <= rsi_6 <= 60)
        print(f'条件2(调整健康): boll_mid({boll_mid:.2f})<=close({close:.2f})<=ma10({ma10:.2f})={boll_mid<=close<=ma10}, rsi_6({rsi_6:.2f})在45-60之间={45<=rsi_6<=60} => {condition2}')
        
        # 3. 反转信号
        open_price = float(row.get('open', 0) or 0)
        kdj_j = float(row.get('kdj_bfq', 50) or 50)
        condition3 = close > open_price
        print(f'条件3(反转信号): close({close:.2f})>open({open_price:.2f})={close>open_price}, kdj_j={kdj_j:.2f}')
        
        # 4. 量价配合
        low = float(row.get('low', 0) or 0)
        volume = float(row.get('vol', 0) or 0)
        low_to_ma20 = abs(low - ma20) / ma20 * 100 if ma20 > 0 else 100
        print(f'条件4(量价配合): low({low:.2f})与ma20距离={low_to_ma20:.2f}%, vol={volume:.0f}')

# 调用检测函数
print('\n=== 二波检测函数结果 ===')
result_0602 = detect_wave2_reversal(ts_code, pro, '20260602')
result_0603 = detect_wave2_reversal(ts_code, pro, '20260603')
print(f'20260602: 总分={result_0602["wave2_score"]}, 信号={result_0602["signal"]}')
print(f'          强股基因={result_0602["breakdown"]["strong_stock"]}, 调整健康={result_0602["breakdown"]["healthy_adjust"]}')
print(f'          反转信号={result_0602["breakdown"]["reversal_signal"]}, 量价配合={result_0602["breakdown"]["volume_price"]}')
print(f'20260603: 总分={result_0603["wave2_score"]}, 信号={result_0603["signal"]}')
print(f'          强股基因={result_0603["breakdown"]["strong_stock"]}, 调整健康={result_0603["breakdown"]["healthy_adjust"]}')
print(f'          反转信号={result_0603["breakdown"]["reversal_signal"]}, 量价配合={result_0603["breakdown"]["volume_price"]}')
