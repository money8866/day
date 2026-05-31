# -*- coding: utf-8 -*-
"""检查TDX数据最新日期"""
import sys, os, datetime
sys.stdout.reconfigure(encoding='utf-8')

TDX_PATH = r"C:\new_tdx\vipdoc"

def parse_last_date(filepath):
    if not os.path.exists(filepath): return None
    with open(filepath, "rb") as f:
        f.seek(-32, 2)  # go to last record
        chunk = f.read(32)
        date_int = int.from_bytes(chunk[0:4], "little")
        return date_int

stocks = ['688213.SH','002317.SZ','300718.SZ','301069.SZ','301029.SZ',
          '688006.SH','603662.SH','000429.SZ','601882.SH','603859.SH','603416.SH']

for tc in stocks:
    code = tc.split('.')[0]
    market = tc.split('.')[1].lower()
    path = os.path.join(TDX_PATH, market, "lday", f"{market}{code}.day")
    last = parse_last_date(path)
    if last:
        dt = datetime.datetime.strptime(str(last), "%Y%m%d")
        print(f"{tc}: TDX最新={dt.strftime('%Y-%m-%d')}")
    else:
        print(f"{tc}: 无文件")
