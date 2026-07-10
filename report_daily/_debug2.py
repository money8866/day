# -*- coding: utf-8 -*-
import subprocess, json

def ps_run(cmd):
    r = subprocess.run(
        ["powershell", "-Command", cmd],
        capture_output=True,
        encoding="utf-8", errors="replace",
        timeout=30
    )
    return r.returncode, r.stdout

def mcp_call(tool, **kwargs):
    args_str = " ".join([f'{k}={v}' for k, v in kwargs.items()])
    cmd = f'mcporter call tdx-finance_qclaw.{tool} {args_str}'
    print(f"  CMD: {cmd[:100]}")
    rc, out = ps_run(cmd)
    print(f"  RC={rc}, out_len={len(out)}")
    if rc != 0 or not out:
        return None
    try:
        return json.loads(out.strip())
    except json.JSONDecodeError as e:
        print(f"  JSON error: {e}")
        return None

r = mcp_call("wenda_notice_query", query='龙蟠科技|20260609|20260709||', pageSize='2')
print(f"Result: {r is not None}, ok={r.get('ok') if r else None}")
if r:
    data = r.get('data', [])
    print(f"data len={len(data)}")
