import sys; sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')
from importlib import machinery
loader = machinery.SourceFileLoader('w2', r'D:\mystock\solo\multi_factor_picker\wave2_pattern_scanner.py')
w2 = loader.load_module()
import os
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
detector = w2.WavePatternDetector()
df = detector.load_data('603950.SH', 300)
if df is not None:
    print('Last 5 rows RSI:')
    print(df[['trade_date','rsi_qfq_6','close','close_qfq']].tail(5).to_string())
    print(f'\nShape: {df.shape}')
else:
    print('None')
