import os; os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
import sys; sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')
from importlib import machinery
loader = machinery.SourceFileLoader('w2', r'D:\mystock\solo\multi_factor_picker\wave2_pattern_scanner.py')
w2 = loader.load_module()
detector = w2.WavePatternDetector()
df = detector.load_data('603950.SH', 300)
if df is not None:
    # cache has 186 rows, not enough for 250-day MA.
    # Let's check what's available
    print(f"Total rows: {len(df)}")
    print(f"First date: {df.trade_date.iloc[0]}, last date: {df.trade_date.iloc[-1]}")
    print(f"Close: {df.close.iloc[-1]:.2f}")
    
    # Check stk_factor_pro MA250 field
    if 'ma_qfq_250' in df.columns:
        print(f"MA250 (from stk_pro): {df.ma_qfq_250.iloc[-1]:.2f}")
    if 'ma_qfq_120' in df.columns:
        print(f"MA120 (from stk_pro): {df.ma_qfq_120.iloc[-1]:.2f}")
    
    # Calculate manually
    if len(df) < 250:
        print(f"\nWARNING: Only {len(df)} rows, not enough for MA250 calculation!")
        print("Using min_periods=120, need at least 120 rows")
        df['ma120'] = df['close'].rolling(120, min_periods=60).mean()
        df['ma250'] = df['close'].rolling(250, min_periods=120).mean()
        print(f"Manual MA120: {df.ma120.iloc[-1]:.2f}")
        print(f"Manual MA250: {df.ma250.iloc[-1]:.2f}")
        print(f"Above MA60? {df.close.iloc[-1] > df.ma_qfq_60.iloc[-1]}")
        print(f"Above MA120? {df.close.iloc[-1] > df.ma120.iloc[-1]}")
        print(f"Above MA250? {df.close.iloc[-1] > df.ma250.iloc[-1]}")
        print(f"Filter passes? {df.close.iloc[-1] > df.ma_qfq_60.iloc[-1] and df.close.iloc[-1] > df.ma120.iloc[-1] and df.close.iloc[-1] > df.ma250.iloc[-1]}")
