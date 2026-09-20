# -*- coding: utf-8 -*-
import requests
from requests.auth import HTTPBasicAuth
import re

APPLE_ID = "kongxp2010@icloud.com"
PASSWORD = "vhom-qefj-tgwi-fmhp"
auth = HTTPBasicAuth(APPLE_ID, PASSWORD)

headers = {
    "User-Agent": "DAVKit/4.0.1 (732.2); CalendarStore/7.0.1 (1274.1); iOS/15.1 (19B74)",
    "Content-Type": "application/xml; charset=utf-8"
}

propfind_body = b'''<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop>
    <D:current-user-principal/>
    <C:calendar-home-set/>
    <D:displayname/>
  </D:prop>
</D:propfind>'''

print("--- Getting principal URL ---")
r = requests.request("PROPFIND", "https://caldav.icloud.com",
    auth=auth, headers={**headers, "Depth": "1"},
    data=propfind_body, timeout=20)
print(f"Status: {r.status_code}")
print(f"Body:\n{r.text}")

# 提取 principal URL
m = re.search(r'<D:href>([^<]+)</D:href>', r.text)
if m:
    print(f"\n找到 href: {m.group(1)}")
