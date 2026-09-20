# -*- coding: utf-8 -*-
import requests
from requests.auth import HTTPBasicAuth
import re

APPLE_ID = "kongxp2010@icloud.com"
PASSWORD = "vhom-qefj-tgwi-fmhp"
auth = HTTPBasicAuth(APPLE_ID, PASSWORD)

DAV_UA = "DAVKit/4.0.1 (732.2); CalendarStore/7.0.1 (1274.1); iOS/15.1 (19B74)"
base = "https://p231-caldav.icloud.com.cn:443"

def check_cal(cal_name, cal_id):
    cal_url = base + f"/1371007321/calendars/{cal_id}/"
    r = requests.request("PROPFIND", cal_url, auth=auth,
        headers={"User-Agent": DAV_UA, "Content-Type": "application/xml"},
        data=b'''<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:"><D:prop><D:getetag/></D:prop></D:propfind>''',
        timeout=20)
    hrefs_raw = re.findall(r'<response[^>]*>.*?<href[^>]*>([^<]+)</href>', r.text, re.DOTALL)
    qclaw = [h for h in hrefs_raw if 'qclaw' in h.lower()]
    print(f"「{cal_name}」日历: {len(qclaw)} 个QClaw事件")
    for h in qclaw:
        print(f"  {h}")

# 工作日历
check_cal("工作", "work")
# 日历
check_cal("日历", "53FF5255-C02D-4747-AEF7-99D3BE254AF8")
