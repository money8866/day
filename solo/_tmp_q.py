# -*- coding: utf-8 -*-
from bts.data import parse_tdx_day_file
p = r'C:\new_tdx\vipdoc\sh\lday\sh000001.day'
d = parse_tdx_day_file(p)
print('列:', d.columns.tolist())
print('最后5日:', d.tail(5).to_string())
