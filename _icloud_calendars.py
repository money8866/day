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

# Use the discovered principal URL
principal_url = "https://caldav.icloud.com/1371007321/principal/"

# Get calendar-home-set
propfind_body = b'''<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop>
    <C:calendar-home-set/>
    <D:displayname/>
  </D:prop>
</D:propfind>'''

print("--- Getting calendar home set ---")
r = requests.request("PROPFIND", principal_url, auth=auth,
    headers={**headers, "Depth": "0"},
    data=propfind_body, timeout=15)
print(f"Status: {r.status_code}")
print(f"Body:\n{r.text}")

# Extract calendar home set URL
m = re.search(r'<D:href>([^<]+)</D:href>', r.text)
if m:
    home_url = "https://caldav.icloud.com" + m.group(1)
    print(f"\nCalendar home set: {home_url}")

    # List calendars
    print("\n--- Listing calendars ---")
    r2 = requests.request("PROPFIND", home_url, auth=auth,
        headers={**headers, "Depth": "1"},
        data=b'''<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop><D:displayname/><D:resourcetype/></D:prop>
</D:propfind>''',
        timeout=15)
    print(f"Status: {r2.status_code}")
    print(f"Body:\n{r2.text[:3000]}")

    # Extract all calendar URLs
    hrefs = re.findall(r'<D:href>([^<]+)</D:href>', r2.text)
    print(f"\nFound {len(hrefs)} calendar URLs:")
    for h in hrefs:
        print(f"  {h}")
