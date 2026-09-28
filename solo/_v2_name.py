# -*- coding: utf-8 -*-
import os, sys
LOG = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '_v2_name.log'),
           'w', encoding='utf-8')
sys.stdout = LOG
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sc = __import__('stock_cache')
pro = sc._get_pro()
for st in ('L', 'D', 'P'):
    try:
        df = pro.stock_basic(ts_code='603003.SH', list_status=st,
                             fields='ts_code,name,industry,list_date,list_status')
        print(st, 'rows', len(df))
        if len(df):
            print(df.to_string())
    except Exception as e:
        print(st, 'err', e)
try:
    df = pro.stock_basic(list_status='D', fields='ts_code,name')
    print('D total', len(df))
except Exception as e:
    print('err', e)
LOG.close()
