# -*- coding: utf-8 -*-
"""
查询 iCloud 日历中「明天」的所有 QClaw 日程，格式化输出。
供每日22:00定时任务调用，提醒用户次日行程。
"""
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, timezone, timedelta
import re
import sys

# 复用 _icloud_calendar 的认证
sys.path.insert(0, r"D:\mystock")
from _icloud_calendar import CALENDARS, auth, DAV_HEADERS

TZ8 = timezone(timedelta(hours=8))
BASE = "https://p231-caldav.icloud.com.cn:443"

def fetch_qclaw_events():
    """遍历所有日历，返回所有 QClaw 事件列表"""
    events = []
    for cal_name, url in CALENDARS.items():
        try:
            r = requests.request("PROPFIND", url, auth=auth,
                headers={"User-Agent": DAV_HEADERS["User-Agent"],
                         "Content-Type": "application/xml", "Depth": "1"},
                data=b'''<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:"><D:prop><D:getetag/></D:prop></D:propfind>''',
                timeout=20)
        except Exception as e:
            print(f"  ⚠️ {cal_name} 读取失败: {e}", file=sys.stderr)
            continue

        hrefs = re.findall(r'<response[^>]*>.*?<href[^>]*>([^<]+)</href>', r.text, re.DOTALL)
        qclaw_hrefs = [h for h in hrefs if 'qclaw' in h.lower()]

        for href in qclaw_hrefs:
            full = href if href.startswith('http') else BASE + href
            try:
                r2 = requests.get(full, auth=auth,
                    headers={"User-Agent": DAV_HEADERS["User-Agent"]}, timeout=15)
            except Exception:
                continue
            if r2.status_code != 200:
                continue
            text = r2.text
            summ = re.search(r'SUMMARY:([^\r\n]+)', text)
            dt   = re.search(r'DTSTART[^:]*:(\d{8}T\d{6})', text)
            loc  = re.search(r'LOCATION:([^\r\n]*)', text)
            desc = re.search(r'DESCRIPTION:([^\r\n]*)', text)
            if dt:
                try:
                    dt_obj = datetime.strptime(dt.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=TZ8)
                except:
                    continue
                events.append({
                    "calendar": cal_name,
                    "summary": summ.group(1) if summ else "(无标题)",
                    "start": dt_obj,
                    "location": loc.group(1) if loc else "",
                    "description": desc.group(1) if desc else "",
                })
    return events

def main():
    # 明天日期
    tomorrow = (datetime.now(TZ8) + timedelta(days=1)).date()
    weekday_cn = ["周一","周二","周三","周四","周五","周六","周日"][tomorrow.weekday()]

    events = fetch_qclaw_events()
    todays = [e for e in events if e["start"].date() == tomorrow]
    todays.sort(key=lambda x: x["start"])

    print(f"📅 明日行程提醒（{tomorrow.strftime('%Y-%m-%d')} {weekday_cn}）")
    print("=" * 40)
    if not todays:
        print("✨ 明日暂无安排，好好休息！")
    else:
        for e in todays:
            t = e["start"].strftime("%H:%M")
            line = f"• {t} {e['summary']}"
            if e["location"]:
                line += f"  📍{e['location'].splitlines()[0]}"
            print(line)
            if e["calendar"] != "日历":
                print(f"    （{e['calendar']}日历）")
    print("=" * 40)

if __name__ == "__main__":
    main()
