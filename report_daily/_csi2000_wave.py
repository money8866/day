# -*- coding: utf-8 -*-
import subprocess, json, os, datetime
import sys
sys.stdout.reconfigure(encoding='utf-8')

SKILL_DIR = r'C:\Users\kongx\.qclaw\skills\tongdaxin-mcp'

def ps_run(cmd, timeout=30):
    r = subprocess.run(['powershell', '-Command', cmd], capture_output=True, encoding='utf-8', errors='replace', timeout=timeout)
    return r.returncode, r.stdout

rc_tok, out_tok = ps_run('& "' + SKILL_DIR + '\\get-token.ps1"')
token = out_tok.strip() if rc_tok == 0 else None
rc_c, out_c = ps_run('mcporter config get tdx-finance_qclaw 2>$null')
if not (rc_c == 0 and token and token in out_c):
    ps_run('mcporter config remove tdx-finance_qclaw 2>$null')
    ps_run('mcporter config add tdx-finance_qclaw "https://txmcp.tdx.com.cn:3001/qclawmcp" --header "Authorization=Bearer %s" --header "Accept=application/json, text/event-stream" --transport http --scope home" % token')

def mcp(tool, **params):
    args = ' '.join(["%s='%s'" % (k, str(v)) for k, v in params.items()])
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'csi2k.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write("mcporter call tdx-finance_qclaw.%s %s\n" % (tool, args))
    rc, out = ps_run('& "%s"' % ps1, timeout=120)
    try: os.remove(ps1)
    except: pass
    if rc != 0:
        print(f"  [MCP ERR] rc={rc}")
        return None
    try:
        return json.loads(out.strip())
    except:
        print(f"  [MCP PARSE ERR] {out[:200]}")
        return None

# 中证2000指数: 932000 (setcode=1 沪)
print("获取中证2000近250日K线...")
resp = mcp('tdx_kline', code='932000', setcode='1', period='4', wantNum='250', tqFlag='11')

if not resp:
    # 尝试深市
    resp = mcp('tdx_kline', code='932000', setcode='0', period='4', wantNum='250', tqFlag='11')

if not resp:
    # 尝试其他代码
    for code in ['932000', '000300', '399303', '931025']:
        resp = mcp('tdx_kline', code=code, setcode='1', period='4', wantNum='250', tqFlag='11')
        if resp:
            print(f"  尝试 code={code} 成功")
            break
        resp = mcp('tdx_kline', code=code, setcode='0', period='4', wantNum='250', tqFlag='11')
        if resp:
            print(f"  尝试 code={code} setcode=0 成功")
            break

if resp:
    rows = resp.get('Rows', [])
    print(f"  获得 {len(rows)} 条数据")
    
    # 解析数据
    data = []
    for row in rows:
        d = row.get('Data', '')
        if len(d) < 29: continue
        date = d[:8]
        open_p = float(d[9:16]) if d[9:16].strip() else 0
        close_p = float(d[16:23]) if d[16:23].strip() else 0
        high_p = float(d[23:30]) if d[23:30].strip() else 0
        low_p = float(d[30:37]) if d[30:37].strip() else 0
        vol = int(d[37:50]) if d[37:50].strip() else 0
        amount = float(d[50:]) if d[50:].strip() else 0
        if close_p > 0:
            data.append({'date': date, 'open': open_p, 'high': high_p, 'low': low_p, 'close': close_p, 'vol': vol})
    
    if data:
        data.sort(key=lambda x: x['date'])
        print(f"  有效数据: {len(data)} 条")
        print(f"  区间: {data[0]['date']} ~ {data[-1]['date']}")
        print(f"  最新收盘: {data[-1]['close']}")
        
        # 找最高点
        max_idx = max(range(len(data)), key=lambda i: data[i]['high'])
        max_row = data[max_idx]
        print(f"\n  === 最高点 ===")
        print(f"  日期: {max_row['date']}")
        print(f"  最高价: {max_row['high']}")
        print(f"  收盘: {max_row['close']}")
        
        # 最近60日数据
        recent = data[-60:]
        min_close = min(r['close'] for r in recent)
        min_low = min(r['low'] for r in recent)
        min_idx = next(i for i, r in enumerate(data) if r['close'] == min_close)
        print(f"\n  === 近期低点 ===")
        print(f"  日期: {data[min_idx]['date']}")
        print(f"  最低价: {min_low}")
        print(f"  收盘: {min_close}")
        
        peak = max_row['high']
        bottom = min_low
        
        # ABC浪计算
        print(f"\n  === ABC三浪分析 ===")
        print(f"  A浪起点(最高点): {peak:.2f} ({max_row['date']})")
        print(f"  C浪起点(近期低点): {bottom:.2f} ({data[min_idx]['date']})")
        
        # 从高点到低点的A浪幅度
        a_drop = (peak - bottom) / peak * 100
        print(f"  A浪跌幅: -{a_drop:.2f}%")
        
        # 找A浪中的次高点(B浪起点之后的高点)
        # A浪 = 从peak到bottom，B浪反弹，C浪再下
        # 先确认哪个是真正的低点
        # C浪起点 = A浪低点后的反弹高点
        a_start_idx = max_idx
        
        # 找A浪终点(最低点之后)
        after_min = data[min_idx:]
        
        # 找A浪低点后的反弹高点(C浪起点)
        c_start_idx = None
        for i in range(min_idx+1, len(data)):
            if data[i]['high'] >= peak * 0.618:  # 反弹超过61.8%
                c_start_idx = i
                break
            if i == min(min_idx+20, len(data)-1):
                break
        
        if c_start_idx:
            c_start = data[c_start_idx]
            print(f"\n  C浪起点(反弹高点): {c_start['high']:.2f} ({c_start['date']})")
            c_start_price = c_start['high']
        else:
            # 没有明显反弹，用当前数据
            c_start_price = data[-1]['close'] * 1.02
            c_start_idx = len(data) - 1
            print(f"\n  C浪起点(当前): ~{c_start_price:.2f}")
        
        current_price = data[-1]['close']
        current_low = data[-1]['low']
        print(f"\n  当前价: {current_price:.2f} (最低: {current_low:.2f})")
        
        # C浪目标位计算
        print(f"\n  === C浪目标位预测 ===")
        print(f"  A浪总跌幅: {a_drop:.2f}%")
        
        # 常规C浪 = A浪的 0.618 / 1.0 / 1.236 / 1.618倍
        c_targets = {}
        for ratio in [0.618, 0.786, 1.0, 1.236, 1.382, 1.618]:
            target = c_start_price - (peak - bottom) * ratio
            c_targets[ratio] = target
            print(f"  C浪={ratio:.3f}xA: {target:.2f}")
        
        # 更精确: 用A浪内部结构估算
        # A浪内部的子浪结构
        # 从peak到bottom，找A浪内部的子高点(小B浪)
        sub_b_idx = None
        for i in range(a_start_idx+1, min_idx):
            if data[i]['high'] > data[a_start_idx]['high'] * 0.95:  # 几乎没反弹
                continue
            if sub_b_idx is None or data[i]['high'] > data[sub_b_idx]['high']:
                sub_b_idx = i
        
        if sub_b_idx:
            sub_b = data[sub_b_idx]
            print(f"\n  A浪次高点(小B): {sub_b['high']:.2f} ({sub_b['date']})")
            sub_b_drop = (sub_b['high'] - bottom) / sub_b['high'] * 100
            print(f"  小B跌幅: -{sub_b_drop:.2f}%")
            # C浪通常 >= A浪
            c_from_b = sub_b['high'] - (peak - bottom)
            print(f"  参考C浪目标(以小B为起点): {c_from_b:.2f}")
        
        # 当前从C浪起点的跌幅
        c_current_drop = (c_start_price - current_price) / c_start_price * 100
        c_total_drop = (c_start_price - bottom) / c_start_price * 100 if c_start_idx else 0
        
        print(f"\n  当前C浪已跌: -{c_current_drop:.2f}%")
        
        # 预测: C浪通常与A浪等长或延伸
        # 考虑中证2000小盘股特性，C浪有时会破A浪低点
        # 最悲观: C浪=A浪的1.382-1.618倍
        most_likely = c_targets.get(1.0, bottom)
        optimistic = c_targets.get(0.618, bottom * 1.02)
        pessimistic = c_targets.get(1.382, bottom * 0.95)
        very_bad = c_targets.get(1.618, bottom * 0.90)
        
        print(f"\n  === 关键支撑位 ===")
        print(f"  乐观 (C浪=A浪×0.618): {optimistic:.2f}")
        print(f"  合理 (C浪=A浪×1.0):   {most_likely:.2f}")
        print(f"  悲观 (C浪=A浪×1.382): {pessimistic:.2f}")
        print(f"  极端 (C浪=A浪×1.618): {very_bad:.2f}")
        print(f"  A浪低点(前低):         {bottom:.2f}")
        
        # 最近60日低点上穿/下穿
        recent_lows = [r['low'] for r in data[-20:]]
        print(f"\n  近20日最低: {min(recent_lows):.2f}")
        print(f"  近20日最高: {max(r['high'] for r in data[-20:]):.2f}")
        
        # 输出完整数据供后续分析
        print(f"\n=== 完整K线数据 ===")
        for row in data[-20:]:
            print(f"  {row['date']}  O:{row['open']:.2f} H:{row['high']:.2f} L:{row['low']:.2f} C:{row['close']:.2f}")
    else:
        print("  无有效数据")
else:
    print("  获取K线失败")
