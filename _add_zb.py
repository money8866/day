import sys
sys.path.insert(0, r"D:\mystock")
from _icloud_calendar import add_event
from datetime import datetime, timezone, timedelta

TZ8 = timezone(timedelta(hours=8))
ok, msg = add_event("日历", "上海专病会议（安排技术人员）",
    datetime(2026, 9, 24, 14, 0, tzinfo=TZ8),
    datetime(2026, 9, 24, 16, 0, tzinfo=TZ8),
    "上海", "周四下午2点上海专病会议，需安排技术人员")
print(msg)
