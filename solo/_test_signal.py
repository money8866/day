import sys, os
os.chdir(r'd:\mystock\solo')
sys.path.insert(0, r'd:\mystock\solo')
from tushare_quant import pro, calculate_short_term_win_score

for code, name in [('300842.SZ','阿石创'), ('002747.SZ','埃斯顿'), ('000725.SZ','京东方A')]:
    r = calculate_short_term_win_score(code, pro)
    print(f"\n【{name} {code}】")
    print(f"  总分: {r['win_score']} | {r['stage']} | {r['signal']}")
    print(f"  形态: {r['pattern_type']}")
    print(f"  均线: {r['ma_structure']}")
    print(f"  RSI: {r['rsi_signal']}")
    print(f"  MACD: {r['macd_signal']}")
    print(f"  KDJ: {r['kdj_signal']}")
    print(f"  成交量: {r['volume_signal']}")
    print(f"  共振: {r['signal_resonance']}")
    print(f"  风险: {r['key_risk']}")
