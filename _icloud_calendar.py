# -*- coding: utf-8 -*-
"""
iCloud 日历 CalDAV 读写模块
支持：创建/删除日程、列出日历
"""
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, timezone, timedelta
import uuid

APPLE_ID = "kongxp2010@icloud.com"
PASSWORD = "vhom-qefj-tgwi-fmhp"

# 已知日历 URL
CALENDARS = {
    "日历":     "https://p231-caldav.icloud.com.cn:443/1371007321/calendars/53FF5255-C02D-4747-AEF7-99D3BE254AF8/",
    "大麦":     "https://p231-caldav.icloud.com.cn:443/1371007321/calendars/64192307-689E-43AD-A273-37049F44F034/",
    "飞猪":     "https://p231-caldav.icloud.com.cn:443/1371007321/calendars/6A266E1A-ACBD-43C8-BC06-C780FCCDA668/",
    "家庭":     "https://p231-caldav.icloud.com.cn:443/1371007321/calendars/eb60ced4-a2c3-46ea-8293-77ae5d90d6a8/",
    "个人":     "https://p231-caldav.icloud.com.cn:443/1371007321/calendars/home/",
    "工作":     "https://p231-caldav.icloud.com.cn:443/1371007321/calendars/work/",
    "任务":     "https://p231-caldav.icloud.com.cn:443/1371007321/calendars/z-20210124-031425-792-001/",
}

TZ8 = timezone(timedelta(hours=8))

auth = HTTPBasicAuth(APPLE_ID, PASSWORD)
DAV_HEADERS = {
    "User-Agent": "DAVKit/4.0.1 (732.2); CalendarStore/7.0.1 (1274.1); iOS/15.1 (19B74)",
    "Content-Type": "text/calendar; charset=utf-8",
}


def ics_event(summary, start, end, location="", description="", uid=None):
    """生成 VEVENT ICS 文本"""
    uid = uid or f"{uuid.uuid4()}@qclaw"
    dt_start = start.astimezone(TZ8).strftime("%Y%m%dT%H%M%S")
    dt_end   = end.astimezone(TZ8).strftime("%Y%m%dT%H%M%S")
    dt_stamp = datetime.now(TZ8).strftime("%Y%m%dT%H%M%S")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//QClaw//Calendar//ZH",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dt_stamp}",
        f"DTSTART:{dt_start}",
        f"DTEND:{dt_end}",
        f"SUMMARY:{summary}",
    ]
    if location:
        lines.append(f"LOCATION:{location}")
    if description:
        lines.append(f"DESCRIPTION:{description}")
    lines += ["STATUS:CONFIRMED", "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines)


def add_event(calendar_name, summary, start, end, location="", description=""):
    """向指定日历添加事件"""
    if calendar_name not in CALENDARS:
        return False, f"日历 '{calendar_name}' 不存在，可选: {list(CALENDARS.keys())}"

    url = CALENDARS[calendar_name]
    uid = f"{uuid.uuid4()}@qclaw"
    ics = ics_event(summary, start, end, location, description, uid)
    # iCloud requires filename.ics in the URL
    put_url = url.rstrip("/") + f"/{uid}.ics"

    r = requests.request("PUT", put_url, auth=auth,
        headers=DAV_HEADERS, data=ics.encode("utf-8"), timeout=15)
    if r.status_code in (201, 204):
        return True, f"✅ 已添加到「{calendar_name}」日历"
    return False, f"❌ 失败 ({r.status_code}): {r.text[:200]}"


def list_calendars():
    """列出所有日历"""
    return [(name, url.split("/")[-2]) for name, url in CALENDARS.items()]


# ===== 测试：列出日历 =====
if __name__ == "__main__":
    print("📅 iCloud 日历列表：")
    for name, cal_id in list_calendars():
        print(f"  • {name}  (ID: {cal_id})")

    print("\n📝 测试添加日程...")
    # 测试：添加一个日程（10秒后）
    test_time = datetime.now(TZ8) + timedelta(minutes=2)
    ok, msg = add_event(
        "日历",
        "【QClaw测试】iCloud日历写入成功！",
        test_time,
        test_time + timedelta(hours=1),
        location="",
        description="这是QClaw自动写入的测试日程，5分钟后会自动出现"
    )
    print(msg)
