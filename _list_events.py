# -*- coding: utf-8 -*-
import requests
from requests.auth import HTTPBasicAuth
import re
from datetime import datetime, timezone, timedelta

APPLE_ID = "kongxp2010@icloud.com"
PASSWORD = "vhom-qefj-tgwi-fmhp"
auth = HTTPBasicAuth(APPLE_ID, PASSWORD)

headers = {
    "User-Agent": "DAVKit/4.0.1 (732.2); CalendarStore/7.0.1 (1274.1); iOS/15.1 (19B74)",
}

cal_url = "https://p231-caldav.icloud.com.cn:443/1371007321/calendars/53FF5255-C02D-4747-AEF7-99D3BE254AF8/"

# Get all events
report_body = b'''<?xml version="1.0" encoding="UTF-8"?>
<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop>
    <D:getetag/>
    <C:calendar-data/>
  </D:prop>
  <C:filter>
    <C:comp-filter name="VCALENDAR">
      <C:comp-filter name="VEVENT">
        <C:time-range start="20260901T000000Z" end="20261001T000000Z"/>
      </C:comp-filter>
    </C:comp-filter>
  </C:filter>
</C:calendar-query>'''

r = requests.request("REPORT", cal_url, auth=auth,
    headers={**headers, "Content-Type": "application/xml"},
    data=report_body, timeout=20)

print(f"Status: {r.status_code}")
# Extract VEVENTs
matches = re.findall(r'BEGIN:VEVENT.*?END:VEVENT', r.text, re.DOTALL)
print(f"找到 {len(matches)} 个事件:\n")
seen = {}
for vevent in matches:
    uid_m = re.search(r'UID:([^\r\n]+)', vevent)
    sum_m = re.search(r'SUMMARY:([^\r\n]+)', vevent)
    dt_m  = re.search(r'DTSTART[^\r\n]*:([^\r\n]+)', vevent)
    href_m = re.search(r'<D:href>([^<]+)</D:href>', vevent)
    uid  = uid_m.group(1) if uid_m else "?"
    summ  = sum_m.group(1) if sum_m else "?"
    dt   = dt_m.group(1) if dt_m else "?"
    href = href_m.group(1) if href_m else ""
    print(f"  [{dt}] {summ}")
    print(f"    UID: {uid}")
    print(f"    href: {href}")
    key = (summ, dt)
    seen[key] = seen.get(key, [])
    seen[key].append((uid, href))
    print()

# Find duplicates
print("\n=== 重复检查 ===")
for key, items in seen.items():
    if len(items) > 1:
        print(f"⚠️ 重复: {key[0]} @ {key[1]}")
        for uid, href in items:
            print(f"   UID={uid}")
            print(f"   href={href}")
