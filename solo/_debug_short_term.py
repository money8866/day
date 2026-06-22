import sys, os
os.chdir(r'd:\mystock\solo')
sys.path.insert(0, r'd:\mystock\solo')

# 导入主程序的pro对象（已初始化token）
from tushare_quant import pro, calculate_short_term_win_score

result = calculate_short_term_win_score('688809.SH', pro)
print('=== 算法输出 ===')
for k, v in result.items():
    print(f'{k}: {v}')

# 再打印详细因子
df = pro.stk_factor(ts_code='688809.SH')
if df is not None and not df.empty:
    latest = df.iloc[0]
    print('\n=== 原始因子 ===')
    print(f'MA5={latest.get("ma5",0):.2f} MA10={latest.get("ma10",0):.2f} MA20={latest.get("ma20",0):.2f} MA60={latest.get("ma60",0):.2f}')
    print(f'close={latest.get("close",0):.2f} MACD={latest.get("macd",0):.4f} DIF={latest.get("dif",0):.4f} DEA={latest.get("dea",0):.4f}')
    print(f'RSI6={latest.get("rsi_6",0):.1f} RSI12={latest.get("rsi_12",0):.1f} RSI24={latest.get("rsi_24",0):.1f}')
    print(f'KDJ_K={latest.get("kdj_k",0):.1f} KDJ_D={latest.get("kdj_d",0):.1f} KDJ_J={latest.get("kdj_j",0):.1f}')
    print(f'BOLL: upper={latest.get("boll_upper",0):.2f} mid={latest.get("boll_mid",0):.2f} lower={latest.get("boll_lower",0):.2f}')
    print(f'ATR={latest.get("atr",0):.4f}')
    print(f'日期: {latest.get("trade_date","?")}')
else:
    print('stk_factor无数据')
