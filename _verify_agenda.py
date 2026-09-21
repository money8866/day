# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"D:\mystock")
import _tomorrow_agenda as m
from datetime import datetime, timezone, timedelta

TZ8 = timezone(timedelta(hours=8))
ev = m.fetch_qclaw_events()
ev.sort(key=lambda x: x["start"])
print("全部QClaw事件：")
for e in ev:
    print(f"  {e['start'].strftime('%Y-%m-%d %H:%M')} | {e['summary']} | {e['calendar']}")

print("\n测试 9/22 的筛选：")
target = datetime(2026,9,22, tzinfo=TZ8).date()
todays = [e for e in ev if e["start"].date()==target]
for e in todays:
    print(f"  {e['start'].strftime('%H:%M')} {e['summary']} ({e['calendar']})")
