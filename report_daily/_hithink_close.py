# -*- coding: utf-8 -*-
"""同花顺MCP复盘生成器 - 2026-07-24"""
import subprocess, json, os
from pathlib import Path

# 工具函数
def mcp_call(svc, tool, args):
    ps1 = os.path.join(os.environ.get('TEMP', 'C:\\temp'), 'mcp_call.ps1')
    with open(ps1, 'w', encoding='utf-8') as f:
        f.write(f'$env:PYTHONIOENCODING="utf-8"\nmcporter call {svc}.{tool} --args \'{json.dumps(args, ensure_ascii=False)}\'\n')
    rc, out = subprocess.getstatusoutput(f'powershell -NoProfile -ExecutionPolicy Bypass -File "{ps1}"')
    try:
        os.remove(ps1)
    except:
        pass
    if rc != 0:
        print(f'Error: {out}')
        return None
    try:
        return json.loads(out.strip())
    except:
        return None

# 指数
print('=== 指数 ===')
idx = mcp_call('hithink-finance-a-share', 'get_a_share_prices_snapshot',
    {"thscodes": "000001.SZ,399001.SZ,399006.SZ,000300.SH,000905.SH,000852.SH"})
indices = []
if idx and idx.get('code') == 0:
    for item in idx['data']['item']:
        name = {'000001.SZ':'上证指数','399001.SZ':'深证成指','399006.SZ':'创业板指',
                '000300.SH':'沪深300','000905.SH':'中证500','000852.SH':'中证1000'}.get(item['thscode'], item['thscode'])
        indices.append({
            'name': name,
            'close': item['last_price'],
            'pct': round(item['price_change_ratio_pct'], 2),
            'vol': round(item['volume']/100000000, 1)  # 亿手
        })
        print(f"{name}: {item['last_price']:.2f}  {item['price_change_ratio_pct']:+.2f}%  量:{item['volume']/100000000:.1f}亿手")

# 涨停
print('\n=== 涨停 ===')
lim = mcp_call('hithink-finance-a-share', 'get_a_share_special_data_limit_up_pool', {})
limit_ups = []
if lim and lim.get('code') == 0:
    total = lim['data']['pagination']['total']
    items = lim['data']['item']
    print(f'涨停: {total}只')
    # TOP10
    for r in items[:10]:
        limit_ups.append({
            'name': r['name'],
            'code': r['thscode'],
            'pct': round(r['price_change_ratio_pct'], 2),
            'reason': r['limit_up_reason'],
            'days': r['continue_day_cnt']
        })
        print(f"  {r['name']}({r['thscode']}): {r['price_change_ratio_pct']:+.2f}%  {r['limit_up_reason'][:30]}...")

# 热股榜
print('\n=== 热股榜 ===')
hot = mcp_call('hithink-finance-a-share', 'get_a_share_special_data_hot_stock_list', {"period":"day"})
hot_stocks = []
if hot and hot.get('code') == 0:
    for r in hot['data']['item'][:10]:
        hot_stocks.append({
            'name': r.get('name',''),
            'code': r.get('thscode',''),
            'rank': r.get('rank',0)
        })
        print(f"  {r.get('name','')}  排名:{r.get('rank',0)}")

# ETF持仓
print('\n=== 持仓ETF ===')
etfs = ['159516.SZ','159611.SZ','512480.SH','512760.SH','159865.SZ','515050.SH']
etf_data = mcp_call('hithink-finance-a-share', 'get_a_share_prices_snapshot', {"thscodes": ','.join(etfs)})
positions = []
if etf_data and etf_data.get('code') == 0:
    for item in etf_data['data']['item']:
        positions.append({
            'code': item['thscode'],
            'close': item['last_price'],
            'pct': round(item['price_change_ratio_pct'], 2)
        })
        print(f"{item['thscode']}: {item['last_price']:.3f}  {item['price_change_ratio_pct']:+.2f}%")

# 写JSON
out = {
    'date': '2026-07-24',
    'indices': indices,
    'limit_ups': limit_ups,
    'limit_count': lim['data']['pagination']['total'] if lim else 0,
    'hot_stocks': hot_stocks,
    'positions': positions
}
with open('D:/mystock/report_daily/close_20260724.json', 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print('\n已保存 close_20260724.json')
