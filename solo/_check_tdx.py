# -*- coding: utf-8 -*-
"""检查TDX .day文件是否有今天数据"""
import struct, os

codes = ['000001', '600519', '300750']
for code in codes:
    mkt = 0 if code.startswith('6') else 1
    mkt_dir = 'sh' if mkt == 0 else 'sz'
    fname = f'{mkt_dir}{code}.day'
    fpath = os.path.join('C:/new_tdx/vipdoc', mkt_dir, 'lday', fname)
    if os.path.exists(fpath):
        sz = os.path.getsize(fpath)
        if sz >= 32:
            with open(fpath, 'rb') as f:
                f.seek(-32, 2)
                data = f.read(32)
                date = struct.unpack('I', data[:4])[0]
                print(f'{code}: 最新日期={date} 文件大小={sz}')
        else:
            print(f'{code}: 文件太小{sj}')
    else:
        print(f'{code}: 文件不存在 {fpath}')