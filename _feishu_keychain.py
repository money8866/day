# -*- coding: utf-8 -*-
"""从 Windows 凭据管理器读取飞书 user token 并调用 API"""
import json, os, urllib.request, urllib.parse

home = os.path.expanduser("~")
config_path = os.path.join(home, ".lark-cli", "config.json")
with open(config_path, "r", encoding="utf-8") as f:
    raw = f.read()

# 提取 appId 和 keychain 中的 token id
data = json.loads(raw)
app = data["apps"][0]
app_id = app["appId"]
print(f"App ID: {app_id}")

# 找 user token 的 keychain id
users = app.get("users", [])
for u in users:
    uid = u.get("userOpenId", "")
    user_token_id = u.get("accessToken", {}).get("id", "") if isinstance(u.get("accessToken"), dict) else ""
    if not user_token_id:
        # 可能是内联存储
        at = u.get("accessToken", "")
        if isinstance(at, str) and at:
            print(f"User token (inline): {at[:20]}...")
            continue
    print(f"User token keychain id: {user_token_id}")
