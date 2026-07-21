# -*- coding: utf-8 -*-
"""获取中证2000今日实时行情"""
import sys
sys.path.insert(0, r'D:\mystock')

# 方案1: Tushare（看是否已更新今日）
try:
    import tushare as ts
    pro = ts.pro_api('1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34')
    print("=== Tushare index_daily ===")
    df = pro.index_daily(ts_code='932000.CSI', start_date='20260721', end_date='20260721')
    if not df.empty:
        r = df.iloc[0]
        print(f"日期: {r['trade_date']}  收盘: {r['close']}  涨跌: {(r['close']-r['pre_close'])/r['pre_close']*100:+.2f}%")
        print(f"开盘: {r['open']}  最高: {r['high']}  最低: {r['low']}  振幅: {(r['high']-r['low'])/r['pre_close']*100:.2f}%")
    else:
        print("Tushare今日数据未更新（盘后才会更新）")
except Exception as e:
    print(f"Tushare失败: {e}")

print()

# 方案2: pytdx 实时
try:
    from pytdx.hq import TdxHq_API
    api = TdxHq_API(heartbeat=False, auto_retry=True)
    # 尝试多个服务器
    servers = [
        ('218.6.170.47', 7709),
        ('123.125.108.14', 7709),
        ('180.153.18.170', 7709),
    ]
    connected = False
    for host, port in servers:
        try:
            if api.connect(host, port, time_out=5):
                connected = True
                print(f"=== pytdx 连接成功: {host}:{port} ===")
                break
        except:
            continue
    
    if connected:
        # 中证2000: market=1(上海), code='932000'
        # 先尝试获取实时行情
        q = api.get_security_quotes([(1, '932000')])
        if q:
            r = q[0]
            print(f"名称: {r.get('name', '中证2000')}")
            print(f"现价: {r['price']}  昨收: {r['pre_close']}  今开: {r['open']}")
            print(f"最高: {r['high']}  最低: {r['low']}")
            chg = (r['price'] - r['pre_close']) / r['pre_close'] * 100
            print(f"涨跌: {chg:+.2f}%")
            amp = (r['high'] - r['low']) / r['pre_close'] * 100
            print(f"振幅: {amp:.2f}%")
            # 从最低点反弹
            reb = (r['price'] - r['low']) / r['low'] * 100
            print(f"从最低点反弹: {reb:+.2f}%")
        else:
            print("pytdx未返回中证2000数据")
        api.disconnect()
    else:
        print("pytdx连接失败")
except Exception as e:
    print(f"pytdx失败: {e}")
    import traceback
    traceback.print_exc()
