import os; os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
import sys; sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')
from importlib import machinery
loader = machinery.SourceFileLoader('w2', r'D:\mystock\solo\multi_factor_picker\wave2_pattern_scanner.py')
w2 = loader.load_module()
detector = w2.WavePatternDetector()
df = detector.load_data('603950.SH', 300)

print('Shape:', df.shape)
print('Columns with ma:', [c for c in df.columns if 'ma' in c])
print('Last row available cols:', list(df.columns))
last = df.iloc[-1]
print('close:', last['close'])
if 'ma120' in df.columns:
    print('ma120:', last['ma120'])
else:
    print('ma120 column not found!')
if 'ma250' in df.columns:
    print('ma250:', last['ma250'])
else:
    print('ma250 column not found!')
