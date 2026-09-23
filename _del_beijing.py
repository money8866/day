import sys, re
sys.path.insert(0, r"D:\mystock")
from _icloud_calendar import CALENDARS, auth, DAV_HEADERS
BASE = "https://p231-caldav.icloud.com.cn:443"
import requests

DAV_UA = DAV_HEADERS["User-Agent"]

for cal_name, url in CALENDARS.items():
    r = requests.request("PROPFIND", url, auth=auth,
        headers={"User-Agent": DAV_UA, "Content-Type": "application/xml", "Depth": "1"},
        data=b'<?xml version="1.0" encoding="UTF-8"?><D:propfind xmlns:D="DAV:"><D:prop><D:getetag/></D:prop></D:propfind>',
        timeout=20)
    hrefs = re.findall(r'<response[^>]*>.*?<href[^>]*>([^<]+)</href>', r.text, re.DOTALL)
    for href in [h for h in hrefs if 'qclaw' in h.lower()]:
        full = BASE + href
        r2 = requests.get(full, auth=auth, headers={"User-Agent": DAV_UA}, timeout=15)
        if r2.status_code == 200 and '北京会议' in r2.text:
            r3 = requests.delete(full, auth=auth, headers={"User-Agent": DAV_UA}, timeout=15)
            print(f"删除: {cal_name} {href.split('/')[-1]} status={r3.status_code}")

print("完成")
