# -*- coding: utf-8 -*-
import requests
from requests.auth import HTTPBasicAuth

APPLE_ID = "kongxp2010@icloud.com"
PASSWORD = "vhom-qefj-tgwi-fmhp"
auth = HTTPBasicAuth(APPLE_ID, PASSWORD)

DAV_UA = "DAVKit/4.0.1 (732.2); CalendarStore/7.0.1 (1274.1); iOS/15.1 (19B74)"
base = "https://p231-caldav.icloud.com.cn:443"

# 要删除的（重复的 高铁去三明 e39d30b7 和测试事件 test）
delete_hrefs = [
    # 重复的高铁去三明（e39d30b7，保留25757cea那个）
    "e39d30b7-bd97-4a1f-affe-db5a2a2ba999%40qclaw.ics",
    # 测试文件
    "test-9f7407f6-4ba3-464c-b1d7-efdb3d179db2%40qclaw.ics",
]

cal_id = "53FF5255-C02D-4747-AEF7-99D3BE254AF8"
for href in delete_hrefs:
    url = base + f"/1371007321/calendars/{cal_id}/" + href
    r = requests.delete(url, auth=auth, headers={"User-Agent": DAV_UA}, timeout=15)
    name = href.split('%40')[0]
    if r.status_code in (200, 204):
        print(f"✅ 已删除: {name}")
    else:
        print(f"❌ 删除失败 {r.status_code}: {name} - {r.text[:100]}")
