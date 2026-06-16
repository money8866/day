import requests
import time

# 新浪 全市场接口 - 快速获取涨跌停统计
print("=== 新浪 全市场A股 完整统计（多页）===")
headers = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": "Mozilla/5.0"
}
url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"

all_stocks = []
# 新浪A股大约5000-6000只，每页100
for page in range(1, 61):
    params = {
        "page": page, "num": 100, "sort": "changepercent", "asc": 0,
        "node": "hs_a", "symbol": "", "_s_r_a": "page"
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        r.encoding = "utf-8"
        items = r.json()
        if not items or (isinstance(items, list) and len(items) == 0):
            print(f"  第{page}页为空，停止")
            break
        all_stocks.extend(items)
        if len(items) < 100:
            print(f"  第{page}页不足100条，最后一页")
            break
        if page % 10 == 0:
            print(f"  已获取 {len(all_stocks)} 条...")
        time.sleep(0.2)
    except Exception as e:
        print(f"  第{page}页失败: {e}")
        break

# 统计
zt = [s for s in all_stocks if float(s.get('changepercent', 0)) >= 9.5]
dt = [s for s in all_stocks if float(s.get('changepercent', 0)) <= -9.5]
up = [s for s in all_stocks if float(s.get('changepercent', 0)) > 0]
down = [s for s in all_stocks if float(s.get('changepercent', 0)) < 0]
zb = []  # 炸板：涨幅0-9.5%但昨曾涨停
# 炸板需要昨收数据，新浪接口没有

print(f"\n=== 全市场A股统计 ({len(all_stocks)}只) ===")
print(f"涨停: {len(zt)} 只")
for s in sorted(zt, key=lambda x: float(x['changepercent']), reverse=True)[:5]:
    print(f"  {s['name']} {s['changepercent']}%")
print(f"跌停: {len(dt)} 只")
for s in sorted(dt, key=lambda x: float(x['changepercent']))[:5]:
    print(f"  {s['name']} {s['changepercent']}%")
print(f"上涨: {len(up)} 只 ({len(up)/len(all_stocks)*100:.1f}%)")
print(f"下跌: {len(down)} 只 ({len(down)/len(all_stocks)*100:.1f}%)")

# 保存数据供后续使用
import json
with open('d:/mystock/solo/cache_daily/market_stats_cache.json', 'w', encoding='utf-8') as f:
    json.dump({
        'total': len(all_stocks),
        'zt_count': len(zt),
        'dt_count': len(dt),
        'up_count': len(up),
        'down_count': len(down),
        'up_ratio': round(len(up)/len(all_stocks)*100, 1),
        'down_ratio': round(len(down)/len(all_stocks)*100, 1),
        'updated': time.strftime('%Y-%m-%d %H:%M:%S')
    }, f, ensure_ascii=False, indent=2)
print(f"\n✅ 缓存已保存到 cache_daily/market_stats_cache.json")
