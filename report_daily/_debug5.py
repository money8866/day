# -*- coding: utf-8 -*-
import subprocess, json, os, datetime, re

def ps_run(cmd, timeout=30):
    r = subprocess.run(["powershell", "-Command", cmd], capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, r.stdout

def mcp_call(tool, **kwargs):
    args_parts = []
    for k, v in kwargs.items():
        v_str = str(v)
        args_parts.append(f'{k}=\'{v_str}\'')
    args_line = " ".join(args_parts)
    ps1 = os.path.join(os.environ.get("TEMP", "C:\\temp"), "tdx_debug5.ps1")
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
start = end - datetime.timedelta(days=7)
resp = mcp_call("wenda_notice_query", query=f"龙蟠科技|{start.strftime('%Y%m%d')}|{end.strftime('%Y%m%d')}|", pageSize="30")
data = resp.get("data", [])
print(f"总条目数: {len(data)-1}")
for item in data[1:16]:
    if not isinstance(item, list) or len(item) < 5:
        continue
    title = (item[0] or "")[:60]
    content = (item[4] or "")[:80]
    codes = re.findall(r"证券代码[：:]\s*(\d{6})", title + content)
    in_title = "龙蟠科技" in title
    print(f"  codes={codes} in_title={in_title} | title={title}")
