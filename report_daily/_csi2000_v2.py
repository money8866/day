# -*- coding: utf-8 -*-
import os, sys, datetime
from pytdx.hq import TdxHq_API

sys.stdout.reconfigure(encoding='utf-8')
print(f"时间: {datetime.datetime.now().strftime('%H:%M:%S')}")

api = TdxHq_API(heartbeat=False, auto_retry=True)

# 尝试连接
connected = False
for ip, port in [('218.108.47.77', 7709), ('123.125.108.14', 7709),
                  ('180.153.18.170', 7709), ('180.153.18.172', 80),
                  ('202.108.253.139', 80), ('60.12.8.53', 7709)]:
    try:
        if api.connect(ip, int(port)):
            connected = True
            print(f"连接成功: {ip}:{port}")
            break
    except:
        continue

if not connected:
    print("连接失败，尝试本地数据...")
    api = None

# 中证2000指数代码: 932000
# TDX中证系列: 1A0001=上证, 399001=深证, 932000=中证2000 (沪)
# 但pytdx对指数支持有限，尝试多个代码
index_codes = [
    (1, '932000'),   # 沪 中证2000
    (0, '932000'),   # 深 中证2000
    (1, '000985'),   # 沪 沪深300
    (0, '399300'),   # 深 沪深300
    (0, '399303'),   # 深 国证2000
    (0, '399008'),   # 深 中小100
    (1, '931025'),   # 沪 中证1000
]

all_data = []
for market, code in index_codes:
    if not connected:
        break
    try:
        data = api.get_security_bars(
            category=4,   # 日K
            market=market,
            code=code,
            start=0,
            count=250
        )
        if data and len(data) > 20:
            print(f"\n找到数据: market={market}, code={code}, 条数={len(data)}")
            all_data = data
            break
    except Exception as e:
        continue

if not all_data and connected:
    # 尝试获取已知指数
    for market, code in [(1, '1A0001'), (0, '399001'), (0, '399006'), (1, '1B0016')]:
        try:
            data = api.get_security_bars(category=4, market=market, code=code, start=0, count=60)
            if data and len(data) > 20:
                print(f"\n[备用] {code}: {len(data)}条")
                all_data = data
                break
        except:
            continue

if connected:
    api.disconnect()

# 如果pytdx失败，读取本地TDX数据
if not all_data:
    print("\n使用本地TDX数据文件...")
    # 尝试找中证2000本地文件
    tdx_path = r'C:\new_tdx\vipdoc\ds'
    if os.path.exists(tdx_path):
        files = os.listdir(tdx_path)
        csi_files = [f for f in files if '932000' in f or '2000' in f.lower() or '中证' in f]
        print(f"  相关文件: {csi_files[:5]}")
        
        # 尝试读指数日线文件
        day_path = r'C:\new_tdx\vipdoc\ds'
        for fname in os.listdir(day_path):
            if '932000' in fname:
                fpath = os.path.join(day_path, fname)
                print(f"  找到: {fname}, 大小: {os.path.getsize(fpath)} bytes")
                with open(fpath, 'rb') as f:
                    content = f.read()
                print(f"  前100字节(hex): {content[:100].hex()}")

# 输出pytdx结果
if all_data:
    print(f"\n共获取 {len(all_data)} 条")
    
    # 解析
    rows = []
    for bar in all_data:
        date = bar.get('date', '')
        open_p = float(bar.get('open', 0))
        high_p = float(bar.get('high', 0))
        low_p = float(bar.get('low', 0))
        close_p = float(bar.get('close', 0))
        vol = int(bar.get('vol', 0))
        if close_p > 0 and date:
            rows.append({'date': date, 'open': open_p, 'high': high_p, 'low': low_p, 'close': close_p, 'vol': vol})
    
    rows.sort(key=lambda x: x['date'])
    print(f"有效数据: {len(rows)} 条")
    print(f"区间: {rows[0]['date']} ~ {rows[-1]['date']}")
    print(f"最新: {rows[-1]['date']} 收{rows[-1]['close']:.2f}")
    
    # 找最高点
    max_idx = max(range(len(rows)), key=lambda i: rows[i]['high'])
    max_row = rows[max_idx]
    peak = max_row['high']
    peak_date = max_row['date']
    
    # 找最低点
    min_idx = min(range(len(rows)), key=lambda i: rows[i]['low'])
    min_row = rows[min_idx]
    bottom = min_row['low']
    bottom_date = min_row['date']
    
    print(f"\n=== 最高点: {peak_date}  {peak:.2f} ===")
    print(f"=== 最低点: {bottom_date}  {bottom:.2f} ===")
    
    # ABC波浪计算
    a_drop = (peak - bottom) / peak * 100
    print(f"\nA浪跌幅: {a_drop:.2f}%")
    
    # 找A浪中的次高点(B浪起点后的反弹)
    # 假设peak是A浪起点，bottom是A浪终点
    # 找A浪内部反弹高点
    sub_b_idx = None
    for i in range(max_idx+1, min_idx):
        if sub_b_idx is None or rows[i]['high'] > rows[sub_b_idx]['high']:
            sub_b_idx = i
    
    if sub_b_idx:
        sub_b = rows[sub_b_idx]
        print(f"A浪次高点(小B): {sub_b['date']} {sub_b['high']:.2f}")
        sub_b_drop = (sub_b['high'] - bottom) / sub_b['high'] * 100
        print(f"小B后跌幅: -{sub_b_drop:.2f}%")
    
    # C浪起点 = A浪低点后的反弹高点
    c_start_idx = None
    if min_idx < len(rows) - 1:
        for i in range(min_idx + 1, min(len(rows), min_idx + 30)):
            if rows[i]['high'] > rows[min_idx]['high'] * 1.01:
                c_start_idx = i
                break
    
    if c_start_idx:
        c_start = rows[c_start_idx]
        c_start_price = c_start['high']
    else:
        c_start_price = rows[-1]['close'] * 1.02
        c_start_idx = len(rows) - 1
    
    current = rows[-1]['close']
    current_low = rows[-1]['low']
    
    print(f"\nC浪起点: {c_start_price:.2f}")
    print(f"当前收盘: {current:.2f}")
    print(f"当前最低: {current_low:.2f}")
    
    # C浪目标
    a_total = peak - bottom
    print(f"\n=== C浪目标位预测 ===")
    print(f"A浪总跌幅: {a_total:.2f}")
    
    targets = {}
    for ratio in [0.618, 0.786, 1.0, 1.236, 1.382, 1.618]:
        t = c_start_price - a_total * ratio
        targets[ratio] = t
        mark = "← 常规目标" if ratio == 1.0 else ("← 乐观" if ratio == 0.618 else ("← 悲观" if ratio == 1.382 else ""))
        print(f"  C浪=A×{ratio:.3f}: {t:.2f}  {mark}")
    
    print(f"\n  A浪低点: {bottom:.2f}  ← 参考")
    
    # 近期20日数据
    print(f"\n近20日K线:")
    print(f"  {'日期':10} {'开盘':8} {'最高':8} {'最低':8} {'收盘':8} {'涨跌幅':8}")
    prev_c = None
    for row in rows[-20:]:
        pct = (row['close']-prev_c)/prev_c*100 if prev_c else 0
        print(f"  {row['date']:10} {row['open']:8.2f} {row['high']:8.2f} {row['low']:8.2f} {row['close']:8.2f} {pct:+7.2f}%")
        prev_c = row['close']
else:
    print("无法获取数据，请手动检查")
