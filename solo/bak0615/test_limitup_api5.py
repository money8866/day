import requests
import time

# 新浪 全市场行情（分页获取，手动过滤涨停跌停）
print("=== 新浪 全市场A股 涨停/跌停统计 ===")
try:
    headers = {
        "Referer": "https://finance.sina.com.cn/",
        "User-Agent": "Mozilla/5.0"
    }

    # 新浪全市场接口
    url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    params = {
        "page": 1, "num": 1000, "sort": "changepercent", "asc": 0,
        "node": "hs_a",
        "symbol": "", "_s_r_a": "page"
    }

    zt_count = 0
    dt_count = 0
    total_count = 0
    zt_samples = []
    dt_samples = []

    for page in range(1, 11):  # 最多10页=10000条
        params["page"] = page
        try:
            r = requests.get(url, params=params, headers=headers, timeout=10)
            r.encoding = "utf-8"
            import json
            items = r.json()
            if not items or (isinstance(items, list) and len(items) == 0):
                break

            for item in items:
                try:
                    pct = float(item.get("changepercent", 0))
                    total_count += 1
                    if pct >= 9.5:
                        zt_count += 1
                        if len(zt_samples) < 3:
                            zt_samples.append(f"{item.get('name','?')} {pct}%")
                    elif pct <= -9.5:
                        dt_count += 1
                        if len(dt_samples) < 3:
                            dt_samples.append(f"{item.get('name','?')} {pct}%")
                except:
                    continue

            if len(items) < 1000:
                break
            time.sleep(0.3)
        except Exception as e:
            print(f"  第{page}页失败: {e}")
            break

    print(f"统计页数: {page}, 总A股数: {total_count}")
    print(f"涨停数: {zt_count}")
    for s in zt_samples:
        print(f"  涨停: {s}")
    print(f"跌停数: {dt_count}")
    for s in dt_samples:
        print(f"  跌停: {s}")
except Exception as e:
    print(f"失败: {e}")

# 尝试雪球 涨跌停统计
print("\n=== 雪球 涨跌停统计 ===")
try:
    url_xq = "https://stock.xueqiu.com/v5/stock/batch/quote.json"
    params_xq = {
        "symbol": "SH000001,SZ399001,SH000300",
        "extend": "detail"
    }
    headers_xq = {
        "Referer": "https://xueqiu.com/",
        "User-Agent": "Mozilla/5.0",
        "Cookie": "xq_a_token=placeholder"  # 雪球需要登录
    }
    r_xq = requests.get(url_xq, params=params_xq, headers=headers_xq, timeout=8)
    print(f"雪球: {r_xq.status_code} {r_xq.text[:200]}")
except Exception as e:
    print(f"雪球失败: {e}")

# 乐咕乐股 涨停统计
print("\n=== 乐咕乐股 涨停数据 ===")
try:
    url_legu = "https://legulegu.com/stockdata/market-activity"
    r_legu = requests.get(url_legu, headers={
        "Referer": "https://legulegu.com/",
        "User-Agent": "Mozilla/5.0"
    }, timeout=8)
    r_legu.encoding = "utf-8"
    import re
    # 找涨停数
    zt_nums = re.findall(r'涨停\s*(\d+)\s*家', r_legu.text)
    dt_nums = re.findall(r'跌停\s*(\d+)\s*家', r_legu.text)
    print(f"乐咕乐股: 涨停={zt_nums}, 跌停={dt_nums}")
    print(f"内容: {r_legu.text[:300]}")
except Exception as e:
    print(f"乐咕乐股失败: {e}")

# 东财Choice 涨停数据（如果有权限）
print("\n=== 东方财富 Choice 涨停统计 ===")
try:
    # 通达信 板块数据 - 涨跌停统计
    import sys
    sys.path.insert(0, 'd:/mystock/solo')
    from mootdx.quotes import Quotes
    from mootdx.config import routes

    # 获取可用服务器
    client = Quotes(market='cf')
    info = client.info()
    print(f"mootdx cf: {str(info)[:200]}")
except Exception as e:
    print(f"mootdx cf失败: {e}")
