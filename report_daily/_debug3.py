# -*- coding: utf-8 -*-
import subprocess, json, os

def ps_run(cmd, timeout=30):
    r = subprocess.run(
        ["powershell", "-Command", cmd],
        capture_output=True, encoding="utf-8", errors="replace", timeout=timeout
    )
    return r.returncode, r.stdout

def mcp_call(tool, **kwargs):
    args_parts = []
    for k, v in kwargs.items():
        v_str = str(v)
        args_parts.append(f'{k}=\'{v_str}\'')
    args_line = " ".join(args_parts)
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), "tdx_debug.ps1")
    with open(ps1, "w", encoding="utf-8") as f:
        f.write(f'mcporter call tdx-finance_qclaw.{tool} {args_line}; exit $LASTEXITCODE\n')
    rc, out = ps_run(f'& "{ps1}"')
    try:
        os.remove(ps1)
    except:
        pass
    print(f"  RC={rc}, out_len={len(out)}")
    if rc != 0 or not out:
        return None
    try:
        return json.loads(out.strip())
    except Exception as e:
        print(f"  JSON error: {e}")
        return None

r = mcp_call("wenda_notice_query", query='龙蟠科技|20260609|20260709||', pageSize='2')
print(f"Result: ok={r.get('ok') if r else None}")
if r:
    print(f"data len={len(r.get('data', []))}")
