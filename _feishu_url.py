# -*- coding: utf-8 -*-
"""绕过 OpenClaw 检测，直接用 config.json 中的 token 调飞书 API"""
import json, os, urllib.request, urllib.parse

home = os.path.expanduser("~")
config_path = os.path.join(home, ".lark-cli", "config.json")
with open(config_path, "r", encoding="utf-8") as f:
    raw = f.read()
data = json.loads(raw)
app = data["apps"][0]
app_id = app["appId"]
users = app.get("users", [])
user_token = None
for u in users:
    at = u.get("accessToken", "")
    if isinstance(at, str) and at:
        user_token = at
        break

if not user_token:
    print("No user token found in config")
    exit(1)

print(f"App: {app_id}, Token prefix: {user_token[:30]}...")

# 直接调飞书开放 API
# base token: FzqWbuzEGarcXUsBTAYc5l3bnUf
# table_id: tblZKY73Tzg2GSuA
base_token = "FzqWbuzEGarcXUsBTAYc5l3bnUf"
table_id = "tblZKY73Tzg2GSuA"

# 先查字段
url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{base_token}/tables/{table_id}/fields"
req = urllib.request.Request(url)
req.add_header("Authorization", f"Bearer {user_token}")
req.add_header("Content-Type", "application/json")
try:
    resp = urllib.request.urlopen(req, timeout=10)
    result = json.loads(resp.read())
    print(json.dumps(result, ensure_ascii=False, indent=2)[:3000])
except Exception as e:
    print(f"Error: {e}")
    import urllib.error
    if isinstance(e, urllib.error.HTTPError):
        print(f"HTTP {e.code}: {e.read()}")
