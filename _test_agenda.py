# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"D:\mystock")
import _tomorrow_agenda as m
ev = m.fetch_qclaw_events()
ev.sort(key=lambda x: x["start"])
for e in ev:
    print(e["start"].strftime("%Y-%m-%d %H:%M"), "|", e["summary"], "|", e["calendar"])
print(f"\n共 {len(ev)} 个QClaw事件")
