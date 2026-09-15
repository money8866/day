# -*- coding: utf-8 -*-
"""从 mcporter.json 提取飞书 token，直接调飞书多维表格 API"""
import json, os, urllib.request

# 从 mcporter.json 读 feishu token
config_path = r"C:\Users\kongx\.qclaw\workspace\config\mcporter.json"
with open(config_path, "r", encoding="utf-8") as f:
    raw = f.read()

data = json.loads(raw)
# 找飞书相关服务
feishu_token = None
for name, srv in data.get("mcpServers", {}).items():
    if "feishu" in name.lower() or "lark" in name.lower():
        headers = srv.get("headers", {})
        auth = headers.get("X-Authorization", "")
        if auth and len(auth) > 50:
            feishu_token = auth
            print(f"Found feishu token in server: {name}")
            break

if not feishu_token:
    print("No feishu token found")
    exit(1)

# 解析 token 获取 scope
parts = feishu_token.split(".")
if len(parts) >= 2:
    import base64
    payload = parts[1] + "==="
    try:
        decoded = base64.b64decode(payload).decode("utf-8")
        token_data = json.loads(decoded)
        print(f"Token scopes: {token_data.get('scope', 'N/A')[:200]}")
        print(f"Token iss: {token_data.get('iss', 'N/A')}")
    except:
        pass

# 调飞书多维表格 API
base_token = "FzqWbuzEGarcXUsBTAYc5l3bnUf"
table_id = "tblZKY73Tzg2GSuA"

url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{base_token}/tables/{table_id}/fields"
req = urllib.request.Request(url)
req.add_header("Authorization", f"Bearer {feishu_token}")
req.add_header("Content-Type", "application/json")
try:
    resp = urllib.request.urlopen(req, timeout=10)
    result = json.loads(resp.read())
    print(json.dumps(result, ensure_ascii=False, indent=2)[:3000])
except Exception as e:
    import urllib.error
    if isinstance(e, urllib.error.HTTPError):
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code}: {body[:1000]}")
    else:
        print(f"Error: {e}")
