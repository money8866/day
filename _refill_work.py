import sys
sys.path.insert(0, r"D:\mystock")
from _icloud_calendar import add_event
from datetime import datetime, timezone, timedelta

TZ8 = timezone(timedelta(hours=8))
events = [
    ("工作", "到北京", datetime(2026,9,22,20,0,tzinfo=TZ8), datetime(2026,9,22,21,0,tzinfo=TZ8), "", "周一晚上到北京"),
    ("工作", "北京会议", datetime(2026,9,23,9,0,tzinfo=TZ8), datetime(2026,9,23,12,0,tzinfo=TZ8), "", "北京会议"),
]
for cal,title,s,e,loc,desc in events:
    ok,msg = add_event(cal,title,s,e,loc,desc)
    print(msg)
