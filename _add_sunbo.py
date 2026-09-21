import sys
sys.path.insert(0, r"D:\mystock")
from _icloud_calendar import add_event
from datetime import datetime, timezone, timedelta

TZ8 = timezone(timedelta(hours=8))
ok, msg = add_event("日历", "跟孙博会面",
    datetime(2026, 9, 21, 11, 0, tzinfo=TZ8),
    datetime(2026, 9, 21, 12, 0, tzinfo=TZ8),
    "", "周一下午11点跟孙博会面")
print(msg)
