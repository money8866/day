import sys
sys.path.insert(0, r"D:\mystock")
from _icloud_calendar import add_event
from datetime import datetime, timezone, timedelta

# 防错校验：确认星期
check = datetime(2026, 10, 5)
print("2026-10-05 weekday:", check.strftime("%A"), "（应=Monday/周一）")

TZ8 = timezone(timedelta(hours=8))
ok, msg = add_event("日历", "永加强海鲜楼晚餐（202包厢）",
    datetime(2026, 10, 5, 18, 0, tzinfo=TZ8),
    datetime(2026, 10, 5, 20, 0, tzinfo=TZ8),
    "龙湾区永中西路1111号", "永加强海鲜楼 二楼202包厢 晚餐18:00；导航 https://surl.amap.com/7P9NW8Xa0pn；楼下有停车场")
print(msg)
