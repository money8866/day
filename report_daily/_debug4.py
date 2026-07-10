# -*- coding: utf-8 -*-
"""诊断公告原始数据结构"""
import subprocess, json, os

def ps_run(cmd, timeout=30):
    r = subprocess.run(["powershell", "-Command", cmd], capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, r.stdout

def mcp_call(tool, **kwargs):
    args_parts = []
    for k, v in kwargs.items():
        v_str = str(v)
        args_parts.append(f'{k}=\'{v_str}\'')
    args_line = " ".join(args_parts)
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), "tdx_debug4.ps1")
    with open(ps1, "w", encoding="utf-8") as f:
        f.write(f'mcporter call tdx-finance_qclaw.{tool} {args_line}; exit $LASTEXITCODE\n')
    rc, out = ps_run(f'& "{ps1}"')
    try:
        os.remove(ps1)
    except:
        pass
    if rc != 0 or not out:
        return None
    try:
        return json.loads(out.strip())
    except:
        return None

import datetime
end = datetime.date.today()
start = end - datetime.timedelta(days=30)
resp = mcp_call("wenda_notice_query", query=f"龙蟠科技|{start.strftime('%Y%m%d')}|{end.strftime('%Y%m%d')}|", pageSize="3")

if resp and resp.get("data"):
    print("总字段数:", len(resp["data"][1]))
    for i, item in enumerate(resp["data"][1:4], 1):
        print(f"\n=== 公告 {i} ===")
        for j, field in enumerate(item):
            val = str(field)[:80] if field else "(empty)"
            print(f"  [{j}] {val}")
