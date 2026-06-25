import os; os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
import sys; sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')
from importlib import machinery
loader = machinery.SourceFileLoader('w2', r'D:\mystock\solo\multi_factor_picker\wave2_pattern_scanner.py')
w2 = loader.load_module()

# Force date to 20260624
detector = w2.WavePatternDetector(force_date='20260624')
df = detector.load_data('603950.SH', 300)
if df is not None:
    print('=== Last 3 rows ===')
    print(df[['trade_date','rsi_qfq_6','close','close_qfq']].tail(3).to_string())
    print(f'\nShape: {df.shape}, last trade_date: {df.trade_date.iloc[-1]}')
    
    # Now test the scorer
    entry_idx = len(df) - 1
    row = df.iloc[entry_idx]
    rsi = float(row.get('rsi_qfq_6', 50))
    print(f'\n=== Scorer RSI check ===')
    print(f'entry_idx={entry_idx}, RSI={rsi}')
    
    # Check if daily supplement happened
    print(f'\n=== Check daily supplement ===')
    from datetime import datetime
    from dateutil import parser
    print(f'daily supplement check...')
