# -*- coding: utf-8 -*-
"""测试 iCloud CalDAV - 多种端点"""
import requests
from requests.auth import HTTPBasicAuth
import re

APPLE_ID = "kongxp@sina.com"
PASSWORD = "vhom-qefj-tgwi-fmhp"

auth = HTTPBasicAuth(APPLE_ID, PASSWORD)
headers_base = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Content-Type": "application/xml"
}

propfind_body = b'''<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:">
  <D:prop>
    <D:displayname/>
    <D:resourcetype/>
  </D:prop>
</D:propfind>'''

# 尝试多个端点
urls_to_try = [
    "https://caldav.icloud.com",
    "https://caldav.icloud.com/principals/__uids__/",
    "https://caldav.icloud.com/caLdav/",
]

for url in urls_to_try:
    print(f"\n--- Trying: {url} ---")
    try:
        resp = requests.request(
            "PROPFIND", url, auth=auth,
            headers={**headers_base, "Depth": "1"},
            data=propfind_body, timeout=15
        )
        print(f"Status: {resp.status_code}")
        if resp.text:
            print(f"Body: {resp.text[:500]}")
        else:
            print("Empty body")
    except Exception as e:
        print(f"Error: {e}")

# 尝试用 basic auth GET 来确认凭据是否正确
print("\n--- Testing basic auth GET ---")
try:
    resp = requests.get(
        "https://caldav.icloud.com",
        auth=auth,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15
    )
    print(f"GET Status: {resp.status_code}")
    print(f"Headers: {dict(list(resp.headers.items())[:5])}")
except Exception as e:
    print(f"Error: {e}")
