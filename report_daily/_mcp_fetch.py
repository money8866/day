# -*- coding: utf-8 -*-
"""Hithink MCP 取数助手 - 通过 mcporter 调用并保存 JSON（修复 GBK 编码）"""
import subprocess, json, os, sys

OUT = "D:/mystock/report_daily"

def mcp(svc, tool, args, outfile):
    """调用 mcporter，返回解析后的 dict，并保存原始 JSON"""
    ps = os.path.join(os.environ.get('TEMP', 'C:/temp'), 'mcp_call.ps1')
    with open(ps, 'w', encoding='utf-8') as f:
        f.write('$env:PYTHONIOENCODING="utf-8"\n')
        f.write('mcporter call %s.%s --args \'%s\'\n' % (svc, tool, json.dumps(args, ensure_ascii=False)))
    try:
        proc = subprocess.run(
            ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ps],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            encoding='utf-8', errors='replace', timeout=60
        )
        raw = proc.stdout
        # 提取第一个 { 开始的 JSON
        s = raw.strip()
        if not s:
            print(f'  [空] {outfile}')
            return None
        # 找到首个 {
        idx = s.find('{')
        if idx > 0:
            s = s[idx:]
        # 去掉尾部非JSON
        end = s.rfind('}')
        if end >= 0:
            s = s[:end+1]
        data = json.loads(s)
        with open(os.path.join(OUT, outfile), 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f'  [OK] {outfile}  code={data.get("code")}  bytes={len(raw)}')
        return data
    except Exception as e:
        print(f'  [ERR] {outfile}: {e}')
        return None
    finally:
        try: os.remove(ps)
        except: pass

print('=== 取数 2026-07-24 收盘 ===')

# 1. 指数
mcp('hithink-finance-a-share', 'get_a_share_prices_snapshot',
    {"thscodes": "000001.SZ,399001.SZ,399006.SZ,000300.SH,000905.SH,000852.SH"},
    'q_indices_0724.json')

# 2. 涨停 3页
for p in [1,2,3]:
    mcp('hithink-finance-a-share', 'get_a_share_special_data_limit_up_pool',
        {"page": p, "size": 50}, f'q_limitup_0724_{p}.json')

# 3. 跌停
mcp('hithink-finance-a-share', 'get_a_share_special_data_limit_down_pool',
    {}, 'q_limitdown_0724.json')

# 4. 热股
mcp('hithink-finance-a-share', 'get_a_share_special_data_hot_stock_list',
    {"period": "day"}, 'q_hot_0724.json')

# 5. 持仓
mcp('hithink-finance-a-share', 'get_a_share_prices_snapshot',
    {"thscodes": "159516.SZ,159611.SZ,512480.SH,512760.SH,159865.SZ,515050.SH"},
    'q_pos_0724.json')

print('=== 完成 ===')
