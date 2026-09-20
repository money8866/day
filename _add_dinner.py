import sys
sys.path.insert(0, r"D:\mystock")
from _icloud_calendar import add_event
from datetime import datetime, timezone, timedelta

TZ8 = timezone(timedelta(hours=8))
ok, msg = add_event("日历", "永加强海鲜楼晚餐",
    datetime(2026, 9, 26, 17, 0, tzinfo=TZ8),
    datetime(2026, 9, 26, 20, 0, tzinfo=TZ8),
    "永加强海鲜楼 二楼208包厢\n龙湾区永中西路1111号",
    "贵宾热线：0577-86987979\n导航：https://surl.amap.com/7P9NW8Xa0pn\n楼下有停车场")
print(msg)
