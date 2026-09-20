# -*- coding: utf-8 -*-
import requests
from requests.auth import HTTPBasicAuth
import re

APPLE_ID = "kongxp2010@icloud.com"
PASSWORD = "vhom-qefj-tgwi-fmhp"
auth = HTTPBasicAuth(APPLE_ID, PASSWORD)

headers = {
    "User-Agent": "DAVKit/4.0.1 (732.2); CalendarStore/7.0.1 (1274.1); iOS/15.1 (19B74)",
    "Content-Type": "application/xml"
}

cal_home = "https://p231-caldav.icloud.com.cn:443/1371007321/calendars/"

propfind = b'''<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop><D:displayname/><D:resourcetype/></D:prop>
</D:propfind>'''

print("--- Listing calendars ---")
r = requests.request("PROPFIND", cal_home, auth=auth,
    headers={**headers, "Depth": "1"},
    data=propfind, timeout=20)
print(f"Status: {r.status_code}")
print(f"Body:\n{r.text}")

hrefs = re.findall(r'<D:href>([^<]+)</D:href>', r.text)
print(f"\n=== All calendar URLs ({len(hrefs)}) ===")
for h in hrefs:
    print(f"  {h}")
