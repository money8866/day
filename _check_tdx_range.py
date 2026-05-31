# -*- coding: utf-8 -*-
"""检查TDX数据历史范围"""
import sys, os, datetime
sys.stdout.reconfigure(encoding='utf-8')

def parse_first_last(filepath):
    if not os.path.exists(filepath): return None, None
    with open(filepath, "rb") as f:
        f.seek(0, 2)
        filesize = f.tell()
        if filesize < 32: return None, None
        # first record
        f.seek(0)
        chunk = f.read(32)
        first = int.from_bytes(chunk[0:4], "little")
        # last record
        f.seek(-32, 2)
        chunk = f.read(32)
        last = int.from_bytes(chunk[0:4], "little")
        return first, last

path = r"C:\new_tdx\vipdoc\sz\lday\sz000001.day"
first, last = parse_first_last(path)
if first:
    print(f"上证指数: {datetime.datetime.strptime(str(first), '%Y%m%d').strftime('%Y-%m-%d')} ~ {datetime.datetime.strptime(str(last), '%Y%m%d').strftime('%Y-%m-%d')}")

path2 = r"C:\new_tdx\vipdoc\sh\lday\sh600000.day"
first2, last2 = parse_first_last(path2)
if first2:
    print(f"浦发银行: {datetime.datetime.strptime(str(first2), '%Y%m%d').strftime('%Y-%m-%d')} ~ {datetime.datetime.strptime(str(last2), '%Y%m%d').strftime('%Y-%m-%d')}")
