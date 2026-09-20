import sys
sys.path.insert(0, r"D:\mystock")
from _icloud_calendar import add_event
from datetime import datetime, timezone, timedelta

TZ8 = timezone(timedelta(hours=8))
ok, msg = add_event("日历", "高铁去三明",
    datetime(2026, 9, 20, 16, 18, tzinfo=TZ8),
    datetime(2026, 9, 20, 18, 0, tzinfo=TZ8),
    "三明", "高铁出发")
print(msg)
