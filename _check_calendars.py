# -*- coding: utf-8 -*-
import requests
from requests.auth import HTTPBasicAuth
import re

APPLE_ID = "kongxp2010@icloud.com"
PASSWORD = "vhom-qefj-tgwi-fmhp"
auth = HTTPBasicAuth(APPLE_ID, PASSWORD)
DAV_UA = "DAVKit/4.0.1 (732.2); CalendarStore/7.0.1 (1274.1); iOS/15.1 (19B74)"

# 从日历列表里看到的 calendar IDs
calendars_info = {
    "日历":   "53FF5255-C02D-4747-AEF7-99D3BE254AF8",
    "工作":   "work",
    "个人":   "home",
    "家庭":   "eb60ced4-a2c3-46ea-8293-77ae5d90d6a8",
}

base = "https://p231-caldav.icloud.com.cn:443"

for name, cal_id in calendars_info.items():
    if "/" in cal_id:
        url = base + cal_id
    else:
        url = base + f"/1371007321/calendars/{cal_id}/"
    print(f"\n--- {name}: {url} ---")
    r = requests.request("PROPFIND", url, auth=auth,
        headers={"User-Agent": DAV_UA, "Content-Type": "application/xml"},
        data=b'''<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:"><D:prop><D:getetag/></D:prop></D:propfind>''',
        timeout=15)
    hrefs = re.findall(r'<response[^>]*>.*?<href[^>]*>([^<]+)</href>', r.text, re.DOTALL)
    qclaw = [h for h in hrefs if 'qclaw' in h.lower()]
    print(f"  QClaw事件: {len(qclaw)}")
    for h in qclaw:
        print(f"    {h}")
