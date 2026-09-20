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

# PROPFIND with Depth:1 to get all event URLs
r = requests.request("PROPFIND", cal_url, auth=auth,
    headers={**headers, "Depth": "1"},
    data=b'''<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:">
  <D:prop><D:getetag/><D:displayname/></D:prop>
</D:propfind>''',
    timeout=20)

print(f"Status: {r.status_code}")
hrefs = re.findall(r'<D:href>([^<]+)</D:href>', r.text)
ics_hrefs = [h for h in hrefs if h.endswith('.ics') or 'qclaw' in h.lower()]
print(f"找到 {len(ics_hrefs)} 个 .ics 文件:")
for h in ics_hrefs:
    print(f"  {h}")

# Also print raw for debugging
print(f"\nRaw response (first 3000):\n{r.text[:3000]}")
