import sys, os
os.chdir(r'd:\mystock\solo')
sys.path.insert(0, r'd:\mystock\solo')
from tushare_quant import pro, calculate_short_term_win_score

stocks = {
    '京东方A': '000725.SZ',
    '阿石创': '300842.SZ',
    '埃斯顿': '002747.SZ',
}

for name, code in stocks.items():
    r = calculate_short_term_win_score(code, pro)
    print(f'\n========== {name} ({code}) ==========')
    print(f"  总分: {r['win_score']} | 阶段: {r['stage']} | 信号: {r['signal']}")
    print(f"  拆解: {r['breakdown']}")
    print(f"  风险: {r['key_risk']}")
