# -*- coding: utf-8 -*-
import subprocess, json, os

def mcp(tool, **params):
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
        return str(e)

# 四大指数最新行情
indices = [
    ('000001', '1', '上证指数'),
    ('399001', '0', '深证成指'),
    ('399006', '0', '创业板指'),
    ('000300', '0', '沪深300'),
    ('000688', '1', '科创50'),
]

print('=== 四大指数最新行情 ===')
for code, sc, name in indices:
    r = mcp('tdx_quotes', code=code, setcode=sc)
    if isinstance(r, dict):
        info = r.get('HQInfo', [{}])[0] if r.get('HQInfo') else {}
        print(f"{name}: 现价={info.get('ZuiXinJia','?')} 涨幅={info.get('ZhangDieFu','?')}% 振幅={info.get('ZhenFu','?')}% 量比={info.get('HuanShouLU','?')}")
    else:
        print(f"{name}: {str(r)[:100]}")

# RSI数据 - 取最近20根日K
print('\n=== RSI指标（日线） ===')
for code, sc, name in indices[:4]:
    r = mcp('tdx_kline', code=code, setcode=sc, period='4', wantNum='20', tqFlag='11')
    if isinstance(r, dict):
        bars = r.get('Data', [])
        if bars:
            closes = [float(b.get('Close', 0)) for b in bars]
            rsi6 = calc_rsi(closes, 6)
            rsi14 = calc_rsi(closes, 14)
            rsi = calc_rsi(closes, 20)
            last = bars[-1]
            print(f"{name}: RSI6={rsi6:.1f} RSI14={rsi14:.1f} RSI20={rsi:.1f} | 最新={closes[-1]:.2f} 日期={last.get('Date','?')}")
    else:
        print(f"{name} K线: {str(r)[:80]}")

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
