# -*- coding: utf-8 -*-
import subprocess, json, os

def mcp_call_simple(tool, **kwargs):
    args = " ".join([f'{k}={v}' for k, v in kwargs.items()])
    cmd = f'mcporter call tdx-finance_qclaw.{tool} {args}'
    result = subprocess.run(
        ["powershell", "-Command", cmd],
        capture_output=True,
        text=True,
        timeout=30
    )
    print(f"=== {tool} ===")
    print(f"rc={result.returncode}, stdout_len={len(result.stdout)}, stderr_len={len(result.stderr)}")
    print(f"stdout[:300]={repr(result.stdout[:300])}")
    if result.returncode != 0:
        print(f"stderr[:200]={repr(result.stderr[:200])}")
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"JSON error at pos {e.pos}: {e.msg}")
        print(f"snippet: {repr(result.stdout[max(0,e.pos-50):e.pos+100])}")
        return None

mcp_call_simple("tdx_lookup_stock", query="龙蟠科技", range="AG")
mcp_call_simple("wenda_notice_query", query="龙蟠科技|20260609|20260709||", pageSize="2")
