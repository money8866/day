import os; os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
import sys; sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')
from importlib import machinery
loader = machinery.SourceFileLoader('w2', r'D:\mystock\solo\multi_factor_picker\wave2_pattern_scanner.py')
w2 = loader.load_module()

# Force date
detector = w2.WavePatternDetector()
df = detector.load_data('603950.SH', 300)
print(f'长源东谷: last date={df.trade_date.iloc[-1]}, RSI={df.rsi_qfq_6.iloc[-1]:.3f}')

# Score the last row
last_row = df.iloc[-1]
prev_row = df.iloc[-2] if len(df) > 1 else None
scorer_result = w2.ResonanceScorer.score(last_row, prev_row, pattern_type='强势横盘', wave1_gain_pct=66.2, new_high_confirmed=True, new_high_pullback=True, is_higher_low=True)
print(f'Total score: {scorer_result["total"]}')
print(f'Details: {scorer_result["details"]}')
