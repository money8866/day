# -*- coding: utf-8 -*-
import sys, re, requests
sys.path.insert(0, r"D:\mystock")
from _icloud_calendar import CALENDARS, auth, DAV_HEADERS
from datetime import datetime

TZ8 = __import__("datetime").timezone(__import__("datetime").timedelta(hours=8))
url = CALENDARS["日历"]
r = requests.request("PROPFIND", url, auth=auth,
    headers={"User-Agent": DAV_HEADERS["User-Agent"], "Content-Type": "application/xml", "Depth": "1"},
    data=b'''<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:"><D:prop><D:getetag/></D:prop></D:propfind>''', timeout=20)

hrefs = re.findall(r'<response[^>]*>.*?<href[^>]*>([^<]+)</href>', r.text, re.DOTALL)
qclaw = [h for h in hrefs if 'qclaw' in h.lower()]
base = "https://p231-caldav.icloud.com.cn:443"
print(f"日历 qclaw 事件 ({len(qclaw)}):")
for h in qclaw:
    full = h if h.startswith('http') else base + h
    try:
        r2 = requests.get(full, auth=auth, headers={"User-Agent": DAV_HEADERS["User-Agent"]}, timeout=15)
    except Exception as e:
        print(f"  GET ERROR {e}")
        continue
    if r2.status_code == 200:
        summ = re.search(r'SUMMARY:([^\r\n]+)', r2.text)
        dt = re.search(r'DTSTART[^:]*:(\d{8}T\d{6})', r2.text)
        dtstr = ""
        if dt:
            dtstr = datetime.strptime(dt.group(1), "%Y%m%dT%H%M%S").strftime("%Y-%m-%d %H:%M")
        print(f"  {dtstr} | {summ.group(1) if summ else '?'}")
    else:
        print(f"  status={r2.status_code} href={h}")
