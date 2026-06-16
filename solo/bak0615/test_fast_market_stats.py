import requests
import time
import json

def fetch_market_stats(timeout=8):
    """
    获取全市场涨跌停统计（新浪财经接口）。
    优化：只取排序两端各100只，快速估算涨跌停数。
    完整统计另起线程获取（用于缓存更新）。
    返回: {zt_count, dt_count, up_count, down_count, total, up_ratio, down_ratio}
    """
    headers = {
        "Referer": "https://finance.sina.com.cn/",
        "User-Agent": "Mozilla/5.0"
    }
    url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"

    all_pcts = []

    # 涨幅排序：从大到小（涨停在前）
    for page in range(1, 3):  # 前200只
        params = {
            "page": page, "num": 100, "sort": "changepercent", "asc": 0,
            "node": "hs_a", "symbol": "", "_s_r_a": "page"
        }
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            r.encoding = "utf-8"
            items = r.json()
            if not items:
                break
            for item in items:
                try:
                    all_pcts.append(float(item.get("changepercent", 0)))
                except:
                    continue
            if len(items) < 100:
                break
            time.sleep(0.1)
        except Exception:
            break

    # 跌幅排序：从大到小（跌停在前）
    for page in range(1, 3):  # 后200只
        params = {
            "page": page, "num": 100, "sort": "changepercent", "asc": 1,
            "node": "hs_a", "symbol": "", "_s_r_a": "page"
        }
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            r.encoding = "utf-8"
            items = r.json()
            if not items:
                break
            for item in items:
                try:
                    all_pcts.append(float(item.get("changepercent", 0)))
                except:
                    continue
            if len(items) < 100:
                break
            time.sleep(0.1)
        except Exception:
            break

    if not all_pcts:
        return None

    # 估算（排序两端数据，涨停/跌停通常集中在两端）
    # 实际全市场约5500只，我们取了约400只作为样本
    sample_size = len(all_pcts)
    # 估算全量比例
    total_est = 5525  # 基于测试结果
    zt_est = max(1, round(sum(1 for p in all_pcts if p >= 9.5) / sample_size * total_est))
    dt_est = max(1, round(sum(1 for p in all_pcts if p <= -9.5) / sample_size * total_est))
    up_est = round(sum(1 for p in all_pcts if p > 0) / sample_size * 100, 1)
    down_est = round(sum(1 for p in all_pcts if p < 0) / sample_size * 100, 1)

    return {
        'zt_count': zt_est,
        'dt_count': dt_est,
        'up_ratio': up_est,
        'down_ratio': down_est,
        'sample_size': sample_size,
        'updated': time.strftime('%H:%M:%S')
    }


# 完整统计（后台/非实时使用）
def fetch_full_market_stats():
    headers = {
        "Referer": "https://finance.sina.com.cn/",
        "User-Agent": "Mozilla/5.0"
    }
    url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"

    all_stocks = []
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
                break
            all_stocks.extend(items)
            if len(items) < 100:
                break
            if page % 10 == 0:
                print(f"  已获取 {len(all_stocks)} 条...")
            time.sleep(0.2)
        except Exception as e:
            print(f"  第{page}页失败: {e}")
            break

    if not all_stocks:
        return None

    zt = sum(1 for s in all_stocks if float(s.get('changepercent', 0)) >= 9.5)
    dt = sum(1 for s in all_stocks if float(s.get('changepercent', 0)) <= -9.5)
    up = sum(1 for s in all_stocks if float(s.get('changepercent', 0)) > 0)
    down = sum(1 for s in all_stocks if float(s.get('changepercent', 0)) < 0)
    total = len(all_stocks)

    return {
        'total': total,
        'zt_count': zt,
        'dt_count': dt,
        'up_count': up,
        'down_count': down,
        'up_ratio': round(up / total * 100, 1),
        'down_ratio': round(down / total * 100, 1),
        'updated': time.strftime('%H:%M:%S')
    }


# 快速测试
print("=== 快速估算（3秒超时）===")
start = time.time()
result = fetch_market_stats(timeout=3)
print(f"耗时: {time.time()-start:.1f}秒")
print(f"结果: {result}")

print("\n=== 完整统计 ===")
start2 = time.time()
result2 = fetch_full_market_stats()
print(f"耗时: {time.time()-start2:.1f}秒")
print(f"结果: {result2}")
