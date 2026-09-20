# -*- coding: utf-8 -*-
"""测试 iCloud CalDAV 连接"""
import requests
from requests.auth import HTTPBasicAuth
import xml.etree.ElementTree as ET

APPLE_ID = "kongxp@sina.com"
PASSWORD = "vhom-qefj-tgwi-fmhp"

# iCloud CalDAV 根地址
root_url = "https://caldav.icloud.com"

auth = HTTPBasicAuth(APPLE_ID, PASSWORD)
headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Depth": "1"
}

# PROPFIND 获取日历列表
propfind_body = '''<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:">
  <D:prop>
    <D:displayname/>
    <D:resourcetype/>
  </D:prop>
</D:propfind>'''

try:
    resp = requests.request(
        "PROPFIND",
        root_url,
        auth=auth,
        headers={**headers, "Content-Type": "application/xml"},
        data=propfind_body.encode("utf-8"),
        timeout=15
    )
    print(f"Status: {resp.status_code}")
    print(f"Response (first 2000 chars):\n{resp.text[:2000]}")

    # 尝试解析 caldav-home-set
    if resp.status_code in (207, 200):
        print("\n✅ iCloud 连接成功！")
        # 尝试列出日历
        # 从响应中提取日历URL
        import re
        home_sets = re.findall(r'<d:href>([^<]+)</d:href>', resp.text)
        print(f"\n找到 {len(home_sets)} 个资源:")
        for h in home_sets[:10]:
            print(f"  {h}")
except Exception as e:
    print(f"❌ 连接失败: {e}")
