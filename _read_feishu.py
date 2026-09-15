# -*- coding: utf-8 -*-
"""读取飞书多维表格：选股跟踪"""
import json, subprocess, os, re

# 1. 获取 user access token
result = subprocess.run(
    ["lark-cli", "auth", "status", "--json"],
    capture_output=True, text=True,
    env={**os.environ, "OPENCLAW_HOME": "", "HERMES_HOME": "", "LARK_CHANNEL": ""}
)
print("auth status:", result.stdout[:200], result.stderr[:200])

# 从 config.json 找 appId + keychain 路径
home = os.path.expanduser("~")
config_path = os.path.join(home, ".lark-cli", "config.json")
with open(config_path, "r", encoding="utf-8") as f:
    raw = f.read()
# 打印原始内容（不含 token 敏感部分）
print("config ok:", "appId" in raw)
