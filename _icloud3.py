# -*- coding: utf-8 -*-
import requests
from requests.auth import HTTPBasicAuth

APPLE_ID = "kongxp2010@icloud.com"
PASSWORD = "vhom-qefj-tgwi-fmhp"
auth = HTTPBasicAuth(APPLE_ID, PASSWORD)

headers = {
    "User-Agent": "DAVKit/4.0.1 (732.2); CalendarStore/7.0.1 (1274.1); iOS/15.1 (19B74)",
    "Content-Type": "application/xml; charset=utf-8"
}

propfind_body = b'''<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:">
  <D:prop><D:displayname/></D:prop>
</D:propfind>'''

# 用 Apple 的标准 CalDAV client headers
print("--- Test 1: CalDAV root with DAVKit UA ---")
r = requests.request("PROPFIND", "https://caldav.icloud.com",
    auth=auth, headers={**headers, "Depth": "0"},
    data=propfind_body, timeout=20)
print(f"Status: {r.status_code}, Body: '{r.text[:500]}'")

print("\n--- Test 2: Principal URL ---")
# iCloud 的 principal URL 格式
r2 = requests.request("PROPFIND", "https://caldav.icloud.com/principals/root/",
    auth=auth, headers={**headers, "Depth": "0"},
    data=propfind_body, timeout=20)
print(f"Status: {r2.status_code}, Body: '{r2.text[:500]}'")

print("\n--- Test 3: Check auth with simple GET ---")
r3 = requests.get("https://caldav.icloud.com",
    auth=auth, headers={"User-Agent": "DAVKit/4.0.1"}, timeout=15)
print(f"Status: {r3.status_code}, Headers: {dict(list(r3.headers.items())[:8])}")
