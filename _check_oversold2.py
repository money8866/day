# -*- coding: utf-8 -*-
import subprocess, json, os

def mcp_raw(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'tdx_mk.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s\n" % (tool, args))
    try:
        result = subprocess.run(
            ['powershell', '-ExecutionPolicy', 'Bypass', '-File', ps1],
            capture_output=True, text=True, timeout=120
        )
        output = result.stdout.strip()
        try: os.remove(ps1)
        except: pass
        idx = output.index('{')
        return json.loads(output[idx:])
    except Exception as e:
        return {'error': str(e)}

def calc_rsi(prices, period):
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

indices = [
    ('000001', '1', '上证指数'),
    ('399001', '0', '深证成指'),
    ('399006', '0', '创业板指'),
    ('000300', '0', '沪深300'),
    ('000688', '1', '科创50'),
]

print('=== 实时行情 ===')
for code, sc, name in indices:
    r = mcp_raw('tdx_quotes', code=code, setcode=sc)
    print(f"\n{name}({code}):")
    if 'error' in r:
        print(f"  Error: {r['error']}")
        continue
    info = r.get('HQInfo', {})
    print(f"  raw keys: {list(r.keys())}")
    if isinstance(info, dict):
        for k, v in info.items():
            print(f"  {k}: {v}")
    elif isinstance(info, list):
        for item in info[:2]:
            for k, v in item.items():
                print(f"  {k}: {v}")

print('\n=== RSI计算（日线20根） ===')
for code, sc, name in indices[:4]:
    r = mcp_raw('tdx_kline', code=code, setcode=sc, period='4', wantNum='20', tqFlag='11')
    if 'error' in r:
        print(f"{name}: {r['error']}")
        continue
    bars = r.get('Data', [])
    if bars:
        closes = [float(b.get('Close', 0)) for b in bars]
        rsi6 = calc_rsi(closes, 6)
        rsi14 = calc_rsi(closes, 14)
        rsi20 = calc_rsi(closes, 20)
        last = bars[-1]
        print(f"{name}: RSI6={rsi6:.1f} RSI14={rsi14:.1f} RSI20={rsi20:.1f} | 最新={closes[-1]:.2f} | {last.get('Date','?')}")
    else:
        print(f"{name}: no data - {str(r)[:100]}")
