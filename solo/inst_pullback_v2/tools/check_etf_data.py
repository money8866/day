import sys
sys.path.insert(0, r'D:\mystock\solo')
import stock_cache as sc

_prefixes = ('159', '510', '512', '515', '516', '561', '562')
etf_codes = sorted(c for c in sc.get_all_cached_ts_codes() if c[:3] in _prefixes)[:50]
print("ETF codes in DB:")
for code in etf_codes:
    print(code)

print("\n--- Checking specific ETF codes ---")
etf_map = {
    '510300': 'SH', '510170': 'SH', '512480': 'SH', '512660': 'SH',
    '512690': 'SH', '512720': 'SH', '512880': 'SH', '515050': 'SH',
    '515210': 'SH', '515980': 'SH', '516160': 'SH', '516510': 'SH',
    '516520': 'SH', '516970': 'SH', '562500': 'SH', '561910': 'SH',
    '159801': 'SZ', '159611': 'SZ', '159638': 'SZ', '159647': 'SZ',
    '159707': 'SZ', '159732': 'SZ', '159825': 'SZ', '159828': 'SZ',
    '159869': 'SZ',
}
for code, suffix in etf_map.items():
    full = f"{code}.{suffix}"
    df = sc.cached_stk_factor_compat(full, '20260624', '20260724', silent=True)
    status = 'OK' if df is not None and not df.empty else 'FAIL'
    if df is not None and not df.empty:
        print(f"  {full}: {status} ({len(df)} rows, close={df['close'].iloc[-1]:.2f})")
    else:
        print(f"  {full}: {status}")