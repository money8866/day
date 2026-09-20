import sys
sys.path.insert(0, r"D:\mystock")
from _icloud_calendar import add_event, CALENDARS
from datetime import datetime, timezone, timedelta

TZ8 = timezone(timedelta(hours=8))

events = [
    # (日历名, 标题, 开始, 结束, 地点, 描述)
    ("日历", "非公会议",
     datetime(2026, 9, 20, 14, 30, tzinfo=TZ8),
     datetime(2026, 9, 20, 16, 0, tzinfo=TZ8),
     "", "非公会议"),

    ("工作", "到北京",
     datetime(2026, 9, 22, 20, 0, tzinfo=TZ8),
     datetime(2026, 9, 22, 21, 0, tzinfo=TZ8),
     "", "周一晚上到北京"),

    ("工作", "北京会议",
     datetime(2026, 9, 23, 9, 0, tzinfo=TZ8),
     datetime(2026, 9, 23, 12, 0, tzinfo=TZ8),
     "", "北京会议"),

    ("日历", "打牌",
     datetime(2026, 9, 26, 18, 0, tzinfo=TZ8),
     datetime(2026, 9, 26, 22, 0, tzinfo=TZ8),
     "", "打牌时间"),
]

print(f"补加 {len(events)} 个缺失日程...\n")
for cal, title, start, end, loc, desc in events:
    ok, msg = add_event(cal, title, start, end, loc, desc)
    print(f"  {msg}")
