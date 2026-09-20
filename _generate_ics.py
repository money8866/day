# -*- coding: utf-8 -*-
"""生成日程 .ics 文件"""
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))  # 北京时间

events = [
    {
        "uid": "weiyi-meeting-20260920@qclaw",
        "summary": "微医人才盘点会",
        "start": datetime(2026, 9, 20, 9, 40, tzinfo=TZ),
        "end": datetime(2026, 9, 20, 10, 40, tzinfo=TZ),
        "location": "腾讯会议：563-244-347\nhttps://meeting.tencent.com/dm/S2q8XI1PCmIY",
        "description": "会议链接：https://meeting.tencent.com/dm/S2q8XI1PCmIY\n腾讯会议：563-244-347",
    },
    {
        "uid": "feigong-meeting-20260920@qclaw",
        "summary": "非公会议",
        "start": datetime(2026, 9, 20, 14, 30, tzinfo=TZ),
        "end": datetime(2026, 9, 20, 16, 0, tzinfo=TZ),
        "location": "",
        "description": "非公会议",
    },
    {
        "uid": "beijing-arrive-20260922@qclaw",
        "summary": "到北京",
        "start": datetime(2026, 9, 22, 20, 0, tzinfo=TZ),
        "end": datetime(2026, 9, 22, 21, 0, tzinfo=TZ),
        "location": "",
        "description": "周一晚上到北京，准备第二天上午的会议",
    },
    {
        "uid": "beijing-meeting-20260923@qclaw",
        "summary": "北京会议",
        "start": datetime(2026, 9, 23, 9, 0, tzinfo=TZ),
        "end": datetime(2026, 9, 23, 12, 0, tzinfo=TZ),
        "location": "",
        "description": "北京会议",
    },
    {
        "uid": "chen-dinner-20260924@qclaw",
        "summary": "范总 + 陈总家吃饭",
        "start": datetime(2026, 9, 24, 17, 30, tzinfo=TZ),
        "end": datetime(2026, 9, 24, 20, 30, tzinfo=TZ),
        "location": "陈总家",
        "description": "和范总一起去陈总家吃饭",
    },
    {
        "uid": "puke-20260926@qclaw",
        "summary": "打牌",
        "start": datetime(2026, 9, 26, 18, 0, tzinfo=TZ),
        "end": datetime(2026, 9, 26, 22, 0, tzinfo=TZ),
        "location": "",
        "description": "打牌时间",
    },
]

def fmt(dt):
    return dt.strftime("%Y%m%dT%H%M%S")

lines = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//QClaw//Calendar//ZH",
    "CALSCALE:GREGORIAN",
    "METHOD:PUBLISH",
    "X-WR-CALNAME:QClaw日程",
    "X-WR-TIMEZONE:Asia/Shanghai",
]

for ev in events:
    lines += [
        "BEGIN:VEVENT",
        f"UID:{ev['uid']}",
        f"DTSTAMP:{datetime.now(TZ).strftime('%Y%m%dT%H%M%SZ')}",
        f"DTSTART:{fmt(ev['start'])}",
        f"DTEND:{fmt(ev['end'])}",
        f"SUMMARY:{ev['summary']}",
    ]
    if ev.get("location"):
        lines.append(f"LOCATION:{ev['location']}")
    if ev.get("description"):
        lines.append(f"DESCRIPTION:{ev['description']}")
    lines += [
        "STATUS:CONFIRMED",
        "END:VEVENT",
    ]

lines.append("END:VCALENDAR")
ics_content = "\r\n".join(lines)

out_path = r"D:\mystock\qclaw_schedule.ics"
with open(out_path, "w", encoding="utf-8") as f:
    f.write(ics_content)

print(f"✅ 生成成功: {out_path}")
print(f"共 {len(events)} 个日程")
