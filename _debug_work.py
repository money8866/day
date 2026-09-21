# -*- coding: utf-8 -*-
import sys, re, requests
sys.path.insert(0, r"D:\mystock")
from _icloud_calendar import CALENDARS, auth, DAV_HEADERS
from datetime import datetime
TZ8 = __import__("datetime").timezone(__import__("datetime").timedelta(hours=8))
BASE = "https://p231-caldav.icloud.com.cn:443"

url = CALENDARS["工作"]
r = requests.request("PROPFIND", url, auth=auth,
    headers={"User-Agent": DAV_HEADERS["User-Agent"], "Content-Type": "application/xml", "Depth": "1"},
    data=b'''<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:"><D:prop><D:getetag/></D:prop></D:propfind>''', timeout=20)
hrefs = re.findall(r'<response[^>]*>.*?<href[^>]*>([^<]+)</href>', r.text, re.DOTALL)
qclaw = [h for h in hrefs if 'qclaw' in h.lower()]
print(f"工作 qclaw href数: {len(qclaw)}")
for h in qclaw[:3]:
    print(f"  raw href: {h[:80]}")
    full = h if h.startswith('http') else BASE + h
    r2 = requests.get(full, auth=auth, headers={"User-Agent": DAV_HEADERS["User-Agent"]}, timeout=15)
    print(f"  GET status={r2.status_code}, len={len(r2.text)}")
    if r2.status_code == 200:
        summ = re.search(r'SUMMARY:([^\r\n]+)', r2.text)
        print(f"    -> {summ.group(1) if summ else '?'}")
