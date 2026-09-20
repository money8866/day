# -*- coding: utf-8 -*-
import requests
from requests.auth import HTTPBasicAuth

APPLE_ID = "kongxp2010@icloud.com"
PASSWORD = "vhom-qefj-tgwi-fmhp"
auth = HTTPBasicAuth(APPLE_ID, PASSWORD)
DAV_UA = "DAVKit/4.0.1 (732.2); CalendarStore/7.0.1 (1274.1); iOS/15.1 (19B74)"

base = "https://p231-caldav.icloud.com.cn:443"
cal_id = "53FF5255-C02D-4747-AEF7-99D3BE254AF8"
url = base + f"/1371007321/calendars/{cal_id}/"

r = requests.request("PROPFIND", url, auth=auth,
    headers={"User-Agent": DAV_UA, "Content-Type": "application/xml"},
    data=b'''<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:"><D:prop><D:getetag/></D:prop></D:propfind>''',
    timeout=20)

# 直接搜索文本中的 qclaw
text = r.text
idx = text.find('qclaw')
print(f"Status: {r.status_code}")
count = text.count('qclaw')
print(f"qclaw出现次数: {count}")

while idx != -1:
    start = max(0, idx-50)
    end = min(len(text), idx+80)
    print(f"\n上下文: ...{text[start:end]}...")
    idx = text.find('qclaw', idx+1)
