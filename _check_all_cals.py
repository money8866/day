# -*- coding: utf-8 -*-
import requests
from requests.auth import HTTPBasicAuth
import re

APPLE_ID = "kongxp2010@icloud.com"
PASSWORD = "vhom-qefj-tgwi-fmhp"
auth = HTTPBasicAuth(APPLE_ID, PASSWORD)
DAV_UA = "DAVKit/4.0.1 (732.2); CalendarStore/7.0.1 (1274.1); iOS/15.1 (19B74)"
base = "https://p231-caldav.icloud.com.cn:443"

def count_qclaw(cal_path):
    url = base + cal_path
    r = requests.request("PROPFIND", url, auth=auth,
        headers={"User-Agent": DAV_UA, "Content-Type": "application/xml"},
        data=b'''<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:"><D:prop><D:getetag/></D:prop></D:propfind>''',
        timeout=20)
    text = r.text
    count = text.lower().count('qclaw')
    print(f"  {cal_path}: {count} 个QClaw事件")
    if count > 0:
        # Extract event names
        for href in re.findall(r'<response[^>]*>.*?<href[^>]*>([^<]+)</href>', text, re.DOTALL):
            if 'qclaw' in href.lower():
                print(f"    {href}")

# 检查各日历
for name, cal_id in [
    ("日历",   "/1371007321/calendars/53FF5255-C02D-4747-AEF7-99D3BE254AF8/"),
    ("工作",   "/1371007321/calendars/work/"),
    ("个人",   "/1371007321/calendars/home/"),
    ("家庭",   "/1371007321/calendars/eb60ced4-a2c3-46ea-8293-77ae5d90d6a8/"),
]:
    count_qclaw(cal_id)
