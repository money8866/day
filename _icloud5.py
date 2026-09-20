# -*- coding: utf-8 -*-
import requests
from requests.auth import HTTPBasicAuth
import re

APPLE_ID = "kongxp2010@icloud.com"
PASSWORD = "vhom-qefj-tgwi-fmhp"
auth = HTTPBasicAuth(APPLE_ID, PASSWORD)

headers = {
    "User-Agent": "DAVKit/4.0.1 (732.2); CalendarStore/7.0.1 (1274.1); iOS/15.1 (19B74)",
    "Content-Type": "application/xml; charset=utf-8"
}

propfind_body = b'''<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop>
    <D:current-user-principal/>
    <C:calendar-home-set/>
  </D:prop>
</D:propfind>'''

# Try root with depth 1 to find principal
for url in [
    "https://caldav.icloud.com",
    "https://caldav.icloud.com/",
    "https://caldav.icloud.com/principals/",
]:
    print(f"\n--- {url} (Depth:1) ---")
    r = requests.request("PROPFIND", url, auth=auth,
        headers={**headers, "Depth": "1"},
        data=propfind_body, timeout=20)
    print(f"Status: {r.status_code}")
    if r.status_code == 207:
        # 提取所有 href
        hrefs = re.findall(r'<D:href>([^<]+)</D:href>', r.text)
        print(f"Found {len(hrefs)} hrefs:")
        for h in hrefs:
            print(f"  {h}")
        # 也找 principal
        principals = re.findall(r'<D:principal-URL[^>]*>[^<]*<D:href>([^<]+)</D:href>', r.text)
        home_sets = re.findall(r'<C:calendar-home-set[^>]*>[^<]*(?:<D:href>([^<]+)</D:href>)?', r.text)
        if principals:
            print(f"Principal: {principals}")
        if home_sets:
            print(f"Calendar home sets: {home_sets}")
    else:
        print(f"Body: '{r.text[:200]}'")
