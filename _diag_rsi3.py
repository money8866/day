import os; os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
import sys; sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')
from importlib import machinery
loader = machinery.SourceFileLoader('w2', r'D:\mystock\solo\multi_factor_picker\wave2_pattern_scanner.py')
w2 = loader.load_module()
detector = w2.WavePatternDetector()
df = detector.load_data('603950.SH', 300)
if df is None or len(df) < 60:
    print("load_data failed")
    sys.exit(1)

# Calculate MA as detection methods do
df['ma120'] = df['close'].rolling(120, min_periods=60).mean()
df['ma250'] = df['close'].rolling(250, min_periods=120).mean()

# Test sideways detection
result = detector.detect_sideways_pattern('603950.SH', today_only=False)
if result:
    print(f"=== 强势横盘结果 ===")
    print(f"Score: {result.get('score')}")
    print(f"RSI: {result.get('rsi')}")
    print(f"Entry date: {result.get('entry_date')}")
    print(f"Details: {result.get('score_details')}")
else:
    print("No sideways pattern")
    # Try all patterns
    for pat_name, pat_func in [
        ('强势横盘', detector.detect_sideways_pattern),
        ('深度回调', detector.detect_deep_pullback_pattern),
        ('放量回调', detector.detect_volume_pullback_pattern),
        ('V型急跌', detector.detect_vshape_pattern),
    ]:
        r = pat_func('603950.SH', today_only=False)
        if r:
            print(f"\n=== {pat_name} ===")
            print(f"Score: {r.get('score')}")
            print(f"RSI: {r.get('rsi')}")
            print(f"Entry date: {r.get('entry_date')}")
            print(f"Details: {r.get('score_details')}")
