# -*- coding: utf-8 -*-
"""修复 run_backtest 股票池过滤 (幂等):
旧: 仅排除 999/8/4 前缀, 未排除 5(沪基金)/1(深基金/转债)/2(深B股)/9(沪B股900),
    导致回测池含基金/B股 (7697只), 与生产口径(沪深A股)不一致.
新: 只保留 ts_code 首字符 in 630 的沪深A股
    (6=沪主板600/601/603/605+科创688, 3=创业板300/301, 0=深主板000/001/002/003),
    排除 1/2/4/5/8/9 开头的基金/B股/北交所.
"""
path = r"d:\mystock\tdx_backtest\volume_surge_strategy.py"
src = open(path, encoding="utf-8").read()

old = """            ts_code = tdx_filename_to_ts_code(path)
            if not ts_code or ts_code.startswith("999") or ts_code.startswith("8") or ts_code.startswith("4"):
                continue"""
new = """            ts_code = tdx_filename_to_ts_code(path)
            # 只保留沪深A股(首字符6/3/0)，排除基金5/1、B股2/9、北交所4/8
            if not ts_code or ts_code[0] not in "630":
                continue"""

if old in src:
    src = src.replace(old, new)
    open(path, "w", encoding="utf-8").write(src)
    print("PATCHED: load filter -> 沪深A股(630) only")
else:
    print("ERROR: pattern not found (already patched or changed)")
