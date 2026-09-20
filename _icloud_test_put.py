# -*- coding: utf-8 -*-
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, timezone, timedelta
import uuid

APPLE_ID = "kongxp2010@icloud.com"
PASSWORD = "vhom-qefj-tgwi-fmhp"
auth = HTTPBasicAuth(APPLE_ID, PASSWORD)

cal_url = "https://p231-caldav.icloud.com.cn:443/1371007321/calendars/53FF5255-C02D-4747-AEF7-99D3BE254AF8/"
uid = f"test-{uuid.uuid4()}@qclaw"
now = datetime.now(timezone(timedelta(hours=8)))
dt_start = now.strftime("%Y%m%dT%H%M%S")
dt_end   = (now + timedelta(hours=1)).strftime("%Y%m%dT%H%M%S")

ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//QClaw//Calendar//ZH
BEGIN:VEVENT
UID:{uid}
DTSTAMP:{now.strftime("%Y%m%dT%H%M%S")}
DTSTART:{dt_start}
DTEND:{dt_end}
SUMMARY:QClaw日历写入测试
DESCRIPTION:这是QClaw自动写入的测试日程
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR"""

# Try different URL formats for PUT
for url in [
    cal_url,
    cal_url.rstrip("/") + f"/{uid}.ics",
]:
    print(f"\n--- PUT {url} ---")
    for ct in [
        "text/calendar; charset=utf-8",
        "text/calendar;method=PUBLISH;charset=utf-8",
        "application/xml",
    ]:
        r = requests.put(url, auth=auth,
            headers={"User-Agent": "DAVKit/4.0.1", "Content-Type": ct},
            data=ics.encode("utf-8"), timeout=15)
        print(f"  Content-Type={ct[:30]:30s} → {r.status_code}")
        if r.status_code not in (200, 201, 204):
            print(f"    Body: {r.text[:200]}")
        else:
            print(f"  ✅ 成功！")
            break
