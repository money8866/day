# -*- coding: utf-8 -*-
import json, os

home = os.path.expanduser("~")
config_path = os.path.join(home, ".lark-cli", "config.json")
with open(config_path, "r", encoding="utf-8") as f:
    raw = f.read()
data = json.loads(raw)
app = data["apps"][0]
users = app.get("users", [])
for u in users:
    at_obj = u.get("accessToken", {})
    print(f"accessToken type: {type(at_obj)}, value: {at_obj}")
    if isinstance(at_obj, dict):
        keychain_id = at_obj.get("id", "")
        print(f"  keychain id: {keychain_id}")
