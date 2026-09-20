# -*- coding: utf-8 -*-
import requests
from requests.auth import HTTPBasicAuth
import re
import urllib.parse

APPLE_ID = "kongxp2010@icloud.com"
PASSWORD = "vhom-qefj-tgwi-fmhp"
auth = HTTPBasicAuth(APPLE_ID, PASSWORD)

DAV_UA = "DAVKit/4.0.1 (732.2); CalendarStore/7.0.1 (1274.1); iOS/15.1 (19B74)"
base = "https://p231-caldav.icloud.com.cn:443"

qclaw_hrefs = [
    "25757cea-ea69-408c-83de-6d0ce42b8044%40qclaw.ics",
    "a07ce8ed-b979-4230-8c9e-0d945927877f%40qclaw.ics",
    "b9f0a6a4-d953-4bb4-b5c7-d621fd8739b3%40qclaw.ics",
    "e39d30b7-bd97-4a1f-affe-db5a2a2ba999%40qclaw.ics",
    "test-9f7407f6-4ba3-464c-b1d7-efdb3d179db2%40qclaw.ics",
]

for href in qclaw_hrefs:
    url = base + "/1371007321/calendars/53FF5255-C02D-4747-AEF7-99D3BE254AF8/" + href
    r = requests.get(url, auth=auth, headers={"User-Agent": DAV_UA}, timeout=15)
    if r.status_code == 200:
        text = r.text
        summ = re.search(r'SUMMARY:([^\r\n]+)', text)
        dt   = re.search(r'DTSTART[^:]*:([^\r\n]+)', text)
        uid  = re.search(r'UID:([^\r\n]+)', text)
        print(f"[{dt.group(1) if dt else '?'}] {summ.group(1) if summ else '?'}")
        print(f"  UID: {uid.group(1) if uid else '?'}")
        print(f"  href: {href}")
        print()
