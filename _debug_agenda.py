# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"D:\mystock")
from _icloud_calendar import CALENDARS, auth, DAV_HEADERS
import requests
import re

TZ8 = __import__("datetime").timezone(__import__("datetime").timedelta(hours=8))

for cal_name, url in CALENDARS.items():
    try:
        r = requests.request("PROPFIND", url, auth=auth,
            headers={"User-Agent": DAV_HEADERS["User-Agent"],
                     "Content-Type": "application/xml", "Depth": "1"},
            data=b'''<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:"><D:prop><D:getetag/></D:prop></D:propfind>''',
            timeout=20)
        hrefs = re.findall(r'<response[^>]*>.*?<href[^>]*>([^<]+)</href>', r.text, re.DOTALL)
        qclaw = [h for h in hrefs if 'qclaw' in h.lower()]
        print(f"{cal_name}: status={r.status_code}, total={len(hrefs)}, qclaw={len(qclaw)}")
    except Exception as e:
        print(f"{cal_name}: ERROR {e}")
