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

cal_url = "https://p231-caldav.icloud.com.cn:443/1371007321/calendars/53FF5255-C02D-4747-AEF7-99D3BE254AF8/"

r = requests.request("PROPFIND", cal_url, auth=auth,
    headers={**headers, "Depth": "1"},
    data=b'''<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:"><D:prop><D:getetag/></D:prop></D:propfind>''',
    timeout=20)

# Extract all hrefs from XML - more robust parsing
hrefs_raw = re.findall(r'<response[^>]*>.*?<href[^>]*>([^<]+)</href>.*?</response>', r.text, re.DOTALL)
ics_files = [h for h in hrefs_raw if h.endswith('.ics')]
print(f"共 {len(ics_files)} 个 .ics 文件:\n")

# Get content of each to see the event details
base = "https://p231-caldav.icloud.com.cn:443"
for ics_path in ics_files:
    full_url = base + ics_path
    r2 = requests.get(full_url, auth=auth, headers={"User-Agent": "DAVKit/4.0.1"}, timeout=15)
    if r2.status_code == 200:
        text = r2.text
        summ = re.search(r'SUMMARY:([^\r\n]+)', text)
        dt   = re.search(r'DTSTART[^\r\n]*:([^\r\n]+)', text)
        uid  = re.search(r'UID:([^\r\n]+)', text)
        print(f"  [{dt.group(1) if dt else '?'}] {summ.group(1) if summ else '?'}")
        print(f"    UID: {uid.group(1) if uid else '?'}")
        print(f"    URL: {ics_path}")
        print()
    else:
        print(f"  ❌ 无法读取 {ics_path}: {r2.status_code}")
