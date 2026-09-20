# -*- coding: utf-8 -*-
"""将所有日程同步到 iCloud 日历"""
import sys
sys.path.insert(0, r"D:\mystock")
from _icloud_calendar import add_event, CALENDARS
from datetime import datetime, timezone, timedelta

TZ8 = timezone(timedelta(hours=8))

# 所有日程：微医(今天日历)、非公会议(今天日历)、陈总家(9/24 日历)、北京行程(9/22-23 工作)、打牌(9/26 日历)
events = [
    # (日历名, 标题, 开始, 结束, 地点, 描述)
    ("日历", "微医人才盘点会",
     datetime(2026, 9, 20, 9, 40, tzinfo=TZ8),
     datetime(2026, 9, 20, 10, 40, tzinfo=TZ8),
     "腾讯会议：563-244-347\nhttps://meeting.tencent.com/dm/S2q8XI1PCmIY",
     "会议链接：https://meeting.tencent.com/dm/S2q8XI1PCmIY\n腾讯会议：563-244-347"),

    ("日历", "非公会议",
     datetime(2026, 9, 20, 14, 30, tzinfo=TZ8),
     datetime(2026, 9, 20, 16, 0, tzinfo=TZ8),
     "", "非公会议"),

    ("工作", "到北京",
     datetime(2026, 9, 22, 20, 0, tzinfo=TZ8),
     datetime(2026, 9, 22, 21, 0, tzinfo=TZ8),
     "", "周一晚上到北京，准备第二天上午的会议"),

    ("工作", "北京会议",
     datetime(2026, 9, 23, 9, 0, tzinfo=TZ8),
     datetime(2026, 9, 23, 12, 0, tzinfo=TZ8),
     "", "北京会议"),

    ("日历", "范总 + 陈总家吃饭",
     datetime(2026, 9, 24, 17, 30, tzinfo=TZ8),
     datetime(2026, 9, 24, 20, 30, tzinfo=TZ8),
     "陈总家",
     "和范总一起去陈总家吃饭"),

    ("日历", "打牌",
     datetime(2026, 9, 26, 18, 0, tzinfo=TZ8),
     datetime(2026, 9, 26, 22, 0, tzinfo=TZ8),
     "", ""),
]

print(f"开始同步 {len(events)} 个日程到 iCloud...\n")
success = 0
for cal, title, start, end, loc, desc in events:
    ok, msg = add_event(cal, title, start, end, loc, desc)
    print(f"  {msg}")
    if ok:
        success += 1

print(f"\n✅ 成功同步 {success}/{len(events)} 个日程到 iCloud 日历！")
print("请在你的 iPhone 日历 App 中查看「日历」和「工作」日历，应该已经自动出现了～")
