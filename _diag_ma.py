import os; os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
import sys; sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')
from importlib import machinery
loader = machinery.SourceFileLoader('w2', r'D:\mystock\solo\multi_factor_picker\wave2_pattern_scanner.py')
w2 = loader.load_module()
detector = w2.WavePatternDetector()
df = detector.load_data('603950.SH', 300)

last = df.iloc[-1]
print('Date:', last.trade_date)
print('Close:', last.close)
print('MA20:', last.get('ma_qfq_20', 0))
print('MA60:', last.get('ma_qfq_60', 0))
print('MA120 (calc):', last.get('ma120', 0))
print('MA250 (calc):', last.get('ma250', 0))
print('RSI_6:', last.get('rsi_qfq_6', 0))
ma60 = float(last.ma_qfq_60) if last.ma_qfq_60 > 0 else 0
ma120 = float(last.ma120) if last.ma120 > 0 else 0
ma250 = float(last.ma250) if last.ma250 > 0 else 0
print(f'above_ma60: {last.close > ma60}')
print(f'above_ma120: {last.close > ma120}')
print(f'above_ma250: {last.close > ma250}')
print(f'filter condition: {last.close > ma60 and ma60 > 0 and last.close > ma120 and last.close > ma250}')
