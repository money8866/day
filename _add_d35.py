import sys, re
sys.path.insert(0, r"D:\mystock")
from _icloud_calendar import CALENDARS, auth, DAV_HEADERS, add_event
from datetime import datetime, timezone, timedelta
import requests

TZ8 = timezone(timedelta(hours=8))
BASE = "https://p231-caldav.icloud.com.cn:443"
DAV_UA = DAV_HEADERS["User-Agent"]

# 1. 删除旧的"高铁去三明"
for cal_name, url in CALENDARS.items():
    r = requests.request("PROPFIND", url, auth=auth,
        headers={"User-Agent": DAV_UA, "Content-Type": "application/xml", "Depth": "1"},
        data=b'<?xml version="1.0" encoding="UTF-8"?><D:propfind xmlns:D="DAV:"><D:prop><D:getetag/></D:prop></D:propfind>',
        timeout=20)
    hrefs = re.findall(r'<response[^>]*>.*?<href[^>]*>([^<]+)</href>', r.text, re.DOTALL)
    for href in [h for h in hrefs if 'qclaw' in h.lower()]:
        full = BASE + href
        r2 = requests.get(full, auth=auth, headers={"User-Agent": DAV_UA}, timeout=15)
        if r2.status_code == 200 and ('三明' in r2.text or '去三明' in r2.text or '高铁' in r2.text):
            r3 = requests.delete(full, auth=auth, headers={"User-Agent": DAV_UA}, timeout=15)
            print(f"删旧: {cal_name} {href.split('/')[-1]} status={r3.status_code}")

# 2. 写入完整发布会日程
ok, msg = add_event("工作", "睿芯高光D35芯片量产发布会",
    datetime(2026, 9, 25, 11, 40, tzinfo=TZ8),
    datetime(2026, 9, 25, 12, 10, tzinfo=TZ8),
    "三明", "①睿芯高光D35芯片量产发布会\n②主题演讲\n③合作企业致辞\n④合作企业签约仪式\n（物产中大/微医控股/炽橙科技/绿城产服/浙大网新）")
print(msg)
