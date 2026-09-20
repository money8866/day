# -*- coding: utf-8 -*-
import requests
from requests.auth import HTTPBasicAuth
import re

APPLE_ID = "kongxp2010@icloud.com"
PASSWORD = "vhom-qefj-tgwi-fmhp"
auth = HTTPBasicAuth(APPLE_ID, PASSWORD)

headers = {
    "User-Agent": "DAVKit/4.0.1 (732.2); CalendarStore/7.0.1 (1274.1); iOS/15.1 (19B74)",
}

# iCloud uses these paths - try each
test_paths = [
    "/principal/",
    "/principals/__uids__/",
    "/principals/root/",
]

for path in test_paths:
    url = f"https://caldav.icloud.com{path}"
    print(f"\n--- {url} ---")
    r = requests.request("PROPFIND", url, auth=auth,
        headers={**headers, "Depth": "0", "Content-Type": "application/xml"},
        data=b'''<?xml version="1.0" encoding="UTF-8"?><D:propfind xmlns:D="DAV:"><D:prop><D:current-user-principal/></D:prop></D:propfind>''',
        timeout=15)
    print(f"Status: {r.status_code}")
    if r.text:
        print(f"Body: {r.text[:500]}")

# Also try POST
print("\n--- Try POST to root ---")
r = requests.post("https://caldav.icloud.com/",
    auth=auth,
    headers={**headers, "Content-Type": "application/xml"},
    data=b'''<?xml version="1.0" encoding="UTF-8"?><D:propfind xmlns:D="DAV:"><D:prop><D:current-user-principal/></D:prop></D:propfind>''',
    timeout=15)
print(f"POST Status: {r.status_code}")
print(f"Body: {r.text[:500]}")
