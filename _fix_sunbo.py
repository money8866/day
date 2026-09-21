# -*- coding: utf-8 -*-
import sys, re, requests
sys.path.insert(0, r"D:\mystock")
from _icloud_calendar import CALENDARS, auth, DAV_HEADERS, add_event
from datetime import datetime, timezone, timedelta

TZ8 = timezone(timedelta(hours=8))
BASE = "https://p231-caldav.icloud.com.cn:443"

# 1. 找出所有含"孙博"的 qclaw 事件并删除
deleted = []
for cal_name, url in CALENDARS.items():
    r = requests.request("PROPFIND", url, auth=auth,
        headers={"User-Agent": DAV_HEADERS["User-Agent"], "Content-Type": "application/xml", "Depth": "1"},
        data=b'''<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:"><D:prop><D:getetag/></D:prop></D:propfind>''', timeout=20)
    hrefs = re.findall(r'<response[^>]*>.*?<href[^>]*>([^<]+)</href>', r.text, re.DOTALL)
    for href in [h for h in hrefs if 'qclaw' in h.lower()]:
        full = href if href.startswith('http') else BASE + href
        r2 = requests.get(full, auth=auth, headers={"User-Agent": DAV_HEADERS["User-Agent"]}, timeout=15)
        if r2.status_code == 200 and '孙博' in r2.text:
            r3 = requests.delete(full, auth=auth, headers={"User-Agent": DAV_HEADERS["User-Agent"]}, timeout=15)
            if r3.status_code in (200, 204):
                deleted.append(f"{cal_name}:{href.split('/')[-1]}")

print("已删除旧事件:", deleted if deleted else "无")

# 2. 写入正确日期：周二 9/22 11:00
ok, msg = add_event("日历", "跟孙博会面",
    datetime(2026, 9, 22, 11, 0, tzinfo=TZ8),
    datetime(2026, 9, 22, 12, 0, tzinfo=TZ8),
    "", "周二 11:00 跟孙博会面")
print(msg)
