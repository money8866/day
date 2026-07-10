# -*- coding: utf-8 -*-
import subprocess, json, os, datetime, re

def ps_run(cmd, timeout=30):
    r = subprocess.run(["powershell", "-Command", cmd], capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, r.stdout

def mcp_call(tool, **kwargs):
    args_parts = [f'{k}=\'{v}\'' for k, v in kwargs.items()]
    args_line = " ".join(args_parts)
    ps1 = os.path.join(os.environ.get("TEMP", "C:\\temp"), "tdx_d7.ps1")
    with open(ps1, "w", encoding="utf-8") as f:
        f.write(f"mcporter call tdx-finance_qclaw.{tool} {args_line}; exit $LASTEXITCODE\n")
    rc, out = ps_run(f"& \"{ps1}\"")
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

end = datetime.date.today()
start = end - datetime.timedelta(days=90)

# 方式1: 直接用代码搜索
resp1 = mcp_call("wenda_notice_query", query=f"603906|{start.strftime('%Y%m%d')}|{end.strftime('%Y%m%d')}|", pageSize="3")
print(f"方式1(代码): 总={len((resp1 or {}).get('data', []))-1}")
for item in (resp1 or {}).get("data", [])[1:4]:
    if isinstance(item, list):
        print(f"  {str(item[0])[:60]}")

# 方式2: 用上交所前缀
resp2 = mcp_call("wenda_notice_query", query=f"603906|{start.strftime('%Y%m%d')}|{end.strftime('%Y%m%d')}|上海证券", pageSize="3")
print(f"方式2(代码+上海): 总={len((resp2 or {}).get('data', []))-1}")

# 方式3: 用完整的上交所公告链接关键词
resp3 = mcp_call("wenda_notice_query", query=f"龙蟠科技集团|{start.strftime('%Y%m%d')}|{end.strftime('%Y%m%d')}|", pageSize="5")
print(f"方式3(龙蟠科技集团): 总={len((resp3 or {}).get('data', []))-1}")
for item in (resp3 or {}).get("data", [])[1:4]:
    if isinstance(item, list):
        print(f"  {str(item[0])[:60]}")
