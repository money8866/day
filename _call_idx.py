# -*- coding: utf-8 -*-
import subprocess, json, urllib.request

r = subprocess.run(
    ["powershell", "-Command", "& 'C:\\Users\\kongx\\.qclaw\\skills\\hithink-mcp\\get-token.ps1'"],
    capture_output=True, text=True, timeout=30
)
token = r.stdout.strip()
headers = {
    "X-Authorization": token,
    "X-Consumer-Id": "qclaw",
    "X-Client-Secret": "1",
    "Content-Type": "application/json"
}

# Test index snapshot
body = {
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
        "name": "get_a_share_index_prices_snapshot",
        "arguments": {"thscodes": "000001.SH,399001.SZ,399006.SZ,000688.SH,000016.SH"}
    },
    "id": 1
}
data = json.dumps(body).encode("utf-8")
req = urllib.request.Request(
    "https://fuyao.aicubes.cn/mcp/a-share-index",
    data=data, headers=headers, method="POST"
)
try:
    with urllib.request.urlopen(req, timeout=20) as resp:
        result = json.loads(resp.read())
        raw = result["result"]["content"][0]["text"]
        data = json.loads(raw)
        print("Index raw:", json.dumps(data, ensure_ascii=False)[:2000])
except Exception as e:
    print(f"Error: {e}")
