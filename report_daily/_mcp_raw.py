# -*- coding: utf-8 -*-
import subprocess, os
OUT = "D:/mystock/report_daily"
def raw(svc, tool, args, name):
    ps = os.path.join(os.environ.get('TEMP','C:/temp'), 'mcp_raw.ps1')
    with open(ps,'w',encoding='utf-8') as f:
        f.write('$env:PYTHONIOENCODING="utf-8"\n')
        f.write('mcporter call %s.%s --args \'%s\'\n' % (svc, tool, __import__('json').dumps(args, ensure_ascii=False)))
    proc = subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',ps],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace', timeout=60)
    raw = proc.stdout
    with open(os.path.join(OUT, name),'w',encoding='utf-8') as f:
        f.write(raw)
    print(name, 'len=', len(raw))
    # 打印前120字符看格式
    print('  HEAD:', repr(raw[:120]))
    try: os.remove(ps)
    except: pass

raw('hithink-finance-a-share','get_a_share_prices_snapshot',
    {"thscodes":"000001.SZ,399001.SZ,399006.SZ,000300.SH,000905.SH,000852.SH"}, 'raw_indices.txt')
