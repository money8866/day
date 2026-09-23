import sys
sys.path.insert(0, r"D:\mystock")
from _icloud_calendar import add_event
from datetime import datetime, timezone, timedelta

TZ8 = timezone(timedelta(hours=8))
ok, msg = add_event("日历", "接儿子（机场）",
    datetime(2026, 9, 29, 23, 0, tzinfo=TZ8),
    datetime(2026, 9, 29, 23, 30, tzinfo=TZ8),
    "", "接儿子航班抵达，请提前到机场等候")
print(msg)
