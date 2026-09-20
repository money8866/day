# -*- coding: utf-8 -*-
import requests
from requests.auth import HTTPBasicAuth

APPLE_ID = "kongxp2010@icloud.com"
PASSWORD = "vhom-qefj-tgwi-fmhp"
auth = HTTPBasicAuth(APPLE_ID, PASSWORD)
headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36", "Content-Type": "application/xml"}

propfind_body = b'''<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:">
  <D:prop><D:displayname/><D:resourcetype/></D:prop>
</D:propfind>'''

for url in ["https://caldav.icloud.com", "https://caldav.icloud.com/principals/__uids__/"]:
    print(f"\n--- {url} ---")
    r = requests.request("PROPFIND", url, auth=auth, headers={**headers, "Depth": "1"}, data=propfind_body, timeout=15)
    print(f"Status: {r.status_code}, Body: {r.text[:300]}")

r2 = requests.get("https://caldav.icloud.com", auth=auth, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
print(f"\nGET Status: {r2.status_code}")
