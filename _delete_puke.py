# -*- coding: utf-8 -*-
import requests
from requests.auth import HTTPBasicAuth
import re

APPLE_ID = "kongxp2010@icloud.com"
PASSWORD = "vhom-qefj-tgwi-fmhp"
auth = HTTPBasicAuth(APPLE_ID, PASSWORD)
DAV_UA = "DAVKit/4.0.1 (732.2); CalendarStore/7.0.1 (1274.1); iOS/15.1 (19B74)"
base = "https://p231-caldav.icloud.com.cn:443"

# 在「日历」中查找打牌事件
cal_url = base + "/1371007321/calendars/53FF5255-C02D-4747-AEF7-99D3BE254AF8"

r = requests.request("PROPFIND", cal_url, auth=auth,
    headers={"User-Agent": DAV_UA, "Content-Type": "application/xml", "Depth": "1"},
    data=b'''<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:"><D:prop><D:getetag/></D:prop></D:propfind>''',
    timeout=20)

# 找所有 qclaw .ics 文件
hrefs = re.findall(r'<response[^>]*>.*?<href[^>]*>([^<]+)</href>', r.text, re.DOTALL)
qclaw_hrefs = [h for h in hrefs if 'qclaw' in h.lower()]

print(f"共 {len(qclaw_hrefs)} 个QClaw事件，查找打牌...")

for href in qclaw_hrefs:
    full_url = base + href if href.startswith('/') else base + '/' + href
    r2 = requests.get(full_url, auth=auth, headers={"User-Agent": DAV_UA}, timeout=15)
    if r2.status_code == 200:
        if '打牌' in r2.text:
            print(f"找到打牌事件: {href}")
            # 删除
            r3 = requests.delete(full_url, auth=auth, headers={"User-Agent": DAV_UA}, timeout=15)
            if r3.status_code in (200, 204):
                print("✅ 打牌日程已删除")
            else:
                print(f"❌ 删除失败: {r3.status_code}")
        else:
            summ = re.search(r'SUMMARY:([^\r\n]+)', r2.text)
            print(f"  保留: {summ.group(1) if summ else '?'}")
