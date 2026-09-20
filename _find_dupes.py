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
    timeout=30)

# Extract all hrefs with regex
hrefs_raw = re.findall(r'<response[^>]*>.*?<href[^>]*>([^<]+)</href>', r.text, re.DOTALL)
ics_files = [h for h in hrefs_raw if h.endswith('.ics')]
print(f"共 {len(ics_files)} 个 .ics 文件")

base = "https://p231-caldav.icloud.com.cn:443"
# Check only qclaw events
qclaw_files = [h for h in ics_files if 'qclaw' in h.lower()]
print(f"QClaw 事件: {len(qclaw_files)} 个")
for h in qclaw_files:
    print(f"  {h}")

# Also check by checking content of files with today's date
today_hrefs = [h for h in ics_files if '20260920' in h]
print(f"\n9/20 相关: {len(today_hrefs)} 个")
for h in today_hrefs:
    print(f"  {h}")

# Count total and show recent ones
print(f"\n全部 .ics (前20):")
for h in ics_files[:20]:
    print(f"  {h}")
