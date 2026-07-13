# -*- coding: utf-8 -*-
import subprocess, json, os, datetime, sys
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import FancyArrowPatch
import numpy as np

sys.stdout.reconfigure(encoding='utf-8')

SKILL_DIR = r'C:\Users\kongx\.qclaw\skills\tongdaxin-mcp'

def ps_run(cmd, timeout=30):
    r = subprocess.run(['powershell', '-Command', cmd], capture_output=True, encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, r.stdout

rc_tok, out_tok = ps_run('& "' + SKILL_DIR + '\\get-token.ps1"')
token = out_tok.strip() if rc_tok == 0 else None
rc_c, out_c = ps_run('mcporter config get tdx-finance_qclaw 2>$null')
if not (rc_c == 0 and token and token in out_c):
    ps_run('mcporter config remove tdx-finance_qclaw 2>$null')
    ps_run('mcporter config add tdx-finance_qclaw "https://txmcp.tdx.com.cn:3001/qclawmcp" --header "Authorization=Bearer %s" --header "Accept=application/json, text/event-stream" --transport http --scope home' % token)

def mcp(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'c2k.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s\n" % (tool, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=120)
    try: os.remove(ps1)
    except: pass
    if rc != 0: return None
    try: return json.loads(out.strip())
    except: return None

print("拉取中证2000 K线...")
# 先用 tdx_kline
resp = mcp('tdx_kline', code='932000', setcode='1', period='4', wantNum='100', tqFlag='11')

if not resp:
    resp = mcp('tdx_kline', code='932000', setcode='0', period='4', wantNum='100', tqFlag='11')

if resp:
    rows = resp.get('Rows', [])
    print(f"  MCP返回 {len(rows)} 条")
    data = []
    for row in rows:
        d = row.get('Data', '')
        if len(d) < 29: continue
        date = d[:8]
        try:
            open_p = float(d[9:16])
            close_p = float(d[16:23])
            high_p = float(d[23:30])
            low_p = float(d[30:37])
            vol = int(d[37:50]) if d[37:50].strip() else 0
            if close_p > 0 and 1000 < close_p < 100000:
                data.append({
                    'date': date,
                    'open': open_p, 'high': high_p,
                    'low': low_p, 'close': close_p,
                    'vol': vol
                })
        except:
            continue
    
    if data:
        df = pd.DataFrame(data)
        df = df.sort_values('date').reset_index(drop=True)
        print(f"  有效数据: {len(df)} 条 {df['date'].iloc[0]}~{df['date'].iloc[-1]}")
        print(f"  最新: {df.iloc[-1]['date']} 收{df.iloc[-1]['close']:.2f}")
        
        # 保存供后续分析
        df.to_csv(r'D:\mystock\report_daily\_csi2000_kline.csv', index=False)
        print("  已保存 CSV")
    else:
        print("  无有效数据")
else:
    print("  MCP 失败，使用 Tushare fallback")
    import tushare as ts
    os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
    pro = ts.pro_api()
    today = datetime.date.today().strftime('%Y%m%d')
    df = pro.index_daily(ts_code='932000.CSI', start_date='20260501', end_date=today)
    df = df.sort_values('trade_date').reset_index(drop=True)
    df = df.rename(columns={'trade_date': 'date', 'vol': 'vol'})
    df['close'] = df['close'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['open'] = df['open'].astype(float)
    df.to_csv(r'D:\mystock\report_daily\_csi2000_kline.csv', index=False)
    print(f"  Tushare: {len(df)} 条 {df['date'].iloc[0]}~{df['date'].iloc[-1]}")
