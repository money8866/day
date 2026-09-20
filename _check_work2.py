# -*- coding: utf-8 -*-
import requests
from requests.auth import HTTPBasicAuth

APPLE_ID = "kongxp2010@icloud.com"
PASSWORD = "vhom-qefj-tgwi-fmhp"
auth = HTTPBasicAuth(APPLE_ID, PASSWORD)
DAV_UA = "DAVKit/4.0.1 (732.2); CalendarStore/7.0.1 (1274.1); iOS/15.1 (19B74)"
base = "https://p231-caldav.icloud.com.cn:443"

for cal_name, cal_path in [
    ("工作", "/1371007321/calendars/work"),
    ("日历", "/1371007321/calendars/53FF5255-C02D-4747-AEF7-99D3BE254AF8"),
]:
    url = base + cal_path
    r = requests.request("PROPFIND", url, auth=auth,
        headers={"User-Agent": DAV_UA, "Content-Type": "application/xml", "Depth": "1"},
        data=b'''<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:"><D:prop><D:getetag/></D:prop></D:propfind>''',
        timeout=20)
    print(f"「{cal_name}」: status={r.status_code}, qclaw={r.text.count('qclaw')}, len={len(r.text)}")
