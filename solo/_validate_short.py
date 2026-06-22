import sys, os
os.chdir(r'd:\mystock\solo')
sys.path.insert(0, r'd:\mystock\solo')
from tushare_quant import pro, calculate_short_term_win_score

# 测试3只不同的股票
for code in ['688809.SH', '000970.SZ', '600378.SH']:
    r = calculate_short_term_win_score(code, pro)
    print(f"\n【{code}】")
    print(f"  评分: {r['win_score']}")
    print(f"  拆解: {r['breakdown']}")
    print(f"  阶段: {r['stage']} | 信号: {r['signal']}")
    print(f"  风险: {r['key_risk']}")
