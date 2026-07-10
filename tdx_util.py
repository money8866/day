# -*- coding: utf-8 -*-
"""
通达信(pytdx)连接工具
market规则: 0=深圳(含主板/中小板/创业板), 1=上海(含科创板)
"""
from pytdx.hq import TdxHq_API
import json

# 可用服务器（按优先级）
TDX_SERVERS = [
    ('218.6.170.47', 7709),
    ('123.125.108.14', 7709),
    ('180.153.18.170', 7709),
    ('180.153.18.172', 80),
    ('202.108.253.139', 80),
]

# 市场代码映射
# TDX接口: 0=深圳, 1=上海
# 代码规则: 60xxxx=上海, 00xxxx/002xxx=深圳, 300xxx=创业板, 688xxx=科创板
MARKET_MAP = {
    '60': 1,   # 上海主板
    '68': 1,   # 科创板
    '00': 0,   # 深圳主板
    '002': 0,  # 中小板
    '300': 0,  # 创业板
    '001': 0,  # 深圳主板(新)
}

def get_market(ts_code):
    """Tushare代码 -> (tdx_market, tdx_code)"""
    code = ts_code.split('.')[0]
    prefix = code[:3] if code.startswith('688') else code[:2]
    if prefix in MARKET_MAP:
        return MARKET_MAP[prefix], code
    if code.startswith('002'):
        return 0, code
    if code.startswith('300'):
        return 0, code
    if code.startswith('688'):
        return 1, code
    if code.startswith('60'):
        return 1, code
    # 默认深圳
    return 0, code

def connect():
    """连接到通达信行情服务器（自动选择可用服务器）"""
    api = TdxHq_API()
    for host, port in TDX_SERVERS:
        try:
            api.connect(host, port)
            # 测试连接
            data = api.get_security_quotes([(1, '000001')])
            if data:
                return api
        except:
            pass
    raise Exception('所有通达信服务器均无法连接')

def get_quote(ts_code):
    """获取单只股票实时行情"""
    market, code = get_market(ts_code)
    api = connect()
    try:
        data = api.get_security_quotes([(market, code)])
        if data:
            d = data[0]
            pct = (d['price'] / d['last_close'] - 1) * 100
            return {
                'ts_code': ts_code,
                'price': d['price'],
                'last_close': d['last_close'],
                'open': d['open'],
                'high': d['high'],
                'low': d['low'],
                'pct_chg': round(pct, 2),
                'vol': d['vol'],
                'amount_yi': round(d['amount'] / 1e8, 2),
                'bid1': d['bid1'],
                'ask1': d['ask1'],
                'bid_vol': d['bid_vol1'],
                'ask_vol': d['ask_vol1'],
                'time': d['servertime']
            }
    finally:
        api.disconnect()
    return None

def get_quotes(ts_codes):
    """批量获取多只股票实时行情"""
    api = connect()
    try:
        params = [get_market(tc) for tc in ts_codes]
        data = api.get_security_quotes(params)
        if data:
            results = []
            for d, tc in zip(data, ts_codes):
                if d:
                    pct = (d['price'] / d['last_close'] - 1) * 100
                    results.append({
                        'ts_code': tc,
                        'price': d['price'],
                        'last_close': d['last_close'],
                        'pct_chg': round(pct, 2),
                        'high': d['high'],
                        'low': d['low'],
                        'vol': d['vol'],
                        'amount_yi': round(d['amount'] / 1e8, 2),
                        'time': d['servertime']
                    })
            return results
    finally:
        api.disconnect()
    return []

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        codes = sys.argv[1:]
    else:
        codes = ['002125.SZ', '000001.SZ', '600519.SH']
    
    results = get_quotes(codes)
    print(json.dumps(results, ensure_ascii=False, indent=2))
